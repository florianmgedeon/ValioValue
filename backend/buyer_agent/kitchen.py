"""The kitchen profile (package 1b): About, Usual products, Sources, Suggestions. Lives on the buyer side.

One JSON file per buyer, DATA_DIR/kitchen/{buyer_id}.json. Every write bumps `version` and appends to
`history`. Writes are atomic (temp file + rename) and serialised per buyer. Signed things (Must, Prefer,
Autonomy) are NOT stored here: they are mandate entries at Valio Aimo. A suggestion of those kinds only becomes
active through a new, signed mandate version (see routes_kitchen.accept).

Nothing learned is active without a human: suggestions start as `proposed` (decision W7).
"""
from __future__ import annotations

import copy
import json
import os
import re
import tempfile
import threading
from pathlib import Path

from . import config

KINDS = ("about", "usual", "must", "prefer", "autonomy")
SIGNED_KINDS = ("must", "prefer", "autonomy")
SOURCE_TYPES = ("photo", "pdf", "text", "order", "correction")
FALLBACK_MODES = ("ask_any", "only_listed", "none")
PROMPT_MAX = 1500

_locks: dict[str, threading.RLock] = {}
_locks_guard = threading.Lock()


def _lock(buyer_id: str) -> threading.RLock:
    with _locks_guard:
        return _locks.setdefault(buyer_id, threading.RLock())


def _safe(buyer_id: str) -> str:
    if not re.fullmatch(r"[\w.-]{1,80}", buyer_id or "") or buyer_id.startswith("."):
        raise ValueError(f"invalid buyer_id {buyer_id!r}")
    return buyer_id


def _path(buyer_id: str) -> Path:
    return Path(config.KITCHEN_DIR) / f"{_safe(buyer_id)}.json"


def _now() -> str:
    from .agent import now_iso
    return now_iso()


def empty(buyer_id: str) -> dict:
    return {"buyer_id": buyer_id, "version": 0, "updated_at": None, "about": {"text": "", "facts": []},
            "usual": [], "suggestions": [], "sources": [], "history": []}


