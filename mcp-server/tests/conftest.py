"""Test setup: isolated data dir, sample catalog, fake Valio API. No network."""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="vv-test-"))
os.environ.update({
    "DATA_DIR": str(ROOT / "data"),
    "MCP_DB_PATH": str(TMP / "test.db"),
    "KEYS_DIR": str(TMP / "keys"),
    "PRODUCTS_FILE": str(ROOT / "data" / "products.sample.json"),
    "VALIO_API_KEY": "test",
    "APPROVER_TOKEN": "dev-approver-token",
})
sys.path.insert(0, str(ROOT / "mcp-server"))

# product -> (price per sales unit, litres per sales unit, available, days late)
FAKE = {
    "DEMO-1001": (1.19, 1.0, 1400, 0),
    "DEMO-1002": (10.90, 10.0, 60, 0),
    "DEMO-1003": (1.35, 1.0, 999999, 1),
    "DEMO-1004": (0.89, 1.0, 5000, 0),
    "DEMO-1005": (1.49, 1.0, 800, 0),
    "DEMO-1006": (2.10, 1.0, 900, 0),
}


def next_monday() -> str:
    d = date.today() + timedelta(days=1)
    while d.weekday() != 0:
        d += timedelta(days=1)
    return d.isoformat()


@pytest.fixture(autouse=True)
def fake_valio(monkeypatch):
    from valio_mcp import valio_api

    async def price(ids, requested_date):
        out = {}
        for pid in ids:
            p, litres, *_ = FAKE.get(pid, (2.0, 1.0, 100, 0))
            m = lambda v: {"value": v, "currency": "EUR"}  # noqa: E731
            out[pid] = {"productId": pid, "catalogPrice": m(p), "listPrice": m(p), "comparisonPrice": m(round(p / litres, 4)),
                        "comparisonUnit": "L", "netPriceFound": False, "error": False, "_currency": "EUR"}
        return out

    async def availability(ids, requested_date):
        d = date.fromisoformat(requested_date)
        closing = (datetime.now() + timedelta(days=2)).replace(microsecond=0).isoformat()
        out = {}
        for pid in ids:
            _, _, amount, late = FAKE.get(pid, (2.0, 1.0, 100, 0))
            out[pid] = {"productId": pid, "closingTime": closing, "salesBlock": False, "logisticModel": "Stock",
                        "scheduleLines": [{"salesBlock": False, "availableAmount": amount,
                                           "availableDate": (d + timedelta(days=late)).isoformat() + "T00:00:00"}]}
        return out

    monkeypatch.setattr(valio_api, "price", price)
    monkeypatch.setattr(valio_api, "availability", availability)


@pytest.fixture(scope="session")
def http():
    """One app for the whole run: the MCP session manager can only be started once per process."""
    from starlette.testclient import TestClient

    from valio_mcp import server
    with TestClient(server.mcp.streamable_http_app()) as c:
        yield c
