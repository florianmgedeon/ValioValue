import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { getOrderStory, listOrders, verifyOrder } from "../../api/orders";
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

/** The story of one order: `GET /orders/{id}/story` (2c). */
export function useOrderStory(orderId: string | undefined) {
  return useQuery({
    queryKey: [...keys.orders, orderId, "story"],
    enabled: !!orderId,
    queryFn: () => getOrderStory(orderId!),
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
