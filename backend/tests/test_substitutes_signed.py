"""Substitutes count as usual products: at autonomy level 1 a change to them widens or narrows the signed
usual-products grant, so it goes through the signature round trip (POST /kitchen/usual/fallback, and accepting a
usual suggestion that carries substitutes)."""
from __future__ import annotations

import pytest

from buyer_agent import kitchen
from test_usual_and_autonomy import usual_kitchen

pytestmark = pytest.mark.anyio

SUB = {"product_id": "DEMO-1005", "product_name": "Kaurajuoma 1 l"}


async def level(client, buyer: str, n: int) -> None:
    pre = (await client.post("/autonomy/level", json={"buyer_id": buyer, "level": n})).json()["detail"]["preview"]
    r = await client.post("/autonomy/level", json={"buyer_id": buyer, "level": n, "mandate_hash": pre["mandate_hash"]})
    assert r.status_code == 200, r.text


async def scope(client, buyer: str) -> list[str]:
    st = (await client.get("/autonomy", params={"buyer_id": buyer})).json()
    assert st["usual_scope"]["in_sync"], st["usual_scope"]
    return (await client.get("/mandate/cedar", params={"buyer_id": buyer})).json()["cedar"]


def milk_id(buyer: str) -> str:
    return kitchen.find_usual(kitchen.load(buyer), "milk")["id"]


async def test_adding_a_substitute_at_level_1_is_signed(client):
    buyer = "subs-signed"
    usual_kitchen(buyer, [("milk", "DEMO-1002", 120, "L")])
    await level(client, buyer, 1)
    body = {"buyer_id": buyer, "by": "Anna", "updates": [{"id": milk_id(buyer), "substitutes": [SUB]}]}

    r = await client.post("/kitchen/usual/fallback", json=body)
    assert r.status_code == 409
    pre = r.json()["detail"]["preview"]
    assert pre["diff"] == ["milk: I may also order Kaurajuoma 1 l alone, as a substitute"]
    assert kitchen.find_usual(kitchen.load(buyer), "milk")["substitutes"] == []  # nothing applied yet

    r = await client.post("/kitchen/usual/fallback", json=body | {"mandate_hash": pre["mandate_hash"]})
    assert r.status_code == 200, r.text
    milk = kitchen.find_usual(r.json()["profile"], "milk")
    assert milk["substitutes"] == [SUB] and milk["fallback_mode"] == "only_listed"
    assert r.json()["mandate"] is not None
    assert '"DEMO-1005"' in await scope(client, buyer)

    # removing it narrows the grant again, also signed
    body["updates"][0]["substitutes"] = []
    r = await client.post("/kitchen/usual/fallback", json=body)
    assert r.status_code == 409
    pre = r.json()["detail"]["preview"]
    assert pre["diff"] == ["milk: I no longer order Kaurajuoma 1 l alone"]
    r = await client.post("/kitchen/usual/fallback", json=body | {"mandate_hash": pre["mandate_hash"]})
    assert r.status_code == 200
    assert '"DEMO-1005"' not in await scope(client, buyer)


async def test_order_and_mode_changes_that_keep_the_grant_need_no_signature(client):
    buyer = "subs-nosig"
    usual_kitchen(buyer, [("milk", "DEMO-1002", 120, "L")])
    sub2 = {"product_id": "DEMO-1006", "product_name": "Maito 1 l"}
    await client.patch("/kitchen", json={"buyer_id": buyer, "usual": {"update": [
        {"id": milk_id(buyer), "substitutes": [SUB, sub2]}]}})
    await level(client, buyer, 1)

    r = await client.post("/kitchen/usual/fallback", json={"buyer_id": buyer, "updates": [
        {"id": milk_id(buyer), "substitutes": [sub2, SUB], "fallback_mode": "ask_any"}]})
    assert r.status_code == 200, r.text
    milk = kitchen.find_usual(r.json()["profile"], "milk")
    assert [s["product_id"] for s in milk["substitutes"]] == ["DEMO-1006", "DEMO-1005"]
    assert milk["fallback_mode"] == "ask_any" and r.json()["mandate"] is None


async def test_at_level_0_substitutes_save_without_signature(client):
    buyer = "subs-level0"
    usual_kitchen(buyer, [("milk", "DEMO-1002", 120, "L")])
    r = await client.post("/kitchen/usual/fallback", json={"buyer_id": buyer, "updates": [
        {"id": milk_id(buyer), "substitutes": [SUB]}]})
    assert r.status_code == 200 and r.json()["mandate"] is None
    assert kitchen.find_usual(r.json()["profile"], "milk")["substitutes"] == [SUB]


