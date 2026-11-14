"""Package 2b: one autonomy level instead of twenty switches, earned with clean orders.

Level, read from the current mandate (no new mandate field):
  0 Suggest          no grants
  1 Usual products   every grant has a scope `product_id in [...]` (the kitchen's usual products)
  2 Within your rules  at least one grant without a product_id scope
  3 Standing orders  not built, shown locked

An order is CLEAN if it is confirmed, its intent was not revised after it was proposed, and no line needed a human
decision other than approving the cart unchanged: a blocked line (in any order for the same intent), a changed
product (the product or usual check failed) or a revised intent makes it not clean. Clean orders are counted since
the last level change; the counter starts at 0 when the level changes.
"""
from __future__ import annotations

import copy
import logging
from datetime import datetime

from . import config, kitchen, valio

log = logging.getLogger("buyer_agent.autonomy")

LEVELS = {0: "Suggest", 1: "Usual products", 2: "Within your rules", 3: "Standing orders"}
DEFAULT_MAX_ORDER_TOTAL = 600.0
LEVEL1_ACCEPTS = [{"check": "quantity", "max_over_pct": 5}]


def product_scope(g: dict) -> list[str] | None:
    for c in g.get("scope") or []:
        if c.get("attr") == "product_id" and c.get("op") == "in":
            return list(c.get("value") or [])
    return None


def level_of(m: dict) -> int:
    grants = [g for g in (m.get("autonomy") or {}).get("grants") or [] if g.get("enabled", True)]
    if not grants:
        return 0
    return 1 if all(product_scope(g) is not None for g in grants) else 2


def grant_summary(g: dict) -> str:
    parts = []
    lim = g.get("limits") or {}
    if "max_order_total" in lim:
        parts.append(f"up to €{lim['max_order_total']:,.0f} an order")
    if "max_line_total" in lim:
        parts.append(f"up to €{lim['max_line_total']:,.0f} a line")
    if "max_unit_price" in lim:
        parts.append(f"unit price up to €{lim['max_unit_price']:,.2f}")
    for a in g.get("accepts") or []:
        c = a.get("check")
        if c == "quantity":
            parts.append(f"accepts +{a.get('max_over_pct', 0):g} % from pack rounding")
        elif c == "delivery_date":
            parts.append(f"accepts up to {a.get('max_days_late', 0)} day(s) late")
        elif c == "preference":
            parts.append(f"accepts missing preference {a['id']}" if a.get("id") else "accepts missing preferences")
        elif c == "usual":
            parts.append("accepts another product than your usual")
        else:
            parts.append(f"accepts {c.replace('_', ' ')}")
    ids = product_scope(g)
    if ids is not None:
        parts.insert(0, f"{len(ids)} usual product{'s' * (len(ids) != 1)}")
    return " · ".join(parts) or "no limits"


