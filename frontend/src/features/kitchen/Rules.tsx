import { useState } from "react";
import { ApiError } from "../../api/client";
import { draftMandateRule } from "../../api/mandate";
import type { RuleCoverage } from "../../api/types";
import { Button, Toggle } from "../../ui";
import { ruleLabel, type HardRule, type Preference, type RuleGroup } from "./model";

/** `POST /mandate/draft` answer for a must or prefer. */
interface RuleDraft {
  kind: RuleGroup;
  rule: HardRule | Preference;
  label?: string;
  explanation?: string;
  product_type?: string | null;
  scope?: { category: string; products?: number; examples?: string[] }[] | null;
  coverage?: RuleCoverage | null;
}

/** The readable message of an API error: `detail` may be a string or `{error}`. */
export function errText(e: unknown): string {
  if (e instanceof ApiError) {
    const d = e.detail as { error?: string } | string | undefined;
    if (typeof d === "string") return d;
    if (d && typeof d === "object" && d.error) return d.error;
  }
  return (e as Error)?.message ?? String(e);
}

export interface RowFlags { changed: Set<string>; fresh: Set<string> }

function rowCls(id: string | undefined, enabled: boolean | undefined, f: RowFlags) {
  return ["rule", enabled === false ? "off" : "", id && f.fresh.has(id) ? "new rise" : id && f.changed.has(id) ? "changed" : ""].join(" ");
}

const IMPORTANCE = [{ value: 1, label: "Nice to have" }, { value: 2, label: "Important" }, { value: 3, label: "Very important" }];

export function MustSection({ rules, flags, onToggle, onUnknown, onAdd }: {
  rules: HardRule[];
  flags: RowFlags;
  onToggle: (r: HardRule) => void;
  onUnknown: (r: HardRule, v: "exclude" | "ask") => void;
  onAdd: (r: HardRule, label: string) => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <section className="sec">
      <div className="sec-h">
        <h2>Never order</h2>
        <span className="spacer" />
        <Button variant="link" className="text-[13px]" onClick={() => setOpen((o) => !o)}>{open ? "Close" : "+ Add"}</Button>
        <p>Valio Aimo blocks every product that breaks one of these, whatever I'm told.</p>
      </div>
      {rules.length === 0 && !open && <div className="rule"><span className="muted text-sm">Nothing yet.</span></div>}
      {rules.map((r) => {
        const ask = r.if_unknown === "ask";
        return (
          <div key={r.id} className={rowCls(r.id, r.enabled, flags)}>
            <div className="grid gap-1">
              <div className="t">{ruleLabel(r)}</div>
              <div className="s">
                <span>If the label doesn't say: {ask ? "I ask you" : "I leave it out"}</span>
                <button type="button" className="link" onClick={() => onUnknown(r, ask ? "exclude" : "ask")}>change</button>
              </div>
            </div>
            <Toggle checked={r.enabled !== false} onChange={() => onToggle(r)} label={`Rule on: ${ruleLabel(r)}`} />
          </div>
        );
      })}
      {open && (
        <AddRule
          group="must"
          initial="No sesame, not even traces"
          onAdd={(rule, label) => {
            onAdd(rule as HardRule, label);
            setOpen(false);
          }}
        />
      )}
    </section>
  );
}

export function PreferSection({ rules, flags, onToggle, onWeight, onAdd }: {
  rules: Preference[];
  flags: RowFlags;
  onToggle: (r: Preference) => void;
  onWeight: (r: Preference, w: number) => void;
  onAdd: (r: Preference, label: string) => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <section className="sec">
      <div className="sec-h">
        <h2>Prefer</h2>
        <span className="spacer" />
        <Button variant="link" className="text-[13px]" onClick={() => setOpen((o) => !o)}>{open ? "Close" : "+ Add"}</Button>
        <p>I pick these first when I can. Missing one never blocks an order.</p>
      </div>
      {rules.length === 0 && !open && <div className="rule"><span className="muted text-sm">Nothing yet. I pick the cheapest.</span></div>}
      {rules.map((r) => (
        <div key={r.id} className={rowCls(r.id, r.enabled, flags)}>
          <div className="t">{ruleLabel(r)}</div>
          <div className="flex items-center gap-3">
            <select className="field" aria-label={`How important: ${ruleLabel(r)}`} value={r.weight ?? 1} onChange={(e) => onWeight(r, Number(e.target.value))}>
              {IMPORTANCE.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
            <Toggle checked={r.enabled !== false} onChange={() => onToggle(r)} label={`Preference on: ${ruleLabel(r)}`} />
          </div>
        </div>
      ))}
      {open && (
        <AddRule
          group="prefer"
          initial=""
          onAdd={(rule, label) => {
            onAdd(rule as Preference, label);
            setOpen(false);
          }}
        />
      )}
    </section>
  );
}

/** A sentence → `POST /mandate/draft` → the rule, explained, with how many products it would leave out. */
function AddRule({ group, initial, onAdd }: { group: RuleGroup; initial: string; onAdd: (rule: HardRule | Preference, label: string) => void }) {
  const [text, setText] = useState(initial);
  const [draft, setDraft] = useState<RuleDraft | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const turn = async () => {
    if (!text.trim()) return;
    setBusy(true);
    setError(null);
    setDraft(null);
    try {
      setDraft((await draftMandateRule({ text, kind: group })) as unknown as RuleDraft);
    } catch (e) {
      setError(`I couldn't turn that into a ${group === "must" ? "rule" : "preference"}. Try saying it differently, e.g. “${group === "must" ? "No sesame, not even traces" : "Organic dairy first"}”. (${errText(e)})`);
    } finally {
      setBusy(false);
    }
  };

  const cov = draft?.coverage;
  return (
    <div className="addrule">
      <div className="eyebrow">{group === "must" ? "Never order" : "Prefer"}</div>
      <form className="frow" onSubmit={(e) => { e.preventDefault(); void turn(); }}>
        <input
          className="field wide"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={group === "must" ? "No sesame, not even traces" : "Finnish ingredients first"}
          aria-label={group === "must" ? "Describe the rule" : "Describe the preference"}
        />
        <Button type="submit" variant={draft ? "ghost" : "primary"} small disabled={busy || !text.trim()}>
          {busy ? "Reading…" : "Check"}
        </Button>
      </form>
      {error && <div className="err">{error}</div>}
      {draft && (
        <div className="grid gap-2">
          <div className="t font-semibold">{draft.label || ruleLabel(draft.rule)}</div>
          {draft.explanation && <p className="text-sm text-ink2">{draft.explanation}</p>}
          {draft.scope?.length ? (
            <div className="muted text-[12.5px]">
              Applies to {draft.scope.map((s) => s.category).join(", ")}
              {draft.scope.some((s) => s.examples?.length) && <> · e.g. {draft.scope.flatMap((s) => s.examples ?? []).slice(0, 3).join(" · ")}</>}
            </div>
          ) : null}
          <div className="frow">
            {cov && cov.checked != null && (
              <span className="muted text-[13px]">
                {cov.excluded ? `Would leave out ${cov.excluded.toLocaleString("en-GB")} of ${cov.checked.toLocaleString("en-GB")} products` : `Checked against ${cov.checked.toLocaleString("en-GB")} products`}
              </span>
            )}
            <span className="spacer" />
            <Button variant="primary" small onClick={() => onAdd(draft.rule, draft.label || ruleLabel(draft.rule))}>
              {group === "must" ? "Add rule" : "Add preference"}
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
