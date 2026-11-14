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
  GET  /internal/mandate/{buyer_id}              current mandate (Must, Prefer, Autonomy)
  POST /internal/mandate/{buyer_id}/preview      validate a new version, returns its hash to sign
  PUT  /internal/mandate/{buyer_id}              store it: hash + buyer signature required
  GET  /internal/mandate/{buyer_id}/history      all versions
  GET  /internal/mandate/{buyer_id}/cedar        the Cedar policies Valio Aimo enforces
  GET  /internal/policy-catalog                  attributes, operators and examples for policy editors
  POST /internal/rule-coverage                   {rule, category?} -> checked / known / safe / excluded / unknown
  GET  /internal/categories?q=fish               category codes for a plain word

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

from . import config, constraints, coverage, diff, intent as intents, mandate as mandates, policy, trust, valio_api
from .catalog import catalog
from .intent import HardRuleIn, ItemIn
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
    original_request: Annotated[str, Field(description="The buyer's message or recipe, verbatim")],
    items: Annotated[list[ItemIn], Field(min_length=1, max_length=40, description=(
        "Everything to buy. One item for a single product request. For a recipe or menu: one item per ingredient, "
        "scaled to the portions the buyer needs, in buyable units (L, KG or sales units)."))],
    deliver_by: Annotated[str, Field(description="Latest delivery date, YYYY-MM-DD")],
    hard_rules: Annotated[list[HardRuleIn], Field(description="Must-haves for ALL items (e.g. a guest's milk allergy)")] = [],
    optimise: Annotated[Literal["price", "reliability", "co2"] | None, Field(description="Only if the buyer asked for it in this request. Default: the mandate's choice")] = None,
    origin_preference: Annotated[str | None, Field(description="Soft preference from this request only, ISO alpha-2. The mandate's preferences apply anyway")] = None,
    budget_max_total: Annotated[float | None, Field(description="Only if the buyer named a budget, for the whole intent")] = None,
    max_over_pct: Annotated[float, Field(ge=0, le=20, description="Allowed overshoot when rounding to whole packs")] = 2.0,
    allow_under: Annotated[bool, Field(description="Only true if the buyer accepts less than asked")] = False,
    open_questions: Annotated[list[str], Field(description="Anything ambiguous you could not resolve, one question each. Leave out what the buyer already answered")] = [],
    clarifications: Annotated[list[str], Field(description="The buyer's answers to earlier questions, verbatim, one per answer")] = [],
    replaces_intent_id: Annotated[str | None, Field(description="When re-proposing after the buyer answered questions: the previous intent_id")] = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Turn the buyer's request (a product, a list or a recipe) into an OrderIntent and show it back.
    Call this FIRST, before searching. Fill only what the buyer said or what the recipe needs.
    Never guess an allergy: if 'non-dairy' or similar is unclear, add an open question.
    Valio Aimo adds the buyer's mandatory rules, cross-checks against the original text and returns
    a read-back, one item_id per item and open questions. Then STOP: a human must confirm. You cannot."""
    session, buyer_id = _caller(ctx)
    mandate = policy.buyer_policy(buyer_id)

    def dump(m, cls):
        return m.model_dump() if hasattr(m, "model_dump") else cls(**m).model_dump()

    it = intents.build(buyer_id=mandate["buyer_id"], mandate=mandate, session=session,
                       original_request=original_request,
                       items=[dump(i, ItemIn) for i in items], deliver_by=deliver_by,
                       hard_rules=[dump(r, HardRuleIn) for r in hard_rules], optimise=optimise,
                       origin_preference=origin_preference, budget_max_total=budget_max_total,
                       max_over_pct=max_over_pct, allow_under=allow_under, agent_questions=open_questions,
                       clarifications=clarifications)
    old = store().get_intent(replaces_intent_id) if replaces_intent_id else None
    if old and old["status"] == "proposed" and old["buyer_id"] == it["buyer_id"]:
        old["status"], old["superseded_by"] = "superseded", it["intent_id"]
        store().update_intent(old)
        it["revises"] = old["intent_id"]
    store().save_intent(it)
    store().audit("buyer_agent", "propose_intent", f"Proposed intent: {it['readback']}"
                  + (f" (after the buyer's answers, replaces {old['intent_id']})" if it.get("revises") else ""),
                  session=session, data={"intent_id": it["intent_id"], "intent_hash": it["intent_hash"],
                                         "questions": it["questions"], "clarifications": it["clarifications"]})
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
    if it["status"] == "fulfilled":
        return None, "every item of this intent is already ordered"
    if it["status"] in ("superseded", "expired") or intents.is_expired(it):
        return None, f"intent is {it['status'] if it['status'] != 'confirmed' else 'expired'}, propose a new one"
    return it, None


def _item(it: dict | None, item_id: str | None) -> tuple[dict | None, str | None]:
    if not it:
        return None, None
    if not item_id:
        if len(it["items"]) == 1:
            return it["items"][0], None
        return None, f"item_id required, this intent has {len(it['items'])} items: " + ", ".join(
            f"{i['item_id']}={i['product']['query']}" for i in it["items"])
    item = intents.get_item(it, item_id)
    return (item, None) if item else (None, f"unknown item_id {item_id}")


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
    item_id: Annotated[str | None, Field(description="Which item of the intent (required if it has several)")] = None,
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
    item, err2 = _item(it, item_id)
    if err or err2:
        return {"error": err or err2}
    rules = intents.effective_rules(it, item, policy.buyer_policy(it["buyer_id"])) if it else []
    # With an intent the hard rules are checked below from data, with evidence, so the name-based shortcut is off
    # and excluded products are reported instead of silently missing.
    pool, total = catalog().search(query, lactose_free=False if it else f.lactose_free,
                                   exclude_allergens=None if it else f.exclude_allergens,
                                   category=f.category, country_of_origin=f.country_of_origin,
                                   limit=200 if it else limit)
    hits, excluded, funnel, usual = pool, [], [], None
    prod_spec = (item or {}).get("product") or {}
    preferred = prod_spec.get("preferred_product_id") if item else None
    sub_ids = list(prod_spec.get("substitute_product_ids") or []) if item else []
    allow_other = prod_spec.get("allow_other_substitutes", True) if item else True
    if it:
        cat = catalog()
        pp = cat.get(preferred) if preferred else None
        sub_prods = [sp for sid in sub_ids if (sp := cat.get(sid)) and (not pp or sp.id != pp.id)]
        explicit_prods = ([pp] if pp else []) + sub_prods
        explicit_set = {p.id for p in explicit_prods}
        catalog_rest = [p for p in pool if p.id not in explicit_set]
        start_count = len(explicit_prods) + len(catalog_rest)
        # When allow_other_substitutes is False, only the buyer's usual product and listed substitutes may be searched.
        pool = explicit_prods if not allow_other else (explicit_prods + catalog_rest)
        hits = []
        dropped_at: dict[str, int] = {}
        for p in pool:
            evs = constraints.evaluate_all(p, rules)
            bad = [e for e in evs if constraints.violates(e)]
            is_usual = bool(pp) and p.id == pp.id
            is_sub = p.id in sub_ids
            if bad:
                dropped_at[bad[0].get("id") or bad[0]["rule"]] = dropped_at.get(bad[0].get("id") or bad[0]["rule"], 0) + 1
                excluded.append({"product_id": p.id, "name": p.name, "reason": constraints.one_line(bad[0]),
                                 "verdict": bad[0]["verdict"], "rule_id": bad[0].get("id")}
                                | ({"usual": True} if is_usual else {})
                                | ({"approved_substitute": True} if is_sub else {}))
            elif len(hits) < limit:
                hits.append(p)
            if is_usual:
                usual = {"product_id": p.id, "name": p.name, "allowed": not bad,
                         "reason": constraints.one_line(bad[0]) if bad else None}
        # Funnel: the pool after each rule, in mandate order. A product is dropped at the first rule it breaks.
        funnel = [{"step": "catalog", "label": f"Matches for '{query}'", "count": start_count}]
        if not allow_other:
            lbl = ("Only your usual product + allowed substitute(s)" if sub_prods
                   else "Only your usual product (no substitutes)")
            funnel.append({"step": "rule", "label": lbl, "count": len(pool)})
        left = len(pool)
        for r in rules:
            left -= dropped_at.get(r.get("id") or r["label"], 0)
            funnel.append({"step": "must" if r.get("source") == "mandate" else "rule", "rule_id": r.get("id"),
                           "label": r["label"], "count": left})
    usual_ids = {usual["product_id"]} if usual else set()
    sub_rank_by_id = {sid: idx + 1 for idx, sid in enumerate(sub_ids)}
    result = {"query": query, "intent_id": intent_id, "item_id": item["item_id"] if item else None,
              "filters": f.model_dump(exclude_none=True),
              "total_matches": total, "returned": len(hits),
              "products": [p.summary() | ({"evidence": constraints.evaluate_all(p, rules)} if it else {})
                           | ({"usual": True} if p.id in usual_ids else {})
                           | ({"approved_substitute": True, "substitute_rank": sub_rank_by_id[p.id]}
                              if p.id in sub_rank_by_id else {}) for p in hits],
              "excluded": excluded[:12], "excluded_count": len(excluded),
              "catalog_source": catalog().source}
    if it:
        result["funnel"] = funnel
        result["usual_product"] = usual
    store().audit("buyer_agent", "search_products",
                  f"Searched catalog for '{query}': {total} matches, returned {len(hits)}"
                  + (f", {len(excluded)} excluded by hard rules" if it else ""),
                  session=session, data={"filters": result["filters"], "product_ids": [p.id for p in hits],
                                         "intent_id": intent_id, "item_id": result["item_id"],
                                         "total_matches": total, "excluded_count": len(excluded)})
    return result


@mcp.tool(annotations=READ_ONLY)
async def check_offer(
    product_ids: Annotated[list[str], Field(min_length=1, max_length=25, description="Shortlisted product ids")],
    intent_id: Annotated[str | None, Field(description="Confirmed intent. Quantity, unit and date are taken from it.")] = None,
    item_id: Annotated[str | None, Field(description="Which item of the intent (required if it has several)")] = None,
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
    item, err2 = _item(it, item_id)
    if err or err2:
        return {"error": err or err2}
    mandate = policy.buyer_policy(it["buyer_id"]) if it else None
    rules = intents.effective_rules(it, item, mandate) if it else []
    prefs = intents.effective_preferences(it, mandate) if it else []
    if it:
        quantity, quantity_unit = item["quantity"]["amount"], item["quantity"]["unit"]
        requested_date = it["deliver_by"]
    if not quantity or not requested_date:
        return {"error": "quantity and requested_date are required without intent_id"}
    prod_spec = item["product"] if item else {}
    preferred = prod_spec.get("preferred_product_id") if item else None
    sub_ids = list(prod_spec.get("substitute_product_ids") or []) if item else []
    allow_other = prod_spec.get("allow_other_substitutes", True) if item else True
    cat = catalog()
    priority_ids = [pid for pid in ([preferred] if preferred else []) + sub_ids if pid and cat.get(pid)]
    # The buyer's usual product and approved substitutes are always quoted so Valio Aimo can rank them deterministically.
    ids = list(dict.fromkeys(priority_ids + [str(p) for p in product_ids]))
    allowed_ids = set(priority_ids)
    prices = await valio_api.price(ids, requested_date)
    avail = await valio_api.availability(ids, requested_date)
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
            "item_id": item["item_id"] if item else None,
        }
        signed = trust.valio_sign_obj(diff.quote_core(offer))
        offer["provenance"] = {"source": f"Valio Aimo API {config.VALIO_API_BASE}/api/price + /api/availability",
                               "fetched_at": iso(), "valid_until": offer["quote_expires_at"], **signed}
        if it and prod:
            offer["evidence"] = constraints.evaluate_all(prod, rules, offer=offer)
            if any(constraints.violates(e) for e in offer["evidence"]):
                offer["can_fulfil"] = False
                offer["fulfil_issues"] = issues + ["breaks a must of the intent or mandate"]
            met = [(p, constraints.evaluate(prod, p, offer=offer)) for p in prefs]
            offer["preferences"] = [{"id": p["id"], "label": p["label"], "weight": p.get("weight", 1),
                                     "met": ev["verdict"] == "safe"} for p, ev in met if ev]
            offer["preference_score"] = sum(x["weight"] for x in offer["preferences"] if x["met"])
        if it and not allow_other and pid not in allowed_ids:
            offer["can_fulfil"] = False
            offer["fulfil_issues"] = list(offer["fulfil_issues"]) + ["not an allowed substitute for this item"]
        if preferred and pid == preferred:
            offer["usual"] = True
        elif pid in sub_ids:
            offer["approved_substitute"] = True
            offer["substitute_rank"] = sub_ids.index(pid) + 1
            offer["is_substitute"] = True
        offers.append(offer)

    primary_offer = next((o for o in offers if o.get("usual")), None)
    for o in offers:
        if o.get("approved_substitute"):
            r_idx = o["substitute_rank"]
            if primary_offer and not primary_offer["can_fulfil"]:
                why = ", ".join(primary_offer.get("fulfil_issues") or ["unavailable"])
                o["substitute_reason"] = f"Fallback #{r_idx}: {primary_offer['name']} unavailable ({why})"
            else:
                o["substitute_reason"] = f"Approved fallback #{r_idx}"

    # Best first: can deliver in full on time, then usual -> approved substitutes in priority order -> preferences -> optimise.
    opt = (it or {}).get("preferences", {}).get("optimise", "price")
    pref = {"price": lambda o: (o["total_price"], -(o["delivery_reliability"] or 0)),
            "reliability": lambda o: (-(o["delivery_reliability"] or 0), o["total_price"]),
            "co2": lambda o: (o["co2_kg_per_kg"] if o["co2_kg_per_kg"] is not None else 1e9, o["total_price"])}[opt]

    def _tier_order(o: dict) -> tuple[int, int]:
        if o.get("usual"):
            return (0, 0)
        if o.get("approved_substitute"):
            return (1, int(o.get("substitute_rank") or 999))
        return (2, 0)

    offers.sort(key=lambda o: (not o["can_fulfil"], *_tier_order(o), -o.get("preference_score", 0), *pref(o)))
    for o, rank in zip([o for o in offers if o["can_fulfil"]], ["best", "better", "good"]):
        o["rank"] = rank
    for o in offers:
        store().save_quote(o["quote_id"], session, buyer_id, o, o["quote_expires_at"])
    recommended = next((o["product_id"] for o in offers if o["can_fulfil"]), None)
    n_full = sum(o["can_fulfil"] for o in offers)
    funnel_tail = _funnel_tail(offers, prefs)

    store().audit("valio_mcp", "check_offer",  # noqa: E501
                  f"Priced and checked availability for {len(ids)} products for {requested_date}: "
                  f"{n_full} can deliver {quantity:g} {quantity_unit} in full",
                  session=session,
                  data={"quotes": [o["quote_id"] for o in offers], "recommended": recommended, "skipped": skipped,
                        "can_fulfil": n_full,
                        "intent_id": intent_id, "item_id": item["item_id"] if item else None})
    return {"intent_id": intent_id, "item_id": item["item_id"] if item else None,
            "product_query": item["product"]["query"] if item else None,
            "requested_date": requested_date, "requested_quantity": quantity, "requested_unit": quantity_unit,
            "offers": offers, "recommended_product_id": recommended, "skipped": skipped,
            "funnel_tail": funnel_tail,
            "note": "unit_price and total_price are net of VAT. available_amount 999999 or more means stock is "
                    "not limited or unknown (terminal = supplier warehouse). fulfil_issues explains why an offer "
                    "cannot deliver in full on time."}


def _funnel_tail(offers: list[dict], prefs: list[dict]) -> list[dict]:
    """After the musts: what can deliver in full, then the usual product, approved substitute, or preferences
    (strongest first, a step only narrows when some offer meets it), then the pick. Each count <= the previous."""
    pool = [o for o in offers if o["can_fulfil"]]
    tail = [{"step": "available", "label": "Can deliver in full on time", "count": len(pool)}]
    usual = [o for o in pool if o.get("usual")]
    subs = sorted((o for o in pool if o.get("approved_substitute")), key=lambda o: o.get("substitute_rank") or 999)
    if usual:
        pool = usual
        tail.append({"step": "usual", "label": "Your usual product", "count": len(pool)})
    elif subs:
        pool = subs[:1]
        tail.append({"step": "usual", "label": "Your approved substitute", "count": len(pool)})
    else:
        for p in sorted(prefs, key=lambda p: -p.get("weight", 1)):
            met = [o for o in pool if any(x["id"] == p["id"] and x["met"] for x in o.get("preferences") or [])]
            if met:
                pool = met
            tail.append({"step": "prefer", "rule_id": p["id"], "label": p["label"], "count": len(pool)})
    tail.append({"step": "pick", "label": "Best offer", "count": min(1, len(pool))})
    return tail


class OrderLine(BaseModel):
    item_id: str = Field(description="item_id from the intent")
    quote_id: str = Field(description="quote_id from check_offer for that item")


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True,
                                      openWorldHint=False))
async def place_order(
    intent_id: Annotated[str, Field(description="The confirmed intent this order fulfils")],
    reason: Annotated[str, Field(description="Why these offers were chosen, shown to the human approver and in the audit log")],
    lines: Annotated[list[OrderLine] | None, Field(description="One line per item: item_id + quote_id. Put all items you can fulfil in ONE order")] = None,
    quote_id: Annotated[str | None, Field(description="Shortcut for a single-item intent instead of lines")] = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Commit a cart for a confirmed intent. Valio Aimo diffs every line against its intent item
    (product, hard rules, quantity, date) and the whole cart against budget and mandate, then returns the tier:
    'confirmed' (auto), 'pending_approval' (ask: wait for a human, you cannot approve) or 'rejected' (block).
    Every item can be bought exactly once. Items not in this order stay open for a later order.
    Idempotent: repeating the same call returns the same order."""
    session, _ = _caller(ctx)
    s = store()
    it = s.get_intent(intent_id)
    if not it:
        return {"status": "rejected", "error": f"unknown intent_id {intent_id}"}
    req = [(l.item_id, l.quote_id) if hasattr(l, "item_id") else (l["item_id"], l["quote_id"]) for l in lines or []]
    if quote_id and not req:
        q = s.get_quote(quote_id)
        iid = (q or {}).get("data", {}).get("item_id") or (it["items"][0]["item_id"] if len(it["items"]) == 1 else "?")
        req = [(iid, quote_id)]
    if not req:
        return {"status": "rejected", "error": "no lines"}

    # idempotent replay: exactly these lines already form one order
    prev_ids = {i.get("order_id") for i in it["items"] if i["item_id"] in {r[0] for r in req}}
    if len(prev_ids) == 1 and None not in prev_ids:
        prev = s.get_order(prev_ids.pop()) or {}
        prev.pop("_session", None)
        if sorted((c["item_id"], c["quote_id"]) for c in prev.get("cart", {}).get("lines", [])) == sorted(req):
            return _order_result(prev) | {"idempotent_replay": True}
    if it["status"] not in ("confirmed", "fulfilled") or intents.is_expired(it):
        return {"status": "rejected", "error": f"intent is {it['status']}, the buyer must confirm a current intent"}

    mandate = policy.buyer_policy(it["buyer_id"])
    order_id = new_id("ord")
    pairs, quoted = [], []
    for iid, qid in req:
        item, quote = intents.get_item(it, iid), s.get_quote(qid)
        pairs.append((item, quote, iid, qid))
        if item and quote:
            quoted.append((iid, quote["data"]))
    s.audit("buyer_agent", "place_order",
            f"Requested order with {len(req)} line(s): " + "; ".join(
                f"{l['quantity']:g} {l['sales_unit']} {l['name']} at {l['unit_price']:.2f} {l['currency']}"
                for _, l in quoted), session=session, order_id=order_id, reason=reason,
            data={"lines": [{"item_id": a, "quote_id": b} for a, b in req], "intent_id": intent_id})

    d = diff.compute(it, pairs, mandate)
    musts = {iid: intents.effective_rules(it, item, mandate) for item, _, iid, _ in pairs if item}
    decision = policy.decide(it, d, quoted, mandate, musts)
    tier = decision["tier"]
    if tier != "block":
        d["checks"].append(diff._check("autonomy", f"mandate v{mandate['version']}",
                                       ", ".join(decision["grants_used"]) or "no grant", tier == "auto", "ask",
                                       decision["detail"]) | {"grants_used": decision["grants_used"]})
        d["deviations"] = sum(1 for c in d["checks"] if not c["ok"] and c["field"] != "autonomy")
    status = policy.STATUS[tier]
    the_cart = diff.cart(it["intent_hash"], quoted)
    cart_hash = trust.digest(the_cart)
    public_diff = {"deviations": d["deviations"], "checks": d["checks"],
                   "accepted": [c["field"] + (f":{c['pref_id']}" if c.get("pref_id") else "")
                                for c in d["checks"] if not c["ok"] and c["field"] != "autonomy"]
                   if tier == "auto" else []}
    failed = [c for c in d["checks"] if not c["ok"]]
    item_ids = [iid for iid, _ in quoted]
    open_after = [i["item_id"] for i in it["items"] if i["status"] == "open" and i["item_id"] not in item_ids]
    order = {"order_id": order_id, "intent_id": intent_id, "status": status, "tier": tier, "buyer_id": it["buyer_id"],
             "lines": [l for _, l in quoted], "item_ids": item_ids, "total_price": the_cart["total_price"],
             "currency": the_cart["currency"], "delivery_date": the_cart["delivery_date"], "diff": public_diff,
             "cart": the_cart, "cart_hash": cart_hash, "fingerprint": trust.fingerprint(cart_hash),
             "intent": intents.public(it), "agent_rationale": reason, "approved_by": None, "created_at": iso(),
             "receipt": None, "quote_id": quoted[0][1]["quote_id"] if quoted else None,
             "items_open_after": open_after if tier != "block" else None,
             "mandate": {"mandate_id": mandate["mandate_id"], "version": mandate["version"],
                         "mandate_hash": mandate["mandate_hash"], "grants_used": decision["grants_used"]},
             "decision": decision}

    def label(c):
        item = intents.get_item(it, c.get("item_id") or "")
        return f"{item['product']['query']}: " if item and len(it["items"]) > 1 else ""

    if tier == "block":
        order["message"] = "Blocked: " + ("; ".join(f"{label(c)}{c['field']} ({c['intended']}): {c['detail']}"
                                                    for c in failed if c["severity"] == "block")
                                          or "; ".join(f"{l['product_id']}: {', '.join(l.get('blocked_by') or [])}"
                                                       for l in decision["lines"] if l["tier"] == "block"))
        s.audit("policy_engine", "policy_check", order["message"], session=session, order_id=order_id,
                data={"tier": tier, "diff": public_diff, "intent_id": intent_id})
        s.save_order(order, session, it["buyer_id"])
        return _order_result(order)

    _reserve(it, item_ids, order_id, the_cart["total_price"])
    if tier == "ask":
        appr = {"approval_id": new_id("appr"), "order_id": order_id, "status": "pending",
                "reason": "; ".join(f"{label(c)}{c['field']}: {c['detail']}" for c in failed), "tier": tier,
                "diff": public_diff, "cart": the_cart, "cart_hash": cart_hash, "fingerprint": order["fingerprint"],
                "intent": intents.public(it), "lines": order["lines"], "total_price": order["total_price"],
                "currency": order["currency"], "agent_rationale": reason, "created_at": iso(),
                "decided_by": None, "decided_at": None, "comment": None,
                "policy_checks": [{"rule": c["field"], "passed": c["ok"], "detail": c["detail"]} for c in d["checks"]]}
        order["approval_id"] = appr["approval_id"]
        s.save_approval(appr)
        s.audit("policy_engine", "policy_check",
                f"Mandate v{mandate['version']}: {decision['detail']}. Sent to human approval",
                session=session, order_id=order_id,
                data={"approval_id": appr["approval_id"], "tier": tier, "diff": public_diff, "intent_id": intent_id})
    else:
        s.audit("policy_engine", "policy_check",
                f"{len(item_ids)} line(s) within mandate v{mandate['version']}: {decision['detail']}"
                + (f" (accepted: {', '.join(public_diff['accepted'])})" if public_diff["accepted"] else ""),
                session=session, order_id=order_id, data={"tier": tier, "diff": public_diff, "intent_id": intent_id})
        order["receipt"] = _receipt(order, it, approved_by=None, buyer_cart_signature=None)
        s.audit("valio_mcp", "order_confirmed",
                f"Order {order_id} confirmed for delivery {order['delivery_date']}, receipt {order['fingerprint']}",
                session=session, order_id=order_id,
                data={"total_price": order["total_price"], "currency": order["currency"], "cart_hash": cart_hash})
    s.save_order(order, session, it["buyer_id"])
    return _order_result(order)


