"""Valio Aimo agent interface: MCP over Streamable HTTP.

Tools (for any external purchasing agent):
  propose_intent, search_products, check_offer, place_order, get_order_status

Human-only REST endpoints (bearer token, NOT MCP tools, so an agent cannot confirm or approve itself):
  GET  /internal/intents/{intent_id}
  POST /internal/intents/{intent_id}/confirm     buyer signature over intent_hash
  POST /internal/intents/{intent_id}/revise      edits -> new proposed intent
  GET  /internal/approvals
  POST /internal/approvals/{approval_id}         buyer signature over cart_hash
  GET  /internal/orders/{order_id}
  GET  /internal/orders/{order_id}/audit
  GET  /internal/mandate/{buyer_id}

Public, read-only:
  GET  /verify/{order_id}    recompute hashes, check all signatures
  GET  /keys                 Valio Aimo public key
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import config, constraints, diff, intent as intents, policy, trust, valio_api
from .catalog import catalog
from .intent import HardRuleIn
from .store import TransitionError, iso, new_id, now, store

log = logging.getLogger("valio_mcp")
HELSINKI = ZoneInfo("Europe/Helsinki")

mcp = FastMCP(
    "valio-aimo",
    instructions=(
        "Valio Aimo foodservice wholesaler. Flow: 1) propose_intent turns the buyer's request into a fixed "
        "OrderIntent. A human at the buyer must confirm it, you cannot. 2) After confirmation: search_products and "
        "check_offer with the intent_id (quantity, date and hard rules come from the intent). 3) place_order with "
        "one quote_id and the intent_id. Valio Aimo diffs the quote against the confirmed intent and decides the "
        "tier: auto (confirmed), ask (pending_approval, a human signs the exact cart) or block (rejected, rule "
        "shown). One confirmed intent buys exactly one order. Prices are net of VAT, in EUR."
    ),
    host=config.MCP_HOST,
    port=config.MCP_PORT,
    stateless_http=True,  # every request carries its own identity headers, scales horizontally
)


# --------------------------------------------------------------------------- helpers

def _caller(ctx: Context | None) -> tuple[str | None, str]:
    """Agent session and buyer id from HTTP headers set by the buyer's MCP client."""
    try:
        h = ctx.request_context.request.headers  # type: ignore[union-attr]
        return h.get("x-agent-session"), h.get("x-buyer-id") or "default"
    except Exception:
        return None, "default"


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=HELSINKI)


def _date(value: str | None) -> str | None:
    return value[:10] if value else None


def _snap(content: float) -> float:
    """Comparison prices are rounded to cents, so 10.97 / 2.19 gives 5.009 L instead of 5 L.
    Snap to the nearest 0.05 when that is within 1 percent."""
    nice = round(content * 20) / 20
    return nice if nice and abs(nice - content) / content < 0.01 else round(content, 4)


def _round_lot(qty: float, lot: float, sales_unit: str) -> float:
    """Round up to the lot size. Most products list 0.01, which we read as 'whole units' except for
    goods sold by weight (KG), where fractions are real."""
    lot = lot or 1
    if sales_unit != "KG":
        lot = max(lot, 1.0)
    return round(math.ceil(round(qty / lot, 6)) * lot, 2)


# --------------------------------------------------------------------------- tools

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False,
                                      openWorldHint=False))
