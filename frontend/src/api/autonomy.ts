import { apiRequest } from "./client";
import { extraGrants, MOCK } from "./mock";
import type { AutonomyState, AutonomyView } from "./types";

export async function getAutonomyState(buyerId?: string): Promise<AutonomyView> {
  const query = buyerId ? `?buyer_id=${encodeURIComponent(buyerId)}` : "";
  const state = await apiRequest<AutonomyView>(`/autonomy${query}`);
  if (!MOCK || state.recommended_next !== undefined) return state;
  // Mock until 2d: new-style fields on top of the built endpoint.
  return {
    ...state,
    recommended_next: state.offer_next ?? false,
    grants: [...(state.grants ?? []).map((g) => ({ ...g, kind: "level" as const, enabled: true })), ...extraGrants],
  };
}

export interface SetAutonomyLevelRequest {
  buyer_id?: string;
  level: 0 | 1 | 2;
  max_order_total?: number;
  by?: string;
  mandate_hash?: string;
}

export function setAutonomyLevel(
  request: SetAutonomyLevelRequest,
): Promise<AutonomyState> {
  return apiRequest<AutonomyState>("/autonomy/level", {
    method: "POST",
    json: request,
  });
}

// TODO(2d): remove with /autonomy/sync-usual.
export function syncUsualProducts(
  request: { buyer_id?: string; by?: string; mandate_hash?: string } = {},
): Promise<AutonomyState> {
  return apiRequest<AutonomyState>("/autonomy/sync-usual", {
    method: "POST",
    json: request,
  });
}
