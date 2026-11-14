# 2d · Extra permissions (own grants) and deviation-based levels

Status: open · Wave 3: start after 2b and 3.1 are merged (both are on main) · Shared contract: [`README.md`](README.md).

## Why

From the UI review (14 Nov): the three policy types are **Must** (never buy, blocks), **Prefer** (ranking) and **Autonomy** (what the agent may buy without asking). Today the only autonomy the buyer can set is the level and its spending limit. The buyer must also be able to say "organic products, up to €200 an order, I don't need to see them". That is not a must (it does not forbid anything else); it is an extra grant beside the level.

Mockup moments: Kitchen → "How autonomous I am":

* the ladder is clickable (0 Suggest · 1 Within your rules · 2 Small deviations · 3 Standing orders, locked). Level 2 before 10 clean orders shows "We recommend waiting a little" with "Unlock anyway". Every change goes through the normal sign flow.
* "Extra permissions": a list of the buyer's own grants (seeded `g2` Fruit and vegetables, up to €150 an order, accepts +5 % pack rounding), each with a toggle, and "+ Add permission": a sentence ("Organic products up to €200 an order") becomes a draft with three fields: *for these products* (scope with match count), *up to* (per order / per line / per unit), *I may also accept* (+5 % rounding, 1 day late, a missed preference). The musts are shown as "still always".
* Activity and receipts name the grant per line ("Alone · g2 Fruit & veg").

## What exists on main (checked 14 Nov)

* **Valio Aimo enforces scoped grants already.** `mandate.py` turns each grant's `scope` conditions, `max_line_total`, `max_unit_price` and `accepts` into one Cedar `permit … order_alone` (`to_cedar`). `policy.py` assigns every autonomous line to exactly one permitting grant and sums `max_order_total` per grant. `grants_used` is on the order. Nothing to build in mcp-server for enforcement.
* **3.1 `POST /mandate/draft`** (`routes_rules.py`) only accepts `kind: "must" | "prefer"`. `_validate` only writes into `mandatory_rules` or `preferences`.
* **2b `autonomy.py`** was built with the **old** level definition: 1 = "Usual products" (every grant scoped `product_id in`), 2 = "Within mandate" (any grant without a product scope), `LEVEL1_ACCEPTS = quantity +5 %`, `POST /autonomy/sync-usual`. With that definition a category- or label-scoped grant (like "organic") makes `level_of` return 2, and `mandate_for_level(…, 1)` **drops** it. Both conflict with this package.

## Build

### 1. Levels by deviations (rework `backend/buyer_agent/autonomy.py`)

As written in [`2b`](2b-orders-and-autonomy.md) "Changed 14 Nov":

* `level_of(m)` reads only the **level grant** `g1` (the one the ladder controls): none / disabled → 0, `accepts` empty → 1, `accepts` non-empty → 2. Other grants never change the level.
* `mandate_for_level(m, level, max_order_total)`: level 0 removes g1, level 1 = g1 with `limits` and `accepts: []`, level 2 = g1 with `accepts: [quantity 5 %, delivery_date 1 day, preference]`. g1 has no scope (any product that passes every must). **All other grants are kept unchanged.**
* `LEVELS = {0: "Suggest", 1: "Within your rules", 2: "Small deviations", 3: "Standing orders"}`.
* Remove `usual_scope`, `/autonomy/sync-usual` and its README line. Keep the clean-order counter as is.
* `GET /autonomy` returns `recommended_next: bool` (clean ≥ needed) instead of hiding level 2: the UI lets the buyer switch any time and only shows the recommendation. `grants` lists g1 with `"kind": "level"` and the others with `"kind": "extra"`.
* Migration: an existing g1 with a `product_id` scope (from the old level 1) is treated as level 1; `POST /autonomy/level` removes that scope.

### 2. Grant drafts (extend 3.1, `routes_rules.py`)

* `DraftRequest.kind` gets `"grant"`. The sentence gives scope, limit and optional accepts:
  ```json
  POST /mandate/draft {"text": "Organic products up to €200 an order", "kind": "grant"}
  → {"kind": "grant",
     "grant": {"label": "Organic products",
               "scope": [{"attr": "labels", "op": "contains", "value": "EU-Bio"}],
               "limits": {"max_order_total": 200}, "accepts": []},
     "explanation": "I may order organic products alone, up to €200 an order. Every must still applies.",
     "coverage": {"checked": 17546, "matching": 212, "known": 17546}, "examples": ["…", "…", "…"]}
  ```
* The LLM proposes scope conditions (same attribute list and operators as musts, from `/policy-catalog`), one limit (`max_order_total | max_line_total | max_unit_price`) and accepts from the fixed list. Product words ("fruit", "dairy") go through `_scope` → `category in [...]` like 3.1 does for musts.
* `_validate` for `kind: "grant"`: add the grant with id `draft` to a copy of `autonomy.grants` and call Valio's mandate preview. Label and explanation are built from the validated grant, not from the model's words.
* Coverage: for a grant, "matching" = products that fit the scope (not "known/excluded"). If `/internal/rule-coverage` cannot express that, add `mode: "match"` there (small mcp-server change).
* Scripted provider: regex for "<organic|Finnish|category word> … up to €<n> (an order|a line|per kg)".
* Nothing is saved. The UI adds the grant to the draft mandate and signs with `PUT /mandate` (next free id `g2`, `g3`, …).

### 3. Toggle and remove grants

* No new endpoint: the UI edits `autonomy.grants[].enabled` in the draft mandate and signs with `PUT /mandate`. Check that `GET /kitchen` or `GET /autonomy` returns each extra grant with `id`, `label`, `summary`, `enabled`, `scope` (human readable), `limits`, `accepts`, `source`.

### 4. Grant per line in the story (2c)

* Order lines already carry `grants` from `policy.py`. 2c's `GET /orders/{id}/story` uses that grant's id and label in the `decided` step ("Alone · g2 Fruit and vegetables") and the receipt lines. If 2c is merged before this package, add it there.

## Not in this package

* Standing orders (level 3).
* Grants learned from photos (1b's `autonomy` suggestions stay as they are; they produce grants in the same shape and benefit from step 3).

## Done when

* A mandate with g1 (no accepts) and an organic grant reads as level 1; switching to level 2 and back keeps the organic grant untouched.
* `POST /mandate/draft {"kind": "grant", "text": "Organic products up to €200 an order"}` returns the grant above with a coverage count; the scripted provider handles it too.
* After signing, an order with an organic line over €200 (organic share) asks, and under it goes through alone with `grants_used: ["g2"]`; a must still blocks an organic product that fails it.
* Tests: `backend/tests/test_autonomy.py` (level detection, mandate_for_level keeps extra grants), `backend/tests/test_rules.py` (grant drafts).
