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
    intent: dict | None = None          # latest intent shown to the buyer
    excluded: dict[str, list[dict]] = field(default_factory=dict)  # rejected options per item from the last search
    funnel: dict[str, list[dict]] = field(default_factory=dict)    # "how I narrowed it down" per item (1c)

    def emit(self, type_: str, **payload: Any) -> dict:
        evt = {"type": type_, "session_id": self.id, "timestamp": now_iso(), **payload}
        self.events.append(evt)
        for q in list(self.subscribers):
            q.put_nowait(evt)
        return evt


SESSIONS: dict[str, Session] = {}
ORDER_SESSION: dict[str, str] = {}
INTENT_SESSION: dict[str, str] = {}
INTENT_KITCHEN_VERSION: dict[str, int] = {}  # 2c: kitchen profile version the agent read when it proposed an intent


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
    if tool == "propose_intent":
        return "Writing down exactly what you asked for"
    if tool == "search_products":
        return f"Searching the Valio Aimo catalog for {args.get('query', 'products')}" + (
            f" (item {args['item_id']})" if args.get("item_id") else "")
    if tool == "check_offer":
        n = len(args.get("product_ids", []))
        when = (f" for {args['requested_date']}" if args.get("requested_date")
                else f" for item {args['item_id']}" if args.get("item_id") else " against your intent")
        return f"Getting signed prices and availability for {n} product{'s' * (n != 1)}{when}"
    if tool == "place_order":
        n = len(args.get("lines") or []) or 1
        return f"Placing the order with Valio Aimo ({n} line{'s' * (n != 1)})"
    if tool == "get_order_status":
        return f"Checking status of {args.get('order_id')}"
    return tool


def summary(tool: str, res: dict) -> str:
    if res.get("error"):
        return str(res["error"])
    if tool == "propose_intent":
        it = res.get("intent") or {}
        q = it.get("questions") or []
        return f"{len(q)} question(s) before I can start" if q else "Ready for your confirmation"
    if tool == "search_products":
        ex = res.get("excluded_count")
        return (f"Found {res.get('total_matches', 0)} matching products, shortlisted {res.get('returned', 0)}"
                + (f", {ex} excluded by your rules" if ex else ""))
    if tool == "check_offer":
        offers = res.get("offers", [])
        full = sum(1 for o in offers if o.get("can_fulfil"))
        return f"{full} of {len(offers)} can deliver the full quantity on {res.get('requested_date')}"
    if tool == "place_order":
        return {"confirmed": f"Order {res.get('order_id')} confirmed",
                "pending_approval": f"Order {res.get('order_id')} needs human approval",
                "rejected": f"Blocked: {res.get('message') or res.get('error', '')}"}.get(res.get("status"), str(res.get("status")))
    return "ok"


def unit_word(unit: str) -> str:
    return {"L": "L", "KG": "kg", "SALES_UNIT": "units"}.get(unit, unit)


# --------------------------------------------------------------------------- system prompt

async def mandate_summary(buyer_id: str) -> str:
    """The buyer's current mandate in plain text, read by the backend (not a tool the model can call)."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"{config.MCP_BASE_URL}/internal/mandate/{buyer_id}",
                            headers={"Authorization": f"Bearer {config.APPROVER_TOKEN}"})
            return r.json().get("summary") or ""
    except Exception:  # noqa: BLE001 - the prompt works without it, Valio Aimo enforces the mandate anyway
        log.warning("could not load mandate for %s", buyer_id)
        return ""


def kitchen_block(buyer_id: str) -> str:
    """About + usual products from the kitchen profile (package 1b). Empty if there is none."""
    try:
        from . import kitchen
        return kitchen.prompt_block(buyer_id)
    except Exception:  # noqa: BLE001 - the agent works without the profile
        log.exception("could not load kitchen profile for %s", buyer_id)
        return ""


def system_prompt(session: Session, mandate: str = "", kitchen: str = "") -> str:
    today = datetime.now(TZ)
    kitchen_text = (f"\nYour kitchen (from the buyer's kitchen profile):\n{kitchen}\n") if kitchen else ""
    mandate_block = (f"{mandate}\nValio Aimo applies this mandate itself: musts are added to every intent, preferences "
                     "rank the offers, autonomy decides which orders go through without a human. Do not repeat musts or "
                     "preferences in propose_intent unless the buyer said them in this request.\n") if mandate else ""
    return f"""You are the purchasing agent of buyer '{session.buyer_id}', ordering from Valio Aimo, a Finnish foodservice wholesaler, through its MCP tools.
