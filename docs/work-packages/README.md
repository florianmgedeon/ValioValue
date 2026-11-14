# Work packages: backend for the "kitchen agent" UI

Status: planned 14 Nov 2026. Goal: the backend can do everything the clickable mockup shows, so the React UI can be built on real endpoints afterwards.

Read first, in this order:

1. [`docs/product-vision.md`](../product-vision.md): what we build and why (context is the product).
2. [`docs/ux-concept.md`](../ux-concept.md): flows and screens.
3. The mockup: [`frontend/mockup/kitchen-agent.html`](../../frontend/mockup/kitchen-agent.html). Open it in a browser, click through onboarding, "Photograph next week's list", "Try to talk me out of a rule" and the Kitchen tab.
4. [`docs/policy-v3-plan.md`](../policy-v3-plan.md): Must / Prefer / Autonomy, mandate versions, Cedar. **Already built.**
5. This file (shared contract), then your package.

## Packages

| Package | File | What | Wave |
|---|---|---|---|
| 1a | [`1a-photos-in-chat.md`](1a-photos-in-chat.md) | Photos and PDFs in the chat, phone as camera (pairing) | 1 |
| 1b | [`1b-kitchen-profile-and-ingest.md`](1b-kitchen-profile-and-ingest.md) | Kitchen profile store (About, Usual, Sources, Suggestions), reading documents into suggestions | 1 |
| 1c | [`1c-usual-products-in-orders.md`](1c-usual-products-in-orders.md) | Agent uses the profile; usual product ranked first; funnel per rule | 1 |
| 2b | [`2b-orders-and-autonomy.md`](2b-orders-and-autonomy.md) | Order list, clean-order counter, autonomy level | 1 |
| 2a | [`2a-learning-in-chat.md`](2a-learning-in-chat.md) | Learning from orders and chat, buyer-side agent tools | 2 (after 1a, 1b, 1c) |
| 2c | [`2c-activity-and-usual-order.md`](2c-activity-and-usual-order.md) | Activity tab: what the agent did and why, per order; "Your usual order" card | 2 (after 1b, 1c, 2b) |
| 2d | [`2d-permissions-and-levels.md`](2d-permissions-and-levels.md) | Extra permissions (own grants, e.g. organic up to €200) from a sentence; levels by deviations, rework of 2b's built level logic | 3 (after 2b, 3.1) |
| 3 | [`3-polish.md`](3-polish.md) | Rule from a sentence, rule coverage, forecast before signing | 2 |

Wave 1 packages run in parallel, each in its own session and branch. Wave 2 starts when wave 1 is merged.

```
1a photos ──┐
1b kitchen ─┼──> 2a learning in chat
1c usual ───┘
2b orders/autonomy ─┬> 2c activity + usual order (also needs 1b, 1c)
                    ├> (3 polish uses 2b's order list for price estimates, optional)
                    └> 2d extra permissions + levels by deviations (also needs 3.1)
```

## Decisions already made (do not reopen in a package)

| # | Decision |
|---|---|
| W1 | **Quotes, not boxes.** A suggestion read from a document carries the verbatim `quote`. No bounding boxes or image coordinates. |
| W2 | **Where a photo goes is decided by where it is taken.** Onboarding and the Kitchen tab send photos to `POST /kitchen/ingest` (learn, never order). The order composer sends them to `POST /chat` (order; from 2a on the agent may also turn context photos into suggestions). |
| W3 | **The kitchen profile lives on the buyer side** (backend), not at Valio Aimo. Valio Aimo only learns the usual product per order item via `preferred_product_id` on the intent. |
| W4 | **Signed things stay in the mandate.** Must, Prefer and Autonomy are mandate entries (`PUT /mandate`, versioned, signed, Cedar). About, Usual, Sources and Suggestions are the kitchen profile (versioned, not signed). Accepting a Must, Prefer or Autonomy suggestion writes a new mandate version. |
| W5 | **Autonomy levels measure deviations, not products** (changed 14 Nov). Level 1 = the agent may order anything that passes every must, without deviation, up to the limit. Level 2 = it may also accept small deviations. A `product_id in [...]` grant scope still works but is optional. Details in 2b. |
| W6 | **We may change the mcp-server** (order list, `preferred_product_id`, funnel counts). Keep the trust boundary: no MCP tool may change the mandate or confirm anything. |
| W7 | **Nothing the agent learns becomes active without a human.** Every learned item is a suggestion with status `proposed` until accepted via REST. |
| W8 | Demo kitchen: `school-kitchen-espoo`. UI language: English. |

