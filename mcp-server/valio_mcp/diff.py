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


def compute_line(intent: dict, item: dict, quote: dict, mandate: dict | None = None) -> tuple[list[dict], list[dict]]:
    """Checks for one cart line against the intent item it claims to fulfil, under the current mandate."""
    from .intent import effective_preferences, effective_rules
    line = quote["data"]
    checks: list[dict] = []
    product = catalog().get(line["product_id"])
    iid = item["item_id"]

    def add(c: dict) -> None:
        checks.append(c | {"item_id": iid})

    # binding: the quote belongs to this buyer and was asked for this item's exact quantity
    q = item["quantity"]
    same_ask = (quote.get("buyer_id") == intent["buyer_id"]
                and D(line.get("requested_quantity", -1)) == D(q["amount"])
                and line.get("requested_unit") == q["unit"]
                and line.get("item_id") in (None, iid))
    add(_check(
        "binding", f"{intent['buyer_id']}, {_q(D(q['amount']), q['unit'])}",
        f"{quote.get('buyer_id')}, {_q(D(line.get('requested_quantity', 0)), line.get('requested_unit', ''))}",
        same_ask, "block",
        "Quote was requested for this buyer and this item's exact quantity" if same_ask
        else "Quote was requested for a different buyer, item or quantity"))

    # product class: the catalog search for the item's product accepts this product
    in_class = bool(product) and product.id in catalog().matching_ids(item["product"]["query"])
    add(_check("product", item["product"]["query"], line["name"], in_class, "ask",
               f"Catalog search for '{item['product']['query']}' includes this product" if in_class
               else f"'{line['name']}' is not what a search for '{item['product']['query']}' returns"))

    # musts from data, never from the agent. Broken -> block. Unknown -> block, or ask if the rule says so.
    rules = effective_rules(intent, item, mandate)
    evidence = constraints.evaluate_all(product, rules, offer=line) if product else []
    for ev in evidence:
        ok = ev["verdict"] == "safe"
        sev = "block" if constraints.violates(ev) else "ask"
        detail = (", ".join(f"{s['source']} {s['field']}={s['value']}" for s in ev["signals"]) or "no data")
        add(_check("hard_rules", ev["rule"], ev["verdict"], ok, sev, detail) | {"evidence": ev})

    # preferences: a miss is a deviation that asks, unless a grant of the mandate accepts it
    for pref in (effective_preferences(intent, mandate) if mandate and product else []):
        ev = constraints.evaluate(product, pref, offer=line)
        if ev is None:
            continue
        ok = ev["verdict"] == "safe"
        detail = (", ".join(f"{s['source']} {s['field']}={s['value']}" for s in ev["signals"]) or "no data")
        add(_check("preference", pref["label"], ev["verdict"], ok, "ask",
                   ("Meets your preference: " if ok else "Misses your preference: ") + detail)
            | {"pref_id": pref["id"], "weight": pref.get("weight", 1)})
    if not product and rules:
        add(_check("hard_rules", "all rules", "unknown product", False, "block",
                   "Product not in catalog, rules cannot be proven"))

    # quantity: nothing less, nothing more than the tolerance
    amount = D(q["amount"])
    got = delivered_amount(line, q["unit"])
    if got is None:
        add(_check("quantity", _q(amount, q["unit"]), f"{line['quantity']:g} {line['sales_unit']}", False, "ask",
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
        add(_check("quantity", _q(amount, q["unit"]), _q(got, q["unit"]) + pack, ok, "ask", detail)
            | {"over_pct": float(over.quantize(Decimal("0.01")))})

    # delivery date and availability
    by = intent["deliver_by"]
    date_ok = line.get("requested_date", "9999") <= by and (line.get("available_date") or "9999") <= by
    add(_check("delivery_date", f"by {by}", str(line.get("available_date") or "no date"), date_ok, "ask",
               "Arrives in time" if date_ok else f"Earliest delivery {line.get('available_date')}"))
    avail_ok = not line.get("sales_block") and D(line.get("available_amount") or 0) >= D(line["quantity"])
    add(_check("availability", f"{line['quantity']:g} {line['sales_unit']}",
               f"{line.get('available_amount', 0):g} available", avail_ok, "ask",
               "In stock" if avail_ok else "; ".join(line.get("fulfil_issues") or ["not available"])))

    # technical: quote still valid, before cut-off, signature intact
    valid = now() < datetime.fromisoformat(quote["expires_at"])
    add(_check("quote_valid", "live quote", f"valid until {quote['expires_at']}", valid, "block",
               "Price locked" if valid else "Quote expired, ask for a new offer"))
    closing = line.get("closing_time")
    cut_ok = not closing or now() < datetime.fromisoformat(closing)
    add(_check("order_cutoff", "before cut-off", closing or "none", cut_ok, "block",
               "Before cut-off" if cut_ok else "Order cut-off has passed"))
    prov = line.get("provenance") or {}
    sig_ok = bool(prov.get("signature")) and trust.verify(trust.valio_key().public_key(), prov.get("hash", ""),
                                                           prov.get("signature")) \
        and prov.get("hash") == trust.digest(quote_core(line))
    add(_check("quote_signature", "quote signed by Valio Aimo", "signature valid" if sig_ok else "invalid",
               sig_ok, "block", "Quote untouched since Valio priced it" if sig_ok
               else "Quote signature does not match its content"))
    return checks, evidence


def compute(intent: dict, pairs: list[tuple[dict | None, dict | None, str, str]], mandate: dict) -> dict:
    """pairs: (item, quote, item_id, quote_id) per requested cart line. Missing item or quote -> block."""
    checks: list[dict] = []
    evidence: dict[str, list] = {}
    seen: set[str] = set()
    total = Decimal(0)
    for item, quote, iid, qid in pairs:
        if item is None:
            checks.append(_check("binding", f"item {iid}", "not in intent", False, "block",
                                 f"The confirmed intent has no item '{iid}'") | {"item_id": iid})
            continue
        if iid in seen:
            checks.append(_check("binding", f"{item['product']['query']} once", "twice in this cart", False, "block",
                                 "Each item of the intent can be bought only once") | {"item_id": iid})
            continue
        seen.add(iid)
        if item["status"] != "open":
            checks.append(_check("binding", f"{item['product']['query']} once",
                                 f"already in order {item.get('order_id')}", False, "block",
                                 "Each item of the intent can be bought only once") | {"item_id": iid})
            continue
        if quote is None:
            checks.append(_check("binding", "a live quote", f"unknown quote {qid}", False, "block",
                                 "Quote not found") | {"item_id": iid})
            continue
        c, ev = compute_line(intent, item, quote, mandate)
        checks += c
        evidence[iid] = ev
        total += D(quote["data"]["total_price"])

    cur = mandate.get("currency", "EUR")
    if intent.get("budget"):
        b = D(intent["budget"]["max_total"])
        spent = D(intent.get("spent") or 0)
        after = spent + total
        checks.append(_check("budget", f"max {b:,.2f} {cur} for the whole intent",
                             f"{after:,.2f} {cur}" + (f" (incl. {spent:,.2f} already ordered)" if spent else ""),
                             after <= b, "ask", "Within your budget" if after <= b else "Over your budget"))
    deviations = sum(1 for c in checks if not c["ok"])
    return {"deviations": deviations, "checks": checks, "evidence": evidence, "total": float(total)}


QUOTE_CORE = ("quote_id", "product_id", "quantity", "sales_unit", "unit_price", "total_price", "currency",
              "requested_date", "available_date", "available_amount", "quote_expires_at", "requested_quantity",
              "requested_unit", "content_per_sales_unit", "item_id", "intent_id")


def quote_core(line: dict) -> dict:
    return {k: line.get(k) for k in QUOTE_CORE}


def cart_line(item_id: str, line: dict) -> dict:
    return {"item_id": item_id, "quote_id": line["quote_id"], "product_id": line["product_id"],
            "name": line["name"], "quantity": line["quantity"], "sales_unit": line["sales_unit"],
            "unit_price": line["unit_price"], "total_price": line["total_price"], "currency": line["currency"],
            "delivery_date": max(line["requested_date"], line.get("available_date") or line["requested_date"]),
            "quote_signature": (line.get("provenance") or {}).get("signature")}


def cart(intent_hash: str, lines: list[tuple[str, dict]]) -> dict:
    """The exact thing that gets ordered, all lines. Its hash is what a human signs on approval."""
    cl = [cart_line(iid, line) for iid, line in lines]
    total = sum(D(c["total_price"]) for c in cl)
    return {"intent_hash": intent_hash, "lines": cl, "total_price": float(total),
            "currency": cl[0]["currency"] if cl else "EUR",
            "delivery_date": max((c["delivery_date"] for c in cl), default=None)}