def _reserve(it: dict, item_ids: list[str], order_id: str, total: float) -> None:
    for i in it["items"]:
        if i["item_id"] in item_ids:
            i["status"], i["order_id"] = "ordered", order_id
    it["spent"] = round(float(it.get("spent") or 0) + total, 2)
    it["order_ids"] = list(it.get("order_ids") or []) + [order_id]
    if not intents.open_items(it):
        it["status"] = "fulfilled"
    store().update_intent(it)


def _release(it: dict, order: dict) -> None:
    """A human refused the cart (or the quote expired): its items are open again, the intent stands."""
    for i in it["items"]:
        if i.get("order_id") == order["order_id"]:
            i["status"], i["order_id"] = "open", None
    it["spent"] = round(float(it.get("spent") or 0) - float(order["total_price"]), 2)
    it["status"] = "confirmed"
    store().update_intent(it)


def _order_result(order: dict) -> dict:
    out = {k: order.get(k) for k in ("status", "tier", "order_id", "intent_id", "total_price", "currency",
                                     "delivery_date", "lines", "item_ids", "items_open_after", "diff", "cart_hash",
                                     "fingerprint", "receipt", "message", "approval_id")}
    out["policy_checks"] = [{"rule": c["field"], "passed": c["ok"], "detail": c["detail"]}
                            for c in (order.get("diff") or {}).get("checks", [])]
    if order.get("status") == "pending_approval":
        out["approval"] = store().get_approval(order["approval_id"])
        out["message"] = "Waiting for human approval at the buyer. Do not retry or re-order."
    return {k: v for k, v in out.items() if v is not None}


