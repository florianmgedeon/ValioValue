"""Uploaded photos and PDFs. Stored on disk, read by the chat (1a), the kitchen ingest (1b) and 2a.

  DATA_DIR/uploads/{upload_id}.{ext}    the bytes (images downscaled to 1600 px, JPEG q85; PDFs as they are)
  DATA_DIR/uploads/{upload_id}.json     metadata (shared contract in docs/work-packages/README.md)

Demo fixtures in contracts/fixtures/uploads/ carry a `text` (what the photo says) and a `fixture` name. When the
same file is uploaded, both are copied into the metadata, so the scripted provider works without vision.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import secrets
from pathlib import Path

from . import config

log = logging.getLogger("buyer_agent.uploads")

MAX_BYTES = 15 * 1024 * 1024
MAX_PDF_BYTES = 10 * 1024 * 1024
MAX_EDGE = 1600
IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}
PDF = "application/pdf"
EXT = {"image/jpeg": "jpg", "application/pdf": "pdf"}


class UploadError(ValueError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _dir() -> Path:
    d = Path(config.UPLOADS_DIR)
    d.mkdir(parents=True, exist_ok=True)
    return d


def sniff(data: bytes, declared: str | None, filename: str | None) -> str:
    """The real media type from the first bytes. Browsers send odd types for HEIC (or none at all)."""
    head = data[:16]
    if head.startswith(b"%PDF"):
        return PDF
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG"):
        return "image/png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head[4:8] == b"ftyp" and head[8:12] in (b"heic", b"heix", b"mif1", b"msf1", b"heim", b"heis", b"hevc"):
        return "image/heic"
    return (declared or "").split(";")[0].strip().lower() or "application/octet-stream"


def _image(data: bytes) -> tuple[bytes, int, int]:
    """Fix EXIF orientation, downscale to MAX_EDGE on the long edge, JPEG quality 85."""
    from PIL import Image, ImageOps
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except ImportError:  # HEIC is only needed for iPhone photos
        pass
    try:
        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img)
    except Exception as e:  # noqa: BLE001 - any decoder error means we cannot use the file
        raise UploadError(415, f"cannot read the image: {e}") from None
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    img.thumbnail((MAX_EDGE, MAX_EDGE))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=85, optimize=True)
    return out.getvalue(), img.width, img.height


def _fixture_meta(sha: str, filename: str | None) -> dict:
    """Fields a demo fixture adds (text for the scripted provider, fixture name for ingest)."""
    d = config.FIXTURES_DIR / "uploads"
    if not d.exists():
        return {}
    stem = Path(filename or "").stem
    for meta_file in sorted(d.glob("*.json")):
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        except ValueError:
            continue
        twin = next((p for p in d.glob(meta_file.stem + ".*") if p.suffix != ".json"), None)
        same = twin is not None and hashlib.sha256(twin.read_bytes()).hexdigest() == sha
        if same or (stem and stem == meta_file.stem):
            return {k: meta[k] for k in ("text", "fixture", "document_type") if k in meta} | {
                "fixture": meta.get("fixture") or meta_file.stem}
    return {}


def save(data: bytes, *, filename: str | None = None, media_type: str | None = None, buyer_id: str | None = None,
         source: str = "upload", pair_id: str | None = None) -> dict:
    if len(data) > MAX_BYTES:
        raise UploadError(413, f"file is larger than {MAX_BYTES // (1024 * 1024)} MB")
    if not data:
        raise UploadError(422, "empty file")
    kind = sniff(data, media_type, filename)
    sha = hashlib.sha256(data).hexdigest()
    width = height = None
    if kind in IMAGE_TYPES:
        data, width, height = _image(data)
        kind = "image/jpeg"
    elif kind == PDF:
        if len(data) > MAX_PDF_BYTES:
            raise UploadError(413, "PDF is larger than 10 MB")
    else:
        raise UploadError(415, f"unsupported type {kind}. Send JPEG, PNG, WebP, HEIC or PDF")
    from .agent import now_iso
    upload_id = f"up_{secrets.token_hex(4)}"
    meta = {"upload_id": upload_id, "media_type": kind, "filename": filename or f"{upload_id}.{EXT[kind]}",
            "bytes": len(data), "width": width, "height": height, "created_at": now_iso(),
            "buyer_id": buyer_id or config.DEFAULT_BUYER_ID, "source": source if source in ("phone", "upload") else "upload",
            "pair_id": pair_id, "sha256": sha} | _fixture_meta(sha, filename)
    d = _dir()
    (d / f"{upload_id}.{EXT[kind]}").write_bytes(data)
    (d / f"{upload_id}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return meta


def _safe(upload_id: str) -> str:
    if not upload_id.startswith("up_") or not upload_id[3:].isalnum():
        raise FileNotFoundError(upload_id)
    return upload_id


def meta(upload_id: str) -> dict:
    return json.loads((_dir() / f"{_safe(upload_id)}.json").read_text(encoding="utf-8"))


def path(upload_id: str) -> Path:
    m = meta(upload_id)
    return _dir() / f"{_safe(upload_id)}.{EXT[m['media_type']]}"


def load(upload_id: str) -> tuple[dict, bytes]:
    """(meta, bytes). Raises FileNotFoundError for an unknown id."""
    return meta(upload_id), path(upload_id).read_bytes()


def public_url(upload_id: str) -> str:
    return f"/uploads/{upload_id}"
