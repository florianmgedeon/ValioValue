# UX concept: context-first purchasing agent

Draft for discussion. Starting point: the "Airbnb × Apple" mockup (onboarding from documents, mandate cards, probation, one decision, receipt). Product goal: [`product-vision.md`](product-vision.md).

## Principles

1. **One input, structure afterwards.** The buyer types, speaks or photographs. The agent turns it into structure (the intent read-back), and the buyer edits *that*. No forms before the agent has understood.
2. **Context is always visible.** When the agent uses something it knows ("milk = Valio Eila"), the order shows it, with its source. The buyer never wonders why.
3. **Every learning is a one-tap question.** The agent proposes, the human accepts. Remember / just this time.
4. **Autonomy is earned and shown.** One clear level, not twenty switches.
5. **Trust signals stay, but quiet.** Hashes, signatures, diff live one tap away (trust inspector), not in the main flow.

## Information architecture

Two places, one composer.

```
┌────────────────────────────────────────────────────────────┐
│ Valio Aimo agent        [Probation · 4/10 ▓▓▓░░] [Kaleva] │
├────────────────────────────────────────────────────────────┤
│  ORDER  |  KITCHEN                                         │  ← two tabs
├────────────────────────────────────────────────────────────┤
│                                                            │
│   (order flow or kitchen profile)                          │
│                                                            │
├────────────────────────────────────────────────────────────┤
│ [📷] [📎]  What do you need? "200 L milk by Monday"   [🎤] [→] │  ← always there
└────────────────────────────────────────────────────────────┘
```