def _receipt(order: dict, it: dict, *, approved_by: str | None, buyer_cart_signature: str | None) -> dict:
    confirmed_at = iso()
    m = order.get("mandate") or {}
    body = {"order_id": order["order_id"], "intent_hash": it["intent_hash"], "cart_hash": order["cart_hash"],
            "status": "confirmed", "tier": order["tier"], "approved_by": approved_by, "confirmed_at": confirmed_at,
            "mandate_id": m.get("mandate_id"), "mandate_hash": m.get("mandate_hash"),
            "grants_used": m.get("grants_used") or []}
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
        quotes = [s.get_quote(l["quote_id"]) for l in order["lines"]]
        if quotes and all(q and now() < datetime.fromisoformat(q["expires_at"]) for q in quotes):
            order |= {"status": "confirmed", "approved_by": who}
            order["receipt"] = _receipt(order, it, approved_by=who, buyer_cart_signature=body.get("signature"))
            s.audit("valio_mcp", "order_confirmed",
                    f"Order {order['order_id']} confirmed for delivery {order['delivery_date']}, "
                    f"receipt {order['fingerprint']}", session=session, order_id=order["order_id"],
                    data={"total_price": order["total_price"], "currency": order["currency"], "approved_by": who})
        else:
            order |= {"status": "quote_expired", "approved_by": who}
            _release(it, order)
            s.audit("valio_mcp", "quote_expired", "Approved after the quote expired. A new offer is needed",
                    session=session, order_id=order["order_id"])
    else:
        order["status"] = "rejected"
        _release(it, order)  # the buyer's intent stands, only this cart was refused
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
    m = policy.buyer_policy(request.path_params["buyer_id"])
    return JSONResponse(m | {"summary": mandates.summary(m)})


