import { apiRequest } from "./client";
import { mock, MOCK, orderStory } from "./mock";
import type { AuditEntry, OrderStory, OrderSummary, Verification } from "./types";

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

/** GET /orders/{id}/story (2c). With VITE_MOCK=1 it answers from a fixture. */
export function getOrderStory(orderId: string): Promise<OrderStory> {
  if (MOCK) return mock.delay(orderStory(orderId));
  return apiRequest<OrderStory>(`/orders/${encodeURIComponent(orderId)}/story`);
}
