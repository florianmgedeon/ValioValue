import { useState } from "react";
import { draftGrant } from "../../api/mandate";
import type { AutonomyView, DraftGrantResponse } from "../../api/types";
import { navigate } from "../../app/route";
import { Button, Chip, Cond, Icon, Modal, Toggle, DiffList } from "../../ui";
import { acceptsText, condText, limitText, ruleLabel, type Grant, type HardRule, type LevelChange } from "./model";
import { errText } from "./Rules";

const eur = (n: number) => `€${n.toLocaleString("en-GB")}`;

/**
 * The three choices, in the backend's meaning (autonomy.py): 0 no grants, 1 g1 scoped to the usual products,
 * 2 g1 for anything that passes the musts. Level 3 (standing orders) is not built and not shown.
 */
export const LEVELS: { n: 0 | 1 | 2; name: string; text: (limit: number) => string; now: (limit: number) => string }[] = [
  { n: 0, name: "Ask me first", text: () => "I prepare every order. You approve each one.", now: () => "Nothing. I ask you before every order." },
  { n: 1, name: "My usual products", text: (l) => `I order your usual products alone, up to ${eur(l)} an order.`, now: (l) => `Your usual products, up to ${eur(l)} an order.` },
  { n: 2, name: "Anything within my rules", text: (l) => `I order any product that passes your rules alone, up to ${eur(l)} an order.`, now: (l) => `Anything that passes your rules, up to ${eur(l)} an order.` },
];

export interface AutonomyProps {
  autonomy: AutonomyView;
  g1: Grant | undefined;
  extras: { grant: Grant; inMandate: boolean; summary?: string; source?: string }[];
  musts: HardRule[];
  pendingLevel: LevelChange | null;
  changedGrants: Set<string>;
  freshGrants: Set<string>;
  onLevel: (n: 0 | 1 | 2) => void;
  onLimit: (limit: number) => void;
  onToggleGrant: (g: Grant) => void;
  onAddGrant: (g: Grant) => void;
  locked: (msg: string) => void;
}