async def test_bad_input(client):
    buyer = "subs-bad"
    usual_kitchen(buyer, [("milk", "DEMO-1002", 120, "L")])
    r = await client.post("/kitchen/usual/fallback", json={"buyer_id": buyer, "updates": [
        {"id": milk_id(buyer), "fallback_mode": "sometimes"}]})
    assert r.status_code == 422
    r = await client.post("/kitchen/usual/fallback", json={"buyer_id": buyer, "updates": [{"id": "u99"}]})
    assert r.status_code == 404


async def test_substitute_suggestion_from_chat_keeps_the_product_and_is_signed(client):
    buyer = "subs-chat"
    usual_kitchen(buyer, [("milk", "DEMO-1002", 120, "L")])
    await level(client, buyer, 1)
    src = {"type": "text", "title": "Typed note", "text": "If milk is out, take Kaurajuoma 1 l, nothing else."}
    saved = kitchen.add_suggestions(buyer, [{
        "kind": "usual", "title": "Milk: if it is out, take Kaurajuoma 1 l, nothing else", "quote": "",
        "payload": {"word": "milk", "substitutes": [SUB], "fallback_mode": "only_listed"}}], src, by="test")
    sid = saved[0]["id"]

    r = await client.post("/kitchen/suggestions/accept", json={"buyer_id": buyer, "ids": [sid], "by": "Anna"})
    assert r.status_code == 409
    pre = r.json()["detail"]["preview"]
    assert pre["diff"] == ["milk: I may also order Kaurajuoma 1 l alone, as a substitute"]
    r = await client.post("/kitchen/suggestions/accept", json={"buyer_id": buyer, "ids": [sid], "by": "Anna",
                                                               "mandate_hash": pre["mandate_hash"]})
    assert r.status_code == 200, r.text
    milk = kitchen.find_usual(r.json()["profile"], "milk")
    assert milk["product_id"] == "DEMO-1002" and milk["typical_qty"] == 120  # the product itself stays
    assert milk["substitutes"] == [SUB] and milk["fallback_mode"] == "only_listed"
    assert '"DEMO-1005"' in await scope(client, buyer)


def test_merge_with_existing():
    p = {"usual": [{"id": "u1", "word": "milk", "aliases": ["maito"], "product_id": "P1", "product_name": "Milk 10 l",
                    "typical_qty": 120, "unit": "L", "per": "week", "status": "active"}]}
    out = kitchen.merge_with_existing(p, {"word": "maito", "substitutes": [SUB]})
    assert out["product_id"] == "P1" and out["word"] == "milk" and out["substitutes"] == [SUB]
    # a suggestion that names a product is left alone
    assert kitchen.merge_with_existing(p, {"word": "milk", "product_query": "x"}) == {"word": "milk", "product_query": "x"}
    assert kitchen.merge_with_existing(p, {"word": "cream"}) == {"word": "cream"}


async def test_unresolved_substitute_is_dropped_with_a_note(monkeypatch):
    from buyer_agent import ingest

    async def search(buyer_id, q, limit):
        return [{"product_id": "DEMO-1005", "name": "Kaurajuoma 1 l"}] if "kaura" in q.lower() else []

    monkeypatch.setattr(ingest, "search_products", search)
    buyer = "subs-unresolved"
    usual_kitchen(buyer, [("milk", "DEMO-1002", 120, "L")])
    profile = kitchen.load(buyer)
    raw = [{"kind": "usual", "title": "Milk fallback", "quote": "",
            "payload": {"word": "milk", "fallback_mode": "only_listed",
                        "substitutes": [{"product_query": "Kaurajuoma 1 l"}, {"product_query": "Imaginary milk 3 l"}]}},
           {"kind": "usual", "title": "Milk fallback 2", "quote": "",
            "payload": {"word": "milk", "substitutes": [{"product_query": "Imaginary milk 3 l"}]}}]
    notes: list[str] = []
    out = await ingest.validate(buyer, raw, profile, {}, None, notes)
    assert len(out) == 1  # the second one has no substitute left
    assert out[0]["payload"]["product_id"] == "DEMO-1002"
    assert [x["product_id"] for x in out[0]["payload"]["substitutes"]] == ["DEMO-1005"]
    assert len(notes) == 2 and "“Imaginary milk 3 l”" in notes[0]


async def test_resolution_prefers_the_closest_name(monkeypatch):
    from buyer_agent import ingest

    async def search(buyer_id, q, limit):
        return [{"product_id": "L", "name": "Valio Eila® butter for laminating 4x5 kg unsalted lactose free"},
                {"product_id": "B", "name": "Valio butter 4x5 kg unsalted"}]

    monkeypatch.setattr(ingest, "search_products", search)
    subs = await ingest.resolve_substitutes("x", [{"product_query": "Valio butter 4x5 kg unsalted"}])
    assert subs[0]["product_id"] == "B"
