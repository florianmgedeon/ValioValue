"""The whole chain on the Valio side: intent -> confirm -> search -> quote -> order -> approve -> verify."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from conftest import next_monday
from valio_mcp import server, trust
from valio_mcp.store import store

AUTH = {"Authorization": "Bearer dev-approver-token"}


def ctx(buyer="juvenes", session="sess_test"):
    req = SimpleNamespace(headers={"x-agent-session": session, "x-buyer-id": buyer})
    return SimpleNamespace(request_context=SimpleNamespace(request=req))


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(scope="module")
def http():
    with TestClient(server.mcp.streamable_http_app()) as c:
        yield c


def propose(text, query, qty, unit="L", rules=(), buyer="juvenes", **kw):
    return run(server.propose_intent(original_request=text, product_query=query, quantity=qty, quantity_unit=unit,
                                     deliver_by=kw.pop("deliver_by", next_monday()), hard_rules=list(rules),
                                     ctx=ctx(buyer), **kw))["intent"]


def confirm(http, it, buyer="juvenes"):
    sig = trust.sign(trust.buyer_key(buyer), it["intent_hash"])
    r = http.post(f"/internal/intents/{it['intent_id']}/confirm", headers=AUTH,
                  json={"intent_hash": it["intent_hash"], "signature": sig, "confirmed_by": "chef.anna"})
    assert r.status_code == 200, r.text
    return r.json()


def offers_for(it, ids=("DEMO-1001", "DEMO-1002", "DEMO-1003"), buyer="juvenes"):
    return run(server.check_offer(product_ids=list(ids), intent_id=it["intent_id"], ctx=ctx(buyer)))["offers"]


def order(it, quote_id, buyer="juvenes"):
    return run(server.place_order(quote_id=quote_id, intent_id=it["intent_id"], reason="test", ctx=ctx(buyer)))


# --------------------------------------------------------------------------- auto

def test_auto_tier_exact_match_and_verify(http):
    it = confirm(http, propose("120 litres of lactose-free milk by Monday", "lactose-free milk", 120))
    assert it["status"] == "confirmed" and it["hard_rules"][0]["rule"] == "lactose_free"
    found = run(server.search_products(query="lactose-free milk", intent_id=it["intent_id"], ctx=ctx()))
    ids = [p["product_id"] for p in found["products"]]
    assert "DEMO-1004" not in ids  # plain milk, no lactose-free signal
    best = next(o for o in offers_for(it) if o["product_id"] == "DEMO-1001")
    assert best["provenance"]["signature"] and best["evidence"][0]["verdict"] == "safe"
    res = order(it, best["quote_id"])
    assert res["status"] == "confirmed" and res["tier"] == "auto", res
    assert res["diff"]["deviations"] == 0
    v = http.get(f"/verify/{res['order_id']}").json()
    assert v["valid"], v

    # idempotent: same quote again -> same order. Another quote -> refused, one intent buys one order.
    again = order(it, best["quote_id"])
    assert again["order_id"] == res["order_id"] and again.get("idempotent_replay")


def test_second_order_on_same_intent_is_refused(http):
    it = confirm(http, propose("120 l lactose-free milk", "lactose-free milk", 120))
    offers = offers_for(it)
    first = order(it, offers[0]["quote_id"])
    assert first["status"] in ("confirmed", "pending_approval")
    second = order(it, offers[1]["quote_id"])
    assert second["status"] == "rejected" and "one order" in second["error"]


# --------------------------------------------------------------------------- ask

def test_ask_tier_needs_signed_cart(http):
    it = confirm(http, propose("I need 1,000 litres of lactose-free milk by Monday", "lactose-free milk", 1000))
    o = next(o for o in offers_for(it) if o["product_id"] == "DEMO-1001")
    res = order(it, o["quote_id"])
    assert res["tier"] == "ask" and res["status"] == "pending_approval"
    failed = {c["field"] for c in res["diff"]["checks"] if not c["ok"]}
    assert failed == {"spend_limit"}, res["diff"]
    qty = next(c for c in res["diff"]["checks"] if c["field"] == "quantity")
    assert qty["ok"] and "1,008" in qty["cart"]  # 84 packs of 12, +0.8 % is inside the 2 % tolerance

    appr = res["approval"]
    bad = http.post(f"/internal/approvals/{appr['approval_id']}", headers=AUTH,
                    json={"decision": "approve", "cart_hash": "sha256:00", "signature": "x"})
    assert bad.status_code == 409
    forged = http.post(f"/internal/approvals/{appr['approval_id']}", headers=AUTH,
                       json={"decision": "approve", "cart_hash": res["cart_hash"],
                             "signature": trust.sign(trust.valio_key(), res["cart_hash"])})
    assert forged.status_code == 403
    ok = http.post(f"/internal/approvals/{appr['approval_id']}", headers=AUTH,
                   json={"decision": "approve", "decided_by": "chef.anna", "cart_hash": res["cart_hash"],
                         "signature": trust.sign(trust.buyer_key("juvenes"), res["cart_hash"])})
    assert ok.status_code == 200 and ok.json()["order"]["status"] == "confirmed"
    v = http.get(f"/verify/{res['order_id']}").json()
    assert v["valid"] and any(c["name"] == "buyer_cart_signature" and c["ok"] for c in v["checks"]), v


def test_quantity_overshoot_outside_tolerance_is_a_deviation(http):
    it = confirm(http, propose("100 l lactose-free milk", "lactose-free milk", 100))
    o = next(o for o in offers_for(it) if o["product_id"] == "DEMO-1001")  # lot of 12 -> 108 L, +8 %
    res = order(it, o["quote_id"])
    qty = next(c for c in res["diff"]["checks"] if c["field"] == "quantity")
    assert not qty["ok"] and res["tier"] == "ask"


# --------------------------------------------------------------------------- block

def test_block_on_allergen_even_if_agent_insists(http):
    it = confirm(http, propose("200 l oat drink, milk allergy", "oat drink", 200,
                               rules=[{"rule": "exclude_allergen", "allergen": "milk"}]))
    # The agent fetches a quote for real milk outside the intent, same quantity, and tries to order it.
    q = run(server.check_offer(product_ids=["DEMO-1001"], requested_date=it["deliver_by"], quantity=200,
                               quantity_unit="L", ctx=ctx()))["offers"][0]
    res = order(it, q["quote_id"])
    assert res["status"] == "rejected" and res["tier"] == "block"
    rule = next(c for c in res["diff"]["checks"] if c["field"] == "hard_rules")
    assert rule["cart"] == "excluded" and "allergen.milk" in rule["detail"]
    # the intent is still usable for a correct order
    assert store().get_intent(it["intent_id"])["status"] == "confirmed"


def test_block_quote_for_other_quantity(http):
    it = confirm(http, propose("120 l lactose-free milk", "lactose-free milk", 120))
    q = run(server.check_offer(product_ids=["DEMO-1001"], requested_date=it["deliver_by"], quantity=12,
                               quantity_unit="L", ctx=ctx()))["offers"][0]
    res = order(it, q["quote_id"])
    assert res["tier"] == "block" and any(c["field"] == "binding" and not c["ok"] for c in res["diff"]["checks"])


def test_tampered_quote_is_blocked(http):
    it = confirm(http, propose("120 l lactose-free milk", "lactose-free milk", 120))
    o = next(o for o in offers_for(it) if o["product_id"] == "DEMO-1001")
    s = store()
    row = s.get_quote(o["quote_id"])
    row["data"]["unit_price"] = 0.01
    s._exec("UPDATE quotes SET data=? WHERE quote_id=?", (json.dumps(row["data"]), o["quote_id"]))
    res = order(it, o["quote_id"])
    assert res["tier"] == "block"


def test_mandate_rules_always_apply(http):
    it = propose("50 l lactose-free milk", "lactose-free milk", 50, buyer="school-kitchen-espoo")
    assert {r.get("allergen") for r in it["hard_rules"] if r["source"] == "mandate"} == {"peanuts", "tree nuts"}
    assert all(c["locked"] for c in it["chips"] if c["label"].startswith("no "))


# --------------------------------------------------------------------------- the human gate

def test_unconfirmed_intent_cannot_order(http):
    it = propose("120 l lactose-free milk", "lactose-free milk", 120)
    assert run(server.check_offer(product_ids=["DEMO-1001"], intent_id=it["intent_id"], ctx=ctx()))["error"]


def test_confirm_requires_matching_hash_signature_and_no_questions(http):
    it = propose("120 l lactose-free milk", "lactose-free milk", 120)
    sig = trust.sign(trust.buyer_key("juvenes"), it["intent_hash"])
    assert http.post(f"/internal/intents/{it['intent_id']}/confirm", headers=AUTH,
                     json={"intent_hash": "sha256:beef", "signature": sig}).status_code == 409
    assert http.post(f"/internal/intents/{it['intent_id']}/confirm", headers=AUTH,
                     json={"intent_hash": it["intent_hash"], "signature": "AAAA"}).status_code == 403
    assert http.post(f"/internal/intents/{it['intent_id']}/confirm",
                     json={"intent_hash": it["intent_hash"], "signature": sig}).status_code == 401
    q = propose("Need 100 l non-dairy milk", "milk", 1000)
    assert len(q["questions"]) == 2
    r = http.post(f"/internal/intents/{q['intent_id']}/confirm", headers=AUTH,
                  json={"intent_hash": q["intent_hash"], "signature": trust.sign(trust.buyer_key("juvenes"), q["intent_hash"])})
    assert r.status_code == 409


def test_revise_creates_new_hash_and_supersedes(http):
    it = propose("120 l lactose-free milk", "lactose-free milk", 120)
    r = http.post(f"/internal/intents/{it['intent_id']}/revise", headers=AUTH,
                  json={"edits": {"quantity": {"amount": 240}}})
    new = r.json()
    assert r.status_code == 200 and new["intent_hash"] != it["intent_hash"] and new["quantity"]["amount"] == 240
    assert store().get_intent(it["intent_id"])["status"] == "superseded"
    assert new["questions"] == []  # the buyer's own edit wins over the original text
