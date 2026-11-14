# V2: Accuracy and engineered trust

Branch `v2-trust-accuracy`, based on `feat/mcp-server-backend` (latest working agent).

## Goal

When the buyer hits **Send**, Valio Aimo executes only what the buyer confirmed. Nothing more, nothing less, nothing different.

We cannot guarantee that against free text, because words are ambiguous. We guarantee it against an **object the buyer saw and confirmed**: "what you see is what you sign".

```
free text ──LLM──> OrderIntent ──buyer confirms + signs──> intent_hash
                                                              │
agent: search, check_offer ──> quote (signed by Valio) ───────┤
                                                              ▼
place_order(quote_id, intent_id) ──> server diff: quote vs intent ──> tier
      Auto  : 0 deviations, inside mandate  -> confirmed, Valio signs receipt
      Ask   : needs a human, buyer signs cart_hash -> confirmed
      Block : hard rule broken -> rejected, rule shown
```

The LLM never decides whether a rule is met. It fills a schema and explains. Deterministic code on the Valio side (the MCP server, our trust boundary) decides.

## Backend: the chain

| # | Step | Where | Open source / standard |
|---|---|---|---|
| 1 | **OrderIntent**: fixed schema (product class, quantity + unit, deadline, hard rules, soft preferences, budget, quantity tolerance). Agent calls `propose_intent`. Missing or ambiguous fields come back as `questions`, never guessed | mcp-server `intent.py` | JSON Schema via Pydantic. Constrained decoding (Outlines, XGrammar) is the production path for self-hosted models, tool schemas do it for hosted ones |
| 2 | **Read-back**: server returns a plain-language read-back plus chips. Human confirms via REST, never via an MCP tool | backend + mcp-server | |
| 3 | **Canonical hash + signature**: intent serialised with JCS, hashed with SHA-256, signed with Ed25519 by the buyer key on human confirm | `trust.py` | RFC 8785 (JCS), Ed25519 (RFC 8032), `cryptography` |
| 4 | **Units and quantity**: exact `Decimal` maths, explicit lot rounding, tolerance is part of the intent (default: round up to whole packs, max +2 %). Outside tolerance is a deviation, not a silent round | `intent.py` | Python `decimal` |
| 5 | **Hard rules tri-state**: each product is `safe`, `excluded` or `unknown` per rule, with evidence (GS1 code, ingredient scan hit, claim). Unknown is never safe. OR logic across signals | `constraints.py` | GS1 allergen levels CONTAINS / MAY_CONTAIN / FREE_FROM |
| 6 | **Signed quotes**: every quote carries source, fetched_at, valid_until and a Valio signature | `server.py` | Ed25519 |
| 7 | **Diff check**: quote line vs confirmed intent, field by field. Result: list of checks plus `deviations` count | `diff.py` | |
| 8 | **Tiers**: Auto / Ask / Block from rule data, not prompt text | `policy.py` | Rule table as JSON. Cedar or OPA is the production path |
| 9 | **Exactly once**: one confirmed intent can be consumed by one order. Repeated `place_order` returns the same order (idempotent). Explicit state machines for intent and order | `store.py` | Stripe-style idempotency, transition tables |
| 10 | **Receipt + verify**: order stores intent_hash, cart_hash, buyer and Valio signatures. `GET /verify/{order_id}` recomputes hashes and checks signatures live | `server.py` | |
| 11 | **Proof**: fixed tricky cases plus property-based tests over random intents and catalog items: no confirmed order ever violates its intent | `mcp-server/tests` | pytest, Hypothesis |

## Frontend: every trust signal backed by a real check

Shipped in `backend/buyer_agent/static/console.html` as reference, contract documented for Moritz's app.

| Component | Shows | Backed by |
|---|---|---|
| Intent chips | "1,000 L · lactose-free · by Mon · max 1,500 €", each editable, Confirm button | OrderIntent + read-back |
| Mandate card | Buyer mandate: limits, categories, hard rules, mandate id | policies.json |
| Rule ticks with evidence | Tick per rule, hover shows field, value, source | constraints evidence |
| Price seal + countdown | "Price locked · 14:32 left", signed by Valio | quote valid_until + signature |
| Intent vs cart | Two columns, big "0 deviations" | diff check |
| Tier badge | Auto / Needs approval (why) / Blocked (rule) | policy tiers |
| Sealed Send button | Lock + fingerprint "Signed · 7F3A-91C2" | cart hash |
| Rejected options | "4 more checked: A too late, B contains milk, C over budget" | search + offer exclusions |
| Honest failure | "Only 800 L by Monday" with options | fulfil_issues |
| Audit timeline | request, intent, quotes, decision, approval, order, with actor | audit log |
| Verify receipt | "Verify" link, live check of hashes and signatures | `/verify` |

## Contract changes (additive)

New SSE events: `intent_proposed`, `intent_confirmed`, `order_rejected`. `offers` gets `excluded` and per-offer `evidence`. `approval_required` and `order_confirmed` get `diff`, `tier`, `receipt`.
New REST: `POST /intents/{id}/confirm`, `GET /orders/{id}/verify`, `GET /mandate`.

## Steps

1. Plan (this file)
2. Contract update
3. Trust core: JCS hashing, Ed25519 keys, intent model, `propose_intent`, confirm endpoint, state machine
4. Constraints with evidence, diff check, tiers, intent-bound idempotent `place_order`, signed quotes, receipts, verify
5. Tests: tricky cases + Hypothesis property tests
6. Buyer backend: pause at intent, resume on confirm, sign on approve, new events, scripted provider
7. Console UI with the trust components
8. End-to-end run offline (fake Valio API + scripted provider), docs, push

## Honest limits (say this in the pitch)

* We guarantee agreement with the confirmed intent and the **declared** supplier data, not that a product is physically free of an allergen.
* Demo keys are generated locally. Production: buyer keys in a wallet or passkey (WebAuthn), Valio key in an HSM, intents as W3C Verifiable Credentials or AP2 mandates.
