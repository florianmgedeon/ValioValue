import type { components } from "./generated";

export type Schema = components["schemas"];

export type ChatRequest = Schema["ChatRequest"];
export type ChatResponse = Schema["ChatResponse"];
export type OrderIntent = Schema["OrderIntent"];
export type Offer = Schema["Offer"];
export type Approval = Schema["Approval"];
export type ApprovalDecision = Schema["ApprovalDecision"];
export type AuditEntry = Schema["AuditEntry"];
export type Mandate = Schema["Mandate"];
export type KitchenProfile = Schema["KitchenProfile"];
export type UploadMeta = Schema["UploadMeta"];
export type Pairing = Schema["Pairing"];
export type OrderSummary = Schema["OrderSummary"];
export type AutonomyState = Schema["AutonomyState"];
export type Verification = Schema["Verification"];
export type RuleCoverage = Schema["RuleCoverage"];
export type Suggestion = Schema["Suggestion"];
export type IngestResult = Schema["IngestResult"];
export type IngestResponse = { buyer_id?: string; results?: IngestResult[] };

export type ChatStreamEvent =
  | Schema["UserMessageEvent"]
  | Schema["ReasoningEvent"]
  | Schema["MessageEvent"]
  | Schema["IntentProposedEvent"]
  | Schema["IntentConfirmedEvent"]
  | Schema["OrderRejectedEvent"]
  | Schema["ToolCallEvent"]
  | Schema["ToolResultEvent"]
  | Schema["OffersEvent"]
  | Schema["ApprovalRequiredEvent"]
  | Schema["OrderConfirmedEvent"]
  | Schema["ErrorEvent"]
  | Schema["DoneEvent"]
  | Schema["AutonomyProgressEvent"];

export type PairingStreamEvent = Schema["PairedEvent"] | Schema["PhotoReceivedEvent"];

export type ApprovalStatus = "pending" | "approved" | "rejected";

// ---- Shapes of endpoints that are planned but not in the contract yet (2c, 2d). Move to the contract when built.

/** GET /kitchen/usual-order (2c) */
export interface UsualOrder {
  items: { word: string; product_id: string; product_name: string; quantity: number; unit: string; last_unit_price: number | null; estimate: number | null }[];
  estimate_total: number | null;
  deliver_by: string;
  based_on: string;
}

/** GET /orders/{id}/story (2c) */
export type StoryStep =
  | { kind: "asked"; at: string; text?: string; attachments?: string[] }
  | { kind: "understood"; at: string; intent_id: string; intent_hash: string; signed_by: string; items: number }
  | { kind: "searched"; at: string; items: { item_id: string; word: string; funnel: number[]; picked: string; why: string }[] }
  | { kind: "decided"; at: string; lines: { item_id: string; outcome: "alone" | "you" | "blocked"; grant?: string; rule?: string; evidence?: string; by?: string; why?: string }[] }
  | { kind: "confirmed"; at: string; receipt?: Record<string, unknown>; verify_url: string }
  | { kind: "learned"; at: string; text: string };

export interface OrderStory {
  order_id: string;
  label: string;
  tier: "auto" | "ask" | "block";
  total: number;
  mandate: { version: number; grants_used: string[] };
  kitchen_profile_version?: number;
  steps: StoryStep[];
}

/** A buyer-made grant beside the level grant g1 (2d). */
export interface ExtraGrant {
  id: string;
  kind: "level" | "extra";
  label: string;
  summary: string;
  enabled: boolean;
  source?: string;
}

/** GET /autonomy as 2d defines it: the built AutonomyState plus `recommended_next` and grant kinds. */
export type AutonomyView = Omit<AutonomyState, "grants"> & {
  recommended_next?: boolean;
  grants?: (NonNullable<AutonomyState["grants"]>[number] & { kind?: "level" | "extra"; enabled?: boolean; source?: string })[];
};

/** POST /mandate/draft {kind: "grant"} (2d) */
export interface DraftGrantResponse {
  kind: "grant";
  grant: Schema["Grant"];
  explanation: string;
  coverage: { checked: number; matching: number; known: number } | null;
  examples: string[];
}
