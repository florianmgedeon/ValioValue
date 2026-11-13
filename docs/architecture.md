# Architecture

## Two parties, two services

| | Buyer side | Valio Aimo side |
|---|---|---|
| Service | `backend/` + `frontend/` | `mcp-server/` |
| Holds | LLM key, chat sessions, buyer UI | Valio API key, quotes, orders, policy, audit log |
| Trust | Untrusted from Valio's view | Source of truth |

The MCP server is the trust boundary. Every rule that matters is enforced there.

## Agent loop (backend)

1. Fetch the tool list from the MCP server.
2. Send user message plus tool definitions to the LLM.
3. LLM returns tool calls. Backend runs them on the MCP server and streams `tool_call` / `tool_result` events.
4. Repeat until the LLM gives a final answer.
5. If `place_order` returns `pending_approval`, emit `approval_required` and stop.
6. When a human approves via `POST /approvals/{id}`, the backend calls the MCP server to finalise and emits `order_confirmed`.

The LLM provider sits behind a small interface so it can be swapped.

## MCP tools (draft)

| Tool | Purpose | Data source |
|---|---|---|
| `search_products` | Text search plus filters (lactose free, category, certificates, allergens) | local catalog |
| `get_product_details` | Full product info incl. reliability and CO2 | local files |
| `check_offer` | Price and availability for a shortlist on a date. Returns quotes with expiry | mock API |
| `find_substitutes` | Ranked good / better / best alternatives inside the assortment | local + mock API |
| `place_order` | Takes `quote_id`s. Returns `confirmed` or `pending_approval` with reasons | SQLite + policy |
| `get_order_status` | Status of an order or approval | SQLite |
| `explain_decision` | Human-readable audit log for an order | SQLite |

**Resources:** buyer policy (mandate), data provenance.

Not a tool on purpose: `approve_order`. Only humans approve, via the backend REST API.

## Policy engine (in mcp-server)

Checked on every `place_order`:

* `spend_limit`: order total vs. autonomous limit per buyer
* `allowed_category`: product category in the buyer's mandate
* `allergen_rules`: forbidden allergens (school, hospital kitchens)
* `substitution`: substitutes outside stated preferences need approval
* `quote_valid`: quote not expired, before `closingTime`

Policies live in a JSON or YAML file per buyer.

## Facts about the mock API

* Only `/api/ping`, `/api/price`, `/api/availability`. **No search, no ordering.**
* Max 1,000 products per request. Undisclosed rate limits. Do not price the whole catalog. Narrow down with static data first.
* `logisticModel` terminal means quantity unknown (`availableAmount` = 999999).
* `availableDate` can differ from `requestedDate`.
* Customer number does not change results in the challenge.
* CO2 values are category averages, not product measurements.

## Assumptions

Keep a running list here. Judges score visibility of assumptions.

* Orders are simulated in our MCP server because the mock API has no order endpoint.
* Prices returned by `check_offer` are treated as fixed for the quote's lifetime: until the order cut-off (`closingTime`) or 30 minutes, whichever comes first.
* Spend limits compare net prices (`catalogPrice.value`, VAT excluded). B2B buyers think in net.
* Litres or kilos per sales unit are derived from the price API (`catalogPrice / comparisonPrice`), so no unit code translation table is needed. Order quantity is rounded up to `allowedLotSize`.
* An offer "can fulfil" only if not sales-blocked, `availableAmount >= quantity` and `availableDate <= requestedDate`. Terminal products (999999) count as available.
* Lactose-free detection: product name (laktoositon, laktosfri, lactose free), a nutritional claim containing LACTOSE, or lactose marked FREE_FROM.
* Approval is a REST call protected by a token the LLM never sees. The agent can request, never approve.
* The agent's identity is the MCP session headers `X-Agent-Session` and `X-Buyer-Id`. Production would replace these with OAuth client credentials or verifiable credentials.
