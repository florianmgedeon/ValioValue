import { apiRequest } from "./client";
import type { OrderIntent } from "./types";

export interface ConfirmIntentRequest {
  intent_hash: string;
  confirmed_by?: string;
  edits?: Record<string, unknown>;
}

export function confirmIntent(
  intentId: string,
  request: ConfirmIntentRequest,
): Promise<OrderIntent> {
  return apiRequest<OrderIntent>(
    `/intents/${encodeURIComponent(intentId)}/confirm`,
    { method: "POST", json: request },
  );
}
