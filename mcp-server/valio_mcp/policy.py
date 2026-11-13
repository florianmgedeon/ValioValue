"""Policy engine. Runs on the Valio Aimo side, so the agent cannot talk its way around it."""
from __future__ import annotations

import json
from datetime import datetime
from functools import lru_cache

from . import config
from .store import now

# Outcome of a failed rule: block the order outright, or route it to a human.
HARD = "reject"
SOFT = "approval"


@lru_cache(maxsize=1)
def _policies() -> dict:
    return json.loads(config.POLICY_FILE.read_text(encoding="utf-8"))


def buyer_policy(buyer_id: str | None) -> dict:
    pols = _policies()
    return pols.get(buyer_id or "default") or pols["default"]


def _fmt(x: float) -> str:
    return f"{x:,.2f}"


def evaluate(quote: dict, buyer_id: str | None) -> list[dict]:
    """Returns PolicyCheck dicts (contract shape) plus an internal `on_fail` field."""
    pol = buyer_policy(buyer_id)
    line = quote["data"]
    checks: list[dict] = []

    expires = datetime.fromisoformat(quote["expires_at"])
    valid = now() < expires
    checks.append({
        "rule": "quote_valid", "passed": valid, "on_fail": HARD,
        "detail": f"Quote {quote['quote_id']} valid until {quote['expires_at']}" if valid
        else f"Quote {quote['quote_id']} expired at {quote['expires_at']}, request a new offer",
    })

    closing = line.get("closing_time")
    cutoff_ok = not closing or now() < datetime.fromisoformat(closing)
    checks.append({
        "rule": "order_cutoff", "passed": cutoff_ok, "on_fail": HARD,
        "detail": f"Ordered before cut-off {closing}" if cutoff_ok
        else f"Order cut-off {closing} has passed, delivery on {line['requested_date']} is not possible",
    })

    ok = bool(line.get("can_fulfil")) or (not cutoff_ok and not [i for i in line.get("fulfil_issues", [])
                                                                 if "cut-off" not in i])
    checks.append({
        "rule": "availability", "passed": ok, "on_fail": SOFT,
        "detail": f"{_fmt(line['quantity'])} {line['sales_unit']} available by {line['requested_date']}" if ok
        else "; ".join(line.get("fulfil_issues") or [f"Only {line.get('available_amount')} {line['sales_unit']} "
                                                     f"from {line.get('available_date')}"]),
    })

    limit = float(pol["spend_limit"])
    total = float(line["total_price"])
    within = total <= limit
    checks.append({
        "rule": "spend_limit", "passed": within, "on_fail": SOFT,
        "detail": f"{_fmt(total)} {line['currency']} within autonomous limit of {_fmt(limit)} {pol['currency']}"
        if within else f"{_fmt(total)} {line['currency']} > {_fmt(limit)} {pol['currency']} autonomous limit",
    })
    return checks


def outcome(checks: list[dict]) -> str:
    if any(not c["passed"] and c["on_fail"] == HARD for c in checks):
        return "rejected"
    if any(not c["passed"] for c in checks):
        return "pending_approval"
    return "confirmed"


def public(checks: list[dict]) -> list[dict]:
    return [{k: v for k, v in c.items() if k != "on_fail"} for c in checks]
