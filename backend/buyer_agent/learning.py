"""Package 2a: learning from orders. Package 2c: the "Your usual order" card.

After an order is confirmed, compare what the buyer asked for with what was ordered and propose at most ONE
thing to remember (decision W7: only suggestions, the buyer accepts in the UI). Two cases, in this order:

1. The usual product was replaced (the buyer chose another product for that word) →
   "Use <new product> whenever you say <word>?"
2. A word without a usual product was ordered with the same product for the second time →
   "Remember <product> as your usual <word>?"

Everything here reads Valio Aimo's order data (`/internal/orders`), nothing is invented.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from . import kitchen, valio

log = logging.getLogger("buyer_agent.learning")

HISTORY_ORDERS = 30  # how many recent orders to look at for "second time" and last prices


def _word(item: dict) -> str:
    return " ".join(((item.get("product") or {}).get("query") or "").lower().split())


def _active_usual(profile: dict) -> list[dict]:
    return [u for u in profile.get("usual") or [] if u.get("status", "active") == "active"]


def _usual_for(profile: dict, word: str, product_id: str | None = None) -> dict | None:
    for u in _active_usual(profile):
        if product_id and u.get("product_id") == product_id:
            return u
        if word and (u["word"] == word or word in (u.get("aliases") or [])):
            return u
    return None


def ordered_items(order: dict) -> list[dict]:
    """Per item of a confirmed order: word, quantity and unit asked for, the preferred product, what was ordered."""
    items = {i["item_id"]: i for i in (order.get("intent") or {}).get("items") or []}
    out = []
    for line in order.get("lines") or []:
        item = items.get(line.get("item_id")) or {}
        q = item.get("quantity") if isinstance(item.get("quantity"), dict) else {}
        out.append({"item_id": line.get("item_id"), "word": _word(item),
                    "preferred": (item.get("product") or {}).get("preferred_product_id"),
                    "product_id": line.get("product_id"), "product_name": line.get("name"),
                    "quantity": q.get("amount") or line.get("requested_quantity"),
                    "unit": q.get("unit") or line.get("requested_unit")})
    return out


def _decided_before(profile: dict, word: str, product_id: str) -> bool:
    """A suggestion for this word and product was already made (still open, accepted or 'just this time')."""
    for s in profile.get("suggestions") or []:
        p = s.get("payload") or {}
        if s.get("kind") == "usual" and p.get("word") == word and p.get("product_id") == product_id:
            return True
    return False


def suggestion_for(order: dict, profile: dict, earlier: list[dict]) -> tuple[dict, dict] | None:
    """(suggestion, source) or None. `earlier`: other confirmed orders of this buyer (full order data)."""
    if order.get("status") != "confirmed":
        return None
    items = ordered_items(order)
    # 1. the usual product was replaced
    for it in items:
        if not it["preferred"] or not it["product_id"] or it["product_id"] == it["preferred"]:
            continue
        old = _usual_for(profile, it["word"], it["preferred"]) or {}
        word = old.get("word") or it["word"]
        if not word or _decided_before(profile, word, it["product_id"]):
            continue
        payload = {"word": word, "aliases": old.get("aliases") or [], "product_id": it["product_id"],
                   "product_name": it["product_name"], "typical_qty": old.get("typical_qty") or it["quantity"],
                   "unit": old.get("unit") or it["unit"], "per": old.get("per") or "week",
                   "replaces": old.get("product_id")}
        return ({"kind": "usual", "title": f"Use {it['product_name']} whenever you say {word}?",
                 "quote": f"You chose {it['product_name']} instead of {old.get('product_name') or 'your usual'}.",
                 "payload": payload},
                {"type": "correction", "title": f"Your decision on order {order['order_id']}", "order_id": order["order_id"]})
    # 2. the same product for a word without a usual product, for the second time
    for it in items:
        if it["preferred"] or not it["word"] or not it["product_id"] or _usual_for(profile, it["word"]):
            continue
        if _decided_before(profile, it["word"], it["product_id"]):
            continue
        again = any(x["word"] == it["word"] and x["product_id"] == it["product_id"]
                    for o in earlier if o.get("order_id") != order.get("order_id") for x in ordered_items(o))
        if not again:
            continue
        payload = {"word": it["word"], "aliases": [], "product_id": it["product_id"], "product_name": it["product_name"],
                   "typical_qty": it["quantity"], "unit": it["unit"], "per": "week"}
        return ({"kind": "usual", "title": f"Remember {it['product_name']} as your usual {it['word']}?",
                 "quote": f"You ordered {it['product_name']} for “{it['word']}” twice.", "payload": payload},
                {"type": "order", "title": f"Order {order['order_id']}", "order_id": order["order_id"]})
    return None


async def recent_orders(buyer_id: str, status: str = "confirmed", limit: int = HISTORY_ORDERS) -> list[dict]:
    """Full order data of the newest orders with this status (newest first)."""
    rows = valio.passthrough(await valio.call("GET", "/internal/orders", params={"buyer_id": buyer_id, "limit": limit}))
    out = []
    for r in rows:
        if r.get("status") != status:
            continue
        try:
            out.append(valio.passthrough(await valio.call("GET", f"/internal/orders/{r['order_id']}")))
        except Exception:  # noqa: BLE001 - one unreadable order must not stop learning
            log.warning("could not load order %s", r.get("order_id"))
    return out


async def after_order(session, order_id: str) -> dict | None:
    """Called in the background after order_confirmed. Emits context_suggestion with at most one suggestion."""
    try:
        orders = await recent_orders(session.buyer_id)
        order = next((o for o in orders if o["order_id"] == order_id), None)
        if order is None:
            order = valio.passthrough(await valio.call("GET", f"/internal/orders/{order_id}"))
        found = suggestion_for(order, kitchen.load(session.buyer_id), orders)
        if not found:
            return None
        sug, src = found
        saved = kitchen.add_suggestions(session.buyer_id, [sug], src)
        if not saved:
            return None
        session.emit("context_suggestion", reason="after_order", order_id=order_id, suggestions=saved)
        return saved[0]
    except Exception:  # noqa: BLE001 - learning is a nice-to-have, never break the order flow
        log.exception("learning after order %s failed", order_id)
        return None


# --------------------------------------------------------------------------- 2c: "Your usual order"

def _per_unit(line: dict, unit: str | None) -> float | None:
    """Price per unit of the kitchen's usual amount (per L, kg or piece), from one order line."""
    total, rq = line.get("total_price"), line.get("requested_quantity")
    same = (line.get("requested_unit") or "").upper() == (unit or "").upper() or unit is None
    if total is not None and rq and same:
        return round(float(total) / float(rq), 4)
    if unit in ("SALES_UNIT", "pcs", "units", None) and line.get("unit_price") is not None:
        return float(line["unit_price"])
    return None


