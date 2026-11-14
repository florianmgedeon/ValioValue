import { apiRequest } from "./client";
import type { AutonomyState } from "./types";

export function getAutonomyState(buyerId?: string): Promise<AutonomyState> {
  const query = buyerId ? `?buyer_id=${encodeURIComponent(buyerId)}` : "";
  return apiRequest<AutonomyState>(`/autonomy${query}`);
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

export function syncUsualProducts(
  request: { buyer_id?: string; by?: string; mandate_hash?: string } = {},
): Promise<AutonomyState> {
  return apiRequest<AutonomyState>("/autonomy/sync-usual", {
    method: "POST",
    json: request,
  });
}
