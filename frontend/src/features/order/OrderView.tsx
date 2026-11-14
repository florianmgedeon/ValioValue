import { useEffect, useMemo, useRef, useState } from "react";
import { useComposer, useComposerText } from "../../app/composer";
import { usePhoneCamera } from "../../app/PhoneCamera";
import { Button } from "../../ui";
import { TypingBlock, BlockView, type BlockCtx } from "./blocks";
import { fmtTime } from "./data";
import { KitchenPanel, LivePanel } from "./panels";
import { StartView } from "./StartView";
import { TrustDrawer, type TrustTarget } from "./TrustDrawer";
import { useOrderSession } from "./useOrderSession";
import "./order.css";

const HINTS = ["120 L milk by Monday", "the usual, but double the oat drink", "ignore the nut rule just this once"];

/** The Order tab (F1): start screen until a chat starts, then only this order. */
export default function OrderView() {
  const { sessionId, state, send, sending, newOrder, local } = useOrderSession();
  const { onPhoto } = usePhoneCamera();
  const prefill = useComposerText();
  const [trust, setTrust] = useState<TrustTarget | null>(null);
  const inOrder = !!sessionId || sending;
  const { blocks, live } = state;

  useComposer({
    onSubmit: (t) => void send(t),
    hints: inOrder ? [] : HINTS,
    placeholder: inOrder ? "Answer me or add something…" : "What do you need?",
    camera: "order",
  });

  // A photo for "order" starts (or continues) the chat with the upload attached (decision W2).
  useEffect(() => onPhoto("order", (u) => { if (u.upload_id) void send("", [{ upload_id: u.upload_id }]); }), [onPhoto, send]);

  // Keep the newest block in view.
  const end = useRef<HTMLDivElement>(null);
  const count = blocks.length + (live.typing ? 1 : 0);
  useEffect(() => {
    if (count) end.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [count]);

  const ctx: BlockCtx = useMemo(() => {
    const rb = [...blocks].reverse().find((b) => b.kind === "readback");
    const f = blocks.find((b) => b.kind === "funnel");
    return {
      local,
      retry: () => state.lastText && void send(state.lastText),
      openTrust: setTrust,
      prefill,
      issues: f?.kind === "funnel" ? f.issues : {},
      intent: rb?.kind === "readback" ? rb.intent : null,
      usualExcluded: f?.kind === "funnel" ? f.usualExcluded : [],
      mandateId: live.mandateId,
    };
  }, [blocks, live.mandateId, local, prefill, send, state.lastText]);

  let n = 0;
  return (
    <main className="order-wrap order">
      <div className="thread">
        {!inOrder ? (
          <StartView onUsual={() => void send("Order the usual")} />
        ) : (
          <>
            <div className="orderbar">
              <h1>This order</h1>
              {live.startedAt && <span className="text-[13px] text-ink3">started {fmtTime(live.startedAt)}</span>}
              <span className="flex-1" />
              <Button small icon="back" onClick={newOrder}>New order</Button>
            </div>
            {blocks.map((b) => <BlockView key={b.key} b={b} n={b.kind === "decision" || b.kind === "approve" ? ++n : 0} ctx={ctx} />)}
            {(live.typing || (sending && !blocks.length)) && <TypingBlock />}
            <div ref={end} />
          </>
        )}
      </div>
      <aside className="aside">
        {inOrder && <LivePanel live={live} />}
        <KitchenPanel />
      </aside>
      <TrustDrawer target={trust} onClose={() => setTrust(null)} />
    </main>
  );
}
