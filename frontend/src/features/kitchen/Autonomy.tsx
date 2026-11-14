import { useState } from "react";
import { draftGrant } from "../../api/mandate";
import type { AutonomyView, DraftGrantResponse } from "../../api/types";
import { navigate } from "../../app/route";
import { Button, Chip, Cond, Icon, KindTag, Modal, SignedTag, Toggle, DiffList } from "../../ui";
import { acceptsText, condText, limitText, ruleLabel, scopeText, type Grant, type HardRule, type LevelChange } from "./model";
import { errText } from "./Rules";

export const LEVELS: { n: 0 | 1 | 2 | 3; name: string; text: (limit: number) => string }[] = [
  { n: 0, name: "Suggest", text: () => "I prepare, you approve every order" },
  { n: 1, name: "Within your rules", text: (l) => `I order alone whatever passes every must, no deviations, up to €${l.toLocaleString("en-GB")}` },
  { n: 2, name: "Small deviations", text: () => "Also pack rounding up to +5 %, 1 day late, a missed preference" },
  { n: 3, name: "Standing orders", text: () => "I reorder your usual order on a schedule" },
];

export interface AutonomyProps {
  autonomy: AutonomyView;
  mandateVersion: number;
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

export function AutonomySection(p: AutonomyProps) {
  const { autonomy: a, g1 } = p;
  const level = a.level ?? 0;
  const clean = a.clean_orders ?? 0;
  const needed = a.needed ?? 10;
  const limit = p.pendingLevel?.max_order_total ?? g1?.limits?.max_order_total ?? 600;
  const recommended = a.recommended_next ?? a.offer_next ?? false;
  const [askLevel2, setAskLevel2] = useState(false);
  const [limitOpen, setLimitOpen] = useState(false);
  const [limitValue, setLimitValue] = useState(String(limit));
  const [permOpen, setPermOpen] = useState(false);
  const shown = p.pendingLevel?.level ?? level;

  const pick = (n: 0 | 1 | 2 | 3) => {
    if (n === 3) return p.locked("Standing orders are not built yet.");
    if (n === 2 && level < 2 && !recommended && p.pendingLevel?.level !== 2) return setAskLevel2(true);
    p.onLevel(n);
  };

  return (
    <section className="sec" id="autonomy">
      <div className="sec-h">
        <KindTag kind="auto" />
        <h2>How autonomous I am</h2>
        <SignedTag />
        <span className="spacer" />
        <Button variant="link" className="text-[13px]" onClick={() => navigate("activity")}>See what I decided</Button>
        <p>I only ever order what passes every must. The level decides how many small deviations I may accept without you. You decide every step up.</p>
      </div>
      <div className="ladder" role="radiogroup" aria-label="Autonomy level">
        {LEVELS.map((l) => {
          const cur = l.n === level;
          const pend = p.pendingLevel?.level === l.n && !cur;
          const lock = l.n === 3;
          const tag = cur ? "CURRENT" : pend ? "TO SIGN" : lock ? "NOT YET" : "";
          return (
            <button key={l.n} type="button" role="radio" aria-checked={cur} className={`rung ${cur ? "cur" : ""} ${pend ? "pend" : ""} ${lock ? "locked" : ""}`} onClick={() => pick(l.n)}>
              <span className="lv">LEVEL {l.n}{tag ? ` · ${tag}` : ""}</span>
              <b>{l.name}</b>
              <span>{l.text(limit)}</span>
              {l.n === 2 && level < 2 && !recommended && <span className="rec">Recommended after {needed} clean orders</span>}
            </button>
          );
        })}
      </div>
      <div className="progress">
        {level < 2 ? (
          <>
            <div>{clean} of {needed} orders without a correction. At {needed} I'll suggest level 2. You can switch earlier yourself.</div>
            <div className="track"><i style={{ width: `${Math.min(100, (clean / needed) * 100)}%` }} /></div>
          </>
        ) : (
          <div>Level 2 is on. Every deviation I accept alone is marked in Activity, so you can see what I decided.</div>
        )}
      </div>

      {shown > 0 ? (
        <div className={`grant ${p.pendingLevel ? "changed" : ""}`}>
          <span className="gid"><Icon name="check" size={12} /></span>
          <div>
            <b>{shown >= 2 ? "Within your rules + small deviations" : "Within your rules"}</b>
            <div className="muted text-[12.5px]">
              {shown >= 2 ? "any product that passes every must" : scopeText(g1?.scope)} · up to €{limit.toLocaleString("en-GB")} an order · {g1 ? acceptsText(g1.accepts) : "no deviations"}
            </div>
            {limitOpen && (
              <form
                className="frow mt-2"
                onSubmit={(e) => {
                  e.preventDefault();
                  const v = Number(limitValue.replace(",", "."));
                  if (v > 0) {
                    p.onLimit(v);
                    setLimitOpen(false);
                  }
                }}
              >
                <span className="text-sm">Up to €</span>
                <input className="field" style={{ width: 110 }} inputMode="decimal" value={limitValue} onChange={(e) => setLimitValue(e.target.value)} aria-label="Limit per order in euro" autoFocus />
                <span className="text-sm">an order</span>
                <Button type="submit" variant="primary" small>Set limit</Button>
              </form>
            )}
          </div>
          <Button variant="link" className="text-[13px]" onClick={() => { setLimitValue(String(limit)); setLimitOpen((o) => !o); }}>{limitOpen ? "Cancel" : "Change limit"}</Button>
        </div>
      ) : (
        <div className="grant">
          <span className="gid">—</span>
          <div><b>Nothing alone</b><div className="muted text-[12.5px]">I prepare every order, you approve every order</div></div>
        </div>
      )}

      <div className="perm-h">
        <b>Extra permissions</b>
        <span className="spacer" />
        <Button variant="link" className="text-[13px]" onClick={() => setPermOpen((o) => !o)}>{permOpen ? "Close" : "+ Add permission"}</Button>
        <p>On top of your level: products I may order alone with their own limit. Musts still block, whatever a permission says.</p>
      </div>
      {p.extras.map(({ grant: g, inMandate, summary, source }) => {
        const cls = [g.enabled === false ? "off" : "", g.id && p.freshGrants.has(g.id) ? "fresh rise" : g.id && p.changedGrants.has(g.id) ? "changed" : ""].join(" ");
        return (
          <div key={g.id} className={`grant ${cls}`}>
            <span className="gid x"><Icon name="check" size={12} /></span>
            <div>
              <b>{g.label || g.id}</b>
              <div className="mt-0.5 flex flex-wrap items-center gap-2 text-[12.5px] text-ink3">
                {inMandate ? (
                  <>
                    <span>{limitText(g.limits)} · {acceptsText(g.accepts)}{source ? ` · ${source}` : ""}</span>
                  </>
                ) : (
                  <span>{summary}{source ? ` · ${source}` : ""} · example</span>
                )}
              </div>
            </div>
            {inMandate ? (
              <Toggle checked={g.enabled !== false} onChange={() => p.onToggleGrant(g)} label={`Permission on: ${g.label ?? g.id}`} />
            ) : (
              <Toggle checked={g.enabled !== false} onChange={() => p.locked("This permission is only an example and can't be changed yet.")} label={`Permission on: ${g.label ?? g.id}`} />
            )}
          </div>
        );
      })}
      {p.extras.length === 0 && !permOpen && <div className="grant"><span className="gid x">—</span><span className="muted text-[13px]">None yet.</span><span /></div>}
      {permOpen && (
        <PermForm
          musts={p.musts}
          levelLimit={limit}
          onAdd={(g) => {
            p.onAddGrant(g);
            setPermOpen(false);
          }}
        />
      )}

      <Modal open={askLevel2} onClose={() => setAskLevel2(false)} width={600} label="Level 2 recommendation">
        <div className="eyebrow" style={{ color: "var(--auto)" }}>Level 2 · Small deviations</div>
        <h2>We recommend waiting a little.</h2>
        <p className="text-ink3">{clean} of {needed} orders have gone through without a correction so far. After {needed} we know your kitchen well enough to suggest this step. You can still unlock it now.</p>
        <DiffList
          lines={[
            { kind: "add", text: "I may then accept alone: pack rounding up to +5 %" },
            { kind: "add", text: "a delivery 1 day later than asked" },
            { kind: "add", text: "a product that misses a preference (e.g. not Finnish)" },
            { kind: "chg", text: `Unchanged: every must still blocks, the €${limit.toLocaleString("en-GB")} limit still holds` },
          ]}
        />
        <p className="text-[13px] text-ink3">Like every rule change, you confirm it first. You can go back to level 1 at any time.</p>
        <div className="flex flex-wrap justify-end gap-2.5">
          <Button onClick={() => setAskLevel2(false)}>Keep level {level}</Button>
          <Button variant="primary" onClick={() => { setAskLevel2(false); p.onLevel(2); }}>Unlock anyway</Button>
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
