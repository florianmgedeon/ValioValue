# 3 · Polish: rules from a sentence, coverage, forecast

Status: 3.1 and 3.2 built (14 Nov 2026), 3.3 to 3.6 open · Wave 2 (independent of 2a; parts use 1c and 2b) · Shared contract: [`README.md`](README.md).

Smaller items that make the Kitchen tab and the read-back feel finished. Each section can be built and merged on its own; do them in this order and stop when time runs out.

## 3.1 Rule from a sentence (`POST /mandate/draft`)

Mockup: Kitchen tab → Must → "+ Add rule": the buyer types "No sesame, not even traces" and gets a condition they can check before adding it.

* `POST /mandate/draft {buyer_id?, text, kind: "must" | "prefer"}` → `{"rule": {"attr": "allergens", "op": "not_contains", "value": "sesame", "if_unknown": "exclude"}, "label": "No sesame, incl. may contain", "explanation": "…", "coverage": {…}}`.
* One LLM call with the attribute list and operators from `GET /policy-catalog` (same approach as 1b's ingest). Validate the result through `POST /mandate/preview` on a copy of the mandate; invalid → 422 with the reason.
* Nothing is saved. The UI adds the rule to its draft and goes through the normal `PUT /mandate` signature.
* Scripted provider: handle "no <allergen>" and "only Finnish <category>" with regexes, else 422 "Please use the form".

## 3.2 Rule coverage (`POST /internal/rule-coverage` in mcp-server, `POST /mandate/coverage` in backend)

Mockup: "label known for 1,412 of 1,431 products" under every must, and "Checked against 1,431 products" in the add-rule form. Honest about missing data, rare in other products.

* mcp-server: `POST /internal/rule-coverage {rule, category?}` → `{"checked": 1431, "known": 1412, "safe": 906, "excluded": 506, "unknown": 19}` using `constraints.evaluate` over the catalog (or the category). Cache per rule hash.
* Backend passthrough `POST /mandate/coverage`. `GET /kitchen` (1b) may add coverage per must if cheap; otherwise the UI calls it per rule.

### Built (3.1 and 3.2)

* `backend/buyer_agent/routes_rules.py`: `POST /mandate/draft`, `POST /mandate/coverage` (without `rule`: coverage for every must of the current mandate).
* `mcp-server/valio_mcp/coverage.py`: `POST /internal/rule-coverage`, plus `GET /internal/categories?q=` so "only Finnish fish" becomes `applies_to: category in [...]`. The draft returns these categories as `scope` with example product names.
* The label and the explanation come from the validated rule, not from the model's words. The model only proposes `{attr, op, value}`.
* If the LLM call fails (not a refusal), the scripted regexes are tried before giving 422.
* Numbers on the full catalog (17,546 products): "no sesame incl. may contain" known for 9,300, unknown 8,246. Allergen data is missing for about half the catalog, so expect lower "known" numbers than in the mockup.
* Tests: `mcp-server/tests/test_coverage.py`, `backend/tests/test_rules.py` (runs the mcp-server in-process). Example: `contracts/fixtures/mandate-draft-sesame.json`.

## 3.3 Forecast before signing (optional, decide first)

Mockup: the read-back footer says "4 lines will go through alone. Granola needs you: its label changed to 'may contain tree nuts'." before the buyer signs. Today nothing is searched before signing, so this cannot be known yet.

Two options:

* **A (cheap, recommended for the hackathon):** the forecast appears after the search, on the `offers` / funnel step, not on the read-back. No backend work; the UI moves the sentence.
* **B (real):** `POST /internal/intents/{id}/forecast` (read-only, no quotes saved): for items with a `preferred_product_id`, evaluate the usual product against all musts and the grants' scopes and limits with the catalog's last known price, and return per item `likely: "auto" | "ask" | "block"` with the reason. Not binding; the read-back says "expected". The trust guarantee is unchanged because `place_order` still decides.

Write the decision into this file before building.

## 3.4 Price estimate in the read-back (optional, needs 1c and 2b)

* For items with a usual product: the last unit price paid (from `GET /orders` data), shown as "≈ €130.80, last price". No new Valio call.

## 3.5 Voice input (frontend only)

* Browser `SpeechRecognition` / `webkitSpeechRecognition` with `lang = "fi-FI"` or `"en-GB"`, the text goes into the composer. No backend. Note for the React build; nothing to do here.

## 3.6 Sessions survive a restart (optional)

* `agent.SESSIONS` lives in memory. If demo rehearsals suffer from restarts: persist `history` and `events` per session as JSON in `DATA_DIR/sessions/`. Skip unless it hurts.

## Done when

* 3.1 and 3.2 work with the mockup's examples ("No sesame, not even traces"; coverage numbers on the three seeded musts).
* The decision for 3.3 is written down; if B, the granola item is forecast as `block` with the label reason.
* Tests for every endpoint built.