/** "What may I order without you?": one sentence for today, the choices behind "Change", extras folded away. */
export function AutonomySection(p: AutonomyProps) {
  const { autonomy: a, g1 } = p;
  const level = (a.level ?? 0) as 0 | 1 | 2;
  const clean = a.clean_orders ?? 0;
  const needed = a.needed ?? 10;
  const limit = p.pendingLevel?.max_order_total ?? g1?.limits?.max_order_total ?? 600;
  const recommended = a.recommended_next ?? a.offer_next ?? false;
  const shown = (p.pendingLevel?.level ?? level) as 0 | 1 | 2;
  const [open, setOpen] = useState(false);
  const [askLevel2, setAskLevel2] = useState(false);
  const [limitValue, setLimitValue] = useState(String(limit));
  const [permOpen, setPermOpen] = useState(false);
  const accepts = shown > 0 && g1?.accepts?.length ? acceptsText(g1.accepts).replace(/^accepts /, "") : null;

  const pick = (n: 0 | 1 | 2) => {
    if (n === 2 && level < 2 && !recommended && p.pendingLevel?.level !== 2) return setAskLevel2(true);
    p.onLevel(n);
  };
  const setLimit = () => {
    const v = Number(limitValue.replace(",", "."));
    if (v > 0) p.onLimit(v);
  };

  return (
    <section className="sec" id="autonomy">
      <div className="sec-h">
        <h2>What may I order without you?</h2>
        <span className="spacer" />
        <Button variant="link" className="text-[13px]" onClick={() => navigate("activity")}>See what I decided</Button>
      </div>

      <div className={`auto-now ${p.pendingLevel ? "changed" : ""}`}>
        <span className="ic"><Icon name="hand" /></span>
        <div className="grid gap-0.5">
          <b>{LEVELS[shown]!.now(limit)}</b>
          <span className="muted text-[13px]">
            {shown > 0 ? "Your musts always apply." : "You can let me order alone once you trust me."}
            {accepts ? ` Small differences I accept: ${accepts}.` : ""}
          </span>
        </div>
        <Button small onClick={() => { setLimitValue(String(limit)); setOpen((o) => !o); }}>{open ? "Close" : "Change"}</Button>
      </div>

      {open && (
        <div className="auto-opts">
          <div className="grid gap-2" role="radiogroup" aria-label="What I may order alone">
            {LEVELS.map((l) => (
              <button key={l.n} type="button" role="radio" aria-checked={shown === l.n} className="lvl" onClick={() => pick(l.n)}>
                <span className="radio" />
                <span className="grid gap-0.5">
                  <b>{l.name}{l.n === level ? <span className="muted font-normal"> · now</span> : null}</b>
                  <span className="muted text-[13px]">{l.text(limit)}</span>
                  {l.n === 2 && level < 2 && !recommended && <span className="rec">I suggest this after {needed} orders without a correction.</span>}
                </span>
              </button>
            ))}
          </div>
          {shown > 0 && (
            <form className="frow" onSubmit={(e) => { e.preventDefault(); setLimit(); }}>
              <span className="text-sm">Up to €</span>
              <input className="field" style={{ width: 110 }} inputMode="decimal" value={limitValue} onChange={(e) => setLimitValue(e.target.value)} aria-label="Limit per order in euro" />
              <span className="text-sm">an order</span>
              <Button type="submit" small disabled={Number(limitValue.replace(",", ".")) === limit}>Set limit</Button>
            </form>
          )}
        </div>
      )}

      {level < 2 && (
        <p className="auto-hint">
          {clean} of {needed} orders went through without a correction. After {needed} I'll suggest letting me do more.
        </p>
      )}

      <details className="auto-more" open={permOpen || p.extras.some((x) => x.grant.id && (p.freshGrants.has(x.grant.id) || p.changedGrants.has(x.grant.id))) || undefined}>
        <summary>Extra permissions{p.extras.length ? ` (${p.extras.length})` : ""}</summary>
        <p className="muted text-[12.5px]">On top of the above: products I may order alone with their own limit. Your musts still apply.</p>
        {p.extras.map(({ grant: g, inMandate, summary, source }) => {
          const cls = [g.enabled === false ? "off" : "", g.id && p.freshGrants.has(g.id) ? "fresh rise" : g.id && p.changedGrants.has(g.id) ? "changed" : ""].join(" ");
          return (
            <div key={g.id} className={`grant ${cls}`}>
              <div>
                <b>{g.label || "Permission"}</b>
                <div className="mt-0.5 text-[12.5px] text-ink3">
                  {inMandate ? `${limitText(g.limits)} · ${acceptsText(g.accepts)}` : summary}{source ? ` · ${source}` : ""}
                </div>
              </div>
              <Toggle
                checked={g.enabled !== false}
                onChange={() => (inMandate ? p.onToggleGrant(g) : p.locked("This permission is only an example and can't be changed yet."))}
                label={`Permission on: ${g.label ?? ""}`}
              />
            </div>
          );
        })}
        {permOpen ? (
          <PermForm musts={p.musts} levelLimit={limit} onAdd={(g) => { p.onAddGrant(g); setPermOpen(false); }} />
        ) : (
          <Button variant="link" className="justify-self-start text-[13px]" onClick={() => setPermOpen(true)}>+ Add permission</Button>
        )}
      </details>

      <Modal open={askLevel2} onClose={() => setAskLevel2(false)} width={560} label="Let me order more alone">
        <h2>Let me order anything within your rules?</h2>
        <p className="text-ink3">{clean} of {needed} orders have gone through without a correction so far. After {needed} I'd suggest this myself. You can still do it now.</p>
        <DiffList
          lines={[
            { kind: "add", text: "I may then order any product that passes your musts, not only your usual ones" },
            { kind: "chg", text: `Unchanged: every must still applies, the ${eur(limit)} limit still holds` },
          ]}
        />
        <p className="text-[13px] text-ink3">You confirm the change before it applies, and you can go back at any time.</p>
        <div className="flex flex-wrap justify-end gap-2.5">
          <Button onClick={() => setAskLevel2(false)}>Not yet</Button>
          <Button variant="primary" onClick={() => { setAskLevel2(false); p.onLevel(2); }}>Yes, do it</Button>
        </div>
      </Modal>
    </section>
  );
}

type Basis = "order" | "line" | "unit";
type Acc = "q" | "d" | "p";
const BASIS: [Basis, string][] = [["order", "per order"], ["line", "per line"], ["unit", "per unit"]];
const ACCS: [Acc, string][] = [["q", "+5 % rounding"], ["d", "1 day late"], ["p", "a missed preference"]];