## Shared contract

Everything here is the interface between packages. Code against it, do not change it without updating this file in the same commit.

### Uploads (owned by 1a, read by 1b and 2a)

Files are stored on disk:

```
DATA_DIR/uploads/{upload_id}.{ext}     # original bytes (jpg, png, heic converted to jpg, pdf)
DATA_DIR/uploads/{upload_id}.json      # metadata
```

```json
{
  "upload_id": "up_3f9a1c2d",
  "media_type": "image/jpeg",
  "filename": "menu_w47.jpg",
  "bytes": 482113,
  "width": 1600, "height": 1200,
  "created_at": "2026-11-14T09:12:03+02:00",
  "buyer_id": "school-kitchen-espoo",
  "source": "phone | upload",
  "pair_id": "pair_8c1d" 
}
```

Images are downscaled on upload to at most 1600 px on the long edge, JPEG quality 85 (keeps them under the LLM image size limits). PDFs are stored as is, max 10 MB.

Helper (in `backend/buyer_agent/uploads.py`, 1a): `load(upload_id) -> (meta: dict, data: bytes)`. Until 1a is merged, 1b may read the two files directly.

### Kitchen profile (owned by 1b, read by 1c, written by 2a via 1b's functions)

One JSON file per buyer: `DATA_DIR/kitchen/{buyer_id}.json`. Every write bumps `version` and appends to `history`.

```json
{
  "buyer_id": "school-kitchen-espoo",
  "version": 7,
  "updated_at": "2026-11-14T09:30:00+02:00",
  "about": {
    "text": "Primary school kitchen in Espoo. About 420 lunches a day, Monday to Friday. Nut-free site.",
    "facts": [{"id": "f1", "label": "420 lunches / day", "source_id": "s1"}]
  },
  "usual": [
    {"id": "u1", "word": "milk", "aliases": ["maito"],
     "product_id": "463038", "product_name": "Valio Eila® kevytmaitojuoma 10 l novobox",
     "typical_qty": 120, "unit": "L", "per": "week",
     "source_id": "s3", "status": "active"}
  ],
  "suggestions": [
    {"id": "sg4", "kind": "prefer", "status": "proposed",
     "title": "Organic, when it costs at most 10 % more",
     "quote": "Organic share target 25 % by 2027",
     "source_id": "s4",
     "payload": {"attr": "labels", "op": "contains", "value": "EU-Bio", "weight": 1}}
  ],
  "sources": [
    {"id": "s1", "type": "photo", "title": "Lunch menu, week 47", "upload_id": "up_3f9a1c2d", "created_at": "..."},
    {"id": "s5", "type": "correction", "title": "Your decision on order ord_41", "order_id": "ord_41", "created_at": "..."}
  ],
  "history": [{"version": 7, "at": "...", "by": "Anna Virtanen", "summary": "Accepted: \"milk\" means Valio Eila 10 L"}]
}
```

Suggestion `kind` and `payload`:

| kind | payload | On accept |
|---|---|---|
| `about` | `{"text": "..."}` or `{"fact": "420 lunches / day"}` | appended to `about` |
| `usual` | `{"word", "aliases"?, "product_id"?, "product_query", "typical_qty"?, "unit"?, "per"?}` | added to `usual` (product resolved first, see 1b) |
| `must` | a mandate must rule: `{"attr", "op", "value", "if_unknown"?, "applies_to"?, "label"?}`. A list `value` (e.g. several allergens) becomes one rule per value | new mandate version (signed) |
| `prefer` | a mandate preference: `{"attr", "op", "value", "weight"}` | new mandate version (signed) |
| `autonomy` | a mandate grant: `{"scope"?, "limits", "accepts"?}`. `"scope": "usual"` means `product_id in` the current usual products | new mandate version (signed) |