async def _mandate_body(request: Request) -> tuple[dict | None, JSONResponse | None]:
    try:
        body = await request.json()
    except ValueError:
        return None, JSONResponse({"error": "body must be JSON"}, status_code=422)
    return (body if isinstance(body, dict) else {}), None


@mcp.custom_route("/internal/mandate/{buyer_id}/preview", methods=["POST"])
async def preview_mandate(request: Request) -> JSONResponse:
    """Validate a new mandate version. Returns it with the hash the buyer must sign. Nothing is stored."""
    if not _authorised(request):
        return _deny()
    body, err = await _mandate_body(request)
    if err:
        return err
    try:
        m = policy.preview(request.path_params["buyer_id"], body.get("mandate", body))
        text, _ = policy.to_cedar(m, m["mandatory_rules"])
    except policy.RuleError as e:
        return JSONResponse({"error": str(e)}, status_code=422)
    return JSONResponse(m | {"summary": mandates.summary(m), "cedar": text})


@mcp.custom_route("/internal/mandate/{buyer_id}", methods=["PUT"])
async def put_mandate(request: Request) -> JSONResponse:
    """Store a new version: {mandate, mandate_hash, signature, updated_by}. Never an MCP tool:
    the agent cannot change its own mandate. Orders from now on are decided against this version."""
    if not _authorised(request):
        return _deny()
    body, err = await _mandate_body(request)
    if err:
        return err
    bid = request.path_params["buyer_id"]
    try:
        m = policy.save(bid, body.get("mandate") or {}, mandate_hash=body.get("mandate_hash") or "",
                        signature=body.get("signature"), updated_by=body.get("updated_by") or "buyer.human")
    except policy.RuleError as e:
        return JSONResponse({"error": str(e)}, status_code=422)
    except PermissionError as e:
        return JSONResponse({"error": str(e)}, status_code=409 if "hash" in str(e).split(" ")[0] else 403)
    store().audit("human", "mandate_changed", f"{m['updated_by']} signed mandate v{m['version']} "
                                              f"({m['fingerprint']}): {mandates.summary(m)}",
                  data={"buyer_id": bid, "mandate_id": m["mandate_id"], "mandate_hash": m["mandate_hash"]})
    return JSONResponse(m | {"summary": mandates.summary(m)})


