"""Photo demo against the running backend (localhost:8000): a handwritten order list becomes an intent.

    python scripts/demo_photo.py [path/to/photo.jpg] ["optional text"]

Uploads the photo (default: contracts/fixtures/uploads/order-list.jpg), sends it to POST /chat as an
attachment and prints the events until the agent proposes the intent. Works with LLM_PROVIDER=scripted too:
the fixture's text stands in for vision.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx

BASE = os.getenv("BACKEND_URL", "http://localhost:8000")
ROOT = Path(__file__).resolve().parents[1]
PHOTO = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "contracts" / "fixtures" / "uploads" / "order-list.jpg"
TEXT = sys.argv[2] if len(sys.argv) > 2 else ""
BUYER = os.getenv("BUYER_ID", "school-kitchen-espoo")


def main() -> None:
    with httpx.Client(base_url=BASE, timeout=60) as c:
        meta = c.post("/uploads", data={"buyer_id": BUYER},
                      files={"file": (PHOTO.name, PHOTO.read_bytes(), "image/jpeg")}).raise_for_status().json()
        print(f"uploaded {meta['upload_id']} ({meta['width']}×{meta['height']}, {meta['bytes']:,} bytes)")
        sid = c.post("/chat", json={"message": TEXT, "buyer_id": BUYER,
                                    "attachments": [{"upload_id": meta["upload_id"]}]}).raise_for_status().json()["session_id"]
    with httpx.stream("GET", f"{BASE}/chat/{sid}/stream", timeout=None) as r:
        for line in r.iter_lines():
            if not line.startswith("data: "):
                continue
            evt = json.loads(line[6:])
            t = evt["type"]
            if t == "reasoning":
                continue
            detail = {"user_message": f"{evt.get('text')!r} + {len(evt.get('attachments') or [])} photo(s)",
                      "message": evt.get("text"), "tool_call": evt.get("label"), "tool_result": evt.get("summary"),
                      "error": evt.get("message")}.get(t, "")
            print(f"{t:16} {detail}")
            if t == "intent_proposed":
                it = evt["intent"]
                print(f"\nread-back: {it['readback']}")
                for i in it["items"]:
                    q = i["quantity"]
                    usual = f"  (usual: {i['usual']['name']})" if i.get("usual") else ""
                    print(f"  {i['item_id']}: {q['amount']:g} {q['unit']} {i['product']['query']}{usual}")
                if it.get("questions"):
                    print("questions:", *it["questions"], sep="\n  ")
            if t == "done":
                break


if __name__ == "__main__":
    main()
