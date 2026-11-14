"""Package 2b: the order list for the start screen and the autonomy ladder."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import autonomy, config, kitchen, valio

router = APIRouter()


@router.get("/orders")
async def list_orders(buyer_id: str | None = None, limit: int = 20, before: str | None = None):
    """Recent orders, newest first, with a `label` for the UI and whether each one counted as clean."""
    bid = buyer_id or config.DEFAULT_BUYER_ID
    rows = await autonomy.orders(bid, limit=max(1, min(limit, 200)), before=before)
    out = []
    for r in rows:
        clean, reason = autonomy.is_clean(r, rows)
        out.append(r | {"clean": clean, "clean_reason": reason})
    return out


@router.get("/autonomy")
async def get_autonomy(buyer_id: str | None = None):
    return await autonomy.state(buyer_id or config.DEFAULT_BUYER_ID)


class LevelIn(BaseModel):
    buyer_id: str | None = None
    level: int
    max_order_total: float | None = None
    by: str | None = None
    mandate_hash: str | None = None


@router.post("/autonomy/level")
async def set_level(body: LevelIn):
    """Change the level. Without mandate_hash: 409 {needs_signature, preview}. With it: signed new mandate."""
    try:
        return await autonomy.set_level(body.buyer_id or config.DEFAULT_BUYER_ID, body.level, body.max_order_total,
                                        body.by or "buyer.human", body.mandate_hash)
    except ValueError as e:
        raise HTTPException(422, str(e)) from None


class SyncIn(BaseModel):
    buyer_id: str | None = None
    by: str | None = None
    mandate_hash: str | None = None


@router.post("/autonomy/sync-usual")
async def sync_usual(body: SyncIn | None = None):
    """'Update your usual products in the grant': level 1 again, with the current usual list and limit."""
    body = body or SyncIn()
    bid = body.buyer_id or config.DEFAULT_BUYER_ID
    m = await valio.mandate(bid)
    if autonomy.level_of(m) != 1:
        raise HTTPException(409, {"error": f"autonomy is level {autonomy.level_of(m)}, not 1 (usual products)"})
    if autonomy.usual_scope(bid, m)["in_sync"] and kitchen.usual_ids(kitchen.load(bid)):
        return await autonomy.state(bid) | {"changed": False}
    try:
        return await autonomy.set_level(bid, 1, None, body.by or "buyer.human", body.mandate_hash) | {"changed": True}
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
