"""Valio Aimo agent interface: MCP over Streamable HTTP.

Tools (for any external purchasing agent):
  search_products, check_offer, place_order, get_order_status

Human-only REST endpoints (bearer token, NOT MCP tools, so an agent cannot approve itself):
  GET  /internal/approvals
  POST /internal/approvals/{approval_id}
  GET  /internal/orders/{order_id}
  GET  /internal/orders/{order_id}/audit
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import config, policy, valio_api
from .catalog import catalog
from .store import iso, new_id, now, store

log = logging.getLogger("valio_mcp")
HELSINKI = ZoneInfo("Europe/Helsinki")

mcp = FastMCP(
    "valio-aimo",
    instructions=(
        "Valio Aimo foodservice wholesaler. Typical flow: search_products on the static catalog, "
        "then check_offer for a short list (max ~10 products) to get binding quotes with price and "
        "availability for a delivery date, then place_order with one quote_id. place_order returns "
        "'confirmed', 'pending_approval' (a human at the buyer must approve) or 'rejected'. "
        "Agents cannot approve orders. Prices are net of VAT, in EUR."
    ),
    host=config.MCP_HOST,
    port=config.MCP_PORT,
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


def _round_lot(qty: float, lot: float) -> float:
    lot = lot or 1
    return math.ceil(round(qty / lot, 6)) * lot


# --------------------------------------------------------------------------- tools

class SearchFilters(BaseModel):
    lactose_free: bool | None = Field(None, description="Only lactose-free products")
    exclude_allergens: list[str] | None = Field(
        None, description="Drop products that contain any of these allergens, e.g. ['gluten','egg']")
    category: str | None = Field(None, description="Valio category code")
    country_of_origin: str | None = Field(None, description="ISO alpha-2, e.g. 'fi'")


@mcp.tool()
async def search_products(
    query: Annotated[str, Field(description="What the buyer needs, e.g. 'lactose-free milk'. English or Finnish.")],
    filters: SearchFilters | None = None,
    limit: Annotated[int, Field(ge=1, le=25)] = 10,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Search Valio Aimo's static product catalog. Returns candidate products with labels, allergens,
    historic delivery reliability and category CO2 estimate. No prices here: use check_offer."""
    session, _ = _caller(ctx)
    f = filters or SearchFilters()
    hits, total = catalog().search(query, lactose_free=f.lactose_free, exclude_allergens=f.exclude_allergens,
                                   category=f.category, country_of_origin=f.country_of_origin, limit=limit)
    result = {"query": query, "filters": f.model_dump(exclude_none=True), "total_matches": total,
              "returned": len(hits), "products": [p.summary() for p in hits],
              "catalog_source": catalog().source}
    store().audit("buyer_agent", "search_products",
                  f"Searched catalog for '{query}': {total} matches, returned {len(hits)}",
                  session=session, data={"filters": result["filters"], "product_ids": [p.id for p in hits]})
    return result


