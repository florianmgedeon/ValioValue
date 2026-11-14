import { apiRequest, apiUrl } from "./client";
import type { IngestResponse, KitchenProfile, Pairing, PairingStreamEvent, UploadMeta, UsualOrder } from "./types";

export function getKitchenProfile(buyerId?: string): Promise<KitchenProfile> {
  const query = buyerId ? `?buyer_id=${encodeURIComponent(buyerId)}` : "";
  return apiRequest<KitchenProfile>(`/kitchen${query}`);
}

export function patchKitchenProfile(
  patch: Record<string, unknown>,
): Promise<KitchenProfile> {
  return apiRequest<KitchenProfile>("/kitchen", { method: "PATCH", json: patch });
}

export function getKitchenHistory(
  buyerId?: string,
): Promise<Record<string, unknown>> {
  const query = buyerId ? `?buyer_id=${encodeURIComponent(buyerId)}` : "";
  return apiRequest<Record<string, unknown>>(`/kitchen/history${query}`);
}

export function ingestKitchenDocuments(
  request: { buyer_id?: string; upload_ids?: string[]; text?: string; title?: string; by?: string },
): Promise<IngestResponse> {
  return apiRequest<IngestResponse>("/kitchen/ingest", {
    method: "POST",
    json: request,
  });
}

export function acceptKitchenSuggestions(
  request: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  return apiRequest<Record<string, unknown>>("/kitchen/suggestions/accept", {
    method: "POST",
    json: request,
  });
}

export function rejectKitchenSuggestion(
  suggestionId: string,
  request: Record<string, unknown> = {},
): Promise<KitchenProfile> {
  return apiRequest<KitchenProfile>(
    `/kitchen/suggestions/${encodeURIComponent(suggestionId)}/reject`,
    { method: "POST", json: request },
  );
}

export function uploadFile(
  file: File,
  metadata: { buyerId?: string; pairId?: string; source?: "upload" | "phone" } = {},
): Promise<UploadMeta> {
  const form = new FormData();
  form.set("file", file);
  if (metadata.buyerId) form.set("buyer_id", metadata.buyerId);
  if (metadata.pairId) form.set("pair_id", metadata.pairId);
  if (metadata.source) form.set("source", metadata.source);
  return apiRequest<UploadMeta>("/uploads", { method: "POST", body: form });
}

export function getUploadMetadata(uploadId: string): Promise<UploadMeta> {
  return apiRequest<UploadMeta>(
    `/uploads/${encodeURIComponent(uploadId)}/meta`,
  );
}

export function createPairing(
  buyerId?: string,
  purpose: "order" | "kitchen" = "order",
): Promise<Pairing> {
  return apiRequest<Pairing>("/pairings", {
    method: "POST",
    json: { buyer_id: buyerId, purpose },
  });
}

/** The desktop closed the camera dialog: the phone gets "scan the QR code again" on its next upload. */
export function endPairing(pairId: string): Promise<unknown> {
  return apiRequest(`/pairings/${encodeURIComponent(pairId)}`, { method: "DELETE" });
}

const pairingEventTypes = ["paired", "photo_received"] as const;

interface PairingExpiredEvent {
  type: "expired";
  pair_id: string;
}

export function subscribeToPairingStream(
  pairId: string,
  onEvent: (event: PairingStreamEvent | PairingExpiredEvent) => void,
  onError?: (event: Event) => void,
): () => void {
  const source = new EventSource(
    apiUrl(`/pairings/${encodeURIComponent(pairId)}/stream`),
  );
  for (const type of pairingEventTypes) {
    source.addEventListener(type, (event) => {
      onEvent(JSON.parse((event as MessageEvent<string>).data) as PairingStreamEvent);
    });
  }
  source.addEventListener("expired", (event) => {
    onEvent(JSON.parse((event as MessageEvent<string>).data) as PairingExpiredEvent);
    source.close();
  });
  source.onerror = (event) => onError?.(event);
  return () => source.close();
}

/** GET /kitchen/usual-order (2c): usual products, usual amounts, last price paid. */
export function getUsualOrder(buyerId?: string): Promise<UsualOrder> {
  const query = buyerId ? `?buyer_id=${encodeURIComponent(buyerId)}` : "";
  return apiRequest<UsualOrder>(`/kitchen/usual-order${query}`);
}
