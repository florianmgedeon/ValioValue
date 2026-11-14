"""Package 1b: kitchen profile store, reading documents, accepting suggestions with the signature round trip."""
from __future__ import annotations

import threading
from collections import Counter

import pytest
from fastapi import HTTPException

from conftest import ROOT
from buyer_agent import config, ingest, kitchen, valio
from valio_mcp import server

pytestmark = pytest.mark.anyio
FIX = ROOT / "contracts" / "fixtures" / "uploads"


@pytest.fixture(autouse=True)
def catalog_search(monkeypatch):
    async def search(buyer_id, query, limit=5):
        res = await server.search_products(query=query, limit=limit)
        return res["products"]
    monkeypatch.setattr(ingest, "search_products", search)


# --------------------------------------------------------------------------- store

def test_store_versions_history_and_atomic_writes():
    bid = "store-test"
    p = kitchen.load(bid)
    assert p["version"] == 0 and p["usual"] == []
    p = kitchen.update(bid, lambda p: kitchen.apply_about(p, {"text": "A hotel in Turku."}, None), "anna", "x")
    assert p["version"] == 1 and p["history"][-1]["by"] == "anna" and p["about"]["text"] == "A hotel in Turku."
    stale = kitchen.load(bid)
    kitchen.update(bid, lambda p: None, "anna", "touch")
    with pytest.raises(kitchen.ConflictError):
        kitchen.save(stale, "bob", "late write")

    def bump():
        for _ in range(10):
            kitchen.update(bid, lambda p: p["about"]["facts"].append({"id": "f", "label": "x"}), "t", "bump")

    threads = [threading.Thread(target=bump) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    p = kitchen.load(bid)
    assert p["version"] == 42 and len(p["about"]["facts"]) == 40
    assert [h["version"] for h in p["history"]] == list(range(1, 43))
    assert not list(config.KITCHEN_DIR.glob(".*tmp"))  # no temp files left behind


def test_seed_and_prompt_block():
    p = kitchen.load("school-kitchen-espoo")
    assert p["version"] == 7 and len(p["usual"]) == 6
    text = kitchen.prompt_block("school-kitchen-espoo")
    assert text.startswith("About: Primary school kitchen in Espoo")
    assert '- "milk" (also: maito) → Valio Eila® semi skimmed milk drink 10 l novobox lactose free (product 463038), ' \
           'usually 120 L a week' in text
    assert len(text) <= kitchen.PROMPT_MAX


def test_prompt_block_stays_short():
    bid = "long-test"
    def fill(p):
        p["about"]["text"] = "Long text. " * 300
        for n in range(40):
            kitchen.apply_usual(p, {"word": f"thing {n}", "product_id": str(1000 + n), "product_name": "A product " * 4,
                                    "typical_qty": 5, "unit": "KG"}, None)
    kitchen.update(bid, fill, "t", "fill")
    text = kitchen.prompt_block(bid)
    assert len(text) <= kitchen.PROMPT_MAX and '"thing 0"' in text


# --------------------------------------------------------------------------- onboarding over REST

async def ingest_fixture(client, buyer, name):
    up = (await client.post("/uploads", data={"buyer_id": buyer},
                            files={"file": (name, (FIX / name).read_bytes(), "")})).json()
    r = await client.post("/kitchen/ingest", json={"buyer_id": buyer, "upload_ids": [up["upload_id"]]})
    assert r.status_code == 200, r.text
    return r.json()


async def test_onboarding_end_to_end(client):
    buyer = "onboard-test"
    results = [await ingest_fixture(client, buyer, n) for n in ("menu-w47.jpg", "allergy-note.jpg", "invoices-oct.pdf")]
    assert [r["document_type"] for r in results] == ["menu", "allergy_note", "invoice"]
    assert results[0]["source"]["type"] == "photo" and results[2]["source"]["type"] == "pdf"
    sugg = [s for r in results for s in r["suggestions"]]
    assert Counter(s["kind"] for s in sugg) == {"about": 2, "usual": 3, "must": 1, "prefer": 1, "autonomy": 1}
    assert all(s["status"] == "proposed" and s["quote"] and s["source_id"] for s in sugg)
    assert next(s for s in sugg if s["kind"] == "must")["quote"].startswith("NO NUTS")

    before_m = (await client.get("/mandate", params={"buyer_id": buyer})).json()
    before_k = (await client.get("/kitchen", params={"buyer_id": buyer})).json()
    ids = [s["id"] for s in sugg]
    r = await client.post("/kitchen/suggestions/accept", json={"buyer_id": buyer, "ids": ids, "by": "Anna"})
    assert r.status_code == 409, r.text
    pre = r.json()["detail"]["preview"]
    # a new buyer starts from the default mandate; its first own version is the next one
    assert r.json()["detail"]["needs_signature"] and pre["version"] >= 1
    assert any(d.startswith("New must: No peanuts or tree nuts") and "(from Allergy note)" in d for d in pre["diff"]), pre["diff"]
    assert "forbid" in pre["cedar"]
    assert (await client.get("/kitchen", params={"buyer_id": buyer})).json()["version"] == before_k["version"]

    r = await client.post("/kitchen/suggestions/accept",
                          json={"buyer_id": buyer, "ids": ids, "by": "Anna", "mandate_hash": pre["mandate_hash"]})
    assert r.status_code == 200, r.text
    k = (await client.get("/kitchen", params={"buyer_id": buyer})).json()
    assert k["version"] == before_k["version"] + 1 and k["history"][-1]["by"] == "Anna"
    assert {u["word"] for u in k["usual"]} == {"milk", "butter", "oat drink"}
    assert len(k["about"]["facts"]) == 2 and all(s["status"] == "accepted" for s in k["suggestions"])
    m = (await client.get("/mandate", params={"buyer_id": buyer})).json()
    assert m["version"] == pre["version"] and m["buyer_id"] == buyer and before_m["buyer_id"] == "default"
    assert {r["value"] for r in m["mandatory_rules"]} >= {"peanuts", "tree nuts"}
    assert any(p["value"] == "HYVAA_SUOMESTA" and p["weight"] == 3 for p in m["preferences"])
    g = next(g for g in m["autonomy"]["grants"] if g.get("source") == "Invoices Sep–Oct")
    assert g["limits"]["max_order_total"] == 600
    assert g["scope"] == [{"attr": "product_id", "op": "in", "value": ["473609", "463038", "464411"]}]
    assert k["mandate_summary"]["version"] == m["version"]
    # already decided
    again = await client.post("/kitchen/suggestions/accept", json={"buyer_id": buyer, "ids": ids[:1]})
    assert again.status_code == 409


async def test_about_and_usual_need_no_signature(client):
    buyer = "nosign-test"
    res = await client.post("/kitchen/ingest", json={"buyer_id": buyer, "text": "We are a hotel kitchen in Turku."})
    s = res.json()["suggestions"]
    assert [x["kind"] for x in s] == ["about"] and res.json()["source"]["type"] == "text"
    r = await client.post("/kitchen/suggestions/accept", json={"buyer_id": buyer, "ids": [s[0]["id"]]})
    assert r.status_code == 200 and r.json()["mandate"] is None
    assert "hotel kitchen in Turku" in r.json()["profile"]["about"]["text"]


async def test_nothing_applied_when_the_mandate_fails(client, monkeypatch):
    buyer = "fail-test"
    sugg = (await ingest_fixture(client, buyer, "allergy-note.jpg"))["suggestions"]
    ids = [s["id"] for s in sugg]
    pre = (await client.post("/kitchen/suggestions/accept", json={"buyer_id": buyer, "ids": ids})).json()["detail"]["preview"]

    async def broken(*a, **kw):
        raise HTTPException(503, "Valio Aimo down")

    monkeypatch.setattr(valio, "put_signed", broken)
    v = kitchen.load(buyer)["version"]
    r = await client.post("/kitchen/suggestions/accept",
                          json={"buyer_id": buyer, "ids": ids, "mandate_hash": pre["mandate_hash"]})
    assert r.status_code == 503
    p = kitchen.load(buyer)
    assert p["version"] == v and p["usual"] == [] and all(s["status"] == "proposed" for s in p["suggestions"])
    # a wrong hash never signs
    r = await client.post("/kitchen/suggestions/accept", json={"buyer_id": buyer, "ids": ids, "mandate_hash": "sha256:00"})
    assert r.status_code == 409 and r.json()["detail"]["needs_signature"]


async def test_reject_keeps_the_record(client):
    buyer = "reject-test"
    s = (await ingest_fixture(client, buyer, "menu-w47.jpg"))["suggestions"][0]
    r = await client.post(f"/kitchen/suggestions/{s['id']}/reject", json={"buyer_id": buyer, "by": "Anna"})
    assert r.status_code == 200
    kept = next(x for x in r.json()["suggestions"] if x["id"] == s["id"])
    assert kept["status"] == "rejected" and kept["decided_by"] == "Anna"
    assert (await client.post(f"/kitchen/suggestions/{s['id']}/reject", json={"buyer_id": buyer})).status_code == 409


async def test_order_list_photo_never_orders(client):
    buyer = "orderlist-test"
    res = await ingest_fixture(client, buyer, "order-list.jpg")
    assert res["document_type"] == "order_list" and res["hint"].startswith("This looks like an order list")
    orders = (await client.get("/orders", params={"buyer_id": buyer})).json()
    assert orders == []


async def test_ingest_validates_what_the_model_says(client, monkeypatch):
    buyer = "validate-test"

    class Fake:
        name = "anthropic"

    async def fake_llm(provider, system, upload_ids, text):
        assert "allergens (set): contains, not_contains" in system  # attributes come from the policy catalog
        return {"document_type": "allergy_note", "title": "Note", "suggestions": [
            {"kind": "must", "title": "No sesame", "quote": "no sesame", "payload": {"attr": "allergens", "op": "not_contains", "value": "sesame"}},
            {"kind": "must", "title": "Vegan only", "quote": "vegan", "payload": {"attr": "vegan", "op": "eq", "value": True}},
            {"kind": "prefer", "title": "Cheap", "quote": "cheap", "payload": {"attr": "unit_price", "op": "lte", "value": "cheap", "weight": 1}},
            {"kind": "wish", "title": "Unicorns", "quote": "x", "payload": {}},
            {"kind": "about", "title": "Invented", "quote": "this is not in the text", "payload": {"text": "x"}},
            {"kind": "usual", "title": "\"butter\"", "quote": "butter", "payload": {"word": "butter", "product_query": "Butter unsalted 500 g"}},
            {"kind": "usual", "title": "\"zork\"", "quote": "zork", "payload": {"word": "zork", "product_query": "zork"}}]}

    monkeypatch.setattr(ingest, "make_provider", lambda *a: Fake())
    monkeypatch.setattr(ingest, "_llm", fake_llm)
    text = "Please remember: no sesame, vegan, cheap, butter, zork."
    res = (await client.post("/kitchen/ingest", json={"buyer_id": buyer, "text": text})).json()
    kinds = [(s["kind"], s["title"]) for s in res["suggestions"]]
    assert kinds == [("must", "No sesame"), ("usual", "\"butter\""), ("usual", "\"zork\"")]
    butter, zork = res["suggestions"][1]["payload"], res["suggestions"][2]["payload"]
    assert butter["product_id"] == "DEMO-2003" and butter["product_name"].startswith("Butter unsalted")
    assert zork["product_id"] is None and "candidates" in zork
    assert all(s["kind"] != "must" or s["title"] != "Vegan only" for s in kitchen.load(buyer)["suggestions"])


async def test_patch_kitchen(client):
    buyer = "patch-test"
    r = await client.patch("/kitchen", json={"buyer_id": buyer, "about": {"text": "Café in Oulu."}, "by": "Anna",
                                             "usual": {"add": [{"word": "Butter", "product_query": "butter unsalted 500 g",
                                                                "typical_qty": 2, "unit": "KG"}]}})
    assert r.status_code == 200, r.text
    p = r.json()
    u = p["usual"][0]
    assert p["about"]["text"] == "Café in Oulu." and u["word"] == "butter" and u["product_id"] == "DEMO-2003"
    r = await client.patch("/kitchen", json={"buyer_id": buyer, "usual": {"remove": [u["id"]]}})
    assert r.json()["usual"][0]["status"] == "removed" and r.json()["version"] == p["version"] + 1
    hist = (await client.get("/kitchen/history", params={"buyer_id": buyer})).json()
    assert hist["history"][0]["summary"].startswith('Usual "butter" removed')
    assert (await client.patch("/kitchen", json={"buyer_id": buyer})).status_code == 422


async def test_kitchen_products_and_usual_substitutes_patch(client):
    buyer = "subs-patch-test"
    search_res = await client.get("/kitchen/products", params={"buyer_id": buyer, "q": "kaurajuoma", "limit": 5})
    assert search_res.status_code == 200, search_res.text
    prods = search_res.json()["products"]
    assert any(p["product_id"] == "DEMO-1005" for p in prods)

    # Add a usual product with ordered substitutes (resolved via product_query and explicit ID)
    r = await client.patch("/kitchen", json={
        "buyer_id": buyer,
        "by": "Anna",
        "usual": {"add": [{
            "word": "oat drink",
            "aliases": ["kaurajuoma"],
            "product_id": "DEMO-2007",
            "product_name": "Oat drink barista 1 l (DEV SAMPLE)",
            "typical_qty": 36,
            "unit": "L",
            "substitutes": [{"product_query": "Kaurajuoma 1 l (DEV SAMPLE)"}],
        }]},
    })
    assert r.status_code == 200, r.text
    u = r.json()["usual"][0]
    assert u["fallback_mode"] == "only_listed"
    assert [s["product_id"] for s in u["substitutes"]] == ["DEMO-1005"]
    block = kitchen.prompt_block(buyer)
    assert "fallback ONLY: Kaurajuoma 1 l (DEV SAMPLE) (product DEMO-1005) (allow_other_substitutes=false)" in block

    # Update fallback_mode to 'none' -> usual_ids excludes substitutes
    r2 = await client.patch("/kitchen", json={
        "buyer_id": buyer,
        "usual": {"update": [{"id": u["id"], "fallback_mode": "none"}]},
    })
    assert r2.status_code == 200
    assert r2.json()["usual"][0]["fallback_mode"] == "none"
    assert kitchen.usual_ids(kitchen.load(buyer)) == ["DEMO-2007"]
    assert "no substitutes allowed (allow_other_substitutes=false)" in kitchen.prompt_block(buyer)

    # Switch back to 'only_listed' -> usual_ids includes DEMO-1005 for Level-1 autonomy
    await client.patch("/kitchen", json={
        "buyer_id": buyer,
        "usual": {"update": [{"id": u["id"], "fallback_mode": "only_listed"}]},
    })
    assert kitchen.usual_ids(kitchen.load(buyer)) == ["DEMO-2007", "DEMO-1005"]

    # Invalid fallback_mode rejected with 422
    bad = await client.patch("/kitchen", json={
        "buyer_id": buyer,
        "usual": {"update": [{"id": u["id"], "fallback_mode": "anything_goes"}]},
    })
    assert bad.status_code == 422
