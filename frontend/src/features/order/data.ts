import { useQuery } from "@tanstack/react-query";
import { getUsualOrder } from "../../api/kitchen";
import { listOrders } from "../../api/orders";
import type { Mandate } from "../../api/types";
import { keys, useAutonomy, useKitchen, useMandate } from "../../app/queries";

export const useRecentOrders = () => useQuery({ queryKey: [...keys.orders, "recent"], queryFn: () => listOrders({ limit: 3 }) });

/** GET /kitchen/usual-order (2c). Not retried: if it fails, the card falls back to the profile. */
export const useUsualOrder = () => useQuery({ queryKey: ["usual-order"], queryFn: () => getUsualOrder(), retry: false });

/** The level grant g1's order limit, from the signed mandate. */
export function levelLimit(mandate: Mandate | undefined): number | null {
  const grants = mandate?.autonomy?.grants ?? [];
  const g = grants.find((x) => x.id === "g1") ?? grants.find((x) => x.enabled !== false);
  return g?.limits?.max_order_total ?? null;
}

/** Everything the "I'm using from your kitchen" panel and the read-back need. */
export function useKitchenContext() {
  const mandate = useMandate();
  const kitchen = useKitchen();
  const autonomy = useAutonomy();
  const m = mandate.data;
  return {
    musts: (m?.mandatory_rules ?? []).filter((r) => r.enabled !== false),
    prefers: (m?.preferences ?? []).filter((p) => p.enabled !== false),
    usualCount: (kitchen.data?.usual ?? []).filter((u) => u.status !== "removed" && u.status !== "replaced").length,
    level: autonomy.data?.level ?? null,
    levelName: autonomy.data?.name ?? null,
    limit: levelLimit(m),
    mandateVersion: m?.version ?? null,
    signedBy: m?.updated_by ?? null,
    profileVersion: kitchen.data?.version ?? null,
    kitchen: kitchen.data,
  };
}

const UNIT: Record<string, string> = { L: "L", KG: "kg", SALES_UNIT: "units" };
export const unitLabel = (u: string | undefined) => (u ? UNIT[u] ?? u : "");

/** "2026-11-16" -> "Mon 16 Nov" */
export function fmtDay(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso.length === 10 ? `${iso}T00:00:00` : iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" });
}

/** "Today", "Yesterday" or "12 Nov" */
export function fmtWhen(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const days = Math.round((new Date().setHours(0, 0, 0, 0) - new Date(d).setHours(0, 0, 0, 0)) / 86_400_000);
  if (days === 0) return "Today";
  if (days === 1) return "Yesterday";
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "short" });
}

export const fmtTime = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" }) : "";

export const shortHash = (h: string | null | undefined) => (h ? h.replace(/^sha256:/, "").slice(0, 8) : "");
