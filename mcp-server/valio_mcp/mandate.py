"""The buyer's mandate in three levels, versioned, and compiled to Cedar.

  mandatory_rules  Must     product excluded, order line blocked. Nobody can override.
  preferences      Prefer   ranking. A miss is a deviation that asks, unless a grant accepts it.
  autonomy.grants  Autonomy when the agent may decide alone: scope + limits + accepted deviations.

JSON is the source of truth (UI, REST, hashing). Cedar is how it is enforced: musts become `forbid`,
grants become `permit` on the action `order_alone`. In Cedar forbid always beats permit, so no grant can
ever turn a block into an autonomous order.
"""
from __future__ import annotations

import json
from typing import Any

from . import rules as R

ACCEPT_CHECKS = ("quantity", "delivery_date", "preference", "product", "budget", "availability")
LIMIT_KEYS = ("max_order_total", "max_line_total", "max_unit_price")
OPTIMISE = ("price", "reliability", "co2")


# --------------------------------------------------------------------------- normalise / validate

def _grant(g: dict, gid: str) -> dict:
    scope = [R.condition(c) for c in g.get("scope") or []]
    limits = {k: float(v) for k, v in (g.get("limits") or {}).items() if v is not None}
    bad = set(limits) - set(LIMIT_KEYS)
    if bad:
        raise R.RuleError(f"grant {gid}: unknown limit {', '.join(sorted(bad))}. Known: {', '.join(LIMIT_KEYS)}")
    accepts = []
    for a in g.get("accepts") or []:
        a = {k: v for k, v in dict(a).items() if v is not None}
        if a.get("check") not in ACCEPT_CHECKS:
            raise R.RuleError(f"grant {gid}: accepts.check must be one of {', '.join(ACCEPT_CHECKS)}")
        if "max_over_pct" in a:
            a["max_over_pct"] = float(a["max_over_pct"])
        if "max_days_late" in a:
            a["max_days_late"] = int(a["max_days_late"])
        accepts.append(a)
    return {"id": g.get("id") or gid, "label": g.get("label") or grant_label(scope, limits, accepts),
            "scope": scope, "limits": limits, "accepts": accepts, "enabled": bool(g.get("enabled", True)),
            "source": g.get("source")}


def grant_label(scope: list[dict], limits: dict, accepts: list[dict]) -> str:
    what = " and ".join(R.label(c) for c in scope) or "anything"
    lim = f" up to {limits['max_order_total']:,.0f} EUR" if "max_order_total" in limits else ""
    acc = (", may accept " + ", ".join(a["check"].replace("_", " ") for a in accepts)) if accepts else ""
    return f"Orders {what} alone{lim}{acc}"


def normalise(raw: dict) -> dict:
    """Validate a V3 mandate (V2 keys are upgraded). Raises RuleError with a readable message."""
    raw = dict(raw)
    grants_in = list(((raw.get("autonomy") or {}).get("grants")) or [])
    if "spend_limit" in raw and not raw.get("autonomy"):  # V2: one implicit grant
        cats = raw.get("allowed_categories")
        grants_in = [{"id": "g1", "scope": [{"attr": "category", "op": "in", "value": cats}] if cats else [],
                      "limits": {"max_order_total": raw["spend_limit"]}, "accepts": [], "source": "v2 spend_limit"}]
    musts = [R.rule(r, source="mandate", rid=f"m{n}") for n, r in enumerate(raw.get("mandatory_rules") or [], 1)]
    for m in musts:
        m["source"] = "mandate"
    prefs = []
    for n, p in enumerate(raw.get("preferences") or [], 1):
        pr = R.rule(p, source="mandate", rid=f"p{n}")
        w = int(p.get("weight", 1))
        if not 1 <= w <= 3:
            raise R.RuleError(f"preference {pr['id']}: weight must be 1, 2 or 3")
        pr["weight"] = w
        pr.pop("if_unknown", None)
        prefs.append(pr)
    grants = [_grant(g, f"g{n}") for n, g in enumerate(grants_in, 1)]
    ids = [x["id"] for x in musts + prefs + grants]
    if len(ids) != len(set(ids)):
        raise R.RuleError("ids of rules, preferences and grants must be unique")
    for i in ids:
        if not str(i).replace("_", "").isalnum():
            raise R.RuleError(f"id '{i}': letters, digits and _ only")
    opt = raw.get("optimise") or "price"
    if opt not in OPTIMISE:
        raise R.RuleError(f"optimise must be one of {', '.join(OPTIMISE)}")
    return {"display_name": raw.get("display_name") or "Buyer", "currency": raw.get("currency") or "EUR",
            "optimise": opt, "mandatory_rules": musts, "preferences": prefs,
            "autonomy": {"grants": grants}}


def content(m: dict) -> dict:
    """What the mandate hash covers."""
    keys = ("buyer_id", "version", "display_name", "currency", "optimise", "mandatory_rules", "preferences",
            "autonomy")
    return {k: m.get(k) for k in keys}


