import { useState } from "react";
import { decideDrift } from "../../api/kitchen";
import type { Drift, KitchenProfile, Schema, UsualOrder } from "../../api/types";
import { useComposerText } from "../../app/composer";
import { keys, queryClient, SIGNER_NAME } from "../../app/queries";
import { navigate } from "../../app/route";
import { Button, Drawer, Icon, Segmented, eur, useToast } from "../../ui";
import { unitText } from "./Profile";
import { errText } from "./Rules";

type Suggestion = Schema["Suggestion"];
type Usual = NonNullable<KitchenProfile["usual"]>[number];
type Week = NonNullable<NonNullable<UsualOrder["items"]>[number]["weeks"]>[number];

const day = (iso?: string | null) => (iso ? new Date(`${iso}T00:00:00`).toLocaleDateString("en-GB", { day: "numeric", month: "short" }) : "");

/** Suggestions that came from an order (2a: at most one per order). Documents keep their own section. */
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

function Spark({ values }: { values: number[] }) {
  const v = values.filter((x) => x > 0);
  if (v.length < 2) return null;
  const min = Math.min(...v), max = Math.max(...v);
  const pts = v.map((x, i) => `${2 + (i * 68) / (v.length - 1)},${max === min ? 12 : 20 - ((x - min) / (max - min)) * 16}`).join(" ");
  return (
    <svg className="spark" width={72} height={24} viewBox="0 0 72 24" aria-hidden="true">
      <polyline fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" points={pts} />
    </svg>
  );
}

/** Against the weeks with any delivery, so a closed week (holiday) does not count as "not ordered". */
function howOften(weeks: Week[], deliveries: number) {
  const n = weeks.filter((w) => (w.quantity ?? 0) > 0).length;
  if (!n) return "";
  return n >= deliveries ? "every week" : `in ${n} of ${deliveries} weeks`;
}

/** "What I learned from your orders": one row per usual product, the 8 weeks behind it, at most one open question. */
export function LearnedSection({ profile, usualOrder, limit, question, onAccept, onReject, busy, drift }: {
  profile: KitchenProfile;
  usualOrder: UsualOrder | undefined;
  limit: number | null;
  question: Suggestion | undefined;
  onAccept: (s: Suggestion) => void;
  onReject: (s: Suggestion) => void;
  busy: string | null;
  drift: Drift[];
}) {
  const setText = useComposerText();
  const [open, setOpen] = useState<Usual | null>(null);
  const items = (profile.usual ?? []).filter((u) => (u.status ?? "active") === "active");
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

      {question && (
        <div className="l-ask" aria-busy={busy === question.id}>
          <span className="ic"><Icon name="bulb" /></span>
          <div>
            <div className="t">{question.title}</div>
            {question.quote && <div className="s">{question.quote}</div>}
          </div>
          <div className="acts">
            <Button small onClick={() => onReject(question)} disabled={busy === question.id}>Just this time</Button>
            <Button small variant="primary" onClick={() => onAccept(question)} disabled={busy === question.id}>Remember</Button>
          </div>
        </div>
      )}

      {items.length === 0 && <div className="l-empty muted">Nothing yet. After a few orders, or one invoice photo, I know your usual products.</div>}
      {items.map((u) => {
        const weeks = weeksOf(u);
        const d = driftOf(u);
        const changed = changedOf(u);
        return (
          <button key={u.id} className={`l-row${d ? " drift" : ""}`} onClick={() => setOpen(u)}>
            <span className="w">{u.word}</span>
            <span className="min-w-0">
              <span className="p">{u.product_name || <span className="muted">no product yet</span>}{changed && <span className="chip k-accent">changed</span>}</span>
              <span className="m">
                {howOften(weeks, deliveries)}
                {d && <>{howOften(weeks, deliveries) ? " · " : ""}<span className="up">{d.pct! > 0 ? "↑" : "↓"} {Math.abs(d.pct!)} % since {day(d.since)}</span></>}
              </span>
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

      {items.some((u) => u.typical_qty != null) && drift.length === 0 && (
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

      <UsualDrawer usual={open} deliveries={deliveries} weeks={open ? weeksOf(open) : []} drift={open ? driftOf(open) : undefined} onClose={() => setOpen(null)} />
    </section>
  );
}

function UsualDrawer({ usual, weeks, deliveries, drift, onClose }: { usual: Usual | null; weeks: Week[]; deliveries: number; drift?: Drift; onClose: () => void }) {
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
      <p className="muted text-[13px]">Change the amount or the product by telling me below, e.g. “we need 100 L milk a week”.</p>
    </Drawer>
  );
}

/** 2e: before "Order the usual", what drifted. Shown only when something did. */
export function DriftCard({ drift, usualOrder, limit }: { drift: Drift[]; usualOrder: UsualOrder | undefined; limit: number | null }) {
  const toast = useToast();
  const [keep, setKeep] = useState<Record<string, "keep" | "back">>({});
  const [busy, setBusy] = useState(false);
  const qty = drift.filter((d) => d.kind === "quantity");
  const cost = drift.find((d) => d.kind === "cost");

  const go = async () => {
    setBusy(true);
    try {
      await decideDrift(drift.map((d) => ({
        key: d.key!, value: d.to!, keep: (keep[d.key!] ?? "keep") === "keep", back_to: d.kind === "quantity" ? d.from : null,
      })), SIGNER_NAME);
      await Promise.all([queryClient.invalidateQueries({ queryKey: keys.kitchen }), queryClient.invalidateQueries({ queryKey: ["usual-order"] })]);
      navigate("order", "usual");
    } catch (e) {
      toast(errText(e));
    } finally {
      setBusy(false);
    }
  };

  const series = (word: string) => usualOrder?.items?.find((i) => i.word === word)?.weeks?.map((w) => w.quantity ?? 0) ?? [];
  return (
    <section className="drift-card" id="drift">
      <div className="h">
        <h2>Before you order the usual</h2>
        <span className="chip">{drift.length === 1 ? "1 thing drifted" : `${drift.length} things drifted`}</span>
        <p>Over the last weeks your orders changed. Have a look, then I order.</p>
      </div>
      {qty.map((d) => (
        <div key={d.key} className="dr">
          <div>
            <div className="t">{d.text}</div>
            <div className="s">{d.detail}</div>
          </div>
          <Spark values={series(d.word ?? "")} />
          <div className="choice">
            <Segmented
              label={`${d.word} amount`}
              value={keep[d.key!] ?? "keep"}
              onChange={(v) => setKeep((k) => ({ ...k, [d.key!]: v }))}
              options={[
                { value: "keep", label: `Keep ${d.to} ${unitText(d.unit ?? undefined)}` },
                { value: "back", label: `Back to ${d.from} ${unitText(d.unit ?? undefined)}` },
              ]}
            />
          </div>
        </div>
      ))}
      {cost && (
        <div className="dr">
          <div>
            <div className="t">{cost.text}</div>
            <div className="s">
              {cost.detail}
              {limit !== null && cost.to! <= limit ? ` Still under your ${eur(limit)}.` : ""}
              {limit !== null && cost.to! > limit ? ` Above your ${eur(limit)}: you approve the cart.` : ""}
            </div>
          </div>
        </div>
      )}
      <div className="f">
        <span>Everything else looks like always.</span>
        <span className="spacer" />
        <Button variant="primary" small disabled={busy} onClick={() => void go()}>{busy ? "Saving…" : "Order the usual"}</Button>
      </div>
    </section>
  );
}
