/**
 * The Order tab's core: chat stream events in, thread blocks and the live panel out.
 *
 * The stream replays everything after a reconnect and has no event ids. So every block has a stable key (intent id,
 * approval id, order id, or timestamp + text) and a repeated event replaces its block instead of adding one.
 * Live state only moves forward or is set from the latest event, so a replay ends in the same state.
 */
import type { Approval, ChatStreamEvent, OrderIntent, Schema } from "../../api/types";

type Diff = Schema["Diff"];
type DiffCheck = Diff["checks"] extends (infer C)[] | undefined ? C & { evidence?: Schema["RuleEvidence"] & { id?: string } } : never;
type Offer = Schema["Offer"];
type FunnelStep = Schema["FunnelStep"];
type Receipt = Schema["Receipt"];

/** 2a: the agent proposes something to remember. Not in the contract types yet. */
export interface ContextSuggestionEvent {
  type: "context_suggestion";
  session_id: string;
  timestamp: string;
  reason?: "after_order" | "from_chat" | "from_photo";
  suggestions?: Schema["Suggestion"][];
}

export type OrderEvent = ChatStreamEvent | ContextSuggestionEvent;

export interface Attachment {
  upload_id?: string;
  url?: string | null;
  media_type?: string | null;
  filename?: string | null;
}

export interface UsualExcluded {
  item_id: string;
  query?: string;
  product_id?: string;
  name?: string;
  reason?: string;
  rule_id?: string;
}

/** Why an item could not be ordered. "missing": the catalog has nothing for it. "rules": no product passes the
 *  musts. "delivery": products pass, none arrives in full on time. */
export interface ItemIssue {
  kind: "missing" | "rules" | "delivery";
  reason: string;
}

export type Block =
  | { kind: "me"; key: string; text: string }
  | { kind: "photo"; key: string; attachments: Attachment[]; text?: string }
  | { kind: "say"; key: string; text: string; /** Said after a receipt: the agent's recap of what the cards show. */ recap: boolean }
  | { kind: "readback"; key: string; intent: OrderIntent; signed: boolean }
  | {
      kind: "funnel";
      key: string;
      /** item_id -> that item's funnel steps */
      items: Record<string, FunnelStep[]>;
      /** item_id -> what the item was called */
      queries: Record<string, string>;
      /** Usual products that broke a rule, per item. Feed the decision card. */
      usualExcluded: UsualExcluded[];
      /** item_id -> why nothing can be ordered for it (missing = fine) */
      issues: Record<string, ItemIssue>;
      checking: boolean;
    }
  | { kind: "decision"; key: string; approval: Approval; checks: DiffCheck[]; decided?: "approve" | "reject" }
  | { kind: "approve"; key: string; approval: Approval; decided?: "approve" | "reject" }
  | { kind: "block"; key: string; orderId: string; checks: DiffCheck[]; message?: string }
  | {
      kind: "receipt";
      key: string;
      orderId: string;
      lines: Offer[];
      total: number;
      currency: string;
      deliveryDate?: string;
      tier?: "auto" | "ask";
      approvedBy?: string | null;
      diff?: Diff;
      receipt?: Receipt & { fingerprint?: string };
      /** Intent items still to be ordered */
      openItems: string[];
    }
  | { kind: "learn"; key: string; suggestion: Schema["Suggestion"]; reason?: string; done?: "remembered" | "once" }
  | { kind: "error"; key: string; message: string; recoverable: boolean };

export type Step = "asked" | "understood" | "searching" | "checking" | "done";
export const STEPS: Step[] = ["asked", "understood", "searching", "checking", "done"];
export type Now = Step | "wait" | "block";

export interface LiveState {
  step: Step | null;
  /** Overrides the step's status line: waiting for the buyer, blocked, done. */
  now: Now | null;
  /** Open decisions (approvals not decided yet). */
  waiting: number;
  total: number | null;
  lines: number | null;
  /** "estimate", "ordered alone", "above your limit", ... */
  note: string | null;
  deliverBy: string | null;
  mandateId: string | null;
  startedAt: string | null;
  /** Latest tool label, e.g. "Searching the Valio Aimo catalog for milk". */
  activity: string | null;
  /** The agent is thinking (reasoning seen, nothing said since). */
  typing: boolean;
  orderId: string | null;
}

