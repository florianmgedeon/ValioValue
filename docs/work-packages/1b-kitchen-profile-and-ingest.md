# 1b · Kitchen profile and reading documents

Status: done 14 Nov 2026 (branch claude/work-packages-welle-1-4-43c31f) · Wave 1 · Runs in parallel with 1a, 1c, 2b. Shared contract: [`README.md`](README.md).

## Why

"Photograph your kitchen, and the agent learns who you are" is the pitch. A photo of a lunch menu, a note on the fridge or an old invoice should become a list of things the agent proposes to remember: About, Usual products, Must, Prefer, Autonomy. Each one quotes the line it came from. Nothing becomes active until the buyer accepts it (W7). Photos taken here **never order anything** (W2).

Today there is no store for About and Usual products, and no way to read documents. The mandate (Must, Prefer, Autonomy) already exists and is signed and versioned on the Valio side.

Mockup moments: onboarding ("Let me get to know your kitchen": three documents, "Read my kitchen", cards with quotes, accept/reject, "Sign & start ordering"), the Kitchen tab (About, Usual products, Sources, "New from your documents", "Teach me with a photo").

## What exists

* Mandate REST in the backend (`app.py`): `GET /mandate`, `POST /mandate/preview`, `PUT /mandate` (backend signs the shown hash with the buyer key, `signing.py`), `GET /mandate/history`, `GET /policy-catalog` (attributes, operators, example values, accept checks, limits). Format: [`docs/policy-v3-plan.md`](../policy-v3-plan.md). Rules are `{attr, op, value}`; ids, labels and `source` are filled in by the server.
* MCP tool `search_products(query)` without `intent_id` works as a plain catalog search (`mcp-server/valio_mcp/server.py`).
* LLM providers in `llm.py` (plain HTTP). 1a adds images to chat messages; this package needs a one-shot extraction call, see step 3.
* Uploads: 1a stores files in `DATA_DIR/uploads/` (shared contract). If 1a is not merged yet, accept multipart directly in `/kitchen/ingest` and store the file the same way.

## Build

### 1. Profile store (`backend/buyer_agent/kitchen.py`)

* JSON file per buyer, format in the shared contract. Atomic writes (temp file + rename), a module-level lock per buyer.
* Functions: `load`, `save(profile, by, summary)` (bumps `version`, appends `history`), `add_suggestions`, `accept`, `reject`, `prompt_block` (see contract).
* `prompt_block(buyer_id)`: About text plus the Usual list as lines `"milk" → Valio Eila® kevytmaitojuoma 10 l novobox (product 463038), usually 120 L a week`. Max ~1,500 characters. 1c puts it into the system prompt.
* Seed: `contracts/fixtures/kitchen/school-kitchen-espoo.json` with the mockup's profile (About, 6 usual products, sources: menu, allergy note, invoices). Copy it to `DATA_DIR/kitchen/` on first `load` if no file exists. Resolve real `product_id`s for the usual products with `search_products` once and write them into the fixture (the milk one is `463038`).

### 2. REST (`backend/buyer_agent/routes_kitchen.py`)

