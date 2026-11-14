# 2b · Order list and autonomy level

Status: open · Wave 1 (it does not depend on the other packages) · Shared contract: [`README.md`](README.md).

## Why

Autonomy is earned and shown: one level, not twenty switches (ux-concept "Autonomy: one ladder"). The header shows "Level 1 · Usual products · 6/10", the Kitchen tab shows the ladder, and the start screen lists recent orders with their outcome (ordered alone / you decided / blocked). Today there is no way to list orders, nothing counts clean orders, and "level" exists only as grants in the mandate.

Mockup moments: header autonomy pill, Kitchen tab → Autonomy (ladder with 4 levels, progress bar, grants g1/g2 with "Change"), start screen "Recent orders", toast "Clean order. 7 of 10 towards level 2".

## What exists

* Orders in the mcp-server SQLite table `orders` (`order_id, session, buyer_id, quote_id, status, data, created_at, updated_at`), `store.get_order`. REST: `GET /internal/orders/{id}` and `/audit` (approver token). No list.
* Order `data` has the tier, lines, totals, `mandate` (`mandate_id`, `version`, `grants_used`), approval link, receipt.
* Approvals: `store.list_approvals`, `GET /internal/approvals`.
* Mandate grants: `autonomy.grants[]` with `scope`, `limits`, `accepts` ([`policy-v3-plan.md`](../policy-v3-plan.md)). Grant scope `product_id in [...]` works (W5).
* Intents can be revised (`POST /internal/intents/{id}/revise`, `revises` / `superseded_by` fields).

## Build

### 1. Order list (mcp-server)

* `store.list_orders(buyer_id, limit=20, before=None)`: newest first.
* `GET /internal/orders?buyer_id=&limit=&before=` (approver token): per order a summary:
  ```json
  {"order_id": "ord_41", "created_at": "...", "status": "confirmed | pending_approval | rejected",
   "tier": "auto | ask | block", "total": 512.0, "currency": "EUR", "lines": 5,
   "title": "milk, butter, rye rolls, apples, oat muesli",
   "decided_by": "Anna Virtanen | null", "blocked_rules": ["m1"],
   "mandate_version": 3, "grants_used": ["g1", "g2"], "intent_id": "int_…", "intent_revised": false}
  ```
  `title` = the item queries joined, max 60 characters.
* Test in `mcp-server/tests/`.

### 2. Backend passthrough (`backend/buyer_agent/routes_orders.py`)

* `GET /orders?buyer_id=&limit=` → the list, plus `label` for the UI: "Ordered alone", "You decided 1", "1 line blocked (nuts)".

### 3. Clean orders and level (`backend/buyer_agent/autonomy.py`)

Definition (write it into `docs/ux-concept.md` too): an order is **clean** if it is `confirmed`, its intent was not revised after it was proposed, and no line needed a human decision other than approving the cart unchanged. A blocked line, a changed product or a revised intent makes it not clean. Counting runs over the most recent orders since the last level change; the counter resets when the level changes.

Level from the current mandate (no new mandate field):

| Level | Name | Mandate shape |
|---|---|---|
| 0 | Suggest | no grants |
| 1 | Usual products | every grant has a scope with `product_id in [...]` (usual products) |
| 2 | Within mandate | at least one grant without a `product_id` scope |
| 3 | Standing orders | not built; shown locked |

`GET /autonomy?buyer_id=`:

```json
{"level": 1, "name": "Usual products", "clean_orders": 6, "needed": 10, "offer_next": false,
 "grants": [{"id": "g1", "label": "Usual products", "summary": "up to €600 an order · accepts +5 % from pack rounding"}],
 "usual_scope": {"in_sync": false, "missing": ["463038"], "extra": []}}
```

* `needed` = 10 (config `AUTONOMY_CLEAN_ORDERS`). `offer_next` = clean ≥ needed and level < 2.
* `usual_scope`: compares the `product_id in [...]` scope of the level-1 grant with the active usual products in the kitchen profile (read `DATA_DIR/kitchen/{buyer_id}.json` directly or via 1b's `kitchen.load`; if no profile exists, `in_sync: true`).

`POST /autonomy/level` `{buyer_id?, level, max_order_total?, by, mandate_hash?}`:

* Builds the new mandate: level 0 removes all grants; level 1 = one grant `g1` with scope `product_id in <usual ids>`, `limits.max_order_total` (default: keep the current one), `accepts: [{"check": "quantity", "max_over_pct": 5}]`, and keeps other grants that are already product-scoped (e.g. g2 "Finnish fruit & vegetables" is category-scoped: decide, document); level 2 = g1 without the product scope.
* Same signature round trip as 1b's accept: without `mandate_hash` → `409 {needs_signature, preview}`; with it → `PUT /mandate`.
* `POST /autonomy/sync-usual` does the same for level 1 with the current usual list (button "Update your usual products in the grant").

### 4. Event on clean order

After `order_confirmed` in the chat stream, the backend emits `autonomy_progress {clean_orders, needed, offer_next, order_id, clean: true|false, reason}` so the UI can show the toast. Hook: `agent._side_events` / `agent.on_decision`. Touch `agent.py` minimally (one call to `autonomy.after_order(session, order)`), because 2a also works in `agent.py`.

### 5. Contract

`contracts/backend-api.yaml`: `/orders`, `/autonomy`, `/autonomy/level`, `/autonomy/sync-usual`, event `autonomy_progress`. Fixtures for each.

## Not in this package

* Standing orders (level 3).
* Proposing the next level as a chat message: 2a may use `offer_next` to say it once.

## Done when

* After a demo run, `GET /orders` lists the orders with correct labels.
* `GET /autonomy` returns level 1 for a mandate with only a product-scoped g1, level 0 without grants, level 2 with an unscoped grant.
* Ten clean orders in a test → `offer_next: true`. A revised intent or a blocked line does not count.
* `POST /autonomy/level` round trip creates a new mandate version; Cedar shows the new grant (`GET /mandate/cedar`).
* Tests: mcp-server list route; backend clean-order rules and level detection with a faked Valio API.
