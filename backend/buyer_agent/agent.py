"""Buyer agent: LLM loop + MCP client. Emits the typed events from contracts/backend-api.yaml."""
from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, AsyncIterator
from zoneinfo import ZoneInfo

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from . import config
from .llm import AssistantTurn, ToolSpec, make_provider, simplify_schema

log = logging.getLogger("buyer_agent")
TZ = ZoneInfo(config.TIMEZONE)


def now_iso() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- sessions and events

@dataclass
class Session:
    id: str
    buyer_id: str
    history: list[dict] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    subscribers: set[asyncio.Queue] = field(default_factory=set)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def emit(self, type_: str, **payload: Any) -> dict:
        evt = {"type": type_, "session_id": self.id, "timestamp": now_iso(), **payload}
        self.events.append(evt)
        for q in list(self.subscribers):
            q.put_nowait(evt)
        return evt


SESSIONS: dict[str, Session] = {}
ORDER_SESSION: dict[str, str] = {}


def get_or_create_session(session_id: str | None, buyer_id: str | None) -> Session:
    if session_id and session_id in SESSIONS:
        return SESSIONS[session_id]
    sid = session_id or f"sess_{secrets.token_hex(4)}"
    SESSIONS[sid] = Session(sid, buyer_id or config.DEFAULT_BUYER_ID)
    return SESSIONS[sid]


# --------------------------------------------------------------------------- MCP

@asynccontextmanager
async def mcp_session(session: Session) -> AsyncIterator[ClientSession]:
    headers = {"X-Agent-Session": session.id, "X-Buyer-Id": session.buyer_id}
    async with streamablehttp_client(config.MCP_SERVER_URL, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as s:
            await s.initialize()
            yield s


def tool_payload(result: Any) -> dict:
    if result.structuredContent is not None:
        return result.structuredContent
    text = "".join(getattr(c, "text", "") for c in result.content)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"text": text}


# --------------------------------------------------------------------------- UI labels

def label(tool: str, args: dict) -> str:
    if tool == "search_products":
        return f"Searching the Valio Aimo catalog for {args.get('query', 'products')}"
    if tool == "check_offer":
        n = len(args.get("product_ids", []))
        return f"Checking price and availability for {n} product{'s' * (n != 1)} for {args.get('requested_date')}"
    if tool == "place_order":
        return "Placing the order with Valio Aimo"
    if tool == "get_order_status":
        return f"Checking status of {args.get('order_id')}"
    return tool


def summary(tool: str, res: dict) -> str:
    if res.get("error"):
        return str(res["error"])
    if tool == "search_products":
        return f"Found {res.get('total_matches', 0)} matching products, shortlisted {res.get('returned', 0)}"
    if tool == "check_offer":
        offers = res.get("offers", [])
        full = sum(1 for o in offers if o.get("can_fulfil"))
        return f"{full} of {len(offers)} can deliver the full quantity on {res.get('requested_date')}"
    if tool == "place_order":
        return {"confirmed": f"Order {res.get('order_id')} confirmed",
                "pending_approval": f"Order {res.get('order_id')} needs human approval",
                "rejected": f"Order rejected: {res.get('message', '')}"}.get(res.get("status"), str(res.get("status")))
    return "ok"


def unit_word(unit: str) -> str:
    return {"L": "L", "KG": "kg", "SALES_UNIT": "units"}.get(unit, unit)


# --------------------------------------------------------------------------- system prompt

