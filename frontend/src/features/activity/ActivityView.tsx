import { useEffect, useMemo, useRef, useState } from "react";
import type { OrderSummary } from "../../api/types";
import { useComposer } from "../../app/composer";
import { useMandate } from "../../app/queries";
import { navigate } from "../../app/route";
import { Button, Tier, eur } from "../../ui";
import "./activity.css";
import { formatDay } from "./format";
import { fromAudit, fromStory } from "./model";
import { useOrderList, useOrderStory } from "./queries";
import { StoryTimeline } from "./StoryTimeline";
import { TrustDrawer, type TrustView } from "./TrustDrawer";

/** Activity tab (F4): every order, what the agent did and why. `#activity/<order_id>` selects an order. */
export function ActivityView({ orderId }: { orderId?: string }) {
  useComposer(null); // The mockup has no composer on this tab.
  const list = useOrderList();
  const orders = useMemo(() => list.data?.pages.flat() ?? [], [list.data]);
  const selectedId = orderId ?? orders[0]?.order_id;
  const summary = orders.find((o) => o.order_id === selectedId);

  const storyRef = useRef<HTMLElement>(null);
  const open = (id: string) => {
    navigate("activity", id);
    // One column on small screens: jump to the story below the list.
    if (window.matchMedia("(max-width: 1000px)").matches) requestAnimationFrame(() => storyRef.current?.scrollIntoView({ block: "start" }));
  };

  return (
    <main className="mx-auto max-w-[1280px] px-4 act-grid">
      <div className="act-list">
        <h1>Activity</h1>
        <p className="mb-2 text-[13px] text-ink3">Every order, what I did and why.</p>
        {list.isPending && <p className="text-sm text-ink3">Loading orders…</p>}
        {list.isError && <p className="text-sm text-must">Could not load orders: {list.error.message}</p>}
        {list.isSuccess && !orders.length && <p className="text-sm text-ink3">No orders yet. Orders you place appear here.</p>}
        {orders.map((o) => <OrderItem key={o.order_id} o={o} current={o.order_id === selectedId} onOpen={open} />)}
        {list.hasNextPage && (
          <Button small onClick={() => void list.fetchNextPage()} disabled={list.isFetchingNextPage}>
            {list.isFetchingNextPage ? "Loading…" : "Load more"}
          </Button>
        )}
      </div>
      <section className="act-story" ref={storyRef}>
        {selectedId ? <Story orderId={selectedId} summary={summary} /> : null}
      </section>
    </main>
  );
}

function OrderItem({ o, current, onOpen }: { o: OrderSummary; current: boolean; onOpen: (id: string) => void }) {
  const id = o.order_id ?? "";
  return (
    <button type="button" className="act-item" aria-current={current} onClick={() => onOpen(id)}>
      <b>{o.title || id}</b>
      <span className="act-px">{eur(o.total)}</span>
      <span className="act-muted">{[o.created_at && formatDay(o.created_at), `#${id}`].filter(Boolean).join(" · ")}</span>
      <span>{o.label && <Tier tier={o.tier ?? "ask"}>{o.label}</Tier>}</span>
    </button>
  );
}

function Story({ orderId, summary }: { orderId: string; summary?: OrderSummary }) {
  const story = useOrderStory(orderId);
  const mandate = useMandate();
  const [trust, setTrust] = useState<TrustView | null>(null);

  useEffect(() => setTrust(null), [orderId]);

  const model = useMemo(() => {
    if (!story.data) return null;
    return "story" in story.data ? fromStory(story.data.story, summary) : fromAudit(orderId, story.data.audit, summary);
  }, [story.data, summary, orderId]);

  const grants = mandate.data?.autonomy?.grants;
  const rules = mandate.data?.mandatory_rules;
  const grantLabel = (id: string) => grants?.find((g) => g.id === id)?.label ?? undefined;
  const ruleLabel = (id: string) => rules?.find((r) => r.id === id)?.label ?? undefined;

  if (story.isPending) return <p className="text-sm text-ink3">Loading order {orderId}…</p>;
  if (story.isError) return <p className="text-sm text-must">Could not load order {orderId}: {story.error.message}</p>;
  if (!model) return null;
  return (
    <>
      <StoryTimeline model={model} grantLabel={grantLabel} ruleLabel={ruleLabel} onTrust={setTrust} />
      <TrustDrawer view={trust} onClose={() => setTrust(null)} />
    </>
  );
}