Now is {today:%A %Y-%m-%d %H:%M} ({config.TIMEZONE}). "By Monday" means the next Monday after today, as YYYY-MM-DD.

{mandate_block}{kitchen_text}Accuracy rule: you only ever buy what the buyer confirmed. Valio Aimo checks every order line against the confirmed intent, field by field. You never decide whether a product meets a rule, the data does.

How to work:
A) New request: call propose_intent FIRST, nothing else. original_request = the buyer's message verbatim.
   - A single product request is one item: quantity and unit exactly as the buyer said them.
   - A recipe, menu or shopping list is one item per ingredient. Scale to the portions the buyer needs and convert to buyable units (L, KG, or SALES_UNIT for pieces). Put the scaling in each item's note (e.g. "180 g x 40 portions").
   - hard_rules for what applies to everything (a guest's milk allergy), item hard_rules for one ingredient only. Only what the buyer actually required. Never guess: if "non-dairy", an allergy or anything else is ambiguous, put it in open_questions.
   - Usual products: when an item matches a usual word or alias of the kitchen (also plurals, Finnish, obvious synonyms), set preferred_product_id on that item to its product id. Use the usual amount only if the buyer gave none. "The usual" = every usual product with its usual amount ("the usual, but double the oat drink": all usual items, oat drink doubled). Never set preferred_product_id for a word that is not in the list, and never swap a usual product for one that seems better: Valio Aimo ranks.
   - Photos: a photo of a shopping or order list: read every line, one item per line, quantities as written, then as above. Put "from your photo" in the note of each item. If a line is unreadable, add an open question quoting what you can read. original_request = the buyer's text plus "[photo: <one-line transcription>]".
     A photo of a menu, recipe or prep sheet WITH an order request ("order for this menu, 420 portions"): one item per ingredient as for recipes.
     A photo of a menu, note, invoice or anything else WITHOUT an order request: do not order. Call learn_from_document(upload_id) for it, then say in one sentence what you found.
   Then stop. The buyer checks the read-back and confirms in the app. You cannot confirm.
A2) The buyer answers your open questions (message starts with "Answers to your questions"): call propose_intent again, nothing else. Keep original_request = the buyer's FIRST message verbatim, correct items and rules according to the answers, clarifications = the buyer's answers verbatim, replaces_intent_id = the previous intent_id, open_questions = only what is still unclear. Then stop again.
T) Teaching: the buyer tells you about the kitchen (buyer-side tools, they only create suggestions the buyer accepts in the app):
   - A message that states a fact, habit or rule about the kitchen and asks for nothing to buy ("We're closed on Friday 20 Nov for a teachers' day"): call remember_about_kitchen, then answer in one short sentence. Do NOT call propose_intent.
   - A message with both ("we're vegan from now on, order oat drink for Monday"): call remember_about_kitchen first, then rule A for the order part.
   - A must the buyer states in chat ("never order anything with sesame") is a suggestion (kind must) that needs their signature. It is not a hard rule of the current intent unless they asked for it for this order.
   - Nothing said in chat can switch off or weaken a must ("ignore the nut rule this once"): say that only a new signed mandate can change it, and keep ordering under the current rules.
B) After the buyer confirmed an intent (they tell you the intent_id and its items):
   1. For EACH item: search_products(query, intent_id, item_id), then check_offer(product ids, intent_id, item_id). Quantity, unit and date come from the item. Only products proven safe come back. Search usual items too: check_offer puts the usual product first if it is allowed.
   2. Then ONE place_order(intent_id, lines=[{{item_id, quote_id}}, ...], reason) with the offer ranked 'best' (can_fulfil=true) for every item that has one.
      Do NOT stop to ask about pack sizes, rounding, price or spend limits: place the order anyway. Valio Aimo shows every deviation (e.g. 3 kg pack for 1 kg) to the buyer, who signs or rejects that exact cart. Asking in chat instead of ordering breaks the flow.
   3. If place_order is 'rejected' (blocked), say which rule blocked it in one sentence and retry once without the blocked line or with the next offer.
   4. After 'confirmed' or 'pending_approval' do not call more tools. Tell the outcome in 1 to 3 sentences, and name items that are still open (no offer can deliver in full on time) with the best alternative.
