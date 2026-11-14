"""Hard rules, decided from data. Never by the LLM.

Every product gets one of three verdicts per rule, with the signals that led to it:
  safe      every signal agrees the rule is met
  excluded  at least one signal says it is not (OR logic: one "contains" is enough)
  unknown   not enough data. Unknown is never treated as safe.

Signals: GS1 allergen levels (CONTAINS / MAY_CONTAIN / FREE_FROM), nutritional claims,
product name, and a scan of the ingredient text for milk derivatives.
"""
from __future__ import annotations

import re
from typing import Any

from .catalog import Product

# Words that mean "the buyer means milk" when naming an allergen.
ALLERGEN_ALIASES = {
    "dairy": "milk", "milk protein": "milk", "maito": "milk", "casein": "milk",
    "nuts": "tree nuts", "nut": "tree nuts", "tree nut": "tree nuts",
    "peanut": "peanuts", "eggs": "egg", "wheat gluten": "gluten", "soya": "soy",
    "sulfites": "sulphites", "shellfish": "crustaceans", "sesame seeds": "sesame",
}


def norm_allergen(name: str) -> str:
    n = name.strip().lower()
    return ALLERGEN_ALIASES.get(n, n)


# --------------------------------------------------------------------------- milk derivative scan

# Whole tokens (en/sv) that indicate milk.
MILK_TOKENS = {
    "milk", "milks", "cream", "butter", "cheese", "whey", "casein", "caseins", "caseinate", "caseinates",
    "lactalbumin", "lactoglobulin", "ghee", "curd", "curds", "yoghurt", "yogurt", "buttermilk", "lactose",
    "mjölk", "grädde", "smör", "ost", "vassle", "kasein", "kaseinat", "voi", "maito",
}
# Finnish compounds: substring inside a token is enough ("kevytmaitojuoma", "herajauhe").
MILK_STEMS = ("maito", "kerma", "juusto", "kaseiini", "kaseinaat", "laktoosi", "maitojauhe", "heraproteiini",
              "herajauhe", "rahka", "jogurtti", "piimä")
# Plant or non-dairy look-alikes. "The word milk is not a dairy signal" in these.
NOT_MILK = ("coconut milk", "oat milk", "soy milk", "soya milk", "almond milk", "rice milk", "cocoa butter",
            "peanut butter", "shea butter", "nut butter", "coconut cream", "oat cream", "soy cream",
            "cream of tartar", "kookosmaito", "kookoskerma", "kaakaovoi", "maapähkinävoi", "kaurakerma",
            "soijakerma", "soijamaito", "mantelimaito", "riisimaito", "kauramaito", "sheavoi")
NEGATED = re.compile(r"(free|vapaa|ton|tön|fri)$")
# In a product NAME, generic words like "cream" or "milk" describe the use, not the source, when the name is
# clearly plant-based ("Oat cooking cream", "Soy milk drink"). Casein, whey etc. always count.
PLANT_MARK = re.compile(r"\b(oat|soy|soya|almond|coconut|rice|pea|plant[- ]based|vegan|kaura|soija|manteli|kookos|kasvipohjainen)\b")
GENERIC_DAIRY = {"milk", "milks", "cream", "butter", "cheese", "yoghurt", "yogurt", "maito", "kerma", "voi", "juusto",
                 "jogurtti", "mjölk", "grädde", "smör", "ost"}


def milk_scan(text: str | None, *, is_name: bool = False) -> list[str]:
    """Milk derivative words found in a text. Handles plant look-alikes and negations ('maidoton',
    'milk-free', 'laktoositon' does not hit on 'laktoosi', but '...maitojuoma' still hits on 'maito')."""
    if not text:
        return []
    t = text.lower()
    for phrase in NOT_MILK:
        t = t.replace(phrase, " ")
    t = re.sub(r"\b[\wäöå]+[ -](?:free|fri)\b", " ", t)  # "milk-free", "dairy free"
    hits: list[str] = []
    plant_name = is_name and bool(PLANT_MARK.search(t))
    for tok in re.findall(r"[\wäöå]+", t):
        if plant_name and (tok in GENERIC_DAIRY or any(tok.startswith(g) or tok.endswith(g) for g in GENERIC_DAIRY
                                                       if len(g) > 3)):
            continue
        if NEGATED.search(tok):  # maidoton, laktoositon, mjölkfri
            continue
        if tok in MILK_TOKENS:
            hits.append(tok)
            continue
        for stem in MILK_STEMS:
            if stem in tok:
                hits.append(tok)
                break
    return sorted(set(hits))


# --------------------------------------------------------------------------- evaluation

