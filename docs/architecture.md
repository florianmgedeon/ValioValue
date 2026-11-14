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
2. New request: the LLM calls `propose_intent`. The backend emits `intent_proposed` and stops. The read-back text comes from Valio, not the model.
3. The human confirms (`POST /intents/{id}/confirm`, with the displayed `intent_hash`). The backend signs the hash with the buyer key, Valio verifies it, the backend emits `intent_confirmed` and resumes the agent.
4. The LLM calls `search_products` and `check_offer` with the `intent_id`, then `place_order(quote_id, intent_id)`.
5. `place_order` returns `confirmed` (auto), `pending_approval` (ask) or `rejected` (block, the agent may try the next offer once).
6. Approve: the human sends the `cart_hash` they were shown, the backend signs it, Valio confirms and signs the receipt.

The LLM provider sits behind a small interface so it can be swapped. `scripted` runs the same flow without a key.

## MCP tools

See [`mcp-server/README.md`](../mcp-server/README.md). Not a tool on purpose: confirming an intent, approving an order.

## Trust chain

| Object | Hash | Signed by |
|---|---|---|
| Mandate | `mandate_hash` (RFC 8785 + SHA-256) | part of every intent |
| OrderIntent | `intent_hash` over the binding fields | buyer (Ed25519) on confirm |
| Quote | hash of price, quantity, availability, expiry | Valio Aimo, when quoted |
| Cart | `cart_hash`, names the `intent_hash` and quote signature | buyer, only for tier ask |
| Receipt | over order, intent hash, cart hash, tier, approver | Valio Aimo |

`GET /verify/{order_id}` recomputes everything from stored content.

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
* An offer "can fulfil" only if not sales-blocked, `availableAmount >= quantity`, `availableDate <= requestedDate` and the order cut-off (`closingTime`) has not passed. Terminal products (999999) count as available.
* The real API returns `availableDate` = requested date even when `closingTime` is already over (seen for terminal products: cut-off Thursday 15:00 for Monday). We treat a passed cut-off as "cannot deliver on that date", and `place_order` rejects it (`order_cutoff` rule).
* `allowedLotSize` is 0.01 for 96% of products. We read that as whole sales units, except for goods sold by KG.
* Litres per sales unit come from `catalogPrice / comparisonPrice`, snapped to the nearest 0.05 when within 1% (prices are rounded to cents).
* Search ranking uses a category signal learned from the catalog (share of a category's products whose name contains the term) and ignores negations like "milk free" or "maidoton", so "milk" finds milk, not milk-free wafers.
* Lactose-free = nutritional claim `FREE_FROM_LACTOSE`, `nonAllergen ML FREE_FROM`, or the name. `LOW_LACTOSE` does not count.
* Lactose-free detection: product name (laktoositon, laktosfri, lactose free), a nutritional claim containing LACTOSE, or lactose marked FREE_FROM.
* Approval is a REST call protected by a token the LLM never sees. The agent can request, never approve.
* The agent's identity is the MCP session headers `X-Agent-Session` and `X-Buyer-Id`. Production would replace these with OAuth client credentials or verifiable credentials.
