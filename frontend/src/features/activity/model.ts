/** One view model for the order timeline, built from `GET /orders/{id}/story` (2c). Nothing is invented. */
import type { OrderStory, OrderSummary } from "../../api/types";

export type Outcome = "alone" | "you" | "blocked" | "info";

export interface SearchedLine {
  itemId?: string;
  name: string;
  /** How many catalog products matched before one was picked. */
  options?: number;
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
  confirmed?: { total?: number; at?: string };
  learned?: string;
}

export function fromStory(s: OrderStory, summary?: OrderSummary): StoryModel {
  const m: StoryModel = {
    orderId: s.order_id,
    label: s.label,
    tier: s.tier,
    total: s.total,
    createdAt: summary?.created_at,
    title: summary?.title,
    mandateVersion: s.mandate?.version,
    kitchenProfileVersion: s.kitchen_profile_version,
    searched: [],
    decided: [],
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
          m.searched.push({ itemId: it.item_id, name, options: typeof it.funnel?.[0] === "number" ? it.funnel[0] : undefined, why: it.why });
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

/** "5 lines · 4 alone · 1 by you · 1 blocked" counts. Lines are unique items; outcomes are counted as listed. */
export function counts(m: StoryModel) {
  const ids = new Set(m.decided.map((l) => l.itemId).filter(Boolean));
  const lines = m.understood?.items ?? (ids.size || m.searched.length);
  const n = (o: Outcome) => m.decided.filter((l) => l.outcome === o).length;
  return { lines, alone: n("alone"), you: n("you"), blocked: n("blocked") };
}
