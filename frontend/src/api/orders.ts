import { apiRequest } from "./client";
import type { AuditEntry, OrderSummary, Verification } from "./types";

export interface ListOrdersOptions {
  buyerId?: string;
  limit?: number;
  before?: string;
}

export function listOrders(options: ListOrdersOptions = {}): Promise<OrderSummary[]> {
  const query = new URLSearchParams();
  if (options.buyerId) query.set("buyer_id", options.buyerId);
  if (options.limit !== undefined) query.set("limit", String(options.limit));
  if (options.before) query.set("before", options.before);
  const suffix = query.size ? `?${query.toString()}` : "";
  return apiRequest<OrderSummary[]>(`/orders${suffix}`);
}

export function getOrderAudit(orderId: string): Promise<AuditEntry[]> {
  return apiRequest<AuditEntry[]>(`/orders/${encodeURIComponent(orderId)}/audit`);
}

export function verifyOrder(orderId: string): Promise<Verification> {
  return apiRequest<Verification>(
    `/orders/${encodeURIComponent(orderId)}/verify`,
  );
}
