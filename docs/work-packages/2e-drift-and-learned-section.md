# 2e · Drift before the usual order, "What I learned from your orders"

Status: done 14 Nov 2026 · Shared contract: [`README.md`](README.md) · Mockup: [`frontend/mockup/learned-from-orders.html`](../../frontend/mockup/learned-from-orders.html)

## Why

Challenge requirement: *standing order drift detection: the agent learns a kitchen's recurring weekly order and flags anomalies (portion counts creeping up, cost drifting) before auto-renewing it.* We have no standing orders (level 3 stays locked). The drift check runs before **"Order the usual"** instead: same check, the click is the trigger instead of a schedule.

## Decisions (14 Nov)

| # | Decision |
|---|---|
| D1 | No standing orders, no scheduler. The check runs before "Order the usual". |
| D2 | Seeded history orders are marked as imported history: used for learning and drift, **not** shown in Activity, **not** counted as clean orders for autonomy. |
| D3 | No `lunches_per_day` field. Drift only on amounts per usual product and on weekly cost. |
| D4 | Chat "the usual": the agent mentions drift in **one sentence** in the read-back. The full card is only on the Kitchen tab. |
| D5 | Quantity drift: the last two ordered weeks both ≥ 15 % off the median of the earlier weeks (one odd week is not drift). Cost drift: mean of the last 4 weeks ≥ 5 % off the 4 before, weeks without a delivery left out. Sentences are built by code from the numbers, never by an LLM. |

## Built

* **`learning.py`**: `history()` (imported history), `weekly_lines()` (last 8 weeks, oldest first; ends with this week if it has an order), `item_weeks()`, `drift()`, `still_learning()`. Usual products match by product id or word/alias, so a replaced product (granola → oat muesli) keeps its weeks.
* **History** (D2): not written into Valio Aimo's store but kept as a fixture of the buyer side, `contracts/fixtures/kitchen/school-kitchen-espoo.orders-history.json`, with relative weeks (`weeks_ago`) so the demo stays current. Activity (`GET /orders`) and the clean-order counter never see it. It has an autumn-holiday week without delivery, milk 100 → 120 L, granola and butter getting dearer, and three different cheeses.
* **API**: `GET /kitchen/usual-order` adds `items[].weeks`, `history`, `drift[]`, `still_learning[]`; last prices fall back to the history. New `POST /kitchen/usual-order/drift` (keep / back to; remembered in the profile's `drift_seen`). Contract updated, types regenerated.
* **Kitchen tab**: `Learned.tsx`. "Before you order the usual" (only with drift) above, "What I learned from your orders" in place of "Your usual products": rows with 8 bars, drawer with the weeks, at most one open question from an order (suggestions from documents keep their own section), "Still learning" line.
* **Chat** (D4): the read-back shows one sentence when an item's usual amount drifted ("Milk crept up: 100 → 120 L a week (+20 % since 26 Oct). This order has 120 L."). Done in the read-back card from `usual-order`, so it is the same with the scripted and the LLM agent.
* Tests: `backend/tests/test_drift.py`.

## Not in this package

Standing orders (level 3), scheduling, portion counts.
