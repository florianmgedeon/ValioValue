"""Package 2c: GET /orders/{id}/story, the plain-language timeline of one order."""
from __future__ import annotations

import json
import re

import pytest

from buyer_agent import agent
from test_usual_and_autonomy import confirm_and_order, usual_kitchen

pytestmark = pytest.mark.anyio

# What a chef should never read in a story sentence.
TECHNICAL = re.compile(r"sha256|mandate|cedar|signature|\bg\d+\b|\bm\d+\b|hash|intent", re.I)


async def test_story_of_an_order_placed_alone(client):
    buyer = "juvenes"
    usual_kitchen(buyer, [("milk", "DEMO-1002", 120, "L")])
    s = agent.get_or_create_session(None, buyer)
    await confirm_and_order(client, s, "120 L milk by Monday")
    done = next(e for e in s.events if e["type"] == "order_confirmed")

    r = await client.get(f"/orders/{done['order_id']}/story")
    assert r.status_code == 200, r.text
    story = r.json()
    assert story["label"] == "Ordered alone" and story["tier"] == "auto"
    kinds = [x["kind"] for x in story["steps"]]
    assert kinds == ["asked", "understood", "searched", "decided", "confirmed"]

    steps = {x["kind"]: x for x in story["steps"]}
    assert steps["asked"]["text"] == "120 L milk by Monday"
    assert steps["understood"]["signed_by"] == "Anna" and steps["understood"]["items"] == 1

    milk = steps["searched"]["items"][0]
    assert milk["word"] == "milk" and milk["picked"]
    assert milk["why"].startswith("Your usual.")
    assert len(milk["why"].split()) <= 20
    f = milk["funnel"]
    assert f[-1] == 1 and all(a >= b for a, b in zip(f, f[1:]))

    line = steps["decided"]["lines"][0]
    assert line["outcome"] == "alone" and line["why"].startswith("No changes")
    assert "limit" in line["why"]

    sentences = [milk["why"], line["why"]]
    assert not [t for t in sentences if TECHNICAL.search(t)], sentences
    assert "sha256" not in json.dumps(steps["decided"])


async def test_story_of_an_unknown_order(client):
    r = await client.get("/orders/ord_nope/story")
    assert r.status_code == 404


def test_failed_checks_read_as_plain_sentences():
    from buyer_agent import story
    order = {"order_id": "ord_x", "status": "pending_approval", "tier": "ask", "item_ids": ["i1"],
             "decision": {"lines": [{"item_id": "i1", "tier": "ask"}]},
             "diff": {"checks": [
                 {"item_id": "i1", "ok": False, "field": "preference", "intended": "Finnish ingredients first",
                  "cart": "excluded", "detail": "Misses your preference: catalog labels=none"},
                 {"item_id": "i1", "ok": False, "field": "quantity", "intended": "40 L", "cart": "42 L (42 × 1 L)",
                  "detail": "5.0 % over, allowed 2 %"}]}}
    s = story.build(order, [], [])
    line = next(x for x in s["steps"] if x["kind"] == "decided")["lines"][0]
    assert line == {"item_id": "i1", "outcome": "you",
                    "why": "Misses your preference “Finnish ingredients first”; 42 L instead of 40 L (whole packs)"}
    assert s["label"] == "Waiting for you"
