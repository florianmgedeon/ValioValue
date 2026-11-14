"""Reading documents into kitchen suggestions (package 1b).

One document = one LLM call that returns JSON only. Every suggestion quotes the line it came from (W1) and is
validated before it is saved: unknown kinds are dropped, musts and preferences must pass Valio Aimo's mandate
preview, usual products are resolved against the catalog. Photos sent here never order anything (W2).
Offline (scripted provider): fixed answers for the demo fixtures in contracts/fixtures/uploads/.
"""
from __future__ import annotations

import copy
import json
import logging
import re

import httpx

from . import config, kitchen, valio
from .llm import AnthropicProvider, OpenAICompatibleProvider, anthropic_blocks, make_provider, openai_parts

log = logging.getLogger("buyer_agent.ingest")

DOC_TYPES = ("menu", "allergy_note", "invoice", "order_list", "sustainability_report", "other")
ORDER_LIST_HINT = "This looks like an order list. Send it in the Order tab to order it."

EXTRACT_SYSTEM = """You read documents from a professional kitchen (photos of menus, notes on the fridge, invoices, reports) and propose what a purchasing agent should remember about this kitchen. You never order anything.

Answer with JSON only, no prose, exactly this shape:
{"document_type": "menu | allergy_note | invoice | order_list | sustainability_report | other",
 "title": "short title, e.g. Lunch menu, week 47",
 "suggestions": [{"kind": "about | usual | must | prefer | autonomy", "title": "one plain sentence", "quote": "verbatim text from the document", "payload": {...}}]}

Kinds and payloads (examples):
- about: who the kitchen is. {"fact": "420 lunches / day"} or {"text": "Primary school kitchen in Espoo."}
  e.g. title "Primary school, about 420 lunches a day, Mon–Fri", quote "420 lunches / day"
- usual: what the kitchen's word means as a product. {"word": "milk", "aliases": ["maito"], "product_query": "Valio Eila kevytmaitojuoma 10 l novobox", "typical_qty": 120, "unit": "L", "per": "week"}
  unit is L, KG or SALES_UNIT. product_query = the product name as written on the document (invoice line).
- must: a rule nothing may break. {"attr": "allergens", "op": "not_contains", "value": "peanuts", "if_unknown": "exclude", "label": "No peanuts incl. may contain"}
- prefer: what is better, weight 1 to 3. {"attr": "labels", "op": "contains", "value": "HYVAA_SUOMESTA", "weight": 3}
- autonomy: when the agent may order alone. {"scope": "usual", "limits": {"max_order_total": 600}, "accepts": [{"check": "quantity", "max_over_pct": 5}]}
  scope "usual" means: only the kitchen's usual products.

Allowed attributes and operators for must and prefer (nothing else):
{attributes}

Rules:
- quote is copied verbatim from the document. Never invent anything that is not written there.
- Prefer few, sure suggestions over many vague ones.
- Allergies are always a must with "if_unknown": "exclude", one must per allergen.
- Autonomy only as a proposal derived from order sizes on invoices: limits.max_order_total = the average invoice rounded up to the next 100.
- Skip what the kitchen already knows (below).
- An order list (lines like "milk 120 L") is document_type order_list: propose usual products at most, never an order.

What is already known:
{known}"""


# --------------------------------------------------------------------------- product resolution (shared with PATCH and accept)

def _tokens(s: str) -> set[str]:
    return {t for t in re.findall(r"[0-9a-zäöå]+", (s or "").lower().replace("®", " ")) if t}


def overlap(query: str, name: str) -> float:
    q = _tokens(query)
    return len(q & _tokens(name)) / len(q) if q else 0.0


async def search_products(buyer_id: str, query: str, limit: int = 5) -> list[dict]:
    """Plain catalog search through Valio Aimo's MCP tool (no intent)."""
    from .agent import Session, mcp_session, tool_payload
    async with mcp_session(Session(f"kitchen-{buyer_id}", buyer_id)) as mcp:
        res = tool_payload(await mcp.call_tool("search_products", {"query": query, "limit": limit}))
    return res.get("products") or []


async def resolve_usual(buyer_id: str, payload: dict) -> dict:
    """Fill product_id and product_name. Top hit if its name matches closely (token overlap ≥ 0.6), else
    product_id None and up to 3 candidates for the UI to choose."""
    out = dict(payload)
    if out.get("product_id") and out.get("product_name"):
        return out
    query = out.get("product_query") or out.get("product_name") or out.get("word") or ""
    try:
        hits = await search_products(buyer_id, query, 5)
    except Exception:  # noqa: BLE001 - keep the suggestion, the buyer chooses later
        log.exception("product search failed for %r", query)
        hits = []
    if out.get("product_id"):
        hit = next((h for h in hits if h["product_id"] == out["product_id"]), None)
        out["product_name"] = out.get("product_name") or (hit or {}).get("name")
        return out
    best = hits[0] if hits else None
    if best and overlap(query, best["name"]) >= 0.6:
        out |= {"product_id": best["product_id"], "product_name": best["name"]}
        out.pop("candidates", None)
    else:
        out |= {"product_id": None, "product_name": None,
                "candidates": [{"product_id": h["product_id"], "name": h["name"]} for h in hits[:3]]}
    return out


