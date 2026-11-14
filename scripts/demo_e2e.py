"""End-to-end demo against the running backend (localhost:8000).

    python scripts/demo_e2e.py ["I need 1,000 litres of lactose-free milk by Monday"]

Sends the request, confirms the intent the agent proposed (signed), approves the order if it
needs a human (signing the cart hash it was shown), then verifies the receipt and prints the audit trail.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time

import httpx

BASE = os.getenv("BACKEND_URL", "http://localhost:8000")
MSG = sys.argv[1] if len(sys.argv) > 1 else "I need 1,000 litres of lactose-free milk by Monday"


def listen(session_id: str, out: list):
    with httpx.stream("GET", f"{BASE}/chat/{session_id}/stream", timeout=None) as r:
        for line in r.iter_lines():
            if not line.startswith("data: "):
                continue
            evt = json.loads(line[6:])
            out.append(evt)
            t = evt["type"]
            detail = {"message": evt.get("text"), "tool_call": evt.get("label"), "tool_result": evt.get("summary"),
                      "intent_proposed": (evt.get("intent") or {}).get("readback"),
                      "intent_confirmed": (evt.get("intent") or {}).get("confirmed_by"),
                      "offers": f"{len(evt.get('offers', []))} offers, {len(evt.get('excluded') or [])} excluded",
                      "approval_required": f"{(evt.get('approval') or {}).get('reason')} "
                                           f"[cart {(evt.get('approval') or {}).get('fingerprint')}]",
                      "order_confirmed": f"{evt.get('order_id')} tier={evt.get('tier')} "
                                         f"deviations={(evt.get('diff') or {}).get('deviations')} "
                                         f"receipt={evt.get('fingerprint')}",
                      "order_rejected": evt.get("message"), "error": evt.get("message")}.get(t, "")
            if t != "reasoning":
                print(f"  [{t}] {detail}")


def wait_for(out: list, types: set[str], timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        hit = next((e for e in reversed(out) if e["type"] in types), None)
        if hit:
            return hit
        time.sleep(0.2)
    sys.exit(f"timeout waiting for {types}")


def main():
    print(f"> {MSG}")
    sid = httpx.post(f"{BASE}/chat", json={"message": MSG}).json()["session_id"]
    out: list = []
    threading.Thread(target=listen, args=(sid, out), daemon=True).start()

    it = wait_for(out, {"intent_proposed"})["intent"]
    if it["questions"]:
        sys.exit(f"agent asks first: {it['questions']}")
    print(f"\n> human confirms intent {it['intent_id']} (hash {it['intent_hash'][:19]}...)")
    r = httpx.post(f"{BASE}/intents/{it['intent_id']}/confirm",
                   json={"intent_hash": it["intent_hash"], "confirmed_by": "chef.anna"})
    print(f"  -> {r.status_code} {r.json().get('status')}")

    evt = wait_for(out, {"approval_required", "order_confirmed"}, timeout=90)
    if evt["type"] == "approval_required":
        appr = evt["approval"]
        print(f"\n> human approves {appr['approval_id']}, signs cart {appr['fingerprint']}")
        r = httpx.post(f"{BASE}/approvals/{appr['approval_id']}",
                       json={"decision": "approve", "decided_by": "chef.anna", "cart_hash": appr["cart_hash"]})
        print(f"  -> {r.status_code} {r.json().get('status')}")
        evt = wait_for(out, {"order_confirmed"})
    time.sleep(0.5)

    oid = evt["order_id"]
    v = httpx.get(f"{BASE}/orders/{oid}/verify").json()
    print(f"\nVerify {oid}: valid={v['valid']}")
    for c in v["checks"]:
        print(f"  {'OK ' if c['ok'] else 'BAD'} {c['name']:<24} {c['detail']}")
    print(f"\nAudit trail for {oid}:")
    for a in httpx.get(f"{BASE}/orders/{oid}/audit").json():
        print(f"  {a['timestamp'][11:19]}  {a['actor']:<14} {a['action']:<16} {a['summary']}")
    sys.exit(0 if v["valid"] else 1)


if __name__ == "__main__":
    main()
