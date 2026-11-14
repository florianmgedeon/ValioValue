import { useQuery } from "@tanstack/react-query";
import { verifyOrder } from "../../api/orders";
import type { OrderIntent } from "../../api/types";
import { Chip, Drawer, HashRow, Icon } from "../../ui";

export type TrustTarget = { mode: "intent"; intent: OrderIntent } | { mode: "receipt"; orderId: string };

function Row({ ok, name, detail }: { ok: boolean; name: string; detail?: string }) {
  return (
    <div className="verify-row">
      <Chip kind={ok ? "prefer" : "must"}><Icon name={ok ? "check" : "x"} /></Chip>
      <span>{name}{detail && <small>{detail}</small>}</span>
    </div>
  );
}

function Receipt({ orderId }: { orderId: string }) {
  const v = useQuery({ queryKey: ["verify", orderId], queryFn: () => verifyOrder(orderId) });
  if (v.isLoading) return <p className="text-ink3">Valio Aimo is recomputing every hash…</p>;
  if (v.isError || !v.data) return <p className="text-must">Could not verify: {(v.error as Error | null)?.message ?? "no answer"}</p>;
  const checks = v.data.checks ?? [];
  // Hash-like details get their own HashRow; everything else is a row.
  const hashes = checks.filter((c) => /^sha256:|^[A-Za-z0-9_-]{40,}={0,2}$/.test(c.detail ?? ""));
  return (
    <>
      <div className={`rounded-xl px-3.5 py-2.5 text-sm font-semibold ${v.data.valid ? "bg-prefer-soft text-prefer" : "bg-must-soft text-must"}`}>
        {v.data.valid ? `Order ${orderId}: every check passes.` : `Order ${orderId}: a check failed.`}
      </div>
      {hashes.map((c) => <HashRow key={c.name} label={c.name ?? "hash"} value={c.detail ?? ""} />)}
      <div className="grid gap-2">
        {checks.map((c) => <Row key={c.name} ok={!!c.ok} name={c.name ?? "check"} detail={hashes.includes(c) ? undefined : c.detail} />)}
      </div>
    </>
  );
}

function Intent({ intent }: { intent: OrderIntent }) {
  const musts = intent.hard_rules.filter((r) => r.source === "mandate" || /^m\d/.test(r.id ?? ""));
  return (
    <>
      {intent.intent_hash && <HashRow label="Intent you signed" value={intent.intent_hash} />}
      {intent.buyer_signature && <HashRow label="Your signature · Ed25519" value={intent.buyer_signature} />}
      <div className="grid gap-2 text-sm">
        <div>Items, amounts, delivery date and your rules, in one hash.</div>
        {musts.length > 0 && <div>Musts added from your mandate: {musts.map((r) => r.id).join(", ")}</div>}
        <div>Valio Aimo only executes what matches this hash.</div>
      </div>
    </>
  );
}

export function TrustDrawer({ target, onClose }: { target: TrustTarget | null; onClose: () => void }) {
  return (
    <Drawer
      open={!!target}
      onClose={onClose}
      title={target?.mode === "intent" ? "What you signed" : "Verify the receipt"}
      icon={<span className="check"><Icon name="shield" /></span>}
    >
      <p className="text-ink3">Anyone can check this without trusting me. Valio Aimo recomputes every hash and re-runs the decision.</p>
      {target?.mode === "intent" && <Intent intent={target.intent} />}
      {target?.mode === "receipt" && <Receipt orderId={target.orderId} />}
    </Drawer>
  );
}
