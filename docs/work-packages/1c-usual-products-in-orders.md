# 1c · Usual products and kitchen context in orders

Status: open · Wave 1 · Runs in parallel with 1a, 1b, 2b. Shared contract: [`README.md`](README.md).

## Why

"Milk" means something different in every kitchen. When the buyer says "milk", the agent should pick *their* milk (Valio Eila 10 L Novobox), and the UI should show why: "◉ your usual · 14 invoices". If the usual product is not allowed any more (the granola's label changed to "may contain tree nuts"), Valio Aimo blocks it with evidence and the buyer makes one decision. That is the core of the demo order.

Today the agent has no memory of the kitchen. The system prompt only carries the mandate summary. Valio Aimo ranks offers by "can fulfil → preference score → optimise" and knows nothing about usual products.

Mockup moments: the read-back card (per item chips "Your usual", "Nut-free ✓", "Finnish ✓"; line "Applied from your kitchen"), "How I narrowed it down" (funnel with a row per rule), the granola decision, the receipt.

## What exists

* `agent.system_prompt(session, mandate)` in `backend/buyer_agent/agent.py`; `mandate_summary(buyer_id)` reads the mandate from Valio.
* `ItemIn` in `mcp-server/valio_mcp/intent.py` (`product_query`, `quantity`, `quantity_unit`, `hard_rules`, `note`). `intents.build` normalises items into `{"item_id", "product": {"query"}, …}`. `item_binding` puts `product`, `quantity`, `hard_rules` into the signed intent hash.
* `search_products` (server.py ~L197): with an intent it evaluates every rule per product and returns `products`, `excluded` (first 12) and `excluded_count`.
* `check_offer` (server.py ~L250–395): evidence per offer, `preference_score`, sort `(not can_fulfil, -preference_score, optimise)`, ranks best/better/good.
* `diff.py` ~L68: check `product` = is the cart product in the catalog class of the item's query (severity ask).
* Kitchen profile: package 1b, `kitchen.prompt_block(buyer_id)` and `DATA_DIR/kitchen/{buyer_id}.json` (shared contract). If 1b is not merged yet, read the JSON yourself and seed from `contracts/fixtures/kitchen/school-kitchen-espoo.json` (create it from the contract example if missing).

## Build

### 1. Context in the agent (backend, `agent.py`)

* `system_prompt` gets a kitchen block from `kitchen.prompt_block(session.buyer_id)`: About text, then the usual products as `word → product name (product_id), usual amount`.
* New rule in "How to work", short:
  - When an item matches a usual word or alias (also plurals, Finnish, obvious synonyms), set `preferred_product_id` on that item and use the usual amount only if the buyer gave none ("the usual" = the usual amounts of all usual products).
  - Never set it for a word that is not in the list. Never change a usual product because it seems better; Valio Aimo ranks.
  - In step B, still search; `check_offer` puts the usual product first if it is allowed.
* "The usual, but double the oat drink" must work: all usual items with usual amounts, oat drink doubled.

### 2. `preferred_product_id` on the intent (mcp-server, `intent.py`, `server.py`)

* `ItemIn.preferred_product_id: str | None` ("The buyer's usual product for this item, from their kitchen profile. Only if the buyer's word matches a usual product").
* Store it as `item["product"]["preferred_product_id"]`, so it is part of the signed binding (the buyer signs "your usual Valio Eila").
* Validate: unknown product id → drop it and add a note to the read-back, do not fail.
* Read-back: the item text says the product name ("milk 120 L → your usual: Valio Eila® kevytmaitojuoma 10 l novobox"), and the item's chip gets `"usual": {"product_id", "name"}`.

### 3. Ranking (mcp-server, `server.py`)

* `search_products` with an item that has a preferred product: always evaluate that product even if the text search would not return it. If it passes every rule, put it first in `products` with `"usual": true`. If it breaks a rule, it is in `excluded` with `"usual": true` and the reason (this is the granola case; the UI shows "Label changed: may contain nuts").
* `check_offer`: sort key becomes `(not can_fulfil, not is_usual, -preference_score, optimise)`. So: the usual product wins if it can deliver in full and breaks no rule; otherwise preferences and optimise decide as today. Mark `offer["usual"] = True`.

### 4. Diff (mcp-server, `diff.py`)

* New check `usual` for items with a preferred product: intended = the usual product, cart = the chosen product, ok if equal, severity `ask`, detail "not your usual product". Autonomy can accept it (`accepts: [{"check": "product"}]` or a new accept check `usual`; pick one, document it in `policy-v3-plan.md`, add it to `GET /internal/policy-catalog`'s accept checks).
* Items without a preferred product are unchanged.

### 5. Funnel per rule (mcp-server, `search_products`)

The UI shows "Looked at 1,284 → Must: no nuts 906 → Must: meat from Finland 871 → in stock 640 → usual first …". Add to the result:

```json
"funnel": [
  {"step": "catalog", "label": "Matches for 'granola'", "count": 46},
  {"step": "must", "rule_id": "m1", "label": "No peanuts or tree nuts incl. may contain", "count": 9},
  {"step": "must", "rule_id": "m3", "label": "Delivery reliability at least 95 %", "count": 8}
]
```

Count the pool after each rule in mandate order (a product is dropped at the first rule it breaks). Availability and preference steps come from `check_offer`: add `"funnel_tail": [{"step": "available", "count": …}, {"step": "prefer", "rule_id": "p1", "count": …}, {"step": "pick", "count": 1}]` to its result. The backend passes both through in the `offers` event (`funnel` field). Keep `total_matches`, `excluded`, `excluded_count` as they are.

### 6. Events and contract

* `intent_proposed.intent.items[].product.preferred_product_id` and the chip's `usual` object.
* `offers` event: `funnel`, and `usual` flags on products and offers.
* Update `contracts/backend-api.yaml` and the fixtures in `contracts/fixtures/events/`.

## Not in this package

* Writing the profile (1b) or learning a new usual product from a correction (2a).
* An autonomy grant scoped to usual products (2b).
* Price estimate in the read-back before signing (package 3, optional).

## Done when

* "120 L milk by Monday" for `school-kitchen-espoo`: the intent's item has `preferred_product_id` 463038, the read-back names it, `check_offer` ranks it best, the order goes through alone under grant g1 (if the seeded mandate has it).
* The granola case: make the usual granola break the nut must in the test data (fake product with `MAY_CONTAIN` tree nuts) → it appears in `excluded` with `usual: true`, another product is ranked best, the `usual` diff check asks.
* "the usual, but double the oat drink" proposes all usual items with usual amounts and oat drink doubled (manual test with a real model; scripted provider may skip this).
* Funnel counts add up (each step ≤ the previous).
* Tests in `mcp-server/tests/`: preferred product stored in the binding (hash changes if it changes), ranking with and without a broken must, `usual` diff check, funnel counts. The property test "no grant turns a block into auto" still passes.
