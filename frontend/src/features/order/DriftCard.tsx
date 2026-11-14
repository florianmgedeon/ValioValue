import { useState } from "react";
import { decideDrift } from "../../api/kitchen";
import type { Drift, UsualOrder } from "../../api/types";
import { keys, queryClient, SIGNER_NAME } from "../../app/queries";
import { Button, Segmented, eur, useToast } from "../../ui";
import { unitLabel } from "./data";

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

/**
 * 2e: the first thing in the order chat after "Order the usual", when the usual order drifted. The choice becomes
 * the usual amount (`POST /kitchen/usual-order/drift`), then `onDone` sends the order with a one-line summary.
 */
export function DriftCard({ drift, usualOrder, limit, onDone }: {
  drift: Drift[];
  usualOrder: UsualOrder | undefined;
  limit: number | null;
  onDone: (summary: string) => void;
}) {
  const toast = useToast();
  const [keep, setKeep] = useState<Record<string, "keep" | "back">>({});
  const [busy, setBusy] = useState(false);
  const qty = drift.filter((d) => d.kind === "quantity");
  const cost = drift.find((d) => d.kind === "cost");
  const many = drift.length === 1 ? "one thing drifted" : `${drift.length === 2 ? "two" : drift.length} things drifted`;

  const go = async () => {
    setBusy(true);
    try {
      await decideDrift(drift.map((d) => ({
        key: d.key!, value: d.to!, keep: (keep[d.key!] ?? "keep") === "keep", back_to: d.kind === "quantity" ? d.from : null,
      })), SIGNER_NAME);
      await Promise.all([queryClient.invalidateQueries({ queryKey: keys.kitchen }), queryClient.invalidateQueries({ queryKey: ["usual-order"] })]);
      const parts = qty.map((d) => `${d.word} stays at ${(keep[d.key!] ?? "keep") === "keep" ? d.to : d.from} ${unitLabel(d.unit ?? undefined)} a week`);
      onDone(parts.length ? `Drift checked · ${parts.join(", ")} from now on.` : "Drift checked.");
    } catch (e) {
      toast(`Not saved: ${(e as Error).message}`);
      setBusy(false);
    }
  };

  const series = (word: string) => usualOrder?.items?.find((i) => i.word === word)?.weeks?.map((w) => w.quantity ?? 0) ?? [];
  return (
    <section className="card drift-card rise">
      <div className="h">
        <h3>Before I write it down: {many}</h3>
        <p>Over the last weeks your orders changed. Everything else looks like always.</p>
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
                { value: "keep", label: `Keep ${d.to} ${unitLabel(d.unit ?? undefined)}` },
                { value: "back", label: `Back to ${d.from} ${unitLabel(d.unit ?? undefined)}` },
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
        <span>{qty.length ? "Your choice becomes the usual amount." : "Nothing to choose, just so you know."}</span>
        <span className="flex-1" />
        <Button variant="primary" small disabled={busy} onClick={() => void go()}>{busy ? "Saving…" : "Use these amounts"}</Button>
      </div>
    </section>
  );
}
