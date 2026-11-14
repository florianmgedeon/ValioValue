"""Package 1b: the kitchen profile over REST. Photos sent here learn, they never order (decision W2)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from . import config, ingest, kitchen, uploads, valio

router = APIRouter()


def _bid(buyer_id: str | None) -> str:
    bid = buyer_id or config.DEFAULT_BUYER_ID
    try:
        kitchen._safe(bid)
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    return bid


def mandate_summary(m: dict) -> dict:
    """Must / Prefer / Autonomy of the current mandate, with ids, for the Kitchen tab."""
    return {"mandate_id": m.get("mandate_id"), "version": m.get("version"), "fingerprint": m.get("fingerprint"),
            "must": [{k: r.get(k) for k in ("id", "label", "source", "enabled", "if_unknown", "attr", "op", "value")}
                     for r in m.get("mandatory_rules") or []],
            "prefer": [{k: r.get(k) for k in ("id", "label", "source", "enabled", "weight", "attr", "op", "value")}
                       for r in m.get("preferences") or []],
            "autonomy": [{k: g.get(k) for k in ("id", "label", "source", "enabled", "scope", "limits", "accepts")}
                         for g in (m.get("autonomy") or {}).get("grants") or []],
            "summary": m.get("summary")}


@router.get("/kitchen")
async def get_kitchen(buyer_id: str | None = None):
    bid = _bid(buyer_id)
    profile = kitchen.load(bid)
    try:
        ms = mandate_summary(await valio.mandate(bid))
    except Exception:  # noqa: BLE001 - the profile is useful without the mandate (Valio Aimo down)
        ms = None
    return profile | {"mandate_summary": ms}


@router.get("/kitchen/history")
async def kitchen_history(buyer_id: str | None = None):
    p = kitchen.load(_bid(buyer_id))
    return {"buyer_id": p["buyer_id"], "version": p["version"], "history": list(reversed(p.get("history") or []))}


class UsualPatch(BaseModel):
    add: list[dict] = []
    update: list[dict] = []
    remove: list[str] = []


class KitchenPatch(BaseModel):
    buyer_id: str | None = None
    about: dict | None = None
    usual: UsualPatch | None = None
    by: str | None = None


@router.patch("/kitchen")
async def patch_kitchen(body: KitchenPatch):
    """Edit About and the usual products directly (no suggestion needed: the human is typing it)."""
    bid = _bid(body.buyer_id)
    by = body.by or "buyer.human"
    resolved_add = [await ingest.resolve_usual(bid, u) for u in (body.usual.add if body.usual else [])]
    resolved_upd = []
    for u in body.usual.update if body.usual else []:
        if not u.get("id"):
            raise HTTPException(422, "usual.update entries need an id")
        if ("product_query" in u or "word" in u) and not u.get("product_id"):
            u = await ingest.resolve_usual(bid, u)
        resolved_upd.append(u)
    parts: list[str] = []

    def fn(p: dict):
        if body.about is not None and "text" in body.about:
            p["about"]["text"] = (body.about.get("text") or "").strip()
            parts.append("About text edited")
        for u in resolved_add:
            if not u.get("word"):
                raise HTTPException(422, "usual.add entries need a word")
            parts.append(kitchen.apply_usual(p, u, u.get("source_id")))
        for u in resolved_upd:
            cur = next((x for x in p["usual"] if x["id"] == u["id"]), None)
            if not cur:
                raise HTTPException(404, f"unknown usual product {u['id']}")
            cur.update({k: v for k, v in u.items() if k in ("word", "aliases", "product_id", "product_name",
                                                            "typical_qty", "unit", "per", "status")})
            parts.append(f'Usual "{cur["word"]}" edited')
        for uid in body.usual.remove if body.usual else []:
            cur = next((x for x in p["usual"] if x["id"] == uid), None)
            if not cur:
                raise HTTPException(404, f"unknown usual product {uid}")
            cur["status"] = "removed"
            parts.append(f'Usual "{cur["word"]}" removed')
        if not parts:
            raise HTTPException(422, "nothing to change")
        return "; ".join(parts)

    return kitchen.update(bid, fn, by, "edited")


# --------------------------------------------------------------------------- ingest

class IngestIn(BaseModel):
    buyer_id: str | None = None
    upload_ids: list[str] = []
    text: str | None = None
    title: str | None = None
    by: str | None = None


@router.post("/kitchen/ingest")
async def kitchen_ingest(request: Request):
    """Read documents into suggestions (status proposed). JSON {upload_ids?, text?, title?} or multipart `file`.
    One result per document, in order. Never orders anything."""
    ctype = request.headers.get("content-type", "")
    if ctype.startswith("multipart/"):
        form = await request.form()
        f = form.get("file")
        if f is None or not hasattr(f, "read"):
            raise HTTPException(422, "multipart needs a file")
        bid = _bid(form.get("buyer_id"))
        try:
            meta = uploads.save(await f.read(uploads.MAX_BYTES + 1), filename=f.filename, media_type=f.content_type,
                                buyer_id=bid)
        except uploads.UploadError as e:
            raise HTTPException(e.status, str(e)) from None
        body = IngestIn(buyer_id=bid, upload_ids=[meta["upload_id"]], title=form.get("title"), by=form.get("by"))
    else:
        try:
            body = IngestIn(**(await request.json()))
        except (ValueError, TypeError) as e:
            raise HTTPException(422, f"invalid body: {e}") from None
    bid = _bid(body.buyer_id)
    if not body.upload_ids and not (body.text or "").strip():
        raise HTTPException(422, "upload_ids or text required")
    for u in body.upload_ids:
        try:
            uploads.meta(u)
        except (FileNotFoundError, ValueError):
            raise HTTPException(404, f"unknown upload {u}") from None
    results = []
    for u in body.upload_ids:
        results.append(await ingest.ingest_one(bid, upload_id=u, title=body.title if len(body.upload_ids) == 1 else None,
                                               by=body.by or "kitchen agent"))
    if (body.text or "").strip():
        results.append(await ingest.ingest_one(bid, text=body.text, title=body.title if not body.upload_ids else None,
                                               by=body.by or "kitchen agent"))
    return {"buyer_id": bid, "results": results, **(results[0] if len(results) == 1 else {})}


# --------------------------------------------------------------------------- accept / reject

class AcceptIn(BaseModel):
    buyer_id: str | None = None
    ids: list[str]
    by: str | None = None
    mandate_hash: str | None = None


@router.post("/kitchen/suggestions/accept")
async def accept(body: AcceptIn):
    """Accept a batch. About and Usual go straight into the profile. Must, Prefer and Autonomy need a new signed
    mandate version: without mandate_hash the answer is 409 with the preview to show; with the previewed hash the
    backend signs, stores the mandate, then applies the rest. One request = one mandate and one profile version."""
    bid = _bid(body.buyer_id)
    by = body.by or "buyer.human"
    profile = kitchen.load(bid)
    by_id = {s["id"]: s for s in profile["suggestions"]}
    missing = [i for i in body.ids if i not in by_id]
    if missing:
        raise HTTPException(404, f"unknown suggestion {', '.join(missing)}")
    chosen = [by_id[i] for i in dict.fromkeys(body.ids)]
    done = [s["id"] for s in chosen if s["status"] != "proposed"]
    if done:
        raise HTTPException(409, {"error": f"already decided: {', '.join(done)}"})

    # resolve usual products first: nothing is applied if this or the mandate fails
    usual_payloads = {s["id"]: await ingest.resolve_usual(bid, s["payload"]) for s in chosen if s["kind"] == "usual"}
    signed = [s for s in chosen if s["kind"] in kitchen.SIGNED_KINDS]
    saved_mandate = None
    if signed:
        current = await valio.mandate(bid)
        usual = kitchen.usual_ids(profile) + [p["product_id"] for p in usual_payloads.values() if p.get("product_id")]
        new, diff = ingest.mandate_with(current, signed, profile, usual=list(dict.fromkeys(usual)))
        saved_mandate = await valio.change(bid, new, diff, body.mandate_hash, by)

    def fn(p: dict):
        parts = []
        for s in p["suggestions"]:
            if s["id"] not in body.ids:
                continue
            if s["kind"] == "about":
                parts.append(kitchen.apply_about(p, s["payload"], s.get("source_id")))
            elif s["kind"] == "usual":
                s["payload"] = usual_payloads[s["id"]]
                parts.append(kitchen.apply_usual(p, s["payload"], s.get("source_id")))
            else:
                parts.append(f"{s['kind'].capitalize()}: {s['title']}")
            s |= {"status": "accepted", "decided_by": by, "decided_at": kitchen._now()}
            if saved_mandate:
                s["mandate_version"] = saved_mandate["version"] if s["kind"] in kitchen.SIGNED_KINDS else None
        return "Accepted: " + "; ".join(parts)

    try:
        new_profile = kitchen.update(bid, fn, by, "accepted")
    except kitchen.ConflictError as e:
        raise HTTPException(409, {"error": str(e)}) from None
    return {"profile": new_profile, "mandate": mandate_summary(saved_mandate) if saved_mandate else None,
            "accepted": list(dict.fromkeys(body.ids))}


class RejectIn(BaseModel):
    buyer_id: str | None = None
    by: str | None = None


@router.post("/kitchen/suggestions/{suggestion_id}/reject")
async def reject(suggestion_id: str, body: RejectIn | None = None):
    body = body or RejectIn()
    bid = _bid(body.buyer_id)
    by = body.by or "buyer.human"

    def fn(p: dict):
        s = next((x for x in p["suggestions"] if x["id"] == suggestion_id), None)
        if not s:
            raise HTTPException(404, "unknown suggestion")
        if s["status"] != "proposed":
            raise HTTPException(409, {"error": f"already {s['status']}"})
        s |= {"status": "rejected", "decided_by": by, "decided_at": kitchen._now()}
        return f"Rejected: {s['title']}"

    return kitchen.update(bid, fn, by, "rejected")
