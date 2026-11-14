/**
 * Trust inspector for one order: what the buyer signed, and the receipt check (`GET /orders/{id}/verify`).
 * TODO(F1): F1 builds the shared drawer in `src/features/order/TrustDrawer.tsx`. When it is merged, import it here
 * and delete this file.
 */
import { Chip, Drawer, HashRow, Icon } from "../../ui";
import { useVerify } from "./queries";

export type TrustView = { kind: "intent"; intentHash?: string; intentId?: string; signedBy?: string } | { kind: "receipt"; orderId: string };

export function TrustDrawer({ view, onClose }: { view: TrustView | null; onClose: () => void }) {
  return (
    <Drawer
      open={!!view}
      onClose={onClose}
      icon={<span className="act-node ok"><Icon name="shield" /></span>}
      title={view?.kind === "receipt" ? "Verify the receipt" : "What you signed"}
    >
      <p className="text-sm text-ink3">Anyone can check this without trusting me. Valio Aimo recomputes every hash and re-runs the decision.</p>
      {view?.kind === "intent" && <IntentBody {...view} />}
      {view?.kind === "receipt" && <ReceiptBody orderId={view.orderId} />}
    </Drawer>
  );
}

function IntentBody({ intentHash, intentId, signedBy }: { intentHash?: string; intentId?: string; signedBy?: string }) {
  return (
    <>
      {intentHash ? <HashRow label="Intent you signed" value={intentHash} /> : <p className="text-sm text-ink3">No intent hash recorded for this order.</p>}
      <div className="grid gap-2 text-sm">
        {intentId && <div>Intent <span className="font-mono">{intentId}</span>{signedBy ? `, signed by ${signedBy}` : ""}.</div>}
        <div>Items, amounts, delivery date and your rules, in one hash.</div>
        <div>Valio Aimo only executes what matches this hash.</div>
      </div>
    </>
  );
}

function ReceiptBody({ orderId }: { orderId: string }) {
  const v = useVerify(orderId, true);
  if (v.isPending) return <p className="text-sm text-ink3">Checking the receipt with Valio Aimo…</p>;
  if (v.isError) return <p className="text-sm text-must">Could not verify: {v.error.message}</p>;
  const checks = v.data.checks ?? [];
  return (
    <div className="grid gap-3">
      <div className="flex items-center gap-2 text-[15px] font-semibold">
        {v.data.valid ? <Chip kind="prefer" icon="check">Valid</Chip> : <Chip kind="must" icon="x">Not valid</Chip>}
        <span>Order <span className="font-mono">{orderId}</span></span>
      </div>
      <div className="grid gap-2">
        {checks.map((c, i) => (
          <div key={i} className="flex items-start gap-2 text-sm">
            <Chip kind={c.ok ? "prefer" : "must"} icon={c.ok ? "check" : "x"}>{c.ok ? "ok" : "fail"}</Chip>
            <span className="min-w-0">
              {c.name}
              {c.detail && <span className="block text-[12.5px] text-ink3 break-words">{c.detail}</span>}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
