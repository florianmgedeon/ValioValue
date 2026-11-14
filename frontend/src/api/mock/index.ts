/**
 * Fixtures for endpoints that are not built yet. Only active with VITE_MOCK=1. Each export names the package that
 * removes it; never mock an endpoint that exists. Shapes follow docs/work-packages/2d-*.md.
 */
import type { DraftGrantResponse, ExtraGrant } from "../types";

export const MOCK = import.meta.env.VITE_MOCK === "1";

const delay = <T,>(v: T, ms = 250) => new Promise<T>((r) => setTimeout(() => r(structuredClone(v)), ms));

/** Removed by 2d (`GET /autonomy` returns `grants[].kind`, `recommended_next`). */
export const extraGrants: ExtraGrant[] = [
  { id: "g2", kind: "extra", label: "Fruit and vegetables", summary: "category in fruit, vegetables · up to €150 an order · accepts +5 % pack rounding", enabled: true, source: "You, 2 Nov" },
];

/** Removed by 2d (`POST /mandate/draft` with `kind: "grant"`). */
export const grantDraft = (text: string): DraftGrantResponse => ({
  kind: "grant",
  grant: {
    label: "Organic products",
    scope: [{ attr: "labels", op: "contains", value: "EU-Bio" }],
    limits: { max_order_total: Number(/(\d+)/.exec(text)?.[1] ?? 200) },
    accepts: [],
    enabled: true,
  },
  explanation: "I may order organic products alone, up to this limit per order. Every must still applies.",
  coverage: { checked: 1431, matching: 212, known: 1431 },
  examples: ["Organic oat flakes 1 kg", "Organic carrots 10 kg", "Valio Luomu milk 1 L"],
});

export const mock = { delay };
