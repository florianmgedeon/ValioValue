"""Swappable LLM providers behind one small interface.

Conversation history is provider-neutral:
  {"role": "user", "text": str, "attachments": [upload_id, ...]}   (attachments optional, package 1a)
  {"role": "assistant", "text": str | None, "tool_calls": [{"id", "name", "args"}]}
  {"role": "tool", "tool_call_id": str, "name": str, "content": str}

Adapters talk plain HTTP (no vendor SDK), so switching is one env var.
"""
from __future__ import annotations

import base64
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Protocol

import httpx

from . import config

log = logging.getLogger("buyer_agent.llm")


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict[str, Any]


@dataclass
class AssistantTurn:
    text: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    reasoning: str | None = None          # model's own (summarized) thinking, if the provider exposes it
    raw: list[dict] | None = None         # provider blocks to replay verbatim (Anthropic thinking signatures)
    usage: dict[str, Any] | None = None
    model: str | None = None


@dataclass
class ToolSpec:
    name: str
    description: str
    schema: dict[str, Any]


class LLMProvider(Protocol):
    name: str

    async def complete(self, system: str, history: list[dict], tools: list[ToolSpec]) -> AssistantTurn: ...


# --------------------------------------------------------------------------- schema helpers

def simplify_schema(schema: dict) -> dict:
    """Inline $refs and collapse Optional anyOf, so every provider (Gemini included) accepts it."""
    defs = schema.get("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(defs[node["$ref"].split("/")[-1]])
            if "anyOf" in node:
                options = [o for o in node["anyOf"] if o.get("type") != "null"]
                if len(options) == 1:
                    merged = {k: v for k, v in node.items() if k != "anyOf"} | walk(options[0])
                    return {k: v for k, v in merged.items() if k != "title" and not (k == "default" and v is None)}
            return {k: walk(v) for k, v in node.items() if k not in ("$defs", "title")}
        if isinstance(node, list):
            return [walk(n) for n in node]
        return node

    return walk(schema)


# --------------------------------------------------------------------------- attachments (photos, PDFs)

def _latest_user(history: list[dict]) -> int:
    return max((i for i, m in enumerate(history) if m["role"] == "user"), default=-1)


def _load(upload_id: str) -> tuple[dict, bytes] | None:
    from . import uploads
    try:
        return uploads.load(upload_id)
    except (FileNotFoundError, KeyError, ValueError):
        log.warning("attachment %s not found", upload_id)
        return None


def stand_in(upload_ids: list[str]) -> str:
    """Short text for photos sent on an earlier turn, to save tokens."""
    return " ".join(f"[{'PDF' if (_load(u) or ({},))[0].get('media_type') == 'application/pdf' else 'photo'} "
                    f"{u} sent earlier]" for u in upload_ids)


def anthropic_blocks(upload_ids: list[str]) -> list[dict]:
    """Anthropic content blocks for uploads: `image` for photos, `document` for PDFs (base64)."""
    out = []
    for u in upload_ids:
        got = _load(u)
        if not got:
            out.append({"type": "text", "text": f"[attachment {u} could not be loaded]"})
            continue
        meta, data = got
        src = {"type": "base64", "media_type": meta["media_type"], "data": base64.b64encode(data).decode()}
        out.append({"type": "document" if meta["media_type"] == "application/pdf" else "image", "source": src})
    return out


def openai_parts(upload_ids: list[str]) -> list[dict]:
    """OpenAI-compatible content parts: `image_url` data URLs. PDFs are not supported by every endpoint,
    so they become a text note."""
    out = []
    for u in upload_ids:
        got = _load(u)
        if not got:
            out.append({"type": "text", "text": f"[attachment {u} could not be loaded]"})
            continue
        meta, data = got
        if meta["media_type"] == "application/pdf":
            log.warning("PDF %s not sent to an OpenAI-compatible model", u)
            out.append({"type": "text", "text": f"[PDF {meta.get('filename') or u} attached, but this model cannot "
                                                f"read PDFs. Ask the buyer to send a photo instead.]"})
            continue
        url = f"data:{meta['media_type']};base64,{base64.b64encode(data).decode()}"
        out.append({"type": "image_url", "image_url": {"url": url}})
    return out


def _user_text(m: dict, latest: bool) -> str:
    text = m.get("text") or ""
    if m.get("attachments") and not latest:
        text = (text + " " + stand_in(m["attachments"])).strip()
    return text or ("(photo)" if m.get("attachments") else "")


# --------------------------------------------------------------------------- Anthropic

class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, model: str, base_url: str = ""):
        self.api_key, self.model = api_key, model or "claude-sonnet-4-5"
        self.url = (base_url or "https://api.anthropic.com").rstrip("/") + "/v1/messages"

    @staticmethod
    def _messages(history: list[dict]) -> list[dict]:
        out: list[dict] = []
        latest = _latest_user(history)
        for n, m in enumerate(history):
            if m["role"] == "user":
                # Photos only on the turn they were sent; later turns get a short stand-in.
                blocks = anthropic_blocks(m["attachments"]) if m.get("attachments") and n == latest else []
                blocks.append({"type": "text", "text": _user_text(m, n == latest)})
                role = "user"
            elif m["role"] == "assistant":
                if m.get("raw"):
                    # Thinking blocks are signed against the system prompt they were made with. The prompt is rebuilt
                    # every turn (time, mandate, kitchen profile), so only this turn's thinking may be sent back.
                    blocks = [b for b in m["raw"] if n > latest or b["type"] not in ("thinking", "redacted_thinking")]
                else:
                    blocks = ([{"type": "text", "text": m["text"]}] if m.get("text") else []) + [
                        {"type": "tool_use", "id": c["id"], "name": c["name"], "input": c["args"]}
                        for c in m.get("tool_calls", [])]
                role = "assistant"
            else:
                blocks = [{"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"]}]
                role = "user"
            if not blocks:
                continue
            if out and out[-1]["role"] == role:
                out[-1]["content"].extend(blocks)
            else:
                out.append({"role": role, "content": blocks})
        return out

    thinking = config.LLM_THINKING

    async def _post(self, body: dict) -> httpx.Response:
        async with httpx.AsyncClient(timeout=180) as c:
            return await c.post(self.url, json=body, headers={
                "x-api-key": self.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"})

    async def complete(self, system, history, tools):
        body: dict[str, Any] = {
            "model": self.model, "max_tokens": 8000, "system": system, "messages": self._messages(history),
            "tools": [{"name": t.name, "description": t.description, "input_schema": t.schema} for t in tools]}
        if self.thinking:
            body |= {"thinking": {"type": "adaptive", "display": "summarized"},
                     "output_config": {"effort": config.LLM_EFFORT}}
        r = await self._post(body)
        if r.status_code == 400 and self.thinking and "thinking" in r.text:
            # model without adaptive thinking: keep going without reasoning display
            self.thinking = False
            body.pop("thinking", None), body.pop("output_config", None)
            r = await self._post(body)
        if r.status_code != 200:
            raise RuntimeError(f"Anthropic HTTP {r.status_code}: {r.text[:400]}")
        data = r.json()
        content = data["content"]
        text = "\n".join(b["text"] for b in content if b["type"] == "text").strip() or None
        calls = [ToolCall(b["id"], b["name"], b.get("input") or {}) for b in content if b["type"] == "tool_use"]
        reasoning = "\n".join(b.get("thinking", "") for b in content if b["type"] == "thinking").strip() or None
        keep = {"thinking": ("type", "thinking", "signature"), "redacted_thinking": ("type", "data"),
                "text": ("type", "text"), "tool_use": ("type", "id", "name", "input")}
        raw = [{k: b[k] for k in keep[b["type"]] if k in b} for b in content if b["type"] in keep]
        return AssistantTurn(text, calls, reasoning=reasoning, raw=raw, usage=data.get("usage"),
                             model=data.get("model", self.model))


# --------------------------------------------------------------------------- OpenAI compatible (OpenAI, Gemini, OpenRouter, Mistral, local)

class OpenAICompatibleProvider:
    def __init__(self, api_key: str, model: str, base_url: str, name: str = "openai_compatible"):
        self.api_key, self.model, self.name = api_key, model, name
        self.url = base_url.rstrip("/") + "/chat/completions"

    @staticmethod
    def _messages(system: str, history: list[dict]) -> list[dict]:
        out: list[dict] = [{"role": "system", "content": system}]
        latest = _latest_user(history)
        for n, m in enumerate(history):
            if m["role"] == "user":
                if m.get("attachments") and n == latest:
                    out.append({"role": "user", "content": openai_parts(m["attachments"])
                                + [{"type": "text", "text": _user_text(m, True)}]})
                else:
                    out.append({"role": "user", "content": _user_text(m, n == latest)})
            elif m["role"] == "assistant":
                msg: dict[str, Any] = {"role": "assistant", "content": m.get("text") or ""}
                if m.get("tool_calls"):
                    msg["tool_calls"] = [{"id": c["id"], "type": "function",
                                          "function": {"name": c["name"], "arguments": json.dumps(c["args"])}}
                                         for c in m["tool_calls"]]
                out.append(msg)
            else:
                out.append({"role": "tool", "tool_call_id": m["tool_call_id"], "content": m["content"]})
        return out

    async def complete(self, system, history, tools):
        body = {"model": self.model, "messages": self._messages(system, history),
                "tools": [{"type": "function", "function": {"name": t.name, "description": t.description,
                                                            "parameters": t.schema}} for t in tools]}
        async with httpx.AsyncClient(timeout=120) as c:
            r = await c.post(self.url, json=body, headers={"Authorization": f"Bearer {self.api_key}"})
        if r.status_code != 200:
            raise RuntimeError(f"{self.name} HTTP {r.status_code}: {r.text[:400]}")
        msg = r.json()["choices"][0]["message"]
        calls = []
        for tc in msg.get("tool_calls") or []:
            args = tc["function"].get("arguments") or "{}"
            calls.append(ToolCall(tc.get("id") or f"call_{uuid.uuid4().hex[:8]}", tc["function"]["name"],
                                  json.loads(args) if isinstance(args, str) else args))
        data = r.json()
        return AssistantTurn((msg.get("content") or "").strip() or None, calls,
                             reasoning=(msg.get("reasoning_content") or msg.get("reasoning") or None),
                             usage=data.get("usage"), model=data.get("model", self.model))


# --------------------------------------------------------------------------- Scripted fallback (no key needed)

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def parse_request(text: str, today: date) -> dict:
    """Very small parser for demo requests like '1,000 litres of lactose-free milk by Monday'."""
    t = text.lower()
    qty, unit = None, "SALES_UNIT"
    m = re.search(r"(\d[\d,.\s]*)\s*(litres?|liters?|l|kg|kilos?|kilograms?)\b", t)
    if m:
        qty = float(re.sub(r"[,\s]", "", m.group(1)))
        unit = "KG" if m.group(2).startswith("k") else "L"
    else:
        m = re.search(r"(\d[\d,]*)\s*(?:x|pcs|units|packs?)?\b", t)
        qty = float(m.group(1).replace(",", "")) if m else 1.0
    when = today + timedelta(days=2)
    if (d := re.search(r"(\d{4}-\d{2}-\d{2})", t)):
        when = date.fromisoformat(d.group(1))
    elif "tomorrow" in t:
        when = today + timedelta(days=1)
    else:
        for i, wd in enumerate(WEEKDAYS):
            if wd in t:
                delta = (i - today.weekday()) % 7 or 7
                when = today + timedelta(days=delta)
                break
    what = t
    if m := re.search(r"\b(?:of)\s+(.+?)(?:\s+(?:by|for|before|on|until|delivered)\b|[.,!?]|$)", t):
        what = m.group(1)
    return {"query": what.strip(), "quantity": qty, "unit": unit, "date": when.isoformat()}


UNIT_MAP = {"l": ("L", 1), "litre": ("L", 1), "litres": ("L", 1), "liter": ("L", 1), "liters": ("L", 1),
            "dl": ("L", 0.1), "cl": ("L", 0.01), "ml": ("L", 0.001), "kg": ("KG", 1), "kilo": ("KG", 1),
            "kilos": ("KG", 1), "g": ("KG", 0.001), "grams": ("KG", 0.001), "gram": ("KG", 0.001),
            "pcs": ("SALES_UNIT", 1), "pieces": ("SALES_UNIT", 1), "kpl": ("SALES_UNIT", 1), "packs": ("SALES_UNIT", 1),
            "pack": ("SALES_UNIT", 1)}
ITEM_RE = re.compile(r"(\d[\d,.\s]*?)\s*(litres|liters|litre|liter|kilos|kilo|grams|gram|pieces|packs|pack|pcs|kpl|kg|dl|cl|ml|l|g)\b\.?\s*(?:of\s+)?([^,;\n]+)",
                     re.I)


WORD_FIRST_RE = re.compile(r"^\s*[-*•]?\s*([^\W\d][^\d\n]*?)\s+(\d+(?:[.,]\d+)?)\s*(litres|liters|litre|liter|kilos|kilo|"
                           r"grams|gram|pieces|packs|pack|pcs|kpl|kg|dl|cl|ml|l|g)?\.?\s*$", re.I)


def parse_items(text: str) -> tuple[list[dict], float]:
    """Recipe or list -> items. 'serves 4' ... 'for 40 portions' scales. Good enough for the offline demo,
    a real LLM does this far better."""
    t = text.lower()
    base = re.search(r"serves\s+(\d+)|recipe for\s+(\d+)|for\s+(\d+)\s+(?:people|persons|servings)", t)
    target = re.search(r"(?:need|cook|make|for)\s+(\d+)\s+(?:portions|guests|covers|pax)", t)
    factor = 1.0
    if base and target:
        factor = float(target.group(1)) / float(next(g for g in base.groups() if g))
    items = []
    for chunk in re.split(r"\n|;|,(?![\d])|\band\b", text):
        m = ITEM_RE.search(chunk)
        if m and not m.group(3).strip(" .-*"):
            m = None
        if not m:
            # a written list: "milk 120 L", "rye rolls 300"
            w = WORD_FIRST_RE.match(chunk)
            if w:
                raw = w.group(2).replace(",", ".")
                unit, mult = UNIT_MAP.get((w.group(3) or "pcs").lower().rstrip("."), ("SALES_UNIT", 1))
                items.append({"product_query": w.group(1).strip(), "quantity": round(float(raw) * mult * factor, 3),
                              "quantity_unit": unit})
            continue
        raw = re.sub(r"\s", "", m.group(1))
        raw = raw.replace(",", "") if re.fullmatch(r"\d{1,3}(,\d{3})+", raw) else raw.replace(",", ".")
        try:
            qty = float(raw)
        except ValueError:
            continue
        unit, mult = UNIT_MAP[m.group(2).lower()]
        name = re.split(r"\s+(?:by|before|until|delivered|for)\b", m.group(3).strip(" .-*"))[0].strip()
        if not name:
            continue
        amount = round(qty * mult * factor, 3)
        items.append({"product_query": name, "quantity": amount, "quantity_unit": unit,
                      **({"note": f"{qty:g} {m.group(2)} x {factor:g}"} if factor != 1 else {})})
    return items, factor


USUAL_LINE = re.compile(
    r'^- "([^"]+)"(?: \(also: ([^)]*)\))? → (.+?) \(product ([\w-]+)\)'
    r'(?:, usually ([\d.]+) (L|kg|units) a (\w+))?(?:; (.+))?$',
    re.M,
)
SUB_PROD_RE = re.compile(r"\(product ([\w-]+)\)")


def usual_from_prompt(system: str) -> list[dict]:
    """The usual products from the kitchen block of the system prompt (written by kitchen.prompt_block)."""
    out = []
    for word, aliases, name, pid, qty, unit, per, tail in USUAL_LINE.findall(system or ""):
        sub_ids = SUB_PROD_RE.findall(tail or "")
        allow_other = "allow_other_substitutes=false" not in (tail or "")
        out.append({"word": word, "aliases": [a.strip() for a in aliases.split(",") if a.strip()], "name": name,
                    "product_id": pid, "typical_qty": float(qty) if qty else None,
                    "unit": {"L": "L", "kg": "KG", "units": "SALES_UNIT"}.get(unit), "per": per or None,
                    "substitute_product_ids": sub_ids, "allow_other_substitutes": allow_other})
    return out


def match_usual(query: str, usual: list[dict]) -> dict | None:
    q = query.lower().strip()
    for u in usual:
        for w in [u["word"], *u["aliases"]]:
            w = w.lower()
            if q in (w, w + "s", w + "es") or re.search(rf"\b{re.escape(w)}(s|es)?\b", q):
                return u
    return None


def apply_usual(items: list[dict], text: str, system: str) -> list[dict]:
    """Scripted stand-in for the 1c prompt rule: usual word -> preferred_product_id (+ substitutes/lock),
    'the usual' -> all usual items with usual amounts, 'double the X' doubles X."""
    usual = usual_from_prompt(system)
    if not usual:
        return items
    t = text.lower()
    if re.search(r"\bthe usual\b", t):
        items = [{"product_query": u["word"], "quantity": u["typical_qty"], "quantity_unit": u["unit"],
                  "note": f"your usual amount a {u['per'] or 'week'}"} for u in usual if u["typical_qty"] and u["unit"]]
        for m in re.finditer(r"\b(double|triple|half)\s+(?:the\s+)?([a-zäöå][a-zäöå ]*?)(?=[,.!?]|$|\s+and\b)", t):
            f = {"double": 2, "triple": 3, "half": 0.5}[m.group(1)]
            for it in items:
                if match_usual(m.group(2), [u for u in usual if u["word"] == it["product_query"]]):
                    it["quantity"] = round(it["quantity"] * f, 3)
                    it["note"] = f"{m.group(1)} your usual amount"
    for it in items:
        u = match_usual(it["product_query"], usual)
        if u:
            it["preferred_product_id"] = u["product_id"]
            if u.get("substitute_product_ids"):
                it["substitute_product_ids"] = list(u["substitute_product_ids"])
            if u.get("allow_other_substitutes") is False:
                it["allow_other_substitutes"] = False
    return items


def _attachment_metas(history: list[dict]) -> list[dict]:
    i = _latest_user(history)
    if i < 0:
        return []
    return [got[0] for got in (_load(u) for u in history[i].get("attachments") or []) if got]


ORDER_WORDS = re.compile(r"\border|\bbuy|\bneed|\bget\b|portions|\bfor\s+\d+|\bthis list\b|\bby\s+\w+day", re.I)
TEACHING = re.compile(r"^\s*(we['’]re|we are|our\b|from now on)", re.I)


def teaching_clause(text: str) -> str | None:
    """2a: the part of a message that tells the agent about the kitchen ("We're closed on Friday ..."), or None.
    Only the first clause counts, and only if it does not itself ask to order something."""
    if not TEACHING.match(text or ""):
        return None
    first = re.split(r"(?<=[.!?])\s+|,\s*(?=order|buy|get|and order)", text.strip(), maxsplit=1)[0].strip()
    return None if ORDER_WORDS.search(first) else first


class ScriptedProvider:
    """Deterministic planner without an LLM key:
    new request -> propose_intent and stop. Confirmed intent -> per item search + check_offer -> one place_order."""

    name = "scripted"

    def __init__(self, today: date):
        self.today = today

    async def complete(self, system, history, tools):
        users = [m for m in history if m["role"] == "user"]
        text = users[-1]["text"] if users else ""
        last_user = max(i for i, m in enumerate(history) if m["role"] == "user")
        tool_msgs = [(m["name"], json.loads(m["content"])) for m in history[last_user:] if m["role"] == "tool"]
        cid = f"call_{uuid.uuid4().hex[:8]}"
        photo_text = None
        metas = _attachment_metas(history)
        if metas:  # no vision here: demo fixtures carry their text in the upload meta
            known = [m for m in metas if m.get("text")]
            if not known:
                return AssistantTurn("I cannot read photos in offline mode. Please type the list, one item per line.")
            kinds = {m.get("document_type") or "order_list" for m in known}
            if "order_list" not in kinds and not ORDER_WORDS.search(text):
                # 2a: a context document without an order request becomes suggestions, never an order
                read = {r.get("source", {}).get("upload_id") for n, r in tool_msgs if n == "learn_from_document"}
                todo = [m for m in known if m.get("fixture") and m.get("upload_id") not in read]
                if todo:
                    return AssistantTurn(None, [ToolCall(cid, "learn_from_document", {"upload_id": todo[0]["upload_id"]})])
                results = [r for n, r in tool_msgs if n == "learn_from_document"]
                if results:
                    n = sum(len(r.get("suggestions") or []) for r in results)
                    title = (results[0].get("source") or {}).get("title") or "your document"
                    return AssistantTurn(f"I read **{title}** and found {n} thing{'s' * (n != 1)} to remember. "
                                         "Nothing is ordered: accept what is right below or in the Kitchen tab."
                                         if n else f"I read **{title}**, but found nothing new to remember.")
                what = {"menu": "a lunch menu", "allergy_note": "an allergy note", "invoice": "an invoice"}.get(
                    sorted(kinds)[0], "a document")
                return AssistantTurn(f"This looks like {what}, not an order. You can add it to your kitchen profile "
                                     f"from the Kitchen tab, then I will remember what it says.")
            photo_text = "\n".join(m["text"] for m in known)
        clause = None if metas or re.search(r"signed intent|Answers to your questions", text) else teaching_clause(text)
        if clause:  # 2a: the buyer teaches the agent something about the kitchen
            done = [r for n, r in tool_msgs if n == "remember_about_kitchen"]
            if not done:
                return AssistantTurn(None, [ToolCall(cid, "remember_about_kitchen", {
                    "kind": "about", "title": clause.rstrip("."), "payload": {"text": clause}, "quote": clause})])
            rest = text.strip()[len(clause):].strip(" ,.;")
            if not rest or not ORDER_WORDS.search(rest):
                if any(n == "propose_intent" for n, _ in tool_msgs):
                    return AssistantTurn(None)
                return AssistantTurn("Noted. I suggest adding this to your kitchen profile: accept it below. "
                                     "Nothing was ordered." if done[0].get("suggestions")
                                     else "I already know that about your kitchen. Nothing was ordered.")
            text = rest  # the order part goes on as a normal request
        confirmed = re.search(r"signed intent (int_\w+)", text)

        answers = re.match(r"Answers to your questions about intent (int_\w+)", text)
        if answers and not any(n == "propose_intent" for n, _ in tool_msgs):
            prev = next((c["args"] for m in reversed(history) if m["role"] == "assistant"
                         for c in m.get("tool_calls") or [] if c["name"] == "propose_intent"), None)
            if prev:
                said = [ln.split("→", 1)[-1].strip() for ln in text.splitlines()[1:] if "→" in ln]
                low = " ".join(said).lower()
                rules = [r for r in prev.get("hard_rules", [])]
                if "allerg" in low and not any(r.get("allergen") == "milk" for r in rules):
                    rules.append({"rule": "exclude_allergen", "allergen": "milk", "strictness": "contains_or_may_contain"})
                if "intoleran" in low or "lactose-free" in low:
                    rules.append({"rule": "lactose_free"})
                items = prev["items"]
                m = re.search(r"(\d[\d,.]*)\s*(l|litres?|kg)\b", low)
                if m and len(items) == 1:
                    items = [dict(items[0], quantity=float(m.group(1).replace(",", "")))]
                return AssistantTurn(None, [ToolCall(cid, "propose_intent", dict(
                    prev, items=items, hard_rules=rules, clarifications=said, replaces_intent_id=answers.group(1),
                    open_questions=[]))])
        if not confirmed:
            if any(n == "propose_intent" for n, _ in tool_msgs):
                return AssistantTurn(None)
            full = "\n".join(x for x in (text, photo_text) if x)
            req = parse_request(full, self.today)
            items, _ = parse_items(full)
            if photo_text:
                items = [dict(i, note="from your photo") for i in items]
                one_line = "; ".join(ln.strip() for ln in photo_text.splitlines() if ln.strip())
                text = (text + "\n" if text else "") + f"[photo: {one_line}]"
            elif not items:
                items = [{"product_query": req["query"], "quantity": req["quantity"], "quantity_unit": req["unit"]}]
            items = apply_usual(items, full, system)
            t = text.lower()
            rules = []
            if re.search(r"(milk|dairy) allerg|allergic to (milk|dairy)", t):
                rules.append({"rule": "exclude_allergen", "allergen": "milk", "strictness": "contains_or_may_contain"})
            if re.search(r"(nut|peanut) allerg", t):
                rules.append({"rule": "exclude_allergen", "allergen": "peanuts", "strictness": "contains_or_may_contain"})
            return AssistantTurn(None, [ToolCall(cid, "propose_intent", {
                "original_request": text, "items": items, "deliver_by": req["date"], "hard_rules": rules})])

        intent_id = confirmed.group(1)
        m = re.search(r"Items: (.+?)\. Go ahead", text)
        items = [tuple(x.split("=", 1)) for x in (m.group(1).split("; ") if m else []) if "=" in x]
        searched = {r.get("item_id"): r for n, r in tool_msgs if n == "search_products"}
        offered = {r.get("item_id"): r for n, r in tool_msgs if n == "check_offer"}
        for iid, query in items:
            if iid not in searched:
                return AssistantTurn(f"Searching for **{query}**." if len(items) > 1 else
                                     f"Searching for **{query}** against your confirmed intent.",
                                     [ToolCall(cid, "search_products", {"query": query, "intent_id": intent_id,
                                                                        "item_id": iid, "limit": 10})])
            if iid not in offered:
                ids = [p["product_id"] for p in searched[iid].get("products", [])][:10]
                if ids:
                    return AssistantTurn(None, [ToolCall(cid, "check_offer", {"product_ids": ids, "intent_id": intent_id,
                                                                               "item_id": iid})])
                offered[iid] = {"offers": []}
        if not any(n == "place_order" for n, _ in tool_msgs):
            lines, missing = [], []
            for iid, query in items:
                best = next((o for o in offered.get(iid, {}).get("offers", []) if o.get("can_fulfil")), None)
                (lines.append({"item_id": iid, "quote_id": best["quote_id"]}) if best else missing.append(query))
            if not lines:
                return AssistantTurn("Nothing that meets every rule can deliver the full quantity on time: "
                                     + ", ".join(missing) + ". Tell me whether a later date or a partial delivery works.")
            reason = ("Cheapest offer per item that meets every rule and delivers in full on time"
                      + (f". No offer yet for: {', '.join(missing)}" if missing else ""))
            return AssistantTurn(None, [ToolCall(cid, "place_order", {"intent_id": intent_id, "lines": lines,
                                                                      "reason": reason})])
        return AssistantTurn("Done.")


# --------------------------------------------------------------------------- factory

def make_provider(today: date | None = None) -> LLMProvider:
    p, key, model, base = config.LLM_PROVIDER, config.LLM_API_KEY, config.LLM_MODEL, config.LLM_BASE_URL
    if not p or p == "scripted" or not key:
        return ScriptedProvider(today or datetime.now().date())
    if p == "anthropic":
        return AnthropicProvider(key, model, base)
    if p == "openai":
        return OpenAICompatibleProvider(key, model or "gpt-4.1-mini", base or "https://api.openai.com/v1", "openai")
    if p == "gemini":
        return OpenAICompatibleProvider(key, model or "gemini-2.5-flash",
                                        base or "https://generativelanguage.googleapis.com/v1beta/openai", "gemini")
    if p == "openai_compatible":
        return OpenAICompatibleProvider(key, model, base)
    raise ValueError(f"unknown LLM_PROVIDER {p}")
