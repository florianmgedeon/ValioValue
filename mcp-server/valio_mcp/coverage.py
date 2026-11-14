"""How well the catalog can check a rule. Read-only, nothing is stored.

"Label known for 1,412 of 1,431 products": for one must (or preference) we run constraints.evaluate over
the catalog and count the verdicts. Products the rule does not apply to (its applies_to does not match) are
not counted. Unknown stays unknown: it is never added to safe.
"""
from __future__ import annotations

import hashlib
import json

from . import constraints
from . import rules as R
from .catalog import catalog

_CACHE: dict[str, dict] = {}


def _key(r: dict, category: str | None) -> str:
    raw = json.dumps({"rule": R.key(r), "if_unknown": r.get("if_unknown"), "category": category,
                      "catalog": catalog().source, "n": len(catalog().products)}, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def rule_coverage(rule: dict, category: str | None = None) -> dict:
    """Counts for one rule: checked, known, safe, excluded, unknown. Raises RuleError for a bad rule."""
    r = R.rule(rule, source=rule.get("source") or "draft", rid=rule.get("id") or "draft")
    r["enabled"] = True
    if R.ATTRIBUTES[r["attr"]][1] == "offer":
        return {"label": r["label"], "checked": 0, "known": 0, "safe": 0, "excluded": 0, "unknown": 0,
                "category": category, "note": "This rule needs a price. It is checked when an offer is made."}
    k = _key(r, category)
    if k in _CACHE:
        return _CACHE[k]
    counts = {"safe": 0, "excluded": 0, "unknown": 0}
    for p in catalog().products.values():
        if category and p.category != category:
            continue
        ev = constraints.evaluate(p, r)
        if ev is None:
            continue
        counts[ev["verdict"]] += 1
    checked = sum(counts.values())
    out = {"label": r["label"], "checked": checked, "known": counts["safe"] + counts["excluded"], **counts,
           "category": category, "if_unknown": r.get("if_unknown", "exclude"), "rule_hash": k}
    _CACHE[k] = out
    return out


# Group words that never appear in product names, as the product words they stand for.
GROUP_WORDS = {
    "dairy": "milk cheese yoghurt cream butter quark", "meat": "meat beef pork mince",
    "poultry": "chicken turkey", "seafood": "fish shrimp salmon", "vegetables": "potato carrot onion cabbage tomato",
    "veg": "potato carrot onion cabbage tomato", "fruit": "apple banana orange pear", "bread": "bread rolls",
}


def categories(query: str, limit: int = 8) -> list[dict]:
    """Category codes for a plain word ('milk', 'fish', 'dairy'), learned from product names. Used to turn
    'only Finnish fish' into applies_to: category in [...]. Each word adds its best categories."""
    cat = catalog()
    q = " ".join(GROUP_WORDS.get(w, w) for w in query.lower().split())
    found: dict[str, float] = {}
    for alts in cat._terms(q):
        aff = cat.affinity(tuple(alts))
        if not aff:
            continue
        top = max(aff.values())
        for c, share in aff.items():
            if share >= max(0.2, top * 0.75):
                found[c] = max(found.get(c, 0.0), share)
    keep = sorted(found.items(), key=lambda t: -t[1])[:limit]
    out = []
    for c, s in keep:
        names = [p.name for p in cat.products.values() if p.category == c][:3]
        out.append({"category": c, "share": round(s, 3), "products": cat._cat_size.get(c, 0), "examples": names})
    return out
