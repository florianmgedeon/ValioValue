import { Icon } from "../ui";
import { useAutonomy, useMandate } from "./queries";
import { navigate, type Tab } from "./route";

const TABS: { tab: Tab; label: string }[] = [
  { tab: "order", label: "Order" },
  { tab: "activity", label: "Activity" },
  { tab: "kitchen", label: "Kitchen" },
];

function initials(name: string) {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]?.toUpperCase()).join("") || "K";
}

export function Header({ tab, showTabs }: { tab: Tab; showTabs: boolean }) {
  const autonomy = useAutonomy();
  const mandate = useMandate();
  const kitchenName = mandate.data?.display_name || "Your kitchen";
  const a = autonomy.data;
  const level = a?.level ?? 0;
  const grants = mandate.data?.autonomy?.grants ?? [];
  const limit = (grants.find((g) => g.id === "g1") ?? grants[0])?.limits?.max_order_total;
  const amount = limit != null ? `€${limit.toLocaleString("en-GB")}` : "";
  // What the agent may order alone, in a few words (the full sentence is on the Kitchen tab).
  const long = level === 0 ? "I ask before every order" : level === 1 ? `Usual products alone · up to ${amount}` : `Orders alone · up to ${amount}`;
  const short = level === 0 ? "Asks first" : amount;

  return (
    <header
      className="sticky z-20 border-b border-line"
      style={{ top: "env(safe-area-inset-top)", background: "color-mix(in srgb, var(--bg) 88%, transparent)", backdropFilter: "saturate(1.6) blur(16px)", WebkitBackdropFilter: "saturate(1.6) blur(16px)" }}
    >
      <div className="mx-auto flex h-15 max-w-[1280px] items-center gap-2 sm:gap-4" style={{ paddingLeft: "max(16px, env(safe-area-inset-left))", paddingRight: "max(16px, env(safe-area-inset-right))" }}>
        <a href="#order" className="hidden items-baseline gap-1.5 text-[17px] font-extrabold tracking-tight whitespace-nowrap sm:flex">
          Aimo Agent
        </a>
        {showTabs && (
          <nav className="flex gap-0.5 rounded-full bg-surface p-[3px] sm:ml-2" role="tablist">
            {TABS.map((t) => (
              <button
                key={t.tab}
                role="tab"
                aria-selected={tab === t.tab}
                className={`h-8 rounded-full px-2.5 text-sm font-semibold sm:px-4 ${tab === t.tab ? "bg-bg text-ink shadow-[0_1px_3px_rgba(0,0,0,.12)]" : "text-ink2"}`}
                onClick={() => navigate(t.tab)}
              >
                {t.label}
              </button>
            ))}
          </nav>
        )}
        <span className="flex-1" />
        {showTabs && a && (
          <button
            className="flex h-9 items-center gap-2.5 rounded-full bg-auto-soft pr-3 pl-2.5 text-[13px] font-semibold whitespace-nowrap text-auto"
            title="What I may order without you"
            onClick={() => navigate("kitchen", "may")}
          >
            <Icon name="hand" className="hidden sm:block" />
            <span className="hidden md:inline">{long}</span>
            <span className="md:hidden">{short}</span>
          </button>
        )}
        <div className="flex h-9 items-center gap-2 rounded-full border-line text-[13px] font-semibold whitespace-nowrap sm:border sm:pr-1 sm:pl-3">
          <span className="hidden max-w-[220px] truncate md:inline" title={kitchenName}>{kitchenName}</span>
          <span className="grid size-7 place-items-center rounded-full bg-ink text-[11px] font-bold text-bg">{initials(kitchenName)}</span>
        </div>
      </div>
    </header>
  );
}
