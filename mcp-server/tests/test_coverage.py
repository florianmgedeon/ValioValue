"""3.2 Rule coverage: honest counts over the catalog, unknown never counted as safe."""
from __future__ import annotations

from test_flow import AUTH
from valio_mcp import coverage, policy
from valio_mcp.catalog import catalog


def test_counts_add_up_and_unknown_is_separate():
    c = coverage.rule_coverage({"attr": "allergens", "op": "not_contains", "value": "sesame"})
    assert c["checked"] == len(catalog().products)
    assert c["known"] == c["safe"] + c["excluded"]
    assert c["checked"] == c["known"] + c["unknown"]
    assert c["label"].startswith("no sesame")


def test_seeded_musts_have_coverage(http):
    for m in policy.buyer_policy("school-kitchen-espoo")["mandatory_rules"]:
        r = http.post("/internal/rule-coverage", headers=AUTH, json={"rule": m})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["checked"] > 0 and body["known"] + body["unknown"] == body["checked"]


def test_applies_to_limits_checked_products():
    milk = next(p for p in catalog().products.values() if "milk" in p.name_en)
    c = coverage.rule_coverage({"attr": "country", "op": "eq", "value": "fi",
                                "applies_to": [{"attr": "category", "op": "in", "value": [milk.category]}]})
    assert 0 < c["checked"] == sum(1 for p in catalog().products.values() if p.category == milk.category)


def test_category_filter_and_cache():
    p = next(iter(catalog().products.values()))
    a = coverage.rule_coverage({"rule": "lactose_free"}, category=p.category)
    assert a["category"] == p.category and a["checked"] <= len(catalog().products)
    assert coverage.rule_coverage({"rule": "lactose_free"}, category=p.category) is a  # cached


def test_offer_attribute_is_not_counted():
    c = coverage.rule_coverage({"attr": "unit_price", "op": "lte", "value": 10})
    assert c["checked"] == 0 and "price" in c["note"]


def test_bad_rule_is_422_and_token_required(http):
    r = http.post("/internal/rule-coverage", headers=AUTH, json={"rule": {"attr": "colour", "op": "eq", "value": "x"}})
    assert r.status_code == 422 and "unknown attribute" in r.json()["error"]
    assert http.post("/internal/rule-coverage", json={"rule": {"rule": "lactose_free"}}).status_code == 401


def test_categories_route(http):
    r = http.get("/internal/categories", headers=AUTH, params={"q": "milk"})
    assert r.status_code == 200
    assert all({"category", "share", "examples"} <= set(c) for c in r.json())