def _sig(source: str, field: str, value: Any, says: str) -> dict:
    return {"source": source, "field": field, "value": str(value), "says": says}


def _verdict(signals: list[dict], fallback: str) -> str:
    if any(s["says"] == "excluded" for s in signals):
        return "excluded"
    if any(s["says"] == "safe" for s in signals):
        return "safe"
    return fallback


def eval_allergen(p: Product, allergen: str, strictness: str = "contains_or_may_contain") -> dict:
    a = norm_allergen(allergen)
    signals: list[dict] = []
    related = {a} | ({"lactose"} if a == "milk" else set())
    for x in sorted(related & set(p.allergens_contains)):
        signals.append(_sig("gs1", f"allergen.{x}", "CONTAINS", "excluded"))
    for x in sorted(related & set(p.allergens_may_contain)):
        signals.append(_sig("gs1", f"allergen.{x}", "MAY_CONTAIN",
                            "excluded" if strictness == "contains_or_may_contain" else "safe"))
    if a in p.allergens_free_from:
        signals.append(_sig("gs1", f"allergen.{a}", "FREE_FROM", "safe"))
    if a == "milk":
        for where, text in (("ingredients", p.key_ingredients), ("name", p.names_all)):
            hits = milk_scan(text, is_name=(where == "name"))
            if hits:
                signals.append(_sig(where, "milk derivatives", ", ".join(hits), "excluded"))
        for c in p.claims:
            if c.upper() in ("FREE_FROM_MILK", "FREE_FROM_DAIRY", "FREE_FROM_MILK_PROTEIN"):
                signals.append(_sig("claim", "nutritionalClaim", c, "safe"))
    if not signals and p.has_allergen_data:
        signals.append(_sig("gs1", "allergen list", f"declared, {a} not listed", "safe"))
    verdict = _verdict(signals, "unknown")
    return {"rule": f"exclude_allergen:{a}", "verdict": verdict, "signals": signals}


def eval_lactose_free(p: Product) -> dict:
    signals: list[dict] = []
    if "lactose" in p.allergens_contains:
        signals.append(_sig("gs1", "allergen.lactose", "CONTAINS", "excluded"))
    if "lactose" in p.allergens_may_contain:
        signals.append(_sig("gs1", "allergen.lactose", "MAY_CONTAIN", "excluded"))
    if "lactose" in p.allergens_free_from:
        signals.append(_sig("gs1", "allergen.lactose", "FREE_FROM", "safe"))
    claims = {c.upper() for c in p.claims}
    if "FREE_FROM_LACTOSE" in claims:
        signals.append(_sig("claim", "nutritionalClaim", "FREE_FROM_LACTOSE", "safe"))
    elif "LOW_LACTOSE" in claims:
        signals.append(_sig("claim", "nutritionalClaim", "LOW_LACTOSE (not lactose-free)", "excluded"))
    if re.search(r"laktoositon|laktosfri|lactose[ -]free", p.names_all):
        signals.append(_sig("name", "name", "lactose-free", "safe"))
    has_safe = any(s["says"] == "safe" for s in signals)
    milk = "milk" in p.allergens_contains or bool(milk_scan(p.names_all) or milk_scan(p.key_ingredients))
    if milk and not has_safe:
        signals.append(_sig("catalog", "milk", "contains milk, no lactose-free declaration", "excluded"))
    if not signals and p.has_allergen_data and not milk:
        signals.append(_sig("gs1", "allergen list", "declared, no milk or lactose", "safe"))
    return {"rule": "lactose_free", "verdict": _verdict(signals, "unknown"), "signals": signals}


def _flip(ev: dict) -> dict:
    """'contains X' is the opposite verdict of 'not_contains X'."""
    flip = {"safe": "excluded", "excluded": "safe", "unknown": "unknown"}
    return ev | {"verdict": flip[ev["verdict"]],
                 "signals": [s | {"says": flip.get(s["says"], s["says"])} for s in ev["signals"]]}


def product_value(p: Product, attr: str, offer: dict | None = None) -> Any:
    """Raw value of a plain attribute. None = unknown. Offer attributes need an offer."""
    if attr in ("unit_price", "comparison_price"):
        return (offer or {}).get(attr)
    if attr == "line_total":
        return (offer or {}).get("total_price")
    return {"product_id": p.id, "name": p.name, "brand": p.brand, "vendor": p.vendor, "category": p.category,
            "country": (p.country or "").lower() or None, "labels": p.labels, "claims": p.claims,
            "allergens_free_from": p.allergens_free_from, "delivery_reliability": p.delivery_reliability,
            "co2_kg_per_kg": p.co2_kg_per_kg}.get(attr)


