"""Packages 1c (usual products in orders) and 2b (order list, clean orders, autonomy level)."""
from __future__ import annotations

import pytest

from conftest import wait_done
from buyer_agent import agent, autonomy, kitchen

pytestmark = pytest.mark.anyio


def usual_kitchen(buyer: str, usual: list[tuple[str, str, float, str]]) -> None:
    def fn(p):
        p["about"]["text"] = "Test kitchen."
        for word, pid, qty, unit in usual:
            kitchen.apply_usual(p, {"word": word, "product_id": pid, "product_name": f"Product {pid}",
                                    "typical_qty": qty, "unit": unit}, None)
    kitchen.update(buyer, fn, "test", "seed")


async def confirm_and_order(client, s, text):
    await agent.run_turn(s, text)
    it = next(e for e in reversed(s.events) if e["type"] == "intent_proposed")["intent"]
    r = await client.post(f"/intents/{it['intent_id']}/confirm", json={"intent_hash": it["intent_hash"],
                                                                       "confirmed_by": "Anna"})
    assert r.status_code == 200, r.text
    await wait_done(s, 2)
    for _ in range(100):  # autonomy_progress is emitted in the background
        if any(e["type"] == "autonomy_progress" for e in s.events):
            break
        import asyncio
        await asyncio.sleep(0.02)
    return it


# --------------------------------------------------------------------------- 1c

async def test_usual_milk_end_to_end(client):
    buyer = "juvenes"  # a seeded buyer: unknown ones fall back to the default mandate
    usual_kitchen(buyer, [("milk", "DEMO-1002", 120, "L"), ("oat drink", "DEMO-2007", 35, "L")])
    s = agent.get_or_create_session(None, buyer)
    prompt = agent.system_prompt(s, "", agent.kitchen_block(buyer))
    assert '- "milk" → Product DEMO-1002 (product DEMO-1002), usually 120 L a week' in prompt
    assert "set preferred_product_id" in prompt

    it = await confirm_and_order(client, s, "120 L milk by Monday")
    item = it["items"][0]
    assert item["product"] == {"query": "milk", "preferred_product_id": "DEMO-1002"}
    assert "your usual: Lactose-free skimmed milk drink 10 l" in it["readback"]
    offers = next(e for e in s.events if e["type"] == "offers")
    assert offers["offers"][0]["product_id"] == "DEMO-1002" and offers["offers"][0]["usual"]
    steps = [f["step"] for f in offers["funnel"]]
    assert steps[0] == "catalog" and steps[-3:] == ["available", "usual", "pick"]
    counts = [f["count"] for f in offers["funnel"]]
    assert all(a >= b for a, b in zip(counts, counts[1:]))
    done = next((e for e in s.events if e["type"] == "order_confirmed"), None)
    assert done, [(e["type"], e.get("text") or e.get("summary") or e.get("message") or (e.get("approval") or {}).get("reason")) for e in s.events][-8:]
    assert done["tier"] == "auto" and done["lines"][0]["product_id"] == "DEMO-1002"
    prog = next(e for e in s.events if e["type"] == "autonomy_progress")
    assert prog["clean"] and prog["order_id"] == done["order_id"] and prog["needed"] == 10

    rows = (await client.get("/orders", params={"buyer_id": buyer})).json()
    assert rows[0]["order_id"] == done["order_id"] and rows[0]["label"] == "Ordered alone" and rows[0]["clean"]


async def test_the_usual_but_double_the_oat_drink():
    buyer = "usual-double"
    usual_kitchen(buyer, [("milk", "DEMO-1002", 120, "L"), ("oat drink", "DEMO-2007", 35, "L")])
    s = agent.get_or_create_session(None, buyer)
    await agent.run_turn(s, "The usual, but double the oat drink, by Monday")
    it = next(e for e in s.events if e["type"] == "intent_proposed")["intent"]
    got = {i["product"]["query"]: (i["quantity"]["amount"], i["product"].get("preferred_product_id")) for i in it["items"]}
    assert got == {"milk": (120, "DEMO-1002"), "oat drink": (70, "DEMO-2007")}


# --------------------------------------------------------------------------- 2b: rules

def grant(gid, scope=None):
    return {"id": gid, "label": gid, "scope": scope or [], "limits": {"max_order_total": 600}, "accepts": [],
            "enabled": True}


def test_level_detection():
    usual = [{"attr": "product_id", "op": "in", "value": ["1"]}]
    assert autonomy.level_of({"autonomy": {"grants": []}}) == 0
    assert autonomy.level_of({"autonomy": {"grants": [grant("g1", usual)]}}) == 1
    assert autonomy.level_of({"autonomy": {"grants": [grant("g1", usual), grant("g2", usual)]}}) == 1
    assert autonomy.level_of({"autonomy": {"grants": [grant("g1", usual), grant("g2")]}}) == 2
    cat = [{"attr": "category", "op": "eq", "value": "FRUIT"}]
    assert autonomy.level_of({"autonomy": {"grants": [grant("g1", cat)]}}) == 2


