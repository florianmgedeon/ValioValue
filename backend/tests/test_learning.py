"""Packages 2a (learning in the chat and from orders) and 2c ("Your usual order", learned step, profile version)."""
from __future__ import annotations

import asyncio

import pytest

from buyer_agent import agent, kitchen, learning, local_tools
from buyer_agent.llm import teaching_clause
from test_usual_and_autonomy import confirm_and_order, usual_kitchen

pytestmark = pytest.mark.anyio


def order(oid, items, status="confirmed"):
    """A confirmed order as /internal/orders/{id} returns it: (item_id, word, preferred, product_id, name, qty, unit)."""
    return {"order_id": oid, "status": status,
            "intent": {"items": [{"item_id": i, "product": {"query": w, "preferred_product_id": pref},
                                  "quantity": {"amount": q, "unit": u}} for i, w, pref, _, _, q, u in items]},
            "lines": [{"item_id": i, "product_id": pid, "name": n, "requested_quantity": q, "requested_unit": u,
                       "total_price": 2.0 * q, "unit_price": 20.0} for i, _, _, pid, n, q, u in items]}


def profile_with(usual=(), suggestions=()):
    p = kitchen.empty("x")
    for word, pid, name in usual:
        kitchen.apply_usual(p, {"word": word, "product_id": pid, "product_name": name, "typical_qty": 15, "unit": "KG"},
                            None)
    p["suggestions"] = list(suggestions)
    return p


# --------------------------------------------------------------------------- comparison logic (no network)

def test_replaced_usual_product_becomes_one_suggestion():
    o = order("ord_1", [("i1", "milk", "M1", "M1", "Milk", 120, "L"),
                        ("i5", "granola", "G1", "OAT", "Nut-free oat muesli 2.5 kg", 15, "KG")])
    p = profile_with([("milk", "M1", "Milk"), ("granola", "G1", "Crunchy granola")])
    sug, src = learning.suggestion_for(o, p, [o])
    assert sug["kind"] == "usual" and sug["title"] == "Use Nut-free oat muesli 2.5 kg whenever you say granola?"
    assert sug["payload"]["product_id"] == "OAT"
    assert sug["payload"]["typical_qty"] == 15 and sug["payload"]["word"] == "granola"
    assert src == {"type": "correction", "title": "Your decision on order ord_1", "order_id": "ord_1"}


def test_same_product_twice_without_usual():
    first = order("ord_1", [("i1", "rye rolls", None, "R1", "Rye rolls 60 g", 300, "SALES_UNIT")])
    second = order("ord_2", [("i1", "rye rolls", None, "R1", "Rye rolls 60 g", 280, "SALES_UNIT")])
    p = profile_with()
    assert learning.suggestion_for(first, p, [first]) is None  # only once so far
    sug, src = learning.suggestion_for(second, p, [second, first])
    assert sug["title"] == "Remember Rye rolls 60 g as your usual rye rolls?" and src["type"] == "order"
    # a word that already has a usual product, or a pair the buyer already answered, is not suggested again
    assert learning.suggestion_for(second, profile_with([("rye rolls", "R2", "Other")]), [second, first]) is None
    done = {"kind": "usual", "status": "rejected", "payload": {"word": "rye rolls", "product_id": "R1"}}
    assert learning.suggestion_for(second, profile_with(suggestions=[done]), [second, first]) is None


def test_at_most_one_suggestion_and_only_for_confirmed_orders():
    o = order("ord_3", [("i1", "granola", "G1", "OAT", "Oat muesli", 15, "KG"),
                        ("i2", "butter", "B1", "B2", "Other butter", 10, "KG")])
    p = profile_with([("granola", "G1", "Granola"), ("butter", "B1", "Butter")])
    sug, _ = learning.suggestion_for(o, p, [o])
    assert sug["payload"]["word"] == "granola"  # the first replaced item, one suggestion only
    assert learning.suggestion_for(o | {"status": "pending_approval"}, p, [o]) is None