/** A sentence → `POST /mandate/draft {kind: "grant"}` (2d; mocked) → three fields → a pending grant. */
function PermForm({ musts, levelLimit, onAdd }: { musts: HardRule[]; levelLimit: number; onAdd: (g: Grant) => void }) {
  const [text, setText] = useState("Organic products up to €200 an order");
  const [draft, setDraft] = useState<DraftGrantResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [amount, setAmount] = useState("200");
  const [basis, setBasis] = useState<Basis>("order");
  const [acc, setAcc] = useState<Record<Acc, boolean>>({ q: false, d: false, p: false });

  const turn = async () => {
    setBusy(true);
    setError(null);
    try {
      const d = await draftGrant(text);
      setDraft(d);
      const l = d.grant.limits ?? {};
      if (l.max_line_total != null) { setBasis("line"); setAmount(String(l.max_line_total)); }
      else if (l.max_unit_price != null) { setBasis("unit"); setAmount(String(l.max_unit_price)); }
      else { setBasis("order"); setAmount(String(l.max_order_total ?? 200)); }
      const has = (c: string) => !!d.grant.accepts?.some((x) => x.check === c);
      setAcc({ q: has("quantity"), d: has("delivery_date"), p: has("preference") });
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  };

  const n = Number(amount.replace(",", "."));
  const save = () => {
    if (!draft || !(n > 0)) return;
    const limits: Grant["limits"] = basis === "order" ? { max_order_total: n } : basis === "line" ? { max_line_total: n } : { max_unit_price: n };
    const accepts: NonNullable<Grant["accepts"]> = [
      ...(acc.q ? [{ check: "quantity" as const, max_over_pct: 5 }] : []),
      ...(acc.d ? [{ check: "delivery_date" as const, max_days_late: 1 }] : []),
      ...(acc.p ? [{ check: "preference" as const }] : []),
    ];
    onAdd({ ...draft.grant, limits, accepts, enabled: true });
  };

  const cov = draft?.coverage;
  return (
    <div className="permform">
      <form className="frow" onSubmit={(e) => { e.preventDefault(); void turn(); }}>
        <input className="field wide" value={text} onChange={(e) => setText(e.target.value)} aria-label="Describe the permission" />
        <Button type="submit" variant={draft ? "ghost" : "primary"} small disabled={busy || !text.trim()}>{busy ? "Reading…" : "Turn into permission"}</Button>
      </form>
      {error && <div className="err">{error}</div>}
      {draft && (
        <>
          {draft.explanation && <p className="text-sm text-ink2">{draft.explanation}</p>}
          <div className="pf">
            <span>For these products</span>
            <div className="grid gap-1.5">
              <div className="flex flex-wrap items-center gap-1.5">
                {(draft.grant.scope ?? []).map((c, i) => <Cond key={i}>{condText(c)}</Cond>)}
                {!draft.grant.scope?.length && <Cond>any product</Cond>}
                {cov && (
                  <span className="muted text-[12.5px]">
                    matches {cov.matching.toLocaleString("en-GB")} of {cov.checked.toLocaleString("en-GB")} products
                    {cov.known === cov.checked ? " (label known for all)" : ` (label known for ${cov.known.toLocaleString("en-GB")})`}
                  </span>
                )}
              </div>
              {draft.examples.length > 0 && <span className="muted text-[12.5px]">e.g. {draft.examples.join(" · ")}</span>}
            </div>
          </div>
          <div className="pf">
            <span>Up to</span>
            <div className="frow">
              <input className="field" style={{ width: 90 }} inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} aria-label="Amount in euro" />
              <span>€</span>
              {BASIS.map(([k, l]) => <button key={k} type="button" className="tog" aria-pressed={basis === k} onClick={() => setBasis(k)}>{l}</button>)}
            </div>
          </div>
          <div className="pf">
            <span>I may also accept</span>
            <div className="frow">
              {ACCS.map(([k, l]) => <button key={k} type="button" className="tog" aria-pressed={acc[k]} onClick={() => setAcc((a) => ({ ...a, [k]: !a[k] }))}>{l}</button>)}
              {!Object.values(acc).some(Boolean) && <span className="muted text-[12.5px]">nothing: any deviation, I ask you</span>}
            </div>
          </div>
          <div className="pf">
            <span>Still always</span>
            <div className="flex flex-wrap gap-1.5">
              {musts.filter((m) => m.enabled !== false).map((m) => <Chip key={m.id} kind="must" icon="lock">{ruleLabel(m)}</Chip>)}
            </div>
          </div>
          <div className="frow">
            <span className="muted text-[13px]">
              {draft.grant.label || "These"} lines count against €{n > 0 ? n.toLocaleString("en-GB") : "?"} {basis === "order" ? `per order, separately from your €${levelLimit.toLocaleString("en-GB")} level limit` : basis === "line" ? "per line" : "per unit"}.
            </span>
            <span className="spacer" />
            <Button variant="primary" small disabled={!(n > 0)} onClick={save}>Add permission</Button>
          </div>
        </>
      )}
    </div>
  );
}
