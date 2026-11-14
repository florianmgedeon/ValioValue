import { QueryClient, useQuery } from "@tanstack/react-query";
import { getAutonomyState } from "../api/autonomy";
import { getKitchenProfile } from "../api/kitchen";
import { getMandate } from "../api/mandate";

export const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 15_000, retry: 1, refetchOnWindowFocus: false } },
});

/** Query keys shared by all features. Invalidate these after anything that changes the mandate or the profile. */
export const keys = {
  autonomy: ["autonomy"] as const,
  kitchen: ["kitchen"] as const,
  mandate: ["mandate"] as const,
  orders: ["orders"] as const,
};

export const useAutonomy = () => useQuery({ queryKey: keys.autonomy, queryFn: () => getAutonomyState() });
export const useKitchen = () => useQuery({ queryKey: keys.kitchen, queryFn: () => getKitchenProfile() });
export const useMandate = () => useQuery({ queryKey: keys.mandate, queryFn: () => getMandate() });

/** After a signed change: everything derived from the mandate or the profile may be stale. */
export function invalidateMandateViews() {
  return Promise.all([
    queryClient.invalidateQueries({ queryKey: keys.autonomy }),
    queryClient.invalidateQueries({ queryKey: keys.kitchen }),
    queryClient.invalidateQueries({ queryKey: keys.mandate }),
  ]);
}

/** Name of the person who signs (shown on "Sign as …"). The backend records `by`. */
export const SIGNER_NAME = import.meta.env.VITE_SIGNER_NAME || "Anna Virtanen";
