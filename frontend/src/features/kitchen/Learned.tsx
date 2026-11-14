import { useState } from "react";
import type { Drift, KitchenProfile, Schema, UsualOrder } from "../../api/types";
import { useComposerText } from "../../app/composer";
import { navigate } from "../../app/route";
import { Button, Drawer, Icon, eur } from "../../ui";
import { unitText } from "./Profile";
import { FallbackEditor, fallbackSentence, type UsualFallbackUpdate } from "./Substitutes";

type Suggestion = Schema["Suggestion"];
type Usual = NonNullable<KitchenProfile["usual"]>[number];
type Week = NonNullable<NonNullable<UsualOrder["items"]>[number]["weeks"]>[number];

const day = (iso?: string | null) => (iso ? new Date(`${iso}T00:00:00`).toLocaleDateString("en-GB", { day: "numeric", month: "short" }) : "");

/** Suggestions that came from an order (2a: at most one per order), as opposed to a document. */
export function isFromOrder(s: Suggestion, profile: KitchenProfile) {
  const src = profile.sources?.find((x) => x.id === s.source_id);
  return src?.type === "order" || src?.type === "correction";
}

/** Eight tiny bars: the amount per week. A week without an order is a flat grey tick. The baseline sits at 80 % of
 *  the smallest amount, so a rise of 20 % is visible; a flat row stays full height. */
function Bars({ weeks }: { weeks: Week[] }) {
  const on = weeks.map((w) => w.quantity ?? 0).filter((q) => q > 0);
  if (!on.length) return null;
  const max = Math.max(...on);
  const base = Math.min(...on) * 0.8;
  const H = 16, bw = 5, gap = 2;
  const h = (v: number) => (max === base ? H : 5 + ((v - base) / (max - base)) * (H - 5));
  return (
    <svg className="bars" width={weeks.length * (bw + gap) - gap} height={H} role="img" aria-label={`ordered in ${on.length} of the last ${weeks.length} weeks`}>
      {weeks.map((w, i) => (w.quantity ?? 0) > 0
        ? <rect key={i} x={i * (bw + gap)} y={H - h(w.quantity!)} width={bw} height={h(w.quantity!)} rx={1.5} fill="currentColor" />
        : <rect key={i} className="gap" x={i * (bw + gap)} y={H - 2} width={bw} height={2} rx={1} />)}
    </svg>
  );
}

/** Against the weeks with any delivery, so a closed week (holiday) does not count as "not ordered". */
function howOften(weeks: Week[], deliveries: number) {
  const n = weeks.filter((w) => (w.quantity ?? 0) > 0).length;
  if (!n) return "";
  return n >= deliveries ? "every week" : `in ${n} of ${deliveries} weeks`;
}

/** "What I learned from your orders": one row per usual product and the 8 weeks behind it. Drift shows as orange
 *  bars here; the drift decision itself comes when the buyer orders the usual (Order tab). */
