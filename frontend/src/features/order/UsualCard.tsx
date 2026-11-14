import { useComposerText } from "../../app/composer";
import { navigate } from "../../app/route";
import { Button, Icon, eur } from "../../ui";
import { fmtDay, unitLabel, useKitchenContext, useUsualOrder } from "./data";
import "./order.css";

/**
 * "Your usual order": the usual products and amounts, with a button that starts the order in the Order tab
 * (`#order/usual`). Lives in the Kitchen tab so the Order tab only has photo + chat; the order itself always
 * runs in the one Order view.
 */
export function UsualCard() {
  const setText = useComposerText();
  const usual = useUsualOrder();
  const k = useKitchenContext();
  // Without 2c's endpoint: the usual products and amounts from the kitchen profile, no price estimate.
  const items = usual.data?.items.map((i) => ({ word: i.word, qty: `${i.quantity} ${i.unit}` }))
    ?? (k.kitchen?.usual ?? []).filter((u) => u.status !== "removed" && u.status !== "replaced" && u.typical_qty).map((u) => ({ word: u.word ?? "?", qty: `${u.typical_qty} ${unitLabel(u.unit)}` }));
  if (!items.length) return null;
  const est = usual.data?.estimate_total ?? null;
  const fits = est !== null && k.limit !== null ? est <= k.limit : null;
  const adjust = () => {
    navigate("order");
    setText("the usual, but ");
  };
  return (
    <div className="usual-card">
      <div className="head">
        <div className="eyebrow flex items-center gap-1" style={{ color: "var(--usual)" }}><Icon name="usual" size={12} /> Your usual order</div>
        <h2>{items.length} products, the usual amounts</h2>
        <p>{usual.data?.based_on ? `Based on ${usual.data.based_on}.` : "From your kitchen profile."} Or just say “the usual” on the Order tab.</p>
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
        <Button small onClick={adjust}>Adjust</Button>
        <Button variant="primary" onClick={() => navigate("order", "usual")}>Order the usual</Button>
      </div>
    </div>
  );
}
