/**
 * Fixtures for endpoints that are not built yet. Only active with VITE_MOCK=1. Each export names the package that
 * removes it; never mock an endpoint that exists. Shapes follow docs/work-packages/2c-*.md and 2d-*.md.
 */
import type { DraftGrantResponse, ExtraGrant, OrderStory, UsualOrder } from "../types";

export const MOCK = import.meta.env.VITE_MOCK === "1";

const delay = <T,>(v: T, ms = 250) => new Promise<T>((r) => setTimeout(() => r(structuredClone(v)), ms));

/** Removed by 2c (`GET /kitchen/usual-order`). */
export const usualOrder: UsualOrder = {
  items: [
    { word: "milk", product_id: "463038", product_name: "Valio Eila® semi-skimmed milk drink 10 L Novobox", quantity: 120, unit: "L", last_unit_price: 1.09, estimate: 130.8 },
    { word: "butter", product_id: "400310", product_name: "Valio unsalted butter 500 g", quantity: 10, unit: "kg", last_unit_price: 9.2, estimate: 92.0 },
    { word: "rye rolls", product_id: "611202", product_name: "Ruispala rye rolls 60 g, 12 pcs", quantity: 300, unit: "pcs", last_unit_price: 0.42, estimate: 126.0 },
    { word: "oat drink", product_id: "520114", product_name: "Oat drink 1 L", quantity: 35, unit: "L", last_unit_price: 1.89, estimate: 66.15 },
    { word: "granola", product_id: "702551", product_name: "Crunchy granola 2.5 kg", quantity: 15, unit: "kg", last_unit_price: 5.36, estimate: 80.4 },
    { word: "potatoes", product_id: "130905", product_name: "Firm potatoes, Finnish, 10 kg", quantity: 80, unit: "kg", last_unit_price: 0.95, estimate: 76.0 },
  ],
  estimate_total: 571.35,
  deliver_by: "2026-11-16",
  based_on: "your usual amounts per week",
};

/** Removed by 2c (`GET /orders/{id}/story`). */
export const orderStory = (orderId: string): OrderStory => ({
  order_id: orderId, label: "You decided 1", tier: "ask", total: 576.15,
  mandate: { version: 3, grants_used: ["g1"] }, kitchen_profile_version: 7,
  steps: [
    { kind: "asked", at: "2026-11-14T07:02:00Z", text: "photo of the order list", attachments: ["up_demo"] },
    { kind: "understood", at: "2026-11-14T07:02:20Z", intent_id: "int_demo", intent_hash: "3fa9e1c07b52d84e", signed_by: "Anna Virtanen", items: 5 },
    { kind: "searched", at: "2026-11-14T07:02:40Z", items: [
      { item_id: "i1", word: "milk", funnel: [27, 27, 19, 1], picked: "Valio Eila® semi-skimmed 10 L × 12", why: "Your usual. Passed 3 musts. Finnish ✓." },
      { item_id: "i5", word: "granola", funnel: [46, 9, 7, 1], picked: "Nut-free oat muesli 2.5 kg × 6", why: "Your usual granola now may contain tree nuts." },
    ] },
    { kind: "decided", at: "2026-11-14T07:02:55Z", lines: [
      { item_id: "i1", outcome: "alone", grant: "g1", why: "No deviation, inside €600 (g1)" },
      { item_id: "i5", outcome: "blocked", rule: "m1", evidence: "Supplier label 9 Nov: MAY_CONTAIN tree nuts (GS1)" },
      { item_id: "i5", outcome: "you", by: "Anna Virtanen", why: "Replaced by Nut-free oat muesli (+€4.80)" },
    ] },
    { kind: "confirmed", at: "2026-11-14T07:03:10Z", verify_url: `/orders/${orderId}/verify` },
    { kind: "learned", at: "2026-11-14T07:03:30Z", text: '"granola" now means Nut-free oat muesli (kitchen profile v8)' },
  ],
});

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
