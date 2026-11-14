# ValioValue frontend

React + TypeScript + Vite + Tailwind client for the buyer-side FastAPI service.
The browser talks only to the backend API defined in `../contracts/backend-api.yaml`.

## Local development

```bash
nvm use
cp .env.example .env
npm install
npm run api:types
npm run dev
```

Start the backend/mock separately. Vite serves the UI on `http://localhost:5173`
and proxies `/api/*` to `VITE_BACKEND_URL` (default `http://localhost:8000`).
The browser-side API base defaults to `/api`, so the development proxy avoids
cross-origin requests. In production, route `/api` to the buyer backend or set
`VITE_API_BASE_URL` to the backend origin when building.
Use Node.js 22.16.0 (also specified in `.nvmrc`); the current Vite toolchain
requires Node.js 20.19+ or 22.12+.

## Contract types

`src/api/generated.d.ts` is generated from the shared OpenAPI file:

```bash
npm run api:types
```

Commit generated type changes together with the corresponding contract changes.
REST wrappers live under `src/api/`; the chat and camera pairing SSE streams are
handled there as well. The UI should consume those wrappers rather than calling
`fetch` or `EventSource` directly.

## Structure

```
src/api/        wrappers per backend area (+ mock/ for endpoints not built yet)
src/ui/         shared primitives, import from "../../ui"
src/app/        shell: routing, header, composer, signing, camera, queries
src/features/   one folder per package (F1 order, F2 kitchen, F3 onboarding + camera, F4 activity)
```

* `#ui` (dev only) shows every primitive and a live signing round trip.
* `VITE_MOCK=1` answers endpoints that are not built yet (2a, 2c, 2d) from `src/api/mock/`.
* Screens register the composer with `useComposer({ onSubmit, hints, placeholder, camera })`.
* Every mandate change goes through `useSignedChange()` (see `src/app/signing.tsx`).
* `npm run lint` and `npm run typecheck` before every commit.

Packages and rules: `docs/work-packages/F0-*.md` to `F4-*.md` and the frontend section of `docs/work-packages/README.md`.
