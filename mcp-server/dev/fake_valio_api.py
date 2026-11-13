"""DEV ONLY: offline stand-in for the Valio Aimo mock API, same request/response shape as its OpenAPI.

Use when junction26.valioaimo.fi is not reachable:
    python mcp-server/dev/fake_valio_api.py          # http://127.0.0.1:8099
    VALIO_API_BASE=http://127.0.0.1:8099 in .env

Prices and stock are invented. Never use for the real demo.
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta

import uvicorn
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="FAKE Valio Aimo API (dev only)")

# product -> (price per sales unit, litres per sales unit, available amount, days late, logistic model)
KNOWN = {
    "DEMO-1001": (1.19, 1.0, 1400, 0, "Stock"),
    "DEMO-1002": (10.90, 10.0, 60, 0, "Stock"),
    "DEMO-1003": (1.35, 1.0, 999999, 1, "Terminal"),
    "DEMO-1004": (0.89, 1.0, 5000, 0, "Stock"),
    "DEMO-1005": (1.49, 1.0, 800, 0, "Stock"),
}


class ProductsRequest(BaseModel):
    customer: str
    requestedDate: date
    products: list[str] = Field(min_length=1, max_length=1000)


def _guess(pid: str) -> tuple[float, float, int, int, str]:
    h = int(hashlib.sha1(pid.encode()).hexdigest(), 16)
    return (round(1 + (h % 2000) / 100, 2), 1.0, (h >> 8) % 500, (h >> 16) % 2, "Stock")


def _auth(key: str | None) -> None:
    if not key:
        raise HTTPException(401, "X-API-Key missing")


@app.get("/api/ping")
def ping(x_api_key: str | None = Header(None)):
    _auth(x_api_key)
    return {"pong": True, "fake": True}


@app.post("/api/price")
def price(req: ProductsRequest, x_api_key: str | None = Header(None)):
    _auth(x_api_key)
    out = []
    for pid in req.products:
        p, litres, *_ = KNOWN.get(pid) or _guess(pid)
        d = req.requestedDate.isoformat()
        money = lambda v: {"value": v, "valueWithTax": round(v * 1.135, 2), "validFrom": d, "validTo": d, "currency": "EUR"}  # noqa: E731
        out.append({"productId": pid, "quantity": 1.0, "catalogPrice": money(p), "listPrice": money(p),
                    "priceWithTax": {"value": round(p * 1.135, 2), "validFrom": d, "validTo": d, "currency": "EUR"},
                    "comparisonPrice": money(round(p / litres, 4)), "comparisonUnit": "L",
                    "tax": {"taxRate": 13.5, "taxValue": round(p * 0.135, 2)}, "netPriceFound": False, "error": False})
    return {"customerId": req.customer, "currency": "EUR", "pricingDate": req.requestedDate.isoformat(), "products": out}


@app.post("/api/availability")
def availability(req: ProductsRequest, x_api_key: str | None = Header(None)):
    _auth(x_api_key)
    out = []
    closing = datetime.combine(req.requestedDate - timedelta(days=1), datetime.min.time()).replace(hour=12)
    for pid in req.products:
        _, _, amount, late, model = KNOWN.get(pid) or _guess(pid)
        out.append({"productId": pid, "closingTime": closing.isoformat(), "salesBlock": False, "logisticModel": model,
                    "scheduleLines": [{"salesBlock": False, "availableAmount": amount, "salesUnit": "PAK",
                                       "availableDate": (req.requestedDate + timedelta(days=late)).isoformat() + "T00:00:00"}]})
    return {"timeNow": datetime.now().isoformat(), "requestedDate": req.requestedDate.isoformat() + "T00:00:00", "products": out}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8099)
