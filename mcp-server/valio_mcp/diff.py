"""Diff check: the quote the agent wants to order vs. the intent the buyer confirmed.

Field by field, exact decimal maths. Each check says what was intended, what the cart would be,
and how bad a mismatch is (ask = a human may accept it, block = never). Zero deviations is the
only way to an autonomous order.
"""
from __future__ import annotations

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from . import constraints, trust
from .catalog import catalog
from .store import now


def D(x) -> Decimal:
    return Decimal(str(x))


def _q(x: Decimal, unit: str) -> str:
    u = {"L": "L", "KG": "kg", "SALES_UNIT": "units"}.get(unit, unit)
    x = x.quantize(Decimal("0.01"), ROUND_HALF_UP).normalize()
    return f"{x:,f} {u}"


def _check(field: str, intended: str, cart: str, ok: bool, severity: str, detail: str) -> dict:
    return {"field": field, "intended": intended, "cart": cart, "ok": ok,
            "severity": "none" if ok else severity, "detail": detail}


def delivered_amount(line: dict, unit: str) -> Decimal | None:
    """What the cart delivers, in the intent's unit."""
    qty = D(line["quantity"])
    if unit == "SALES_UNIT":
        return qty
    content = line.get("content_per_sales_unit")
    if not content or line.get("comparison_unit") != unit:
        return None
    return qty * D(content)


