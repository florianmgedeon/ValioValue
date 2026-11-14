import { useSyncExternalStore } from "react";

export type Tab = "order" | "activity" | "kitchen" | "onboard" | "ui";
export interface Route {
  tab: Tab;
  /** Second segment, e.g. the order id in `#activity/ord_41`. */
  param?: string;
}

const TABS: Tab[] = ["order", "activity", "kitchen", "onboard", "ui"];

export function parseHash(hash: string): Route {
  const [head = "", param] = hash.replace(/^#\/?/, "").split("/");
  const tab = (TABS as string[]).includes(head) ? (head as Tab) : "order";
  if (tab === "ui" && !import.meta.env.DEV) return { tab: "order" };
  return param ? { tab, param: decodeURIComponent(param) } : { tab };
}

export function routeHref(tab: Tab, param?: string): string {
  return `#${tab}${param ? `/${encodeURIComponent(param)}` : ""}`;
}

export function navigate(tab: Tab, param?: string) {
  const next = routeHref(tab, param);
  if (location.hash !== next) location.hash = next;
  window.scrollTo(0, 0);
}

const subscribe = (cb: () => void) => {
  window.addEventListener("hashchange", cb);
  return () => window.removeEventListener("hashchange", cb);
};

/** The current hash route. Tabs: `#order`, `#activity[/<order_id>]`, `#kitchen`, `#onboard`, `#ui` (dev only). */
export function useHashRoute(): Route {
  const hash = useSyncExternalStore(subscribe, () => location.hash);
  return parseHash(hash);
}
