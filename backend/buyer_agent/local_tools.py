"""Package 2a: tools that run on the buyer side, in this backend, not at Valio Aimo.

They are offered to the model next to Valio Aimo's MCP tools and dispatched locally. They can only create
suggestions in the kitchen profile (decision W7). They never touch the mandate, intents or orders, so no local tool
can weaken a rule: Valio Aimo still enforces the signed mandate on every order.
"""
from __future__ import annotations

import logging

from . import ingest, kitchen, uploads, valio
from .llm import ToolSpec

log = logging.getLogger("buyer_agent.local_tools")

CHAT_KINDS = ("about", "usual", "prefer", "must")

SPECS = [
    ToolSpec(
        "remember_about_kitchen",
        "The buyer said something about the kitchen (a fact, a habit, a rule). Creates ONE suggestion for the "
        "kitchen profile that the buyer accepts or rejects in the app. Nothing becomes active by this call. "
        "kind: about = who the kitchen is or a fact (payload {text} or {fact}); usual = what a word means as a "
        "product (payload {word, product_query, typical_qty?, unit?, per?}); must = a rule nothing may break "
        "(payload {attr, op, value, if_unknown: 'exclude', label}); prefer = what is better (payload {attr, op, "
        "value, weight 1-3}). quote = the buyer's words, verbatim.",
        {"type": "object", "required": ["kind", "title", "payload", "quote"],
         "properties": {"kind": {"type": "string", "enum": list(CHAT_KINDS)},
                        "title": {"type": "string", "description": "One plain sentence for the buyer"},
                        "payload": {"type": "object"},
                        "quote": {"type": "string", "description": "The buyer's words, copied verbatim"}}},
    ),
    ToolSpec(
        "learn_from_document",
        "Read a photo or PDF the buyer sent in this chat (menu, allergy note, invoice) and save what is worth "
        "remembering as suggestions for the kitchen profile. Never orders anything.",
        {"type": "object", "required": ["upload_id"],
         "properties": {"upload_id": {"type": "string", "description": "An upload id from the buyer's message"}}},
    ),
]
NAMES = {s.name for s in SPECS}


def label(tool: str, args: dict) -> str:
    if tool == "remember_about_kitchen":
        return "Noting this for your kitchen profile"
    if tool == "learn_from_document":
        return "Reading your document for the kitchen profile"
    return tool


def summary(tool: str, res: dict) -> str:
    if res.get("error"):
        return str(res["error"])
    n = len(res.get("suggestions") or [])
    return f"{n} suggestion{'s' * (n != 1)} for your kitchen profile" if n else "Nothing new to remember"


def _session_uploads(session) -> set[str]:
    return {u for m in session.history if m.get("role") == "user" for u in m.get("attachments") or []}


def _last_user_text(session) -> str:
    return next((m.get("text") or "" for m in reversed(session.history) if m.get("role") == "user"), "")


async def remember_about_kitchen(session, kind: str, title: str, payload: dict, quote: str) -> dict:
    if kind not in CHAT_KINDS:
        return {"error": f"kind must be one of {', '.join(CHAT_KINDS)}"}
    if not isinstance(payload, dict):
        return {"error": "payload must be an object"}
    profile = kitchen.load(session.buyer_id)
    mandate = await valio.mandate(session.buyer_id)
    raw = [{"kind": kind, "title": title, "quote": quote, "payload": payload}]
    ok = await ingest.validate(session.buyer_id, raw, profile, mandate, _last_user_text(session))
    if not ok:
        hint = ("quote must be the buyer's words, copied verbatim" if quote and kind in ("about", "usual")
                else "the rule is not valid for Valio Aimo: use an attribute and operator from the policy catalog")
        return {"error": f"suggestion not saved: {hint}"}
    src = {"type": "text", "title": (quote or title)[:60], "text": quote, "session_id": session.id}
    saved = kitchen.add_suggestions(session.buyer_id, ok, src)
    return {"suggestions": saved, "source": src,
            "note": "Saved as a suggestion. The buyer accepts it in the app" + (
                "; a must or preference also needs their signature." if kind in kitchen.SIGNED_KINDS else ".")}


async def learn_from_document(session, upload_id: str) -> dict:
    if upload_id not in _session_uploads(session):
        return {"error": f"unknown upload {upload_id}: use an upload id from the buyer's message"}
    try:
        uploads.meta(upload_id)
    except (FileNotFoundError, ValueError):
        return {"error": f"upload {upload_id} not found"}
    return await ingest.ingest_one(session.buyer_id, upload_id=upload_id)


async def call(session, tool: str, args: dict) -> dict:
    try:
        if tool == "remember_about_kitchen":
            return await remember_about_kitchen(session, args.get("kind"), args.get("title") or "",
                                                args.get("payload") or {}, args.get("quote") or "")
        if tool == "learn_from_document":
            return await learn_from_document(session, args.get("upload_id") or "")
    except TypeError as e:
        return {"error": f"bad arguments: {e}"}
    return {"error": f"unknown tool {tool}"}


def reason(tool: str) -> str:
    return "from_photo" if tool == "learn_from_document" else "from_chat"
