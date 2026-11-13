"""Buyer backend: REST + SSE API from contracts/backend-api.yaml."""
from __future__ import annotations

import asyncio
import json
import logging

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from . import agent, config
from .llm import make_provider

app = FastAPI(title="ValioValue buyer agent")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
_tasks: set[asyncio.Task] = set()


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    buyer_id: str | None = None


class ApprovalDecision(BaseModel):
    decision: str
    comment: str | None = None
    decided_by: str | None = None


async def _valio(method: str, path: str, **kw) -> httpx.Response:
    """Human-side calls to Valio Aimo's approval endpoints. The token never reaches the LLM."""
    async with httpx.AsyncClient(timeout=20) as c:
        return await c.request(method, f"{config.MCP_BASE_URL}{path}",
                               headers={"Authorization": f"Bearer {config.APPROVER_TOKEN}"}, **kw)


def _passthrough(r: httpx.Response):
    if r.status_code >= 400:
        raise HTTPException(r.status_code, r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text)
    return r.json()


@app.get("/health")
async def health():
    return {"status": "ok", "llm_provider": make_provider().name, "mcp_server": config.MCP_SERVER_URL}


@app.post("/chat")
async def chat(req: ChatRequest):
    session = agent.get_or_create_session(req.session_id, req.buyer_id)
    task = asyncio.create_task(agent.run_turn(session, req.message))
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


@app.get("/approvals")
async def list_approvals(status: str | None = None):
    return _passthrough(await _valio("GET", "/internal/approvals", params={"status": status} if status else None))


@app.post("/approvals/{approval_id}")
async def decide(approval_id: str, body: ApprovalDecision):
    if body.decision not in ("approve", "reject"):
        raise HTTPException(422, "decision must be approve or reject")
    res = _passthrough(await _valio("POST", f"/internal/approvals/{approval_id}", json=body.model_dump()))
    agent.on_decision(res["order"], res["approval"])
    return res["approval"]


@app.get("/orders/{order_id}/audit")
async def audit(order_id: str):
    return _passthrough(await _valio("GET", f"/internal/orders/{order_id}/audit"))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    uvicorn.run(app, host=config.BACKEND_HOST, port=config.BACKEND_PORT)


if __name__ == "__main__":
    main()
