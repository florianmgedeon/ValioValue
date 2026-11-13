"""End-to-end demo against the running backend (localhost:8000).

    python scripts/demo_e2e.py ["I need 1,000 litres of lactose-free milk by Monday"]

Sends the request, prints the event stream, approves the pending order as a human,
waits for order_confirmed and prints the audit trail.
"""
from __future__ import annotations

import json
import os
import sys
import threading

import httpx

BASE = os.getenv("BACKEND_URL", "http://localhost:8000")
MSG = sys.argv[1] if len(sys.argv) > 1 else "I need 1,000 litres of lactose-free milk by Monday"


def events(session_id: str, out: list, done_count: int, stop: threading.Event):
    with httpx.stream("GET", f"{BASE}/chat/{session_id}/stream", timeout=None) as r:
        seen_done = 0
        for line in r.iter_lines():
            if not line.startswith("data: "):
                continue
            evt = json.loads(line[6:])
            out.append(evt)
            t = evt["type"]
            detail = {"message": evt.get("text"), "tool_call": evt.get("label"), "tool_result": evt.get("summary"),
                      "offers": f"{len(evt.get('offers', []))} offers, recommended {evt.get('recommended_product_id')}",
                      "approval_required": evt.get("approval", {}).get("reason"),
                      "order_confirmed": f"{evt.get('order_id')} {evt.get('total_price')} {evt.get('currency')} "
                                         f"approved_by={evt.get('approved_by')}",
                      "error": evt.get("message")}.get(t, "")
            print(f"  [{t}] {detail}")
            if t == "done":
                seen_done += 1
                if seen_done >= done_count:
                    stop.set()
                    return


def run_until_done(session_id: str, done_count: int) -> list:
    out, stop = [], threading.Event()
    th = threading.Thread(target=events, args=(session_id, out, done_count, stop), daemon=True)
    th.start()
    return out, stop, th


def main():
    print(f"> {MSG}")
    sid = httpx.post(f"{BASE}/chat", json={"message": MSG}).json()["session_id"]
    out, stop, th = run_until_done(sid, 2)
    while not stop.is_set():
        appr = next((e["approval"] for e in out if e["type"] == "approval_required"), None)
        if appr and any(e["type"] == "done" for e in out):
            print(f"\n> human approves {appr['approval_id']} ({appr['total_price']} {appr['currency']})")
            r = httpx.post(f"{BASE}/approvals/{appr['approval_id']}",
                           json={"decision": "approve", "decided_by": "chef.anna", "comment": "OK, go ahead"})
            print(f"  approval -> {r.json()['status']}")
            break
        if any(e["type"] == "order_confirmed" for e in out) or (
                any(e["type"] == "done" for e in out) and not appr and len(out) > 2):
            break
        stop.wait(0.2)
    th.join(timeout=30)
    order = next((e for e in out if e["type"] == "order_confirmed"), None)
    if not order:
        sys.exit("no order confirmed")
    print(f"\nAudit trail for {order['order_id']}:")
    for a in httpx.get(f"{BASE}/orders/{order['order_id']}/audit").json():
        print(f"  {a['timestamp']}  {a['actor']:<14} {a['action']:<16} {a['summary']}"
              + (f"  (reason: {a['reason']})" if a.get("reason") else ""))


if __name__ == "__main__":
    main()
