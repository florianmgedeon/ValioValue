/**
 * One view model for the order timeline. Built from `GET /orders/{id}/story` (2c) or, until 2c lands, from the
 * audit trail (`GET /orders/{id}/audit`) plus the order summary. Fields the source does not have stay empty;
 * nothing is invented.
 */
import type { AuditEntry, OrderStory, OrderSummary, Schema } from "../../api/types";

export type Outcome = "alone" | "you" | "blocked" | "info";

export interface SearchedLine {
  itemId?: string;
  name: string;
  /** e.g. "27 → 27 → 19 → 1" */
  funnel?: string;
  why?: string;
}

export interface DecidedLine {
  itemId?: string;
  name: string;
  outcome: Outcome;
  grant?: string;
  rule?: string;
  evidence?: string;
  by?: string;
  why?: string;
}

export interface StoryModel {
  orderId: string;
  /** true when built from the audit trail (no `why` sentences yet). */
  reduced: boolean;
  label?: string;
  tier?: "auto" | "ask" | "block";
  total?: number;
  createdAt?: string;
  title?: string;
  mandateVersion?: number;
  kitchenProfileVersion?: number;
  asked?: { text?: string; attachments: string[] };
  understood?: { intentId?: string; intentHash?: string; signedBy?: string; items?: number; text?: string };
  searched: SearchedLine[];
  decided: DecidedLine[];
  /** Plain audit lines for the "decided" step when the story is reduced (policy check, human approval). */
  decidedNotes: string[];
  confirmed?: { total?: number; at?: string };
  learned?: string;
}

type FunnelStep = Schema["FunnelStep"];

/** Funnel as `27 → 27 → 19 → 1`. Accepts plain counts or the `FunnelStep` objects from the offers event. */
export function funnelText(funnel: unknown): string | undefined {
  if (!Array.isArray(funnel) || !funnel.length) return undefined;
  const counts = funnel.map((f: number | FunnelStep) => (typeof f === "number" ? f : f?.count)).filter((n) => typeof n === "number");
  return counts.length ? counts.join(" → ") : undefined;
}

export function fromStory(s: OrderStory, summary?: OrderSummary): StoryModel {
  const m: StoryModel = {
    orderId: s.order_id,
    reduced: false,
    label: s.label,
    tier: s.tier,
    total: s.total,
    createdAt: summary?.created_at,
    title: summary?.title,
    mandateVersion: s.mandate?.version,
    kitchenProfileVersion: s.kitchen_profile_version,
    searched: [],
    decided: [],
    decidedNotes: [],
  };
  const names = new Map<string, string>();
  const words = new Map<string, string>();
  for (const step of s.steps) {
    switch (step.kind) {
      case "asked":
        m.asked = { text: step.text, attachments: step.attachments ?? [] };
        m.createdAt ??= step.at;
        break;
      case "understood":
        m.understood = { intentId: step.intent_id, intentHash: step.intent_hash, signedBy: step.signed_by, items: step.items };
        break;
      case "searched":
        for (const it of step.items) {
          const name = it.picked || it.word;
          names.set(it.item_id, name);
          words.set(it.item_id, it.word);
          m.searched.push({ itemId: it.item_id, name, funnel: funnelText(it.funnel), why: it.why });
        }
        break;
      case "decided":
        for (const l of step.lines) {
          m.decided.push({
            itemId: l.item_id,
            // A blocked line is about what was asked for ("granola"), not what was picked instead.
            name: (l.outcome === "blocked" ? words.get(l.item_id) : names.get(l.item_id)) ?? l.item_id,
            outcome: l.outcome,
            grant: l.grant,
            rule: l.rule,
            evidence: l.evidence,
            by: l.by,
            why: l.why,
          });
        }
        break;
      case "confirmed":
        m.confirmed = { total: s.total, at: step.at };
        break;
      case "learned":
        m.learned = step.text;
        break;
    }
  }
  return m;
}

const str = (v: unknown) => (typeof v === "string" && v ? v : undefined);
const num = (v: unknown) => (typeof v === "number" ? v : undefined);

/**
 * Reduced timeline from the audit trail (fallback until 2c). Uses the tool calls in order:
 * propose_intent, confirm_intent, search_products, check_offer, policy_check, approve/reject, order_confirmed.
 * TODO(2c): remove when `GET /orders/{id}/story` is built.
 */
export function fromAudit(orderId: string, audit: AuditEntry[], summary?: OrderSummary): StoryModel {
  const m: StoryModel = {
    orderId,
    reduced: true,
    label: summary?.label,
    tier: summary?.tier,
    total: summary?.total,
    createdAt: summary?.created_at,
    title: summary?.title,
    mandateVersion: summary?.mandate_version,
    searched: [],
    decided: [],
    decidedNotes: [],
  };
  const searchByItem = new Map<string, SearchedLine>();
  for (const e of audit) {
    const d = e.data ?? {};
    switch (e.action) {
      case "propose_intent": {
        // The newest proposal wins (a revised intent replaces the first one).
        const text = e.summary.replace(/^Proposed intent:\s*/, "");
        m.understood = { ...m.understood, intentId: str(d.intent_id), intentHash: str(d.intent_hash), text };
        break;
      }
      case "confirm_intent": {
        const signedBy = /^(.+?) confirmed and signed/.exec(e.summary)?.[1];
        m.understood = { ...m.understood, intentId: str(d.intent_id) ?? m.understood?.intentId, intentHash: str(d.intent_hash) ?? m.understood?.intentHash, signedBy };
        break;
      }
      case "search_products": {
        const query = /for '([^']+)'/.exec(e.summary)?.[1] ?? e.summary;
        const itemId = str(d.item_id);
        const line: SearchedLine = { itemId, name: query, why: e.summary.replace(/^Searched catalog for '[^']+':\s*/, "") };
        // Several searches for one item: keep the last one.
        if (itemId && searchByItem.has(itemId)) Object.assign(searchByItem.get(itemId)!, line);
        else {
          m.searched.push(line);
          if (itemId) searchByItem.set(itemId, line);
        }
        break;
      }
      case "policy_check":
      case "approve":
      case "reject":
      case "quote_expired":
        m.decidedNotes.push(e.summary);
        if (e.action === "approve") m.decided.push({ name: "Cart", outcome: "you", by: /^(.+?) approved/.exec(e.summary)?.[1] });
        break;
      case "order_confirmed":
        m.confirmed = { total: num(d.total_price) ?? summary?.total, at: e.timestamp };
        break;
    }
  }
  if (!m.confirmed && summary?.status === "confirmed") m.confirmed = { total: summary.total };
  return m;
}

/** "5 lines · 4 alone · 1 by you · 1 blocked" counts. Lines are unique items; outcomes are counted as listed. */
export function counts(m: StoryModel) {
  const ids = new Set(m.decided.map((l) => l.itemId).filter(Boolean));
  const lines = m.understood?.items ?? (ids.size || m.searched.length);
  const n = (o: Outcome) => m.decided.filter((l) => l.outcome === o).length;
  return { lines, alone: n("alone"), you: n("you"), blocked: n("blocked") };
}