def _compare(op: str, have: Any, want: Any) -> bool:
    if op in ("eq", "ne"):
        same = (str(have).lower() == str(want).lower()) if isinstance(have, str) else have == want
        return same if op == "eq" else not same
    if op in ("in", "not_in"):
        hit = str(have).lower() in {str(w).lower() for w in want}
        return hit if op == "in" else not hit
    if op in ("contains", "not_contains"):
        hit = str(want).lower() in {str(h).lower() for h in have}
        return hit if op == "contains" else not hit
    return {"lt": have < want, "lte": have <= want, "gt": have > want, "gte": have >= want}[op]


def check(p: Product, c: dict, offer: dict | None = None) -> dict | None:
    """Verdict for one normalised condition. None if it cannot be checked yet (offer attribute, no offer)."""
    from .rules import ATTRIBUTES
    a, op, v = c["attr"], c["op"], c["value"]
    if ATTRIBUTES[a][1] == "offer" and offer is None:
        return None
    if a in ("allergens", "allergens_contains") and op in ("contains", "not_contains"):
        ev = eval_allergen(p, v, "contains_or_may_contain" if a == "allergens" else "contains")
        return ev if op == "not_contains" else _flip(ev)
    if a == "lactose_free":
        ev = eval_lactose_free(p)
        return ev if v else _flip(ev)
    have = product_value(p, a, offer)
    field = {"country": "countryOfOrigin"}.get(a, a)
    src = "price API" if ATTRIBUTES[a][1] == "offer" else "catalog"
    if have is None:
        return {"rule": a, "verdict": "unknown", "signals": []}
    ok = _compare(op, have, v)
    shown = ", ".join(map(str, have)) if isinstance(have, list) else have
    return {"rule": a, "verdict": "safe" if ok else "excluded",
            "signals": [_sig(src, field, shown if shown != "" else "none", "safe" if ok else "excluded")]}


def evaluate(p: Product, rule: dict, offer: dict | None = None) -> dict | None:
    """Verdict + evidence for a must (or preference). Accepts V2 shorthands. None = not applicable here:
    the rule is off, its applies_to does not match this product, or it needs a price we do not have yet."""
    from .rules import rule as norm
    r = rule if "attr" in rule and "label" in rule else norm(rule, source=rule.get("source", "request"))
    if not r.get("enabled", True):
        return None
    for cond in r.get("applies_to") or []:
        ev = check(p, cond, offer)
        if ev is None or ev["verdict"] != "safe":
            return None
    ev = check(p, r, offer)
    if ev is None:
        return None
    ev["rule"] = r["label"]
    ev["id"] = r.get("id")
    ev["source"] = r.get("source", "request")
    ev["if_unknown"] = r.get("if_unknown", "exclude")
    return ev


def evaluate_all(p: Product, rules: list[dict], offer: dict | None = None) -> list[dict]:
    return [ev for ev in (evaluate(p, r, offer) for r in rules) if ev is not None]


def violates(ev: dict) -> bool:
    """A must is broken if the data says so, or if it is unknown and the rule says 'exclude if unknown'."""
    return ev["verdict"] == "excluded" or (ev["verdict"] == "unknown" and ev.get("if_unknown") != "ask")


def facts(p: Product, offer: dict | None = None) -> dict:
    """Product attributes as plain values, for Cedar. Unknown values are left out."""
    from .rules import ATTRIBUTES
    out: dict[str, Any] = {}
    for a, (typ, where, _) in ATTRIBUTES.items():
        if a in ("allergens", "allergens_contains"):
            data = p.has_allergen_data or p.allergens_contains or p.allergens_may_contain
            if data:
                out[a] = sorted(set(p.allergens_contains) | (set(p.allergens_may_contain) if a == "allergens" else set()))
            continue
        if a == "lactose_free":
            v = eval_lactose_free(p)["verdict"]
            if v != "unknown":
                out[a] = v == "safe"
            continue
        v = product_value(p, a, offer)
        if v is not None:
            out[a] = list(v) if typ == "set" else v
    return out


def one_line(ev: dict) -> str:
    """'contains milk (GS1 allergen.milk CONTAINS)' style reason for rejected options."""
    bad = [s for s in ev["signals"] if s["says"] == "excluded"]
    if ev["verdict"] == "unknown":
        return f"{ev['rule']}: no data to prove it, treated as not safe"
    s = bad[0] if bad else None
    return f"{ev['rule']}: {s['value']} ({s['source']} {s['field']})" if s else ev["rule"]