Source `type`: `photo`, `pdf`, `text` (typed sentence), `order`, `correction`.

Python helpers (in `backend/buyer_agent/kitchen.py`, 1b), used by 1c and 2a:

```python
def load(buyer_id: str) -> dict                       # returns an empty profile if none exists
def add_suggestions(buyer_id: str, items: list[dict], source: dict) -> list[dict]   # status "proposed"
def prompt_block(buyer_id: str) -> str                # plain text for the agent's system prompt (About + Usual)
```

Until 1b is merged, 1c reads the JSON file directly and falls back to `contracts/fixtures/kitchen/school-kitchen-espoo.json` (created by 1b; 1c may create the same file if it is first, using the example above).

### New REST endpoints (backend, port 8000)

| Endpoint | Package |
|---|---|
| `POST /uploads` (multipart) → upload meta | 1a |
| `GET /uploads/{upload_id}` → the file, `GET /uploads/{upload_id}/meta` → its metadata | 1a |
| `POST /pairings` → `{pair_id, phone_url}` | 1a |
| `GET /pairings/{pair_id}/stream` (SSE `photo_received`) | 1a |
| `GET /phone` → phone camera page | 1a |
| `POST /chat` gains `attachments: [{"upload_id"}]` | 1a |
| `GET /kitchen`, `PATCH /kitchen`, `GET /kitchen/history` | 1b |
| `POST /kitchen/ingest` | 1b |
| `POST /kitchen/suggestions/accept`, `POST /kitchen/suggestions/{id}/reject` | 1b |
| `GET /orders`, `GET /autonomy`, `POST /autonomy/level`, `POST /autonomy/sync-usual` (built with the old levels; 2d removes `sync-usual`) | 2b |
| `GET /orders/{order_id}/story`, `GET /kitchen/usual-order` | 2c |
| `POST /mandate/draft`, `POST /mandate/coverage` | 3 |

Signed changes (`/kitchen/suggestions/accept`, `/autonomy/level`, `/autonomy/sync-usual`) answer 409 `needs_signature` with a preview first; send the preview's `mandate_hash` back to sign. Level 1 keeps only grants scoped by `product_id` and drops category-scoped grants (as built; 2d changes this: the level only sets g1, other grants are kept).

All take `buyer_id` (query or body), default `config.DEFAULT_BUYER_ID`. For the demo set `DEFAULT_BUYER_ID=school-kitchen-espoo` in `.env`.

### New SSE events (chat stream)

| Event | Payload | Package |
|---|---|---|
| `photo_received` (pairing stream) | `{"upload_id", "url", "media_type", "pair_id"}` | 1a |
| `context_suggestion` (chat stream) | `{"suggestions": [<suggestion>], "reason": "after_order" \| "from_chat" \| "from_photo"}` | 2a |

Existing events (`intent_proposed`, `offers`, `approval_required`, `order_confirmed`, `order_rejected`, …) keep their shape. 1c adds fields to them, never removes.

### File ownership (to avoid merge conflicts)

| File | 1a | 1b | 1c | 2b | 2a | 3 |
|---|---|---|---|---|---|---|
| `backend/buyer_agent/app.py` | `/chat` request model + include router | include router | – | include router | – | include router |
| `backend/buyer_agent/llm.py` | images in messages | – | – | – | – | – |
| `backend/buyer_agent/agent.py` | `run_turn(images)`, history | – | `system_prompt` | one call `autonomy.after_order` | local tools, after-order hook | – |
| new `backend/buyer_agent/uploads.py`, `routes_uploads.py`, `static/phone.html` | ✎ | | | | | |
| new `backend/buyer_agent/kitchen.py`, `ingest.py`, `routes_kitchen.py` | | ✎ | read | | calls | |
| new `backend/buyer_agent/routes_orders.py`, `autonomy.py` | | | | ✎ | | |
| `mcp-server/valio_mcp/intent.py` | | | `preferred_product_id` | | | |
| `mcp-server/valio_mcp/server.py` | | | `search_products`, `check_offer` | `/internal/orders` list route | | coverage route |
| `mcp-server/valio_mcp/diff.py` | | | "not your usual" check | | | |
| `mcp-server/valio_mcp/store.py` | | | | `list_orders` | | |
| `contracts/backend-api.yaml` | own paths | own paths | event fields | own paths | own events | own paths |

