"""Package 1c: the buyer's usual product on the intent, in the ranking, in the diff, and the funnel per rule."""
from __future__ import annotations

import dataclasses

import pytest

from conftest import next_monday
from test_flow import AUTH, ctx, run
from test_mandate import put
from valio_mcp import intent as intents, policy, server, trust
from valio_mcp.catalog import catalog

BUYER = "usual-test"
NUTS = [{"rule": "exclude_allergen", "allergen": "peanuts"}, {"rule": "exclude_allergen", "allergen": "tree nuts"}]


@pytest.fixture(scope="module", autouse=True)
def granola_products():
    """Two granolas in the sample catalog: the usual one now 'may contain tree nuts', the other is safe."""
    c = catalog()
    base = c.get("DEMO-2001")
    fakes = [dataclasses.replace(base, id="DEMO-3001", name="Crunchy granola 2.5 kg (DEV SAMPLE)",
                                 names_all="crunchy granola 2.5 kg", name_en="crunchy granola 2.5 kg",
                                 allergens_contains=["oats"], allergens_may_contain=["tree nuts"],
                                 has_allergen_data=True, labels=[]),
             dataclasses.replace(base, id="DEMO-3002", name="Plain granola 2 kg (DEV SAMPLE)",
                                 names_all="plain granola 2 kg", name_en="plain granola 2 kg",
                                 allergens_contains=["oats"], allergens_may_contain=[], has_allergen_data=True,
                                 labels=[])]
    for p in fakes:
        c.products[p.id] = p
    if hasattr(c, "_match_cache"):
        del c._match_cache
    yield
    for p in fakes:
        c.products.pop(p.id, None)
    if hasattr(c, "_match_cache"):
        del c._match_cache


def confirmed(http, items, buyer=BUYER):
    it = run(server.propose_intent(original_request="test", items=items, deliver_by=next_monday(),
                                   ctx=ctx(buyer)))["intent"]
    r = http.post(f"/internal/intents/{it['intent_id']}/confirm", headers=AUTH,
                  json={"intent_hash": it["intent_hash"], "signature": trust.sign(trust.buyer_key(buyer), it["intent_hash"])})
    assert r.status_code == 200, r.text
    return r.json()


MILK = {"product_query": "lactose-free milk", "quantity": 120, "quantity_unit": "L"}


def test_preferred_product_is_in_the_signed_binding():
    m = policy.buyer_policy("juvenes")
    kw = dict(buyer_id="juvenes", mandate=m, session=None, original_request="", deliver_by=next_monday(),
              hard_rules=[], optimise="price", origin_preference=None, budget_max_total=None, max_over_pct=2,
              allow_under=False, agent_questions=[], check_text=False, intent_id="int_fixed")
    plain = intents.build(items=[MILK], **kw)
    usual = intents.build(items=[MILK | {"preferred_product_id": "DEMO-1002"}], **kw)
    other = intents.build(items=[MILK | {"preferred_product_id": "DEMO-1001"}], **kw)
    assert usual["items"][0]["product"]["preferred_product_id"] == "DEMO-1002"
    assert len({plain["intent_hash"], usual["intent_hash"], other["intent_hash"]}) == 3
    assert "your usual: Lactose-free skimmed milk drink 10 l" in usual["readback"]
    assert usual["chips"][0]["usual"] == {"product_id": "DEMO-1002", "name": catalog().get("DEMO-1002").name}
    # unknown id: dropped with a note, never fails
    bad = intents.build(items=[MILK | {"preferred_product_id": "NOPE-1"}], **kw)
    assert "preferred_product_id" not in bad["items"][0]["product"] and bad["intent_hash"] == plain["intent_hash"]
    assert any("NOPE-1" in n for n in bad["notes"]) and "NOPE-1" in bad["readback"]


def test_usual_product_ranks_first_and_orders_alone(http):
    put(http, {"autonomy": {"grants": [{"id": "g1", "limits": {"max_order_total": 5000},
                                        "accepts": [{"check": "quantity", "max_over_pct": 5}]}]}}, buyer=BUYER)
    it = confirmed(http, [MILK | {"preferred_product_id": "DEMO-1002"}])
    found = run(server.search_products(query="lactose-free milk", intent_id=it["intent_id"], ctx=ctx(BUYER)))
    assert found["products"][0]["product_id"] == "DEMO-1002" and found["products"][0]["usual"]
    assert found["usual_product"]["allowed"]
    # the agent forgot the usual product: it is quoted anyway, and ranked best although 1001 is cheaper per litre
    res = run(server.check_offer(product_ids=["DEMO-1001", "DEMO-1003"], intent_id=it["intent_id"], ctx=ctx(BUYER)))
    best = res["offers"][0]
    assert best["product_id"] == "DEMO-1002" and best["usual"] and best["rank"] == "best"
    assert [s["step"] for s in res["funnel_tail"]] == ["available", "usual", "pick"]
    order = run(server.place_order(intent_id=it["intent_id"], reason="usual", quote_id=best["quote_id"], ctx=ctx(BUYER)))
    assert order["tier"] == "auto", order
    usual = next(c for c in order["diff"]["checks"] if c["field"] == "usual")
    assert usual["ok"]


