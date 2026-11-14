# Product vision: a purchasing agent that knows your kitchen

## The goal in one sentence

A kitchen says what it needs in its own words ("200 L milk by Monday", a photo of a handwritten list), and the agent orders the right products from Valio Aimo, because it knows the kitchen: who they are, what they always buy, what they never accept, and how much it may decide on its own.

## Why context is the product

Every purchasing agent can turn "milk" into a catalog search. That is not the hard part. The hard part is that "milk" means something different in every kitchen:

| Kitchen | "Milk" means |
|---|---|
| Hotel breakfast, Helsinki | Valio Eila lactose-free 10 L Novobox, Finnish, delivered Mon 06:00 at dock B |
| School kitchen, Espoo | Skimmed milk 1 L, nut-free site, max €300 per order without the head cook |
| Vegan restaurant | Oat drink, barista version, never dairy |

A buyer should never have to type that again. So the centre of the product is the **kitchen context**: a living profile of the restaurant that the agent uses on every order, and that grows with every order.

## What the kitchen context holds

| Layer | Example | Enforced where |
|---|---|---|
| **About us** (soft facts) | "School canteen, 400 meals a day, Finnish-first, closed in July" | Agent prompt: shapes interpretation and questions |
| **Usual products** (vocabulary) | "milk" → Valio Eila 10 L Novobox, usually 200 L a week | Agent + ranking: the default pick, deviation is visible |
| **Must** (hard rules) | No peanuts incl. "may contain", vegan, delivery by 06:00 | Valio Aimo mandate: checked by code on every order line, cannot be talked away |
| **Prefer** (soft rules) | Finnish first, lower CO₂, organic when ≤ 10 % more | Ranking and explanation, never blocks |
| **Autonomy** (authority) | Orders usual products alone up to €2,000, everything else asks | Valio Aimo tiers: auto / ask / block |

Every entry carries **where it came from** (menu photo, invoice, order history, a correction the buyer made) and a **status** (proposed, active, off). Nothing the agent learns becomes active without one tap from a human. Active hard rules and limits are part of the signed mandate, so they travel with every order and every receipt.

## How the context gets built

1. **Show, don't type.** Onboarding is a drop zone: photos of menus, shopping lists, invoices, a delivery note, a sustainability report. The agent reads them and proposes entries ("Your menu says 'Finnish breakfast'. Prefer Finnish origin?").
2. **Learn from orders.** Every correction becomes a question: "You swapped Arla for Valio Eila. Use Valio Eila whenever you say milk?" Remember / just this time.
3. **Learn from approvals.** If the buyer approves the same kind of deviation three times, the agent offers to stop asking about it.
4. **Earn autonomy.** The agent starts on probation (suggests, the human signs). After a run of orders without corrections it proposes more autonomy. The human decides.

## The killer feature (hackathon pitch)

**Photograph your kitchen, and the agent learns who you are.** One photo of a menu or a handwritten list both creates an order and teaches the agent something permanent about the restaurant. Other teams build a chat that searches a catalog. We build an agent whose understanding of the customer compounds, while every rule that matters is still enforced by Valio Aimo, not by the prompt.

## What stays true from V2

The context changes *what the agent proposes*. It never weakens *what gets guaranteed*: the buyer still sees and signs the intent, Valio Aimo still checks every line against it, and the receipt names the mandate version that was in force. See [`v2-plan.md`](v2-plan.md) and [`architecture.md`](architecture.md).

## Non-goals for the hackathon

* Full inventory or stock management.
* Multi-supplier comparison outside Valio Aimo.
* Menu planning or recipe generation from scratch.