* **Composer** (bottom, sticky, everywhere): text, voice, camera, file. Same field for orders and for teaching ("we're vegan from now on"). The agent sorts it: order, context, or both, and says which.
* **Order**: the current request from read-back to receipt, plus recent orders.
* **Kitchen**: the kitchen profile (the mockup's mandate cards, grown up).

## Flow 1: Onboarding, "Let me get to know your kitchen"

Keep the mockup's first screen, change two things: it is a real drop zone, and it ends in a first order, not in a settings page.

```
Let me get to know your kitchen.
Drop what you already have: menus, shopping lists, invoices, delivery notes.

┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌ ─ ─ ─ ─ ─ ┐
│ menu.jpg    │ │ invoice.pdf │ │ list.jpg    │   + add / 📷
│ ✓ read      │ │ reading…    │ │ queued      │ └ ─ ─ ─ ─ ─ ┘
└─────────────┘ └─────────────┘ └─────────────┘
Or tell me in one sentence: [ We're a school canteen in Espoo, nut-free … ]

Here's what I learned                                  accept all (6)
 ABOUT   School canteen, ~400 meals/day         from menu.jpg         ✓ ✗
 MUST    No peanuts, incl. "may contain"        from menu.jpg         ✓ ✗
 USUAL   milk → Valio rasvaton 1 L, ~120 L/wk   from invoice.pdf      ✓ ✗
 USUAL   oat drink → Oatly Barista 1 L          from invoice.pdf      ✓ ✗
 PREFER  Finnish first                          from menu.jpg         ✓ ✗
 LIMIT   Orders alone up to €300                you, just now         edit

[ Start with this kitchen → ]   "Your usual weekly order is ready to check."
```

Changes vs. mockup:
* Add **ABOUT** and **USUAL** card kinds. MUST / PREFER / AUTHORITY stay.
* Per-card "If missing: exclude / ignore / ask" and "Agent decides / Asks on deviation" move into the card's detail view. Defaults: MUST → exclude if unknown, PREFER → ignore. Onboarding is accept/reject only.
* Cards show the **snippet of the source** (cropped photo region or invoice line), not a generic document icon. That is the "it really read my menu" moment.

## Flow 2: Ordering

### 2a. Input

One field. Examples that must all work:
* "200 L milk by Monday"
* "the usual, but double the oat drink" (needs USUAL)
* 🎤 voice note in Finnish
* 📷 photo of a whiteboard list
* 📷 photo of next week's menu ("order for this menu, 400 portions")

The mockup's three fields (How much / When / Rules) go away. They come back as **editable chips** in the read-back.

### 2b. Read-back with context

```
You asked: "200 L milk by Monday"

 ┌───────────────────────────────────────────────────────────┐
 │ Milk                                              200 L   │
 │ Valio Eila kevytmaitojuoma 10 L Novobox · 20 boxes        │
 │ ◉ your usual (from 12 orders)     ✓ lactose-free  ✓ FI    │
 │                                   [change product]        │
 └───────────────────────────────────────────────────────────┘
 Deliver  [Mon 16 Nov, 06:00 · dock B]   Budget [≤ €2,000]
 Applied from your kitchen: Lactose-free · Finnish first · Dock B

 [ Confirm & order ]        est. €212 · inside your limit → orders alone
```

* Each item card shows **which context entry decided it** (◉ your usual, MUST chips, PREFER chips). Tap a chip to see the source.
* The footer says in advance what will happen: *orders alone* / *will ask you about 1 thing* / *blocked by a rule*. No surprises after Confirm.
* Questions from the agent (`open_questions`) appear as inline choice buttons on the affected item, not as a chat message.

### 2c. Sourcing, decision, receipt

Keep the mockup's parts, they are good: "How I narrowed it down" funnel, **one decision for you** (with options and price difference), receipt "What I did and why". Add:
* In the funnel, a line per context rule ("Finnish first: 27 → 19").
* In the receipt, a line "Used kitchen profile v7".

### 2d. Learning moment

Right after the receipt (or after a change the buyer made):

```
 💡 You changed Arla 1 L to Valio Eila 10 L.
    Use Valio Eila 10 L whenever you say "milk"?   [Remember] [Just this time]
```

Max one such prompt per order. Accepted → new USUAL entry, source "your correction on order #41".

## Flow 3: Kitchen profile (tab "Kitchen")

The mockup's mandate page, organised by what the buyer thinks about, not by rule type:

```
Hotel Kaleva · kitchen profile v7 · signed by Anna, 12 Nov      [history]

ABOUT US         Hotel breakfast buffet, 180 guests/day, Finnish-first   ✎
USUAL PRODUCTS   milk → Valio Eila 10 L · butter → Valio 500 g · …  (14) ›
MUST             Lactose-free milk · Delivery Mon 06:00 dock B       (3) ›
PREFER           Finnish first · Lower CO₂                           (2) ›
AUTONOMY         Level 2 · orders usual products alone up to €2,000      ›
NEW (2)          "Prefer organic when ≤ 10 % more" from Sustainability.pdf  ✓ ✗
SOURCES          menu.jpg · invoices Q3 · 41 orders                       ›
```

* Every row opens the mockup-style card with source snippet, data coverage ("origin known for 27 of 27"), on/off.
* Changing MUST or AUTONOMY creates a new mandate version that needs a signature. Changing ABOUT / USUAL / PREFER does not.
* **History**: what changed when, and which orders used which version.

## Autonomy: one ladder instead of many switches

| Level | Name | Agent may | Unlock |
|---|---|---|---|
| 0 | Suggest | Prepare carts, human signs every order | start |
| 1 | Usual | Order USUAL products alone, inside MUST and the limit | 10 orders without correction (agent proposes, human accepts) |
| 2 | Within mandate | Order anything inside MUST and the limit, ask on deviations | human chooses |
| 3 | Standing orders | Reorder usual quantities on a schedule | human chooses |

Header shows the level and progress (the mockup's "Probation · week 1 · 1/3 autonomous", made concrete). Per-rule overrides stay available in card details for power users.

## Mockup: keep / change / drop

| Keep | Change | Drop |
|---|---|---|
| Onboarding from documents, "found in …" provenance | Real drop zone + camera, source snippets instead of icons | Three input fields |
| MUST / PREFER / AUTHORITY cards | Add ABOUT and USUAL, group by topic | Per-card segmented controls on first contact |
| Probation indicator | Turn into the autonomy ladder with real unlock criteria | Single-product (milk only) assumption |
| Funnel, one decision, receipt | Show which context entry acted in each step | |
| Data coverage line ("13 of 27") | Keep, it is honest and rare | |

## Backend gaps (what the UI needs that does not exist yet)

Today the mandate is a static `mcp-server/policies.json` (spend limit, mandatory allergen rules) and the agent has no memory across sessions.

| Need | Proposal | Side |
|---|---|---|
| Kitchen profile store | `GET/PATCH /kitchen` (about, usual, prefer, sources, history), per buyer, JSON file or SQLite | buyer backend |
| Hard rules + limit editable | `POST /mandate` revise → new `mandate_id` v+1, human signs `mandate_hash`; Valio side replaces static policies.json | Valio (mcp-server) |
| Context in the agent | Profile rendered into `system_prompt`; USUAL map passed so `propose_intent` can set a preferred product per item | buyer backend |
| Usual product as default | Item field `preferred_product_id`; `search_products` ranks it first; diff shows "your usual" vs. other | both |
| Ingest photos/PDFs | `POST /kitchen/ingest` (multipart) → vision LLM → proposed entries with source snippet (bbox or line) | buyer backend |
| Learning prompts | After `order_confirmed`, compare intent vs. cart vs. buyer edits → `context_suggestion` SSE event | buyer backend |
| Autonomy level | Mandate field `autonomy_level` + `auto_limit`; `policy.tier` uses it (e.g. level 0 → every order is `ask`) | Valio (mcp-server) |
| Composer routing | Agent tool or classification: order vs. teach vs. both | buyer backend |

## Demo script (3 minutes)

1. Drop a menu photo and an invoice. Profile fills in 20 seconds, six cards, accept all.
2. Say "200 litres of milk by Monday". Read-back shows "your usual", lactose-free, dock B. Orders alone, receipt.
3. Photograph a handwritten list with five items. One item has a pack-size deviation: one decision, sign.
4. Learning prompt: "Remember Valio Eila for milk?" Remember. Profile v8, probation bar ticks.
5. Tap the receipt: verify live. Context changed what was proposed, Valio Aimo guaranteed what was ordered.
