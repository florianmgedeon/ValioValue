"""OrderIntent: the buyer's request as a fixed, checkable object.

An intent has one or more ITEMS (a single product request, or everything a recipe needs).
The agent fills it (propose_intent). Valio Aimo validates it, adds the mandate's mandatory rules,
finds open questions, writes the read-back and hashes the binding fields (RFC 8785 + SHA-256).
Only a human can confirm it, via the buyer backend, with the buyer's signature over the hash.

The guarantee is per item: every item is bought at most once, and only as specified.
Items can be ordered together or in several orders; the intent is fulfilled when every item is ordered.
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


class ItemIn(BaseModel):
    product_query: str = Field(description="Short product description, English, e.g. 'lactose-free milk'")
    quantity: float = Field(gt=0, description="Amount to buy")
    quantity_unit: Literal["L", "KG", "SALES_UNIT"] = Field(description="L, KG or sales units (packs, pieces)")
    hard_rules: list[HardRuleIn] = Field(default_factory=list, description="Must-haves for this item only")
    note: str | None = Field(None, description="Optional, e.g. 'for the risotto, 40 portions'")


UNIT_WORDS = {"l": "L", "litre": "L", "litres": "L", "liter": "L", "liters": "L", "litra": "L", "litraa": "L",
              "kg": "KG", "kilo": "KG", "kilos": "KG", "kilogram": "KG", "kilograms": "KG", "kiloa": "KG"}
NON_DAIRY = re.compile(r"non[- ]?dairy|dairy[- ]?free|no dairy|without dairy|maidoton|maitotuotteeton|mjölkfri")
LACTOSE_FREE = re.compile(r"lactose[- ]?free|laktoositon|laktosfri")
PLANT = re.compile(r"non[- ]?dairy|dairy[- ]?free|plant|oat|soy|almond|coconut|rice|kaura|soija|vegan|alternative")
DAIRY_WORD = re.compile(r"\bmilk\b|maito|mjölk|cream|kerma|butter|cheese|juusto|yog")


def _numbers_with_units(text: str) -> list[tuple[Decimal, str]]:
    out = []
    for num, unit in re.findall(r"(\d[\d\s,.]*)\s*(litres|liters|litre|liter|litraa|litra|kilograms|kilogram|kilos|kiloa|kilo|kg|l)\b",
                                text.lower()):
        raw = num.strip().replace(" ", "")
        if re.fullmatch(r"\d{1,3}([,.]\d{3})+", raw):  # "1,000" and "1.000" are thousands
            raw = re.sub(r"[,.]", "", raw)
        else:  # "1,5" is a decimal
            raw = raw.replace(",", ".")
        try:
            out.append((Decimal(raw), UNIT_WORDS[unit]))
        except Exception:
            continue
    return out


def fmt_qty(amount: float, unit: str) -> str:
    u = {"L": "L", "KG": "kg", "SALES_UNIT": "units"}[unit]
    return f"{amount:,.0f} {u}" if float(amount).is_integer() else f"{amount:,.2f} {u}"


def rule_label(r: dict) -> str:
    if r["rule"] == "lactose_free":
        return "lactose-free"
    if r["rule"] == "exclude_allergen":
        extra = " incl. 'may contain'" if r.get("strictness") == "contains_or_may_contain" else ""
        return f"no {r['allergen']}{extra}"
    if r["rule"] == "origin":
        return f"origin {r['country'].upper()}"
    return r["rule"]


def _rule_key(r: dict) -> tuple:
    if r["rule"] == "exclude_allergen":
        return ("exclude_allergen", r.get("allergen"))
    if r["rule"] == "origin":
        return ("origin", r.get("country"))
    return (r["rule"],)


def _clean_rules(rules: list[dict], default_source: str = "request") -> list[dict]:
    seen, out = set(), []
    for r in rules:
        r = {k: v for k, v in dict(r).items() if v is not None}
        r.setdefault("source", default_source)
        if r["rule"] == "exclude_allergen":
            r["allergen"] = norm_allergen(r.get("allergen") or "")
            r.setdefault("strictness", "contains_or_may_contain")
            r.pop("country", None)
        elif r["rule"] == "origin":
            r["country"] = (r.get("country") or "").lower()
            r.pop("allergen", None)
            r.pop("strictness", None)
        else:
            r = {"rule": "lactose_free", "source": r["source"]}
        if _rule_key(r) in seen:
            continue
        seen.add(_rule_key(r))
        out.append(r)
    return out


def effective_rules(intent: dict, item: dict) -> list[dict]:
    """Rules that apply to one item: mandate and request-wide rules plus the item's own."""
    return _clean_rules(list(intent.get("hard_rules") or []) + list(item.get("hard_rules") or []))


def item_binding(item: dict) -> dict:
    return {k: item.get(k) for k in ("item_id", "product", "quantity", "hard_rules")}


def binding(intent: dict) -> dict:
    """The fields the signature covers. Read-back text and order status are not binding."""
    keys = ("intent_id", "buyer_id", "mandate_id", "mandate_hash", "deliver_by", "hard_rules", "preferences", "budget")
    return {k: intent.get(k) for k in keys} | {"items": [item_binding(i) for i in intent.get("items", [])]}


