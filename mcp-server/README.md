# mcp-server

Owner: Florian

Valio Aimo's agent interface. MCP over Streamable HTTP on `localhost:8001/mcp`. This is the trust boundary: every rule that matters is decided here, from data, never by the agent's LLM.

```bash
pip install -r mcp-server/requirements-dev.txt
cd mcp-server && python -m valio_mcp.server
curl localhost:8001/health
pytest                                   # 41 tests incl. property-based (Hypothesis)
```

## The guarantee

When the buyer hits Send, Valio Aimo executes only what the buyer confirmed. Not against free text, but against an **OrderIntent the buyer saw and signed** ("what you see is what you sign"). An intent has one or more items (a product, a list, a recipe). **Every item is bought exactly once and only as specified**, together in one cart or in waves. `tests/test_guarantee.py` checks it on random intent/quote pairs per run: an autonomous order never deviates from its intent, and a cart line can only fill a distinct, open item.

```
propose_intent ─> human confirms + signs intent_hash ─> search / check_offer (intent_id)
     ─> place_order(quote_id, intent_id) ─> diff quote vs intent ─> auto | ask | block
```

## MCP tools

| Tool | Annotations | What it does |
|---|---|---|
| `propose_intent(original_request, items[], deliver_by, hard_rules, ...)` | write, not destructive | Fixed OrderIntent with one item per product or ingredient. Adds the mandate's mandatory rules, cross-checks numbers and "non-dairy" against the buyer's own text, returns read-back, chips, open questions and an RFC 8785 + SHA-256 hash. The agent must stop here |
| `search_products(query, intent_id, item_id, ...)` | read-only | Catalog search. Products whose kind changes (oat, soy, plant-based ...) only match when asked for. With `intent_id` every hard rule is evaluated from data, unsafe products come back in `excluded` with the reason |
| `check_offer(product_ids, intent_id, item_id)` | read-only | Live price and availability. Quantity, unit and date come from the item. Each offer: `quote_id`, expiry, rule `evidence`, `provenance` signed by Valio Aimo (Ed25519) |
| `place_order(intent_id, lines[{item_id, quote_id}], reason)` | destructive, idempotent | Per-line diff plus cart-level budget and mandate, tier, one cart hash, receipt. Each item once, open items stay orderable, replays return the same order |
| `get_order_status(order_id)` | read-only | Order and approval |

Annotations are hints for hosts. Enforcement is server side.

## Hard rules (`constraints.py`)

Per product and rule: `safe`, `excluded` or `unknown`, with the signals. Unknown is never safe. OR logic: one "contains" signal excludes.

* GS1 allergen levels `CONTAINS` / `MAY_CONTAIN` / `FREE_FROM` (`MAY_CONTAIN` excludes under allergy strictness)
* Nutritional claims (`FREE_FROM_LACTOSE`, `LOW_LACTOSE` is not lactose-free). A "milk-free" claim loses to an ingredient hit
* Milk-derivative scan of name and ingredients in en/fi/sv, with plant look-alikes (oat/coconut milk, cocoa/peanut butter) and negations (`maidoton`, `milk-free`)

## Diff and tiers (`diff.py`, `policy.py`)

| Check | Mismatch |
|---|---|
| binding (buyer, exact quantity the quote was asked for), quote signature, quote valid, cut-off | block |
| musts (mandate and request) not `safe` (unknown counts as broken unless the rule says `if_unknown: ask`) | block |
| product class (catalog search for the intent's product includes it) | ask |
| quantity in Decimal: not less, max +`max_over_pct` from pack rounding | ask |
| delivery date, availability, budget, missed preference | ask |

Then Cedar decides per line (`policy.decide`): a broken must or technical rule blocks; a line goes through alone only if a grant of the mandate covers its scope, its limits and every *ask* deviation. `auto`: confirmed, the receipt names the grants. `ask`: a human signs the exact cart hash. `block`: rejected, the intent stays usable. Details: [`docs/policy-v3-plan.md`](../docs/policy-v3-plan.md).

## Human-only endpoints (not MCP)

Bearer `APPROVER_TOKEN`. Called by the buyer backend for its human UI, never by the LLM.

```
GET  /internal/intents/{id}
POST /internal/intents/{id}/confirm   {"intent_hash", "signature", "confirmed_by"}     409 on hash mismatch or open questions
POST /internal/intents/{id}/revise    {"edits": {...}}  -> new proposed intent, new hash
GET  /internal/approvals?status=pending
POST /internal/approvals/{id}         {"decision", "decided_by", "cart_hash", "signature"}   409 if the cart changed
GET  /internal/orders/{id}  /internal/orders/{id}/audit
GET  /internal/mandate/{buyer_id}            current mandate (Must, Prefer, Autonomy) + summary
POST /internal/mandate/{buyer_id}/preview    validate, next version, hash to sign, Cedar
PUT  /internal/mandate/{buyer_id}            {"mandate", "mandate_hash", "signature", "updated_by"}   409 hash, 403 signature, 422 invalid
GET  /internal/mandate/{buyer_id}/history    /internal/mandate/{buyer_id}/cedar    /internal/policy-catalog
POST /internal/rule-coverage                 {"rule", "category"?} -> {checked, known, safe, excluded, unknown}  read-only, cached
GET  /internal/categories?q=fish             category codes for a product word (for "only Finnish fish")
```

Public: `GET /verify/{order_id}` recomputes every hash and checks every signature. `GET /keys` returns Valio Aimo's public key.

## Mandates (three levels, versioned)

`mandatory_rules` (Must: block), `preferences` (Prefer: ranking, a miss asks), `autonomy.grants` (when the agent may decide alone). All rules are conditions `{attr, op, value}` over product data, no fixed rule kinds. Versions live in SQLite; every change is signed by the buyer. Format and decision: [`docs/policy-v3-plan.md`](../docs/policy-v3-plan.md).

Seeds in `policies.json` (V2 format, migrated on first read, `spend_limit` becomes grant `g1`): `juvenes` (1,000 EUR autonomous), `hotel-chain-helsinki` (500), `school-kitchen-espoo` (300, always no peanuts and tree nuts incl. may contain), `default`.

## Data

* `data/raw/products.json` (gitignored, fetch with `scripts/fetch_data.sh`). Falls back to `data/products.sample.json` (6 invented DEV SAMPLE products).
* `data/delivery_reliability.csv`, `data/carbon_footprint.csv` from the challenge.
* Demo keys are generated in `data/keys/` (gitignored). Production: buyer keys as passkeys/wallet, Valio key in an HSM.
* Offline: `python mcp-server/dev/fake_valio_api.py` serves invented prices on `:8099` in the real API's shape.