def next_weekday(today: date, weekday: int = 0) -> date:
    d = today + timedelta(days=1)
    while d.weekday() != weekday:
        d += timedelta(days=1)
    return d


async def usual_order(buyer_id: str, today: date | None = None) -> dict:
    profile = kitchen.load(buyer_id)
    usual = [u for u in _active_usual(profile) if u.get("product_id") and u.get("typical_qty")]
    prices: dict[str, float] = {}
    if usual:
        try:
            for o in await recent_orders(buyer_id):  # newest first: the first price found is the last paid
                for line in o.get("lines") or []:
                    pid = line.get("product_id")
                    if pid in prices:
                        continue
                    u = next((x for x in usual if x["product_id"] == pid), None)
                    if u and (p := _per_unit(line, u.get("unit"))) is not None:
                        prices[pid] = p
                if all(u["product_id"] in prices for u in usual):
                    break
        except Exception:  # noqa: BLE001 - the card works without prices
            log.exception("could not read last prices for %s", buyer_id)
    items = []
    for u in usual:
        price = prices.get(u["product_id"])
        items.append({"word": u["word"], "product_id": u["product_id"], "product_name": u.get("product_name"),
                      "quantity": u["typical_qty"], "unit": u.get("unit"),
                      "last_unit_price": round(price, 2) if price is not None else None,
                      "estimate": round(price * float(u["typical_qty"]), 2) if price is not None else None})
    known = [i["estimate"] for i in items if i["estimate"] is not None]
    pers = {u.get("per") or "week" for u in usual}
    today = today or date.today()
    return {"items": items, "estimate_total": round(sum(known), 2) if known else None,
            "estimate_complete": bool(items) and len(known) == len(items),
            "deliver_by": next_weekday(today).isoformat(),
            "based_on": f"your usual amounts per {pers.pop()}" if len(pers) == 1 else "your usual amounts",
            "kitchen_profile_version": profile.get("version")}


# --------------------------------------------------------------------------- 2c: the "learned" step of a story

def learned_steps(profile: dict, order_id: str) -> list[dict]:
    """Accepted suggestions whose source is this order, as plain sentences."""
    src_ids = {s["id"] for s in profile.get("sources") or [] if s.get("order_id") == order_id}
    out = []
    for s in profile.get("suggestions") or []:
        if s.get("source_id") not in src_ids or s.get("status") != "accepted":
            continue
        p = s.get("payload") or {}
        text = (f"“{p['word']}” now means {p.get('product_name') or p.get('product_id')}"
                if s["kind"] == "usual" and p.get("word") else s.get("title"))
        out.append({"kind": "learned", "at": s.get("decided_at"), "text": text, "suggestion_id": s["id"]})
    return out
