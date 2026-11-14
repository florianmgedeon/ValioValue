"""Mandates and the order decision. Runs on the Valio Aimo side, so the agent cannot talk its way around it.

Mandates are versioned in SQLite (seeded from policies.json). Every change is a new version with a new hash,
signed by the buyer. Orders are always decided against the CURRENT version; the receipt names it.

Decision per order line, in Cedar (see mandate.py):
  `order` denied        -> block  (a must, or a technical rule: binding, signature, expiry, cut-off)
  `order_alone` allowed -> auto   (a grant covers this line, its scope, limits and every deviation)
  otherwise             -> ask    (a human signs the exact cart)
Grant limits on the order total are summed here over the lines each grant covers.
The order's tier is the worst line tier.
"""
from __future__ import annotations

import json
from datetime import date
import cedarpy

from . import config, constraints, mandate as M, trust
from .catalog import catalog
from .rules import RuleError
from .store import iso, store

STATUS = {"auto": "confirmed", "ask": "pending_approval", "block": "rejected"}


# --------------------------------------------------------------------------- mandates

def _seed_file() -> dict:
    return json.loads(config.POLICY_FILE.read_text(encoding="utf-8"))


def _finish(m: dict, buyer_id: str, version: int) -> dict:
    m = dict(m, buyer_id=buyer_id, version=version)
    m["mandate_id"] = f"mandate_{buyer_id}_v{version}"
    m["mandate_hash"] = trust.digest(M.content(m))
    m["fingerprint"] = trust.fingerprint(m["mandate_hash"])
    return m


def _resolve(buyer_id: str | None) -> str:
    bid = buyer_id or "default"
    if bid == "_doc":
        return "default"
    if store().latest_mandate(bid) or bid in _seed_file():
        return bid
    return "default"


def buyer_policy(buyer_id: str | None) -> dict:
    """The current mandate. Unknown buyers get the default mandate (as before)."""
    bid = _resolve(buyer_id)
    m = store().latest_mandate(bid)
    if m:
        return m
    m = _finish(M.normalise(_seed_file()[bid]), bid, 1) | {"updated_by": "seed (policies.json)",
                                                            "updated_at": iso(), "signature": None}
    try:
        store().save_mandate(m)
    except Exception:  # another request seeded it at the same moment
        return store().latest_mandate(bid) or m
    return m


def preview(buyer_id: str, raw: dict) -> dict:
    """Validate a new mandate and return it as it would be stored (next version, hash). Nothing is saved."""
    cur = store().latest_mandate(buyer_id) or (buyer_policy(buyer_id) if buyer_id in _seed_file() else None)
    version = (cur["version"] + 1) if cur else 1
    return _finish(M.normalise(raw), buyer_id, version)


def save(buyer_id: str, raw: dict, *, mandate_hash: str, signature: str | None, updated_by: str) -> dict:
    """Store a new version. The hash must be the one previewed (what you see is what you sign)."""
    m = preview(buyer_id, raw)
    if mandate_hash != m["mandate_hash"]:
        raise PermissionError("mandate_hash does not match the mandate. Preview it again.")
    if not trust.verify(trust.buyer_public(buyer_id), m["mandate_hash"], signature):
        raise PermissionError("buyer signature over mandate_hash is invalid")
    m |= {"updated_by": updated_by, "updated_at": iso(), "signature": signature}
    to_cedar(m, list(m["mandatory_rules"]))  # must compile, else refuse
    store().save_mandate(m)
    return m


def to_cedar(m: dict, musts: list[dict]) -> tuple[str, list[str]]:
    """Cedar text for this mandate plus the musts of one decision. Raises RuleError if it does not parse."""
    text, ids = M.to_cedar(m, [r for r in musts if r.get("enabled", True)])
    try:
        cedarpy.format_policies(text)
    except Exception as e:  # noqa: BLE001 - any parse failure means the mandate is not enforceable
        raise RuleError(f"mandate does not compile to Cedar: {e}") from None
    return text, ids