def row(n, **kw):
    return {"order_id": f"ord_{n}", "intent_id": f"int_{n}", "status": "confirmed", "tier": "auto", "lines": 1,
            "blocked_lines": 0, "asked_lines": 0, "intent_revised": False, "product_changed": False,
            "created_at": f"2026-11-{n + 1:02d}T10:00:00+02:00"} | kw


def test_clean_order_rules():
    rows = [row(1), row(2, intent_revised=True), row(3, product_changed=True), row(4, status="pending_approval", tier="ask"),
            row(5, tier="ask", asked_lines=1), row(6, intent_id="int_7"), row(7, status="rejected", tier="block",
                                                                              blocked_lines=1, blocked_labels=["no peanuts"])]
    got = {r["order_id"]: autonomy.is_clean(r, rows) for r in rows}
    assert got["ord_1"] == (True, "Clean order")
    assert got["ord_2"][0] is False and "changed" in got["ord_2"][1]
    assert got["ord_3"] == (False, "a different product was chosen")
    assert got["ord_4"][0] is False
    assert got["ord_5"][0] is True  # approving the cart unchanged is fine
    assert got["ord_6"] == (False, "a line was blocked")  # an earlier cart of the same intent was blocked
    assert [autonomy.label(r) for r in (rows[0], rows[4], rows[6], rows[3])] == [
        "Ordered alone", "You decided 1", "1 line blocked (no peanuts)", "Waiting for you"]


async def test_ten_clean_orders_offer_the_next_level(monkeypatch):
    buyer = "ladder-test"
    usual_kitchen(buyer, [("milk", "DEMO-1002", 120, "L")])

    async def fake_orders(bid, limit=20, before=None):
        rows = [row(n) for n in range(9)] + [row(9, intent_revised=True)]
        return [r | {"label": autonomy.label(r)} for r in rows]

    monkeypatch.setattr(autonomy, "orders", fake_orders)
    st = await autonomy.state(buyer)
    assert st["clean_orders"] == 9 and not st["offer_next"]

    async def ten(bid, limit=20, before=None):
        return [row(n) | {"label": "Ordered alone"} for n in range(10)]

    monkeypatch.setattr(autonomy, "orders", ten)
    st = await autonomy.state(buyer)
    assert st["clean_orders"] == 10 and st["offer_next"] is (st["level"] < 2)


# --------------------------------------------------------------------------- 2b: level over REST

async def test_level_round_trip_and_usual_sync(client):
    buyer = "level-test"
    usual_kitchen(buyer, [("milk", "DEMO-1002", 120, "L"), ("oat drink", "DEMO-2007", 35, "L")])
    r = await client.post("/autonomy/level", json={"buyer_id": buyer, "level": 1, "by": "Anna"})
    assert r.status_code == 409
    pre = r.json()["detail"]["preview"]
    assert "Usual products" in pre["diff"][0] and "product_id" in pre["cedar"]
    r = await client.post("/autonomy/level", json={"buyer_id": buyer, "level": 1, "by": "Anna",
                                                   "mandate_hash": pre["mandate_hash"]})
    assert r.status_code == 200, r.text
    st = r.json()
    assert st["level"] == 1 and st["name"] == "Usual products" and st["clean_orders"] == 0
    assert st["grants"][0]["summary"].startswith("2 usual products · up to €500 an order")
    assert st["usual_scope"] == {"in_sync": True, "missing": [], "extra": [], "grant_id": "g1"}
    cedar = (await client.get("/mandate/cedar", params={"buyer_id": buyer})).json()
    assert '"DEMO-1002"' in cedar["cedar"] and "g1" in cedar["policy_ids"]

    await client.patch("/kitchen", json={"buyer_id": buyer, "usual": {"add": [
        {"word": "butter", "product_id": "DEMO-2003", "product_name": "Butter"}]}})
    st = (await client.get("/autonomy", params={"buyer_id": buyer})).json()
    assert st["usual_scope"]["in_sync"] is False and st["usual_scope"]["missing"] == ["DEMO-2003"]
    r = await client.post("/autonomy/sync-usual", json={"buyer_id": buyer})
    assert r.status_code == 409
    h = r.json()["detail"]["preview"]["mandate_hash"]
    r = await client.post("/autonomy/sync-usual", json={"buyer_id": buyer, "mandate_hash": h})
    assert r.status_code == 200 and r.json()["usual_scope"]["in_sync"] and r.json()["changed"]

    for level in (2, 0):
        pre = (await client.post("/autonomy/level", json={"buyer_id": buyer, "level": level})).json()["detail"]["preview"]
        st = (await client.post("/autonomy/level", json={"buyer_id": buyer, "level": level,
                                                         "mandate_hash": pre["mandate_hash"]})).json()
        assert st["level"] == level
    assert (await client.post("/autonomy/level", json={"buyer_id": buyer, "level": 3})).status_code == 422
    assert (await client.post("/autonomy/level", json={"buyer_id": "no-usual-test", "level": 1})).status_code == 422
