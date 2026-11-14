"""V3 mandates: Must / Prefer / Autonomy, editable over REST, versioned, enforced in Cedar."""
from __future__ import annotations

import pytest

from conftest import next_monday
from test_flow import AUTH, ctx, run
from valio_mcp import mandate as mandates, policy, server, trust
from valio_mcp.rules import RuleError

BUYER = "kaleva-test"




def put(http, m, buyer=BUYER, sign_with=None):
    pre = http.post(f"/internal/mandate/{buyer}/preview", headers=AUTH, json={"mandate": m})
    assert pre.status_code == 200, pre.text
    h = pre.json()["mandate_hash"]
    sig = trust.sign(trust.buyer_key(sign_with or buyer), h)
    r = http.put(f"/internal/mandate/{buyer}", headers=AUTH,
                 json={"mandate": m, "mandate_hash": h, "signature": sig, "updated_by": "anna"})
    assert r.status_code == 200, r.text
    return r.json()


def intent(http, items, buyer=BUYER):
    it = run(server.propose_intent(original_request="test", items=items, deliver_by=next_monday(), ctx=ctx(buyer)))
    it = it["intent"]
    sig = trust.sign(trust.buyer_key(buyer), it["intent_hash"])
    r = http.post(f"/internal/intents/{it['intent_id']}/confirm", headers=AUTH,
                  json={"intent_hash": it["intent_hash"], "signature": sig})
    assert r.status_code == 200, r.text
    return r.json()


def quote(it, pid, item_id="i1", buyer=BUYER):
    return run(server.check_offer(product_ids=[pid], intent_id=it["intent_id"], item_id=item_id, ctx=ctx(buyer)))["offers"][0]


def order(it, lines, buyer=BUYER):
    return run(server.place_order(intent_id=it["intent_id"], reason="test", ctx=ctx(buyer),
                                  lines=[{"item_id": i, "quote_id": q} for i, q in lines]))


MILK = [{"product_query": "lactose-free milk", "quantity": 120, "quantity_unit": "L"}]
FINNISH_LABEL = {"id": "p1", "attr": "labels", "op": "contains", "value": "HYVAA_SUOMESTA", "weight": 3}


# --------------------------------------------------------------------------- format

def test_v2_policies_are_migrated():
    j = policy.buyer_policy("juvenes")
    assert j["version"] >= 1 and j["autonomy"]["grants"][0]["limits"]["max_order_total"] == 1000
    s = policy.buyer_policy("school-kitchen-espoo")
    musts = s["mandatory_rules"]
    assert [m["id"] for m in musts] == ["m1", "m2"]
    assert musts[0]["attr"] == "allergens" and musts[0]["value"] == "peanuts"
    assert musts[0]["rule"] == "exclude_allergen"  # V2 shape kept for older clients


def test_any_data_attribute_is_a_rule_without_defining_rule_kinds():
    m = mandates.normalise({"mandatory_rules": [{"attr": "delivery_reliability", "op": "gte", "value": 0.95},
                                                {"attr": "labels", "op": "contains", "value": "EU-Bio"},
                                                {"attr": "unit_price", "op": "lte", "value": 1.4}]})
    assert [r["label"] for r in m["mandatory_rules"]] == [
        "delivery reliability ≥ 0.95", "labels includes EU-Bio", "unit price ≤ 1.4"]
    for bad in ({"attr": "vegan", "op": "eq", "value": True}, {"attr": "country", "op": "lt", "value": "fi"},
                {"attr": "unit_price", "op": "lte", "value": "cheap"}):
        with pytest.raises(RuleError):
            mandates.normalise({"mandatory_rules": [bad]})


def test_catalog_lists_attributes_from_data(http):
    c = http.get("/internal/policy-catalog", headers=AUTH).json()
    country = next(a for a in c["attributes"] if a["attr"] == "country")
    assert "fi" in country["examples"] and "eq" in country["ops"]


# --------------------------------------------------------------------------- REST: what you see is what you sign

