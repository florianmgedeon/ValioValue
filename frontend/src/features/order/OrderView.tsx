import { useEffect, useMemo, useRef, useState } from "react";
import { getUsualOrder } from "../../api/kitchen";
import type { Drift, UsualOrder } from "../../api/types";
import { useComposer, useComposerText } from "../../app/composer";
import { usePhoneCamera } from "../../app/PhoneCamera";
import { navigate } from "../../app/route";
import { useMandate } from "../../app/queries";
import { Button, Icon } from "../../ui";
import { TypingBlock, BlockView, type BlockCtx } from "./blocks";
import { DriftCard } from "./DriftCard";
import { fmtTime, levelLimit } from "./data";
import { KitchenPanel, LivePanel } from "./panels";
import { StartView } from "./StartView";
import { useOrderSession } from "./useOrderSession";
import "./order.css";

const HINTS = ["the usual", "120 L milk by Monday", "the usual, but double the oat drink"];

/**
 * The Order tab (F1): photo tile + chat until an order starts, then only this order. Every way in (photo, typed,
 * spoken, "Order the usual" from the Kitchen tab via `#order/usual`) ends in this one thread.
 */
export default function OrderView({ start }: { start?: string }) {
  const { sessionId, state, send, sending, newOrder, local } = useOrderSession();
  const { onPhoto } = usePhoneCamera();
  const prefill = useComposerText();
  // 2e: "Order the usual" first shows what drifted (if anything), then sends. `checked` is the one-line summary.
  const [pre, setPre] = useState<{ drift: Drift[]; usual: UsualOrder } | null>(null);
  const [checked, setChecked] = useState<string | null>(null);
  const limit = levelLimit(useMandate().data);
  const inOrder = !!sessionId || sending || !!pre;
  const { blocks, live } = state;

  useComposer({
    onSubmit: (t) => void send(t),
    hints: inOrder ? [] : HINTS,
    placeholder: inOrder ? "Answer me or add something…" : "What do you need?",
    camera: "order",
    prominent: !inOrder,
  });

  // "Order the usual" from the Kitchen tab lands here as `#order/usual`: start it once, then clear the route.
  const started = useRef(false);
  useEffect(() => {
    if (start !== "usual" || started.current) return;
    started.current = true;
    navigate("order");
    if (sessionId) return;
    void (async () => {
      let usual: UsualOrder | null = null;
      try {
        usual = await getUsualOrder();
      } catch {
        // no drift check without the usual order: order as before
      }
      if (usual?.drift?.length) setPre({ drift: usual.drift, usual });
      else void send("Order the usual");
    })();
  }, [start, sessionId, send]);
  useEffect(() => { if (!sessionId && !sending && !pre) setChecked(null); }, [sessionId, sending, pre]);

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
      prefill,
      issues: f?.kind === "funnel" ? f.issues : {},
      intent: rb?.kind === "readback" ? rb.intent : null,
      usualExcluded: f?.kind === "funnel" ? f.usualExcluded : [],
      mandateId: live.mandateId,
    };
  }, [blocks, live.mandateId, local, prefill, send, state.lastText]);

  let n = 0;
  return (
    <main className={`order-wrap order ${inOrder ? "" : "order-start"}`}>
      <div className="thread">
        {!inOrder ? (
          <StartView />
        ) : (
          <>
            <div className="orderbar">
              <h1>This order</h1>
              {live.startedAt && <span className="text-[13px] text-ink3">started {fmtTime(live.startedAt)}</span>}
              <span className="flex-1" />
              <Button small icon="back" onClick={() => { setPre(null); setChecked(null); newOrder(); }}>New order</Button>
            </div>
            {pre && (
              <>
                <div className="msg-me"><div className="bubble">Order the usual</div></div>
                <div className="agent">
                  <span className="agent-dot"><Icon name="spark" /></span>
                  <div className="agent-body">
                    <DriftCard drift={pre.drift} usualOrder={pre.usual} limit={limit} onDone={(summary) => { setChecked(summary); setPre(null); void send("Order the usual"); }} />
                  </div>
                </div>
              </>
            )}
            {blocks.map((b, i) => (
              <div key={b.key} className="contents">
                <BlockView b={b} n={b.kind === "decision" || b.kind === "approve" ? ++n : 0} ctx={ctx} />
                {i === 0 && checked && (
                  <div className="agent">
                    <span className="agent-dot"><Icon name="spark" /></span>
                    <div className="agent-body"><div className="drift-done"><Icon name="check" size={14} className="inline align-[-2px]" /> {checked}</div></div>
                  </div>
                )}
              </div>
            ))}
            {(live.typing || (sending && !blocks.length)) && <TypingBlock />}
            <div ref={end} />
          </>
        )}
      </div>
      {inOrder && (
        <aside className="aside">
          <LivePanel live={live} />
          <KitchenPanel />
        </aside>
      )}
    </main>
  );
}