# --------------------------------------------------------------------------- building a new mandate from suggestions

def _scope(scope, usual: list[str]) -> list[dict]:
    if scope in ("usual", "usual_products"):
        return [{"attr": "product_id", "op": "in", "value": usual}] if usual else []
    if isinstance(scope, dict):
        return [scope]
    return list(scope or [])


def _same_rule(a: dict, b: dict) -> bool:
    return (a.get("attr"), a.get("op"), str(a.get("value")).lower()) == (b.get("attr"), b.get("op"), str(b.get("value")).lower())


def mandate_with(current: dict, suggestions: list[dict], profile: dict, usual: list[str] | None = None
                 ) -> tuple[dict, list[str]]:
    """Current mandate + the must / prefer / autonomy suggestions. Returns (new mandate, diff sentences)."""
    new = copy.deepcopy(valio.editable(current))
    new.setdefault("mandatory_rules", [])
    new.setdefault("preferences", [])
    new.setdefault("autonomy", {"grants": []}).setdefault("grants", [])
    usual = usual if usual is not None else kitchen.usual_ids(profile)
    diff: list[str] = []
    for s in suggestions:
        src = (kitchen.source(profile, s.get("source_id")) or {}).get("title") or "kitchen profile"
        p = dict(s.get("payload") or {})
        if s["kind"] in ("must", "prefer"):
            key = "mandatory_rules" if s["kind"] == "must" else "preferences"
            values = p["value"] if isinstance(p.get("value"), list) and p.get("op") in ("contains", "not_contains") else [p.get("value")]
            added = 0
            for v in values:
                r = {k: x for k, x in p.items() if k in ("attr", "op", "if_unknown", "applies_to", "label", "weight", "rule",
                                                         "allergen", "strictness", "country")}
                r["value"] = v
                if len(values) > 1:
                    r.pop("label", None)
                elif not r.get("label"):
                    r["label"] = s.get("title")
                if s["kind"] == "prefer":
                    r["weight"] = int(p.get("weight") or 1)
                    r.pop("if_unknown", None)
                if any(_same_rule(r, o) for o in new[key]):
                    continue
                r |= {"id": valio.fresh_id(new, "m" if s["kind"] == "must" else "p"), "source": src}
                new[key].append(r)
                added += 1
            what = "Must" if s["kind"] == "must" else "Preference"
            diff.append(f"New {what.lower()}: {s['title']} (from {src})" if added
                        else f"{what} you already have: {s['title']}")
        elif s["kind"] == "autonomy":
            g = {"id": valio.fresh_id(new, "g"), "label": p.get("label") or s.get("title"),
                 "scope": _scope(p.get("scope"), usual), "limits": p.get("limits") or {},
                 "accepts": p.get("accepts") or [], "source": src}
            new["autonomy"]["grants"].append(g)
            diff.append(f"May order alone: {g['label']} (from {src})")
    return new, diff


# --------------------------------------------------------------------------- the LLM call

def _json(text: str) -> dict:
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    start, end = t.find("{"), t.rfind("}")
    return json.loads(t[start:end + 1]) if start >= 0 else {}