def test_put_needs_previewed_hash_and_buyer_signature(http):
    m = {"display_name": "Kaleva", "mandatory_rules": [{"rule": "lactose_free"}]}
    pre = http.post(f"/internal/mandate/{BUYER}/preview", headers=AUTH, json={"mandate": m}).json()
    wrong = http.put(f"/internal/mandate/{BUYER}", headers=AUTH,
                     json={"mandate": m, "mandate_hash": "sha256:00", "signature": "x"})
    assert wrong.status_code == 409
    forged = http.put(f"/internal/mandate/{BUYER}", headers=AUTH,
                      json={"mandate": m, "mandate_hash": pre["mandate_hash"],
                            "signature": trust.sign(trust.valio_key(), pre["mandate_hash"])})
    assert forged.status_code == 403
    assert http.put(f"/internal/mandate/{BUYER}", json={"mandate": m}).status_code == 401
    bad = http.post(f"/internal/mandate/{BUYER}/preview", headers=AUTH,
                    json={"mandate": {"mandatory_rules": [{"attr": "nope", "op": "eq", "value": 1}]}})
    assert bad.status_code == 422 and "unknown attribute" in bad.json()["error"]
    v1 = put(http, m)
    v2 = put(http, m | {"display_name": "Hotel Kaleva"})
    assert v2["version"] == v1["version"] + 1 and v2["mandate_hash"] != v1["mandate_hash"]
    hist = http.get(f"/internal/mandate/{BUYER}/history", headers=AUTH).json()
    assert [h["version"] for h in hist][:2] == [v2["version"], v1["version"]]
    ced = http.get(f"/internal/mandate/{BUYER}/cedar", headers=AUTH).json()
    assert "forbid" in ced["cedar"] and "m1" in ced["policy_ids"]


# --------------------------------------------------------------------------- Prefer

def test_preference_ranks_offers_and_a_miss_asks(http):
    put(http, {"preferences": [FINNISH_LABEL],
               "autonomy": {"grants": [{"id": "g1", "limits": {"max_order_total": 2000}}]}})
    it = intent(http, MILK)
    offers = run(server.check_offer(product_ids=["DEMO-1002", "DEMO-1001"], intent_id=it["intent_id"],
                                    ctx=ctx(BUYER)))["offers"]
    # 1002 is cheaper per litre, but 1001 carries the label the buyer prefers
    assert offers[0]["product_id"] == "DEMO-1001" and offers[0]["preference_score"] == 3
    res = order(it, [("i1", offers[1]["quote_id"])])
    assert res["tier"] == "ask", res
    pref = next(c for c in res["diff"]["checks"] if c["field"] == "preference")
    assert not pref["ok"] and pref["pref_id"] == "p1"


def test_grant_that_accepts_the_preference_lets_the_agent_decide(http):
    put(http, {"preferences": [FINNISH_LABEL],
               "autonomy": {"grants": [{"id": "g1", "limits": {"max_order_total": 2000},
                                        "accepts": [{"check": "preference", "id": "p1"}]}]}})
    it = intent(http, MILK)
    res = order(it, [("i1", quote(it, "DEMO-1002")["quote_id"])])
    assert res["tier"] == "auto", res
    assert res["diff"]["accepted"] == ["preference:p1"] and res["receipt"]["grants_used"] == ["g1"]


# --------------------------------------------------------------------------- Autonomy

def test_no_grants_means_every_order_asks(http):
    put(http, {"autonomy": {"grants": []}})
    it = intent(http, MILK)
    res = order(it, [("i1", quote(it, "DEMO-1001")["quote_id"])])
    assert res["tier"] == "ask" and "grants no autonomy" in next(
        c for c in res["diff"]["checks"] if c["field"] == "autonomy")["detail"]


def test_scope_and_accepted_delay(http):
    late_ok = {"id": "gfi", "scope": [{"attr": "country", "op": "eq", "value": "fi"}],
               "limits": {"max_order_total": 500}, "accepts": [{"check": "delivery_date", "max_days_late": 1}]}
    put(http, {"autonomy": {"grants": [late_ok]}})
    it = intent(http, MILK)
    res = order(it, [("i1", quote(it, "DEMO-1003")["quote_id"])])  # arrives one day late
    assert res["tier"] == "auto" and res["diff"]["accepted"] == ["delivery_date"], res
    put(http, {"autonomy": {"grants": [late_ok | {"accepts": [{"check": "delivery_date", "max_days_late": 0}]}]}})
    it = intent(http, MILK)
    assert order(it, [("i1", quote(it, "DEMO-1003")["quote_id"])])["tier"] == "ask"
    put(http, {"autonomy": {"grants": [late_ok | {"scope": [{"attr": "country", "op": "eq", "value": "se"}]}]}})
    it = intent(http, MILK)
    assert order(it, [("i1", quote(it, "DEMO-1003")["quote_id"])])["tier"] == "ask"


