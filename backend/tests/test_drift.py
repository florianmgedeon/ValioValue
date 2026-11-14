"""Package 2e: weeks per usual product, drift before "Order the usual", imported history stays out of Activity."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from buyer_agent import kitchen, learning

pytestmark = pytest.mark.anyio

TODAY = date(2026, 11, 14)  # a Saturday; last week starts Monday 2 Nov


def week(ago: int, lines: list[tuple]) -> dict:
    """Imported history week: lines are (word, product_id, qty, unit, price per unit)."""
    monday = TODAY - timedelta(days=TODAY.weekday()) - timedelta(weeks=ago)
    return {"week": monday, "imported": True,
            "lines": [{"word": w, "product_id": pid, "product_name": pid, "quantity": q, "unit": u,
                       "total_price": round(q * p, 2)} for w, pid, q, u, p in lines]}


def profile(qty: float = 120):
    p = kitchen.empty("x")
    kitchen.apply_usual(p, {"word": "milk", "product_id": "M", "product_name": "Milk", "typical_qty": qty, "unit": "L"}, None)
    kitchen.apply_usual(p, {"word": "butter", "product_id": "B", "product_name": "Butter", "typical_qty": 10, "unit": "KG"}, None)
    return p


def history(milk: list[float], milk_price: float = 1.0) -> list[dict]:
    """8 weeks, oldest first; 0 = no delivery that week."""
    return [week(8 - i, [("milk", "M", q, "L", milk_price), ("butter", "B", 10, "KG", 8.0)] if q else [])
            for i, q in enumerate(milk)]


def run(p: dict, hist: list[dict], orders: list[dict] | None = None):
    weeks = learning.weekly_lines(orders or [], hist, TODAY)
    return weeks, learning.drift(learning._active_usual(p), p, weeks)


def test_weeks_are_the_last_eight_oldest_first_with_gaps():
    weeks, _ = run(profile(), history([100, 100, 100, 100, 0, 110, 120, 120]))
    assert len(weeks) == 8 and weeks[-1]["week"] == "2026-11-02" and weeks[4]["orders"] == 0
    milk = learning.item_weeks(learning._active_usual(profile())[0], profile(), weeks)
    assert [w["quantity"] for w in milk] == [100, 100, 100, 100, 0, 110, 120, 120]


def test_a_creeping_amount_is_flagged_with_a_true_sentence():
    _, drift = run(profile(), history([100, 100, 100, 100, 0, 110, 120, 120]))
    milk = next(d for d in drift if d["kind"] == "quantity")
    assert (milk["word"], milk["from"], milk["to"], milk["pct"]) == ("milk", 100, 120, 20)
    assert milk["text"] == "Milk crept up: 100 → 120 L a week" and milk["since"] == "2026-10-26"


def test_one_unusual_week_is_not_drift():
    _, drift = run(profile(), history([100, 100, 100, 100, 100, 100, 140, 100]))
    assert not [d for d in drift if d["kind"] == "quantity"]


def test_cost_drift_names_what_changed_it():
    _, drift = run(profile(), history([100] * 8) [:4] + history([100] * 8, milk_price=1.5)[4:])
    cost = next(d for d in drift if d["kind"] == "cost")
    assert cost["to"] > cost["from"] and cost["words"] == ["milk"] and "Mostly milk." in cost["detail"]


def test_a_decided_drift_is_not_shown_again():
    p = profile()
    p["drift_seen"] = {"quantity:milk": 120}
    _, drift = run(p, history([100, 100, 100, 100, 0, 110, 120, 120]))
    assert not [d for d in drift if d["kind"] == "quantity"]


def test_this_weeks_real_order_counts():
    o = {"order_id": "o1", "status": "confirmed", "created_at": "2026-11-12T10:00:00+02:00",
         "intent": {"items": [{"item_id": "i1", "product": {"query": "milk"}, "quantity": {"amount": 130, "unit": "L"}}]},
         "lines": [{"item_id": "i1", "product_id": "M", "name": "Milk", "total_price": 130.0}]}
    weeks, _ = run(profile(), history([100] * 8), [o])
    assert weeks[-1]["week"] == "2026-11-09" and weeks[-1]["lines"][0]["quantity"] == 130


@pytest.fixture
def demo(monkeypatch):
    """A copy of the demo kitchen and its imported history under its own buyer id (other tests use the demo one)."""
    import json
    from buyer_agent import config
    bid = "drift-demo"
    seed = json.loads((config.FIXTURES_DIR / "kitchen" / "school-kitchen-espoo.json").read_text(encoding="utf-8"))
    kitchen._write(seed | {"buyer_id": bid})
    real = learning.history
    monkeypatch.setattr(learning, "history", lambda b, today: real("school-kitchen-espoo" if b == bid else b, today))
    return bid


async def test_demo_kitchen_shows_milk_drift_and_decision_hides_it(client, demo):
    u = (await client.get("/kitchen/usual-order", params={"buyer_id": demo})).json()
    keys = {d["key"] for d in u["drift"]}
    assert "quantity:milk" in keys and "cost" in keys
    assert u["history"]["orders"] >= 7 and u["still_learning"][0]["word"] == "cheese"
    milk = next(i for i in u["items"] if i["word"] == "milk")
    assert len(milk["weeks"]) == 8 and milk["last_unit_price"] is not None

    r = await client.post("/kitchen/usual-order/drift", json={
        "buyer_id": demo,
        "decisions": [{"key": "quantity:milk", "keep": False, "value": 120, "back_to": 100}, {"key": "cost", "value": 585}]})
    assert r.status_code == 200, r.text
    assert next(x for x in r.json()["usual"] if x["word"] == "milk")["typical_qty"] == 100
    u2 = (await client.get("/kitchen/usual-order", params={"buyer_id": demo})).json()
    assert "quantity:milk" not in {d["key"] for d in u2["drift"]}


async def test_imported_history_is_not_in_activity(client, demo):
    assert (await client.get("/kitchen/usual-order", params={"buyer_id": demo})).json()["history"]["orders"] >= 7
    assert (await client.get("/orders", params={"buyer_id": demo})).json() == []
    assert (await client.get("/autonomy", params={"buyer_id": demo})).json()["clean_orders"] == 0