async def _llm(provider, system: str, upload_ids: list[str], text: str | None) -> dict:
    """One extraction call with the document attached. TODO(dedupe with 1a): reuses llm.py's block builders."""
    ask = "Read this document and answer with the JSON." + (f"\n\nDocument text:\n{text}" if text else "")
    if isinstance(provider, AnthropicProvider):
        body = {"model": provider.model, "max_tokens": 4000, "system": system,
                "messages": [{"role": "user", "content": anthropic_blocks(upload_ids) + [{"type": "text", "text": ask}]}]}
        async with httpx.AsyncClient(timeout=180) as c:
            r = await c.post(provider.url, json=body, headers={"x-api-key": provider.api_key,
                                                                "anthropic-version": "2023-06-01"})
        if r.status_code != 200:
            raise RuntimeError(f"Anthropic HTTP {r.status_code}: {r.text[:300]}")
        return _json("".join(b.get("text", "") for b in r.json()["content"] if b["type"] == "text"))
    if isinstance(provider, OpenAICompatibleProvider):
        body = {"model": provider.model, "response_format": {"type": "json_object"},
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": openai_parts(upload_ids) + [{"type": "text", "text": ask}]}]}
        async with httpx.AsyncClient(timeout=180) as c:
            r = await c.post(provider.url, json=body, headers={"Authorization": f"Bearer {provider.api_key}"})
        if r.status_code != 200:
            raise RuntimeError(f"{provider.name} HTTP {r.status_code}: {r.text[:300]}")
        return _json(r.json()["choices"][0]["message"].get("content") or "")
    raise RuntimeError("no extraction for this provider")


def _fixture_answer(meta: dict | None, text: str | None) -> dict:
    """Offline answers: the demo fixtures carry their extraction; typed text becomes an About suggestion."""
    if meta and meta.get("fixture"):
        f = config.FIXTURES_DIR / "uploads" / f"{meta['fixture']}.json"
        if f.exists():
            data = json.loads(f.read_text(encoding="utf-8"))
            if data.get("extraction"):
                return data["extraction"]
    if text and text.strip():
        t = text.strip()
        out = [{"kind": "about", "title": t.rstrip("."), "quote": t, "payload": {"text": t}}]
        for allergen, word in (("peanuts", "nut"), ("tree nuts", "nut"), ("sesame", "sesame")):
            if re.search(rf"\b{word}[- ]free\b|\bno {word}s?\b", t, re.I):
                quote = re.search(rf"[^.]*\b{word}[^.]*", t, re.I).group(0).strip()
                out.append({"kind": "must", "title": f"No {allergen}, including \"may contain\"", "quote": quote,
                            "payload": {"attr": "allergens", "op": "not_contains", "value": allergen,
                                        "if_unknown": "exclude"}})
        return {"document_type": "other", "title": t[:40], "suggestions": out}
    return {"document_type": "other", "title": (meta or {}).get("filename") or "Document", "suggestions": [],
            "note": "I cannot read this document offline. Type what matters, or connect a model."}


def _known(profile: dict, mandate: dict) -> str:
    return (kitchen.prompt_block(profile["buyer_id"]) or "nothing yet") + "\n" + (mandate.get("summary") or "")


def _attributes(cat: dict) -> str:
    return "\n".join(f"- {a['attr']} ({a['type']}): {', '.join(a['ops'])}" + (
        f"; e.g. {', '.join(str(x) for x in a['examples'][:8])}" if a.get("examples") else "")
        for a in cat.get("attributes") or [] if a["source"] == "product")


# --------------------------------------------------------------------------- validation

def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip(" .!\"'“”")


async def validate(buyer_id: str, raw: list[dict], profile: dict, mandate: dict, doc_text: str | None) -> list[dict]:
    out = []
    for s in raw or []:
        kind = s.get("kind")
        if kind == "auto":
            kind = "autonomy"
        if kind not in kitchen.KINDS or not isinstance(s.get("payload"), dict):
            log.info("dropped suggestion with unknown kind %r", kind)
            continue
        s = {"kind": kind, "title": str(s.get("title") or kind), "quote": str(s.get("quote") or ""),
             "payload": dict(s["payload"])}
        if doc_text and s["quote"] and _norm(s["quote"]) not in _norm(doc_text):
            log.info("dropped suggestion, quote not in the text: %r", s["quote"])
            continue
        if kind == "about" and not (s["payload"].get("text") or s["payload"].get("fact")):
            continue
        if kind == "usual":
            if not s["payload"].get("word"):
                continue
            s["payload"] = await resolve_usual(buyer_id, s["payload"])
        if kind in kitchen.SIGNED_KINDS:
            new, _ = mandate_with(mandate, [s | {"source_id": None}], profile)
            _, err = await valio.preview(buyer_id, new)
            if err:
                log.warning("dropped %s suggestion %r: %s", kind, s["title"], err)
                continue
        out.append(s)
    return out


# --------------------------------------------------------------------------- one document

async def ingest_one(buyer_id: str, *, upload_id: str | None = None, text: str | None = None,
                     title: str | None = None, by: str = "kitchen agent") -> dict:
    from . import uploads
    meta = uploads.meta(upload_id) if upload_id else None
    stype = "text" if not meta else "pdf" if meta["media_type"] == "application/pdf" else "photo"
    profile = kitchen.load(buyer_id)
    mandate = await valio.mandate(buyer_id)
    provider = make_provider()
    note = None
    if provider.name == "scripted":
        ans = _fixture_answer(meta, text)
        note = ans.get("note")
    else:
        cat = valio.passthrough(await valio.call("GET", "/internal/policy-catalog"))
        system = EXTRACT_SYSTEM.replace("{attributes}", _attributes(cat)).replace("{known}", _known(profile, mandate))
        try:
            ans = await _llm(provider, system, [upload_id] if upload_id else [], text)
        except (RuntimeError, ValueError, KeyError, httpx.HTTPError) as e:
            log.exception("extraction failed")
            ans = {"document_type": "other", "suggestions": []}
            note = f"I could not read this document: {e}"
    doc_type = ans.get("document_type") if ans.get("document_type") in DOC_TYPES else "other"
    suggestions = await validate(buyer_id, ans.get("suggestions") or [], profile, mandate,
                                 text if stype == "text" else None)
    src = {"type": stype, "title": title or ans.get("title") or (meta or {}).get("filename") or "Typed note",
           "upload_id": upload_id, "document_type": doc_type}
    if stype == "text":
        src["text"] = text
    saved = kitchen.add_suggestions(buyer_id, suggestions, src, by=by)
    out = {"source": src, "document_type": doc_type, "suggestions": saved}
    if doc_type == "order_list":
        out["hint"] = ORDER_LIST_HINT
    if note:
        out["note"] = note
    return out