def catalog_info() -> dict:
    """Everything a policy editor needs: attributes with types, operators and example values from the data."""
    from collections import Counter

    from .rules import ATTRIBUTES, OPS
    prods = list(catalog().products.values())
    examples: dict[str, list] = {}
    for a, (typ, where, _) in ATTRIBUTES.items():
        if where != "product" or typ not in ("string", "set") or a in ("name", "product_id"):
            continue
        cnt: Counter = Counter()
        for p in prods:
            v = constraints.product_value(p, a)
            if a in ("allergens", "allergens_contains"):
                v = set(p.allergens_contains) | (set(p.allergens_may_contain) if a == "allergens" else set())
            for x in (v if isinstance(v, (list, set)) else [v]):
                if x:
                    cnt[str(x)] += 1
        examples[a] = [x for x, _ in cnt.most_common(25)]
    return {"attributes": [{"attr": a, "type": t, "source": w, "description": d, "ops": list(OPS[t]),
                            "examples": examples.get(a, [True, False] if t == "bool" else [])}
                           for a, (t, w, d) in ATTRIBUTES.items()],
            "accept_checks": [{"check": "quantity", "params": {"max_over_pct": "number"}},
                              {"check": "delivery_date", "params": {"max_days_late": "integer"}},
                              {"check": "preference", "params": {"id": "preference id, empty = all"}},
                              {"check": "product", "params": {}}, {"check": "budget", "params": {}},
                              {"check": "availability", "params": {}}],
            "limits": list(M.LIMIT_KEYS), "optimise": list(M.OPTIMISE), "if_unknown": ["exclude", "ask"],
            "products": len(prods)}


# --------------------------------------------------------------------------- decision

def _dec(x: float | int | None) -> dict:
    return {"__extn": {"fn": "decimal", "arg": f"{float(x or 0):.4f}"}}


def _entity_attrs(f: dict) -> dict:
    return {k: (_dec(v) if isinstance(v, float) and not isinstance(v, bool) else v) for k, v in f.items()}


def _days_late(by: str, line: dict) -> int:
    try:
        d = (date.fromisoformat(line.get("available_date") or by) - date.fromisoformat(by)).days
    except ValueError:
        return 999
    return max(d, 0)


def decide(intent: dict, d: dict, lines: list[tuple[str, dict]], m: dict, musts_by_item: dict[str, list[dict]]
           ) -> dict:
    """Tier for an order from the diff checks, via Cedar. Pure function of its inputs, so /verify can rerun it.

    lines: (item_id, quote line) per cart line. musts_by_item: the musts (with ids) that applied per item."""
    checks = d["checks"]
    # Checks without a line of their own (whole cart, or a line whose item or quote is missing) apply to every line,
    # so a block there can never get lost.
    line_ids = {iid for iid, _ in lines}
    order_level = [c for c in checks if not c.get("item_id") or c["item_id"] not in line_ids]
    principal = {"type": "Buyer", "id": m["buyer_id"]}
    inputs = []
    for iid, line in lines:
        mine = [c for c in checks if c.get("item_id") == iid]
        failed = [c for c in mine + order_level if not c["ok"]]
        tech = sorted({c["field"] for c in failed if c["severity"] == "block" and c["field"] != "hard_rules"})
        verdicts = {ev["id"]: ev["verdict"] for ev in d["evidence"].get(iid, []) if ev.get("id")}
        devs = []
        for c in failed:
            if c["severity"] != "ask":
                continue
            if c["field"] == "preference":
                devs.append(f"preference:{c.get('pref_id')}")
            elif c["field"] == "hard_rules":
                devs.append(f"must_unknown:{(c.get('evidence') or {}).get('id')}")
            else:
                devs.append(c["field"])
        qty = next((c for c in mine if c["field"] == "quantity"), {})
        prod = catalog().get(line["product_id"])
        facts = constraints.facts(prod, line) if prod else {"product_id": line["product_id"]}
        ctx = {"technical_blocks": tech, "verdicts": verdicts, "deviations": sorted(set(devs)),
               "quantity_over_pct": float(qty.get("over_pct") or 0), "days_late": _days_late(intent["deliver_by"], line),
               "line_total": float(line["total_price"]), "unit_price": float(line["unit_price"])}
        inputs.append({"item_id": iid, "product_id": line["product_id"], "facts": facts, "context": ctx,
                       "musts": [r["id"] for r in musts_by_item.get(iid, [])],
                       "python_block": any(c["severity"] == "block" for c in failed)})
    return evaluate(m, inputs, musts_by_item, principal)