export interface OrderState {
  blocks: Block[];
  live: LiveState;
  /** Last message the buyer sent, for "retry" after an error. */
  lastText: string | null;
}

export const initialState: OrderState = {
  blocks: [],
  live: {
    step: null, now: null, waiting: 0, total: null, lines: null, note: null, deliverBy: null, mandateId: null,
    startedAt: null, activity: null, typing: false, orderId: null,
  },
  lastText: null,
};

const FUNNEL_KEY = "funnel";

/** Insert or replace the block with the same key. Returns a new list. */
function upsert(blocks: Block[], block: Block): Block[] {
  const i = blocks.findIndex((b) => b.key === block.key);
  if (i < 0) return [...blocks, block];
  const next = blocks.slice();
  next[i] = block;
  return next;
}

function update<K extends Block["kind"]>(blocks: Block[], key: string, kind: K, f: (b: Extract<Block, { kind: K }>) => Block): Block[] {
  return blocks.map((b) => (b.key === key && b.kind === kind ? f(b as Extract<Block, { kind: K }>) : b));
}

const stepAfter = (cur: Step | null, next: Step): Step => (cur && STEPS.indexOf(cur) > STEPS.indexOf(next) ? cur : next);

/** Checks a human has to look at: failed, severity ask, about one line (not the whole-cart autonomy/budget check). */
export function lineDeviations(diff: Diff | undefined): DiffCheck[] {
  return ((diff?.checks ?? []) as DiffCheck[]).filter((c) => c.ok === false && c.severity === "ask" && !!c.item_id);
}

export function blockedChecks(diff: Diff | undefined): DiffCheck[] {
  return ((diff?.checks ?? []) as DiffCheck[]).filter((c) => c.ok === false && c.severity === "block");
}

function openApprovals(blocks: Block[]): number {
  return blocks.filter((b) => (b.kind === "decision" || b.kind === "approve") && !b.decided).length;
}

/** Hide the agent's restatement of the read-back card; the card says it better. */
function isReadbackEcho(text: string) {
  return /^Here is what I understood:/.test(text);
}

type OffersEvent = Extract<ChatStreamEvent, { type?: "offers" }>;
type OfferWithIssues = Offer & { can_fulfil?: boolean; fulfil_issues?: string[] | null };

const RULE_WORDS = /must|rule|allerg|contain|excluded|proving|unknown/i;

/** From one item's offers: why nothing can be ordered, or null if an offer can deliver. */
export function itemIssue(evt: OffersEvent): ItemIssue | null {
  const offers = (evt.offers ?? []) as OfferWithIssues[];
  if (offers.some((o) => o.can_fulfil !== false)) return null;
  const excluded = evt.excluded?.[0]?.reason;
  const found = evt.funnel?.find((f) => f.step === "catalog")?.count;
  if (!offers.length && found === 0 && !excluded) return { kind: "missing", reason: `Nothing in the Valio Aimo catalog matches "${evt.product_query ?? "this"}".` };
  if (!offers.length) return { kind: "rules", reason: excluded ?? "No product in the catalog passes your rules." };
  const issue = offers.flatMap((o) => o.fulfil_issues ?? []).find(Boolean);
  if (issue && RULE_WORDS.test(issue) && !/deliver|cut-?off|earliest|stock|available/i.test(issue)) return { kind: "rules", reason: excluded ?? issue };
  return { kind: "delivery", reason: issue ?? "Nothing arrives in full by your date." };
}

function withIssue(issues: Record<string, ItemIssue>, iid: string, issue: ItemIssue | null) {
  const rest = { ...issues };
  delete rest[iid];
  return issue ? { ...rest, [iid]: issue } : rest;
}

