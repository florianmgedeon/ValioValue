import { apiRequest } from "./client";
import { grantDraft, mock, MOCK } from "./mock";
import type { DraftGrantResponse, Mandate, RuleCoverage } from "./types";

export interface MandateUpdate {
  mandate: Record<string, unknown>;
  buyer_id?: string;
  updated_by?: string;
  mandate_hash?: string;
}

export function getMandate(buyerId?: string): Promise<Mandate> {
  const query = buyerId ? `?buyer_id=${encodeURIComponent(buyerId)}` : "";
  return apiRequest<Mandate>(`/mandate${query}`);
}

export function previewMandate(update: MandateUpdate): Promise<Mandate> {
  return apiRequest<Mandate>("/mandate/preview", {
    method: "POST",
    json: update,
  });
}

export function saveMandate(update: MandateUpdate): Promise<Mandate> {
  return apiRequest<Mandate>("/mandate", { method: "PUT", json: update });
}

export function getMandateHistory(buyerId?: string): Promise<Mandate[]> {
  const query = buyerId ? `?buyer_id=${encodeURIComponent(buyerId)}` : "";
  return apiRequest<Mandate[]>(`/mandate/history${query}`);
}

export function getPolicyCatalog(): Promise<Record<string, unknown>> {
  return apiRequest<Record<string, unknown>>("/policy-catalog");
}

export function getCompiledMandate(buyerId?: string): Promise<Record<string, unknown>> {
  const query = buyerId ? `?buyer_id=${encodeURIComponent(buyerId)}` : "";
  return apiRequest<Record<string, unknown>>(`/mandate/cedar${query}`);
}

export interface DraftMandateRuleRequest {
  text: string;
  kind?: "must" | "prefer";
  buyer_id?: string;
}

export function draftMandateRule(
  request: DraftMandateRuleRequest,
): Promise<Record<string, unknown>> {
  return apiRequest<Record<string, unknown>>("/mandate/draft", {
    method: "POST",
    json: request,
  });
}

export function checkMandateCoverage(
  request: Record<string, unknown>,
): Promise<RuleCoverage> {
  return apiRequest<RuleCoverage>("/mandate/coverage", {
    method: "POST",
    json: request,
  });
}

/** POST /mandate/draft {kind: "grant"} (2d): a sentence -> an extra permission. Mocked with VITE_MOCK=1 until 2d. */
export function draftGrant(text: string, buyerId?: string): Promise<DraftGrantResponse> {
  if (MOCK) return mock.delay(grantDraft(text), 500);
  return apiRequest<DraftGrantResponse>("/mandate/draft", { method: "POST", json: { text, kind: "grant", buyer_id: buyerId } });
}