def test_broken_usual_is_excluded_and_another_product_asks(http):
    put(http, {"mandatory_rules": NUTS,
               "autonomy": {"grants": [{"id": "g1", "limits": {"max_order_total": 5000}}]}}, buyer=BUYER)
    item = {"product_query": "granola", "quantity": 6, "quantity_unit": "SALES_UNIT", "preferred_product_id": "DEMO-3001"}
    it = confirmed(http, [item])
    found = run(server.search_products(query="granola", intent_id=it["intent_id"], ctx=ctx(BUYER)))
    ex = next(e for e in found["excluded"] if e["product_id"] == "DEMO-3001")
    assert ex["usual"] and "tree nuts" in ex["reason"]
    assert [p["product_id"] for p in found["products"]] == ["DEMO-3002"]
    assert found["usual_product"]["allowed"] is False
    res = run(server.check_offer(product_ids=["DEMO-3002"], intent_id=it["intent_id"], ctx=ctx(BUYER)))
    assert res["offers"][0]["product_id"] == "DEMO-3002"  # the usual one cannot fulfil: it breaks a must
    usual_offer = next(o for o in res["offers"] if o["product_id"] == "DEMO-3001")
    assert not usual_offer["can_fulfil"]
    order = run(server.place_order(intent_id=it["intent_id"], reason="other granola",
                                   quote_id=res["offers"][0]["quote_id"], ctx=ctx(BUYER)))
    assert order["tier"] == "ask", order
    usual = next(c for c in order["diff"]["checks"] if c["field"] == "usual")
    assert not usual["ok"] and usual["detail"] == "not your usual product"


def test_a_grant_can_accept_a_different_product(http):
    put(http, {"mandatory_rules": NUTS,
               "autonomy": {"grants": [{"id": "g1", "limits": {"max_order_total": 5000},
                                        "accepts": [{"check": "usual"}]}]}}, buyer=BUYER)
    item = {"product_query": "granola", "quantity": 6, "quantity_unit": "SALES_UNIT", "preferred_product_id": "DEMO-3001"}
    it = confirmed(http, [item])
    res = run(server.check_offer(product_ids=["DEMO-3002"], intent_id=it["intent_id"], ctx=ctx(BUYER)))
    order = run(server.place_order(intent_id=it["intent_id"], reason="x", quote_id=res["offers"][0]["quote_id"],
                                   ctx=ctx(BUYER)))
    assert order["tier"] == "auto" and order["diff"]["accepted"] == ["usual"], order
    # and the usual granola itself is still blocked, whatever the grant says
    it2 = confirmed(http, [item])
    bad = run(server.check_offer(product_ids=["DEMO-3001"], intent_id=it2["intent_id"], ctx=ctx(BUYER)))["offers"][0]
    assert run(server.place_order(intent_id=it2["intent_id"], reason="x", quote_id=bad["quote_id"],
                                  ctx=ctx(BUYER)))["tier"] == "block"


def test_funnel_counts_add_up(http):
    put(http, {"mandatory_rules": NUTS + [{"rule": "lactose_free"}],
               "preferences": [{"attr": "labels", "op": "contains", "value": "HYVAA_SUOMESTA", "weight": 3}],
               "autonomy": {"grants": []}}, buyer=BUYER)
    it = confirmed(http, [{"product_query": "milk", "quantity": 20, "quantity_unit": "L"}])
    found = run(server.search_products(query="milk", intent_id=it["intent_id"], ctx=ctx(BUYER)))
    f = found["funnel"]
    assert f[0]["step"] == "catalog" and [s.get("rule_id") for s in f[1:]] == ["m1", "m2", "m3"]
    counts = [s["count"] for s in f]
    assert all(a >= b for a, b in zip(counts, counts[1:])), f
    assert counts[-1] == len(found["products"]) or found["returned"] == 10
    assert counts[0] - counts[-1] == found["excluded_count"]
    res = run(server.check_offer(product_ids=[p["product_id"] for p in found["products"]], intent_id=it["intent_id"],
                                 ctx=ctx(BUYER)))
    tail = [s["count"] for s in res["funnel_tail"]]
    assert all(a >= b for a, b in zip(tail, tail[1:])) and tail[0] <= len(res["offers"])
    assert res["funnel_tail"][1]["step"] == "prefer" and res["funnel_tail"][-1]["count"] == 1


