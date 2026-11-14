import { apiRequest } from "./client";
import type { Mandate, RuleCoverage } from "./types";

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
