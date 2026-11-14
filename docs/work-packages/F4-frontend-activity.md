# F4 · Frontend: Activity tab (what the agent did and why)

Status: done 14 Nov 2026 (fallback active until 2c) · Frontend wave B: after F0, parallel to F1–F3 · Shared rules: [Frontend packages in `README.md`](README.md#frontend-packages-f0f4).

## Why

Trust needs a readable trail per order, not only hashes. Mockup: Activity tab.

## Files

Only `src/features/activity/**`.

## Build

### 1. List

* `GET /orders` (2b): title, total, date, order id, `Tier` badge with the backend's `label` ("Ordered alone", "You decided 1", "1 line blocked (nuts)"). Newest first; "Load more" with `before`.
* Selection in the hash: `#activity/<order_id>` (F1 links here from Recent orders and the receipt).

### 2. Story timeline

* `GET /orders/{id}/story` (2c; mock until built). Steps in order, each with an icon node:
  * **You asked**: text or photo thumbnail.
  * **I understood, you signed**: item count, signer, intent hash (opens the trust drawer from F1; import it from `src/features/order/TrustDrawer.tsx`).
  * **I searched and picked**: per line the product, the funnel as `27 → 27 → 19 → 1` and the plain `why` sentence.
  * **Valio Aimo decided, line by line**: per line an outcome chip: `Alone · <grant id> <grant label>` / `You decided` / `Blocked · <rule>`, plus evidence for blocks.
  * **Confirmed**: total, "Verify receipt" (`GET /orders/{id}/verify`).
  * **I learned**: only when the story has a `learned` step.
* Header: "N lines · a alone · b by you · c blocked · mandate vM · kitchen profile vK".

### 3. Fallback without 2c

If `/story` is not built and mocks are off: build a reduced timeline from `GET /orders/{id}/audit` (tool calls in order: `propose_intent`, `search_products`, `check_offer`, `place_order`) and the order summary. Show "Details come with the next backend version" where the `why` sentences would be. Remove the fallback when 2c lands.

## Done when

* After the F1 demo runs, every order appears with the right badge, and its timeline matches what happened (granola blocked, muesli chosen by you, the rest alone under g1).
* Deep link `#activity/<id>` opens the right order after a reload.

## Built (14 Nov)

* `src/features/activity/`: `ActivityView` (list + story, no composer on this tab), `StoryTimeline`, `model.ts` (one view model from `/story` or from the audit trail), `queries.ts` (`useOrderList` with "Load more" via `before=<last order_id>`, `useOrderStory`, `useVerify`), `activity.css` (mockup classes, prefixed `act-`).
* `App.tsx`: one route line, `#activity[/<order_id>]`. Without an id the newest order is shown.
* Fallback: `/story` answers 404 and mocks are off → reduced timeline from `/orders/{id}/audit` + the list summary, with "Details come with the next backend version." (`TODO(2c)` in `queries.ts` and `model.ts`).
* Trust drawer: F1's `src/features/order/TrustDrawer.tsx` did not exist yet, so `TrustDrawer.tsx` here shows the intent hash and the `GET /orders/{id}/verify` checks. `TODO(F1)`: replace it with F1's drawer after merge.
* Checked in the browser against the current backend (scripted provider, fake Valio API): an auto order and an approved order show the right badges and audit timelines, "Verify receipt" lists all checks, `#activity/<id>` survives a reload. With `VITE_MOCK=1` the full story renders (granola blocked under m1, oat muesli by you, milk alone under g1, learned step). 375 px: no horizontal scroll.
* Not checked: the full F1 demo run (F1 is not merged). Note for F0: at 1024 px the header overflows by about 50 px on every tab.
