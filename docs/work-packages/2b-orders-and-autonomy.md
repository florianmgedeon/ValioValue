# 2b · Order list and autonomy level

Status: done 14 Nov 2026 (branch claude/work-packages-welle-1-4-43c31f) · Wave 1 (it does not depend on the other packages) · Shared contract: [`README.md`](README.md). **Note:** built with the old levels (1 = usual products). The level table below is the new target; [`2d`](2d-permissions-and-levels.md) reworks the code.

## Why

Autonomy is earned and shown: one level, not twenty switches (ux-concept "Autonomy: one ladder"). The header shows "Level 1 · Usual products · 6/10", the Kitchen tab shows the ladder, and the start screen lists recent orders with their outcome (ordered alone / you decided / blocked). Today there is no way to list orders, nothing counts clean orders, and "level" exists only as grants in the mandate.

Mockup moments: header autonomy pill, Kitchen tab → Autonomy (ladder with 4 levels, progress bar, grant g1 with "Change"), Activity tab (order list with badges), start screen "Recent orders", toast "Clean order. 7 of 10 towards level 2".

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

Level from the current mandate (no new mandate field). **Changed 14 Nov (UI review):** the level measures *which deviations* the agent may decide alone, not *which products*. Any product that passes every must may be ordered alone; a product-id scope on a grant is allowed but optional and is not a level.

| Level | Name | Agent may order alone | Mandate shape |
|---|---|---|---|
| 0 | Suggest | nothing, the buyer signs every order | no grants |
| 1 | Within your rules | anything that passes every must, with no deviation, up to the limit | grants with `limits` but no `accepts` |
| 2 | Small deviations | as 1, plus small deviations | at least one grant with `accepts` (quantity ≤ 5 %, delivery ≤ 1 day late, preference misses) |
| 3 | Standing orders | reorders the usual order on a schedule | not built; shown locked |

`GET /autonomy?buyer_id=`:

```json
{"level": 1, "name": "Within your rules", "clean_orders": 6, "needed": 10, "offer_next": false,
 "next": {"level": 2, "name": "Small deviations", "would_accept": ["quantity +5 % from pack rounding", "delivery 1 day late", "a missed preference"]},
 "grants": [{"id": "g1", "label": "Within your rules", "summary": "up to €600 an order · no deviations"}]}
```

* `needed` = 10 (config `AUTONOMY_CLEAN_ORDERS`). `offer_next` = clean ≥ needed and level < 2.

`POST /autonomy/level` `{buyer_id?, level, max_order_total?, by, mandate_hash?}`:

* Builds the new mandate: level 0 removes all grants. Level 1 = grant `g1` with `limits.max_order_total` (default: keep the current one) and no `accepts`. Level 2 = g1 plus `accepts: [{"check": "quantity", "max_over_pct": 5}, {"check": "delivery_date", "max_days_late": 1}, {"check": "preference"}]`. Other grants the buyer made by hand (with scopes) are kept as they are.
* Same signature round trip as 1b's accept: without `mandate_hash` → `409 {needs_signature, preview}`; with it → `PUT /mandate`.
* No `usual_scope` check and no `/autonomy/sync-usual` (dropped with the level change).

### 4. Event on clean order

After `order_confirmed` in the chat stream, the backend emits `autonomy_progress {clean_orders, needed, offer_next, order_id, clean: true|false, reason}` so the UI can show the toast. Hook: `agent._side_events` / `agent.on_decision`. Touch `agent.py` minimally (one call to `autonomy.after_order(session, order)`), because 2a also works in `agent.py`.

### 5. Contract

`contracts/backend-api.yaml`: `/orders`, `/autonomy`, `/autonomy/level`, event `autonomy_progress`. Fixtures for each.

## Not in this package

* Standing orders (level 3).
* Proposing the next level as a chat message: 2a may use `offer_next` to say it once.

## Done when

* After a demo run, `GET /orders` lists the orders with correct labels.
* `GET /autonomy` returns level 0 without grants, level 1 for grants without `accepts`, level 2 when a grant accepts deviations.
* Ten clean orders in a test → `offer_next: true`. A revised intent or a blocked line does not count.
* `POST /autonomy/level` round trip creates a new mandate version; Cedar shows the new grant (`GET /mandate/cedar`).
* Tests: mcp-server list route; backend clean-order rules and level detection with a faked Valio API.
