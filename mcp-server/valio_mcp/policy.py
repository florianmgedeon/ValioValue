"""Mandates and tiers. Runs on the Valio Aimo side, so the agent cannot talk its way around it.

Tier of an order, decided from the diff between the confirmed intent and the quote (see diff.py):
  auto   no deviations, inside the mandate      -> confirmed by Valio Aimo
  ask    a deviation a human may accept         -> pending, the buyer signs the exact cart
  block  a hard rule or a technical rule broken -> rejected, cannot be overridden
"""
from __future__ import annotations

import json
from functools import lru_cache

from . import config, trust


@lru_cache(maxsize=1)
def _policies() -> dict:
    return json.loads(config.POLICY_FILE.read_text(encoding="utf-8"))


def buyer_policy(buyer_id: str | None) -> dict:
    pols = _policies()
    bid = buyer_id if buyer_id in pols and buyer_id != "_doc" else "default"
    pol = dict(pols[bid])
    pol.setdefault("allowed_categories", None)
    pol.setdefault("mandatory_rules", [])
    pol["buyer_id"] = bid
    pol["mandate_id"] = f"mandate_{bid}_v1"
    pol["mandate_hash"] = trust.digest({k: v for k, v in pol.items() if k not in ("mandate_hash",)})
    return pol


def tier(checks: list[dict]) -> str:
    sev = {c["severity"] for c in checks if not c["ok"]}
    return "block" if "block" in sev else "ask" if "ask" in sev else "auto"


STATUS = {"auto": "confirmed", "ask": "pending_approval", "block": "rejected"}
