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