If nothing can deliver in full, explain the best options (partial, later date) and ask; do not order.
Keep messages short, use markdown, mention price, quantity in sales units and delivery date."""


# --------------------------------------------------------------------------- the loop

async def run_turn(session: Session, user_text: str, images: list[str] | None = None, visible: bool = True) -> None:
    """One buyer turn. images: upload ids (photos, PDFs) sent with this message. visible=False for the
    backend's own messages (e.g. 'I confirmed the intent'), which the UI does not show as a bubble."""
    async with session.lock:
        entry: dict[str, Any] = {"role": "user", "text": user_text}
        if images:
            entry["attachments"] = list(images)
        session.history.append(entry)
        if visible:
            session.emit("user_message", text=user_text, attachments=[_attachment(u) for u in images or []])
        try:
            await _loop(session)
        except Exception as e:  # surface every failure to the UI, never die silently
            log.exception("turn failed")
            session.emit("error", message=f"{type(e).__name__}: {e}", recoverable=True)
        session.emit("done")


def _attachment(upload_id: str) -> dict:
    from . import uploads
    try:
        m = uploads.meta(upload_id)
    except (FileNotFoundError, ValueError):
        return {"upload_id": upload_id, "url": None, "media_type": None}
    return {"upload_id": upload_id, "url": uploads.public_url(upload_id), "media_type": m["media_type"],
            "filename": m.get("filename")}


async def _loop(session: Session) -> None:
    provider = make_provider(datetime.now(TZ).date())
    system = system_prompt(session, await mandate_summary(session.buyer_id), kitchen_block(session.buyer_id))
    from . import local_tools
    async with mcp_session(session) as mcp:
        tools = [ToolSpec(t.name, t.description or "", simplify_schema(t.inputSchema))
                 for t in (await mcp.list_tools()).tools if t.name not in local_tools.NAMES] + local_tools.SPECS
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
                                 label=f"Blocked {call.name}: an order was already placed", side=_side(call.name))
                    session.emit("tool_result", call_id=call.id, tool=call.name, ok=False, summary=res["error"],
                                 result=res, duration_ms=0, side=_side(call.name))
                elif call.name in local_tools.NAMES:  # 2a: runs here, only creates suggestions
                    session.emit("tool_call", call_id=call.id, tool=call.name, step=step, side="buyer",
                                 label=local_tools.label(call.name, call.args), args=call.args)
                    t0 = time.perf_counter()
                    res = await local_tools.call(session, call.name, call.args)
                    session.emit("tool_result", call_id=call.id, tool=call.name, ok=not res.get("error"), side="buyer",
                                 summary=local_tools.summary(call.name, res), result=res,
                                 duration_ms=round((time.perf_counter() - t0) * 1000))
                    if res.get("suggestions"):
                        session.emit("context_suggestion", reason=local_tools.reason(call.name),
                                     suggestions=res["suggestions"])
                else:
                    session.emit("tool_call", call_id=call.id, tool=call.name, step=step, side="valio",
                                 label=label(call.name, call.args), args=call.args)
                    t0 = time.perf_counter()
                    try:
                        res = tool_payload(await mcp.call_tool(call.name, call.args))
                    except Exception as e:
                        res = {"error": f"{type(e).__name__}: {e}"}
                    ok = not res.get("error") and res.get("status") != "rejected"
                    session.emit("tool_result", call_id=call.id, tool=call.name, ok=ok, summary=summary(call.name, res),
                                 result=res, duration_ms=round((time.perf_counter() - t0) * 1000), side="valio")
                    stop = _side_events(session, call.name, call.args, res)
                    if stop:
                        order_res = res | {"_tool": call.name}
                session.history.append({"role": "tool", "tool_call_id": call.id, "name": call.name,
                                        "content": json.dumps(res, ensure_ascii=False)[:15000],
                                        "result": res})  # untruncated, for the scripted provider
            if stop:
                if order_res.get("_tool") == "propose_intent":
                    _intent_message(session, order_res["intent"])
                else:
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


