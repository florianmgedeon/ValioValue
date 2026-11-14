"""Buyer backend: REST + SSE API from contracts/backend-api.yaml."""
from __future__ import annotations

import asyncio
import json
import logging

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path

from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel

from . import agent, config, signing, valio
from .llm import make_provider, simplify_schema
from .routes_kitchen import router as kitchen_router
from .routes_orders import router as orders_router
from .routes_rules import router as rules_router
from .routes_uploads import router as uploads_router

app = FastAPI(title="ValioValue buyer agent")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(uploads_router)   # 1a photos and phone pairing
app.include_router(kitchen_router)   # 1b kitchen profile and ingest
app.include_router(orders_router)    # 2b order list and autonomy level
app.include_router(rules_router)     # 3.1 / 3.2: POST /mandate/draft, POST /mandate/coverage
_tasks: set[asyncio.Task] = set()


class Attachment(BaseModel):
    upload_id: str


class ChatRequest(BaseModel):
    message: str = ""  # may be empty when a photo is attached
    session_id: str | None = None
    buyer_id: str | None = None
    attachments: list[Attachment] = []


class ApprovalDecision(BaseModel):
    decision: str
    comment: str | None = None
    decided_by: str | None = None
    cart_hash: str | None = None  # the hash the human was shown


class IntentConfirm(BaseModel):
    intent_hash: str
    confirmed_by: str | None = None
    edits: dict | None = None


async def _valio(method: str, path: str, **kw) -> httpx.Response:
    """Human-side calls to Valio Aimo's approval endpoints. The token never reaches the LLM."""
    return await valio.call(method, path, **kw)


_passthrough = valio.passthrough


@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse("/app")


@app.get("/app", include_in_schema=False)
async def buyer_app():
    """Buyer UI: request, intent, sourcing, cart, receipt, with mandate, trust inspector and audit trail."""
    return FileResponse(Path(__file__).parent / "static" / "app.html")


@app.get("/console", include_in_schema=False)
async def console():
    """Plain debug UI: what the agent does, offers, approvals, audit trail."""
    return FileResponse(Path(__file__).parent / "static" / "console.html")


@app.get("/health")
async def health():
    return {"status": "ok", "llm_provider": make_provider().name, "mcp_server": config.MCP_SERVER_URL}


@app.post("/chat")
async def chat(req: ChatRequest):
    from . import uploads
    ids = [a.upload_id for a in req.attachments]
    for u in ids:
        try:
            uploads.meta(u)
        except (FileNotFoundError, ValueError):
            raise HTTPException(404, f"unknown upload {u}") from None
    if not req.message.strip() and not ids:
        raise HTTPException(422, "message or attachments required")
    session = agent.get_or_create_session(req.session_id, req.buyer_id)
    task = asyncio.create_task(agent.run_turn(session, req.message, images=ids or None))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return {"session_id": session.id}


@app.get("/chat/{session_id}/stream")
async def stream(session_id: str):
    session = agent.SESSIONS.get(session_id)
    if not session:
        raise HTTPException(404, "unknown session")
    queue: asyncio.Queue = asyncio.Queue()
    backlog = list(session.events)
    session.subscribers.add(queue)

    def frame(evt: dict) -> str:
        return f"event: {evt['type']}\ndata: {json.dumps(evt, ensure_ascii=False)}\n\n"

    async def gen():
        try:
            for evt in backlog:
                yield frame(evt)
            while True:
                try:
                    yield frame(await asyncio.wait_for(queue.get(), timeout=15))
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            session.subscribers.discard(queue)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


_tools_cache: list | None = None


@app.get("/tools")
async def list_tools():
    """The MCP tools the agent sees, with their documentation and input schema."""
    global _tools_cache
    if _tools_cache is None:
        async with agent.mcp_session(agent.Session("tools-listing", config.DEFAULT_BUYER_ID)) as mcp:
            _tools_cache = [{"name": t.name, "description": t.description, "input_schema": simplify_schema(t.inputSchema)}
                            for t in (await mcp.list_tools()).tools]
    return _tools_cache


@app.get("/approvals")
async def list_approvals(status: str | None = None):
    return _passthrough(await _valio("GET", "/internal/approvals", params={"status": status} if status else None))


@app.post("/approvals/{approval_id}")
async def decide(approval_id: str, body: ApprovalDecision):
    """Approve signs exactly the cart_hash the human was shown. If the cart changed, Valio answers 409."""
    if body.decision not in ("approve", "reject"):
        raise HTTPException(422, "decision must be approve or reject")
    payload = body.model_dump()
    if body.decision == "approve":
        appr = next((a for a in _passthrough(await _valio("GET", "/internal/approvals"))
                     if a["approval_id"] == approval_id), None)
        if not appr:
            raise HTTPException(404, "unknown approval")
        if not body.cart_hash:
            # v0.1 frontends do not send the hash yet. Sign the one on file and say so in the audit comment.
            payload["cart_hash"] = appr.get("cart_hash")
            payload["comment"] = ((body.comment or "") + " [client did not echo cart_hash]").strip()
        payload["signature"] = signing.sign(appr["intent"]["buyer_id"], payload["cart_hash"] or "")
    res = _passthrough(await _valio("POST", f"/internal/approvals/{approval_id}", json=payload))
    agent.on_decision(res["order"], res["approval"])
    return res["approval"]


