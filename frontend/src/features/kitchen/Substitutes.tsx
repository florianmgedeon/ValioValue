import { useEffect, useState } from "react";
import { searchKitchenProducts, type KitchenCatalogProduct } from "../../api/kitchen";
import type { KitchenProfile, Schema } from "../../api/types";
import { navigate } from "../../app/route";
import { Button, Icon } from "../../ui";

type UsualProduct = Schema["UsualProduct"];
export type FallbackMode = NonNullable<UsualProduct["fallback_mode"]>;
export type SubstituteEntry = { product_id: string; product_name: string };
/** An unsigned change to one usual product's fallback, waiting in the change bar. */
export type FallbackDraft = { substitutes: SubstituteEntry[]; fallback_mode: FallbackMode };

const FALLBACK_LABELS: Record<FallbackMode, { short: string; desc: string }> = {
  only_listed: {
    short: "Only these substitutes",
    desc: "If it's out of stock, I try your substitutes in order and never take any other product.",
  },
  ask_any: {
    short: "Ask me before other products",
    desc: "I try your substitutes first; if none are in stock, I ask you before picking another product.",
  },
  none: {
    short: "Never substitute",
    desc: "Only this product may be ordered. If it's out of stock, I leave it open and tell you.",
  },
};

export const subsOf = (u: UsualProduct): SubstituteEntry[] =>
  (u.substitutes ?? [])
    .filter((s): s is { product_id: string; product_name?: string | null } => !!s.product_id)
    .map((s) => ({ product_id: s.product_id, product_name: s.product_name ?? s.product_id }));

export const modeOf = (u: UsualProduct): FallbackMode => u.fallback_mode ?? (subsOf(u).length > 0 ? "only_listed" : "ask_any");

const draftOf = (u: UsualProduct): FallbackDraft => ({ substitutes: subsOf(u), fallback_mode: modeOf(u) });

/** Anything other than the default ("ask me", no substitutes) is worth a line. */
export const hasRule = (d: FallbackDraft) => d.fallback_mode !== "ask_any" || d.substitutes.length > 0;

export function ruleText(d: FallbackDraft) {
  if (d.fallback_mode === "none") return "never substitute";
  if (!d.substitutes.length) return "ask me";
  const chain = d.substitutes.map((s, i) => `${i + 1}. ${s.product_name}`).join(" → ");
  return `${chain}${d.fallback_mode === "only_listed" ? " · nothing else" : " · then ask me"}`;
}

/** One line for a usual-product row in "What I know"; null for the default rule. */
export function fallbackLine(u: UsualProduct) {
  const d = draftOf(u);
  return hasRule(d) ? `If out: ${ruleText(d)}` : null;
}

const sameDraft = (a: FallbackDraft, b: FallbackDraft) =>
  a.fallback_mode === b.fallback_mode && a.substitutes.map((s) => s.product_id).join() === b.substitutes.map((s) => s.product_id).join();

