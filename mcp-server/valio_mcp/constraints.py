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


def eval_origin(p: Product, country: str) -> dict:
    if not p.country:
        return {"rule": f"origin:{country}", "verdict": "unknown", "signals": []}
    ok = p.country.lower() == country.lower()
    return {"rule": f"origin:{country}", "verdict": "safe" if ok else "excluded",
            "signals": [_sig("catalog", "countryOfOrigin", p.country, "safe" if ok else "excluded")]}


def evaluate(p: Product, rule: dict) -> dict:
    kind = rule.get("rule")
    if kind == "lactose_free":
        ev = eval_lactose_free(p)
    elif kind == "exclude_allergen":
        ev = eval_allergen(p, rule.get("allergen", ""), rule.get("strictness") or "contains_or_may_contain")
    elif kind == "origin":
        ev = eval_origin(p, rule.get("country", ""))
    else:
        ev = {"rule": str(kind), "verdict": "unknown", "signals": []}
    ev["source"] = rule.get("source", "request")
    return ev


def evaluate_all(p: Product, rules: list[dict]) -> list[dict]:
    return [evaluate(p, r) for r in rules]


def one_line(ev: dict) -> str:
    """'contains milk (GS1 allergen.milk CONTAINS)' style reason for rejected options."""
    bad = [s for s in ev["signals"] if s["says"] == "excluded"]
    if ev["verdict"] == "unknown":
        return f"{ev['rule']}: no data to prove it, treated as not safe"
    s = bad[0] if bad else None
    return f"{ev['rule']}: {s['value']} ({s['source']} {s['field']})" if s else ev["rule"]