@app.post("/intents/{intent_id}/confirm")
async def confirm_intent(intent_id: str, body: IntentConfirm):
    """With edits: returns a revised intent (new hash) to be confirmed. Without: signs the hash the human saw,
    Valio confirms, and the agent continues in the same stream."""
    sid = agent.INTENT_SESSION.get(intent_id)
    session = agent.SESSIONS.get(sid) if sid else None
    if body.edits:
        new = _passthrough(await _valio("POST", f"/internal/intents/{intent_id}/revise", json={"edits": body.edits}))
        if session:
            agent.on_intent_revised(session, new)
        return new
    it = _passthrough(await _valio("GET", f"/internal/intents/{intent_id}"))
    if body.intent_hash != it["intent_hash"]:
        raise HTTPException(409, {"error": "The intent changed since it was shown. Check it again.",
                                  "intent": it})
    confirmed = _passthrough(await _valio("POST", f"/internal/intents/{intent_id}/confirm", json={
        "intent_hash": body.intent_hash, "signature": signing.sign(it["buyer_id"], body.intent_hash),
        "confirmed_by": body.confirmed_by or "buyer.human"}))
    if session:
        task = asyncio.create_task(agent.on_intent_confirmed(session, confirmed))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
    return confirmed


@app.get("/orders/{order_id}/verify")
async def verify(order_id: str):
    async with httpx.AsyncClient(timeout=20) as c:
        return (await c.get(f"{config.MCP_BASE_URL}/verify/{order_id}")).json()


@app.get("/mandate")
async def mandate(buyer_id: str | None = None):
    """Current mandate: mandatory_rules (Must), preferences (Prefer), autonomy.grants (Autonomy)."""
    return _passthrough(await _valio("GET", f"/internal/mandate/{buyer_id or config.DEFAULT_BUYER_ID}"))


class MandateUpdate(BaseModel):
    mandate: dict
    buyer_id: str | None = None
    updated_by: str | None = None
    mandate_hash: str | None = None  # the hash the human was shown (from /mandate/preview); recommended


@app.post("/mandate/preview")
async def mandate_preview(body: MandateUpdate):
    """Validate a new mandate. Returns the next version, its hash, a plain summary and the Cedar policies."""
    bid = body.buyer_id or config.DEFAULT_BUYER_ID
    return _passthrough(await _valio("POST", f"/internal/mandate/{bid}/preview", json={"mandate": body.mandate}))


@app.put("/mandate")
async def mandate_put(body: MandateUpdate):
    """Store a new mandate version, signed with the buyer key. If mandate_hash is sent, it must match what the
    preview gives now (the human saw exactly this). Changes apply to every order decided from now on."""
    bid = body.buyer_id or config.DEFAULT_BUYER_ID
    pre = _passthrough(await _valio("POST", f"/internal/mandate/{bid}/preview", json={"mandate": body.mandate}))
    if body.mandate_hash and body.mandate_hash != pre["mandate_hash"]:
        raise HTTPException(409, {"error": "The mandate changed since it was shown. Check it again.", "preview": pre})
    return _passthrough(await _valio("PUT", f"/internal/mandate/{bid}", json={
        "mandate": body.mandate, "mandate_hash": pre["mandate_hash"],
        "signature": signing.sign(bid, pre["mandate_hash"]), "updated_by": body.updated_by or "buyer.human"}))


@app.get("/mandate/history")
async def mandate_history(buyer_id: str | None = None):
    return _passthrough(await _valio("GET", f"/internal/mandate/{buyer_id or config.DEFAULT_BUYER_ID}/history"))


@app.get("/mandate/cedar")
async def mandate_cedar(buyer_id: str | None = None):
    return _passthrough(await _valio("GET", f"/internal/mandate/{buyer_id or config.DEFAULT_BUYER_ID}/cedar"))


@app.get("/policy-catalog")
async def policy_catalog():
    """Attributes, operators and example values a policy editor can offer."""
    return _passthrough(await _valio("GET", "/internal/policy-catalog"))


@app.get("/orders/{order_id}/audit")
async def audit(order_id: str):
    return _passthrough(await _valio("GET", f"/internal/orders/{order_id}/audit"))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    uvicorn.run(app, host=config.BACKEND_HOST, port=config.BACKEND_PORT)


if __name__ == "__main__":
    main()
