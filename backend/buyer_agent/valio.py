"""Human-side calls to Valio Aimo (approver token), shared by the kitchen (1b) and autonomy (2b) routes.

The mandate round trip is the same everywhere: build the new mandate, preview it, show the hash to the human,
and only with that hash echoed back sign and store it. One request = one mandate version.
"""
from __future__ import annotations

import httpx
from fastapi import HTTPException

from . import config, signing

MANDATE_KEYS = ("display_name", "currency", "optimise", "mandatory_rules", "preferences", "autonomy")


async def call(method: str, path: str, **kw) -> httpx.Response:
    async with httpx.AsyncClient(timeout=20) as c:
        return await c.request(method, f"{config.MCP_BASE_URL}{path}",
                               headers={"Authorization": f"Bearer {config.APPROVER_TOKEN}"}, **kw)


def passthrough(r: httpx.Response):
    if r.status_code >= 400:
        ct = r.headers.get("content-type", "")
        raise HTTPException(r.status_code, r.json() if ct.startswith("application/json") else r.text)
    return r.json()


async def mandate(buyer_id: str) -> dict:
    return passthrough(await call("GET", f"/internal/mandate/{buyer_id}"))


def editable(m: dict) -> dict:
    """The parts of a stored mandate that a new version is built from (ids and labels are kept)."""
    return {k: m[k] for k in MANDATE_KEYS if k in m}


def fresh_id(m: dict, prefix: str) -> str:
    taken = {x.get("id") for x in (m.get("mandatory_rules") or []) + (m.get("preferences") or [])
             + ((m.get("autonomy") or {}).get("grants") or [])}
    n = 1
    while f"{prefix}{n}" in taken:
        n += 1
    return f"{prefix}{n}"


async def preview(buyer_id: str, new: dict) -> tuple[dict | None, str | None]:
    """(preview, None) or (None, error message) if Valio Aimo refuses the mandate (422)."""
    r = await call("POST", f"/internal/mandate/{buyer_id}/preview", json={"mandate": new})
    if r.status_code == 422:
        return None, (r.json() or {}).get("error") or r.text
    return passthrough(r), None


async def put_signed(buyer_id: str, new: dict, mandate_hash: str, by: str) -> dict:
    """Sign the previewed hash with the buyer key and store the version."""
    return passthrough(await call("PUT", f"/internal/mandate/{buyer_id}", json={
        "mandate": new, "mandate_hash": mandate_hash, "signature": signing.sign(buyer_id, mandate_hash),
        "updated_by": by}))


def needs_signature(new: dict, pre: dict, diff: list[str]) -> HTTPException:
    return HTTPException(409, {"needs_signature": True, "preview": {
        "mandate": new, "mandate_hash": pre["mandate_hash"], "version": pre["version"],
        "fingerprint": pre.get("fingerprint"), "summary": pre.get("summary"), "cedar": pre.get("cedar"),
        "diff": diff}})


async def change(buyer_id: str, new: dict, diff: list[str], mandate_hash: str | None, by: str) -> dict:
    """The signature round trip. Without mandate_hash (or with an outdated one): 409 with the preview.
    With the previewed hash: signed and stored, returns the new version."""
    pre, err = await preview(buyer_id, new)
    if err:
        raise HTTPException(422, {"error": err})
    if not mandate_hash or mandate_hash != pre["mandate_hash"]:
        raise needs_signature(new, pre, diff)
    return await put_signed(buyer_id, new, mandate_hash, by)