def _ts(s: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(s) if s else None
    except ValueError:
        return None


def last_level_change(history: list[dict]) -> datetime | None:
    """When the level last changed (newest version whose level differs from the one before)."""
    versions = sorted(history, key=lambda m: m.get("version", 0))
    when = None
    for prev, cur in zip(versions, versions[1:]):
        if level_of(prev) != level_of(cur):
            when = _ts(cur.get("updated_at"))
    return when


def is_clean(o: dict, all_orders: list[dict]) -> tuple[bool, str]:
    if o.get("status") != "confirmed":
        return False, {"pending_approval": "waiting for your decision", "rejected": "blocked or rejected",
                       "quote_expired": "the quote expired"}.get(o.get("status"), "not confirmed")
    if o.get("intent_revised"):
        return False, "the order was changed after it was proposed"
    if o.get("blocked_lines") or any(x.get("intent_id") == o.get("intent_id") and x.get("tier") == "block"
                                     for x in all_orders if x.get("intent_id")):
        return False, "a line was blocked"
    if o.get("product_changed"):
        return False, "a different product was chosen"
    return True, "Clean order"


def label(o: dict) -> str:
    """Start screen: 'Ordered alone', 'You decided 1', '1 line blocked (nuts)'."""
    st, tier = o.get("status"), o.get("tier")
    if tier == "block" or st == "rejected" and o.get("blocked_lines"):
        n = o.get("blocked_lines") or o.get("lines") or 1
        why = ", ".join(o.get("blocked_labels") or o.get("blocked_rules") or [])
        return f"{n} line{'s' * (n != 1)} blocked" + (f" ({why})" if why else "")
    if st == "pending_approval":
        return "Waiting for you"
    if st == "rejected":
        return "You rejected it"
    if st == "quote_expired":
        return "Quote expired"
    if tier == "auto":
        return "Ordered alone"
    return f"You decided {o.get('asked_lines') or 1}"


async def orders(buyer_id: str, limit: int = 20, before: str | None = None) -> list[dict]:
    params = {"buyer_id": buyer_id, "limit": limit} | ({"before": before} if before else {})
    rows = valio.passthrough(await valio.call("GET", "/internal/orders", params=params))
    return [r | {"label": label(r)} for r in rows]


async def state(buyer_id: str) -> dict:
    m = await valio.mandate(buyer_id)
    history = valio.passthrough(await valio.call("GET", f"/internal/mandate/{buyer_id}/history"))
    rows = await orders(buyer_id, limit=200)
    since = last_level_change(history)
    counted = [o for o in rows if since is None or (_ts(o.get("created_at")) or since) >= since]
    clean = sum(1 for o in counted if is_clean(o, rows)[0])
    lvl = level_of(m)
    grants = [g for g in (m.get("autonomy") or {}).get("grants") or [] if g.get("enabled", True)]
    needed = config.AUTONOMY_CLEAN_ORDERS
    return {"buyer_id": buyer_id, "level": lvl, "name": LEVELS[lvl], "clean_orders": clean, "needed": needed,
            "offer_next": clean >= needed and lvl < 2, "since": since.isoformat() if since else None,
            "mandate_version": m.get("version"),
            "grants": [{"id": g["id"], "label": g.get("label"), "summary": grant_summary(g),
                        "scope": "usual products" if product_scope(g) is not None else "anything"} for g in grants],
            "levels": [{"level": k, "name": v, "locked": k == 3} for k, v in LEVELS.items()],
            "usual_scope": usual_scope(buyer_id, m)}


def usual_scope(buyer_id: str, m: dict) -> dict:
    """Does the level-1 grant still cover exactly the kitchen's usual products?"""
    grant = next((g for g in (m.get("autonomy") or {}).get("grants") or []
                  if g.get("enabled", True) and product_scope(g) is not None), None)
    p = kitchen.load(buyer_id)
    if grant is None or (not p.get("version") and not p.get("usual")):
        return {"in_sync": True, "missing": [], "extra": [], "grant_id": grant["id"] if grant else None}
    have, want = product_scope(grant), kitchen.usual_ids(p)
    missing = [x for x in want if x not in have]
    extra = [x for x in have if x not in want]
    return {"in_sync": not missing and not extra, "missing": missing, "extra": extra, "grant_id": grant["id"]}


def with_usual_scope(m: dict, added: list[str], removed: list[str]) -> dict | None:
    """Level 1 only: the mandate with the usual-products grant widened by `added` and narrowed by `removed`.
    None when nothing changes (another level, or the grant already says so)."""
    if level_of(m) != 1:
        return None
    new = copy.deepcopy(valio.editable(m))
    grants = (new.get("autonomy") or {}).get("grants") or []
    g = next((x for x in grants if x.get("id") == "g1" and x.get("enabled", True)), None) or next(
        (x for x in grants if x.get("enabled", True) and product_scope(x) is not None), None)
    if g is None:
        return None
    have = product_scope(g) or []
    want = [x for x in have if x not in removed] + [x for x in added if x not in have]
    if want == have:
        return None
    for c in g["scope"]:
        if c.get("attr") == "product_id" and c.get("op") == "in":
            c["value"] = want
    return new


def mandate_for_level(m: dict, level: int, usual: list[str], max_order_total: float | None) -> tuple[dict, list[str]]:
    """The new mandate for a level. Level 1 keeps other product-scoped grants and drops grants scoped any other
    way (e.g. by category): those would make it level 2. Level 2 = g1 without the product scope."""
    new = valio.editable(m)
    grants = list((m.get("autonomy") or {}).get("grants") or [])
    g1_old = next((g for g in grants if g["id"] == "g1"), grants[0] if grants else None)
    limit = float(max_order_total or ((g1_old or {}).get("limits") or {}).get("max_order_total") or DEFAULT_MAX_ORDER_TOTAL)
    diff: list[str] = []
    if level == 0:
        new["autonomy"] = {"grants": []}
        diff.append("Nothing alone any more: I suggest, you approve every order")
    elif level == 1:
        if not usual:
            raise ValueError("the kitchen profile has no usual products with a product yet")
        keep = [g for g in grants if g["id"] != "g1" and product_scope(g) is not None]
        dropped = [g for g in grants if g["id"] != "g1" and product_scope(g) is None]
        g1 = {"id": "g1", "label": "Usual products", "scope": [{"attr": "product_id", "op": "in", "value": usual}],
              "limits": {"max_order_total": limit}, "accepts": LEVEL1_ACCEPTS, "source": "autonomy level 1"}
        new["autonomy"] = {"grants": [g1] + keep}
        diff.append(f"Usual products: I order your {len(usual)} usual products alone, up to €{limit:,.0f} "
                    f"an order, and accept +5 % from pack rounding")
        diff += [f"Remove the permission “{g.get('label')}”: it is not limited to your usual products" for g in dropped]
    elif level == 2:
        others = [g for g in grants if g["id"] != "g1"]
        acc = (g1_old or {}).get("accepts") or LEVEL1_ACCEPTS
        g1 = {"id": "g1", "label": "Within your rules", "scope": [], "limits": {"max_order_total": limit}, "accepts": acc,
              "source": "autonomy level 2"}
        new["autonomy"] = {"grants": [g1] + others}
        diff.append(f"Within your rules: I order anything your musts allow alone, up to €{limit:,.0f} an order")
    else:
        raise ValueError("level 3 (standing orders) is not built yet")
    return new, diff


async def set_level(buyer_id: str, level: int, max_order_total: float | None, by: str, mandate_hash: str | None) -> dict:
    m = await valio.mandate(buyer_id)
    usual = kitchen.usual_ids(kitchen.load(buyer_id))
    new, diff = mandate_for_level(m, level, usual, max_order_total)
    saved = await valio.change(buyer_id, new, diff, mandate_hash, by)
    return await state(buyer_id) | {"mandate_version": saved["version"], "fingerprint": saved.get("fingerprint")}


async def after_order(session, order: dict) -> None:
    """After order_confirmed: emit autonomy_progress so the UI can show 'Clean order. 7 of 10 towards level 2'."""
    try:
        st = await state(session.buyer_id)
        rows = await orders(session.buyer_id, limit=200)
        row = next((o for o in rows if o["order_id"] == order.get("order_id")), None)
        clean, reason = is_clean(row, rows) if row else (False, "order not found")
        session.emit("autonomy_progress", order_id=order.get("order_id"), clean=clean, reason=reason,
                     clean_orders=st["clean_orders"], needed=st["needed"], offer_next=st["offer_next"],
                     level=st["level"], name=st["name"])
    except Exception:  # noqa: BLE001 - progress is a nice-to-have, never break the order flow
        log.exception("autonomy_progress failed")
