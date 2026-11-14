"""Package 2b: the order list for the start screen and the clean-order counter."""
from __future__ import annotations

from test_flow import AUTH, confirm, ctx, offers_for, order, propose, run
from test_mandate import put
from valio_mcp import server, trust

BUYER = "orders-list-test"


def test_list_orders_newest_first_with_summaries(http):
    put(http, {"mandatory_rules": [{"rule": "exclude_allergen", "allergen": "milk"}],
               "autonomy": {"grants": [{"id": "g1", "limits": {"max_order_total": 5000}}]}}, buyer=BUYER)
    # 1: auto
    it = confirm(http, propose("24 l oat drink", "oat drink", 24, buyer=BUYER), buyer=BUYER)
    o1 = order(it, offers_for(it, ("DEMO-2007",), buyer=BUYER)[0]["quote_id"], buyer=BUYER)
    assert o1["status"] == "confirmed", [c for c in o1["diff"]["checks"] if not c["ok"]]
    # 2: blocked (milk), same intent shape
    it2 = confirm(http, propose("10 dark chocolate", "dark chocolate", 10, unit="SALES_UNIT", buyer=BUYER), buyer=BUYER)
    o2 = order(it2, offers_for(it2, ("DEMO-2008",), buyer=BUYER)[0]["quote_id"], buyer=BUYER)
    assert o2["status"] == "rejected"
    # 3: asks (not in stock in full) and a human approves
    it3 = confirm(http, propose("240 l oat drink", "oat drink", 240, buyer=BUYER), buyer=BUYER)
    q3 = offers_for(it3, ("DEMO-2007",), buyer=BUYER)[0]
    o3 = order(it3, q3["quote_id"], buyer=BUYER)
    assert o3["status"] in ("pending_approval", "confirmed")
    if o3["status"] == "pending_approval":
        a = o3["approval"]
        r = http.post(f"/internal/approvals/{a['approval_id']}", headers=AUTH, json={
            "decision": "approve", "decided_by": "Anna Virtanen", "cart_hash": a["cart_hash"],
            "signature": trust.sign(trust.buyer_key(BUYER), a["cart_hash"])})
        assert r.status_code == 200, r.text

    assert http.get(f"/internal/orders?buyer_id={BUYER}").status_code == 401
    rows = http.get(f"/internal/orders?buyer_id={BUYER}", headers=AUTH).json()
    assert [r["order_id"] for r in rows] == [o3["order_id"], o2["order_id"], o1["order_id"]]
    first, blocked, auto = rows
    assert auto["tier"] == "auto" and auto["status"] == "confirmed" and auto["grants_used"] == ["g1"]
    assert auto["title"] == "oat drink" and auto["lines"] == 1 and auto["total"] > 0
    assert blocked["status"] == "rejected" and blocked["blocked_rules"] == ["m1"]
    if first["tier"] == "ask":
        assert first["decided_by"] == "Anna Virtanen" and first["status"] == "confirmed"
    assert all(r["mandate_version"] and r["intent_revised"] is False for r in rows)

    page = http.get(f"/internal/orders?buyer_id={BUYER}&limit=1&before={o3['order_id']}", headers=AUTH).json()
    assert [r["order_id"] for r in page] == [o2["order_id"]]
    assert http.get("/internal/orders?limit=x", headers=AUTH).status_code == 422


def test_title_is_short_and_revised_intents_are_flagged(http):
    items = [{"product_query": f"product number {n} with a long name", "quantity": 1, "quantity_unit": "SALES_UNIT"}
             for n in range(4)]
    it = propose("list", None, None, buyer=BUYER, items=items)
    r = http.post(f"/internal/intents/{it['intent_id']}/revise", headers=AUTH,
                  json={"edits": {"items": [{"item_id": "i4", "remove": True}]}})
    new = confirm(http, r.json(), buyer=BUYER)
    q = run(server.check_offer(product_ids=["DEMO-2001"], intent_id=new["intent_id"], item_id="i1", ctx=ctx(BUYER)))
    lines = [{"item_id": "i1", "quote_id": q["offers"][0]["quote_id"]}]
    o = run(server.place_order(intent_id=new["intent_id"], reason="x", lines=lines, ctx=ctx(BUYER)))
    row = next(x for x in http.get(f"/internal/orders?buyer_id={BUYER}", headers=AUTH).json()
               if x["order_id"] == o["order_id"])
    assert row["intent_revised"] is True and row["product_changed"] is True
    assert len(row["title"]) <= 60
