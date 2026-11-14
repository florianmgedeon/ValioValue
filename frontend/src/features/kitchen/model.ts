/**
 * Pending changes to the signed mandate. They collect in the change bar and are signed together as one
 * `PUT /mandate`. The autonomy level is separate: the backend builds the level grant (`POST /autonomy/level`).
 * Changes are kept as operations and applied to the freshest mandate at sign time.
 */
import type { Mandate, Schema } from "../../api/types";
import type { DiffKind } from "../../ui";

export type HardRule = Schema["HardRule"];
export type Preference = Schema["Preference"];
export type Grant = Schema["Grant"];
export type Condition = Schema["Condition"];
export type RuleGroup = "must" | "prefer";

export type Op =
  | { key: string; t: "patchRule"; group: RuleGroup; id: string; patch: Partial<HardRule> & Partial<Preference>; text: string; kind: DiffKind }
  | { key: string; t: "addRule"; group: RuleGroup; rule: HardRule | Preference; text: string; kind: "add" }
  | { key: string; t: "patchGrant"; id: string; patch: Partial<Grant>; text: string; kind: DiffKind }
  | { key: string; t: "addGrant"; grant: Grant; text: string; kind: "add" };

/** A pending level or limit change. Signed on its own after the batched mandate. */
export interface LevelChange {
  level: 0 | 1 | 2;
  max_order_total?: number;
  text: string;
  kind: DiffKind;
}

export interface EditableMandate {
  display_name?: string;
  currency?: string;
  optimise?: Mandate["optimise"];
  mandatory_rules: HardRule[];
  preferences: Preference[];
  autonomy: { grants: Grant[] };
}

/** The fields `PUT /mandate` takes, copied so edits never touch the query cache. */
export function editable(m: Mandate): EditableMandate {
  const copy = structuredClone(m);
  return {
    display_name: copy.display_name,
    currency: copy.currency,
    optimise: copy.optimise,
    mandatory_rules: copy.mandatory_rules ?? [],
    preferences: copy.preferences ?? [],
    autonomy: { grants: copy.autonomy?.grants ?? [] },
  };
}

export function applyOps(m: Mandate, ops: Op[]): EditableMandate {
  const out = editable(m);
  const list = (g: RuleGroup) => (g === "must" ? out.mandatory_rules : out.preferences) as (HardRule | Preference)[];
  for (const op of ops) {
    if (op.t === "addRule") list(op.group).push(structuredClone(op.rule));
    if (op.t === "addGrant") out.autonomy.grants.push(structuredClone(op.grant));
  }
  for (const op of ops) {
    if (op.t === "patchRule") {
      const r = list(op.group).find((x) => x.id === op.id);
      if (r) Object.assign(r, op.patch);
    }
    if (op.t === "patchGrant") {
      const g = out.autonomy.grants.find((x) => x.id === op.id);
      if (g) Object.assign(g, op.patch);
    }
  }
  return out;
}

/** Add or replace an op by key. `same` = the change undoes itself (back to the signed value): drop it. */
export function upsert(ops: Op[], op: Op, same = false): Op[] {
  const rest = ops.filter((o) => o.key !== op.key);
  return same ? rest : [...rest, op];
}

export function nextId(prefix: string, ids: (string | undefined)[]): string {
  const n = ids.reduce((max, id) => {
    const m = id && new RegExp(`^${prefix}(\\d+)$`).exec(id);
    return m ? Math.max(max, Number(m[1])) : max;
  }, 0);
  return `${prefix}${n + 1}`;
}

const show = (v: unknown): string => (Array.isArray(v) ? v.join(", ") : typeof v === "string" ? v : JSON.stringify(v));

/** `allergens not_contains peanuts` · applies to category in A, B */
export function condText(c: { attr?: string; op?: string; value?: unknown; applies_to?: Condition[] }): string {
  const base = `${c.attr ?? "?"} ${c.op ?? "?"} ${show(c.value)}`;
  const scope = c.applies_to?.length ? ` · applies to ${c.applies_to.map((a) => `${a.attr} ${a.op} ${show(a.value)}`).join(", ")}` : "";
  return base + scope;
}

export function scopeText(scope: Condition[] | undefined): string {
  if (!scope?.length) return "any product that passes every must";
  return scope.map((c) => (c.attr === "product_id" && Array.isArray(c.value) ? `${c.value.length} usual products` : condText(c))).join(" · ");
}

export function limitText(limits: Grant["limits"]): string {
  if (!limits) return "no limit";
  const parts = [];
  if (limits.max_order_total != null) parts.push(`up to €${limits.max_order_total.toLocaleString("en-GB")} an order`);
  if (limits.max_line_total != null) parts.push(`up to €${limits.max_line_total.toLocaleString("en-GB")} a line`);
  if (limits.max_unit_price != null) parts.push(`up to €${limits.max_unit_price.toLocaleString("en-GB")} per unit`);
  return parts.join(" · ") || "no limit";
}

export function acceptsText(accepts: Grant["accepts"]): string {
  if (!accepts?.length) return "no deviations";
  const parts = accepts.map((a) => {
    switch (a.check) {
      case "quantity": return `+${a.max_over_pct ?? 5} % pack rounding`;
      case "delivery_date": return `${a.max_days_late ?? 1} day${(a.max_days_late ?? 1) === 1 ? "" : "s"} late`;
      case "preference": return "a missed preference";
      default: return a.check ?? "?";
    }
  });
  return `accepts ${parts.join(", ")}`;
}

export function ruleLabel(r: { label?: string; attr?: string; op?: string; value?: unknown }): string {
  return r.label || condText(r);
}

export function plural(n: number, one: string, many = `${one}s`) {
  return `${n.toLocaleString("en-GB")} ${n === 1 ? one : many}`;
}
