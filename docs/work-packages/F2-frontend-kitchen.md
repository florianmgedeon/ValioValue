# F2 · Frontend: Kitchen tab (profile, rules, autonomy, permissions)

Status: done 14 Nov 2026 · Frontend wave B: after F0, parallel to F1, F3, F4 · Shared rules: [Frontend packages in `README.md`](README.md#frontend-packages-f0f4).

## Why

Where the buyer sees and changes what the agent knows and may do. Mockup: Kitchen tab.

## Files

Only `src/features/kitchen/**`.

## Build

### 1. Header and teach card

* Title = kitchen name; line "Kitchen profile vN · mandate vM signed by <name>, <date> · Valio Aimo enforces every 🔒 rule"; "History" → drawer with `GET /mandate/history` and `GET /kitchen/history` merged by date.
* **Teach me with a photo** (large dark card, three steps, document thumbnails) → `PhoneCamera.open("kitchen")` and "Upload" (file input) → `POST /uploads` → `POST /kitchen/ingest`. New suggestions appear in section 2. Never starts an order (decision W2).

### 2. New from your documents

* `kitchen.suggestions` with kind tag, condition (`Cond` mono chip), source and quote. Accept → `POST /kitchen/suggestions/accept` through `useSignedChange` (signed kinds: must, prefer, autonomy; unsigned: about, usual go through without a modal). The backend decides: it answers 409 only for signed kinds (`kitchen.SIGNED_KINDS`), so prefer is signed too (decision W4). Reject → `/reject`.

### 3. About, Usual products, Sources

* About text with fact chips; "Edit" → inline textarea → `PATCH /kitchen`.
* Usual products: word (handwriting) → product name, source, typical amount per week.
* Sources: thumbnails of uploads (`GET /uploads/{id}`), counts of orders and corrections.

### 4. Must and Prefer

* Must rows: label, `attr op value` condition, source, coverage line ("label known for X of Y", `POST /mandate/coverage` without rule → per must), "if unknown" segmented (exclude / ask me), on/off toggle. Changes collect in a **change bar** ("N changes to signed rules · mandate vM → vM+1 · Review & sign") and go through `useSignedChange` as one `PUT /mandate`.
* "+ Add rule": sentence → `POST /mandate/draft {kind: "must"}` → shows the returned rule, explanation, coverage and scope examples; "Add rule" puts it into the pending changes. 422 → show the message and the manual form (attr / op / value selects from `GET /policy-catalog`).
* Prefer rows: weight 1–3, toggle; "+ Add preference" same as must with `kind: "prefer"`. Preferences are part of the mandate too, so they go into the same change bar.

### 5. How autonomous I am

* Ladder with four rungs (0 Suggest, 1 Within your rules, 2 Small deviations, 3 Standing orders locked). Current from `GET /autonomy`.
* Click a rung → pending change "Autonomy: level N". Level 2 while `recommended_next` is false → modal "We recommend waiting a little" (clean count, what level 2 accepts, musts and limit unchanged) with "Keep level" / "Unlock anyway". Signing: `POST /autonomy/level` through `useSignedChange` (not through the batched `PUT /mandate`, because the backend builds the grant).
* Progress line: "N of 10 orders without a correction…" below level 2; at level 2 a sentence that deviations are marked in Activity.
* g1 row: summary, "Change limit" → number input → `POST /autonomy/level {level, max_order_total}`.

### 6. Extra permissions

* List of grants with `kind: "extra"` from `GET /autonomy` (2d; mock until built): id, label, scope condition, limit, accepts, source, toggle (→ batched `PUT /mandate` with `enabled`).
* "+ Add permission": sentence → `POST /mandate/draft {kind: "grant"}` (2d; mock) → form with three fields: *For these products* (scope chips, match count, examples), *Up to* (amount + per order / per line / per unit), *I may also accept* (+5 % rounding, 1 day late, a missed preference), plus "Still always" with the musts. "Add permission" → pending change, signed via the change bar.

## Not in this package

* Backend for grants and the new level logic (package 2d).

## Done when

* Against the backend: accept a must suggestion → sign modal → mandate +1; add "No sesame, not even traces" via the sentence → signed; switch level 1 → 2 (with the recommendation modal) and back.
* With `VITE_MOCK=1`: extra permissions list and the organic grant draft work as in the mockup.
* Every signed change shows the diff and hash before signing; unsigned kinds never open the modal.

## Built (14 Nov)

* `src/features/kitchen/`: `KitchenView` (data, pending changes, change bar, history drawer), `Profile.tsx` (teach card, suggestions, About, Usual, Sources), `Rules.tsx` (Must, Prefer, add-rule from a sentence with manual fallback from `GET /policy-catalog`), `Autonomy.tsx` (ladder, level-2 recommendation modal, g1 + limit, extra permissions, permission form), `model.ts` (pending operations applied to the freshest mandate at sign time), `kitchen.css` (mockup classes, scoped under `.kt`). `App.tsx`: one line.
* Signing: rule, preference and permission edits collect in the change bar and go through `useSignedChange` as one `PUT /mandate`. A level or limit change is signed after that with `POST /autonomy/level` (second modal). Undoing a change (toggle back) removes it from the bar.
* Extra permissions are read from the mandate (`autonomy.grants` except g1), so disabled grants stay visible. With `VITE_MOCK=1` the fixture grant from `GET /autonomy` is shown too, marked as example; its toggle does nothing.
* The composer on this tab teaches: a typed sentence goes to `POST /kitchen/ingest {text}`, never to `/chat` (W2). Camera and upload photos for `kitchen` go to `/kitchen/ingest` as well.
* Checked in the browser against the backend (scripted provider, fake Valio): accept prefer suggestion → sign modal → mandate v2; accept about → no modal; "No sesame, not even traces" + weight change → one signature → v3; level 2 → 1 → 2 with the recommendation modal; with `VITE_MOCK=1` the organic permission draft → signed grant g3. 375 px: no horizontal scroll. `npm run build`, `lint`, `typecheck` pass.
* Contract note: this file said prefer suggestions are unsigned; the backend signs them (W4). Text above fixed.
