"""Rule tools for the Kitchen tab (work package 3.1 and 3.2).

POST /mandate/draft     a sentence ("No sesame, not even traces") -> one checked rule. Nothing is saved:
                        the UI adds it to its draft and signs it with the normal PUT /mandate.
POST /mandate/coverage  how many catalog products the rule can decide from data (passthrough to Valio).

The LLM only proposes {attr, op, value}. Valio's own mandate preview validates it, and the label and the
explanation the buyer reads are built from the validated rule, not from the model's words.
"""
from __future__ import annotations

import logging
import re
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import config
from .llm import ToolSpec, make_provider

log = logging.getLogger(__name__)
router = APIRouter()

DRAFT_ID = "draft"


async def _valio(method: str, path: str, **kw) -> httpx.Response:
    async with httpx.AsyncClient(timeout=30) as c:
        return await c.request(method, f"{config.MCP_BASE_URL}{path}",
                               headers={"Authorization": f"Bearer {config.APPROVER_TOKEN}"}, **kw)


def _json(r: httpx.Response):
    if r.headers.get("content-type", "").startswith("application/json"):
        return r.json()
    return r.text


def _ok(r: httpx.Response):
    if r.status_code >= 400:
        raise HTTPException(r.status_code, _json(r))
    return r.json()


class DraftError(ValueError):
    """The sentence could not be turned into a rule. The message is shown to the buyer."""


# --------------------------------------------------------------------------- scripted parsing (no LLM)

ALLERGEN_WORDS = {
    "peanut": "peanuts", "peanuts": "peanuts", "nut": "tree nuts", "nuts": "tree nuts", "tree nut": "tree nuts",
    "tree nuts": "tree nuts", "milk": "milk", "dairy": "milk", "milk protein": "milk", "lactose": "lactose",
    "egg": "egg", "eggs": "egg", "gluten": "gluten", "wheat": "wheat", "soy": "soy", "soya": "soy",
    "fish": "fish", "shellfish": "crustaceans", "crustaceans": "crustaceans", "molluscs": "molluscs",
    "sesame": "sesame", "sesame seeds": "sesame", "celery": "celery", "mustard": "mustard", "lupin": "lupin",
    "sulphites": "sulphites", "sulfites": "sulphites", "oats": "oats", "rye": "rye", "barley": "barley",
}
COUNTRY_WORDS = {
    "finnish": "fi", "finland": "fi", "swedish": "se", "sweden": "se", "estonian": "ee", "estonia": "ee",
    "danish": "dk", "denmark": "dk", "norwegian": "no", "norway": "no", "german": "de", "germany": "de",
    "dutch": "nl", "netherlands": "nl", "eu": None,
}
COUNTRY_NAMES = {"fi": "Finnish", "se": "Swedish", "ee": "Estonian", "dk": "Danish", "no": "Norwegian",
                 "de": "German", "nl": "Dutch"}
_NO = re.compile(r"^\s*(?:no|without|free from|exclude|never any|never)\s+(?:any\s+)?(.+?)"
                 r"(?=[,.;!]|\s+(?:at all|not even|even|incl\.?|including|please|ever)\b|$)", re.I)
_MAY_OK = re.compile(r"(may[- ]contain|traces?)\s+(?:is|are)?\s*(?:ok|okay|fine|allowed|acceptable)", re.I)
_ONLY = re.compile(r"^\s*only\s+([a-z]+)\s+(.+?)\s*[.!]?\s*$", re.I)
_ONLY_FROM = re.compile(r"^\s*only\s+(.+?)\s+from\s+([a-z]+)\s*[.!]?\s*$", re.I)


def scripted_draft(text: str) -> dict:
    """'No <allergen>' and 'Only Finnish <products>'. Anything else: use the form."""
    m = _NO.match(text)
    if m:
        word = m.group(1).strip().lower()
        allergen = ALLERGEN_WORDS.get(word) or ALLERGEN_WORDS.get(word.rstrip("s"))
        if allergen:
            attr = "allergens_contains" if _MAY_OK.search(text) else "allergens"
            return {"attr": attr, "op": "not_contains", "value": allergen}
    m = _ONLY.match(text)
    if m and m.group(1).lower() in COUNTRY_WORDS and COUNTRY_WORDS[m.group(1).lower()]:
        return {"attr": "country", "op": "eq", "value": COUNTRY_WORDS[m.group(1).lower()],
                "product_type": m.group(2).strip()}
    m = _ONLY_FROM.match(text)
    if m and COUNTRY_WORDS.get(m.group(2).lower()):
        return {"attr": "country", "op": "eq", "value": COUNTRY_WORDS[m.group(2).lower()],
                "product_type": m.group(1).strip()}
    raise DraftError("I could not turn this into a rule without the language model. Please use the form.")


# --------------------------------------------------------------------------- LLM drafting

