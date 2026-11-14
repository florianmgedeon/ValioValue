# F3 · Frontend: onboarding and the phone as camera

Status: done 14 Nov 2026 · Frontend wave B: after F0, parallel to F1, F2, F4 · Shared rules: [Frontend packages in `README.md`](README.md#frontend-packages-f0f4).

## Why

The killer feature: photograph what the kitchen already has (menus, fridge notes, invoices) and the agent learns from it; photograph an order list and it orders. Mockup: onboarding screen and the phone modal.

## Files

`src/features/onboarding/**`, `src/features/camera/**`, and the body of `PhoneCameraProvider` (F0 leaves it as a stub in `src/app/`).

## Build

### 1. Phone modal (`PhoneCameraModal`)

* `open(where)` → `POST /pairings` → QR code of the pairing URL (`qrcode` npm package, SVG) and the short code; subscribe to the pairing stream.
* `paired` → "Anna's iPhone connected"; `photo_received {upload_id, where}` → close the modal and call the handler registered for `where`: `order` → F1 starts a chat with the attachment; `kitchen` and `onboard` → `POST /kitchen/ingest`.
* The desktop also offers "Upload" (file input, `capture` on mobile) → `POST /uploads` → same handler. On a phone (narrow viewport or touch) the camera button opens the file input with `capture="environment"` directly instead of a QR code.
* The phone page itself is the backend's `GET /phone` (package 1a); nothing to build for the phone in React.
* The mockup's simulated viewfinder is not needed; show a live "Waiting for your phone…" state and the received thumbnail.

### 2. Onboarding (`OnboardingView`)

Shown when the kitchen profile has no version yet (route decided in F0), and from "Restart onboarding" in a dev menu.

* Hero, document grid: every upload as a `Paper`-style card with status Ready / Reading… (scan animation) / Read; drop zone (drag and drop, multiple files) + "Use your phone".
* "Read my kitchen" → `POST /kitchen/ingest` with all upload ids; while running, cards show Reading…; result suggestions appear one by one as LEARN cards.
* "Or tell me in one sentence" input → `POST /kitchen/ingest {text}`.
* "Skip, use example kitchen" → ingest the fixture uploads in `contracts/fixtures/uploads/` (menu, allergy note, invoices).
* LEARN card: kind tag, title, "from <document>: “quote”" in handwriting, sub line, signed tag for must/autonomy, ✓ / ✕. Hover → highlight the quote in the document image: the ingest result carries per suggestion the quote and (if 1b provides it) a bounding box; draw the highlight as an absolutely positioned box over the image. Without a box, show the quote only (decision W1: quotes, no boxes needed).
* Adopt bar: "N things to remember. 2 of them are signed rules…" → "Sign & start ordering" → accept all checked suggestions in one go through `useSignedChange` → route to `#order`.

## Not in this package

* Kitchen-tab suggestions list (F2 reuses the LEARN card from here: export it from `src/features/onboarding/LearnCard.tsx`).

## Done when

* Phone on the same Wi-Fi: scan the QR, take a photo, it appears on the beamer and is read (onboarding and Kitchen) or starts an order (Order tab).
* Onboarding with the three fixture documents produces the suggestions from 1b's fixtures; accepting signs one mandate version.
* Without a phone: drag and drop and the file input work.

## Built (14 Nov)

* `src/features/camera/PhoneCameraModal.tsx`: `POST /pairings` (purpose `order` or `kitchen`), QR code as SVG (`qrcode`), phone URL and short code, live `paired` / `photo_received` from the pairing stream, received thumbnail in a phone frame, then closes. "Upload from this computer" uses the provider's file input.
* `src/app/PhoneCamera.tsx` (provider body): `open(where)` shows the modal on a desktop and the device camera (`capture="environment"`) on a phone (coarse pointer, narrow). Photos for `kitchen` / `onboard` with no registered handler are ingested (`features/camera/ingestPhoto.ts`, toast with the result), so the Kitchen tab learns from photos before F2 registers its own handler. `order` photos go to F1's handler.
* `src/features/onboarding/OnboardingView.tsx`: hero, document grid (Ready / Uploading… / Reading… with scan line / Read / Failed), drop zone (drag and drop, several files, file picker) + "Use your phone", "Read my kitchen" (one `POST /kitchen/ingest` per document, in order, LEARN cards appear one by one), "Or tell me in one sentence", "Skip, use example kitchen" (uploads `menu-w47.jpg`, `allergy-note.jpg`, `invoices-oct.pdf` from `contracts/fixtures/uploads/`), order-list hint and ingest notes under the card, "Try reading again" for failed documents. Hover a card: its document gets a ring and the quote is shown on it (no boxes, W1). Adopt bar → one `POST /kitchen/suggestions/accept` through `useSignedChange` (one mandate version), then the dropped ones are rejected, then `#order`. Without must / prefer / autonomy it saves without the sign modal.
* `src/features/onboarding/LearnCard.tsx`: `LearnCard`, `suggestionKind`, `suggestionSub`, exported for F2.
* `DemoMenu` ("Demo · restart onboarding", dev only, ≥ 1240 px). Restarting keeps the profile; onboarding adds to it.
* Shared changes: `src/api/types.ts` (`Suggestion`, `IngestResult`, `IngestResponse`), `ingestKitchenDocuments` typed, `App.tsx` (onboard route + demo menu), `vite.config.ts` (`server.fs.allow` for the fixture documents), `qrcode` dependency.
* Checked in the browser against the backend (scripted provider): example kitchen → 8 suggestions → drop one → sign → mandate v2, 7 accepted, 1 rejected, route to `#order`. Pairing simulated with curl (`GET /phone`, `POST /uploads` with `pair_id`): "connected", thumbnail, card added; the order list shows the "send it in the Order tab" hint. Typed sentence → about + 2 musts. 375 px: no horizontal scroll.
* Not checked: a real phone on the LAN (needs `BACKEND_HOST=0.0.0.0` and `PUBLIC_BASE_URL`), and the order hand-off (F1 registers the `order` handler).
