"""Package 2c: one readable timeline per order for the Activity tab.

Built from the order, its intent and its audit trail at Valio Aimo. The `why` sentences come from templates filled
with that data, never from an LLM, so they are always true. They are written for a chef: no hashes, rule ids, grant
ids or policy language. If a field is missing it is left out, never invented.
"""
from __future__ import annotations

from . import valio

OPTIMISE_TEXT = {"price": "Cheapest that delivers in time.", "co2": "Lowest CO₂ that delivers in time.",
                 "reliability": "Most reliable delivery."}


def _eur(v: float) -> str:
    return f"€{v:,.0f}" if float(v).is_integer() else f"€{v:,.2f}"


def _by_item(audit: list[dict], action: str) -> dict[str, dict]:
    """Last audit entry of this action per item_id (a search can be repeated)."""
    out: dict[str, dict] = {}
    for e in audit:
        d = e.get("data") or {}
        if e.get("action") == action and d.get("item_id"):
            out[d["item_id"]] = e
    return out


def _first(audit: list[dict], *actions: str) -> dict | None:
    return next((e for e in audit if e.get("action") in actions), None)


def _funnel(search: dict | None, offer: dict | None) -> list[int]:
    """catalog → after your musts → can deliver → picked. Only the counts that were recorded."""
    sd, od = (search or {}).get("data") or {}, (offer or {}).get("data") or {}
    total, excluded = sd.get("total_matches"), sd.get("excluded_count")
    if total is None:
        return []
    out = [total]
    if excluded is not None:
        out.append(max(total - excluded, 0))
        if od.get("can_fulfil") is not None:
            out.append(od["can_fulfil"])
    return out + [1]


def _search_why(line: dict, item: dict | None, optimise: str | None) -> str:
    parts = []
    usual = (item or {}).get("usual") or {}
    if line.get("usual") or (usual.get("product_id") and usual["product_id"] == line.get("product_id")):
        parts.append("Your usual.")
    musts = [e for e in line.get("evidence") or [] if e.get("verdict") == "safe"]
    if musts:
        parts.append(f"Passes your {len(musts)} must{'s' * (len(musts) != 1)}." if len(musts) > 1 else "Passes your must.")
    for p in line.get("preferences") or []:
        parts.append(f"{p.get('label')} {'✓' if p.get('met') else '✗'}.")
    if line.get("rank") == "best" and optimise in OPTIMISE_TEXT:
        parts.append(OPTIMISE_TEXT[optimise])
    return " ".join(parts)


def _evidence(check: dict) -> str | None:
    ev = check.get("evidence") or {}
    sig = [s for s in ev.get("signals") or [] if s.get("says") != "safe"]
    if sig:
        return "; ".join(f"{(s.get('source') or '').upper()} {s.get('field')}: {s.get('value')}".strip() for s in sig)
    return check.get("detail")


def _check_text(c: dict) -> str:
    """One failed check as a short sentence for a chef. Falls back to the check's own detail."""
    f, want, got = c.get("field"), c.get("intended"), str(c.get("cart") or "")
    if f == "preference":
        return f"Misses your preference “{want}”"
    if f == "quantity":
        return f"{got.split(' (')[0]} instead of {want} (whole packs)"
    if f in ("delivery_date", "available_date"):
        return f"Arrives {got} instead of {want}"
    if f in ("product", "usual"):
        return f"{got} instead of {want}"
    if f == "hard_rules":
        return f"Breaks your must “{want}”"
    if f == "budget":
        return "Above your budget"
    return c.get("detail") or str(f)


def _limit(history: list[dict], version: int | None, grant_id: str | None) -> float | None:
    m = next((x for x in history if x.get("version") == version), None)
    g = next((g for g in ((m or {}).get("autonomy") or {}).get("grants") or [] if g.get("id") == grant_id), None)
    return ((g or {}).get("limits") or {}).get("max_order_total")


def _decided(order: dict, names: dict[str, str], history: list[dict]) -> list[dict]:
    decision = order.get("decision") or {}
    checks = (order.get("diff") or {}).get("checks") or []
    accepted = (order.get("diff") or {}).get("accepted") or []
    version = (order.get("mandate") or {}).get("version") or decision.get("mandate_version")
    rules = {r.get("id"): r.get("label") for r in (order.get("intent") or {}).get("hard_rules") or []}
    out = []
    for l in decision.get("lines") or []:
        iid = l.get("item_id")
        tier = l.get("tier") or order.get("tier")
        failed = [c for c in checks if c.get("item_id") == iid and not c.get("ok")]
        if tier == "block":
            c = next((c for c in failed if c.get("severity") == "block"), failed[0] if failed else {})
            rid = ((c.get("evidence") or {}).get("id")) or next(iter(l.get("blocked_by") or []), None)
            out.append({"item_id": iid, "outcome": "blocked", "rule": rid,
                        "why": rules.get(rid) or c.get("intended"), "evidence": _evidence(c) if c else None})
        elif tier == "auto":
            grant = next(iter(l.get("grants") or decision.get("grants_used") or []), None)
            lim = _limit(history, version, grant)
            why = "No changes" if not accepted else "Small change accepted: " + ", ".join(accepted)
            out.append({"item_id": iid, "outcome": "alone", "grant": grant,
                        "why": why + (f", within your {_eur(lim)} limit." if lim else ".")})
        else:
            why = "; ".join(_check_text(c) for c in failed) or "Above your limit"
            line = {"item_id": iid, "outcome": "you", "why": why}
            if order.get("approved_by"):
                line["by"] = order["approved_by"]
            out.append(line)
    return [{k: v for k, v in x.items() if v is not None} for x in out]


