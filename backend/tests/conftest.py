"""Backend test setup. The real Valio Aimo side (mcp-server) runs in-process: REST calls go through
httpx.ASGITransport, MCP tool calls straight to the tool functions. Valio's price API and the LLM are faked.
No network, no key."""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="vv-backend-test-"))
os.environ.update({
    "DATA_DIR": str(ROOT / "data"),
    "UPLOADS_DIR": str(TMP / "uploads"),
    "KITCHEN_DIR": str(TMP / "kitchen"),
    "MCP_DB_PATH": str(TMP / "valio.db"),
    "KEYS_DIR": str(TMP / "keys"),
    "PRODUCTS_FILE": str(ROOT / "data" / "products.sample.json"),
    "VALIO_API_KEY": "test",
    "APPROVER_TOKEN": "dev-approver-token",
    "LLM_PROVIDER": "scripted",
    "LLM_API_KEY": "",
    "DEFAULT_BUYER_ID": "school-kitchen-espoo",
    "MCP_SERVER_URL": "http://valio.test/mcp",
})
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "mcp-server")]

from buyer_agent import agent, app as app_module, ingest, valio  # noqa: E402
from valio_mcp import server, valio_api  # noqa: E402

TOOLS = {"propose_intent": server.propose_intent, "search_products": server.search_products,
         "check_offer": server.check_offer, "place_order": server.place_order,
         "get_order_status": server.get_order_status}


def ctx(buyer: str, session: str = "sess_test"):
    req = SimpleNamespace(headers={"x-agent-session": session, "x-buyer-id": buyer})
    return SimpleNamespace(request_context=SimpleNamespace(request=req))


class FakeMCP:
    """What agent.mcp_session yields, backed by the tool functions themselves."""

    def __init__(self, session):
        self.session = session

    async def list_tools(self):
        return SimpleNamespace(tools=await server.mcp.list_tools())

    async def call_tool(self, name, args):
        res = await TOOLS[name](**args, ctx=ctx(self.session.buyer_id, self.session.id))
        return SimpleNamespace(structuredContent=res, content=[])


def next_monday() -> str:
    d = date.today() + timedelta(days=1)
    while d.weekday() != 0:
        d += timedelta(days=1)
    return d.isoformat()


@pytest.fixture(autouse=True)
def wiring(monkeypatch):
    valio_app = server.mcp.streamable_http_app()

    async def call(method, path, **kw):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=valio_app), base_url="http://valio.test") as c:
            return await c.request(method, path, headers={"Authorization": "Bearer dev-approver-token"}, **kw)

    monkeypatch.setattr(valio, "call", call)

    async def mandate_summary(buyer_id):
        r = await call("GET", f"/internal/mandate/{buyer_id}")
        return r.json().get("summary") or ""

    monkeypatch.setattr(agent, "mandate_summary", mandate_summary)

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def mcp_session(session):
        yield FakeMCP(session)

    monkeypatch.setattr(agent, "mcp_session", mcp_session)

    async def price(ids, requested_date):
        m = lambda v: {"value": v, "currency": "EUR"}  # noqa: E731
        return {pid: {"productId": pid, "catalogPrice": m(2.0), "listPrice": m(2.0), "comparisonPrice": m(2.0),
                      "comparisonUnit": "L", "netPriceFound": False, "error": False, "_currency": "EUR"} for pid in ids}

    async def availability(ids, requested_date):
        d = date.fromisoformat(requested_date)
        closing = (datetime.now() + timedelta(days=2)).replace(microsecond=0).isoformat()
        return {pid: {"productId": pid, "closingTime": closing, "salesBlock": False, "logisticModel": "Stock",
                      "scheduleLines": [{"salesBlock": False, "availableAmount": 5000,
                                         "availableDate": d.isoformat() + "T00:00:00"}]} for pid in ids}

    monkeypatch.setattr(valio_api, "price", price)
    monkeypatch.setattr(valio_api, "availability", availability)


@pytest.fixture
async def client():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_module.app), base_url="http://backend.test") as c:
        yield c


async def wait_done(session, n_done: int = 1, timeout: float = 10):
    import asyncio
    for _ in range(int(timeout * 50)):
        if sum(1 for e in session.events if e["type"] == "done") >= n_done:
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"turn did not finish: {[e['type'] for e in session.events]}")


@pytest.fixture
def anyio_backend():
    return "asyncio"