@mcp.custom_route("/internal/mandate/{buyer_id}/history", methods=["GET"])
async def mandate_history(request: Request) -> JSONResponse:
    if not _authorised(request):
        return _deny()
    bid = request.path_params["buyer_id"]
    policy.buyer_policy(bid)  # seeds v1 if needed
    return JSONResponse(store().mandate_history(bid))


@mcp.custom_route("/internal/mandate/{buyer_id}/cedar", methods=["GET"])
async def mandate_cedar(request: Request) -> JSONResponse:
    """The mandate as Cedar policies (musts as forbid, grants as permit). Request rules of an intent are
    added per decision."""
    if not _authorised(request):
        return _deny()
    m = policy.buyer_policy(request.path_params["buyer_id"])
    text, ids = policy.to_cedar(m, m["mandatory_rules"])
    return JSONResponse({"mandate_id": m["mandate_id"], "cedar": text, "policy_ids": ids})


@mcp.custom_route("/internal/policy-catalog", methods=["GET"])
async def policy_catalog(request: Request) -> JSONResponse:
    if not _authorised(request):
        return _deny()
    return JSONResponse(policy.catalog_info())


@mcp.custom_route("/internal/rule-coverage", methods=["POST"])
async def rule_coverage(request: Request) -> JSONResponse:
    """{rule, category?} -> how many catalog products the rule can decide from data. Read-only."""
    if not _authorised(request):
        return _deny()
    body, err = await _mandate_body(request)
    if err:
        return err
    if not isinstance(body.get("rule"), dict):
        return JSONResponse({"error": "rule is required"}, status_code=422)
    try:
        return JSONResponse(coverage.rule_coverage(body["rule"], body.get("category") or None))
    except policy.RuleError as e:
        return JSONResponse({"error": str(e)}, status_code=422)