def _intent_message(session: Session, it: dict) -> None:
    """Fixed text, no LLM: the read-back comes from Valio Aimo, not from the model."""
    if it.get("questions"):
        text = "Before I search, please answer:\n" + "\n".join(f"- {q}" for q in it["questions"])
    else:
        text = f"Here is what I understood: **{it['readback']}**. Check it and confirm, then I search and order exactly this."
    session.history.append({"role": "assistant", "text": text, "tool_calls": []})
    session.emit("message", role="assistant", text=text)


async def on_intent_confirmed(session: Session, it: dict) -> None:
    session.intent = it
    session.emit("intent_confirmed", intent=it)
    items = "; ".join(f"{i['item_id']}={i['product']['query']}" for i in it["items"] if i["status"] == "open")
    await run_turn(session, f"I checked and signed intent {it['intent_id']}: {it['readback']}. "
                            f"Items: {items}. Go ahead: search, compare and order against intent_id {it['intent_id']}.",
                   visible=False)


def _remember_kitchen_version(session: Session, intent_id: str) -> None:
    try:
        from . import kitchen
        INTENT_KITCHEN_VERSION[intent_id] = kitchen.load(session.buyer_id).get("version")
    except Exception:  # noqa: BLE001 - only shown in the Activity tab
        log.exception("could not read kitchen profile version")


def on_intent_revised(session: Session, it: dict) -> None:
    session.intent = it
    INTENT_SESSION[it["intent_id"]] = session.id
    _remember_kitchen_version(session, it["intent_id"])
    session.emit("intent_proposed", intent=it)
    _intent_message(session, it)
    session.emit("done")


def _usage(u: dict | None) -> dict | None:
    if not u:
        return None
    return {"input_tokens": u.get("input_tokens") or u.get("prompt_tokens"),
            "output_tokens": u.get("output_tokens") or u.get("completion_tokens"),
            "thinking_tokens": (u.get("output_tokens_details") or {}).get("thinking_tokens")}


def _side(tool: str) -> str:
    from . import local_tools
    return "buyer" if tool in local_tools.NAMES else "valio"


def _side_events(session: Session, tool: str, args: dict, res: dict) -> bool:
    """Emit rich UI events. Returns True when the turn should end (intent shown or order placed)."""
    if tool == "propose_intent" and res.get("intent"):
        session.intent = res["intent"]
        INTENT_SESSION[res["intent"]["intent_id"]] = session.id
        _remember_kitchen_version(session, res["intent"]["intent_id"])
        session.emit("intent_proposed", intent=res["intent"])
        return True
    if tool == "search_products" and res.get("excluded") is not None:
        session.excluded[res.get("item_id") or "_"] = res["excluded"]
        session.funnel[res.get("item_id") or "_"] = res.get("funnel") or []
        if not res.get("products") and res.get("item_id"):  # nothing safe: show why, as an empty offer card
            session.emit("offers", request_summary="no product meets every rule", offers=[],
                         recommended_product_id=None, excluded=res["excluded"], intent_id=args.get("intent_id"),
                         item_id=res["item_id"], product_query=res.get("query"), funnel=res.get("funnel") or [],
                         usual_product=res.get("usual_product"))
    if tool == "check_offer" and res.get("offers") is not None:
        iid = res.get("item_id")
        rq = float(res.get("requested_quantity") or 0)
        summary_ = (f"{rq:,.0f}" if rq.is_integer() else f"{rq:,.2f}".rstrip("0")) + (
            f" {unit_word(res.get('requested_unit') or 'SALES_UNIT')}"
                    + (f" {res['product_query']}" if res.get("product_query") else "")
                    + f", delivery by {res.get('requested_date')}")
        session.emit("offers", request_summary=summary_, offers=res["offers"],
                     recommended_product_id=res.get("recommended_product_id"),
                     excluded=session.excluded.get(iid or "_", []), intent_id=args.get("intent_id"), item_id=iid,
                     product_query=res.get("product_query"),
                     funnel=session.funnel.get(iid or "_", []) + (res.get("funnel_tail") or []))
    if tool == "place_order" and res.get("order_id"):
        ORDER_SESSION[res["order_id"]] = session.id
        if res.get("status") == "pending_approval":
            session.emit("approval_required", approval=res["approval"])
            return True
        if res.get("status") == "confirmed":
            session.emit("order_confirmed", order_id=res["order_id"], status="confirmed", lines=res["lines"],
                         total_price=res["total_price"], currency=res["currency"],
                         delivery_date=res["delivery_date"], approved_by=None, tier=res.get("tier"),
                         diff=res.get("diff"), receipt=res.get("receipt"), fingerprint=res.get("fingerprint"),
                         items_open_after=res.get("items_open_after"), intent_id=res.get("intent_id"))
            _after_order(session, res)
            return True
        if res.get("status") == "rejected":
            session.emit("order_rejected", order_id=res["order_id"], tier=res.get("tier", "block"),
                         diff=res.get("diff"), message=res.get("message") or res.get("error"))
    return False


