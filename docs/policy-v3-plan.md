# V3 plan: policies in three levels (Must, Prefer, Autonomy)

Status: built (backend base, 14 Nov 2026). Policy editor UI is the next step. Builds on [`v2-plan.md`](v2-plan.md). Product context: [`product-vision.md`](product-vision.md).

## Goal

A buyer's policy has three levels, each with one clear effect, and the UI shows exactly these three:

| Level | Question it answers | Effect on an order | Who can override |
|---|---|---|---|
| **Must** (`mandatory_rules`) | What may never be bought? | Product excluded, order line **blocked** | nobody (change the policy) |
| **Prefer** (`preferences`) | What is better? | **Ranking**. A miss is a deviation that **asks**, unless autonomy accepts it | autonomy grant or the human |
| **Autonomy** (`autonomy`) | When may the agent decide alone? | Turns *ask* into *auto* inside an explicit scope and limit | human only |

The policy is editable over REST (never via an MCP tool), versioned, hashed and signed by the buyer. Orders are decided against the current version; the receipt names it.

## Today (V2)

```json
"school-kitchen-espoo": {
  "display_name": "...", "currency": "EUR",
  "spend_limit": 300.0,
  "allowed_categories": null,
  "mandatory_rules": [{"rule": "exclude_allergen", "allergen": "peanuts", "strictness": "contains_or_may_contain"}]
}
```

* `mcp-server/policies.json`, read once and cached (`policy.buyer_policy`). Not editable.
* Must exists (`mandatory_rules`: lactose_free, exclude_allergen, origin).
* Prefer does not exist in the policy. The agent sets `optimise` and `origin_preference` per intent.
* Autonomy is hard-coded: `diff.py` gives every check a fixed severity, `policy.tier` takes the worst. Any deviation asks, `spend_limit` is the only knob.
* `allowed_categories` is loaded but **not enforced anywhere**.

## Format (V3, built)

One condition language `{attr, op, value}` for musts, preferences and grant scopes. No fixed rule kinds: any
attribute the product data has can be used (list with examples: `GET /policy-catalog`). Code: `rules.py`
(conditions), `constraints.py` (verdicts with evidence), `mandate.py` (format, Cedar), `policy.py` (versions,
decision).

```json
{
  "display_name": "Hotel Kaleva",
  "currency": "EUR",
  "optimise": "price",

  "mandatory_rules": [
    {"id": "m1", "rule": "lactose_free"},
    {"id": "m2", "attr": "allergens", "op": "not_contains", "value": "peanuts"},
    {"id": "m3", "attr": "delivery_reliability", "op": "gte", "value": 0.95, "if_unknown": "ask"},
    {"id": "m4", "attr": "comparison_price", "op": "lte", "value": 1.40,
     "applies_to": [{"attr": "category", "op": "eq", "value": "DEV-DAIRY"}]}
  ],

  "preferences": [
    {"id": "p1", "attr": "labels", "op": "contains", "value": "HYVAA_SUOMESTA", "weight": 3},
    {"id": "p2", "attr": "labels", "op": "contains", "value": "EU-Bio", "weight": 1}
  ],

  "autonomy": {
    "grants": [
      {"id": "g1", "limits": {"max_order_total": 2000},
       "accepts": [{"check": "quantity", "max_over_pct": 5}]},
      {"id": "g2", "scope": [{"attr": "country", "op": "eq", "value": "fi"},
                             {"attr": "category", "op": "eq", "value": "DEV-DAIRY"}],
       "limits": {"max_order_total": 500},
       "accepts": [{"check": "preference", "id": "p2"}, {"check": "delivery_date", "max_days_late": 1}]}
    ]
  }
}
```

Ids, labels, `source` and `enabled` are filled in by the server when missing. The V2 shorthands
(`{"rule": "lactose_free"}`, `exclude_allergen`, `origin`) are accepted and kept on the output, so older clients
still work. `policies.json` stays in V2 format and is migrated on first read: `spend_limit` becomes grant `g1`
(`allowed_categories` its scope, which is now actually enforced).

### Must

* Condition plus `id`, `label`, `enabled`, `source`, `applies_to` (only for products matching all of these) and `if_unknown`.
* `if_unknown`: `exclude` (default) or `ask` (product is kept, the line needs a human). Never `ignore`: a must you can ignore is a preference.
* Broken → **block**, always. Autonomy cannot touch it.
* Derived attributes keep the old, careful semantics: `allergens` (contains or may contain, incl. ingredient scan for milk), `allergens_contains`, `lactose_free`. Offer attributes (`unit_price`, `comparison_price`, `line_total`) are checked once there is a price.