async def propose_intent(
    original_request: Annotated[str, Field(description="The buyer's message, verbatim")],
    product_query: Annotated[str, Field(description="Short product description, English, e.g. 'lactose-free milk'")],
    quantity: Annotated[float, Field(gt=0, description="Amount exactly as the buyer said it")],
    quantity_unit: Annotated[Literal["L", "KG", "SALES_UNIT"], Field(description="Unit the buyer used")],
    deliver_by: Annotated[str, Field(description="Latest delivery date, YYYY-MM-DD")],
    hard_rules: Annotated[list[HardRuleIn], Field(description="Must-haves: lactose_free, exclude_allergen, origin")] = [],
    optimise: Annotated[Literal["price", "reliability", "co2"], Field(description="What to optimise among offers that meet every rule")] = "price",
    origin_preference: Annotated[str | None, Field(description="Soft preference only, ISO alpha-2")] = None,
    budget_max_total: Annotated[float | None, Field(description="Only if the buyer named a budget")] = None,
    max_over_pct: Annotated[float, Field(ge=0, le=20, description="Allowed overshoot when rounding to whole packs")] = 2.0,
    allow_under: Annotated[bool, Field(description="Only true if the buyer accepts less than asked")] = False,
    open_questions: Annotated[list[str], Field(description="Anything ambiguous you could not resolve, one question each")] = [],
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Turn the buyer's request into an OrderIntent and show it back. Call this FIRST, before searching.
    Fill only what the buyer said. Never guess an allergy: if 'non-dairy' or similar is unclear, add an open question.
    Valio Aimo adds the buyer's mandatory rules, cross-checks numbers against the original text and returns
    a read-back and questions. Then STOP: a human must confirm the intent. You cannot confirm it."""
    session, buyer_id = _caller(ctx)
    mandate = policy.buyer_policy(buyer_id)
    it = intents.build(buyer_id=mandate["buyer_id"], mandate=mandate, session=session,
                       original_request=original_request, product_query=product_query, quantity=quantity,
                       quantity_unit=quantity_unit, deliver_by=deliver_by,
                       hard_rules=[r.model_dump() if hasattr(r, 'model_dump') else HardRuleIn(**r).model_dump()
                                   for r in hard_rules], optimise=optimise,
                       origin_preference=origin_preference, budget_max_total=budget_max_total,
                       max_over_pct=max_over_pct, allow_under=allow_under, agent_questions=open_questions)
    store().save_intent(it)
    store().audit("buyer_agent", "propose_intent", f"Proposed intent: {it['readback']}", session=session,
                  data={"intent_id": it["intent_id"], "intent_hash": it["intent_hash"], "questions": it["questions"]})
    return {"intent": intents.public(it),
            "next": "Wait. The buyer must answer the questions and confirm. Do not call more tools in this turn."
            if it["questions"] else "Wait for the buyer to confirm. Do not call more tools in this turn."}


def _confirmed_intent(intent_id: str | None) -> tuple[dict | None, str | None]:
    if not intent_id:
        return None, None
    it = store().get_intent(intent_id)
    if not it:
        return None, f"unknown intent_id {intent_id}"
    if it["status"] == "proposed":
        return None, "intent not confirmed by the buyer yet"
    if it["status"] in ("superseded", "expired") or intents.is_expired(it):
        return None, f"intent is {it['status'] if it['status'] != 'confirmed' else 'expired'}, propose a new one"
    return it, None


class SearchFilters(BaseModel):
    lactose_free: bool | None = Field(None, description="Only lactose-free products")
    exclude_allergens: list[str] | None = Field(
        None, description="Drop products that contain any of these allergens, e.g. ['gluten','egg']")
    category: str | None = Field(None, description="Valio category code")
    country_of_origin: str | None = Field(None, description="ISO alpha-2, e.g. 'fi'")


@mcp.tool(annotations=READ_ONLY)
async def search_products(
    query: Annotated[str, Field(description="What the buyer needs, e.g. 'lactose-free milk'. English or Finnish.")],
    intent_id: Annotated[str | None, Field(description="Confirmed intent: its hard rules are applied from data")] = None,
    filters: SearchFilters | None = None,
    limit: Annotated[int, Field(ge=1, le=25)] = 10,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Search Valio Aimo's static product catalog. With intent_id, every hard rule is checked from GS1 data,
    claims and an ingredient scan: only products proven safe are returned, the rest is listed in `excluded`
    with the reason. No prices here: use check_offer."""
    session, _ = _caller(ctx)
    f = filters or SearchFilters()
    it, err = _confirmed_intent(intent_id)
    if err:
        return {"error": err}
    pool, total = catalog().search(query, lactose_free=f.lactose_free, exclude_allergens=f.exclude_allergens,
                                   category=f.category, country_of_origin=f.country_of_origin,
                                   limit=200 if it else limit)
    hits, excluded = pool, []
    if it:
        hits = []
        for p in pool:
            evs = constraints.evaluate_all(p, it["hard_rules"])
            bad = [e for e in evs if e["verdict"] != "safe" and not e["rule"].startswith("origin")]
            if bad:
                excluded.append({"product_id": p.id, "name": p.name, "reason": constraints.one_line(bad[0]),
                                 "verdict": bad[0]["verdict"]})
            elif len(hits) < limit:
                hits.append(p)
    result = {"query": query, "intent_id": intent_id, "filters": f.model_dump(exclude_none=True),
              "total_matches": total, "returned": len(hits),
              "products": [p.summary() | ({"evidence": constraints.evaluate_all(p, it["hard_rules"])} if it else {})
                           for p in hits],
              "excluded": excluded[:12], "excluded_count": len(excluded),
              "catalog_source": catalog().source}
    store().audit("buyer_agent", "search_products",
                  f"Searched catalog for '{query}': {total} matches, returned {len(hits)}"
                  + (f", {len(excluded)} excluded by hard rules" if it else ""),
                  session=session, data={"filters": result["filters"], "product_ids": [p.id for p in hits],
                                         "intent_id": intent_id, "excluded_count": len(excluded)})
    return result


@mcp.tool(annotations=READ_ONLY)
async def check_offer(
    product_ids: Annotated[list[str], Field(min_length=1, max_length=25, description="Shortlisted product ids")],
    intent_id: Annotated[str | None, Field(description="Confirmed intent. Quantity, unit and date are taken from it.")] = None,
    requested_date: Annotated[str | None, Field(description="Desired delivery date, YYYY-MM-DD (ignored with intent_id)")] = None,
    quantity: Annotated[float | None, Field(gt=0, description="Amount the buyer needs (ignored with intent_id)")] = None,
    quantity_unit: Annotated[Literal["L", "KG", "SALES_UNIT"], Field(
        description="Unit of `quantity`. L or KG are converted to sales units per product.")] = "SALES_UNIT",
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Get binding quotes: live price and availability from Valio Aimo for each product on the date.
    Each offer has a quote_id that fixes the price until quote_expires_at and is signed by Valio Aimo.
    With intent_id, each offer carries the evidence for every hard rule. Offers are sorted best first."""
    session, buyer_id = _caller(ctx)
    it, err = _confirmed_intent(intent_id)
    if err:
        return {"error": err}
    if it:
        quantity, quantity_unit = it["quantity"]["amount"], it["quantity"]["unit"]
        requested_date = it["deliver_by"]
    if not quantity or not requested_date:
        return {"error": "quantity and requested_date are required without intent_id"}
    ids = list(dict.fromkeys(str(p) for p in product_ids))
    prices = await valio_api.price(ids, requested_date)
    avail = await valio_api.availability(ids, requested_date)
    cat = catalog()
    offers, skipped = [], []

    for pid in ids:
        prod, pr, av = cat.get(pid), prices.get(pid), avail.get(pid)
        if not pr or pr.get("error") or not pr.get("catalogPrice"):
            skipped.append({"product_id": pid, "reason": "no price"})
            continue
        unit_price = float(pr["catalogPrice"]["value"])
        cmp_price = (pr.get("comparisonPrice") or {}).get("value")
        cmp_unit = pr.get("comparisonUnit")
        sales_unit = (prod.sales_unit if prod else None) or "ST"
        lot = prod.lot_size if prod else 1.0

        # Content per sales unit derived from the price API itself: catalog / comparison price.
        content = _snap(unit_price / cmp_price) if cmp_price else None
        if quantity_unit == "SALES_UNIT":
            qty = _round_lot(quantity, lot, sales_unit)
        elif cmp_unit == quantity_unit and content:
            qty = _round_lot(quantity / content, lot, sales_unit)
        else:
            skipped.append({"product_id": pid, "reason": f"cannot convert {quantity_unit} to {sales_unit}"})
            continue

        line = (av or {}).get("scheduleLines") or []
        sl = line[0] if line else {}
        available_amount = float(sl.get("availableAmount", 0) or 0)
        available_date = _date(sl.get("availableDate"))
        sales_block = bool((av or {}).get("salesBlock") or sl.get("salesBlock"))
        closing = _parse_ts((av or {}).get("closingTime"))
        cutoff_passed = bool(closing and closing <= now())
        issues = []
        if sales_block:
            issues.append("sales blocked")
        if not sl:
            issues.append("no delivery date available")
        elif available_amount < qty:
            issues.append(f"only {available_amount:g} {sales_unit} available")
        if available_date and available_date > requested_date:
            issues.append(f"earliest delivery {available_date}")
        if cutoff_passed:
            issues.append(f"order cut-off {closing.isoformat()} has passed")
        can_fulfil = not issues
        ttl = now() + timedelta(minutes=config.QUOTE_TTL_MINUTES)
        expires = min(closing, ttl) if closing and closing > now() else ttl
        quote_id = new_id("q")
        offer = {
            "product_id": pid,
            "name": prod.name if prod else pid,
            "rank": None,
            "is_substitute": False,
            "substitute_reason": None,
            "sales_unit": sales_unit,
            "quantity": qty,
            "requested_quantity": quantity,
            "requested_unit": quantity_unit,
            "content_per_sales_unit": content,
            "unit_price": unit_price,
            "list_price": (pr.get("listPrice") or {}).get("value"),
            "total_price": round(unit_price * qty, 2),
            "currency": pr.get("_currency") or pr["catalogPrice"].get("currency") or "EUR",
            "comparison_price": cmp_price,
            "comparison_unit": cmp_unit,
            "net_price_found": pr.get("netPriceFound"),
            "available_amount": available_amount,
            "available_date": available_date,
            "requested_date": requested_date,
            "closing_time": closing.isoformat() if closing else None,
            "logistic_model": ((av or {}).get("logisticModel") or "").lower() or None,
            "sales_block": sales_block,
            "can_fulfil": can_fulfil,
            "fulfil_issues": issues,
            "order_cutoff_passed": cutoff_passed,
            "delivery_reliability": prod.delivery_reliability if prod else None,
            "co2_kg_per_kg": prod.co2_kg_per_kg if prod else None,
            "certificates": prod.labels if prod else [],
            "allergens": prod.allergens_contains if prod else [],
            "category": prod.category if prod else None,
            "quote_id": quote_id,
            "quote_expires_at": iso(expires),
            "intent_id": intent_id,
        }
        signed = trust.valio_sign_obj(diff.quote_core(offer))
        offer["provenance"] = {"source": f"Valio Aimo API {config.VALIO_API_BASE}/api/price + /api/availability",
                               "fetched_at": iso(), "valid_until": offer["quote_expires_at"], **signed}
        if it and prod:
            offer["evidence"] = constraints.evaluate_all(prod, it["hard_rules"])
            if any(e["verdict"] != "safe" and not e["rule"].startswith("origin") for e in offer["evidence"]):
                offer["can_fulfil"] = False
                offer["fulfil_issues"] = issues + ["breaks a hard rule of the intent"]
        offers.append(offer)

    # Best first: can deliver in full on time, then the intent's preference (default: cheapest total).
    opt = (it or {}).get("preferences", {}).get("optimise", "price")
    pref = {"price": lambda o: (o["total_price"], -(o["delivery_reliability"] or 0)),
            "reliability": lambda o: (-(o["delivery_reliability"] or 0), o["total_price"]),
            "co2": lambda o: (o["co2_kg_per_kg"] if o["co2_kg_per_kg"] is not None else 1e9, o["total_price"])}[opt]
    offers.sort(key=lambda o: (not o["can_fulfil"], *pref(o)))
    for o, rank in zip([o for o in offers if o["can_fulfil"]], ["best", "better", "good"]):
        o["rank"] = rank
    for o in offers:
        store().save_quote(o["quote_id"], session, buyer_id, o, o["quote_expires_at"])
    recommended = next((o["product_id"] for o in offers if o["can_fulfil"]), None)
    n_full = sum(o["can_fulfil"] for o in offers)

    store().audit("valio_mcp", "check_offer",  # noqa: E501
                  f"Priced and checked availability for {len(ids)} products for {requested_date}: "
                  f"{n_full} can deliver {quantity:g} {quantity_unit} in full",
                  session=session,
                  data={"quotes": [o["quote_id"] for o in offers], "recommended": recommended, "skipped": skipped,
                        "intent_id": intent_id})
    return {"requested_date": requested_date, "requested_quantity": quantity, "requested_unit": quantity_unit,
            "offers": offers, "recommended_product_id": recommended, "skipped": skipped,
                "note": "unit_price and total_price are net of VAT. available_amount 999999 or more means stock is "
                    "not limited or unknown (terminal = supplier warehouse). fulfil_issues explains why an offer "
                    "cannot deliver in full on time."}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True,
                                      openWorldHint=False))
async def place_order(
    quote_id: Annotated[str, Field(description="quote_id from check_offer")],
    intent_id: Annotated[str, Field(description="The confirmed intent this order fulfils")],
    reason: Annotated[str, Field(description="Why this offer was chosen, shown to the human approver and in the audit log")],
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Commit to a quote for a confirmed intent. Valio Aimo diffs the quote against the intent field by field
    (product, hard rules, quantity, date, budget, mandate) and returns the tier:
    'confirmed' (auto), 'pending_approval' (ask: wait for a human, you cannot approve) or 'rejected' (block).
    Idempotent: one confirmed intent buys exactly one order, repeating the call returns the same order."""
    session, buyer_id = _caller(ctx)
    s = store()
    it = s.get_intent(intent_id)
    if not it:
        return {"status": "rejected", "error": f"unknown intent_id {intent_id}"}
    if it["status"] == "consumed":
        prev = s.get_order(it["order_id"]) or {}
        prev.pop("_session", None)
        if prev.get("quote_id") == quote_id:
            return _order_result(prev) | {"idempotent_replay": True}
        return {"status": "rejected", "error": f"intent already used by order {it['order_id']}. "
                                               f"One intent buys exactly one order."}
    if it["status"] != "confirmed" or intents.is_expired(it):
        return {"status": "rejected", "error": f"intent is {it['status']}, the buyer must confirm a current intent"}
    quote = s.get_quote(quote_id)
    if not quote:
        s.audit("buyer_agent", "place_order", f"Tried to order unknown quote {quote_id}", session=session, reason=reason)
        return {"status": "rejected", "error": f"unknown quote_id {quote_id}"}

    line = quote["data"]
    mandate = policy.buyer_policy(it["buyer_id"])
    order_id = new_id("ord")
    s.audit("buyer_agent", "place_order",
            f"Requested order for {line['quantity']:g} {line['sales_unit']} of {line['product_id']} "
            f"({line['name']}) at {line['unit_price']:.2f} {line['currency']}",
            session=session, order_id=order_id, reason=reason, data={"quote_id": quote_id, "intent_id": intent_id})

    d = diff.compute(it, quote, mandate)
    tier = policy.tier(d["checks"])
    status = policy.STATUS[tier]
    the_cart = diff.cart(it["intent_hash"], line)
    cart_hash = trust.digest(the_cart)
    public_diff = {"deviations": d["deviations"], "checks": d["checks"]}
    failed = [c for c in d["checks"] if not c["ok"]]
    order = {"order_id": order_id, "quote_id": quote_id, "intent_id": intent_id, "status": status, "tier": tier,
             "buyer_id": it["buyer_id"], "lines": [line], "total_price": line["total_price"],
             "currency": line["currency"], "delivery_date": the_cart["delivery_date"], "diff": public_diff,
             "cart": the_cart, "cart_hash": cart_hash, "fingerprint": trust.fingerprint(cart_hash),
             "intent": intents.public(it), "agent_rationale": reason, "approved_by": None, "created_at": iso(),
             "receipt": None}

    if tier == "block":
        order["message"] = "Blocked: " + "; ".join(f"{c['field']} ({c['intended']}): {c['detail']}" for c in failed
                                                   if c["severity"] == "block")
        s.audit("policy_engine", "policy_check", order["message"], session=session, order_id=order_id,
                data={"tier": tier, "diff": public_diff, "intent_id": intent_id})
        s.save_order(order, session, it["buyer_id"])
        return _order_result(order)

    it |= {"status": "consumed", "order_id": order_id}
    s.update_intent(it)
    if tier == "ask":
        appr = {"approval_id": new_id("appr"), "order_id": order_id, "status": "pending",
                "reason": "; ".join(f"{c['field']}: {c['detail']}" for c in failed), "tier": tier,
                "diff": public_diff, "cart": the_cart, "cart_hash": cart_hash, "fingerprint": order["fingerprint"],
                "intent": intents.public(it), "lines": order["lines"], "total_price": order["total_price"],
                "currency": order["currency"], "agent_rationale": reason, "created_at": iso(),
                "decided_by": None, "decided_at": None, "comment": None,
                "policy_checks": [{"rule": c["field"], "passed": c["ok"], "detail": c["detail"]} for c in d["checks"]]}
        order["approval_id"] = appr["approval_id"]
        s.save_approval(appr)
        s.audit("policy_engine", "policy_check",
                f"{d['deviations']} deviation(s) a human may accept: {', '.join(c['field'] for c in failed)}. "
                f"Sent to human approval", session=session, order_id=order_id,
                data={"approval_id": appr["approval_id"], "tier": tier, "diff": public_diff, "intent_id": intent_id})
    else:
        s.audit("policy_engine", "policy_check", "0 deviations from the confirmed intent, within the mandate",
                session=session, order_id=order_id, data={"tier": tier, "diff": public_diff, "intent_id": intent_id})
        order["receipt"] = _receipt(order, it, approved_by=None, buyer_cart_signature=None)
        s.audit("valio_mcp", "order_confirmed",
                f"Order {order_id} confirmed for delivery {order['delivery_date']}, receipt {order['fingerprint']}",
                session=session, order_id=order_id,
                data={"total_price": order["total_price"], "currency": order["currency"], "cart_hash": cart_hash})
    s.save_order(order, session, it["buyer_id"])
    return _order_result(order)


def _order_result(order: dict) -> dict:
    out = {k: order.get(k) for k in ("status", "tier", "order_id", "intent_id", "total_price", "currency",
                                     "delivery_date", "lines", "diff", "cart_hash", "fingerprint", "receipt",
                                     "message", "approval_id")}
    out["policy_checks"] = [{"rule": c["field"], "passed": c["ok"], "detail": c["detail"]}
                            for c in (order.get("diff") or {}).get("checks", [])]
    if order.get("status") == "pending_approval":
        out["approval"] = store().get_approval(order["approval_id"])
        out["message"] = "Waiting for human approval at the buyer. Do not retry or re-order."
    return {k: v for k, v in out.items() if v is not None}


def _receipt(order: dict, it: dict, *, approved_by: str | None, buyer_cart_signature: str | None) -> dict:
    confirmed_at = iso()
    body = {"order_id": order["order_id"], "intent_hash": it["intent_hash"], "cart_hash": order["cart_hash"],
            "status": "confirmed", "tier": order["tier"], "approved_by": approved_by, "confirmed_at": confirmed_at}
    signed = trust.valio_sign_obj(body)
    return body | {"buyer_intent_signature": it["buyer_signature"], "buyer_cart_signature": buyer_cart_signature,
                   "receipt_hash": signed["hash"], "valio_signature": signed["signature"],
                   "valio_key_id": signed["key_id"], "fingerprint": trust.fingerprint(order["cart_hash"]),
                   "verify_url": f"{config.PUBLIC_BASE_URL}/verify/{order['order_id']}"}


@mcp.tool(annotations=READ_ONLY)
async def get_order_status(order_id: str, ctx: Context | None = None) -> dict[str, Any]:
    """Current status of an order and its approval."""
    order = store().get_order(order_id)
    if not order:
        return {"error": f"unknown order_id {order_id}"}
    order.pop("_session", None)
    appr = store().get_approval(order["approval_id"]) if order.get("approval_id") else None
    return {"order": order, "approval": appr}


@mcp.resource("valio://policy/{buyer_id}", mime_type="application/json")
def get_policy(buyer_id: str) -> dict[str, Any]:
    """The mandate Valio Aimo enforces for this buyer on every place_order, with its hash."""
    return policy.buyer_policy(buyer_id)


# --------------------------------------------------------------------------- human-only REST

def _authorised(request: Request) -> bool:
    return request.headers.get("authorization", "") == f"Bearer {config.APPROVER_TOKEN}"


def _deny() -> JSONResponse:
    return JSONResponse({"error": "approver token required"}, status_code=401)


@mcp.custom_route("/internal/approvals", methods=["GET"])
async def list_approvals(request: Request) -> JSONResponse:
    if not _authorised(request):
        return _deny()
    return JSONResponse(store().list_approvals(request.query_params.get("status")))


@mcp.custom_route("/internal/approvals/{approval_id}", methods=["POST"])
async def decide_approval(request: Request) -> JSONResponse:
    """Human decision. Approve requires the cart_hash the human was shown and the buyer's signature over it:
    what you see is what you sign. If the cart changed in between, the hashes differ and nothing happens."""
    if not _authorised(request):
        return _deny()
    s = store()
    appr = s.get_approval(request.path_params["approval_id"])
    if not appr:
        return JSONResponse({"error": "not found"}, status_code=404)
    if appr["status"] != "pending":
        return JSONResponse({"error": f"already {appr['status']}", "approval": appr}, status_code=409)
    body = await request.json()
    decision = body.get("decision")
    if decision not in ("approve", "reject"):
        return JSONResponse({"error": "decision must be approve or reject"}, status_code=422)
    who = body.get("decided_by") or "buyer.human"
    order = s.get_order(appr["order_id"])
    session = order.pop("_session", None)
    it = s.get_intent(order["intent_id"])

    if decision == "approve":
        if body.get("cart_hash") != order["cart_hash"]:
            return JSONResponse({"error": "cart_hash does not match the cart on file. Reload and check again.",
                                 "expected_fingerprint": order["fingerprint"]}, status_code=409)
        if not trust.verify(trust.buyer_public(it["buyer_id"]), order["cart_hash"], body.get("signature")):
            return JSONResponse({"error": "buyer signature over cart_hash is invalid"}, status_code=403)

    appr |= {"status": "approved" if decision == "approve" else "rejected", "decided_by": who,
             "decided_at": iso(), "comment": body.get("comment")}
    s.update_approval(appr)
    s.audit("human", decision, f"{who} {appr['status']} the order"
            + (f", signed cart {order['fingerprint']}" if decision == "approve" else ""),
            session=session, order_id=order["order_id"], reason=body.get("comment"),
            data={"cart_hash": order["cart_hash"]} if decision == "approve" else {})

    if decision == "approve":
        quote = s.get_quote(order["quote_id"])
        if quote and now() < datetime.fromisoformat(quote["expires_at"]):
            order |= {"status": "confirmed", "approved_by": who}
            order["receipt"] = _receipt(order, it, approved_by=who, buyer_cart_signature=body.get("signature"))
            s.audit("valio_mcp", "order_confirmed",
                    f"Order {order['order_id']} confirmed for delivery {order['delivery_date']}, "
                    f"receipt {order['fingerprint']}", session=session, order_id=order["order_id"],
                    data={"total_price": order["total_price"], "currency": order["currency"], "approved_by": who})
        else:
            order |= {"status": "quote_expired", "approved_by": who}
            it["status"] = "confirmed"
            it["order_id"] = None
            s.update_intent(it)
            s.audit("valio_mcp", "quote_expired", "Approved after the quote expired. A new offer is needed",
                    session=session, order_id=order["order_id"])
    else:
        order["status"] = "rejected"
        it["status"] = "confirmed"  # the buyer's intent stands, only this cart was refused
        it["order_id"] = None
        s.update_intent(it)
    s.update_order(order)
    return JSONResponse({"approval": appr, "order": order})


# --------------------------------------------------------------------------- intents (human only)

@mcp.custom_route("/internal/intents/{intent_id}", methods=["GET"])
async def get_intent(request: Request) -> JSONResponse:
    if not _authorised(request):
        return _deny()
    it = store().get_intent(request.path_params["intent_id"])
    return JSONResponse(intents.public(it)) if it else JSONResponse({"error": "not found"}, status_code=404)


@mcp.custom_route("/internal/intents/{intent_id}/confirm", methods=["POST"])
async def confirm_intent(request: Request) -> JSONResponse:
    """The buyer confirms exactly the intent they saw: hash must match, signature must verify."""
    if not _authorised(request):
        return _deny()
    s = store()
    it = s.get_intent(request.path_params["intent_id"])
    if not it:
        return JSONResponse({"error": "not found"}, status_code=404)
    body = await request.json()
    if it["status"] != "proposed":
        return JSONResponse({"error": f"intent is {it['status']}", "intent": intents.public(it)}, status_code=409)
    if it["questions"]:
        return JSONResponse({"error": "open questions must be answered first", "questions": it["questions"]},
                            status_code=409)
    if body.get("intent_hash") != it["intent_hash"] or intents.intent_hash(it) != it["intent_hash"]:
        return JSONResponse({"error": "intent_hash does not match. The intent changed, check it again."},
                            status_code=409)
    if not trust.verify(trust.buyer_public(it["buyer_id"]), it["intent_hash"], body.get("signature")):
        return JSONResponse({"error": "buyer signature over intent_hash is invalid"}, status_code=403)
    it |= {"status": "confirmed", "buyer_signature": body["signature"],
           "confirmed_by": body.get("confirmed_by") or "buyer.human", "confirmed_at": iso()}
    s.update_intent(it)
    s.audit("human", "confirm_intent", f"{it['confirmed_by']} confirmed and signed the intent "
                                       f"{trust.fingerprint(it['intent_hash'])}: {it['readback']}",
            session=it.get("session"), data={"intent_id": it["intent_id"], "intent_hash": it["intent_hash"]})
    return JSONResponse(intents.public(it))


@mcp.custom_route("/internal/intents/{intent_id}/revise", methods=["POST"])
async def revise_intent(request: Request) -> JSONResponse:
    """Human edits a proposed intent (or answers its questions). Returns a NEW proposed intent with a new hash,
    which the human then confirms. The old one is superseded."""
    if not _authorised(request):
        return _deny()
    s = store()
    old = s.get_intent(request.path_params["intent_id"])
    if not old:
        return JSONResponse({"error": "not found"}, status_code=404)
    if old["status"] != "proposed":
        return JSONResponse({"error": f"intent is {old['status']}"}, status_code=409)
    edits = (await request.json()).get("edits") or {}
    mandate = policy.buyer_policy(old["buyer_id"])
    try:
        new = intents.build(buyer_id=old["buyer_id"], mandate=mandate, session=old.get("session"),
                            **intents.apply_edits(old, edits))
    except (KeyError, ValueError, TypeError) as e:
        return JSONResponse({"error": f"invalid edits: {e}"}, status_code=422)
    old["status"] = "superseded"
    old["superseded_by"] = new["intent_id"]
    s.update_intent(old)
    new["revises"] = old["intent_id"]
    s.save_intent(new)
    s.audit("human", "revise_intent", f"Buyer edited the intent: {new['readback']}", session=new.get("session"),
            data={"intent_id": new["intent_id"], "revises": old["intent_id"], "edits": edits})
    return JSONResponse(intents.public(new))


@mcp.custom_route("/internal/mandate/{buyer_id}", methods=["GET"])
async def get_mandate(request: Request) -> JSONResponse:
    if not _authorised(request):
        return _deny()
    return JSONResponse(policy.buyer_policy(request.path_params["buyer_id"]))


# --------------------------------------------------------------------------- public verification

@mcp.custom_route("/keys", methods=["GET"])
async def keys(_: Request) -> JSONResponse:
    return JSONResponse({"valio_aimo": trust.valio_public()})


@mcp.custom_route("/verify/{order_id}", methods=["GET"])
async def verify_order(request: Request) -> JSONResponse:
    return JSONResponse(verify(request.path_params["order_id"]))


def verify(order_id: str) -> dict:
    """Recompute every hash from stored content and check every signature. Anyone can run this."""
    s = store()
    order = s.get_order(order_id)
    if not order:
        return {"order_id": order_id, "valid": False, "checks": [{"name": "order", "ok": False, "detail": "not found"}]}
    it = s.get_intent(order["intent_id"]) or {}
    rc = order.get("receipt") or {}
    checks = []

    def add(name: str, ok: bool, detail: str):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    ih = intents.intent_hash(it) if it else None
    add("intent_hash", ih and ih == it.get("intent_hash") == rc.get("intent_hash"),
        f"Recomputed {trust.fingerprint(ih or '')} from the stored intent (RFC 8785 + SHA-256)")
    add("buyer_intent_signature", it and trust.verify(trust.buyer_public(it["buyer_id"]), it["intent_hash"],
                                                     it.get("buyer_signature")),
        f"Ed25519 signature by {it.get('confirmed_by')} over the intent hash")
    ch = trust.digest(order["cart"])
    add("cart_hash", ch == order["cart_hash"] == rc.get("cart_hash"),
        f"Recomputed {trust.fingerprint(ch)} from the ordered cart")
    add("cart_bound_to_intent", order["cart"]["intent_hash"] == it.get("intent_hash"),
        "The cart names the confirmed intent")
    line = order["lines"][0]
    prov = line.get("provenance") or {}
    add("quote_signature", trust.verify(trust.valio_key().public_key(), prov.get("hash", ""), prov.get("signature"))
        and prov.get("hash") == trust.digest(diff.quote_core(line)),
        "Price and availability signed by Valio Aimo when quoted")
    if order.get("tier") == "ask":
        add("buyer_cart_signature", trust.verify(trust.buyer_public(order["buyer_id"]), order["cart_hash"],
                                                 rc.get("buyer_cart_signature")),
            f"{order.get('approved_by')} signed exactly this cart")
    else:
        add("zero_deviations", order.get("diff", {}).get("deviations") == 0,
            "Autonomous order: 0 deviations from the intent")
    body = {k: rc.get(k) for k in ("order_id", "intent_hash", "cart_hash", "status", "tier", "approved_by",
                                   "confirmed_at")}
    add("valio_receipt_signature", rc and trust.digest(body) == rc.get("receipt_hash")
        and trust.verify(trust.valio_key().public_key(), rc.get("receipt_hash", ""), rc.get("valio_signature")),
        "Receipt signed by Valio Aimo")
    return {"order_id": order_id, "status": order["status"], "fingerprint": order.get("fingerprint"),
            "valid": order["status"] == "confirmed" and all(c["ok"] for c in checks), "checks": checks,
            "keys": {"valio_aimo": trust.valio_public(),
                     "buyer": {"key_id": trust.key_id(trust.buyer_public(order["buyer_id"])),
                               "public_key": trust.public_b64(trust.buyer_public(order["buyer_id"]))}}}


@mcp.custom_route("/internal/orders/{order_id}", methods=["GET"])
async def get_order(request: Request) -> JSONResponse:
    if not _authorised(request):
        return _deny()
    order = store().get_order(request.path_params["order_id"])
    if not order:
        return JSONResponse({"error": "not found"}, status_code=404)
    order["session"] = order.pop("_session", None)
    return JSONResponse(order)


@mcp.custom_route("/internal/orders/{order_id}/audit", methods=["GET"])
async def order_audit(request: Request) -> JSONResponse:
    if not _authorised(request):
        return _deny()
    return JSONResponse(store().audit_for_order(request.path_params["order_id"]))


@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "products": len(catalog().products), "catalog": catalog().source,
                         "valio_api": config.VALIO_API_BASE})


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    catalog()  # load eagerly so the first tool call is fast
    store()
    log.info("Valio Aimo signing key %s", trust.valio_public()["key_id"])
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