@mcp.tool()
async def check_offer(
    product_ids: Annotated[list[str], Field(min_length=1, max_length=25, description="Shortlisted product ids")],
    requested_date: Annotated[str, Field(description="Desired delivery date, YYYY-MM-DD")],
    quantity: Annotated[float, Field(gt=0, description="Amount the buyer needs")],
    quantity_unit: Annotated[Literal["L", "KG", "SALES_UNIT"], Field(
        description="Unit of `quantity`. L or KG are converted to sales units per product.")] = "SALES_UNIT",
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Get binding quotes: live price and availability from Valio Aimo for each product on the date.
    Each offer has a quote_id that fixes the price until quote_expires_at. Offers are sorted best first."""
    session, buyer_id = _caller(ctx)
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
        content = round(unit_price / cmp_price, 4) if cmp_price else None
        if quantity_unit == "SALES_UNIT":
            qty = _round_lot(quantity, lot)
        elif cmp_unit == quantity_unit and content:
            qty = _round_lot(quantity / content, lot)
        else:
            skipped.append({"product_id": pid, "reason": f"cannot convert {quantity_unit} to {sales_unit}"})
            continue

        line = (av or {}).get("scheduleLines") or []
        sl = line[0] if line else {}
        available_amount = float(sl.get("availableAmount", 0) or 0)
        available_date = _date(sl.get("availableDate"))
        sales_block = bool((av or {}).get("salesBlock") or sl.get("salesBlock"))
        can_fulfil = (not sales_block and bool(sl) and available_amount >= qty
                      and available_date is not None and available_date <= requested_date)

        closing = _parse_ts((av or {}).get("closingTime"))
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
            "delivery_reliability": prod.delivery_reliability if prod else None,
            "co2_kg_per_kg": prod.co2_kg_per_kg if prod else None,
            "certificates": prod.labels if prod else [],
            "allergens": prod.allergens_contains if prod else [],
            "quote_id": quote_id,
            "quote_expires_at": iso(expires),
        }
        store().save_quote(quote_id, session, buyer_id, offer, iso(expires))
        offers.append(offer)

    # Best first: can deliver in full on time, then cheapest total, then most reliable.
    offers.sort(key=lambda o: (not o["can_fulfil"], o["total_price"], -(o["delivery_reliability"] or 0)))
    for o, rank in zip([o for o in offers if o["can_fulfil"]], ["best", "better", "good"]):
        o["rank"] = rank
    recommended = next((o["product_id"] for o in offers if o["can_fulfil"]), None)
    n_full = sum(o["can_fulfil"] for o in offers)

    store().audit("valio_mcp", "check_offer",
                  f"Priced and checked availability for {len(ids)} products for {requested_date}: "
                  f"{n_full} can deliver {quantity:g} {quantity_unit} in full",
                  session=session,
                  data={"quotes": [o["quote_id"] for o in offers], "recommended": recommended, "skipped": skipped})
    return {"requested_date": requested_date, "requested_quantity": quantity, "requested_unit": quantity_unit,
            "offers": offers, "recommended_product_id": recommended, "skipped": skipped,
            "note": "unit_price and total_price are net of VAT. available_amount 999999 means a terminal "
                    "(supplier warehouse) product with unknown stock."}


@mcp.tool()
async def place_order(
    quote_id: Annotated[str, Field(description="quote_id from check_offer")],
    reason: Annotated[str, Field(description="Why this offer was chosen, shown to the human approver and in the audit log")],
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Commit to a quote. Valio Aimo enforces the buyer's mandate (spend limit, availability, quote validity).
    Returns status 'confirmed', 'pending_approval' (wait for a human, you cannot approve) or 'rejected'."""
    session, buyer_id = _caller(ctx)
    s = store()
    quote = s.get_quote(quote_id)
    if not quote:
        s.audit("buyer_agent", "place_order", f"Tried to order unknown quote {quote_id}", session=session, reason=reason)
        return {"status": "rejected", "error": f"unknown quote_id {quote_id}"}
    line = quote["data"]
    order_id = new_id("ord")
    s.audit("buyer_agent", "place_order",
            f"Requested order for {line['quantity']:g} {line['sales_unit']} of {line['product_id']} "
            f"({line['name']}) at {line['unit_price']:.2f} {line['currency']}",
            session=session, order_id=order_id, reason=reason, data={"quote_id": quote_id})

    checks = policy.evaluate(quote, buyer_id)
    status = policy.outcome(checks)
    public_checks = policy.public(checks)
    order = {"order_id": order_id, "quote_id": quote_id, "status": status, "buyer_id": buyer_id,
             "lines": [line], "total_price": line["total_price"], "currency": line["currency"],
             "delivery_date": line["requested_date"], "policy_checks": public_checks,
             "agent_rationale": reason, "approved_by": None, "created_at": iso()}
    result: dict = {"status": status, "order_id": order_id, "policy_checks": public_checks,
                    "total_price": order["total_price"], "currency": order["currency"],
                    "delivery_date": order["delivery_date"], "lines": order["lines"]}

    failed = [c for c in public_checks if not c["passed"]]
    if status == "pending_approval":
        appr = {"approval_id": new_id("appr"), "order_id": order_id, "status": "pending",
                "reason": "; ".join(c["detail"] for c in failed), "policy_checks": public_checks,
                "lines": order["lines"], "total_price": order["total_price"], "currency": order["currency"],
                "agent_rationale": reason, "created_at": iso(), "decided_by": None, "decided_at": None,
                "comment": None}
        order["approval_id"] = appr["approval_id"]
        s.save_approval(appr)
        result |= {"approval_id": appr["approval_id"], "approval": appr,
                   "message": "Waiting for human approval at the buyer. Do not retry or re-order."}
        s.audit("policy_engine", "policy_check",
                f"{', '.join(c['rule'] for c in failed)} failed. Sent to human approval",
                session=session, order_id=order_id, data={"approval_id": appr["approval_id"], "checks": public_checks})
    elif status == "rejected":
        result["message"] = "Order rejected: " + "; ".join(c["detail"] for c in failed)
        s.audit("policy_engine", "policy_check", result["message"], session=session, order_id=order_id,
                data={"checks": public_checks})
    else:
        s.audit("policy_engine", "policy_check", "All checks passed, within the agent's autonomous mandate",
                session=session, order_id=order_id, data={"checks": public_checks})
        s.audit("valio_mcp", "order_confirmed",
                f"Order {order_id} confirmed for delivery {order['delivery_date']}",
                session=session, order_id=order_id,
                data={"total_price": order["total_price"], "currency": order["currency"]})
    s.save_order(order, session, buyer_id)
    return result


@mcp.tool()
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
    """The mandate Valio Aimo enforces for this buyer on every place_order."""
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

    appr |= {"status": "approved" if decision == "approve" else "rejected", "decided_by": who,
             "decided_at": iso(), "comment": body.get("comment")}
    s.update_approval(appr)
    s.audit("human", decision, f"{who} {appr['status']} the order", session=session,
            order_id=order["order_id"], reason=body.get("comment"))

    if decision == "approve":
        quote = s.get_quote(order["quote_id"])
        if quote and now() < datetime.fromisoformat(quote["expires_at"]):
            order |= {"status": "confirmed", "approved_by": who}
            s.audit("valio_mcp", "order_confirmed",
                    f"Order {order['order_id']} confirmed for delivery {order['delivery_date']}",
                    session=session, order_id=order["order_id"],
                    data={"total_price": order["total_price"], "currency": order["currency"], "approved_by": who})
        else:
            order |= {"status": "quote_expired", "approved_by": who}
            s.audit("valio_mcp", "quote_expired", "Approved after the quote expired. A new offer is needed",
                    session=session, order_id=order["order_id"])
    else:
        order["status"] = "rejected"
    s.update_order(order)
    return JSONResponse({"approval": appr, "order": order})


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
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