def compute(intent: dict, quote: dict, mandate: dict) -> dict:
    line = quote["data"]
    checks: list[dict] = []
    product = catalog().get(line["product_id"])

    # 1. binding: the quote belongs to this buyer and was asked for this intent's quantity and date
    q = intent["quantity"]
    same_ask = (quote.get("buyer_id") == intent["buyer_id"]
                and D(line.get("requested_quantity", -1)) == D(q["amount"])
                and line.get("requested_unit") == q["unit"])
    checks.append(_check(
        "binding", f"{intent['buyer_id']}, {_q(D(q['amount']), q['unit'])}",
        f"{quote.get('buyer_id')}, {_q(D(line.get('requested_quantity', 0)), line.get('requested_unit', ''))}",
        same_ask, "block",
        "Quote was requested for this buyer and this exact quantity" if same_ask
        else "Quote was requested for a different buyer or quantity than the confirmed intent"))

    # 2. product class: the catalog search for the intent's product accepts this product
    in_class = bool(product) and product.id in catalog().matching_ids(intent["product"]["query"])
    checks.append(_check("product", intent["product"]["query"], line["name"], in_class, "ask",
                         f"Catalog search for '{intent['product']['query']}' includes this product" if in_class
                         else f"'{line['name']}' is not what a search for '{intent['product']['query']}' returns"))

    # 3. hard rules from data, never from the agent
    evidence = constraints.evaluate_all(product, intent["hard_rules"]) if product else []
    for ev in evidence:
        ok = ev["verdict"] == "safe"
        sev = "ask" if ev["rule"].startswith("origin") else "block"
        detail = (", ".join(f"{s['source']} {s['field']}={s['value']}" for s in ev["signals"]) or "no data")
        checks.append(_check("hard_rules", ev["rule"], ev["verdict"], ok, sev, detail) | {"evidence": ev})
    if not product and intent["hard_rules"]:
        checks.append(_check("hard_rules", "all rules", "unknown product", False, "block",
                             "Product not in catalog, rules cannot be proven"))

    # 4. quantity: nothing less, nothing more than the tolerance
    amount = D(q["amount"])
    got = delivered_amount(line, q["unit"])
    if got is None:
        checks.append(_check("quantity", _q(amount, q["unit"]),
                             f"{line['quantity']:g} {line['sales_unit']}", False, "ask",
                             f"Cannot prove how many {q['unit']} one {line['sales_unit']} holds"))
    else:
        over = ((got - amount) / amount * 100) if amount else Decimal(0)
        under_ok = got >= amount or q.get("allow_under")
        ok = under_ok and over <= D(q.get("max_over_pct", 0))
        pack = (f" ({line['quantity']:g} × {line['content_per_sales_unit']:g} {q['unit']})"
                if q["unit"] != "SALES_UNIT" and line.get("content_per_sales_unit") else "")
        if got < amount and not q.get("allow_under"):
            detail = f"{_q(amount - got, q['unit'])} short"
        elif not ok:
            detail = f"{over.quantize(Decimal('0.1'))} % over, allowed {q.get('max_over_pct', 0):g} %"
        elif got == amount:
            detail = "Exact"
        else:
            detail = f"Rounded up to whole packs: +{over.quantize(Decimal('0.1'))} % (allowed {q.get('max_over_pct', 0):g} %)"
        checks.append(_check("quantity", _q(amount, q["unit"]), _q(got, q["unit"]) + pack, ok, "ask", detail))

    # 5. delivery date and availability
    by = intent["deliver_by"]
    date_ok = line.get("requested_date", "9999") <= by and (line.get("available_date") or "9999") <= by
    checks.append(_check("delivery_date", f"by {by}", str(line.get("available_date") or "no date"), date_ok, "ask",
                         "Arrives in time" if date_ok else f"Earliest delivery {line.get('available_date')}"))
    avail_ok = not line.get("sales_block") and D(line.get("available_amount") or 0) >= D(line["quantity"])
    checks.append(_check("availability", f"{line['quantity']:g} {line['sales_unit']}",
                         f"{line.get('available_amount', 0):g} available", avail_ok, "ask",
                         "In stock" if avail_ok else "; ".join(line.get("fulfil_issues") or ["not available"])))

    # 6. money: the buyer's own budget and the mandate's autonomous limit
    total = D(line["total_price"])
    if intent.get("budget"):
        b = D(intent["budget"]["max_total"])
        checks.append(_check("budget", f"max {b:,.2f} {intent['budget']['currency']}", f"{total:,.2f} {line['currency']}",
                             total <= b, "ask", "Within your budget" if total <= b else "Over your budget"))
    lim = D(mandate["spend_limit"])
    checks.append(_check("spend_limit", f"auto up to {lim:,.2f} {mandate['currency']}", f"{total:,.2f} {line['currency']}",
                         total <= lim, "ask", "Within the agent's autonomous limit" if total <= lim
                         else "Above the agent's autonomous limit, a human must sign"))
    cats = mandate.get("allowed_categories")
    if cats:
        ok = line.get("category") in cats or (product and product.category in cats)
        checks.append(_check("category", ", ".join(cats), product.category if product else "?", bool(ok), "ask",
                             "Category in mandate" if ok else "Category outside the mandate"))

    # 7. technical: quote still valid, before cut-off, signature intact
    valid = now() < datetime.fromisoformat(quote["expires_at"])
    checks.append(_check("quote_valid", "live quote", f"valid until {quote['expires_at']}", valid, "block",
                         "Price locked" if valid else "Quote expired, ask for a new offer"))
    closing = line.get("closing_time")
    cut_ok = not closing or now() < datetime.fromisoformat(closing)
    checks.append(_check("order_cutoff", "before cut-off", closing or "none", cut_ok, "block",
                         "Before cut-off" if cut_ok else "Order cut-off has passed"))
    prov = line.get("provenance") or {}
    sig_ok = bool(prov.get("signature")) and trust.verify(trust.valio_key().public_key(), prov.get("hash", ""),
                                                           prov.get("signature")) \
        and prov.get("hash") == trust.digest(quote_core(line))
    checks.append(_check("quote_signature", "quote signed by Valio Aimo", "signature valid" if sig_ok else "invalid",
                         sig_ok, "block", "Quote untouched since Valio priced it" if sig_ok
                         else "Quote signature does not match its content"))

    deviations = sum(1 for c in checks if not c["ok"])
    return {"deviations": deviations, "checks": checks, "evidence": evidence}


QUOTE_CORE = ("quote_id", "product_id", "quantity", "sales_unit", "unit_price", "total_price", "currency",
              "requested_date", "available_date", "available_amount", "quote_expires_at", "requested_quantity",
              "requested_unit", "content_per_sales_unit")


def quote_core(line: dict) -> dict:
    return {k: line.get(k) for k in QUOTE_CORE}


def cart(intent_hash: str, line: dict) -> dict:
    """The exact thing that gets ordered. Its hash is what a human signs on approval."""
    return {"intent_hash": intent_hash, "quote_id": line["quote_id"], "product_id": line["product_id"],
            "name": line["name"], "quantity": line["quantity"], "sales_unit": line["sales_unit"],
            "unit_price": line["unit_price"], "total_price": line["total_price"], "currency": line["currency"],
            "delivery_date": max(line["requested_date"], line.get("available_date") or line["requested_date"]),
            "quote_signature": (line.get("provenance") or {}).get("signature")}
