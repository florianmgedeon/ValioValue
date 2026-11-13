"""Thin client for the Valio Aimo mock API (/api/price, /api/availability)."""
from __future__ import annotations

import httpx

from . import config

MAX_PRODUCTS = 1000


class ValioApiError(RuntimeError):
    pass


async def _post(path: str, product_ids: list[str], requested_date: str) -> dict:
    if not config.VALIO_API_KEY:
        raise ValioApiError("VALIO_API_KEY is not set")
    if not 1 <= len(product_ids) <= MAX_PRODUCTS:
        raise ValioApiError(f"1..{MAX_PRODUCTS} products per request, got {len(product_ids)}")
    body = {"customer": config.VALIO_CUSTOMER, "requestedDate": requested_date, "products": product_ids}
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(
            f"{config.VALIO_API_BASE}{path}", json=body, headers={"X-API-Key": config.VALIO_API_KEY}
        )
    if r.status_code != 200:
        raise ValioApiError(f"{path} returned HTTP {r.status_code}: {r.text[:300]}")
    return r.json()


async def price(product_ids: list[str], requested_date: str) -> dict[str, dict]:
    data = await _post("/api/price", product_ids, requested_date)
    return {p["productId"]: p | {"_currency": data.get("currency")} for p in data.get("products", [])}


async def availability(product_ids: list[str], requested_date: str) -> dict[str, dict]:
    data = await _post("/api/availability", product_ids, requested_date)
    return {p["productId"]: p | {"_timeNow": data.get("timeNow")} for p in data.get("products", [])}