| Endpoint | Does |
|---|---|
| `GET /kitchen?buyer_id=` | Profile, plus `mandate_summary` (the current mandate's Must / Prefer / Autonomy with ids and versions, from `GET /internal/mandate/{buyer_id}`), so the Kitchen tab needs one call |
| `PATCH /kitchen` | `{about?: {text}, usual?: {add?: [...], update?: [...], remove?: [ids]}, by}` → new version. Usual entries without `product_id` are resolved (step 4) |
| `GET /kitchen/history` | Versions with summaries |
| `POST /kitchen/ingest` | See step 3 |
| `POST /kitchen/suggestions/accept` | See step 5 |
| `POST /kitchen/suggestions/{id}/reject` | Status `rejected`, stays in the list for the record |

### 3. Ingest (`backend/buyer_agent/ingest.py`)

`POST /kitchen/ingest` body: `{buyer_id?, upload_ids?: [..], text?: "We're a school canteen in Espoo, nut-free…", title?}` or multipart with `file`.

* One call per document (so the UI can show "Reading…" per card; the endpoint processes a list sequentially and returns per document). Optional: `?stream=1` returns SSE `ingest_progress` per document; only if cheap.
* Add a `source` (`photo`, `pdf` or `text`), then call the LLM **once** with the image/PDF and an extraction prompt, asking for JSON only:
  ```json
  {"document_type": "menu | allergy_note | invoice | order_list | sustainability_report | other",
   "title": "Lunch menu, week 47",
   "suggestions": [{"kind": "must", "title": "...", "quote": "verbatim text from the document", "payload": {...}}]}
  ```
* The prompt includes: the five kinds with examples (from the mockup cards), the allowed attributes and operators **from `GET /policy-catalog`** (so musts and preferences come out in the real condition language), the current profile and mandate summary (to skip what is already known), and the rules: quote verbatim, never invent, prefer fewer and sure over many and vague, allergies always as `must` with `if_unknown: "exclude"`, autonomy only as a proposal derived from order sizes on invoices (e.g. `limits.max_order_total` ≈ the average invoice rounded up to 100).
* Implement the call in `ingest.py` as a small function per provider (Anthropic Messages with an `image` or `document` block; OpenAI-compatible with `image_url`). If 1a's helpers for building image blocks are merged, reuse them; otherwise keep this local and leave a `TODO(dedupe with 1a)`.
* Validate every suggestion: unknown kind → drop; `must`/`prefer` payload → run through `POST /mandate/preview` with the rule added to a copy of the current mandate (422 → drop it and log); `usual` → resolve the product (step 4).
* `ScriptedProvider` / no key: return fixed suggestions for the three demo fixtures (match by upload meta `fixture` field or filename), so onboarding works offline. For unknown documents return an empty list with a note.
* Response: `{"source": {...}, "document_type": "...", "suggestions": [...]}` (suggestions already saved with status `proposed`).
* `document_type == "order_list"`: do not order. Return the suggestions (often usual products) plus `"hint": "This looks like an order list. Send it in the Order tab to order it."`.

### 4. Resolving usual products

* `product_query` from the document (an invoice line like "Valio Eila® kevytmaitojuoma 10 l novobox") → MCP `search_products(query, limit=5)` through an MCP client session like `agent.mcp_session`. Take the top hit if its name matches closely (simple token overlap ≥ 0.6), else keep `product_id: null` and put up to 3 `candidates` into the payload for the UI to choose.
* `word` is the buyer's word ("milk", "butter"); `aliases` may hold the Finnish word from the invoice.

### 5. Accepting suggestions

`POST /kitchen/suggestions/accept` `{buyer_id?, ids: [...], by, mandate_hash?}`:

* `about` and `usual` are applied directly to the profile (one new profile version for the batch).
* If the batch has `must`, `prefer` or `autonomy` items: build the new mandate (current mandate + these rules/grants, with `source` = the suggestion's source title) and call `POST /mandate/preview`.
  * Without `mandate_hash`: return `409 {"needs_signature": true, "preview": {mandate, mandate_hash, version, cedar, diff}}`. Apply nothing.
  * With `mandate_hash` equal to the preview's hash: `PUT /mandate` (the backend signs), then mark the suggestions accepted and apply `about`/`usual`. One request = one mandate version and one profile version.
* `diff` in the preview: a list of plain sentences, e.g. `Add must: No peanuts or tree nuts incl. "may contain" (from Allergy note)`.
* This is the onboarding "Sign & start ordering" button: accept all accepted cards in one call.

### 6. Contract

`contracts/backend-api.yaml`: all `/kitchen*` paths and schemas `KitchenProfile`, `Suggestion`, `Source`. Fixtures: `contracts/fixtures/kitchen/school-kitchen-espoo.json`, `contracts/fixtures/kitchen/ingest-menu.json` (response for the menu photo).

Demo document fixtures in `contracts/fixtures/uploads/`: `menu-w47.jpg`, `allergy-note.jpg`, `invoices-oct.pdf` matching the mockup texts (menu footer "420 lunches / day · Nut-free school · Finnish ingredients first · Summer break 1 Jun – 9 Aug"; note "NO NUTS – not even 'may contain'!! … 18 milk-free diets → oat drink"; invoice lines Valio Eila 10 l novobox ×48, Valio voi suolaton 500 g ×80, …, "avg. € 540 per delivery").

## Not in this package

* The agent using the profile when ordering: 1c.
* Suggestions coming from the chat or from orders: 2a (uses `kitchen.add_suggestions`).
* Keeping an "usual products only" grant scope in sync: 2b reports when it is outdated.
* Rule from a typed sentence in the Must editor ("No sesame"): package 3.

## Done when

* Onboarding works end to end over REST with the three fixtures: ingest each → 8 suggestions similar to the mockup (2 about, 3 usual, 1 must, 1 prefer, 1 autonomy) with verbatim quotes → accept all with the signature round trip → `GET /kitchen` shows version +1, `GET /mandate` shows the new must, preference and grant.
* The same works with `LLM_PROVIDER=scripted` via fixed fixture answers.
* A must suggestion with an unknown attribute is dropped, never saved.
* An order-list photo in ingest does not create an order.
* Tests in `backend/tests/`: store versioning and atomic write, accept flow (409 without hash, success with hash, nothing applied on mandate error), ingest validation with a faked LLM response.
