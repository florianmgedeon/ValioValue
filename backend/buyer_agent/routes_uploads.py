"""Package 1a: uploads, phone pairing (phone as camera), the phone page."""
from __future__ import annotations

import asyncio
import json
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from . import config, uploads

router = APIRouter()
PAIRING_TTL_S = 30 * 60


@dataclass
class Pairing:
    pair_id: str
    buyer_id: str
    purpose: str
    created: float = field(default_factory=time.time)
    events: list[dict] = field(default_factory=list)
    subscribers: set[asyncio.Queue] = field(default_factory=set)

    @property
    def expired(self) -> bool:
        return time.time() - self.created > PAIRING_TTL_S

    def emit(self, type_: str, **payload) -> None:
        evt = {"type": type_, "pair_id": self.pair_id, **payload}
        self.events.append(evt)
        for q in list(self.subscribers):
            q.put_nowait(evt)


PAIRINGS: dict[str, Pairing] = {}


def _pairing(pair_id: str | None) -> Pairing | None:
    for pid in [k for k, p in PAIRINGS.items() if p.expired]:
        PAIRINGS.pop(pid, None)
    return PAIRINGS.get(pair_id or "")


@router.post("/uploads")
async def upload(file: UploadFile = File(...), buyer_id: str | None = Form(None), pair_id: str | None = Form(None),
                 source: str | None = Form(None)):
    """Store a photo or PDF. Images are downscaled to 1600 px (JPEG q85). Returns the upload meta."""
    data = await file.read(uploads.MAX_BYTES + 1)
    pairing = _pairing(pair_id) if pair_id else None
    if pair_id and not pairing:
        raise HTTPException(404, "unknown or expired pairing. Scan the QR code again.")
    try:
        meta = uploads.save(data, filename=file.filename, media_type=file.content_type,
                            buyer_id=buyer_id or (pairing.buyer_id if pairing else None),
                            source=source or ("phone" if pairing else "upload"), pair_id=pair_id)
    except uploads.UploadError as e:
        raise HTTPException(e.status, str(e)) from None
    if pairing:
        pairing.emit("photo_received", upload_id=meta["upload_id"], url=uploads.public_url(meta["upload_id"]),
                     media_type=meta["media_type"], purpose=pairing.purpose)
    return meta | {"url": uploads.public_url(meta["upload_id"])}


@router.get("/uploads/{upload_id}")
async def get_upload(upload_id: str):
    try:
        meta = uploads.meta(upload_id)
        return FileResponse(uploads.path(upload_id), media_type=meta["media_type"],
                            headers={"Cache-Control": "private, max-age=86400"})
    except (FileNotFoundError, KeyError):
        raise HTTPException(404, "unknown upload") from None


@router.get("/uploads/{upload_id}/meta")
async def get_upload_meta(upload_id: str):
    try:
        return uploads.meta(upload_id) | {"url": uploads.public_url(upload_id)}
    except FileNotFoundError:
        raise HTTPException(404, "unknown upload") from None


class PairingIn(BaseModel):
    buyer_id: str | None = None
    purpose: str = "order"  # order | kitchen: where the desktop sends the photo (decision W2)


@router.post("/pairings")
async def create_pairing(body: PairingIn | None = None):
    body = body or PairingIn()
    if body.purpose not in ("order", "kitchen"):
        raise HTTPException(422, "purpose must be order or kitchen")
    p = Pairing(f"pair_{secrets.token_hex(4)}", body.buyer_id or config.DEFAULT_BUYER_ID, body.purpose)
    PAIRINGS[p.pair_id] = p
    from .agent import TZ
    expires = datetime.fromtimestamp(p.created, TZ) + timedelta(seconds=PAIRING_TTL_S)
    return {"pair_id": p.pair_id, "phone_url": f"{config.PUBLIC_BASE_URL}/phone?pair={p.pair_id}",
            "purpose": p.purpose, "expires_at": expires.isoformat(timespec="seconds")}


@router.delete("/pairings/{pair_id}", status_code=204)
async def end_pairing(pair_id: str):
    """The desktop closed the camera dialog: further phone uploads get a 404 ("scan the QR code again")."""
    p = PAIRINGS.pop(pair_id, None)
    if p:
        p.emit("expired")


@router.get("/pairings/{pair_id}/stream")
async def pairing_stream(pair_id: str):
    """SSE for the desktop: `paired` when the phone opens the page, `photo_received` per photo (any number)."""
    p = _pairing(pair_id)
    if not p:
        raise HTTPException(404, "unknown or expired pairing")
    queue: asyncio.Queue = asyncio.Queue()
    backlog = list(p.events)
    p.subscribers.add(queue)

    def frame(evt: dict) -> str:
        return f"event: {evt['type']}\ndata: {json.dumps(evt, ensure_ascii=False)}\n\n"

    async def gen():
        try:
            for evt in backlog:
                yield frame(evt)
            while not p.expired:
                try:
                    evt = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                yield frame(evt)
                if evt["type"] == "expired":  # ended by the desktop
                    return
            yield frame({"type": "expired", "pair_id": p.pair_id})
        finally:
            p.subscribers.discard(queue)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/phone", include_in_schema=False)
async def phone(pair: str | None = None):
    """The phone camera page. Opening it marks the pairing as paired."""
    p = _pairing(pair)
    if p and not any(e["type"] == "paired" for e in p.events):
        p.emit("paired", purpose=p.purpose)
    return FileResponse(Path(__file__).parent / "static" / "phone.html", headers={"Cache-Control": "no-store"})
