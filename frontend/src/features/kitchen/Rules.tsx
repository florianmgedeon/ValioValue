import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { ApiError } from "../../api/client";
import { draftMandateRule, getPolicyCatalog } from "../../api/mandate";
import type { RuleCoverage } from "../../api/types";
import { Button, Cond, KindTag, Segmented, SignedTag, Toggle, Weight } from "../../ui";
import { condText, ruleLabel, type Condition, type HardRule, type Preference, type RuleGroup } from "./model";

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

interface CatalogAttr { attr: string; type?: string; description?: string; ops?: string[]; examples?: unknown[] }

/** The readable message of an API error: `detail` may be a string or `{error}`. */
export function errText(e: unknown): string {
  if (e instanceof ApiError) {
    const d = e.detail as { error?: string } | string | undefined;
    if (typeof d === "string") return d;
    if (d && typeof d === "object" && d.error) return d.error;
  }
  return (e as Error)?.message ?? String(e);
}

export function coverageText(c: RuleCoverage | undefined | null): string | null {
  if (!c || c.checked == null) return null;
  if (c.note) return c.note;
  return `label known for ${(c.known ?? 0).toLocaleString("en-GB")} of ${c.checked.toLocaleString("en-GB")}`;
}

export interface RowFlags { changed: Set<string>; fresh: Set<string> }

function rowCls(id: string | undefined, enabled: boolean | undefined, f: RowFlags) {
  return ["rule", enabled === false ? "off" : "", id && f.fresh.has(id) ? "new rise" : id && f.changed.has(id) ? "changed" : ""].join(" ");
}

