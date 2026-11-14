/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string;
  /** "1": endpoints not built yet (2a, 2c, 2d) return fixtures from src/api/mock. */
  readonly VITE_MOCK?: string;
  /** Name on "Sign as …". */
  readonly VITE_SIGNER_NAME?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
