# 1a · Photos in the chat, phone as camera

Status: open · Wave 1 · Runs in parallel with 1b, 1c, 2b. Shared contract: [`README.md`](README.md).

## Why

The killer feature starts with a photo. A cook photographs a handwritten order list ("milk 120 L, butter 10 kg, …") and the agent orders it. In the demo the photo is taken with a phone and appears on the big screen right away. Today `/chat` only takes text and the LLM history has no images.

This package makes photos and PDFs reach the agent. It does **not** decide what to learn from a photo: photos of menus, notes and invoices go to `POST /kitchen/ingest` (package 1b, decision W2). In the chat, from this package on, the agent orders from list photos and politely points context photos to the Kitchen tab. Package 2a later lets it turn context photos in the chat into suggestions.

Mockup moments: "Photograph next week's list" starter, camera button in the composer, "Use your phone" in onboarding (QR code, phone viewfinder, photo appears on the desktop).

## What exists

* `backend/buyer_agent/app.py`: `POST /chat` (`ChatRequest{message, session_id, buyer_id}`), SSE `GET /chat/{id}/stream`.
* `backend/buyer_agent/agent.py`: `run_turn(session, user_text)` appends `{"role": "user", "text": ...}` to `session.history`, `_loop` calls `provider.complete(system, history, tools)`.
* `backend/buyer_agent/llm.py`: `AnthropicProvider._messages` builds `[{"type": "text"}]` blocks for user turns; `OpenAICompatibleProvider._messages` sends user `content` as a plain string; `ScriptedProvider` parses the last user text with regexes (`parse_request`, `parse_items`).
* The system prompt (`agent.system_prompt`, rule A) already handles "a recipe, menu or shopping list is one item per ingredient".
* `config.BACKEND_HOST` defaults to `127.0.0.1`.

## Build

### 1. Uploads (`backend/buyer_agent/uploads.py`, `routes_uploads.py`)

* `POST /uploads` multipart: `file`, optional `buyer_id`, `pair_id`, `source` (`upload` | `phone`). Returns the upload meta from the shared contract.
* Accept `image/jpeg`, `image/png`, `image/webp`, `image/heic` (convert to JPEG), `application/pdf`. Reject others with 415, files over 15 MB with 413.
* Images: fix EXIF orientation, downscale to max 1600 px long edge, JPEG q85. Add `Pillow` (and `pillow-heif` for HEIC from iPhones) to `backend/requirements.txt`.
* Store as in the contract (`DATA_DIR/uploads/`). `uploads.load(upload_id) -> (meta, bytes)`.
* `GET /uploads/{upload_id}` returns the file (for thumbnails in the UI).

### 2. Phone pairing (`routes_uploads.py`, `static/phone.html`)

* `POST /pairings` `{buyer_id?, purpose: "order" | "kitchen"}` → `{pair_id, phone_url, expires_at}`. `phone_url = {PUBLIC_BASE_URL}/phone?pair={pair_id}`. Pairings live in memory, 30 min.
* `GET /pairings/{pair_id}/stream` SSE: sends `paired` when the phone page opens (`GET /phone?pair=…` marks it), and `photo_received {upload_id, url, media_type, pair_id}` for each upload with that `pair_id`.
* `GET /phone` serves `static/phone.html`: a single mobile page, no build step. Big shutter button using `<input type="file" accept="image/*" capture="environment">` (works over plain HTTP on the LAN; `getUserMedia` would need HTTPS). Shows the photo, "Send to kitchen screen", upload progress, a check mark, and "Take another".
* New config: `PUBLIC_BASE_URL` (e.g. `http://192.168.1.20:8000`). Document in `.env.example` that the demo needs `BACKEND_HOST=0.0.0.0` and the laptop's LAN IP in `PUBLIC_BASE_URL`.
* The QR code is drawn by the frontend from `phone_url`. Nothing to do here.

### 3. Images in the chat (`app.py`, `agent.py`, `llm.py`)

* `ChatRequest` gains `attachments: list[{"upload_id": str}] = []`. `message` may be empty when there is an attachment.
* `run_turn(session, user_text, images=None)`: history entry becomes `{"role": "user", "text": ..., "attachments": [upload_id, ...]}`. Emit a `user_message` event with the attachment ids (so a second screen can show the photo in the thread).
* `AnthropicProvider._messages`: for each attachment add an `image` block (`source.type = "base64"`) or, for PDFs, a `document` block, before the text block. Load bytes via `uploads.load`. Only attach images on the turn where they were sent; later turns keep a short text stand-in (`[photo up_… sent earlier]`) to save tokens.
* `OpenAICompatibleProvider._messages`: user `content` becomes a list with `{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,…"}}` parts. PDFs: not supported by every endpoint; send a text note instead and log a warning.
* `ScriptedProvider`: no vision. If the upload meta has a `text` field (see fixtures below), treat it as the user's text. Otherwise answer with a message asking to type the list. This keeps the scripted demo working.
* System prompt, new rule (keep it short):
  - A photo of a shopping or order list: read every line, one item per line, quantities as written, then rule A. Put "from your photo" in the note of each item. If a line is unreadable, add an open question quoting what you can read.
  - A photo of a menu, recipe or prep sheet **with** an order request ("order for this menu, 420 portions"): rule A as for recipes.
  - A photo of a menu, note, invoice or anything else **without** an order request: do not order. Say in one sentence what it is and that it can be added to the kitchen profile from the Kitchen tab. (2a replaces this with a tool.)
* `propose_intent.original_request` = the buyer's text plus `[photo: <one-line transcription>]`, so Valio Aimo's cross-check of numbers against the request still works (`intent.py` compares numbers in the request with the items).

### 4. Demo fixtures

* `contracts/fixtures/uploads/order-list.jpg`: a photo of a handwritten list (milk 120 L, butter 10 kg, rye rolls 300, apples 40 kg, granola 15 kg, "by Monday morning"). A real photo is best; a generated image is fine.
* `contracts/fixtures/uploads/order-list.json`: meta with `"text": "milk 120 L\nbutter 10 kg\nrye rolls 300\napples 40 kg\ngranola 15 kg\nby Monday morning"` for the scripted provider.
* `scripts/demo_photo.py`: upload the fixture, `POST /chat` with the attachment, print the events until `intent_proposed`.

### 5. Contract

`contracts/backend-api.yaml`: `/uploads`, `/uploads/{id}`, `/pairings`, `/pairings/{id}/stream`, `ChatRequest.attachments`, events `user_message`, `paired`, `photo_received`. Fixture `contracts/fixtures/events/photo_received.json`.

## Not in this package

* Reading menus, notes and invoices into suggestions: 1b (`POST /kitchen/ingest`).
* The agent deciding on its own that a chat photo is context: 2a.
* Voice input: frontend only (browser speech recognition), see package 3.
* Frontend and React UI.

## Done when

* `curl -F file=@contracts/fixtures/uploads/order-list.jpg localhost:8000/uploads` returns meta; the stored image is ≤ 1600 px.
* With `LLM_PROVIDER=anthropic`: a chat turn with the list photo ends in `intent_proposed` with 5 items and correct quantities.
* With `LLM_PROVIDER=scripted`: the same turn ends in `intent_proposed` via the fixture text.
* Phone flow on a real phone in the same Wi-Fi: open `phone_url`, take a photo, the desktop's pairing stream receives `photo_received` within 2 s.
* A photo of a menu without an order request does not call `propose_intent`.
* Tests in `backend/tests/`: upload validation and downscale, pairing stream receives `photo_received`, Anthropic and OpenAI message builders include the image parts (no network).
