# F0 · Frontend foundation: tokens, shell, primitives, signing

Status: done 14 Nov 2026 · Frontend wave A: first, alone. F1–F4 start when F0 is merged · Shared rules: [Frontend packages in `README.md`](README.md#frontend-packages-f0f4).

## Why

The mockup ([`frontend/mockup/kitchen-agent.html`](../../frontend/mockup/kitchen-agent.html), artifact Qs4e5X53hbzDDmYQkqLD17) becomes the React app in `frontend/`. F1–F4 build the four screens in parallel; they must not each invent buttons, chips, modals and the sign flow. F0 builds everything they share, so the feature packages only touch their own folders.

## What exists

* `frontend/`: Vite 8, React 19, TypeScript, Tailwind 4 (`@tailwindcss/vite`), `/api` proxy to the backend.
* `src/api/*`: typed wrappers per area, `generated.d.ts` from `contracts/backend-api.yaml`, `subscribeToChatStream`, pairing stream.
* `src/app/App.tsx`: placeholder page.

## Build

### 1. Dependencies

* `@tanstack/react-query` (REST cache, refetch after signing). Nothing else: no router library, no component library, no state library. Chat state is a reducer (F1).

### 2. Design tokens (`src/styles.css`)

* Copy the mockup's `:root` variables (surfaces, ink, `--accent`, `--must`, `--prefer`, `--usual`, `--about`, `--auto`, their `-soft` variants, `--paper*`, `--hl-*`, shadows, fonts) and both dark-mode blocks (`prefers-color-scheme` and `[data-theme="dark"]`).
* Expose them to Tailwind with `@theme` (`--color-must: var(--must)` …) so components write `bg-must-soft text-must`.
* Fonts: Inter, JetBrains Mono, Caveat (handwriting for usual words and quotes) via Google Fonts link in `index.html`.
* Keyframes `rise`, `pulse`, `scan`, `flash`; respect `prefers-reduced-motion`.

### 3. Primitives (`src/ui/`)

One file each, props typed, no data fetching:

| Component | Mockup class | Notes |
|---|---|---|
| `Button` | `.btn` primary / ghost / small | `icon` prop |
| `Icon` | `ic()` | the mockup's SVG paths as a map |
| `KindTag` | `.kind` | `must · prefer · usual · about · auto` |
| `Chip` | `.chip` | kind colour or `plain`, optional icon |
| `SignedTag` | `.signed` | lock + "signed rule" |
| `Card`, `CardHeader`, `CardFooter` | `.card` | variants `decision`, `block`, `receipt` |
| `Toggle`, `Segmented`, `Weight`, `YesNo` | `.toggle`, `.seg`, `.weight`, `.yn` | accessible roles (`switch`, `radio`, `aria-pressed`) |
| `Modal`, `Drawer`, `Toast` | `.scrim .modal`, `.drawer`, `.toast` | Escape closes; one toast host |
| `HashRow` | `.hashrow` | label + monospace hash, copy button |
| `CedarBlock` | `pre.cedar` | light syntax colouring (comment, keyword, string, annotation) |
| `Paper` | `.paper` | document look; variants `hand`, `inv`; children may contain `<Mark>` |
| `Mark` | `mark[data-h]` | `kind`, `on`, `focus`; highlights a quote |
| `Tier` | `.tier` | auto / ask / block / live badge |

### 4. App shell (`src/app/`)

* `useHashRoute()`: `#order | #activity | #kitchen`, default `#order`; `onboard` when the kitchen profile has no `version` (F3 renders it).
* `Header`: brand, tabs, autonomy pill (from `GET /autonomy`: level, name, clean/needed bar, hidden bar at level ≥ 2), kitchen name + avatar. Pill click → `#kitchen` and scroll to the autonomy section.
* `Composer`: fixed bottom bar with camera (opens the phone modal from F3 through a context callback), attach, input, mic (Web Speech API, `en-GB`, fills the input; package 3.5), send; hint chips per tab. `onSubmit` is a prop: F1 wires it to the chat, F2 to "teach" (also the chat).
* `QueryClientProvider`, `ToastProvider`, `PhoneCameraProvider` (context with `open(where: "order" | "kitchen" | "onboard")`; F3 fills in the modal).

### 5. Signing (`src/app/useSignedChange.ts`)

Every change to the mandate answers `409 {needs_signature, preview}` first (`/kitchen/suggestions/accept`, `/autonomy/level`, `PUT /mandate`). One hook for all:

```ts
const sign = useSignedChange();
await sign({ call: (mandate_hash?) => setAutonomyLevel({ level: 2, mandate_hash }), title: "Autonomy level 2" });
```

* First call without hash → on 409 open `SignModal` with the preview's diff lines, the generated Cedar (`GET /mandate/cedar` on the preview if available, else hidden), and `mandate_hash`.
* "Sign as <name>" → same call with the hash → invalidate `mandate`, `kitchen`, `autonomy` queries → toast "Mandate vN signed".
* F2 batches several pending changes (change bar "3 changes to signed rules · Review & sign"): the hook accepts a list and shows them as one diff when they go through one `PUT /mandate`.

### 6. Mock layer (`src/api/mock/`)

* `VITE_MOCK=1` (in `.env.example`, off by default): wrappers for endpoints that are not built yet return fixtures instead of calling the backend. Each mocked wrapper says which package removes the mock.
* Initial list: `GET /kitchen/usual-order`, `GET /orders/{id}/story` (2c), `POST /mandate/draft` with `kind: "grant"` (2d), new-style `GET /autonomy` with `recommended_next` and `grants[].kind` (2d). Fixtures follow the JSON shapes in the 2c and 2d package files.
* Mocks never replace built endpoints; if the backend has it, call it.

### 7. Tooling

* `npm run lint` (ESLint flat config for TS + React hooks) and `npm run typecheck`. No test framework in F0; F1 adds Vitest for the reducer.
* Remove `syncUsualProducts` from `src/api/autonomy.ts` only when 2d lands (leave a TODO now).

## Not in this package

* Any screen content beyond the shell. The tabs render empty placeholders that F1–F4 replace.

## Done when

* `npm run dev` shows the shell with working tabs, live autonomy pill, composer, light and dark mode.
* A storybook-like page `#ui` (dev only) shows every primitive in every variant, so F1–F4 can look things up.
* `useSignedChange` works end to end against the backend: change the level on `#ui` → sign modal → new mandate version.
* `npm run build`, `lint`, `typecheck` pass.

## Built (14 Nov)

* `src/styles.css`: tokens + dark mode, `@theme inline` (`bg-must-soft`, `text-ink3`, `font-hand`, `shadow-card` …), component classes from the mockup in `@layer components`.
* `src/ui/`: `Icon`, `Button`, `Chip`/`KindTag`/`SignedTag`/`Cond`/`Tier`, `Card`, `Toggle`/`Segmented`/`Weight`/`YesNo`/`Typing`, `Modal`/`Drawer`, `ToastProvider`/`useToast`, `HashRow`/`CedarBlock`/`DiffList`, `Paper`/`Mark`, `eur()`. One barrel: `src/ui/index.ts`.
* `src/app/`: `useHashRoute` / `navigate` (`#order`, `#activity/<id>`, `#kitchen`, `#onboard`, `#ui`), `Header` (tabs, autonomy pill, kitchen name from `mandate.display_name`), `Composer` + `useComposer(config)` (each screen registers submit, hints, placeholder, camera target) + `useComposerText()` (prefill), `PhoneCameraProvider` (`open`, `upload`, `onPhoto(where, handler)`, `deliver`, `setOpener` for F3's modal; until then `open` = file picker), `SignProvider` + `useSignedChange` (`call` mode for 409 endpoints, `mandate` mode = preview + `PUT /mandate`), `queries.ts` (`useAutonomy`, `useKitchen`, `useMandate`, `keys`, `invalidateMandateViews`), placeholders per screen in `App.tsx`.
* `src/api/mock/`: `VITE_MOCK=1` → `getUsualOrder`, `getOrderStory`, `draftGrant`, and `getAutonomyState` adds `recommended_next` + `grants[].kind` + extra grant g2. New types in `src/api/types.ts` (`UsualOrder`, `OrderStory`, `AutonomyView`, `ExtraGrant`, `DraftGrantResponse`).
* `npm run lint`, `npm run typecheck`, `npm run build` pass. Checked in the browser against the current backend (scripted provider): `#ui` → Level 1 → sign modal with backend diff, Cedar and hash → mandate v2, pill updates. 375 px: no horizontal scroll.
* Note: the backend must be the current main (wave 1). An older backend answers 404 on `/kitchen` and `/autonomy`.