New routes go into their own `routes_*.py` with a FastAPI `APIRouter`; `app.py` only gets one `app.include_router(...)` line per package.

### Conventions for every package

* Contract first: add your endpoints and events to `contracts/backend-api.yaml` and an example to `contracts/fixtures/`.
* Tests: mcp-server changes get pytest tests in `mcp-server/tests/`. Backend changes get tests in a new `backend/tests/` (pytest + `httpx.ASGITransport` against `buyer_agent.app:app`, LLM calls faked). Run: `(cd mcp-server && ../.venv/bin/pytest)` and `(cd backend && ../.venv/bin/pytest)`.
* Everything must work with `LLM_PROVIDER=scripted` (no key) in a degraded but working way, so the demo never depends on a live model.
* Run the stack: `scripts/dev.sh` (mcp-server :8001, backend :8000). Offline Valio API: `FAKE_VALIO=1`.
* Docs: update the package file's status line and the "Not built yet" lists in `docs/ux-concept.md` when done.
* Write docs and code comments in plain English, short sentences.

## Frontend packages (F0–F4)

The React app in `frontend/` implements the mockup [`frontend/mockup/kitchen-agent.html`](../../frontend/mockup/kitchen-agent.html) (artifact Qs4e5X53hbzDDmYQkqLD17) against the buyer backend.

| Package | File | What | Wave |
|---|---|---|---|
| F0 | [`F0-frontend-foundation.md`](F0-frontend-foundation.md) | Tokens, shell (header, tabs, autonomy pill, composer), primitives, `useSignedChange`, mock layer | A (alone, first) |
| F1 | [`F1-frontend-order.md`](F1-frontend-order.md) | Order tab: start screen, order mode, stream reducer, live panel, trust drawer | B |
| F2 | [`F2-frontend-kitchen.md`](F2-frontend-kitchen.md) | Kitchen tab: teach card, suggestions, about/usual/sources, must/prefer, ladder, extra permissions | B |
| F3 | [`F3-frontend-onboarding-camera.md`](F3-frontend-onboarding-camera.md) | Onboarding with LEARN cards, phone modal with QR and pairing stream, uploads | B |
| F4 | [`F4-frontend-activity.md`](F4-frontend-activity.md) | Activity tab: order list and story timeline | B |

```
F0 foundation ──┬> F1 order
                ├> F2 kitchen
                ├> F3 onboarding + camera
                └> F4 activity
```

Rules for all frontend packages:

* **Own folder.** Each package writes only `src/features/<name>/**`. Shared code lives in `src/ui/` (primitives), `src/app/` (shell, routing, signing, providers) and `src/api/` (wrappers). A missing primitive goes into `src/ui/` as its own small commit, named in the PR.
* **Data only through `src/api/` wrappers** and TanStack Query; no `fetch` or `EventSource` in components.
* **Not built yet → mock, don't wait.** With `VITE_MOCK=1` the wrappers for 2a, 2c and 2d endpoints return fixtures in the shapes from those package files. Never mock a built endpoint.
* **Every mandate change goes through `useSignedChange`**: the buyer sees diff and hash before signing. Unsigned kitchen-profile changes (about, usual, prefer suggestions) never open the sign modal.
* **Mockup wins on wording and layout**; the contract wins on data. If they disagree, change the mockup or the contract in the same PR and say which.
* **Desktop for the beamer, phone works:** no horizontal scroll at 375 px, 16 px gutters, safe areas.
* **Shared files** (`App.tsx`, `styles.css`, `src/ui/index.ts`): one line per package (a route, an export), no rewrites.