def test_learned_steps_only_for_accepted_suggestions_of_this_order():
    p = profile_with()
    p["sources"] = [{"id": "s1", "type": "correction", "order_id": "ord_1"}, {"id": "s2", "type": "text"}]
    p["suggestions"] = [
        {"id": "sg1", "kind": "usual", "status": "accepted", "source_id": "s1", "decided_at": "t",
         "payload": {"word": "granola", "product_name": "Nut-free oat muesli"}},
        {"id": "sg2", "kind": "about", "status": "accepted", "source_id": "s2", "title": "x"},
        {"id": "sg3", "kind": "usual", "status": "rejected", "source_id": "s1", "payload": {"word": "milk"}}]
    assert learning.learned_steps(p, "ord_1") == [
        {"kind": "learned", "at": "t", "text": "“granola” now means Nut-free oat muesli", "suggestion_id": "sg1"}]


def test_teaching_clause():
    assert teaching_clause("We're closed on Friday 20 Nov for a teachers' day.") == \
        "We're closed on Friday 20 Nov for a teachers' day."
    assert teaching_clause("We're vegan from now on, order oat drink for Monday") == "We're vegan from now on"
    assert teaching_clause("Our usual order please") is None
    assert teaching_clause("120 L milk by Monday") is None


# --------------------------------------------------------------------------- buyer-side tools in the loop

async def test_teaching_in_the_composer_makes_an_about_suggestion_and_no_intent():
    s = agent.get_or_create_session(None, "teach-buyer")
    await agent.run_turn(s, "We're closed on Friday 20 Nov for a teachers' day.")
    assert not any(e["type"] == "intent_proposed" for e in s.events)
    call = next(e for e in s.events if e["type"] == "tool_call")
    assert call["tool"] == "remember_about_kitchen" and call["side"] == "buyer"
    sug = next(e for e in s.events if e["type"] == "context_suggestion")
    assert sug["reason"] == "from_chat" and len(sug["suggestions"]) == 1
    assert sug["suggestions"][0]["kind"] == "about" and sug["suggestions"][0]["status"] == "proposed"
    p = kitchen.load("teach-buyer")
    assert "teachers' day" not in (p["about"]["text"] or "")  # nothing active without the buyer (W7)
    assert next(x for x in p["sources"] if x["id"] == sug["suggestions"][0]["source_id"])["type"] == "text"


async def test_teaching_and_ordering_in_one_message():
    s = agent.get_or_create_session(None, "teach-buyer-2")
    await agent.run_turn(s, "We're vegan from now on, order 35 L oat drink by Monday")
    tools = [e["tool"] for e in s.events if e["type"] == "tool_call"]
    assert tools == ["remember_about_kitchen", "propose_intent"]
    assert next(e for e in s.events if e["type"] == "intent_proposed")


async def test_local_tools_cannot_change_the_mandate_and_validate_input():
    s = agent.get_or_create_session(None, "teach-buyer-3")
    s.history.append({"role": "user", "text": "never order anything with sesame"})
    before = (await __import__("buyer_agent.valio", fromlist=["x"]).mandate("teach-buyer-3"))["version"]
    bad = await local_tools.call(s, "remember_about_kitchen", {"kind": "must", "title": "x", "quote": "sesame",
                                                               "payload": {"attr": "nonsense", "op": "eq", "value": 1}})
    assert bad.get("error")
    assert (await local_tools.call(s, "remember_about_kitchen", {"kind": "autonomy", "title": "x", "quote": "",
                                                                 "payload": {}}))["error"]
    assert (await local_tools.call(s, "learn_from_document", {"upload_id": "up_not_mine"}))["error"]
    assert (await local_tools.call(s, "place_order", {}))["error"]
    after = (await __import__("buyer_agent.valio", fromlist=["x"]).mandate("teach-buyer-3"))["version"]
    assert before == after


# --------------------------------------------------------------------------- learning after an order, end to end

