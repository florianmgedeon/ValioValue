import { useComposerText } from "../../app/composer";
import { usePhoneCamera } from "../../app/PhoneCamera";
import { SIGNER_NAME } from "../../app/queries";
import { navigate } from "../../app/route";
import { Button, Icon, Paper, Tier, eur } from "../../ui";
import { fmtDay, fmtWhen, unitLabel, useKitchenContext, useRecentOrders, useUsualOrder } from "./data";

function greeting() {
  const h = new Date().getHours();
  return h < 11 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
}

function PhotoCard() {
  const camera = usePhoneCamera();
  return (
    <div className="photo-card" role="button" tabIndex={0} onClick={() => camera.open("order")} onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && camera.open("order")}>
      <span className="ic"><Icon name="cam" /></span>
      <span className="relative z-[1] grid gap-1.5">
        <h2>Photograph an order</h2>
        <p>A list, a note, a whiteboard. For tomorrow or next week: I read the date too.</p>
      </span>
      <span className="acts">
        <Button variant="primary" small icon="phone" onClick={(e) => { e.stopPropagation(); camera.open("order"); }}>Use your phone</Button>
        <Button small icon="clip" onClick={(e) => { e.stopPropagation(); camera.upload("order"); }}>Upload</Button>
      </span>
      <span className="stack" aria-hidden="true">
        <Paper variant="hand" tilt="l">
          <b>Order – Wed!</b><br />milk 120 L<br />butter 10 kg<br />rye rolls 300<br />apples 40 kg<br />granola 15 kg
        </Paper>
      </span>
    </div>
  );
}

function UsualCard({ onOrder }: { onOrder: () => void }) {
  const setText = useComposerText();
  const usual = useUsualOrder();
  const k = useKitchenContext();
  // Without 2c's endpoint: the usual products and amounts from the kitchen profile, no price estimate.
  const items = usual.data?.items.map((i) => ({ word: i.word, qty: `${i.quantity} ${i.unit}` }))
    ?? (k.kitchen?.usual ?? []).filter((u) => u.status !== "removed" && u.status !== "replaced" && u.typical_qty).map((u) => ({ word: u.word ?? "?", qty: `${u.typical_qty} ${unitLabel(u.unit)}` }));
  if (!items.length) return null;
  const est = usual.data?.estimate_total ?? null;
  const fits = est !== null && k.limit !== null ? est <= k.limit : null;
  return (
    <div className="usual-card">
      <div className="head">
        <div className="eyebrow flex items-center gap-1" style={{ color: "var(--usual)" }}><Icon name="usual" size={12} /> Your usual order</div>
        <h2>{items.length} products, the usual amounts</h2>
        <p>{usual.data?.based_on ? `Based on ${usual.data.based_on}.` : "From your kitchen profile."}</p>
      </div>
      <div className="ulist">
        {items.map((i) => <div key={i.word}><span className="w">{i.word}</span><span className="q">{i.qty}</span></div>)}
      </div>
      <div className="foot">
        <div className="est">
          {est !== null ? <>≈ <b className="num">{eur(est)}</b>{usual.data?.deliver_by ? ` · ${fmtDay(usual.data.deliver_by)}` : ""}<br /></> : null}
          {fits === true && `Inside your ${eur(k.limit)} limit: I can order it alone.`}
          {fits === false && `Above your ${eur(k.limit)} limit: you approve the cart.`}
          {fits === null && "Valio Aimo prices it after you confirm."}
        </div>
        <Button small onClick={() => setText("the usual, but ")}>Adjust</Button>
        <Button variant="primary" onClick={onOrder}>Order the usual</Button>
      </div>
    </div>
  );
}

function Recent() {
  const orders = useRecentOrders();
  const list = orders.data ?? [];
  if (!list.length) return null;
  return (
    <div className="recent">
      <div className="recent-h">
        <span className="eyebrow">Recent orders</span>
        <button className="link flex items-center gap-1 text-[13px]" onClick={() => navigate("activity")}>All activity <Icon name="arrow" size={13} /></button>
      </div>
      {list.slice(0, 3).map((o) => (
        <button key={o.order_id} className="recent-row" onClick={() => navigate("activity", o.order_id)}>
          <span className="text-ink3">{fmtWhen(o.created_at)}</span>
          <span className="truncate">{o.title}{o.lines ? ` · ${o.lines} line${o.lines === 1 ? "" : "s"}` : ""}</span>
          <Tier tier={o.tier ?? "ask"}>{o.label ?? o.status}</Tier>
          <span className="num font-semibold">{eur(o.total)}</span>
        </button>
      ))}
    </div>
  );
}

/** Before a chat starts: greeting, photo, the usual order, recent orders. */
export function StartView({ onUsual }: { onUsual: () => void }) {
  const k = useKitchenContext();
  const by = k.kitchen?.history?.at(-1)?.by;
  const first = (by && !by.includes(".") ? by : SIGNER_NAME).split(/\s+/)[0];
  return (
    <section className="hello">
      <h1>{greeting()}, {first}.</h1>
      <p>What does the kitchen need? Take a photo, order your usual, or just type it below.</p>
      <div className="starters">
        <PhotoCard />
        <UsualCard onOrder={onUsual} />
      </div>
      <Recent />
    </section>
  );
}
