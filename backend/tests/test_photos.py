"""Package 1a: uploads, phone pairing, photos in the chat."""
from __future__ import annotations

import io
import json

import pytest
from PIL import Image

from conftest import ROOT, wait_done
from buyer_agent import agent, routes_uploads, uploads
from buyer_agent.llm import AnthropicProvider, OpenAICompatibleProvider

pytestmark = pytest.mark.anyio
FIX = ROOT / "contracts" / "fixtures" / "uploads"


def jpeg(w: int, h: int) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (w, h), (200, 30, 30)).save(out, "JPEG")
    return out.getvalue()


async def test_upload_downscales_and_validates(client):
    r = await client.post("/uploads", files={"file": ("big.jpg", jpeg(4000, 3000), "image/jpeg")})
    assert r.status_code == 200, r.text
    meta = r.json()
    assert meta["upload_id"].startswith("up_") and meta["media_type"] == "image/jpeg"
    assert max(meta["width"], meta["height"]) == 1600 and meta["source"] == "upload"
    stored = Image.open(io.BytesIO((await client.get(f"/uploads/{meta['upload_id']}")).content))
    assert stored.size == (1600, 1200)
    png = io.BytesIO()
    Image.new("RGB", (300, 200)).save(png, "PNG")
    r = await client.post("/uploads", files={"file": ("x.png", png.getvalue(), "image/png")})
    assert r.json()["media_type"] == "image/jpeg" and r.json()["width"] == 300  # small images keep their size
    assert (await client.post("/uploads", files={"file": ("a.txt", b"hello", "text/plain")})).status_code == 415
    big = b"%PDF-1.4" + b"0" * (16 * 1024 * 1024)
    assert (await client.post("/uploads", files={"file": ("a.pdf", big, "application/pdf")})).status_code == 413
    pdf = await client.post("/uploads", files={"file": ("inv.pdf", (FIX / "invoices-oct.pdf").read_bytes(), "")})
    assert pdf.json()["media_type"] == "application/pdf" and pdf.json()["fixture"] == "invoices-oct"
    assert (await client.get("/uploads/up_nothere")).status_code == 404
    assert (await client.get("/uploads/..%2Fsecret")).status_code == 404


async def test_fixture_upload_carries_its_text(client):
    r = await client.post("/uploads", files={"file": ("IMG_0042.jpg", (FIX / "order-list.jpg").read_bytes(), "image/jpeg")})
    meta = r.json()
    assert meta["fixture"] == "order-list" and meta["text"].startswith("milk 120 L")
    assert uploads.load(meta["upload_id"])[1][:3] == b"\xff\xd8\xff"


async def test_pairing_stream_gets_paired_and_photo_received(client):
    p = (await client.post("/pairings", json={"purpose": "kitchen"})).json()
    assert p["phone_url"].endswith(f"/phone?pair={p['pair_id']}") and p["expires_at"]
    stream = await routes_uploads.pairing_stream(p["pair_id"])
    frames = stream.body_iterator
    page = await client.get(f"/phone?pair={p['pair_id']}")
    assert page.status_code == 200 and "capture=\"environment\"" in page.text
    up = await client.post("/uploads", data={"pair_id": p["pair_id"]},
                           files={"file": ("p.jpg", jpeg(800, 600), "image/jpeg")})
    assert up.json()["source"] == "phone" and up.json()["pair_id"] == p["pair_id"]
    first, second = await frames.__anext__(), await frames.__anext__()
    assert first.startswith("event: paired")
    assert second.startswith("event: photo_received")
    data = json.loads(second.split("data: ", 1)[1])
    assert data["upload_id"] == up.json()["upload_id"] and data["url"] == f"/uploads/{data['upload_id']}"
    assert data["media_type"] == "image/jpeg" and data["purpose"] == "kitchen"
    await frames.aclose()
    bad = await client.post("/uploads", data={"pair_id": "pair_nope"}, files={"file": ("p.jpg", jpeg(10, 10), "image/jpeg")})
    assert bad.status_code == 404


def _history_with_photo():
    img = uploads.save(jpeg(64, 64), filename="a.jpg")
    pdf = uploads.save((FIX / "invoices-oct.pdf").read_bytes(), filename="inv.pdf")
    old = uploads.save(jpeg(32, 32), filename="old.jpg")
    history = [{"role": "user", "text": "earlier", "attachments": [old["upload_id"]]},
               {"role": "assistant", "text": "ok", "tool_calls": []},
               {"role": "user", "text": "order this list", "attachments": [img["upload_id"], pdf["upload_id"]]}]
    return history, img, pdf, old


