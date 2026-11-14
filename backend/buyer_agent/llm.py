"""Swappable LLM providers behind one small interface.

Conversation history is provider-neutral:
  {"role": "user", "text": str}
  {"role": "assistant", "text": str | None, "tool_calls": [{"id", "name", "args"}]}
  {"role": "tool", "tool_call_id": str, "name": str, "content": str}

Adapters talk plain HTTP (no vendor SDK), so switching is one env var.
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Protocol

import httpx

from . import config


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


# --------------------------------------------------------------------------- Anthropic

class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, model: str, base_url: str = ""):
        self.api_key, self.model = api_key, model or "claude-sonnet-4-5"
        self.url = (base_url or "https://api.anthropic.com").rstrip("/") + "/v1/messages"

    @staticmethod
    def _messages(history: list[dict]) -> list[dict]:
        out: list[dict] = []
        for m in history:
            if m["role"] == "user":
                blocks = [{"type": "text", "text": m["text"]}]
                role = "user"
            elif m["role"] == "assistant":
                if m.get("raw"):
                    blocks = list(m["raw"])
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
        for m in history:
            if m["role"] == "user":
                out.append({"role": "user", "content": m["text"]})
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


class ScriptedProvider:
    """Deterministic planner without an LLM key:
    new request -> propose_intent and stop. Confirmed intent -> search -> check_offer -> place_order."""

    name = "scripted"

    def __init__(self, today: date):
        self.today = today

    async def complete(self, system, history, tools):
        users = [m for m in history if m["role"] == "user"]
        text = users[-1]["text"] if users else ""
        last_user = max(i for i, m in enumerate(history) if m["role"] == "user")
        results = {m["name"]: json.loads(m["content"]) for m in history[last_user:] if m["role"] == "tool"}
        cid = f"call_{uuid.uuid4().hex[:8]}"
        confirmed = re.search(r"signed intent (int_\w+)", text)

        if not confirmed:
            if "propose_intent" in results:
                return AssistantTurn(None)
            req = parse_request(text, self.today)
            t = text.lower()
            rules = []
            if re.search(r"(milk|dairy) allerg|allergic to (milk|dairy)", t):
                rules.append({"rule": "exclude_allergen", "allergen": "milk",
                              "strictness": "contains_or_may_contain"})
            return AssistantTurn(None, [ToolCall(cid, "propose_intent", {
                "original_request": text, "product_query": req["query"], "quantity": req["quantity"],
                "quantity_unit": req["unit"], "deliver_by": req["date"], "hard_rules": rules})])

        intent_id = confirmed.group(1)
        m = re.search(r"Product: (.+?)\. Go ahead", text)
        query = m.group(1) if m else "products"
        if "search_products" not in results:
            return AssistantTurn(f"Searching for **{query}** against your confirmed intent.",
                                 [ToolCall(cid, "search_products", {"query": query, "intent_id": intent_id, "limit": 12})])
        if "check_offer" not in results:
            ids = [p["product_id"] for p in results["search_products"].get("products", [])][:10]
            if not ids:
                return AssistantTurn(f"Nothing in the Valio Aimo catalog for '{query}' meets every rule of your intent.")
            return AssistantTurn(None, [ToolCall(cid, "check_offer", {"product_ids": ids, "intent_id": intent_id})])
        if "place_order" not in results:
            offers = results["check_offer"].get("offers", [])
            best = next((o for o in offers if o.get("can_fulfil")), None)
            if not best:
                return AssistantTurn("None of the matching products can deliver the full quantity on time. "
                                     "Tell me whether a later date or a partial delivery works.")
            reason = (f"Cheapest offer that meets every rule and delivers the full {best['quantity']:g} "
                      f"{best['sales_unit']} by {best['requested_date']}"
                      + (f", delivery reliability {best['delivery_reliability']:.0%}" if best.get("delivery_reliability") else ""))
            return AssistantTurn(None, [ToolCall(cid, "place_order", {"quote_id": best["quote_id"],
                                                                      "intent_id": intent_id, "reason": reason})])
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