def evaluate(m: dict, inputs: list[dict], musts_by_item: dict[str, list[dict]], principal: dict) -> dict:
    all_musts = {r["id"]: r for rs in musts_by_item.values() for r in rs}
    text, ids = to_cedar(m, list(all_musts.values()))
    grant_ids = {g["id"] for g in m["autonomy"]["grants"]}
    out_lines = []
    for inp in inputs:
        res_uid = {"type": "Product", "id": inp["product_id"]}
        entities = [{"uid": principal, "attrs": {}, "parents": []},
                    {"uid": res_uid, "attrs": _entity_attrs(inp["facts"]), "parents": []}]
        ctx = dict(inp["context"])
        ctx |= {k: _dec(ctx[k]) for k in ("quantity_over_pct", "line_total", "unit_price")}

        def ask(action: str):
            req = {"principal": f'Buyer::{json.dumps(principal["id"])}', "action": f'Action::"{action}"',
                   "resource": f'Product::{json.dumps(inp["product_id"])}', "context": ctx}
            r = cedarpy.is_authorized(req, text, entities)
            reasons = [ids[int(x.replace("policy", ""))] for x in r.diagnostics.reasons
                       if x.startswith("policy") and x[6:].isdigit() and int(x[6:]) < len(ids)]
            return r.allowed, reasons, list(r.diagnostics.errors)

        ok, reasons, errors = ask("order")
        line = {"item_id": inp["item_id"], "product_id": inp["product_id"], "deviations": ctx["deviations"],
                "line_total": inp["context"]["line_total"], "errors": [str(e) for e in errors]}
        if not ok or inp["python_block"] or errors:
            line |= {"tier": "block", "blocked_by": [r for r in reasons if r != "base"] or
                     (["evaluation_error"] if errors else ["technical"])}
        else:
            alone, why, errs = ask("order_alone")
            line |= {"tier": "auto" if alone and not errs else "ask",
                     "grants": [g for g in why if g in grant_ids] if alone else [],
                     "errors": line["errors"] + [str(e) for e in errs]}
        out_lines.append(line)

    # Order-total limits: every autonomous line is assigned to ONE grant that permits it, and a grant's
    # max_order_total counts only the lines assigned to it. Lines with a single option go first, then each line
    # takes the permitting grant with the most room left. Deterministic, so /verify gets the same answer.
    caps = {g["id"]: g["limits"].get("max_order_total", float("inf")) for g in m["autonomy"]["grants"]}
    room = dict(caps)
    exhausted: dict[str, tuple[float, float]] = {}
    auto_lines = sorted((l for l in out_lines if l["tier"] == "auto"),
                        key=lambda l: (len(l["grants"]), -l["line_total"], l["item_id"]))
    for l in auto_lines:
        fits = [g for g in l["grants"] if room[g] + 1e-9 >= l["line_total"]]
        if not fits:
            for g in l["grants"]:
                exhausted[g] = (caps[g] - room[g] + l["line_total"], caps[g])
            l |= {"tier": "ask", "grants": []}
            continue
        g = max(fits, key=lambda x: (room[x], x))
        room[g] -= l["line_total"]
        l["grants"] = [g]
    tiers = [l["tier"] for l in out_lines] or ["block"]
    tier = "block" if "block" in tiers else "ask" if "ask" in tiers else "auto"
    labels = {g["id"]: g["label"] for g in m["autonomy"]["grants"]}
    if tier == "auto":
        used = sorted({g for l in out_lines for g in l["grants"]})
        detail = "Agent may order this alone: " + "; ".join(f"{g} {labels[g]}" for g in used)
    elif tier == "ask":
        used, parts = [], []
        prefs = {f"preference:{p['id']}": f"preference '{p['label']}'" for p in m["preferences"]}
        ctx_of = {i["item_id"]: i["context"] for i in inputs}
        for l in out_lines:
            if l["tier"] != "ask":
                continue
            c = ctx_of.get(l["item_id"], {})
            devs = [prefs.get(x) or {"quantity": f"quantity +{c.get('quantity_over_pct', 0):g} %",
                                     "delivery_date": f"{c.get('days_late', 0)} day(s) late"}.get(x, x.replace("_", " "))
                    for x in l["deviations"]]
            parts.append(f"{l['product_id']}: " + (f"no grant accepts {', '.join(devs)}" if devs
                                                    else "no grant covers this product or amount"))
        for gid, (total, cap) in exhausted.items():
            parts.append(f"{gid} {labels[gid]}: {total:,.2f} over its limit {cap:,.2f}")
        if not m["autonomy"]["grants"]:
            parts = ["the mandate grants no autonomy, every order needs a human signature"]
        detail = "A human must sign: " + "; ".join(parts)
    else:
        used, detail = [], "Blocked"
    return {"tier": tier, "grants_used": used, "lines": out_lines, "detail": detail,
            "exhausted": {k: {"total": v[0], "limit": v[1]} for k, v in exhausted.items()},
            "mandate_id": m["mandate_id"], "mandate_version": m["version"], "mandate_hash": m["mandate_hash"],
            "inputs": inputs, "musts": list(all_musts.values()), "principal": principal}


def recheck(decision: dict) -> dict:
    """Rerun a stored decision against the stored mandate version (for /verify)."""
    m = store().mandate_version(decision["principal"]["id"], decision["mandate_version"])
    if not m:
        raise LookupError("mandate version not found")
    musts = {"_": decision.get("musts") or []}
    return evaluate(m, decision["inputs"], musts, decision["principal"])


def tier(checks: list[dict]) -> str:
    """Severity-only tier, without autonomy (kept for tests and tools that only have checks)."""
    sev = {c["severity"] for c in checks if not c["ok"]}
    return "block" if "block" in sev else "ask" if "ask" in sev else "auto"

