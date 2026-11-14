# 2a · Learning in the chat and from orders

Status: open · Wave 2: start after 1a, 1b and 1c are merged · Shared contract: [`README.md`](README.md).

## Why

The kitchen context should grow with every order, and the buyer should be able to teach the agent in the same field they order in. Three moments in the mockup:

1. **After a decision:** "You chose nut-free oat muesli instead of your usual granola. Use it whenever you say granola?" [Remember] [Just this time].
2. **Teaching in the composer:** "We're closed on Friday 20 Nov for a teachers' day" is not an order. The agent says so and proposes an About entry.
3. **A context photo in the chat:** a menu photo sent in the order composer without an order request becomes suggestions instead of an order.

Rule W7 stays: the agent only ever creates suggestions. Accepting goes through 1b's REST endpoints.

## What exists (after wave 1)

* 1a: images in the chat, `run_turn(session, text, images)`, uploads.
* 1b: `kitchen.add_suggestions`, `POST /kitchen/suggestions/accept`, `ingest.py` (the extraction for one document), `GET /kitchen`.
* 1c: items carry `preferred_product_id`; the `usual` diff check; `offers` events with `usual` flags.
* `agent._loop` gets its tools from the MCP server only (`mcp.list_tools()`), executes calls via `mcp.call_tool`, and `_side_events` turns tool results into SSE events. The `scripted` provider plans with regexes.
* `agent.on_decision(order, approval)` runs after a human decision; `order_confirmed` is emitted in `_side_events` or after approval.

## Build

### 1. Buyer-side tools in the agent loop (`agent.py`, new `local_tools.py`)

* A small registry of tools that run in the backend, not at Valio Aimo. They are merged into the tool list given to the LLM and dispatched locally in `_loop` (same `tool_call` / `tool_result` events, `"side": "buyer"` in the event so the console can tell them apart).
* They can only create suggestions. They never touch the mandate, intents or orders.
* Tools:
  - `remember_about_kitchen(kind: "about" | "usual" | "prefer" | "must", title, payload, quote)`: the buyer said something about the kitchen. Creates one suggestion with source type `text` and the buyer's sentence as `quote`. Must and prefer payloads are validated like in 1b (via mandate preview; invalid → tool returns an error the model can correct).
  - `learn_from_document(upload_id)`: runs 1b's extraction on an uploaded photo or PDF and saves the suggestions.
* Each call emits `context_suggestion {"suggestions": [...], "reason": "from_chat" | "from_photo"}`.

### 2. System prompt (`agent.py`)

Replace 1a's "point to the Kitchen tab" rule:

* A message that states a fact, habit or rule about the kitchen and asks for nothing to buy → `remember_about_kitchen`, then one short sentence. Do not call `propose_intent`.
* A message with both ("we're vegan from now on, order oat drink for Monday") → `remember_about_kitchen` and then rule A.
* A photo without an order request → `learn_from_document`, then one sentence naming what was found.
* A must the buyer states in chat ("never order anything with sesame") becomes a **suggestion** that needs a signature, never a hard rule of the current intent unless the buyer asked for it for this order.

### 3. Learning from an order (`learning.py`, called from `agent.py`)

After `order_confirmed` (both the auto path and after an approval), compare per item:

* the intent's `preferred_product_id` vs the product actually ordered, and
* items without a preferred product vs what was ordered.

Create at most **one** suggestion per order (the most useful one), kind `usual`, source type `correction` with the order id:

* The usual product was replaced by the buyer's decision → "Use <new product> whenever you say <word>?" (replace the usual entry on accept).
* An item without a usual product that the buyer ordered for the second time with the same product → "Remember <product> as your usual <word>?".

Emit `context_suggestion {"reason": "after_order", "order_id", "suggestions": [one]}`. "Just this time" in the UI = `POST /kitchen/suggestions/{id}/reject`. "Remember" = accept (no signature needed for `usual`).

Optional, only if time: three approvals in a row that accepted the same kind of deviation (same check, same item word) → suggest an autonomy grant `accepts` entry (kind `autonomy`, needs signature).

### 4. Scripted provider

* Messages starting with "We're", "We are", "Our", "From now on" → `remember_about_kitchen` with kind `about` and the sentence as title.
* A photo whose upload meta has `fixture` set to a known context document → `learn_from_document`.

### 5. Contract

`contracts/backend-api.yaml`: event `context_suggestion`, the `side` field on tool events. Fixtures: `contracts/fixtures/events/context_suggestion-after_order.json`, `…-from_chat.json`.

## Not in this package

* The store, ingest and accept endpoints (1b).
* Autonomy levels and the clean-order counter (2b). If `autonomy_progress.offer_next` is true, the agent may mention it once after an order; nothing more.

## Done when

* Demo order with the granola decision (choose oat muesli) → `context_suggestion` with "Use Nut-free oat muesli whenever you say granola?" → accept → next "granola 15 kg" proposes oat muesli as the usual product.
* "We're closed on Friday 20 Nov for a teachers' day." → no intent, one `about` suggestion.
* A menu photo in the order composer without a request → suggestions, no intent.
* "Ignore the nut rule this once" still ends in Valio Aimo's block (no local tool can weaken anything).
* Tests in `backend/tests/`: local tool dispatch, one suggestion per order max, comparison logic with fixture orders, scripted routing.