def _write(profile: dict) -> None:
    path = _path(profile["buyer_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(profile, f, ensure_ascii=False, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def load(buyer_id: str) -> dict:
    """The profile. Seeded from contracts/fixtures/kitchen/{buyer_id}.json on first use, else empty."""
    with _lock(buyer_id):
        path = _path(buyer_id)
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        seed = config.FIXTURES_DIR / "kitchen" / f"{_safe(buyer_id)}.json"
        if seed.exists():
            profile = json.loads(seed.read_text(encoding="utf-8"))
            profile["buyer_id"] = buyer_id
            _write(profile)
            return profile
        return empty(buyer_id)


def save(profile: dict, by: str, summary: str) -> dict:
    """Store a new version. The caller holds the profile it loaded; the version must still be current."""
    bid = profile["buyer_id"]
    with _lock(bid):
        cur = load(bid)
        if cur["version"] != profile["version"]:
            raise ConflictError(f"kitchen profile changed (now v{cur['version']}), reload it")
        out = copy.deepcopy(profile)
        out["version"] = cur["version"] + 1
        out["updated_at"] = _now()
        out.setdefault("history", []).append({"version": out["version"], "at": out["updated_at"], "by": by or "buyer.human",
                                              "summary": summary})
        _write(out)
        return out


class ConflictError(RuntimeError):
    pass


def update(buyer_id: str, fn, by: str, summary: str) -> dict:
    """Load, change with fn(profile), save, under the buyer's lock. fn may return a better summary."""
    with _lock(buyer_id):
        p = load(buyer_id)
        better = fn(p)
        return save(p, by, better or summary)


def _next_id(items: list[dict], prefix: str) -> str:
    nums = [int(m.group(1)) for x in items if (m := re.fullmatch(rf"{prefix}(\d+)", str(x.get("id", ""))))]
    return f"{prefix}{max(nums, default=0) + 1}"


def add_source(profile: dict, source: dict) -> dict:
    """Append a source (photo, pdf, text, order, correction) and return it with its id."""
    src = {k: v for k, v in source.items() if v is not None}
    if src.get("type") not in SOURCE_TYPES:
        raise ValueError(f"source type must be one of {', '.join(SOURCE_TYPES)}")
    src["id"] = _next_id(profile["sources"], "s")
    src.setdefault("title", src["type"])
    src.setdefault("created_at", _now())
    profile["sources"].append(src)
    return src


def _same(a: dict, b: dict) -> bool:
    return a["kind"] == b["kind"] and json.dumps(a.get("payload"), sort_keys=True) == json.dumps(
        b.get("payload"), sort_keys=True)


def add_suggestions(buyer_id: str, items: list[dict], source: dict, by: str = "kitchen agent") -> list[dict]:
    """Save suggestions (status `proposed`) from one source. Returns them with ids. Exact duplicates of a
    suggestion that is still proposed are skipped. Used by ingest (1b) and learning in chat (2a)."""
    added: list[dict] = []

    def fn(p: dict):
        src = source if source.get("id") and any(s["id"] == source["id"] for s in p["sources"]) else add_source(p, source)
        source.update(src)  # the caller learns the source id
        for it in items:
            if it.get("kind") not in KINDS:
                continue
            s = {"id": _next_id(p["suggestions"], "sg"), "kind": it["kind"], "status": "proposed",
                 "title": it.get("title") or it["kind"], "quote": it.get("quote") or "", "source_id": src["id"],
                 "payload": it.get("payload") or {}, "created_at": _now()}
            if any(_same(s, o) and o["status"] == "proposed" for o in p["suggestions"]):
                continue
            p["suggestions"].append(s)
            added.append(s)
        n = len(added)
        return f"{n} suggestion{'s' * (n != 1)} from {src.get('title')}"

    update(buyer_id, fn, by, "suggestions added")
    return added


def source(profile: dict, source_id: str | None) -> dict | None:
    return next((s for s in profile["sources"] if s["id"] == source_id), None)


def apply_about(profile: dict, payload: dict, source_id: str | None) -> str:
    about = profile.setdefault("about", {"text": "", "facts": []})
    if payload.get("fact"):
        about.setdefault("facts", []).append({"id": _next_id(about.get("facts") or [], "f"), "label": payload["fact"],
                                              "source_id": source_id})
        return f"About: {payload['fact']}"
    text = (payload.get("text") or "").strip()
    if text and text.lower() not in (about.get("text") or "").lower():
        about["text"] = ((about.get("text") or "").rstrip() + " " + text).strip()
    return f"About: {text}"


def normalise_substitutes(subs: list | None, primary_id: str | None = None) -> list[dict]:
    """Deduplicate substitute entries in order, excluding the primary product id."""
    out: list[dict] = []
    seen: set[str] = {str(primary_id)} if primary_id else set()
    for raw in subs or []:
        s = {"product_id": raw, "product_name": None} if isinstance(raw, str) else dict(raw or {})
        pid = str(s["product_id"]).strip() if s.get("product_id") else None
        if pid:
            if pid in seen:
                continue
            seen.add(pid)
        item: dict = {"product_id": pid, "product_name": s.get("product_name")}
        if s.get("candidates"):
            item["candidates"] = s["candidates"]
        if pid or item.get("candidates"):
            out.append(item)
    return out


def normalise_fallback_mode(mode: str | None, has_substitutes: bool) -> str:
    if mode is None:
        return "only_listed" if has_substitutes else "ask_any"
    if mode not in FALLBACK_MODES:
        raise ValueError(f"fallback_mode must be one of {', '.join(FALLBACK_MODES)}")
    return mode


def apply_usual(profile: dict, payload: dict, source_id: str | None) -> str:
    """Add or replace the usual product for a word. Payload must already be resolved (product_id or None)."""
    word = (payload.get("word") or "").strip().lower()
    subs = normalise_substitutes(payload.get("substitutes"), payload.get("product_id"))
    mode = normalise_fallback_mode(payload.get("fallback_mode"), bool(subs))
    entry = {"word": word, "aliases": [a.lower() for a in payload.get("aliases") or []],
             "product_id": payload.get("product_id"), "product_name": payload.get("product_name"),
             "substitutes": subs, "fallback_mode": mode,
             "typical_qty": payload.get("typical_qty"), "unit": payload.get("unit"), "per": payload.get("per") or "week",
             "source_id": source_id, "status": "active"}
    old = next((u for u in profile["usual"] if u["word"] == word and u.get("status", "active") == "active"), None)
    if old:
        old["status"] = "replaced"
    entry["id"] = _next_id(profile["usual"], "u")
    profile["usual"].append(entry)
    return f'Usual: "{word}" means {entry["product_name"] or "a product still to choose"}'


def usual_ids(profile: dict) -> list[str]:
    """Product ids covered by level-1 autonomy: every active usual product plus its approved substitutes."""
    ids: list[str] = []
    for u in profile.get("usual") or []:
        if u.get("status", "active") != "active":
            continue
        if u.get("product_id"):
            ids.append(u["product_id"])
        if u.get("fallback_mode", "ask_any") != "none":
            for s in u.get("substitutes") or []:
                if s.get("product_id"):
                    ids.append(s["product_id"])
    return list(dict.fromkeys(ids))


def enrich_intent_items(buyer_id: str, args: dict) -> dict:
    """Attach configured substitute_product_ids and allow_other_substitutes from the kitchen profile onto
    propose_intent items, so an LLM cannot forget or bypass a usual product's fallback rule."""
    items_in = args.get("items")
    if not isinstance(items_in, list) or not items_in:
        return args
    profile = load(buyer_id)
    active = [u for u in profile.get("usual") or [] if u.get("status", "active") == "active" and u.get("product_id")]
    if not active:
        return args
    by_pid = {u["product_id"]: u for u in active}
    by_word: dict[str, dict] = {}
    for u in active:
        for w in [u["word"], *(u.get("aliases") or [])]:
            wl = w.strip().lower()
            for form in (wl, f"{wl}s", f"{wl}es"):
                by_word.setdefault(form, u)

    out_items = []
    for raw in items_in:
        it = dict(raw)
        u = by_pid.get(it.get("preferred_product_id"))
        if not u and not it.get("preferred_product_id"):
            q = (it.get("product_query") or "").strip().lower()
            u = by_word.get(q)
        if u:
            sub_ids = [s["product_id"] for s in (u.get("substitutes") or []) if s.get("product_id")]
            mode = u.get("fallback_mode") or ("only_listed" if sub_ids else "ask_any")
            if not it.get("preferred_product_id"):
                it["preferred_product_id"] = u["product_id"]
            if mode == "none":
                it.pop("substitute_product_ids", None)
                it["allow_other_substitutes"] = False
            elif mode == "only_listed":
                if sub_ids:
                    it["substitute_product_ids"] = sub_ids
                else:
                    it.pop("substitute_product_ids", None)
                it["allow_other_substitutes"] = False
            elif sub_ids and not it.get("substitute_product_ids"):
                it["substitute_product_ids"] = sub_ids
        out_items.append(it)
    return dict(args, items=out_items)


def _unit(u: str | None) -> str | None:
    return {"L": "L", "l": "L", "KG": "kg", "kg": "kg", "SALES_UNIT": "units", "pcs": "units", "units": "units",
            "unit": "units"}.get(u or "")


def _num(x) -> str:
    return f"{float(x):g}"


def _fallback_prompt_suffix(u: dict) -> str:
    subs = [s for s in (u.get("substitutes") or []) if s.get("product_id")]
    mode = u.get("fallback_mode") or ("only_listed" if subs else "ask_any")
    if mode == "none":
        return "; no substitutes allowed (allow_other_substitutes=false)"
    if mode == "only_listed":
        if subs:
            names = ", ".join(f"{s.get('product_name') or s['product_id']} (product {s['product_id']})" for s in subs)
            return f"; fallback ONLY: {names} (allow_other_substitutes=false)"
        return "; no substitutes allowed (allow_other_substitutes=false)"
    if subs:
        names = ", ".join(f"{s.get('product_name') or s['product_id']} (product {s['product_id']})" for s in subs)
        return f"; fallback first: {names}"
    return ""


def prompt_block(buyer_id: str) -> str:
    """Plain text for the agent's system prompt: About, then the usual products, max ~1,500 characters.
    Lines look like: - "milk" (also: maito) → Valio Eila® … (product 463038), usually 120 L a week"""
    p = load(buyer_id)
    about = (p.get("about") or {}).get("text") or ""
    facts = [f["label"] for f in (p.get("about") or {}).get("facts") or [] if f["label"].lower() not in about.lower()]
    lines = []
    if about or facts:
        lines.append("About: " + " ".join([about] + [f + "." for f in facts]).strip())
    usual = [u for u in p.get("usual") or [] if u.get("status", "active") == "active"]
    if usual:
        lines.append("Usual products (the buyer's word → their product):")
        for u in usual:
            alias = f" (also: {', '.join(u['aliases'])})" if u.get("aliases") else ""
            if u.get("product_id"):
                amount = (f", usually {_num(u['typical_qty'])} {_unit(u.get('unit'))} a {u.get('per') or 'week'}"
                          if u.get("typical_qty") and _unit(u.get("unit")) else "")
                fb = _fallback_prompt_suffix(u)
                lines.append(f'- "{u["word"]}"{alias} → {u.get("product_name") or u["product_id"]} '
                             f'(product {u["product_id"]}){amount}{fb}')
            else:
                lines.append(f'- "{u["word"]}"{alias} → no product chosen yet, search normally')
    text = "\n".join(lines)
    if len(text) > PROMPT_MAX:  # keep whole lines; usual products matter more than a long About text
        keep, size = [], 0
        for ln in lines[1:] if lines and lines[0].startswith("About") else lines:
            if size + len(ln) + 1 > PROMPT_MAX - 200:
                break
            keep.append(ln)
            size += len(ln) + 1
        head = lines[0][: PROMPT_MAX - size - 5] + "…" if lines and lines[0].startswith("About") else ""
        text = "\n".join([head] + keep if head else keep)
    return text