DRAFT_TOOL = ToolSpec(
    "draft_rule",
    "Return the one rule the buyer's sentence means.",
    {"type": "object", "properties": {
        "attr": {"type": "string", "description": "One attribute from the list."},
        "op": {"type": "string", "description": "An operator allowed for that attribute's type."},
        "value": {"description": "String, number, true/false, or a list for in / not_in."},
        "if_unknown": {"type": "string", "enum": ["exclude", "ask"],
                       "description": "Musts only. 'exclude' unless the buyer says to ask when data is missing."},
        "weight": {"type": "integer", "minimum": 1, "maximum": 3,
                   "description": "Preferences only. 3 = strong, 1 = slight."},
        "product_type": {"type": "string", "description": "If the rule is only for some products ('only "
                         "Finnish fish'), the product word in English ('fish'). Leave out otherwise."},
    }, "required": ["attr", "op", "value"]})
CANNOT_TOOL = ToolSpec(
    "cannot_draft", "The sentence is not one rule the product data can check.",
    {"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"]})


def _system(catalog: dict, kind: str) -> str:
    attrs = "\n".join(
        f"- {a['attr']} ({a['type']}; ops: {', '.join(a['ops'])}): {a['description']}"
        + (f". Values seen: {', '.join(map(str, a['examples'][:15]))}" if a.get("examples") else "")
        for a in catalog["attributes"] if a["source"] == "product")
    return f"""You turn one sentence from a professional kitchen's buyer into one {kind} rule for food ordering.
A must is a hard rule (a product that breaks it is never bought). A preference ranks products.
Call draft_rule exactly once, or cannot_draft if the sentence is not one checkable rule.

Attributes and operators:
{attrs}

Guidance:
- Allergens: 'allergens' counts 'contains' and 'may contain'. Use it for 'not even traces' and by default.
  Use 'allergens_contains' only if the buyer says traces or 'may contain' are fine. Op: not_contains.
  Allergen values in English: peanuts, tree nuts, milk, egg, gluten, wheat, soy, fish, crustaceans, molluscs,
  sesame, celery, mustard, lupin, sulphites.
- country is lower-case ISO alpha-2 ('fi').
- Labels and claims: use a value from 'Values seen'.
- If the rule is only for some products, set product_type to a plain English word; do not invent category codes.
- Number attributes (delivery_reliability 0 to 1, co2_kg_per_kg): use only a threshold the buyer states.
  "Low CO2" or "reliable" without a number: cannot_draft and ask for the number.
- Do not combine several rules. If the sentence has several, cannot_draft and say which ones to add one by one."""


async def llm_draft(provider, text: str, kind: str, catalog: dict) -> dict:
    turn = await provider.complete(_system(catalog, kind), [{"role": "user", "text": text}],
                                   [DRAFT_TOOL, CANNOT_TOOL])
    call = turn.tool_calls[0] if turn.tool_calls else None
    if call is None:
        raise DraftError(turn.text or "The model did not return a rule. Please use the form.")
    if call.name == "cannot_draft":
        raise DraftError(call.args.get("reason") or "This is not one rule. Please use the form.")
    return {k: v for k, v in call.args.items() if k in DRAFT_TOOL.schema["properties"] and v not in (None, "")}


# --------------------------------------------------------------------------- label and explanation

def _label(r: dict, product_type: str | None) -> str | None:
    a, op, v = r["attr"], r["op"], r["value"]
    if a == "allergens" and op == "not_contains":
        return f"No {v}, incl. may contain"
    if a == "allergens_contains" and op == "not_contains":
        return f"No {v} (may contain is fine)"
    if a == "country" and op == "eq":
        nat = COUNTRY_NAMES.get(v, f"origin {str(v).upper()}")
        return f"Only {nat} {product_type}" if product_type else f"Only {nat} products"
    return None  # Valio's own label


def explain(r: dict, kind: str, product_type: str | None) -> str:
    """Plain words for what the validated rule does. Built from the rule, never from the model."""
    a, op, v = r["attr"], r["op"], r["value"]
    scope = f"Applies to {product_type} only. " if product_type else ""
    if kind == "prefer":
        return (f"{scope}Products where {r['label']} rank higher (weight {r.get('weight', 1)}). "
                "Others can still be bought, but the agent tells you.")
    unknown = ("If the data is missing, the product is not bought." if r.get("if_unknown", "exclude") == "exclude"
               else "If the data is missing, the agent asks you.")
    if a == "allergens" and op == "not_contains":
        what = f"Products whose label says they contain or may contain {v} are never bought."
    elif a == "allergens_contains" and op == "not_contains":
        what = f"Products that contain {v} are never bought. 'May contain {v}' is allowed."
    elif a == "country" and op == "eq":
        what = f"Only products with country of origin {str(v).upper()} are bought."
    else:
        what = f"Only products where {r['label']} are bought."
    return f"{scope}{what} {unknown}"


