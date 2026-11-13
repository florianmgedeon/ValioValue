# ValioValue

Hack Junction 2026 · Valio Aimo Challenge · **Trusted Trade Agents**

An external purchasing agent that finds products, compares offers and orders from Valio Aimo, with the guardrails enforced on Valio Aimo's side, not in the agent's prompt.

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
* **Approvals** are human only. They go through the backend REST API, never through an MCP tool, so the agent cannot approve its own orders.

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
| `tool_call` | Live step indicator, use the `label` field |
| `tool_result` | Tick off the step, show `summary` |
| `offers` | Product cards with price, availability, reliability, CO2, rank |
| `approval_required` | Approval card with reason and policy checks |
| `order_confirmed` | Confirmation card |
| `error` | Error banner |
| `done` | Agent finished this turn |

Examples for each are in `contracts/fixtures/`.

## Working together

* `main` must always run.
* Short feature branches, merge often.
* Contract changes: edit `contracts/` in its own commit and tell the other person.
* Secrets only in `.env`, never committed. Copy `.env.example`.