export function orderReducer(state: OrderState, evt: OrderEvent): OrderState {
  const { live } = state;
  let blocks = state.blocks;
  const startedAt = live.startedAt ?? evt.timestamp ?? null;

  switch (evt.type) {
    case "user_message": {
      // An answer to the read-back's questions is sent wrapped for the agent: show only what the buyer wrote.
      const raw = evt.text ?? "";
      const text = /^Answers to your questions about intent /.test(raw)
        ? [...new Set(raw.split("\n").slice(1).map((l) => l.split(" → ").slice(1).join(" → ").trim()).filter(Boolean))].join("\n") || raw
        : raw;
      const atts = (evt.attachments ?? []) as Attachment[];
      const key = `me:${evt.timestamp}:${text}`;
      blocks = upsert(blocks, atts.length ? { kind: "photo", key, attachments: atts, text: text || undefined } : { kind: "me", key, text });
      return {
        blocks,
        lastText: text || state.lastText,
        live: { ...live, startedAt, step: stepAfter(live.step, "asked"), now: live.now === "done" || live.now === "block" ? live.now : null, typing: true },
      };
    }

    case "reasoning":
      return { ...state, live: { ...live, startedAt, typing: true } };

    case "message": {
      const text = evt.text ?? "";
      if (!text || isReadbackEcho(text)) return { ...state, live: { ...live, typing: false } };
      const key = `say:${evt.timestamp}:${text}`;
      const prev = blocks.find((b) => b.key === key);
      // Keep the first answer on a replay: by then the receipt exists for every message.
      const recap = prev?.kind === "say" ? prev.recap : blocks.some((b) => b.kind === "receipt" || b.kind === "block");
      return { ...state, blocks: upsert(blocks, { kind: "say", key, text, recap }), live: { ...live, typing: false } };
    }

    case "tool_call": {
      const checking = evt.tool === "place_order";
      if (checking) blocks = update(blocks, FUNNEL_KEY, "funnel", (b) => ({ ...b, checking: true }));
      return {
        ...state,
        blocks,
        live: { ...live, startedAt, activity: evt.label ?? live.activity, step: checking ? stepAfter(live.step, "checking") : live.step },
      };
    }

    case "tool_result":
      return state;

    case "intent_proposed": {
      const it = evt.intent;
      if (!it) return state;
      const key = `intent:${it.intent_id}`;
      const prev = blocks.find((b) => b.key === key);
      const signed = (prev?.kind === "readback" && prev.signed) || it.status === "confirmed" || it.status === "fulfilled";
      blocks = upsert(blocks, { kind: "readback", key, intent: it, signed });
      return {
        ...state,
        blocks,
        live: {
          ...live, startedAt, typing: false,
          step: stepAfter(live.step, "understood"),
          now: signed || live.step === "done" ? live.now : "wait",
          deliverBy: it.deliver_by ?? live.deliverBy,
          lines: live.total === null ? it.items.length : live.lines,
          mandateId: it.mandate_id ?? live.mandateId,
        },
      };
    }

    case "intent_confirmed": {
      const it = evt.intent;
      if (!it) return state;
      const key = `intent:${it.intent_id}`;
      blocks = blocks.some((b) => b.key === key)
        ? update(blocks, key, "readback", (b) => ({ ...b, intent: { ...b.intent, ...it }, signed: true }))
        : upsert(blocks, { kind: "readback", key, intent: it, signed: true });
      return {
        ...state,
        blocks,
        live: {
          ...live, startedAt,
          step: stepAfter(live.step, "searching"),
          now: live.now === "wait" && openApprovals(blocks) === 0 ? null : live.now,
          deliverBy: it.deliver_by ?? live.deliverBy,
          mandateId: it.mandate_id ?? live.mandateId,
        },
      };
    }

    case "offers": {
      const iid = evt.item_id ?? "_";
      const prev = blocks.find((b) => b.key === FUNNEL_KEY);
      const base = prev?.kind === "funnel" ? prev : { kind: "funnel" as const, key: FUNNEL_KEY, items: {}, queries: {}, usualExcluded: [], issues: {}, checking: false };
      const usual = (evt.excluded ?? [])
        .filter((x) => x.usual)
        .map((x) => ({ item_id: iid, query: evt.product_query, product_id: x.product_id, name: x.name, reason: x.reason, rule_id: x.rule_id }));
      blocks = upsert(blocks, {
        ...base,
        items: { ...base.items, [iid]: evt.funnel ?? [] },
        queries: { ...base.queries, [iid]: evt.product_query ?? iid },
        usualExcluded: [...base.usualExcluded.filter((u) => u.item_id !== iid), ...usual],
        issues: withIssue(base.issues, iid, itemIssue(evt)),
      });
      return { ...state, blocks, live: { ...live, startedAt, step: stepAfter(live.step, "searching") } };
    }

    case "approval_required": {
      const a = evt.approval;
      if (!a) return state;
      const key = `approval:${a.approval_id}`;
      const prev = blocks.find((b) => b.key === key);
      const decided = prev && (prev.kind === "decision" || prev.kind === "approve") ? prev.decided : undefined;
      const fromServer = a.status === "approved" ? "approve" : a.status === "rejected" ? "reject" : undefined;
      const checks = lineDeviations(a.diff);
      blocks = upsert(
        blocks,
        checks.length ? { kind: "decision", key, approval: a, checks, decided: decided ?? fromServer } : { kind: "approve", key, approval: a, decided: decided ?? fromServer },
      );
      blocks = update(blocks, FUNNEL_KEY, "funnel", (b) => ({ ...b, checking: false }));
      const waiting = openApprovals(blocks);
      return {
        ...state,
        blocks,
        live: {
          ...live, startedAt, typing: false,
          step: stepAfter(live.step, "checking"),
          now: waiting ? "wait" : live.now,
          waiting,
          total: a.total_price,
          lines: a.lines.length,
          note: waiting ? (checks.length ? `${waiting} decision${waiting > 1 ? "s wait" : " waits"} for you` : "above your limit") : live.note,
          orderId: a.order_id,
        },
      };
    }

    case "order_rejected": {
      const orderId = evt.order_id ?? "?";
      blocks = upsert(blocks, { kind: "block", key: `block:${orderId}`, orderId, checks: blockedChecks(evt.diff), message: evt.message });
      blocks = update(blocks, FUNNEL_KEY, "funnel", (b) => ({ ...b, checking: false }));
      // A rejected approval also ends here.
      blocks = blocks.map((b) => ((b.kind === "decision" || b.kind === "approve") && b.approval.order_id === orderId && !b.decided ? { ...b, decided: "reject" as const } : b));
      return {
        ...state,
        blocks,
        live: { ...live, startedAt, typing: false, step: stepAfter(live.step, "checking"), now: "block", waiting: openApprovals(blocks), orderId },
      };
    }

    case "order_confirmed": {
      const orderId = evt.order_id ?? "?";
      blocks = upsert(blocks, {
        kind: "receipt",
        key: `receipt:${orderId}`,
        orderId,
        lines: evt.lines ?? [],
        total: evt.total_price ?? 0,
        currency: evt.currency ?? "EUR",
        deliveryDate: evt.delivery_date,
        tier: evt.tier,
        approvedBy: evt.approved_by,
        diff: evt.diff,
        receipt: { ...evt.receipt, fingerprint: evt.receipt?.fingerprint ?? (evt as { fingerprint?: string }).fingerprint },
        openItems: evt.items_open_after ?? [],
      });
      blocks = update(blocks, FUNNEL_KEY, "funnel", (b) => ({ ...b, checking: false }));
      // The approval for this order was decided (by this screen or another one).
      blocks = blocks.map((b) => ((b.kind === "decision" || b.kind === "approve") && b.approval.order_id === orderId && !b.decided ? { ...b, decided: "approve" as const } : b));
      return {
        ...state,
        blocks,
        live: {
          ...live, startedAt, typing: false,
          step: "done",
          now: "done",
          waiting: openApprovals(blocks),
          total: evt.total_price ?? live.total,
          lines: evt.lines?.length ?? live.lines,
          note: evt.tier === "auto" ? "ordered alone" : "confirmed after your OK",
          deliverBy: evt.delivery_date ?? live.deliverBy,
          orderId,
        },
      };
    }

    case "context_suggestion": {
      for (const s of evt.suggestions ?? []) {
        const key = `learn:${s.id}`;
        const prev = blocks.find((b) => b.key === key);
        blocks = upsert(blocks, { kind: "learn", key, suggestion: s, reason: evt.reason, done: prev?.kind === "learn" ? prev.done : undefined });
      }
      return { ...state, blocks };
    }

    case "error":
      return {
        ...state,
        blocks: upsert(blocks, { kind: "error", key: `error:${evt.timestamp}:${evt.message}`, message: evt.message ?? "Something went wrong.", recoverable: evt.recoverable ?? true }),
        live: { ...live, typing: false },
      };

    case "done":
      return { ...state, live: { ...live, typing: false, activity: null } };

    case "autonomy_progress":
      return state;

    default:
      return state;
  }
}

