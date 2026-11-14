# ValioValue

Hack Junction 2026 · Valio Aimo Challenge · **Trusted Trade Agents**

An external purchasing agent that finds products, compares offers and orders from Valio Aimo, with the guardrails enforced on Valio Aimo's side, not in the agent's prompt.

**V2: what you see is what you sign.** The agent first turns the request (a product, a list or a whole recipe) into an OrderIntent with one item per ingredient. The buyer confirms and signs it. Valio Aimo then diffs every cart line against its item, field by field, and decides: auto, ask (a human signs the exact cart) or block. Every item is bought exactly once. Every receipt can be verified live. Plan and rationale: [`docs/v2-plan.md`](docs/v2-plan.md).

## Architecture

```
frontend/  (localhost:5173)          buyer side
   │  REST + SSE  (contracts/backend-api.yaml)
backend/   (localhost:8000)          buyer agent: LLM loop + MCP client
   │  MCP over Streamable HTTP
mcp-server/ (localhost:8001/mcp, later hosted)   Valio Aimo side
   ├── Valio Aimo mock API  (/api/price, /api/availability)
   ├── static data          (product catalog, delivery reliability, CO2)
   └── SQLite               (quotes, orders, approvals, audit log)
```

* The **frontend** only talks to the backend.
* The **backend** never talks to the Valio Aimo API directly, only through the MCP server.
* **Confirming intents and approving orders** are human only. They go through the backend REST API with the buyer's signature, never through an MCP tool, so the agent cannot confirm or approve itself.

More detail in [`docs/architecture.md`](docs/architecture.md).

## Repo layout

| Folder | Owner | What |
|---|---|---|
| `contracts/` | both | API contract and example fixtures. **Change this first, then the code.** |
| `mock-backend/` | both | Zero-dependency fake backend that replays fixtures |
| `frontend/` | Moritz | Chat, offer cards, approval inbox, audit view |
| `backend/` | Florian | Agent loop, MCP client, LLM provider |
| `mcp-server/` | Florian | Valio Aimo's agent interface |
| `docs/` | both | Architecture, assumptions, demo script |

## Run the real stack

```bash
cp .env.example .env                  # add VALIO_API_KEY, optionally an LLM key
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
scripts/fetch_data.sh                 # downloads the full product catalog into data/raw/
scripts/dev.sh                        # mcp-server :8001 + backend :8000
.venv/bin/python scripts/demo_e2e.py  # intent -> signed confirm -> offers -> signed approval -> verify -> audit
(cd mcp-server && ../.venv/bin/pytest)  # 31 tests incl. property-based guarantee
```

Open **http://localhost:8000/app**: the buyer UI. Paste or upload a recipe or a request, check and sign the intent (one line per ingredient), see per item the products proven safe with their evidence and signed price locks, the cart with "what differs from what you signed", sign it, and verify the receipt. The right column shows the mandate, the trust inspector (mandate, intent, quotes, cart, receipt as a hash chain) and the audit trail.

**http://localhost:8000/console** is the developer view: the reference trust UI (mandate, intent chips with "Confirm & sign", rule evidence, price lock countdown, rejected options, "You asked vs cart", tier badges, "Verify receipt") plus agent steps and audit trail. Served by the backend, no build step, separate from `frontend/`.

No LLM key: `LLM_PROVIDER=scripted` runs a deterministic agent through the same MCP flow.
No network to the Valio API: `FAKE_VALIO=1 scripts/dev.sh` with `VALIO_API_BASE=http://127.0.0.1:8099`.

## Quick start for frontend work

```bash
node mock-backend/server.mjs          # Node 18+, no install needed
# -> http://localhost:8000
```

Try it:

```bash
curl -X POST localhost:8000/chat -H 'Content-Type: application/json' \
  -d '{"message":"I need 1,000 litres of lactose-free milk by Monday"}'
# -> {"session_id":"sess_1"}

curl -N localhost:8000/chat/sess_1/stream        # watch the events arrive

curl -X POST localhost:8000/approvals/appr_01 -H 'Content-Type: application/json' \
  -d '{"decision":"approve","decided_by":"chef.anna"}'
# -> the stream then sends order_confirmed
```

`DELAY_MS=300 node mock-backend/server.mjs` makes the replay faster.

When the real backend is ready, it runs on the same port with the same contract. Nothing changes in the frontend.

## Event types (SSE)

| Event | Show in UI as |
|---|---|
| `message` | Agent chat bubble (markdown) |
| `intent_proposed` | Read-back, chips, questions, "Confirm & sign" button with `intent_hash` fingerprint. Confirm via `POST /intents/{id}/confirm` |
| `intent_confirmed` | "Signed by ..." line, agent continues in the same stream |
| `order_rejected` | Blocked box with the rule and the diff |
| `tool_call` | Live step indicator, use the `label` field |
| `tool_result` | Tick off the step, show `summary` |
| `offers` | Product cards with price, availability, reliability, CO2, rank, rule `evidence`, `provenance`, and `excluded` options |
| `approval_required` | Approval card with `diff` (you asked vs cart), `tier`, "Approve & sign cart" sending `cart_hash` |
| `order_confirmed` | Confirmation card with `tier`, `diff`, `receipt`, "Verify receipt" via `GET /orders/{id}/verify` |
| `error` | Error banner |
| `done` | Agent finished this turn |

Examples for each are in `contracts/fixtures/`.

## Working together

* `main` must always run.
* Short feature branches, merge often.
* Contract changes: edit `contracts/` in its own commit and tell the other person.
* Secrets only in `.env`, never committed. Copy `.env.example`.