def test_anthropic_message_builder_includes_images_and_pdfs():
    history, img, pdf, old = _history_with_photo()
    msgs = AnthropicProvider._messages(history)
    first, last = msgs[0]["content"], msgs[-1]["content"]
    assert [b["type"] for b in first] == ["text"] and f"[photo {old['upload_id']} sent earlier]" in first[0]["text"]
    assert [b["type"] for b in last] == ["image", "document", "text"]
    assert last[0]["source"]["type"] == "base64" and last[0]["source"]["media_type"] == "image/jpeg"
    assert last[1]["source"]["media_type"] == "application/pdf"


def test_openai_message_builder_includes_image_url_parts():
    history, *_ = _history_with_photo()
    msgs = OpenAICompatibleProvider._messages("sys", history)
    assert isinstance(msgs[1]["content"], str) and "sent earlier" in msgs[1]["content"]
    parts = msgs[-1]["content"]
    assert parts[0]["type"] == "image_url" and parts[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert parts[1]["type"] == "text" and "cannot read PDFs" in parts[1]["text"]  # PDFs: a note, not a crash
    assert parts[-1] == {"type": "text", "text": "order this list"}


async def test_scripted_list_photo_ends_in_intent_with_five_items(client):
    up = (await client.post("/uploads", files={"file": ("l.jpg", (FIX / "order-list.jpg").read_bytes(), "image/jpeg")})).json()
    s = agent.get_or_create_session(None, "photo-buyer")
    await agent.run_turn(s, "", images=[up["upload_id"]])
    types = [e["type"] for e in s.events]
    assert types[0] == "user_message" and s.events[0]["attachments"][0]["upload_id"] == up["upload_id"]
    it = next(e for e in s.events if e["type"] == "intent_proposed")["intent"]
    assert [(i["product"]["query"], i["quantity"]["amount"], i["quantity"]["unit"]) for i in it["items"]] == [
        ("milk", 120, "L"), ("butter", 10, "KG"), ("rye rolls", 300, "SALES_UNIT"), ("apples", 40, "KG"),
        ("granola", 15, "KG")]
    assert all(i["note"] == "from your photo" for i in it["items"])
    assert "[photo: milk 120 L; butter 10 kg" in it["original_request"]


async def test_menu_photo_without_request_does_not_order(client):
    up = (await client.post("/uploads", files={"file": ("m.jpg", (FIX / "menu-w47.jpg").read_bytes(), "image/jpeg")})).json()
    s = agent.get_or_create_session(None, "photo-buyer")
    await agent.run_turn(s, "", images=[up["upload_id"]])
    assert not any(e["type"] in ("tool_call", "intent_proposed") for e in s.events)
    msg = next(e for e in s.events if e["type"] == "message")["text"]
    assert "lunch menu" in msg and "Kitchen tab" in msg


async def test_chat_endpoint_accepts_attachments(client):
    up = (await client.post("/uploads", files={"file": ("l.jpg", (FIX / "order-list.jpg").read_bytes(), "image/jpeg")})).json()
    assert (await client.post("/chat", json={"message": ""})).status_code == 422
    assert (await client.post("/chat", json={"attachments": [{"upload_id": "up_missing"}]})).status_code == 404
    r = await client.post("/chat", json={"message": "", "attachments": [{"upload_id": up["upload_id"]}],
                                         "buyer_id": "photo-buyer"})
    assert r.status_code == 200
    s = agent.SESSIONS[r.json()["session_id"]]
    await wait_done(s)
    assert any(e["type"] == "intent_proposed" for e in s.events)


def test_anthropic_replays_thinking_only_for_the_current_turn():
    """Thinking signatures are bound to the system prompt, which is rebuilt each turn (it has the time in it).
    Old thinking blocks must be dropped, or Anthropic answers 400 'block is bound to a different conversation'."""
    think = {"type": "thinking", "thinking": "hm", "signature": "sig"}
    call = {"type": "tool_use", "id": "c1", "name": "propose_intent", "input": {}}
    history = [
        {"role": "user", "text": "120 L milk"},
        {"role": "assistant", "raw": [think, call]},
        {"role": "tool", "tool_call_id": "c1", "content": "{}"},
        {"role": "user", "text": "I signed intent int_1"},
        {"role": "assistant", "raw": [think, dict(call, id="c2")]},
        {"role": "tool", "tool_call_id": "c2", "content": "{}"},
    ]
    msgs = AnthropicProvider._messages(history)
    assistants = [m["content"] for m in msgs if m["role"] == "assistant"]
    assert [b["type"] for b in assistants[0]] == ["tool_use"]
    assert [b["type"] for b in assistants[1]] == ["thinking", "tool_use"]