export function LearnedSection({ profile, usualOrder, limit, drift, onSaveUsual }: {
  profile: KitchenProfile;
  usualOrder: UsualOrder | undefined;
  limit: number | null;
  drift: Drift[];
  onSaveUsual?: (update: UsualFallbackUpdate) => Promise<void>;
}) {
  const setText = useComposerText();
  const [openId, setOpen] = useState<string | null>(null);
  const items = (profile.usual ?? []).filter((u) => (u.status ?? "active") === "active");
  // Look the product up on every render, so the drawer shows saved substitutes right away.
  const open = items.find((u) => u.id === openId) ?? null;
  const weeksOf = (u: Usual) => usualOrder?.items?.find((i) => i.word === u.word)?.weeks ?? [];
  const driftOf = (u: Usual) => drift.find((d) => d.kind === "quantity" && d.word === u.word);
  const changedOf = (u: Usual) => profile.suggestions?.find((s) => s.kind === "usual" && s.status === "accepted"
    && (s.payload as { word?: string; replaces?: string } | undefined)?.word === u.word && (s.payload as { replaces?: string }).replaces);
  const est = usualOrder?.estimate_total ?? null;
  const fits = est !== null && limit !== null ? est <= limit : null;
  const h = usualOrder?.history;
  const still = usualOrder?.still_learning?.[0];
  const deliveries = h?.weeks ?? 8;

  return (
    <section className="sec learned" id="learned">
      <div className="sec-h">
        <h2>What I learned from your orders</h2>
        <span className="spacer" />
        {h?.orders ? <span className="muted text-[13px]">{h.orders} orders since {day(h.since)}</span> : null}
        <p>When you say the word, I pick this product. The bars are the last eight weeks.</p>
      </div>

      {items.length === 0 && <div className="l-empty muted">Nothing yet. After a few orders, or one invoice photo, I know your usual products.</div>}
      {items.map((u) => {
        const weeks = weeksOf(u);
        const d = driftOf(u);
        const changed = changedOf(u);
        return (
          <button key={u.id} className={`l-row${d ? " drift" : ""}`} onClick={() => setOpen(u.id ?? null)}>
            <span className="w">{u.word}</span>
            <span className="min-w-0">
              <span className="p">{u.product_name || <span className="muted">no product yet</span>}{changed && <span className="chip k-accent">changed</span>}</span>
              <span className="m">
                {howOften(weeks, deliveries)}
                {d && <>{howOften(weeks, deliveries) ? " · " : ""}<span className="up">{d.pct! > 0 ? "↑" : "↓"} {Math.abs(d.pct!)} % since {day(d.since)}</span></>}
              </span>
              <span className="m">{fallbackSentence(u)}</span>
            </span>
            <span className="r">
              <span className="qty">{u.typical_qty != null ? `~${u.typical_qty} ${unitText(u.unit)} / ${u.per || "week"}` : ""}</span>
              <Bars weeks={weeks} />
            </span>
            <Icon name="chev" className="chev" />
          </button>
        );
      })}

      {still && (
        <div className="l-still">
          Still learning: <b>{still.word}</b> – {still.products} different products in {still.orders} orders. I’ll ask you next time.
        </div>
      )}

      {items.some((u) => u.typical_qty != null) && (
        <div className="usual-foot">
          <span className="est">
            {est !== null ? <>The usual order ≈ <b className="num">{eur(est)}</b>. </> : null}
            {fits === true && "Inside your limit: I can order it alone."}
            {fits === false && "Above your limit: you approve the cart."}
          </span>
          <Button small onClick={() => { navigate("order"); setText("the usual, but "); }}>Adjust</Button>
          <Button variant="primary" small onClick={() => navigate("order", "usual")}>Order the usual</Button>
        </div>
      )}

      <UsualDrawer usual={open} deliveries={deliveries} weeks={open ? weeksOf(open) : []} drift={open ? driftOf(open) : undefined} onSaveUsual={onSaveUsual} onClose={() => setOpen(null)} />
    </section>
  );
}

function UsualDrawer({ usual, weeks, deliveries, drift, onSaveUsual, onClose }: { usual: Usual | null; weeks: Week[]; deliveries: number; drift?: Drift; onSaveUsual?: (update: UsualFallbackUpdate) => Promise<void>; onClose: () => void }) {
  if (!usual) return null;
  const main = usual.product_name ?? "";
  const shown = [...weeks].reverse();
  const n = weeks.filter((w) => (w.quantity ?? 0) > 0).length;
  const why = n === 0
    ? "Not ordered in the last eight weeks. I know it from your documents."
    : `Ordered ${howOften(weeks, deliveries)}${usual.typical_qty != null ? `, usually ${usual.typical_qty} ${unitText(usual.unit)}` : ""}.`
      + (drift ? ` ${drift.text}, ${drift.detail?.toLowerCase()}` : "");
  return (
    <Drawer open onClose={onClose} title={main} icon={<span className="l-dw">{usual.word}</span>}>
      <p className="text-[15px]">{why}</p>
      {n > 0 && (
        <div className="grid gap-2">
          <span className="eyebrow">The last eight weeks</span>
          <div className="l-ev">
            {shown.map((w) => {
              const other = w.products?.length && !w.products.includes(main) ? w.products.join(", ") : "";
              return (
                <div key={w.week} className={(w.quantity ?? 0) > 0 ? "" : "none"}>
                  <span className="d">{day(w.week)}</span>
                  <span className="o">{other}</span>
                  <span className="q">{(w.quantity ?? 0) > 0 ? `${w.quantity} ${unitText(usual.unit)}` : "–"}</span>
                </div>
              );
            })}
          </div>
        </div>
      )}
      {onSaveUsual && usual.id && <FallbackEditor key={usual.id} usual={usual} onSave={onSaveUsual} />}
      <p className="muted text-[13px]">Change the amount or the product by telling me below, e.g. “we need 100 L milk a week”.</p>
    </Drawer>
  );
}