def _label(order: dict, decided: list[dict]) -> str:
    st, tier = order.get("status"), order.get("tier")
    if tier == "block":
        n = sum(d["outcome"] == "blocked" for d in decided) or 1
        return f"{n} line{'s' * (n != 1)} blocked"
    if st == "pending_approval":
        return "Waiting for you"
    if st == "rejected":
        return "You rejected it"
    if st == "quote_expired":
        return "Quote expired"
    if tier == "auto":
        return "Ordered alone"
    return f"You decided {sum(d['outcome'] == 'you' for d in decided) or 1}"


def build(order: dict, audit: list[dict], history: list[dict], attachments: list[str] | None = None) -> dict:
    it = order.get("intent") or {}
    items = {i["item_id"]: i for i in it.get("items") or []}
    item_ids = order.get("item_ids") or [l.get("item_id") for l in order.get("lines") or []]
    lines = {l.get("item_id"): l for l in order.get("lines") or []}
    searches, offers = _by_item(audit, "search_products"), _by_item(audit, "check_offer")
    optimise = (it.get("preferences") or {}).get("optimise")
    steps: list[dict] = []

    proposed = _first(audit, "propose_intent")
    if it.get("original_request") or attachments:
        steps.append({"kind": "asked", "at": it.get("created_at") or (proposed or {}).get("timestamp"),
                      "text": it.get("original_request") or None, "attachments": attachments or []})
    if it.get("confirmed_at"):
        steps.append({"kind": "understood", "at": it["confirmed_at"], "intent_id": it.get("intent_id"),
                      "intent_hash": it.get("intent_hash"), "signed_by": it.get("confirmed_by"),
                      "items": len(it.get("items") or [])})

    names: dict[str, str] = {}
    searched = []
    for iid in item_ids:
        item, line = items.get(iid), lines.get(iid) or {}
        word = ((item or {}).get("product") or {}).get("query") or iid
        names[iid] = line.get("name") or word
        searched.append({"item_id": iid, "word": word, "funnel": _funnel(searches.get(iid), offers.get(iid)),
                         "picked": line.get("name") or "", "why": _search_why(line, item, optimise) if line else ""})
    if searched:
        at = (searches.get(item_ids[0]) or {}).get("timestamp") if item_ids else None
        steps.append({"kind": "searched", "at": at or order.get("created_at"), "items": searched})

    decided = _decided(order, names, history)
    if decided:
        check = _first(audit, "policy_check")
        steps.append({"kind": "decided", "at": (check or {}).get("timestamp") or order.get("created_at"), "lines": decided})

    if order.get("status") == "confirmed":
        rc = order.get("receipt") or {}
        steps.append({"kind": "confirmed", "at": rc.get("confirmed_at") or order.get("created_at"),
                      "verify_url": f"/orders/{order['order_id']}/verify"})

    m = order.get("mandate") or {}
    return {"order_id": order["order_id"], "label": _label(order, decided), "tier": order.get("tier"),
            "total": order.get("total_price"),
            "mandate": {"version": m.get("version"), "grants_used": m.get("grants_used") or []},
            "steps": steps}


async def load(order_id: str) -> dict:
    """Fetch order, audit trail and mandate history from Valio Aimo, then build the story."""
    from . import agent  # late: agent imports a lot

    order = valio.passthrough(await valio.call("GET", f"/internal/orders/{order_id}"))
    audit = valio.passthrough(await valio.call("GET", f"/internal/orders/{order_id}/audit"))
    history = valio.passthrough(await valio.call("GET", f"/internal/mandate/{order.get('buyer_id')}/history"))
    attachments: list[str] = []
    session = agent.SESSIONS.get(order.get("session") or "")
    if session:  # photos only live as long as the chat session
        for e in session.events:
            if e.get("type") == "user_message":
                attachments += [a["upload_id"] for a in e.get("attachments") or [] if a.get("upload_id")]
    return build(order, audit, history, attachments)