def intent_hash(intent: dict) -> str:
    return trust.digest(binding(intent))


def build(*, buyer_id: str, mandate: dict, session: str | None, original_request: str, items: list[dict],
          deliver_by: str, hard_rules: list[dict], optimise: str, origin_preference: str | None,
          budget_max_total: float | None, max_over_pct: float, allow_under: bool, agent_questions: list[str],
          intent_id: str | None = None, check_text: bool = True, clarifications: list[str] | None = None) -> dict:
    today = now().date()
    questions: list[str] = [q for q in agent_questions if q]
    clarifications = [c for c in (clarifications or []) if c and c.strip()]
    # The buyer's answers count as the buyer's own words for every cross-check below.
    text = ("\n".join([original_request or ""] + clarifications)).lower() if check_text else ""
    global_rules = _clean_rules([dict(m, source="mandate") for m in mandate.get("mandatory_rules") or []]
                                + [r for r in hard_rules if r.get("source") != "mandate"])

    norm_items = []
    for n, it in enumerate(items, start=1):
        q = it.get("quantity")
        if isinstance(q, dict):  # already normalised (edits)
            amount, unit = float(q["amount"]), q["unit"]
            pct, under = float(q.get("max_over_pct", max_over_pct)), bool(q.get("allow_under", allow_under))
        else:
            amount, unit = float(it["quantity"]), it["quantity_unit"]
            pct, under = float(max_over_pct), bool(allow_under)
        query = ((it.get("product") or {}).get("query") if isinstance(it.get("product"), dict)
                 else it.get("product_query")) or ""
        rules = _clean_rules([r for r in it.get("hard_rules") or [] if r.get("source") != "mandate"])
        # The buyer's own words win: "lactose-free" in the text or the item name becomes a rule.
        if not any(r["rule"] == "lactose_free" for r in rules + global_rules) and (
                LACTOSE_FREE.search(query.lower()) or (len(items) == 1 and LACTOSE_FREE.search(text))):
            rules.append({"rule": "lactose_free", "source": "request_text"})
        norm_items.append({"item_id": it.get("item_id") or f"i{n}", "product": {"query": query.strip()},
                           "quantity": {"amount": amount, "unit": unit, "max_over_pct": pct, "allow_under": under},
                           "hard_rules": rules, "note": it.get("note"),
                           "status": "open", "order_id": None})

    # Deterministic cross-checks between the buyer's own words and what the agent filled in.
    all_rules = global_rules + [r for i in norm_items for r in i["hard_rules"]]
    if NON_DAIRY.search(text) and not any((r["rule"] == "exclude_allergen" and r.get("allergen") == "milk")
                                          or r["rule"] == "lactose_free" for r in all_rules):
        questions.append("You wrote non-dairy. Does that mean a milk allergy (no milk protein at all, lactose-free "
                         "milk is not allowed) or lactose intolerance (lactose-free milk is fine)?")
    if len(norm_items) == 1:  # with recipes the agent scales, so numbers legitimately differ from the text
        i0 = norm_items[0]
        mentioned = _numbers_with_units(text)
        unit, amount = i0["quantity"]["unit"], Decimal(str(i0["quantity"]["amount"]))
        if unit in ("L", "KG") and mentioned and not any(u == unit and abs(n - amount) < Decimal("0.0001")
                                                          for n, u in mentioned):
            said = ", ".join(fmt_qty(float(n), u) for n, u in mentioned)
            questions.append(f"Your message says {said}, but the order would be {fmt_qty(float(amount), unit)}. "
                             f"Which is right?")
    for i in norm_items:
        q = i["product"]["query"].lower()
        eff = _clean_rules(global_rules + i["hard_rules"])
        if not PLANT.search(q) and DAIRY_WORD.search(q) and any(
                r["rule"] == "exclude_allergen" and r.get("allergen") == "milk" for r in eff):
            questions.append(f"'{i['product']['query']}' is a dairy product, but there is a no-milk rule. "
                             f"Did you mean a plant-based alternative?")
        if not q.strip():
            questions.append(f"Item {i['item_id']}: which product?")
        if i["quantity"]["amount"] <= 0:
            questions.append(f"How much {i['product']['query']}?")
    if not norm_items:
        questions.append("What do you need?")
    ids = [i["item_id"] for i in norm_items]
    if len(set(ids)) != len(ids):
        questions.append("Two items have the same id.")
    try:
        d = date.fromisoformat(deliver_by)
        if d < today:
            questions.append(f"Delivery date {deliver_by} is in the past.")
    except ValueError:
        questions.append(f"'{deliver_by}' is not a date. When do you need it?")
        d = None
    for r in all_rules:
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
        "clarifications": clarifications,
        "items": norm_items,
        "deliver_by": deliver_by,
        "hard_rules": global_rules,
        "preferences": {"optimise": optimise, "origin": (origin_preference or None)},
        "budget": {"max_total": float(budget_max_total), "currency": mandate.get("currency", "EUR")}
        if budget_max_total else None,
        "spent": 0.0,
        "questions": list(dict.fromkeys(questions)),
        "session": session,
        "created_at": iso(),
        "expires_at": iso(now() + timedelta(hours=config.INTENT_TTL_HOURS)),
        "buyer_signature": None, "confirmed_by": None, "confirmed_at": None, "order_ids": [],
    }
    intent["readback"], intent["chips"] = readback(intent, d)
    intent["intent_hash"] = intent_hash(intent)
    return intent


