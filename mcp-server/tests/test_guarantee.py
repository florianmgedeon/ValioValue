"""The guarantee as a property: an order Valio Aimo confirms on its own never deviates from the
confirmed intent. Hypothesis generates thousands of intent/quote pairs, including nasty ones."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from hypothesis import HealthCheck, given, settings, strategies as st

from valio_mcp import constraints, diff, intent as intents, policy, trust
from valio_mcp.catalog import catalog
from valio_mcp.store import iso, now

MANDATE = policy.buyer_policy("juvenes")
PRODUCTS = sorted(catalog().products)
BY = (date.today() + timedelta(days=5)).isoformat()


def make_intent(amount, unit, pct, under, rules, budget, query):
    it = intents.build(buyer_id="juvenes", mandate=MANDATE, session=None, original_request="", product_query=query,
                       quantity=amount, quantity_unit=unit, deliver_by=BY, hard_rules=rules, optimise="price",
                       origin_preference=None, budget_max_total=budget, max_over_pct=pct, allow_under=under,
                       agent_questions=[], check_text=False)
    it["status"] = "confirmed"
    return it


def make_quote(pid, qty, content, unit_price, avail_amount, day_offset, req_amount, req_unit, buyer, expired):
    d = date.fromisoformat(BY)
    line = {"quote_id": "q_prop", "product_id": pid, "name": catalog().get(pid).name, "quantity": qty,
            "sales_unit": "PAK", "unit_price": unit_price, "total_price": round(unit_price * qty, 2), "currency": "EUR",
            "requested_date": BY, "available_date": (d + timedelta(days=day_offset)).isoformat(),
            "available_amount": avail_amount, "content_per_sales_unit": content, "comparison_unit": "L",
            "requested_quantity": req_amount, "requested_unit": req_unit, "sales_block": False,
            "closing_time": None, "fulfil_issues": [],
            "quote_expires_at": iso(now() + timedelta(minutes=-1 if expired else 30))}
    line["provenance"] = trust.valio_sign_obj(diff.quote_core(line))
    return {"quote_id": "q_prop", "buyer_id": buyer, "data": line, "expires_at": line["quote_expires_at"]}


RULES = st.lists(st.sampled_from([{"rule": "lactose_free"}, {"rule": "exclude_allergen", "allergen": "milk"},
                                  {"rule": "exclude_allergen", "allergen": "oats"},
                                  {"rule": "origin", "country": "fi"}]), max_size=2)


@settings(max_examples=1500, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(amount=st.integers(1, 3000), pct=st.sampled_from([0, 1, 2, 5]), under=st.booleans(), rules=RULES,
       budget=st.one_of(st.none(), st.integers(50, 3000)), query=st.sampled_from(["lactose-free milk", "milk", "oat drink"]),
       pid=st.sampled_from(PRODUCTS), qty=st.integers(1, 3500), content=st.sampled_from([None, 0.5, 1.0, 1.5, 10.0]),
       unit_price=st.sampled_from([0.49, 0.89, 1.19, 1.35, 10.9]), avail=st.integers(0, 4000),
       day_offset=st.integers(-2, 2), same_request=st.booleans(), same_buyer=st.booleans(), expired=st.booleans())
def test_autonomous_orders_never_deviate(amount, pct, under, rules, budget, query, pid, qty, content, unit_price,
                                          avail, day_offset, same_request, same_buyer, expired):
    it = make_intent(amount, "L", pct, under, rules, budget, query)
    quote = make_quote(pid, qty, content, unit_price, avail, day_offset,
                       amount if same_request else amount + 1, "L", "juvenes" if same_buyer else "someone-else",
                       expired)
    d = diff.compute(it, quote, MANDATE)
    tier = policy.tier(d["checks"])
    if tier != "auto":
        return
    line = quote["data"]
    # nothing less, nothing more than the tolerance
    got = Decimal(str(qty)) * Decimal(str(content))
    assert got >= amount or under
    assert got <= Decimal(amount) * (1 + Decimal(pct) / 100)
    # nothing different
    assert pid in catalog().matching_ids(query)
    for ev in constraints.evaluate_all(catalog().get(pid), it["hard_rules"]):
        assert ev["verdict"] == "safe"
    assert line["available_date"] <= BY and avail >= qty
    assert line["total_price"] <= MANDATE["spend_limit"] and (budget is None or line["total_price"] <= budget)
    assert same_request and same_buyer and not expired
    assert d["deviations"] == 0
