# mcp-server

Owner: Florian

Valio Aimo's agent interface. MCP over Streamable HTTP on `localhost:8001/mcp`.

```bash
pip install -r mcp-server/requirements.txt
cd mcp-server && python -m valio_mcp.server
curl localhost:8001/health
```

## MCP tools

| Tool | What it does | Source |
|---|---|---|
| `search_products(query, filters, limit)` | Catalog search with fi/sv/en synonyms. Filters: `lactose_free`, `exclude_allergens`, `category`, `country_of_origin` | static product file |
| `check_offer(product_ids, requested_date, quantity, quantity_unit)` | Live price and availability. Converts `L`/`KG` to sales units (catalog price / comparison price), rounds up to lot size, issues a `quote_id` per product | `/api/price`, `/api/availability` |
| `place_order(quote_id, reason)` | Runs the policy engine. Returns `confirmed`, `pending_approval` or `rejected` | SQLite + `policies.json` |
| `get_order_status(order_id)` | Order and approval status | SQLite |

Resource: `valio://policy/{buyer_id}` (the mandate that is enforced).

## Policy engine

Checked on every `place_order`, server side:

| Rule | On fail |
|---|---|
| `quote_valid` (not expired, before cut-off) | rejected |
| `availability` (full quantity on the requested date) | human approval |
| `spend_limit` (per buyer in `policies.json`, net of VAT) | human approval |

## Human-only endpoints (not MCP)

Bearer `APPROVER_TOKEN`. Called by the buyer backend for its human UI, never by the LLM.

```
GET  /internal/approvals?status=pending
POST /internal/approvals/{approval_id}   {"decision":"approve|reject","decided_by":"chef.anna","comment":"..."}
GET  /internal/orders/{order_id}
GET  /internal/orders/{order_id}/audit
```

## Data

* `data/raw/products.json` (gitignored, fetch with `scripts/fetch_data.sh`). Falls back to `data/products.sample.json` (6 invented DEV SAMPLE products) when missing.
* `data/delivery_reliability.csv`, `data/carbon_footprint.csv` from the challenge.
* Offline: `python mcp-server/dev/fake_valio_api.py` serves invented prices on `:8099` in the real API's shape.