@mcp.custom_route("/internal/categories", methods=["GET"])
async def find_categories(request: Request) -> JSONResponse:
    """?q=fish -> category codes whose product names match, for rules like 'only Finnish fish'."""
    if not _authorised(request):
        return _deny()
    return JSONResponse(coverage.categories(request.query_params.get("q", "")))


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
    sigs = []
    for line in order["lines"]:
        prov = line.get("provenance") or {}
        sigs.append(trust.verify(trust.valio_key().public_key(), prov.get("hash", ""), prov.get("signature"))
                    and prov.get("hash") == trust.digest(diff.quote_core(line)))
    add("quote_signatures", bool(sigs) and all(sigs),
        f"{sum(sigs)} of {len(sigs)} line(s): price and availability signed by Valio Aimo when quoted")
    items = {i["item_id"]: i for i in it.get("items", [])}
    ids = [c["item_id"] for c in order["cart"].get("lines", [])]
    add("one_line_per_item", len(ids) == len(set(ids)) and all(i in items for i in ids),
        f"{len(ids)} line(s), each fulfils a different item of the intent")
    if order.get("tier") == "ask":
        add("buyer_cart_signature", trust.verify(trust.buyer_public(order["buyer_id"]), order["cart_hash"],
                                                 rc.get("buyer_cart_signature")),
            f"{order.get('approved_by')} signed exactly this cart")
    else:
        try:
            again = policy.recheck(order["decision"])
            ok = again["tier"] == "auto" and again["grants_used"] == rc.get("grants_used")
            detail = (f"Re-ran Cedar on mandate v{again['mandate_version']}: auto under "
                      f"{', '.join(again['grants_used'])}" if ok else f"Re-ran Cedar: {again['tier']}, not auto")
        except (KeyError, LookupError) as e:
            ok, detail = False, f"cannot re-run the decision: {e}"
        add("autonomy", ok, detail)
    m = store().mandate_version(order["buyer_id"], (order.get("mandate") or {}).get("version") or 0)
    add("mandate_hash", bool(m) and trust.digest(mandates.content(m))
        == m["mandate_hash"] == rc.get("mandate_hash"),
        f"Receipt names mandate {rc.get('mandate_id')}, recomputed from the stored version")
    body = {k: rc.get(k) for k in ("order_id", "intent_hash", "cart_hash", "status", "tier", "approved_by",
                                   "confirmed_at", "mandate_id", "mandate_hash", "grants_used")}
    add("valio_receipt_signature", rc and trust.digest(body) == rc.get("receipt_hash")
        and trust.verify(trust.valio_key().public_key(), rc.get("receipt_hash", ""), rc.get("valio_signature")),
        "Receipt signed by Valio Aimo")
    return {"order_id": order_id, "status": order["status"], "fingerprint": order.get("fingerprint"),
            "valid": order["status"] == "confirmed" and all(c["ok"] for c in checks), "checks": checks,
            "keys": {"valio_aimo": trust.valio_public(),
                     "buyer": {"key_id": trust.key_id(trust.buyer_public(order["buyer_id"])),
                               "public_key": trust.public_b64(trust.buyer_public(order["buyer_id"]))}}}