### Prefer

* Condition plus `weight` 1 to 3. Offers are sorted by: can fulfil → preference score (sum of weights met) → `optimise`.
* If the chosen offer misses a preference, the diff adds a check `preference` (severity ask). Whether that really asks is decided by autonomy: the mockup's "Agent decides / Asks on deviation" is whether a grant accepts that preference.
* A request may add a preference (`origin_preference`) or change `optimise` for one intent; it cannot switch off a must.

### Autonomy

A list of **grants**: inside this **scope**, up to these **limits**, the agent may accept these **deviations** on its own. No grants = every order needs a human ("suggest only").

| Field | Meaning | Vocabulary |
|---|---|---|
| `scope` | Lines the grant covers, all conditions must hold. Empty = every line | any condition |
| `limits` | `max_order_total` (summed over the lines assigned to this grant), `max_line_total`, `max_unit_price` | |
| `accepts` | Deviations it may accept | `quantity` (`max_over_pct`), `delivery_date` (`max_days_late`), `preference` (`id`, empty = all), `product`, `budget`, `availability` |

Decision for an order:

1. Any broken must or technical rule (binding, quote signature, expiry, cut-off) → **block**.
2. Per line, Cedar checks `order_alone`: a grant permits the line if its scope matches, its line limits hold and it accepts every deviation of the line.
3. Each permitted line is assigned to one grant (lines with one option first, then the grant with most room left); `max_order_total` counts only the lines assigned to that grant (decision D4).
4. All lines assigned → **auto**, the receipt names the grants. Otherwise → **ask**, the approval says which deviation or limit no grant covered.

The decision runs in Cedar, see the next section.

## Cedar as the decision engine