export function MustSection({ rules, flags, coverage, onToggle, onUnknown, onAdd }: {
  rules: HardRule[];
  flags: RowFlags;
  coverage: Map<string, RuleCoverage>;
  onToggle: (r: HardRule) => void;
  onUnknown: (r: HardRule, v: "exclude" | "ask") => void;
  onAdd: (r: HardRule, label: string) => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <section className="sec">
      <div className="sec-h">
        <KindTag kind="must" />
        <h2>Must</h2>
        <SignedTag />
        <span className="spacer" />
        <Button variant="link" className="text-[13px]" onClick={() => setOpen((o) => !o)}>{open ? "Close" : "+ Add rule"}</Button>
        <p>Never broken. Valio Aimo blocks any line that fails one, whatever I'm told.</p>
      </div>
      {rules.map((r) => {
        const cov = coverageText(r.id ? coverage.get(r.id) : undefined);
        return (
          <div key={r.id} className={rowCls(r.id, r.enabled, flags)}>
            <div className="grid gap-1">
              <div className="t">{ruleLabel(r)}</div>
              {r.source && r.source !== "mandate" && <div className="s"><span>{r.source}</span></div>}
              <div className="s">
                {cov && <span>{cov} ·</span>}
                <span>if unknown</span>
                <Segmented
                  label="If the data is unknown"
                  value={r.if_unknown ?? "exclude"}
                  options={[{ value: "exclude", label: "exclude" }, { value: "ask", label: "ask me" }]}
                  onChange={(v) => onUnknown(r, v)}
                />
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
        <KindTag kind="prefer" />
        <h2>Prefer</h2>
        <SignedTag />
        <span className="spacer" />
        <Button variant="link" className="text-[13px]" onClick={() => setOpen((o) => !o)}>{open ? "Close" : "+ Add preference"}</Button>
        <p>Ranks products. Missing one never blocks. At level 2 I may accept a miss alone.</p>
      </div>
      {rules.length === 0 && !open && <div className="rule"><span className="muted text-sm">No preferences yet. I rank by price.</span></div>}
      {rules.map((r) => (
        <div key={r.id} className={rowCls(r.id, r.enabled, flags)}>
          <div className="grid gap-1">
            <div className="t">{ruleLabel(r)}</div>
            {(r as { source?: string }).source && (r as { source?: string }).source !== "mandate" && <div className="s"><span>{(r as { source?: string }).source}</span></div>}
          </div>
          <div className="flex items-center gap-3">
            <Weight value={r.weight ?? 1} onChange={(w) => onWeight(r, w)} />
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

/** A sentence → `POST /mandate/draft` → the rule, explained, with coverage. 422 → the message and the manual form. */
function AddRule({ group, initial, onAdd }: { group: RuleGroup; initial: string; onAdd: (rule: HardRule | Preference, label: string) => void }) {
  const [text, setText] = useState(initial);
  const [draft, setDraft] = useState<RuleDraft | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [manual, setManual] = useState(false);

  const turn = async () => {
    if (!text.trim()) return;
    setBusy(true);
    setError(null);
    setDraft(null);
    try {
      setDraft((await draftMandateRule({ text, kind: group })) as unknown as RuleDraft);
      setManual(false);
    } catch (e) {
      setError(errText(e));
      setManual(true);
    } finally {
      setBusy(false);
    }
  };

  const cov = draft?.coverage;
  return (
    <div className="addrule">
      <div className="eyebrow">{group === "must" ? "New rule" : "New preference"}</div>
      <form className="frow" onSubmit={(e) => { e.preventDefault(); void turn(); }}>
        <input
          className="field wide"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={group === "must" ? "No sesame, not even traces" : "Finnish ingredients first"}
          aria-label={group === "must" ? "Describe the rule" : "Describe the preference"}
        />
        <Button type="submit" variant={draft ? "ghost" : "primary"} small disabled={busy || !text.trim()}>
          {busy ? "Reading…" : group === "must" ? "Turn into rule" : "Turn into preference"}
        </Button>
      </form>
      {error && <div className="err">{error}</div>}
      {draft && (
        <div className="grid gap-2">
          <div className="t font-semibold">{draft.label || ruleLabel(draft.rule)}</div>
          <div className="frow"><Cond>{condText(draft.rule)}</Cond>{"if_unknown" in draft.rule && <span className="muted text-[13px]">if unknown: {draft.rule.if_unknown === "ask" ? "ask me" : "exclude"}</span>}{"weight" in draft.rule && <span className="muted text-[13px]">weight {draft.rule.weight}</span>}</div>
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
                Checked against {cov.checked.toLocaleString("en-GB")} products · label known for {(cov.known ?? 0).toLocaleString("en-GB")}
                {cov.excluded ? ` · ${cov.excluded.toLocaleString("en-GB")} excluded` : ""}
              </span>
            )}
            <span className="spacer" />
            <Button variant="primary" small onClick={() => onAdd(draft.rule, draft.label || ruleLabel(draft.rule))}>
              {group === "must" ? "Add rule" : "Add preference"}
            </Button>
          </div>
        </div>
      )}
      {!draft && !manual && <Button variant="link" className="justify-self-start text-[13px]" onClick={() => setManual(true)}>Or fill in the form</Button>}
      {manual && <ManualRule group={group} onAdd={onAdd} />}
    </div>
  );
}

function parseValue(raw: string, op: string, type?: string): unknown {
  const one = (v: string): unknown => (type === "number" && v.trim() !== "" && !Number.isNaN(Number(v)) ? Number(v) : type === "boolean" ? v.trim() === "true" : v.trim());
  if (op === "in" || op === "not_in") return raw.split(",").map(one).filter((v) => v !== "");
  return one(raw);
}

/** attr / op / value from `GET /policy-catalog`. */
function ManualRule({ group, onAdd }: { group: RuleGroup; onAdd: (rule: HardRule | Preference, label: string) => void }) {
  const catalog = useQuery({ queryKey: ["policy-catalog"], queryFn: getPolicyCatalog, staleTime: Infinity });
  const attrs = ((catalog.data?.attributes as CatalogAttr[] | undefined) ?? []).filter((a) => a.ops?.length);
  const [attr, setAttr] = useState("allergens");
  const [op, setOp] = useState("not_contains");
  const [value, setValue] = useState("");
  const [unknown, setUnknown] = useState<"exclude" | "ask">("exclude");
  const [weight, setWeight] = useState(2);
  const a = attrs.find((x) => x.attr === attr) ?? attrs[0];
  const ops = a?.ops ?? [];
  const curOp = ops.includes(op) ? op : ops[0] ?? op;

  if (catalog.isLoading) return <span className="muted text-[13px]">Loading the attributes…</span>;
  if (catalog.isError) return <div className="err">Could not load the attributes: {errText(catalog.error)}</div>;

  const add = () => {
    const cond = { attr: a?.attr ?? attr, op: curOp, value: parseValue(value, curOp, a?.type) } as Condition;
    const label = condText(cond);
    const rule = group === "must"
      ? ({ ...cond, label, if_unknown: unknown, enabled: true } as HardRule)
      : ({ ...cond, label, weight, enabled: true } as Preference);
    onAdd(rule, label);
  };

  return (
    <div className="grid gap-2.5">
      <div className="frow">
        <select className="field" aria-label="Attribute" value={a?.attr ?? attr} onChange={(e) => setAttr(e.target.value)}>
          {attrs.map((x) => <option key={x.attr} value={x.attr} title={x.description}>{x.attr}</option>)}
        </select>
        <select className="field" aria-label="Operator" value={curOp} onChange={(e) => setOp(e.target.value)}>
          {ops.map((o) => <option key={o}>{o}</option>)}
        </select>
        <input
          className="field"
          style={{ width: 140 }}
          aria-label="Value"
          value={value}
          list={`k-ex-${a?.attr}`}
          placeholder={a?.examples?.length ? String(a.examples[0]) : "value"}
          onChange={(e) => setValue(e.target.value)}
        />
        <datalist id={`k-ex-${a?.attr}`}>{(a?.examples ?? []).map((x) => <option key={String(x)} value={String(x)} />)}</datalist>
        {group === "must" ? (
          <>
            <span className="muted text-[13px]">if unknown</span>
            <Segmented value={unknown} options={[{ value: "exclude", label: "exclude" }, { value: "ask", label: "ask me" }]} onChange={setUnknown} label="If unknown" />
          </>
        ) : (
          <Weight value={weight} onChange={setWeight} />
        )}
      </div>
      {a?.description && <span className="muted text-[12.5px]">{a.description}</span>}
      <div className="frow">
        <span className="spacer" />
        <Button variant="primary" small disabled={!value.trim()} onClick={add}>{group === "must" ? "Add rule" : "Add preference"}</Button>
      </div>
    </div>
  );
}