def system_prompt(session: Session) -> str:
    today = datetime.now(TZ)
    return f"""You are the purchasing agent of buyer '{session.buyer_id}', ordering from Valio Aimo, a Finnish foodservice wholesaler, through its MCP tools.
Now is {today:%A %Y-%m-%d %H:%M} ({config.TIMEZONE}). Quote expiry and order cut-off times are in the same time zone. "By Monday" means the next Monday after today, as YYYY-MM-DD.

How to work:
1. search_products with a short product query in English (e.g. "lactose-free milk") and filters that reflect hard requirements (lactose-free, allergens, origin).
2. check_offer with ALL search results that match the request (up to 10 product ids, different pack sizes included, since price per litre differs), the delivery date, and the quantity in the unit the buyer used (L or KG are converted to sales units by Valio). Leave out only products that clearly are something else.
3. Pick the offer that exactly matches the request and can deliver in full on time (can_fulfil=true). Default preference: lowest total price, then higher delivery reliability. Offers come sorted best first, rank 'best' is usually right. Never pick a product that violates a stated requirement. Only call something cheapest if it is cheapest among the offers you checked.
4. place_order with that single quote_id and a one-sentence reason a human approver can understand.
5. After place_order, do not call any more tools. Tell the buyer the outcome in 1 to 3 sentences: product, quantity, total, delivery date, and if pending_approval, why it waits for their approval. You cannot approve orders and must not re-order to get around a limit.
If nothing can deliver in full, explain the best options (partial, later date) and ask; do not order.
Keep messages short, use markdown, mention price, quantity in sales units and delivery date."""


# --------------------------------------------------------------------------- the loop

async def run_turn(session: Session, user_text: str) -> None:
    async with session.lock:
        session.history.append({"role": "user", "text": user_text})
        try:
            await _loop(session)
        except Exception as e:  # surface every failure to the UI, never die silently
            log.exception("turn failed")
            session.emit("error", message=f"{type(e).__name__}: {e}", recoverable=True)
        session.emit("done")


async def _loop(session: Session) -> None:
    provider = make_provider(datetime.now(TZ).date())
    system = system_prompt(session)
    async with mcp_session(session) as mcp:
        tools = [ToolSpec(t.name, t.description or "", simplify_schema(t.inputSchema))
                 for t in (await mcp.list_tools()).tools]
        step = 0

        async def think() -> AssistantTurn:
            nonlocal step
            step += 1
            t0 = time.perf_counter()
            turn = await provider.complete(system, session.history, tools)
            session.emit("reasoning", step=step, model=turn.model or provider.name, text=turn.reasoning,
                         tool_calls=[c.name for c in turn.tool_calls], usage=_usage(turn.usage),
                         duration_ms=round((time.perf_counter() - t0) * 1000))
            return turn

        for _ in range(config.LLM_MAX_STEPS):
            turn = await think()
            session.history.append({"role": "assistant", "text": turn.text, "raw": turn.raw,
                                    "tool_calls": [{"id": c.id, "name": c.name, "args": c.args} for c in turn.tool_calls]})
            if turn.text:
                session.emit("message", role="assistant", text=turn.text)
            if not turn.tool_calls:
                return
            stop, order_res = False, None
            for call in turn.tool_calls:
                if stop:  # an order is already in flight, refuse anything after it in this turn
                    res = {"error": "skipped: an order was already placed in this turn"}
                    session.emit("tool_call", call_id=call.id, tool=call.name, step=step, args=call.args,
                                 label=f"Blocked {call.name}: an order was already placed")
                    session.emit("tool_result", call_id=call.id, tool=call.name, ok=False, summary=res["error"],
                                 result=res, duration_ms=0)
                else:
                    session.emit("tool_call", call_id=call.id, tool=call.name, step=step,
                                 label=label(call.name, call.args), args=call.args)
                    t0 = time.perf_counter()
                    try:
                        res = tool_payload(await mcp.call_tool(call.name, call.args))
                    except Exception as e:
                        res = {"error": f"{type(e).__name__}: {e}"}
                    ok = not res.get("error") and res.get("status") != "rejected"
                    session.emit("tool_result", call_id=call.id, tool=call.name, ok=ok, summary=summary(call.name, res),
                                 result=res, duration_ms=round((time.perf_counter() - t0) * 1000))
                    stop = _side_events(session, call.name, call.args, res)
                    if stop:
                        order_res = res
                session.history.append({"role": "tool", "tool_call_id": call.id, "name": call.name,
                                        "content": json.dumps(res, ensure_ascii=False)[:15000]})
            if stop:
                await _wrap_up(session, provider, think, order_res)
                return
        session.emit("message", role="assistant", text="I stopped after too many steps. Please refine the request.")


