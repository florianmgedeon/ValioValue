# backend

Owner: Florian

Buyer agent: LLM loop, MCP client, REST + SSE API from `contracts/backend-api.yaml`. Port 8000. Drop-in replacement for `mock-backend/`.

```bash
pip install -r backend/requirements.txt
cd backend && python -m buyer_agent.app
python scripts/demo_e2e.py        # full flow incl. human approval and audit
```

## LLM providers

Set `LLM_PROVIDER` in `.env`. All adapters are plain HTTP in `buyer_agent/llm.py`, no vendor SDK.

| `LLM_PROVIDER` | Default model | Notes |
|---|---|---|
| `anthropic` | `claude-sonnet-4-5` | Messages API with tool use. Tested with `LLM_MODEL=claude-haiku-5-5` |
| `openai` | `gpt-4.1-mini` | Chat Completions tools |
| `gemini` | `gemini-2.5-flash` | Gemini's OpenAI-compatible endpoint |
| `openai_compatible` | `LLM_MODEL` | Any endpoint via `LLM_BASE_URL` |
| `scripted` | none | Deterministic search, offer, order. Used automatically when no key is set |

## How a turn runs

1. Open an MCP session with headers `X-Agent-Session` and `X-Buyer-Id` (the Valio side uses them for audit and policy).
2. Loop: LLM gets tools, backend executes calls on MCP, streams `tool_call` / `tool_result`, plus `offers` after `check_offer`.
3. `place_order` ends the turn with `approval_required` or `order_confirmed`.
4. `POST /approvals/{id}` forwards the human decision to Valio with the approver token and emits `order_confirmed` on the original session stream.