async def wait_for(s, type_, timeout=5.0):
    for _ in range(int(timeout * 50)):
        if (e := next((e for e in s.events if e["type"] == type_), None)):
            return e
        await asyncio.sleep(0.02)
    return None


async def order_and_approve(client, s, text):
    """Confirm the intent, then approve the cart if Valio Aimo asks (pack rounding needs a human here)."""
    await confirm_and_order(client, s, text)
    appr = next((e["approval"] for e in s.events if e["type"] == "approval_required"), None)
    if appr:
        r = await client.post(f"/approvals/{appr['approval_id']}", json={
            "decision": "approve", "decided_by": "Anna", "cart_hash": appr["cart_hash"]})
        assert r.status_code == 200, r.text
    assert await wait_for(s, "order_confirmed")


async def test_second_order_of_the_same_product_suggests_a_usual_and_accept_makes_it_usual(client):
    buyer = "juvenes"
    kitchen.update(buyer, lambda p: p.update(usual=[], suggestions=[]), "test", "reset")
    s1 = agent.get_or_create_session(None, buyer)
    await order_and_approve(client, s1, "20 L oat drink by Monday")
    assert await wait_for(s1, "context_suggestion", 0.5) is None  # first time: nothing to learn yet
    s2 = agent.get_or_create_session(None, buyer)
    await order_and_approve(client, s2, "20 L oat drink by Monday")
    sug = await wait_for(s2, "context_suggestion")
    assert sug and sug["reason"] == "after_order" and sug["order_id"] and len(sug["suggestions"]) == 1
    sg = sug["suggestions"][0]
    assert sg["kind"] == "usual" and sg["title"].endswith("as your usual oat drink?")

    r = await client.post("/kitchen/suggestions/accept", json={"buyer_id": buyer, "ids": [sg["id"]]})
    assert r.status_code == 200, r.text
    assert sg["payload"]["product_id"] in kitchen.usual_ids(kitchen.load(buyer))

    story = (await client.get(f"/orders/{sug['order_id']}/story")).json()
    learned = [x for x in story["steps"] if x["kind"] == "learned"]
    assert learned and learned[0]["text"].startswith("“oat drink” now means")
    assert isinstance(story.get("kitchen_profile_version"), int)
    rows = (await client.get("/orders", params={"buyer_id": buyer})).json()
    assert any("kitchen_profile_version" in r for r in rows)

    # the next order uses it as the usual product
    s3 = agent.get_or_create_session(None, buyer)
    await agent.run_turn(s3, "20 L oat drink by Monday")
    it = next(e for e in s3.events if e["type"] == "intent_proposed")["intent"]
    assert it["items"][0]["product"]["preferred_product_id"] == sg["payload"]["product_id"]


# --------------------------------------------------------------------------- 2c: "Your usual order"

async def test_usual_order_with_last_prices(client):
    buyer = "juvenes"
    usual_kitchen(buyer, [("milk", "DEMO-1002", 120, "L"), ("oat drink", "DEMO-2007", 35, "L")])
    s = agent.get_or_create_session(None, buyer)
    await confirm_and_order(client, s, "120 L milk by Monday")
    r = await client.get("/kitchen/usual-order", params={"buyer_id": buyer})
    assert r.status_code == 200, r.text
    u = r.json()
    milk = next(i for i in u["items"] if i["word"] == "milk")
    assert milk["quantity"] == 120 and milk["unit"] == "L" and milk["last_unit_price"] is not None
    assert milk["estimate"] == pytest.approx(milk["last_unit_price"] * 120, abs=0.6)
    oat = next(i for i in u["items"] if i["word"] == "oat drink")
    if oat["last_unit_price"] is None:
        assert oat["estimate"] is None and not u["estimate_complete"]
    assert u["estimate_total"] >= milk["estimate"] and u["deliver_by"] and u["based_on"] == "your usual amounts per week"


async def test_usual_order_of_an_empty_kitchen(client):
    u = (await client.get("/kitchen/usual-order", params={"buyer_id": "nobody-here"})).json()
    assert u["items"] == [] and u["estimate_total"] is None