async def _wrap_up(session: Session, provider, think, order_res: dict) -> None:
    """One last LLM call so the agent explains the outcome in its own words (and its reasoning is visible).
    Any further tool call is ignored: an order is in flight. Falls back to a fixed text."""
    text = None
    if provider.name != "scripted":
        try:
            turn = await think()
            text = turn.text
        except Exception:
            log.exception("wrap-up call failed")
    text = text or _order_message(order_res)
    session.history.append({"role": "assistant", "text": text, "tool_calls": []})
    session.emit("message", role="assistant", text=text)


def _usage(u: dict | None) -> dict | None:
    if not u:
        return None
    return {"input_tokens": u.get("input_tokens") or u.get("prompt_tokens"),
            "output_tokens": u.get("output_tokens") or u.get("completion_tokens"),
            "thinking_tokens": (u.get("output_tokens_details") or {}).get("thinking_tokens")}


def _side_events(session: Session, tool: str, args: dict, res: dict) -> bool:
    """Emit rich UI events. Returns True when the turn should end (order placed)."""
    if tool == "check_offer" and res.get("offers") is not None:
        session.emit("offers",
                     request_summary=f"{args.get('quantity', 0):,.0f} {unit_word(args.get('quantity_unit', 'SALES_UNIT'))}, "
                                     f"delivery {args.get('requested_date')}",
                     offers=res["offers"], recommended_product_id=res.get("recommended_product_id"))
    if tool == "place_order" and res.get("order_id"):
        ORDER_SESSION[res["order_id"]] = session.id
        if res.get("status") == "pending_approval":
            session.emit("approval_required", approval=res["approval"])
            return True
        if res.get("status") == "confirmed":
            session.emit("order_confirmed", order_id=res["order_id"], status="confirmed", lines=res["lines"],
                         total_price=res["total_price"], currency=res["currency"],
                         delivery_date=res["delivery_date"], approved_by=None)
            return True
    return False


def _order_message(res: dict) -> str:
    line = (res.get("lines") or [{}])[0]
    what = f"{line.get('quantity', 0):,.0f} {line.get('sales_unit', '')} of {line.get('name', '')}"
    money = f"{res.get('total_price', 0):,.2f} {res.get('currency', 'EUR')}"
    if res.get("status") == "pending_approval":
        return (f"Order **{res['order_id']}** for {what} ({money}, delivery {res.get('delivery_date')}) "
                f"is waiting for your approval: {res['approval']['reason']}.")
    return f"Order **{res['order_id']}** confirmed: {what}, {money}, delivery {res.get('delivery_date')}."


# --------------------------------------------------------------------------- after a human decision

def on_decision(order: dict, approval: dict) -> None:
    sid = ORDER_SESSION.get(order["order_id"]) or order.get("session")
    session = SESSIONS.get(sid) if sid else None
    if not session:
        return
    if order["status"] == "confirmed":
        session.emit("order_confirmed", order_id=order["order_id"], status="confirmed", lines=order["lines"],
                     total_price=order["total_price"], currency=order["currency"],
                     delivery_date=order["delivery_date"], approved_by=order.get("approved_by"))
        text = (f"Order **{order['order_id']}** confirmed for {order['delivery_date']}. "
                f"Approved by {order.get('approved_by')}.")
    elif order["status"] == "quote_expired":
        text = f"Approval came after the quote expired. Order **{order['order_id']}** needs a fresh offer."
    else:
        text = f"Order **{order['order_id']}** was rejected by {approval.get('decided_by')}." + (
            f" Comment: {approval['comment']}" if approval.get("comment") else "")
    session.history.append({"role": "assistant", "text": text, "tool_calls": []})
    session.emit("message", role="assistant", text=text)
    session.emit("done")
