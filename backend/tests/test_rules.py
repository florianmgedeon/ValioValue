"""3.1 rule from a sentence and 3.2 coverage, backend side. Valio Aimo (mcp-server) runs in-process."""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="vv-backend-test-"))
os.environ.update({"DATA_DIR": str(ROOT / "data"), "MCP_DB_PATH": str(TMP / "test.db"), "KEYS_DIR": str(TMP / "keys"),
                   "PRODUCTS_FILE": str(ROOT / "data" / "products.sample.json"), "VALIO_API_KEY": "test",
                   "APPROVER_TOKEN": "dev-approver-token", "LLM_PROVIDER": "scripted"})
for p in (ROOT / "backend", ROOT / "mcp-server"):
    sys.path.insert(0, str(p))

from buyer_agent import config, llm, routes_rules  # noqa: E402
from buyer_agent.app import app  # noqa: E402
from valio_mcp import server  # noqa: E402

BUYER = "school-kitchen-espoo"


@pytest.fixture(autouse=True)
def valio(monkeypatch):
    """Backend -> Valio calls go to the mcp-server's Starlette app in this process."""
    monkeypatch.setattr(config, "LLM_PROVIDER", "scripted")
    valio_app = server.mcp.streamable_http_app()

    async def call(method, path, **kw):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=valio_app), base_url="http://valio") as c:
            return await c.request(method, path, headers={"Authorization": f"Bearer {config.APPROVER_TOKEN}"}, **kw)

    monkeypatch.setattr(routes_rules, "_valio", call)


def post(path, body):
    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://backend") as c:
            return await c.post(path, json=body)
    return asyncio.run(go())


def test_no_sesame_not_even_traces():
    r = post("/mandate/draft", {"buyer_id": BUYER, "text": "No sesame, not even traces", "kind": "must"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert {k: d["rule"][k] for k in ("attr", "op", "value", "if_unknown")} == {
        "attr": "allergens", "op": "not_contains", "value": "sesame", "if_unknown": "exclude"}
    assert d["label"] == "No sesame, incl. may contain"
    assert "may contain sesame" in d["explanation"]
    c = d["coverage"]
    assert c["checked"] > 0 and c["known"] + c["unknown"] == c["checked"]


def test_may_contain_is_fine_uses_contains_only():
    d = post("/mandate/draft", {"buyer_id": BUYER, "text": "No milk, may contain is fine"}).json()
    assert d["rule"]["attr"] == "allergens_contains" and d["rule"]["value"] == "milk"


def test_only_finnish_milk_gets_a_category_scope():
    r = post("/mandate/draft", {"buyer_id": BUYER, "text": "Only Finnish milk"})
    assert r.status_code == 200, r.text
    rule = r.json()["rule"]
    assert rule["attr"] == "country" and rule["value"] == "fi" and rule["label"] == "Only Finnish milk"
    assert rule["applies_to"][0]["attr"] == "category" and rule["applies_to"][0]["value"]
    assert r.json()["scope"][0]["examples"]


def test_prefer_gets_a_weight_and_no_if_unknown():
    d = post("/mandate/draft", {"buyer_id": BUYER, "text": "No soy", "kind": "prefer"}).json()
    assert d["rule"]["weight"] == 2 and "if_unknown" not in d["rule"]


def test_scripted_cannot_parse_says_use_the_form():
    r = post("/mandate/draft", {"buyer_id": BUYER, "text": "Cheaper than last week please"})
    assert r.status_code == 422 and "form" in r.json()["detail"]["error"]


def test_nothing_is_saved():
    before = asyncio.run(routes_rules._valio("GET", f"/internal/mandate/{BUYER}")).json()["version"]
    post("/mandate/draft", {"buyer_id": BUYER, "text": "No sesame"})
    assert asyncio.run(routes_rules._valio("GET", f"/internal/mandate/{BUYER}")).json()["version"] == before


def test_llm_rule_is_validated_by_valio(monkeypatch):
    class Fake:
        name = "fake"

        def __init__(self, args, tool="draft_rule"):
            self.args, self.tool = args, tool

        async def complete(self, system, history, tools):
            assert "allergens" in system and history[0]["text"]
            return llm.AssistantTurn(None, [llm.ToolCall("c1", self.tool, self.args)])

    monkeypatch.setattr(routes_rules, "make_provider", lambda: Fake({"attr": "labels", "op": "contains",
                                                                     "value": "EU_ORGANIC_FARMING"}))
    d = post("/mandate/draft", {"buyer_id": BUYER, "text": "Organic only"}).json()
    assert d["rule"]["attr"] == "labels" and d["provider"] == "fake"

    monkeypatch.setattr(routes_rules, "make_provider", lambda: Fake({"attr": "labels", "op": "lt", "value": "x"}))
    r = post("/mandate/draft", {"buyer_id": BUYER, "text": "Labels below x"})
    assert r.status_code == 422 and "not valid" in r.json()["detail"]["error"]

    monkeypatch.setattr(routes_rules, "make_provider", lambda: Fake({"reason": "Two rules."}, "cannot_draft"))
    r = post("/mandate/draft", {"buyer_id": BUYER, "text": "No nuts and only Finnish"})
    assert r.status_code == 422 and r.json()["detail"]["error"] == "Two rules."


def test_coverage_passthrough_and_all_musts():
    r = post("/mandate/coverage", {"rule": {"attr": "allergens", "op": "not_contains", "value": "sesame"}})
    assert r.status_code == 200 and r.json()["checked"] > 0
    allm = post("/mandate/coverage", {"buyer_id": BUYER}).json()["musts"]
    assert [m["id"] for m in allm] == ["m1", "m2"]
    assert all(m["coverage"]["checked"] > 0 for m in allm)
    assert post("/mandate/coverage", {"rule": {"attr": "nope", "op": "eq", "value": 1}}).status_code == 422