def order_summary(order: dict) -> dict:
    """One row of the order list: outcome, money, who decided, which rules blocked, whether it was clean."""
    it = store().get_intent(order.get("intent_id") or "") or order.get("intent") or {}
    items = {i["item_id"]: i for i in it.get("items") or []}
    names = [items[i]["product"]["query"] for i in order.get("item_ids") or [] if i in items] or [
        l.get("name", "") for l in order.get("lines") or []]
    title = ", ".join(names)
    if len(title) > 60:
        title = title[:59].rstrip(" ,") + "…"
    checks = (order.get("diff") or {}).get("checks") or []
    decision = order.get("decision") or {}
    dlines = decision.get("lines") or []
    blocked = sorted({r for l in dlines if l.get("tier") == "block"
                      for r in l.get("blocked_by") or [] if r not in ("technical", "evaluation_error")})
    must_labels = {r["id"]: r["label"] for r in decision.get("musts") or [] if r.get("id")}
    decided_by = order.get("approved_by")
    if not decided_by and order.get("approval_id"):
        decided_by = (store().get_approval(order["approval_id"]) or {}).get("decided_by")
    m = order.get("mandate") or {}
    return {"order_id": order["order_id"], "created_at": order.get("created_at") or order.get("_created_at"),
            "status": order["status"], "tier": order.get("tier"), "total": order.get("total_price"),
            "currency": order.get("currency", "EUR"), "lines": len(order.get("lines") or []), "title": title,
            "decided_by": decided_by, "blocked_rules": blocked,
            "blocked_labels": [must_labels.get(r, r) for r in blocked],
            "blocked_lines": sum(1 for l in dlines if l.get("tier") == "block"),
            "asked_lines": sum(1 for l in dlines if l.get("tier") == "ask"),
            "mandate_version": m.get("version"),
            "grants_used": m.get("grants_used") or [], "intent_id": order.get("intent_id"),
            "intent_revised": bool(it.get("revises")),
            "product_changed": any(not c["ok"] and c["field"] in ("product", "usual") for c in checks),
            "delivery_date": order.get("delivery_date"), "session": order.get("_session")}


@mcp.custom_route("/internal/orders", methods=["GET"])
async def list_orders(request: Request) -> JSONResponse:
    """Recent orders of a buyer, newest first, as summaries."""
    if not _authorised(request):
        return _deny()
    q = request.query_params
    try:
        limit = max(1, min(int(q.get("limit") or 20), 200))
    except ValueError:
        return JSONResponse({"error": "limit must be a number"}, status_code=422)
    rows = store().list_orders(q.get("buyer_id"), limit=limit, before=q.get("before"))
    return JSONResponse([order_summary(o) for o in rows])


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