/** Local actions from this screen (the buyer clicked). The stream confirms them later; these only avoid a flicker. */
export type LocalAction =
  | { type: "local/signed"; intentId: string }
  | { type: "local/decided"; approvalId: string; decision: "approve" | "reject" }
  | { type: "local/learned"; suggestionId: string; done: "remembered" | "once" }
  | { type: "local/reset" };

export function reduce(state: OrderState, action: OrderEvent | LocalAction): OrderState {
  switch (action.type) {
    case "local/reset":
      return initialState;
    case "local/signed":
      return { ...state, blocks: update(state.blocks, `intent:${action.intentId}`, "readback", (b) => ({ ...b, signed: true })) };
    case "local/decided": {
      const blocks = state.blocks.map((b) =>
        (b.kind === "decision" || b.kind === "approve") && b.approval.approval_id === action.approvalId ? { ...b, decided: action.decision } : b,
      );
      const waiting = openApprovals(blocks);
      return { ...state, blocks, live: { ...state.live, waiting, now: waiting ? "wait" : state.live.now === "wait" ? null : state.live.now } };
    }
    case "local/learned":
      return { ...state, blocks: update(state.blocks, `learn:${action.suggestionId}`, "learn", (b) => ({ ...b, done: action.done })) };
    default:
      return orderReducer(state, action);
  }
}