def summary(m: dict) -> str:
    """Plain text for the agent's system prompt and the read-back."""
    lines = [f"Mandate v{m['version']} of {m['display_name']}:"]
    on = lambda xs: [x for x in xs if x.get("enabled", True)]  # noqa: E731
    lines.append("MUST (Valio Aimo blocks anything else): " + ("; ".join(r["label"] for r in on(m["mandatory_rules"]))
                                                              or "none"))
    lines.append("PREFER (ranking): " + ("; ".join(f"{p['label']} (weight {p['weight']})"
                                                   for p in on(m["preferences"])) or "none")
                 + f"; then {m['optimise']}")
    gs = on(m["autonomy"]["grants"])
    lines.append("AUTONOMY: " + ("; ".join(g["label"] for g in gs) if gs
                                 else "none, every order needs a human signature"))
    return "\n".join(lines)


# --------------------------------------------------------------------------- Cedar

def _lit(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return f'decimal("{float(v):.4f}")'
    return json.dumps(str(v))


def _cond(c: dict, ref: str = "resource") -> str:
    a, op, v = c["attr"], c["op"], c["value"]
    typ = R.ATTRIBUTES[a][0]
    x = f"{ref}.{a}"
    has = f"{ref} has {a}"
    if op in ("in", "not_in"):
        expr = f"[{', '.join(_lit(i) for i in v)}].contains({x})"
        return f"({has} && {'!' if op == 'not_in' else ''}{expr})"
    if op in ("contains", "not_contains"):
        return f"({has} && {'!' if op == 'not_contains' else ''}{x}.contains({_lit(v)}))"
    if typ == "number":
        fn = {"lt": "lessThan", "lte": "lessThanOrEqual", "gt": "greaterThan", "gte": "greaterThanOrEqual"}.get(op)
        return f"({has} && {x}.{fn}({_lit(v)}))" if fn else f"({has} && {x} == {_lit(v)})"
    return f"({has} && {x} {'==' if op == 'eq' else '!='} {_lit(v)})"


def _accepts(g: dict, pref_ids: list[str]) -> list[str]:
    names, extra = set(), []
    for a in g["accepts"]:
        if a["check"] == "preference":
            ids = [a["id"]] if a.get("id") else pref_ids
            names |= {f"preference:{i}" for i in ids}
        else:
            names.add(a["check"])
        if a["check"] == "quantity" and "max_over_pct" in a:
            extra.append(f'(!context.deviations.contains("quantity") || '
                         f'context.quantity_over_pct.lessThanOrEqual({_lit(a["max_over_pct"])}))')
        if a["check"] == "delivery_date" and "max_days_late" in a:
            extra.append(f'(!context.deviations.contains("delivery_date") || '
                         f'context.days_late <= {int(a["max_days_late"])})')
    return [f"[{', '.join(json.dumps(n) for n in sorted(names))}].containsAll(context.deviations)"] + extra


def to_cedar(m: dict, musts: list[dict]) -> tuple[str, list[str]]:
    """Cedar policy text and the rule id behind each policy, in order (Cedar reports policy0, policy1, ...).

    musts: every must that applies to this decision (mandate and request rules), with ids. Their verdicts come
    from constraints.py with evidence and reach Cedar as context.verdicts.<id>."""
    principal = f'Buyer::{json.dumps(m["buyer_id"])}'
    out, ids = [], []

    def add(pid: str, text: str, note: str) -> None:
        out.append(f"// {note}\n@id({json.dumps(pid)})\n{text}")
        ids.append(pid)

    add("technical", f"forbid (principal == {principal}, action, resource)\n"
                     "when { context.technical_blocks != [] };",
        "Valio Aimo: binding, quote signature, quote expiry, cut-off")
    add("base", f'permit (principal == {principal}, action == Action::"order", resource);',
        "The buyer may order through the agent at all")
    for r in musts:
        rid = r["id"]
        cond = f'context.verdicts has {rid} && context.verdicts.{rid} == "excluded"'
        if r.get("if_unknown") != "ask":
            cond = f'context.verdicts has {rid} && context.verdicts.{rid} != "safe"'
        add(rid, f"forbid (principal == {principal}, action, resource)\nwhen {{ {cond} }};",
            f"MUST {r['label']} ({r.get('source', 'mandate')})")
        if r.get("if_unknown") == "ask":
            add(f"{rid}_unknown",
                f'forbid (principal == {principal}, action == Action::"order_alone", resource)\n'
                f'when {{ context.verdicts has {rid} && context.verdicts.{rid} == "unknown" }};',
                f"MUST {r['label']}: no data, a human decides")
    pref_ids = [p["id"] for p in m["preferences"] if p.get("enabled", True)]
    for g in m["autonomy"]["grants"]:
        if not g.get("enabled", True):
            continue
        conds = [_cond(c) for c in g["scope"]]
        if "max_line_total" in g["limits"]:
            conds.append(f'context.line_total.lessThanOrEqual({_lit(g["limits"]["max_line_total"])})')
        if "max_unit_price" in g["limits"]:
            conds.append(f'context.unit_price.lessThanOrEqual({_lit(g["limits"]["max_unit_price"])})')
        conds += _accepts(g, pref_ids)
        body = " &&\n       ".join(conds)
        note = g["label"] + (f" (order total ≤ {g['limits']['max_order_total']:g} is summed by Valio Aimo over "
                             f"the lines this grant covers)" if "max_order_total" in g["limits"] else "")
        add(g["id"], f'permit (principal == {principal}, action == Action::"order_alone", resource)\n'
                     f"when {{ {body} }};", f"AUTONOMY {note}")
    return "\n\n".join(out) + "\n", ids
