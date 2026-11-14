import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { ApiError } from "../../api/client";
import { MOCK } from "../../api/mock";
import { getOrderAudit, getOrderStory, listOrders, verifyOrder } from "../../api/orders";
import type { AuditEntry, OrderStory } from "../../api/types";
import { keys } from "../../app/queries";

export const PAGE = 20;

/** `GET /orders`, newest first, paged with `before=<last order_id>`. Lives under `keys.orders`, so F1 refreshes it. */
export function useOrderList() {
  return useInfiniteQuery({
    queryKey: [...keys.orders, "list"],
    queryFn: ({ pageParam }) => listOrders({ limit: PAGE, before: pageParam }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => (last.length < PAGE ? undefined : last[last.length - 1]?.order_id),
  });
}

export type StorySource = { story: OrderStory } | { audit: AuditEntry[] };

/**
 * The story of one order. Until 2c builds `/story`, a 404 falls back to the audit trail (mocks off only).
 * TODO(2c): drop the fallback.
 */
export function useOrderStory(orderId: string | undefined) {
  return useQuery({
    queryKey: [...keys.orders, orderId, "story"],
    enabled: !!orderId,
    queryFn: async (): Promise<StorySource> => {
      try {
        return { story: await getOrderStory(orderId!) };
      } catch (e) {
        if (MOCK || !(e instanceof ApiError) || (e.status !== 404 && e.status !== 405)) throw e;
        return { audit: await getOrderAudit(orderId!) };
      }
    },
  });
}

export function useVerify(orderId: string, enabled: boolean) {
  return useQuery({
    queryKey: [...keys.orders, orderId, "verify"],
    enabled,
    queryFn: () => verifyOrder(orderId),
    staleTime: 0,
  });
}