def readback(intent: dict, d: date | None) -> tuple[str, list[dict]]:
    when = f"{d:%a %d %b %Y}" if d else intent["deliver_by"]
    opt = {"price": "cheapest that delivers in full", "reliability": "most reliable delivery",
           "co2": "lowest CO2"}[intent["preferences"]["optimise"]]
    items = intent["items"]

    def item_text(i: dict) -> str:
        own = [rule_label(r) for r in i["hard_rules"] if r["rule"] != "lactose_free"
               or "lactose" not in i["product"]["query"].lower()]
        return f"{fmt_qty(i['quantity']['amount'], i['quantity']['unit'])} {i['product']['query']}" + (
            f" ({', '.join(own)})" if own else "")

    head = item_text(items[0]) if len(items) == 1 else f"{len(items)} items: " + "; ".join(item_text(i) for i in items)
    parts = [head, f"delivered by {when}"]
    rules = [rule_label(r) for r in intent["hard_rules"]]
    if len(items) == 1:
        rules += [rule_label(r) for r in items[0]["hard_rules"]]
        rules = list(dict.fromkeys(rules))
    if rules:
        parts.append(("all items: " if len(items) > 1 else "rules: ") + "; ".join(rules))
    parts.append(opt)
    if intent["budget"]:
        parts.append(f"budget max {intent['budget']['max_total']:,.2f} {intent['budget']['currency']}")
    q0 = items[0]["quantity"] if items else {"max_over_pct": 0, "allow_under": False}
    parts.append("rounding up to whole packs, max +{:g} %".format(q0["max_over_pct"]) if not q0["allow_under"]
                 else "partial delivery allowed")

    chips = [{"field": f"items.{i['item_id']}", "label": item_text(i), "locked": False} for i in items]
    chips.append({"field": "deliver_by", "label": f"by {when}", "locked": False})
    chips += [{"field": "hard_rules", "label": rule_label(r), "locked": r.get("source") == "mandate"}
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


def get_item(intent: dict, item_id: str) -> dict | None:
    return next((i for i in intent.get("items", []) if i["item_id"] == item_id), None)


def open_items(intent: dict) -> list[dict]:
    return [i for i in intent.get("items", []) if i["status"] == "open"]


def apply_edits(intent: dict, edits: dict[str, Any]) -> dict:
    """build() kwargs for a revised intent. Items are merged by item_id ('remove': true drops one,
    new item_ids are added). Mandate rules cannot be removed."""
    by_id = {i["item_id"]: dict(i) for i in intent["items"]}
    order = [i["item_id"] for i in intent["items"]]
    for e in edits.get("items") or []:
        iid = e.get("item_id")
        if e.get("remove"):
            by_id.pop(iid, None)
            continue
        base = by_id.get(iid) or {"item_id": iid or f"i{len(order) + 1}", "product": {"query": ""},
                                  "quantity": {"amount": 0, "unit": "SALES_UNIT"}, "hard_rules": []}
        if "product" in e:
            base["product"] = {"query": e["product"].get("query", base["product"]["query"])}
        if "product_query" in e:
            base["product"] = {"query": e["product_query"]}
        if "quantity" in e:
            q = e["quantity"] if isinstance(e["quantity"], dict) else {"amount": e["quantity"]}
            base["quantity"] = dict(base["quantity"], **q)
        if "hard_rules" in e:
            base["hard_rules"] = e["hard_rules"]
        by_id[base["item_id"]] = base
        if base["item_id"] not in order:
            order.append(base["item_id"])
    items = [by_id[i] for i in order if i in by_id]
    prefs = dict(intent["preferences"], **(edits.get("preferences") or {}))
    rules = edits.get("hard_rules")
    rules = [r for r in (rules if rules is not None else intent["hard_rules"]) if r.get("source") != "mandate"]
    budget = edits.get("budget", intent["budget"])
    q0 = intent["items"][0]["quantity"] if intent["items"] else {}
    return dict(items=items, deliver_by=edits.get("deliver_by", intent["deliver_by"]), hard_rules=rules,
                optimise=prefs.get("optimise") or "price", origin_preference=prefs.get("origin"),
                budget_max_total=(budget or {}).get("max_total"), max_over_pct=float(q0.get("max_over_pct", 2)),
                allow_under=bool(q0.get("allow_under", False)), agent_questions=[],
                original_request=intent.get("original_request", ""), check_text=False,
                clarifications=intent.get("clarifications"))