def test_grant_limit_counts_only_its_own_lines(http):
    """Decision D4 (a): the Finnish-label grant (300 EUR) covers the milk line, the general grant the butter."""
    put(http, {"preferences": [FINNISH_LABEL],
               "autonomy": {"grants": [
                   {"id": "gsmall", "scope": [{"attr": "category", "op": "eq", "value": "DEV-DAIRY"}],
                    "limits": {"max_order_total": 150}, "accepts": [{"check": "preference"}]},
                   {"id": "gbig", "limits": {"max_order_total": 2000}}]}})
    items = MILK + [{"product_query": "butter", "quantity": 100, "quantity_unit": "SALES_UNIT"}]
    it = intent(http, items)
    q1 = quote(it, "DEMO-1002", "i1")    # 130.80 EUR, misses the label -> only gsmall
    q2 = quote(it, "DEMO-2003", "i2")    # 200 EUR, has the label -> gsmall or gbig
    res = order(it, [("i1", q1["quote_id"]), ("i2", q2["quote_id"])])
    assert res["tier"] == "auto", res["diff"]
    assert res["receipt"]["grants_used"] == ["gbig", "gsmall"]


def test_must_always_blocks_even_with_a_generous_grant(http):
    put(http, {"mandatory_rules": [{"attr": "labels", "op": "contains", "value": "HYVAA_SUOMESTA"}],
               "autonomy": {"grants": [{"id": "gall", "accepts": [{"check": c} for c in mandates.ACCEPT_CHECKS]}]}})
    it = intent(http, MILK)
    res = order(it, [("i1", quote(it, "DEMO-1002")["quote_id"])])
    assert res["tier"] == "block" and "labels" in res["message"], res
    found = run(server.search_products(query="lactose-free milk", intent_id=it["intent_id"], ctx=ctx(BUYER)))
    assert [p["product_id"] for p in found["products"]] == ["DEMO-1001"]


def test_unknown_data_can_ask_instead_of_exclude(http):
    put(http, {"mandatory_rules": [{"attr": "delivery_reliability", "op": "gte", "value": 0.9, "if_unknown": "ask"}],
               "autonomy": {"grants": [{"id": "g1", "limits": {"max_order_total": 2000}}]}})
    it = intent(http, MILK)
    found = run(server.search_products(query="lactose-free milk", intent_id=it["intent_id"], ctx=ctx(BUYER)))
    assert found["products"], "unknown reliability is not excluded when the rule says ask"
    res = order(it, [("i1", quote(it, "DEMO-1001")["quote_id"])])
    assert res["tier"] == "ask", res


# --------------------------------------------------------------------------- versions

def test_orders_use_the_current_mandate_and_receipts_name_it(http):
    put(http, {"autonomy": {"grants": [{"id": "g1", "limits": {"max_order_total": 2000}}]}})
    it = intent(http, MILK)
    q = quote(it, "DEMO-1002")
    m = put(http, {"mandatory_rules": [{"attr": "labels", "op": "contains", "value": "HYVAA_SUOMESTA"}],
                   "autonomy": {"grants": [{"id": "g1", "limits": {"max_order_total": 2000}}]}})
    res = order(it, [("i1", q["quote_id"])])  # confirmed before the change, decided after it
    assert res["tier"] == "block", res
    it2 = intent(http, MILK)
    ok = order(it2, [("i1", quote(it2, "DEMO-1001")["quote_id"])])
    assert ok["tier"] == "auto" and ok["receipt"]["mandate_id"] == m["mandate_id"]
    v = http.get(f"/verify/{ok['order_id']}").json()
    assert v["valid"], v
    assert {c["name"] for c in v["checks"] if c["ok"]} >= {"autonomy", "mandate_hash", "valio_receipt_signature"}
