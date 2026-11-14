"""Generic conditions over product data: the one language for musts, preferences and grant scopes.

A condition is {attr, op, value}. Attributes come from the product data (plus a few derived ones whose
meaning needs several signals, e.g. lactose_free). Nobody has to define rule kinds: any attribute the data
has can be used with the operators of its type.

Shorthands from V2 still work and are kept on the normalised rule for older clients:
  {"rule": "lactose_free"}                                  -> lactose_free eq true
  {"rule": "exclude_allergen", "allergen": "milk"}          -> allergens not_contains milk   (incl. may contain)
  {"rule": "exclude_allergen", ..., "strictness": "contains"} -> allergens_contains not_contains milk
  {"rule": "origin", "country": "fi"}                       -> country eq fi
"""
from __future__ import annotations

from typing import Any

# name -> (type, where the value comes from, description)
ATTRIBUTES: dict[str, tuple[str, str, str]] = {
    "product_id": ("string", "product", "Valio Aimo product id"),
    "name": ("string", "product", "Product name"),
    "brand": ("string", "product", "Brand (Synkka)"),
    "vendor": ("string", "product", "Vendor / manufacturer"),
    "category": ("string", "product", "Valio category code"),
    "country": ("string", "product", "Country of origin, ISO alpha-2"),
    "labels": ("set", "product", "Packaging labels and certificates, e.g. HYVAA_SUOMESTA, EU-Bio"),
    "claims": ("set", "product", "Nutritional claims, e.g. FREE_FROM_LACTOSE"),
    "allergens": ("set", "product", "Allergens the product contains or may contain (GS1, claims, ingredient scan)"),
    "allergens_contains": ("set", "product", "Allergens the product contains (may-contain ignored)"),
    "allergens_free_from": ("set", "product", "Allergens declared FREE_FROM"),
    "lactose_free": ("bool", "product", "Lactose-free from GS1, claims and name"),
    "delivery_reliability": ("number", "product", "Share of deliveries on time, 0 to 1"),
    "co2_kg_per_kg": ("number", "product", "CO2e per kg, category average"),
    "unit_price": ("number", "offer", "Net price per sales unit, EUR"),
    "comparison_price": ("number", "offer", "Net price per litre or kilo, EUR"),
    "line_total": ("number", "offer", "Net total of the order line, EUR"),
}

OPS: dict[str, tuple[str, ...]] = {
    "string": ("eq", "ne", "in", "not_in"),
    "set": ("contains", "not_contains"),
    "bool": ("eq",),
    "number": ("lt", "lte", "gt", "gte", "eq"),
}

OP_WORDS = {"eq": "is", "ne": "is not", "in": "is one of", "not_in": "is none of", "contains": "includes",
            "not_contains": "excludes", "lt": "<", "lte": "≤", "gt": ">", "gte": "≥"}


class RuleError(ValueError):
    pass


def _norm_value(attr: str, op: str, value: Any) -> Any:
    from .constraints import norm_allergen
    typ = ATTRIBUTES[attr][0]
    if op in ("in", "not_in"):
        if not isinstance(value, list) or not value:
            raise RuleError(f"{attr} {op}: value must be a non-empty list")
        return [str(v).lower() if attr == "country" else str(v) for v in value]
    if typ == "bool":
        if not isinstance(value, bool):
            raise RuleError(f"{attr}: value must be true or false")
        return value
    if typ == "number":
        try:
            return float(value)
        except (TypeError, ValueError):
            raise RuleError(f"{attr}: value must be a number") from None
    if value is None or str(value).strip() == "":
        raise RuleError(f"{attr} {op}: value is required")
    if attr.startswith("allergens"):
        return norm_allergen(str(value))
    return str(value).lower() if attr == "country" else str(value)


def condition(c: dict) -> dict:
    """Validate and normalise one {attr, op, value}, shorthands accepted."""
    c = {k: v for k, v in dict(c).items() if v is not None}
    kind = c.get("rule")
    if kind == "lactose_free":
        c |= {"attr": "lactose_free", "op": "eq", "value": True}
    elif kind == "exclude_allergen":
        strict = c.get("strictness") or "contains_or_may_contain"
        c |= {"attr": "allergens" if strict == "contains_or_may_contain" else "allergens_contains",
              "op": "not_contains", "value": c.get("allergen")}
    elif kind == "origin":
        c |= {"attr": "country", "op": "eq", "value": c.get("country")}
    attr, op = c.get("attr"), c.get("op")
    if attr not in ATTRIBUTES:
        raise RuleError(f"unknown attribute '{attr}'. Known: {', '.join(ATTRIBUTES)}")
    if op not in OPS[ATTRIBUTES[attr][0]]:
        raise RuleError(f"operator '{op}' not allowed for {attr} ({ATTRIBUTES[attr][0]}): {', '.join(OPS[ATTRIBUTES[attr][0]])}")
    return {"attr": attr, "op": op, "value": _norm_value(attr, op, c.get("value"))}


def shorthand(c: dict) -> dict:
    """The V2 shape of a condition, if it has one (older UIs and the read-back use it)."""
    a, op, v = c["attr"], c["op"], c["value"]
    if a == "lactose_free" and op == "eq" and v is True:
        return {"rule": "lactose_free"}
    if a in ("allergens", "allergens_contains") and op == "not_contains":
        return {"rule": "exclude_allergen", "allergen": v,
                "strictness": "contains_or_may_contain" if a == "allergens" else "contains"}
    if a == "country" and op == "eq":
        return {"rule": "origin", "country": v}
    return {"rule": "condition"}


def label(c: dict) -> str:
    a, op, v = c["attr"], c["op"], c["value"]
    if a == "lactose_free":
        return "lactose-free" if v else "not lactose-free"
    if a in ("allergens", "allergens_contains") and op == "not_contains":
        return f"no {v}" + (" incl. 'may contain'" if a == "allergens" else "")
    if a == "country" and op == "eq":
        return f"origin {str(v).upper()}"
    if isinstance(v, list):
        v = ", ".join(v)
    if isinstance(v, float):
        v = f"{v:g}"
    return f"{a.replace('_', ' ')} {OP_WORDS[op]} {v}"


def rule(r: dict, *, source: str, rid: str | None = None) -> dict:
    """A must or preference: condition + id, label, source, enabled, if_unknown, applies_to, shorthand fields."""
    c = condition(r)
    out = {"id": r.get("id") or rid, **c, "label": r.get("label") or label(c),
           "source": r.get("source") or source, "enabled": bool(r.get("enabled", True))}
    out["if_unknown"] = r.get("if_unknown") or "exclude"
    if out["if_unknown"] not in ("exclude", "ask"):
        raise RuleError("if_unknown must be 'exclude' or 'ask'")
    out["applies_to"] = [condition(x) for x in r.get("applies_to") or []]
    for k, v in shorthand(c).items():
        out[k] = v
    return out


def key(r: dict) -> tuple:
    """Two rules with the same condition are the same rule."""
    v = r["value"]
    return (r["attr"], r["op"], tuple(v) if isinstance(v, list) else v,
            tuple((a["attr"], a["op"], str(a["value"])) for a in r.get("applies_to") or []))


def dedupe(rules: list[dict]) -> list[dict]:
    seen, out = set(), []
    for r in rules:
        if key(r) not in seen:
            seen.add(key(r))
            out.append(r)
    return out