/** Read-only, at the top of the usual-product drawer. Changing it is signed, so it links to "What I may do". */
export function FallbackSummary({ usual }: { usual: UsualProduct }) {
  const d = draftOf(usual);
  return (
    <div className="grid gap-2">
      <span className="eyebrow">If it's out of stock</span>
      <div className="fbcard">
        {d.fallback_mode === "none" ? (
          <div className="chain"><div>Never substitute. If it's out, I leave it open and tell you.</div></div>
        ) : (
          <div className="chain">
            <div><span className="sub-rank now">now</span>{usual.product_name ?? usual.word}</div>
            {d.substitutes.map((s, i) => <div key={s.product_id}><span className="sub-rank">#{i + 1}</span>{s.product_name}</div>)}
            <div className="muted text-[13px]">
              {d.fallback_mode === "only_listed" ? "Nothing else." : d.substitutes.length ? "Then I ask you before taking another product." : "I ask you before taking another product."}
            </div>
          </div>
        )}
        <div className="foot">
          <Icon name="lock" size={14} />
          <span>Signed rule · substitutes count as usual products</span>
          <span className="spacer" />
          <button type="button" className="link" onClick={() => navigate("kitchen", `may:${usual.id}`)}>Change</button>
        </div>
      </div>
    </div>
  );
}

/** "What I may do": what to take when a usual product is out of stock. Edits wait in the change bar and are
 *  signed with the other rule changes, because substitutes widen what I may order alone at level 1. */
export function SubstituteRules({ profile, drafts, onDraft, level, limit, focusId }: {
  profile: KitchenProfile;
  drafts: Record<string, FallbackDraft>;
  onDraft: (id: string, draft: FallbackDraft | null) => void;
  level: 0 | 1 | 2;
  limit: number | null;
  focusId?: string;
}) {
  const items = (profile.usual ?? []).filter((u) => (u.status ?? "active") === "active" && u.id);
  const [open, setOpen] = useState<string | null>(focusId ?? null);

  useEffect(() => {
    if (!focusId) return;
    setOpen(focusId);
    const t = setTimeout(() => document.getElementById(`fb-${focusId}`)?.scrollIntoView({ block: "center", behavior: "smooth" }), 60);
    return () => clearTimeout(t);
  }, [focusId]);

  if (!items.length) return null;
  const alone = level === 1
    ? "They count as usual products, so I order them alone like the rest."
    : level === 2
      ? "I order them alone when they pass your rules."
      : "I still ask you before every order.";
  return (
    <section className="sec" id="substitutes">
      <div className="sec-h">
        <h2>If a usual product is out of stock</h2>
        <p>I take your substitutes in this order. {alone}</p>
      </div>
      {items.map((u) => {
        const saved = draftOf(u);
        const d = drafts[u.id!] ?? saved;
        const changed = !!drafts[u.id!];
        const isOpen = open === u.id;
        const set = (next: FallbackDraft) => onDraft(u.id!, sameDraft(next, saved) ? null : next);
        const added = d.fallback_mode === "none" ? [] : d.substitutes.filter((s) => !saved.substitutes.some((x) => x.product_id === s.product_id));
        return (
          <div key={u.id} id={`fb-${u.id}`} className={`fbrow${changed ? " changed" : ""}`}>
            <span className="w">{u.word}</span>
            <div className="min-w-0">
              <div className="p">{u.product_name || <span className="muted">no product yet</span>}</div>
              <div className="r">
                {hasRule(d) ? <b>{ruleText(d)}</b> : "ask me (default)"}
                {changed && <span className="chg"> · changed, not signed</span>}
              </div>
            </div>
            <Button small onClick={() => setOpen(isOpen ? null : u.id!)}>{isOpen ? "Close" : "Change"}</Button>
            {isOpen && (
              <FallbackEditor
                usual={u}
                draft={d}
                onChange={set}
                widen={added.length > 0 && level === 1
                  ? `${added.length === 1 ? "This product" : "These products"} will count as usual: I may order ${added.length === 1 ? "it" : "them"} alone${limit != null ? `, up to €${limit.toLocaleString("en-GB")} an order` : ""}.`
                  : null}
              />
            )}
          </div>
        );
      })}
    </section>
  );
}

function FallbackEditor({ usual, draft, onChange, widen }: {
  usual: UsualProduct;
  draft: FallbackDraft;
  onChange: (d: FallbackDraft) => void;
  widen: string | null;
}) {
  const [query, setQuery] = useState(usual.word ?? "");
  const [hits, setHits] = useState<KitchenCatalogProduct[]>([]);
  const [searching, setSearching] = useState(false);
  const { substitutes: subs, fallback_mode: mode } = draft;

  useEffect(() => {
    if (!query.trim()) {
      setHits([]);
      return;
    }
    let cancelled = false;
    setSearching(true);
    const t = setTimeout(() => {
      void searchKitchenProducts(query.trim(), 6)
        .then((res) => { if (!cancelled) setHits(res.products ?? []); })
        .catch(() => { if (!cancelled) setHits([]); })
        .finally(() => { if (!cancelled) setSearching(false); });
    }, 180);
    return () => {
      cancelled = true;
      clearTimeout(t);
    };
  }, [query]);

  const setSubs = (next: SubstituteEntry[], nextMode = mode) => {
    let m = nextMode;
    if (m === "ask_any" && subs.length === 0 && next.length > 0) m = "only_listed";
    if (m === "only_listed" && next.length === 0) m = "ask_any";
    onChange({ substitutes: next, fallback_mode: m });
  };
  const move = (idx: number, dir: -1 | 1) => {
    const copy = [...subs];
    const [item] = copy.splice(idx, 1);
    copy.splice(idx + dir, 0, item!);
    setSubs(copy);
  };

  return (
    <div className="usual-edit">
      <div className="usual-edit-row">
        <span className="eyebrow">If {usual.word} is out of stock</span>
        <div className="frow">
          {(["only_listed", "ask_any", "none"] as FallbackMode[]).map((k) => (
            <button key={k} type="button" className="tog" aria-pressed={mode === k}
              onClick={() => onChange({ substitutes: k === "none" ? [] : subs, fallback_mode: k })}>
              {FALLBACK_LABELS[k].short}
            </button>
          ))}
        </div>
        <p className="muted text-[12.5px]">{FALLBACK_LABELS[mode].desc}</p>
      </div>

      {mode !== "none" && (
        <div className="usual-edit-row">
          <span className="eyebrow">Substitutes in order</span>
          {subs.length === 0 ? (
            <p className="muted text-[13px]">No substitutes yet. Pick one below so I know what to take instead.</p>
          ) : (
            <div className="grid gap-1.5">
              {subs.map((s, idx) => (
                <div key={s.product_id} className="sub-item">
                  <span className="sub-rank">#{idx + 1}</span>
                  <div className="min-w-0 flex-1"><b>{s.product_name}</b></div>
                  <div className="sub-acts">
                    <button type="button" className="sub-btn" disabled={idx === 0} onClick={() => move(idx, -1)} aria-label="Move up">↑</button>
                    <button type="button" className="sub-btn" disabled={idx === subs.length - 1} onClick={() => move(idx, 1)} aria-label="Move down">↓</button>
                    <button type="button" className="sub-btn del" onClick={() => setSubs(subs.filter((x) => x.product_id !== s.product_id))} aria-label="Remove substitute">×</button>
                  </div>
                </div>
              ))}
            </div>
          )}

          <div className="sub-search">
            <div className="frow">
              <Icon name="search" size={16} className="text-ink3" />
              <input
                className="field wide"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder={`Search Valio Aimo for "${usual.word}" substitutes…`}
                aria-label="Search catalog for substitute products"
              />
            </div>
            {searching && <div className="muted text-xs px-1">Searching…</div>}
            {!searching && hits.length > 0 && (
              <div className="sub-hits">
                {hits
                  .filter((h) => h.product_id !== usual.product_id && !subs.some((s) => s.product_id === h.product_id))
                  .slice(0, 4)
                  .map((h) => (
                    <div key={h.product_id} className="sub-hit">
                      <div className="min-w-0 font-medium text-[13.5px]">{h.name}</div>
                      <Button small variant="ghost" onClick={() => setSubs([...subs, { product_id: h.product_id, product_name: h.name }])}>
                        + Add as #{subs.length + 1}
                      </Button>
                    </div>
                  ))}
              </div>
            )}
          </div>
        </div>
      )}

      {widen && <div className="widen"><Icon name="up" size={15} /><span>{widen}</span></div>}
    </div>
  );
}
