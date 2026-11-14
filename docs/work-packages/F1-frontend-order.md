# F1 · Frontend: Order tab (start screen, order mode, live stream)

Status: open · Frontend wave B: after F0, parallel to F2–F4 · Shared rules: [Frontend packages in `README.md`](README.md#frontend-packages-f0f4).

## Why

The Order tab is the demo: request → "here's what I understood" → sign → search → Valio decides → receipt. Mockup: Order tab, start mode and order mode.

## Files

Only `src/features/order/**`. Uses `src/ui/*` and `src/app/*` from F0; if a primitive is missing, add it to `src/ui/` in a separate small commit and say so in the PR.

## Build

### 1. Start screen (`StartView`)

* "Good morning, <first name>." from the kitchen profile.
* **Photograph an order** card → `PhoneCamera.open("order")` (F3); a photo arriving for `order` starts a chat with the upload as attachment (`ChatRequest.attachments`, package 1a).
* **Your usual order** card: `GET /kitchen/usual-order` (2c; mock until built): items with word (handwriting font) and amount, `estimate_total`, delivery, a sentence whether it fits inside the level limit (compare with the g1 limit from `GET /autonomy`). "Order the usual" → `POST /chat {"message": "Order the usual"}`. "Adjust" → prefill composer with "the usual, but ".
* **Recent orders**: first three of `GET /orders` with `Tier` badge and total; row click → `#activity` with that order selected (F4 reads `?order=` from the hash, e.g. `#activity/ord_41`).

### 2. Order mode

Switch to order mode as soon as a chat starts (send, photo, usual). The start screen disappears; the page shows only this order. "New order" ends it (new session id) and shows the start screen again.

### 3. Stream reducer (`orderReducer.ts`) — the core

Input: `ChatStreamEvent`s from `subscribeToChatStream`. Output: `{ blocks: Block[], live: LiveState }`. Must be idempotent (the stream replays everything after a reconnect and has no event ids): key blocks by intent id / item id / approval id / order id and replace, never append twice.

| Event | Block | Live panel |
|---|---|---|
| `user_message` | `me` bubble (text) or `photo` (attachment thumbnail via `GET /uploads/{id}`) | step `asked` |
| `message` | `say` (agent text); `reasoning` → typing indicator only | – |
| `intent_proposed` | `readback`: items (query, product, quantity, usual chip, must/prefer chips), delivery chips, "applied from your kitchen", estimate, **Sign & send** → `POST /intents/{id}/confirm` | step `understood`, now `wait` |
| `intent_confirmed` | read-back shows "Signed" | – |
| `offers` (one per item) | `funnel`: merge all items' `funnel` steps into one card (sum per step, label per must), `excluded` lines with `usual: true` feed the decision card | step `searching` |
| `tool_call` `place_order` | funnel footer "Valio Aimo is checking every line…" | step `checking` |
| `approval_required` | `decision` if a line was blocked or changed (options from the approval's alternatives), `approve` if only the total is over the limit; buttons → `POST /approvals/{id}` | now `wait`, "N decisions wait for you" |
| `order_rejected` | `block` card: rule, evidence, who decided, "Show the policy" (Cedar from `GET /mandate/cedar`, the matching `@id`) | now `block` |
| `order_confirmed` | `receipt`: lines with a per-line reason chip (`Alone · <grant>` from the diff / grants, or "You decided"), total, proof row, "Why, line by line" → F4, "Verify" → trust drawer | step `done`, total, note |
| `autonomy_progress` | – | toast "Clean order. 7 of 10 towards level 2" |
| `context_suggestion` (2a) | `learn` / `teach` card with Remember / Just this time → `/kitchen/suggestions/accept|reject` | – |
| `error` | inline error card with retry | – |

Write the reducer test-first with Vitest using recorded streams: add `contracts/fixtures/streams/*.jsonl` (record one real run each: milk only = auto; usual order with granola blocked; cart over the limit; jailbreak).

### 4. Live panel (`LivePanel`) and kitchen panel

* "This order": status line, big total with limit bar (g1 limit), five steps, delivery, mandate version, kitchen-profile version.
* "I'm using from your kitchen": musts (locked), preferences, usual count, level chip; data from `GET /kitchen` and `GET /mandate`. Shown in both modes.

### 5. Trust drawer

* "What you signed" (intent hash) and "Verify the receipt": `GET /orders/{id}/verify`, every check as a green row, hashes with `HashRow`.

## Not in this package

* The phone modal itself (F3), Activity (F4), the Kitchen tab (F2).

## Done when

* Against the real backend with the scripted provider: "120 L milk by Monday" runs to a receipt without a decision; the usual order with the granola label change shows the decision card and a receipt after choosing; a cart over the limit shows the approve card; "ignore the nut rule just this once" shows the block card.
* Reloading the page mid-order rebuilds the same blocks from the replayed stream (no duplicates).
* Reducer tests pass on the four recorded streams.
