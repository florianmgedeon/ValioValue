"""OrderIntent: the buyer's request as a fixed, checkable object.

The agent fills it (propose_intent). Valio Aimo validates it, adds the mandate's mandatory rules,
finds open questions, writes the read-back and hashes the binding fields (RFC 8785 + SHA-256).
Only a human can confirm it, via the buyer backend, with the buyer's signature over the hash.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

from . import config, trust
from .constraints import norm_allergen
from .store import iso, new_id, now


class HardRuleIn(BaseModel):
    rule: Literal["lactose_free", "exclude_allergen", "origin"]
    allergen: str | None = Field(None, description="For exclude_allergen: milk, gluten, egg, peanuts, tree nuts, soy, fish ...")
    strictness: Literal["contains", "contains_or_may_contain"] = Field(
        "contains_or_may_contain", description="Allergy: also exclude 'may contain'. Default for allergies.")
    country: str | None = Field(None, description="For origin: ISO alpha-2, e.g. 'fi'")


UNIT_WORDS = {"l": "L", "litre": "L", "litres": "L", "liter": "L", "liters": "L", "litra": "L", "litraa": "L",
              "kg": "KG", "kilo": "KG", "kilos": "KG", "kilogram": "KG", "kilograms": "KG", "kiloa": "KG"}
NON_DAIRY = re.compile(r"non[- ]?dairy|dairy[- ]?free|no dairy|without dairy|maidoton|maitotuotteeton|mjölkfri")
LACTOSE_FREE = re.compile(r"lactose[- ]?free|laktoositon|laktosfri")
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def _numbers_with_units(text: str) -> list[tuple[Decimal, str]]:
    out = []
    for num, unit in re.findall(r"(\d[\d\s,.]*)\s*(litres|liters|litre|liter|litraa|litra|kilograms|kilogram|kilos|kiloa|kilo|kg|l)\b",
                                text.lower()):
        raw = num.strip().replace(" ", "")
        # "1,000" and "1.000" are thousands, "1,5" is a decimal
        if re.fullmatch(r"\d{1,3}([,.]\d{3})+", raw):
            raw = re.sub(r"[,.]", "", raw)
        else:
            raw = raw.replace(",", ".")
        try:
            out.append((Decimal(raw), UNIT_WORDS[unit]))
        except Exception:
            continue
    return out


def _fmt_qty(amount: float, unit: str) -> str:
    u = {"L": "L", "KG": "kg", "SALES_UNIT": "sales units"}[unit]
    return f"{amount:,.0f} {u}" if float(amount).is_integer() else f"{amount:,.2f} {u}"


def _rule_label(r: dict) -> str:
    if r["rule"] == "lactose_free":
        return "lactose-free"
    if r["rule"] == "exclude_allergen":
        extra = " incl. 'may contain'" if r.get("strictness") == "contains_or_may_contain" else ""
        return f"no {r['allergen']}{extra}"
    if r["rule"] == "origin":
        return f"origin {r['country'].upper()}"
    return r["rule"]


def binding(intent: dict) -> dict:
    """The fields the signature covers. Read-back text and status are not binding."""
    keys = ("intent_id", "buyer_id", "mandate_id", "mandate_hash", "product", "quantity", "deliver_by",
            "hard_rules", "preferences", "budget")
    return {k: intent.get(k) for k in keys}


def intent_hash(intent: dict) -> str:
    return trust.digest(binding(intent))


def _clean_rules(rules: list[dict], mandate_rules: list[dict]) -> list[dict]:
    seen, out = set(), []
    for r in [dict(m, source="mandate") for m in mandate_rules] + [dict(r, source=r.get("source") or "request")
                                                                    for r in rules]:
        r = {k: v for k, v in r.items() if v is not None}
        if r["rule"] == "exclude_allergen":
            r["allergen"] = norm_allergen(r.get("allergen") or "")
            r.setdefault("strictness", "contains_or_may_contain")
            r.pop("country", None)
            key = ("exclude_allergen", r["allergen"])
        elif r["rule"] == "origin":
            r["country"] = (r.get("country") or "").lower()
            r.pop("allergen", None)
            r.pop("strictness", None)
            key = ("origin", r["country"])
        else:
            r = {"rule": "lactose_free", "source": r["source"]}
            key = ("lactose_free",)
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def build(*, buyer_id: str, mandate: dict, session: str | None, original_request: str, product_query: str,
          quantity: float, quantity_unit: str, deliver_by: str, hard_rules: list[dict], optimise: str,
          origin_preference: str | None, budget_max_total: float | None, max_over_pct: float, allow_under: bool,
          agent_questions: list[str], intent_id: str | None = None, check_text: bool = True) -> dict:
    today = now().date()
    questions: list[str] = [q for q in agent_questions if q]
    text = (original_request or "").lower() if check_text else ""
    rules = _clean_rules(hard_rules, mandate.get("mandatory_rules") or [])

    # Deterministic cross-checks between the buyer's own words and what the agent filled in.
    if LACTOSE_FREE.search(text) and not any(r["rule"] == "lactose_free" for r in rules):
        rules.append({"rule": "lactose_free", "source": "request_text"})
    if NON_DAIRY.search(text) and not any(r["rule"] == "exclude_allergen" and r.get("allergen") == "milk"
                                          for r in rules):
        questions.append("You wrote non-dairy. Does that mean a milk allergy (no milk protein at all, lactose-free "
                         "milk is not allowed) or lactose intolerance (lactose-free milk is fine)?")
    mentioned = _numbers_with_units(text)
    if quantity_unit in ("L", "KG") and mentioned and not any(
            u == quantity_unit and abs(n - Decimal(str(quantity))) < Decimal("0.0001") for n, u in mentioned):
        said = ", ".join(_fmt_qty(float(n), u) for n, u in mentioned)
        questions.append(f"Your message says {said}, but the order would be {_fmt_qty(quantity, quantity_unit)}. "
                         f"Which is right?")
    milk_product = re.search(r"\bmilk\b|maito|mjölk|cream|kerma|butter|cheese|juusto|yog", product_query.lower())
    if milk_product and any(r["rule"] == "exclude_allergen" and r.get("allergen") == "milk" for r in rules):
        questions.append(f"'{product_query}' is a dairy product, but there is also a no-milk rule. "
                         f"Did you mean a plant-based alternative?")

    try:
        d = date.fromisoformat(deliver_by)
        if d < today:
            questions.append(f"Delivery date {deliver_by} is in the past.")
    except ValueError:
        questions.append(f"'{deliver_by}' is not a date. When do you need it?")
        d = None
    if quantity <= 0:
        questions.append("How much do you need?")
    if not product_query.strip():
        questions.append("Which product do you need?")
    for r in rules:
        if r["rule"] == "exclude_allergen" and not r.get("allergen"):
            questions.append("Which allergen must be excluded?")
        if r["rule"] == "origin" and len(r.get("country", "")) != 2:
            questions.append("Which country of origin (two-letter code)?")

    intent = {
        "intent_id": intent_id or new_id("int"),
        "status": "proposed",
        "buyer_id": buyer_id,
        "mandate_id": mandate["mandate_id"],
        "mandate_hash": mandate["mandate_hash"],
        "original_request": original_request,
        "product": {"query": product_query.strip()},
        "quantity": {"amount": float(quantity), "unit": quantity_unit, "max_over_pct": float(max_over_pct),
                     "allow_under": bool(allow_under)},
        "deliver_by": deliver_by,
        "hard_rules": rules,
        "preferences": {"optimise": optimise, "origin": (origin_preference or None)},
        "budget": {"max_total": float(budget_max_total), "currency": mandate.get("currency", "EUR")}
        if budget_max_total else None,
        "questions": list(dict.fromkeys(questions)),
        "session": session,
        "created_at": iso(),
        "expires_at": iso(now() + timedelta(hours=config.INTENT_TTL_HOURS)),
        "buyer_signature": None, "confirmed_by": None, "confirmed_at": None, "order_id": None,
    }
    intent["readback"], intent["chips"] = readback(intent, d)
    intent["intent_hash"] = intent_hash(intent)
    return intent


def readback(intent: dict, d: date | None) -> tuple[str, list[dict]]:
    q = intent["quantity"]
    when = f"{d:%a %d %b %Y}" if d else intent["deliver_by"]
    rules = [_rule_label(r) for r in intent["hard_rules"]]
    opt = {"price": "cheapest that delivers in full", "reliability": "most reliable delivery",
           "co2": "lowest CO2"}[intent["preferences"]["optimise"]]
    parts = [f"{_fmt_qty(q['amount'], q['unit'])} {intent['product']['query']}", f"delivered by {when}"]
    if rules:
        parts.append("rules: " + "; ".join(rules))
    parts.append(opt)
    if intent["budget"]:
        parts.append(f"budget max {intent['budget']['max_total']:,.2f} {intent['budget']['currency']}")
    parts.append("rounding up to whole packs, max +{:g} %".format(q["max_over_pct"]) if not q["allow_under"]
                 else "partial delivery allowed")
    chips = [{"field": "quantity", "label": _fmt_qty(q["amount"], q["unit"]), "locked": False},
             {"field": "product", "label": intent["product"]["query"], "locked": False},
             {"field": "deliver_by", "label": f"by {when}", "locked": False}]
    chips += [{"field": "hard_rules", "label": _rule_label(r), "locked": r.get("source") == "mandate"}
              for r in intent["hard_rules"]]
    if intent["budget"]:
        chips.append({"field": "budget", "label": f"max {intent['budget']['max_total']:,.0f} "
                                                  f"{intent['budget']['currency']}", "locked": False})
    chips.append({"field": "preferences", "label": opt, "locked": False})
    return " · ".join(parts), chips


def public(intent: dict) -> dict:
    return {k: v for k, v in intent.items() if k != "session"}


def is_expired(intent: dict) -> bool:
    return now() >= datetime.fromisoformat(intent["expires_at"])


def apply_edits(intent: dict, edits: dict[str, Any]) -> dict:
    """Return build() kwargs for a revised intent. Mandate rules cannot be removed."""
    q = dict(intent["quantity"], **(edits.get("quantity") or {}))
    prefs = dict(intent["preferences"], **(edits.get("preferences") or {}))
    rules = edits.get("hard_rules")
    rules = [r for r in (rules if rules is not None else intent["hard_rules"]) if r.get("source") != "mandate"]
    budget = edits.get("budget", intent["budget"])
    return dict(product_query=(edits.get("product") or {}).get("query", intent["product"]["query"]),
                quantity=float(q["amount"]), quantity_unit=q["unit"], deliver_by=edits.get("deliver_by",
                                                                                          intent["deliver_by"]),
                hard_rules=rules, optimise=prefs.get("optimise") or "price", origin_preference=prefs.get("origin"),
                budget_max_total=(budget or {}).get("max_total"), max_over_pct=float(q.get("max_over_pct", 2)),
                allow_under=bool(q.get("allow_under", False)), agent_questions=[],
                original_request=intent.get("original_request", ""), check_text=False)