def test_catalog_offers_the_usual_accept_check(http):
    c = http.get("/internal/policy-catalog", headers=AUTH).json()
    assert "usual" in [a["check"] for a in c["accept_checks"]]


def test_substitute_products_and_lock_are_in_the_signed_binding():
    m = policy.buyer_policy("juvenes")
    kw = dict(buyer_id="juvenes", mandate=m, session=None, original_request="", deliver_by=next_monday(),
              hard_rules=[], optimise="price", origin_preference=None, budget_max_total=None, max_over_pct=2,
              allow_under=False, agent_questions=[], check_text=False, intent_id="int_subs")
    usual_only = intents.build(items=[MILK | {"preferred_product_id": "DEMO-1002"}], **kw)
    sub_open = intents.build(items=[MILK | {"preferred_product_id": "DEMO-1002",
                                            "substitute_product_ids": ["DEMO-1005", "DEMO-1001"],
                                            "allow_other_substitutes": True}], **kw)
    sub_locked = intents.build(items=[MILK | {"preferred_product_id": "DEMO-1002",
                                              "substitute_product_ids": ["DEMO-1005", "DEMO-1001"],
                                              "allow_other_substitutes": False}], **kw)
    sub_reordered = intents.build(items=[MILK | {"preferred_product_id": "DEMO-1002",
                                                 "substitute_product_ids": ["DEMO-1001", "DEMO-1005"],
                                                 "allow_other_substitutes": False}], **kw)
    no_subs = intents.build(items=[MILK | {"preferred_product_id": "DEMO-1002",
                                           "allow_other_substitutes": False}], **kw)
    assert len({usual_only["intent_hash"], sub_open["intent_hash"], sub_locked["intent_hash"],
                sub_reordered["intent_hash"], no_subs["intent_hash"]}) == 5
    assert sub_locked["items"][0]["product"] == {
        "query": "lactose-free milk",
        "preferred_product_id": "DEMO-1002",
        "substitute_product_ids": ["DEMO-1005", "DEMO-1001"],
        "allow_other_substitutes": False,
    }
    assert "[fallback only:" in sub_locked["readback"]
    assert "[no substitutes allowed]" in no_subs["readback"]
    assert sub_locked["chips"][0]["allow_other_substitutes"] is False
    assert [s["product_id"] for s in sub_locked["chips"][0]["substitutes"]] == ["DEMO-1005", "DEMO-1001"]

    # Unknown or duplicate substitute IDs (or repeating primary) are cleaned up with a note
    cleaned = intents.build(items=[MILK | {"preferred_product_id": "DEMO-1002",
                                           "substitute_product_ids": ["DEMO-1002", "DEMO-1005", "DEMO-1005", "NOPE-9"],
                                           "allow_other_substitutes": False}], **kw)
    assert cleaned["items"][0]["product"]["substitute_product_ids"] == ["DEMO-1005"]
    assert any("NOPE-9" in n for n in cleaned["notes"])