_bg: set[asyncio.Task] = set()


def _after_order(session: Session, order: dict) -> None:
    """Package 2b: tell the UI whether this order counted as clean (event autonomy_progress).
    Package 2a: then propose at most one thing to remember from this order (event context_suggestion)."""
    from . import autonomy, learning

    async def run() -> None:
        await autonomy.after_order(session, order)
        await learning.after_order(session, order["order_id"])

    try:
        task = asyncio.get_running_loop().create_task(run())
    except RuntimeError:
        return
    _bg.add(task)
    task.add_done_callback(_bg.discard)


def _order_message(res: dict) -> str:
    lines = res.get("lines") or [{}]
    line = lines[0]
    what = (f"{line.get('quantity', 0):,.0f} {line.get('sales_unit', '')} of {line.get('name', '')}" if len(lines) == 1
            else f"{len(lines)} lines")
    money = f"{res.get('total_price', 0):,.2f} {res.get('currency', 'EUR')}"
    if res.get("status") == "pending_approval":
        return (f"Order **{res['order_id']}** for {what} ({money}, delivery {res.get('delivery_date')}) "
                f"is waiting for your approval: {res['approval']['reason']}.")
    open_ = res.get("items_open_after") or []
    return (f"Order **{res['order_id']}** confirmed: {what}, {money}, delivery {res.get('delivery_date')}. "
            f"0 deviations from your intent, receipt {res.get('fingerprint')}."
            + (f" Still open: {', '.join(open_)}." if open_ else ""))


# --------------------------------------------------------------------------- after a human decision

def on_decision(order: dict, approval: dict) -> None:
    sid = ORDER_SESSION.get(order["order_id"]) or order.get("session")
    session = SESSIONS.get(sid) if sid else None
    if not session:
        return
    if order["status"] == "confirmed":
        session.emit("order_confirmed", order_id=order["order_id"], status="confirmed", lines=order["lines"],
                     total_price=order["total_price"], currency=order["currency"],
                     delivery_date=order["delivery_date"], approved_by=order.get("approved_by"),
                     tier=order.get("tier"), diff=order.get("diff"), receipt=order.get("receipt"),
                     fingerprint=order.get("fingerprint"), items_open_after=order.get("items_open_after"),
                     intent_id=order.get("intent_id"))
        _after_order(session, order)
        text = (f"Order **{order['order_id']}** confirmed for {order['delivery_date']}. "
                f"Approved and signed by {order.get('approved_by')}, receipt {order.get('fingerprint')}.")
    elif order["status"] == "quote_expired":
        text = f"Approval came after the quote expired. Order **{order['order_id']}** needs a fresh offer."
    else:
        text = f"Order **{order['order_id']}** was rejected by {approval.get('decided_by')}." + (
            f" Comment: {approval['comment']}" if approval.get("comment") else "")
    session.history.append({"role": "assistant", "text": text, "tool_calls": []})
    session.emit("message", role="assistant", text=text)
    session.emit("done")