/** Fold a whole recorded stream. */
export function fold(events: OrderEvent[], from: OrderState = initialState): OrderState {
  return events.reduce(orderReducer, from);
}

// ---- Funnel: one card for all items

export interface FunnelRow {
  key: string;
  label: string;
  why?: string;
  count: number;
  kind?: "must" | "prefer" | "usual";
}

const STEP_ORDER: Record<string, number> = { catalog: 0, must: 1, rule: 1, available: 2, usual: 3, prefer: 4, pick: 5 };

/** Merge every item's funnel: sum the counts per step (per must), keep the first label seen. */
export function mergeFunnel(items: Record<string, FunnelStep[]>): FunnelRow[] {
  const rows = new Map<string, FunnelRow & { order: number; seen: number }>();
  let seen = 0;
  const n = Object.keys(items).length;
  for (const steps of Object.values(items)) {
    for (const s of steps) {
      const step = s.step ?? "rule";
      const key = step === "must" || step === "rule" || step === "prefer" || step === "usual" ? `${step}:${s.rule_id ?? s.label}` : step;
      const row = rows.get(key);
      if (row) {
        row.count += s.count ?? 0;
        continue;
      }
      const kind = step === "must" || step === "rule" ? "must" : step === "prefer" ? "prefer" : step === "usual" ? "usual" : undefined;
      const label =
        step === "catalog" ? "Looked at in the catalog"
        : step === "must" || step === "rule" ? `Your must: ${s.label ?? s.rule_id}`
        : step === "usual" ? (s.label ?? "Your usual product first")
        : step === "pick" ? "Picked"
        : s.label ?? step;
      const order = step === "usual" && /^Only your usual/i.test(s.label ?? "") ? 0.5 : (STEP_ORDER[step] ?? 2);
      rows.set(key, { key, label, why: step === "catalog" ? `${n} line${n === 1 ? "" : "s"}` : undefined, count: s.count ?? 0, kind, order, seen: seen++ });
    }
  }
  return [...rows.values()].sort((a, b) => a.order - b.order || a.seen - b.seen).map(({ order: _o, seen: _s, ...r }) => r);
}

/** "mandate_school-kitchen-espoo_v3" -> 3 */
export function mandateVersion(mandateId: string | null | undefined): number | null {
  const m = /_v(\d+)$/.exec(mandateId ?? "");
  return m ? Number(m[1]) : null;
}