[Cedar](https://www.cedarpolicy.com/) (open source, AWS Verified Permissions) decides Must and Autonomy. Prefer stays in Python because it is ranking, not authorisation.

| Level | In Cedar | Action |
|---|---|---|
| Must | `forbid` (with `action` unconstrained, so it also blocks `order_alone`) | any |
| Autonomy grant | `permit` | `order_alone` |
| Prefer | not in Cedar, ranking in `check_offer` | |
| (base) | one `permit` for the buyer on `order` | `order` |

Per order line (`policy.decide`):

1. Python collects the facts. Must verdicts come from `constraints.py` **with evidence** (GS1 signals, ingredient scan, claims) and reach Cedar as `context.verdicts.<rule id>`. Product attributes (country, category, labels, reliability, prices …) are the `Product` entity, for grant scopes. Line facts are `context`: `deviations`, `quantity_over_pct`, `days_late`, `line_total`, `unit_price`, `technical_blocks`.
2. `order` denied → **block**. Determining policies = the musts (or `technical`) that fired.
3. `order_alone` allowed → candidate grants. Then the order-total limits (see Autonomy, step 3).
4. Otherwise (Cedar's default deny) → **ask**. The reason comes from the diff: the deviations no grant covered.

**forbid always beats permit** in Cedar, so "no grant can turn a block into auto" is a property of the language. A Hypothesis test checks our compilation keeps it: a grant that accepts every deviation without limit still never lets a broken must through. As a second line, a block found by Python blocks even if Cedar disagreed (fail closed), and a Cedar evaluation error blocks too.

JSON stays the source of truth (UI, `PUT /mandate`, hashing). `mandate.to_cedar` generates the text per decision (mandate musts and grants plus the request's own musts). `GET /mandate/cedar` shows it. Generated example:

```cedar
// MUST no peanuts incl. 'may contain' (mandate)
@id("m2")
forbid (principal == Buyer::"hotel-kaleva", action, resource)
when { context.verdicts has m2 && context.verdicts.m2 != "safe" };

// AUTONOMY Orders origin FI and category is DEV-DAIRY alone up to 500 EUR ...
@id("g2")
permit (principal == Buyer::"hotel-kaleva", action == Action::"order_alone", resource)
when { (resource has country && resource.country == "fi") &&
       (resource has category && resource.category == "DEV-DAIRY") &&
       ["delivery_date", "preference:p2"].containsAll(context.deviations) &&
       (!context.deviations.contains("delivery_date") || context.days_late <= 1) };
```

Notes from the spike and the build: `diagnostics.reasons` returns positional ids (`policy0` …), mapped back to rule ids; decimals use the `__extn` form; attribute names come from a closed list and values are JSON-escaped, so no Cedar injection through a mandate.

### Version, hash, signature

* Every change creates `version + 1` and a new `mandate_hash` (RFC 8785 + SHA-256). Preview returns the hash, the backend signs exactly that hash with the buyer key, Valio Aimo verifies and stores the version (append-only SQLite table `mandates`). V1 is seeded from `policies.json`, unsigned.
* Orders are decided against the **current** version (D3). The receipt signs `mandate_id`, `mandate_hash` and `grants_used`.
* `/verify` recomputes the mandate hash from the stored version and, for auto orders, re-runs the Cedar decision from the stored inputs.

## Endpoints

| Where | Endpoint | What |
|---|---|---|
| mcp-server (Valio) | `GET /internal/mandate/{buyer_id}` | current version + plain `summary` |
| mcp-server | `POST /internal/mandate/{buyer_id}/preview` | validate, next version, hash to sign, generated Cedar |
| mcp-server | `PUT /internal/mandate/{buyer_id}` | `{mandate, mandate_hash, signature, updated_by}`; 409 hash mismatch, 403 bad signature, 422 invalid |
| mcp-server | `GET /internal/mandate/{buyer_id}/history`, `/cedar` | versions; Cedar text |
| mcp-server | `GET /internal/policy-catalog` | attributes (type, operators, example values from the data), accept checks, limits |
| backend | `GET /mandate`, `POST /mandate/preview`, `PUT /mandate`, `GET /mandate/history`, `GET /mandate/cedar`, `GET /policy-catalog` | pass-through; `PUT` previews, checks the shown `mandate_hash` (optional) and signs |

All need the approver token on the Valio side. No MCP tool can change the mandate. The agent gets it read-only as a summary in its system prompt.

## What was built

| Area | Files |
|---|---|
| Conditions, attribute list | `mcp-server/valio_mcp/rules.py` |
| Verdicts with evidence for any condition, facts for Cedar | `constraints.py` |
| Mandate format, V2 migration, Cedar compiler | `mandate.py` |
| Versions in SQLite, preview/save, Cedar decision, grant assignment, recheck | `policy.py`, `store.py` |
| Musts with ids, mandate + request preferences | `intent.py` |
| Preference checks, `over_pct`, spend limit moved into autonomy | `diff.py` |
| Ranking by preference score, decision in `place_order`, receipt, `/verify`, REST | `server.py` |
| Backend endpoints, mandate in the agent prompt | `backend/buyer_agent/app.py`, `agent.py` |
| `app.html` and `console.html` show Must / Prefer / Agent alone | `backend/buyer_agent/static/` |
| Contract and fixtures | `contracts/` |
| Tests: 12 new in `test_mandate.py`, guarantee properties now run through Cedar, new property "no grant turns a block into auto" | `mcp-server/tests/` |

Not built yet: the policy editor UI (next step), `max_premium_pct` on preferences, `usual_only` scopes (with the kitchen context).

## Decisions (agreed 14 Nov 2026)

| # | Topic | Decision |
|---|---|---|
| D1 | Who builds | Claude builds backend and mcp-server changes |
| D2 | Scope of this step | Backend base only. Policy editor UI is the next step. `app.html` and `console.html` must read the new format |
| D3 | Policy changes while an intent is confirmed but not yet ordered | No re-confirmation. `place_order` always checks against the **current** policy; the receipt names the version. Placed orders are never touched. (Replaces the `mandate_current` block above.) |
| D4 | Grant limits | A grant's `max_order_total` counts only the lines **that grant covers**. A grant with empty scope covers every line, so it limits the whole order |
| D5 | Musts | Always block |
| D6 | Rule kinds | **No fixed rule kinds.** One condition language `{attr, op, value}` over product attributes for musts, preferences and grant scopes. Attributes come from the product data (plus a few derived ones like `lactose_free`), listed by `GET /policy-catalog`. Missing attribute = unknown, handled by `if_unknown`. The format section above is updated accordingly when building |
| D7 | Roles | None for now. Any app user may change the policy, signed with the buyer key |
| D8 | Demo buyers | Seed data only, not relevant for the structure. Existing `policies.json` entries are migrated |
| D9 | Usual products | Later, with the kitchen context |

Decided during the build without further input (veto welcome): rules from the buyer's request ("lactose-free this time") use the same condition format and are added to the intent as musts; a request may override `optimise` for one intent but cannot switch off a policy must.