def test_ordered_substitutes_take_over_in_priority_order_and_order_autonomously(http):
    # DEMO-1002 only has 60 units (600 L) available; 700 L forces fallback.
    # DEMO-1005 (1.49/L) is Fallback #1, DEMO-1001 (1.19/L) is Fallback #2; DEMO-1004 (0.89/L) is unapproved.
    put(http, {"autonomy": {"grants": [{
        "id": "g1",
        "scope": [{"attr": "product_id", "op": "in", "value": ["DEMO-1002", "DEMO-1005", "DEMO-1001"]}],
        "limits": {"max_order_total": 5000},
        "accepts": [{"check": "quantity", "max_over_pct": 5}],
    }]}}, buyer=BUYER)
    item = {
        "product_query": "milk",
        "quantity": 700,
        "quantity_unit": "L",
        "preferred_product_id": "DEMO-1002",
        "substitute_product_ids": ["DEMO-1005", "DEMO-1001"],
        "allow_other_substitutes": False,
    }
    it = confirmed(http, [item])
    found = run(server.search_products(query="milk", intent_id=it["intent_id"], ctx=ctx(BUYER)))
    # Strictly restricted to usual + listed substitutes (unapproved DEMO-1004 is excluded from search)
    assert [p["product_id"] for p in found["products"]] == ["DEMO-1002", "DEMO-1005", "DEMO-1001"]
    assert found["products"][1]["approved_substitute"] is True and found["products"][1]["substitute_rank"] == 1
    assert found["products"][2]["approved_substitute"] is True and found["products"][2]["substitute_rank"] == 2
    counts = [s["count"] for s in found["funnel"]]
    assert all(a >= b for a, b in zip(counts, counts[1:])), found["funnel"]

    # Even if the caller passes unapproved DEMO-1004 to check_offer, Fallback #1 (DEMO-1005) wins ahead of cheaper
    # Fallback #2 (DEMO-1001), and DEMO-1004 is marked can_fulfil=False.
    res = run(server.check_offer(product_ids=["DEMO-1004", "DEMO-1001"], intent_id=it["intent_id"], ctx=ctx(BUYER)))
    by_id = {o["product_id"]: o for o in res["offers"]}
    assert not by_id["DEMO-1002"]["can_fulfil"]
    assert not by_id["DEMO-1004"]["can_fulfil"]
    assert any("not an allowed substitute" in msg for msg in by_id["DEMO-1004"]["fulfil_issues"])
    best = res["offers"][0]
    second = res["offers"][1]
    assert best["product_id"] == "DEMO-1005" and best["rank"] == "best"
    assert best["approved_substitute"] is True and best["is_substitute"] is True and best["substitute_rank"] == 1
    assert "Fallback #1" in best["substitute_reason"]
    assert second["product_id"] == "DEMO-1001" and second["rank"] == "better" and second["substitute_rank"] == 2

    order = run(server.place_order(intent_id=it["intent_id"], reason="approved fallback #1",
                                   quote_id=best["quote_id"], ctx=ctx(BUYER)))
    assert order["tier"] == "auto", order
    usual_chk = next(c for c in order["diff"]["checks"] if c["field"] == "usual")
    assert usual_chk["ok"] and "approved substitute #1" in usual_chk["detail"]
    ver = http.get(f"/verify/{order['order_id']}").json()
    assert ver["valid"] is True


def test_locked_substitutes_block_unapproved_products_even_with_permissive_grant(http):
    # Even if a Level-2 grant accepts 'usual' and 'availability' deviations, allow_other_substitutes=False
    # makes unapproved products a hard block (severity='block').
    put(http, {"autonomy": {"grants": [{
        "id": "g_all",
        "limits": {"max_order_total": 10000},
        "accepts": [{"check": "usual"}, {"check": "availability"}, {"check": "quantity", "max_over_pct": 10}],
    }]}}, buyer=BUYER)
    # Ask for 900 L: DEMO-1002 has 600 L and sole approved substitute DEMO-1005 has 800 L -> neither can fulfil,
    # while unapproved DEMO-1001 (1400 L) and DEMO-1004 (5000 L) have plenty of stock.
    item = {
        "product_query": "milk",
        "quantity": 900,
        "quantity_unit": "L",
        "preferred_product_id": "DEMO-1002",
        "substitute_product_ids": ["DEMO-1005"],
        "allow_other_substitutes": False,
    }
    it = confirmed(http, [item])
    res = run(server.check_offer(product_ids=["DEMO-1001", "DEMO-1004"], intent_id=it["intent_id"], ctx=ctx(BUYER)))
    assert res["recommended_product_id"] is None
    unapproved = next(o for o in res["offers"] if o["product_id"] == "DEMO-1001")
    assert not unapproved["can_fulfil"]
    blocked = run(server.place_order(intent_id=it["intent_id"], reason="try unapproved milk",
                                     quote_id=unapproved["quote_id"], ctx=ctx(BUYER)))
    assert blocked["tier"] == "block", blocked
    usual_chk = next(c for c in blocked["diff"]["checks"] if c["field"] == "usual")
    assert not usual_chk["ok"] and usual_chk["severity"] == "block"

    # Mode 'none': preferred_product_id with empty substitute_product_ids and allow_other_substitutes=False
    it_none = confirmed(http, [{
        "product_query": "milk",
        "quantity": 700,
        "quantity_unit": "L",
        "preferred_product_id": "DEMO-1002",
        "allow_other_substitutes": False,
    }])
    res_none = run(server.check_offer(product_ids=["DEMO-1005"], intent_id=it_none["intent_id"], ctx=ctx(BUYER)))
    assert res_none["recommended_product_id"] is None
    sub_quote = next(o for o in res_none["offers"] if o["product_id"] == "DEMO-1005")
    blocked_none = run(server.place_order(intent_id=it_none["intent_id"], reason="try substitute when none allowed",
                                          quote_id=sub_quote["quote_id"], ctx=ctx(BUYER)))
    assert blocked_none["tier"] == "block"