# --------------------------------------------------------------------------- routes

class DraftRequest(BaseModel):
    text: str
    kind: Literal["must", "prefer"] = "must"
    buyer_id: str | None = None


_catalog_cache: dict | None = None


async def _policy_catalog() -> dict:
    global _catalog_cache
    if _catalog_cache is None:
        _catalog_cache = _ok(await _valio("GET", "/internal/policy-catalog"))
    return _catalog_cache


async def _scope(product_type: str) -> list[dict]:
    """The categories a product word stands for, with example names so the buyer can see what is meant."""
    cats = _ok(await _valio("GET", "/internal/categories", params={"q": product_type}))
    if not cats:
        raise DraftError(f"I found no products for '{product_type}'. Please use the form.")
    return cats


async def _validate(bid: str, rule: dict, kind: str) -> dict:
    """Add the rule to a copy of the current mandate and let Valio preview it. Returns the normalised rule."""
    cur = _ok(await _valio("GET", f"/internal/mandate/{bid}"))
    key = "mandatory_rules" if kind == "must" else "preferences"
    copy = {k: cur.get(k) for k in ("display_name", "currency", "optimise", "mandatory_rules", "preferences",
                                    "autonomy")}
    copy[key] = list(copy.get(key) or []) + [rule | {"id": DRAFT_ID}]
    r = await _valio("POST", f"/internal/mandate/{bid}/preview", json={"mandate": copy})
    if r.status_code == 422:
        raise DraftError(f"This rule is not valid: {(_json(r) or {}).get('error', r.text)}")
    pre = _ok(r)
    out = next(x for x in pre[key] if x["id"] == DRAFT_ID)
    keep = ("attr", "op", "value", "label", "applies_to") + (("if_unknown",) if kind == "must" else ("weight",))
    return {k: out[k] for k in keep if k in out and (k != "applies_to" or out[k])}


@router.post("/mandate/draft")
async def mandate_draft(body: DraftRequest):
    """A sentence -> one rule, validated by Valio, with its coverage. Nothing is saved."""
    text = body.text.strip()
    if not text:
        raise HTTPException(422, {"error": "Please describe the rule."})
    bid = body.buyer_id or config.DEFAULT_BUYER_ID
    provider = make_provider()
    try:
        if provider.name == "scripted":
            d = scripted_draft(text)
        else:
            try:
                d = await llm_draft(provider, text, body.kind, await _policy_catalog())
            except DraftError:
                raise
            except Exception as e:  # model down: the regexes still cover the common sentences
                log.warning("rule draft via %s failed (%s), trying the scripted parser", provider.name, e)
                d = scripted_draft(text)
        product_type = d.pop("product_type", None)
        rule = {k: d[k] for k in ("attr", "op", "value") if k in d}
        if body.kind == "must":
            rule["if_unknown"] = d.get("if_unknown") or "exclude"
        else:
            rule["weight"] = int(d.get("weight") or 2)
        scope = await _scope(product_type) if product_type else None
        if scope:
            rule["applies_to"] = [{"attr": "category", "op": "in", "value": [c["category"] for c in scope]}]
        if len(rule) < 3 or "attr" not in rule:
            raise DraftError("The rule is incomplete. Please use the form.")
        rule = await _validate(bid, rule, body.kind)
        label = _label(rule, product_type)
        if label:
            rule["label"] = label
    except DraftError as e:
        raise HTTPException(422, {"error": str(e)}) from None
    cov = await _valio("POST", "/internal/rule-coverage", json={"rule": rule})
    return {"kind": body.kind, "rule": rule, "label": rule["label"],
            "explanation": explain(rule, body.kind, product_type), "product_type": product_type,
            "scope": scope, "coverage": cov.json() if cov.status_code == 200 else None, "provider": provider.name}


class CoverageRequest(BaseModel):
    rule: dict | None = None
    category: str | None = None
    buyer_id: str | None = None


@router.post("/mandate/coverage")
async def mandate_coverage(body: CoverageRequest):
    """With a rule: its coverage. Without: coverage for every must of the current mandate."""
    if body.rule is not None:
        return _ok(await _valio("POST", "/internal/rule-coverage",
                                json={"rule": body.rule, "category": body.category}))
    m = _ok(await _valio("GET", f"/internal/mandate/{body.buyer_id or config.DEFAULT_BUYER_ID}"))
    out = []
    for r in m.get("mandatory_rules") or []:
        cov = _ok(await _valio("POST", "/internal/rule-coverage", json={"rule": r, "category": body.category}))
        out.append({"id": r.get("id"), "label": r.get("label"), "enabled": r.get("enabled", True), "coverage": cov})
    return {"mandate_id": m.get("mandate_id"), "musts": out}

