import { useEffect, useState } from "react";
import { searchKitchenProducts, type KitchenCatalogProduct } from "../../api/kitchen";
import type { Schema } from "../../api/types";
import { Button, Icon } from "../../ui";

type UsualProduct = Schema["UsualProduct"];
export type FallbackMode = NonNullable<UsualProduct["fallback_mode"]>;
export type SubstituteEntry = { product_id: string; product_name: string };
export type UsualFallbackUpdate = { id: string; substitutes: SubstituteEntry[]; fallback_mode: FallbackMode };

const FALLBACK_LABELS: Record<FallbackMode, { short: string; desc: string }> = {
  only_listed: {
    short: "Only take these substitutes",
    desc: "If #1 is out of stock, I try your substitutes in order and never fall back to any other product.",
  },
  ask_any: {
    short: "Ask me before other products",
    desc: "I try your substitutes first; if none are in stock, I ask you before picking another product.",
  },
  none: {
    short: "Never substitute",
    desc: "Only your primary product may be ordered. If it's out of stock, I leave it open.",
  },
};

const subsOf = (u: UsualProduct): SubstituteEntry[] =>
  (u.substitutes ?? [])
    .filter((s): s is { product_id: string; product_name?: string | null } => !!s.product_id)
    .map((s) => ({ product_id: s.product_id, product_name: s.product_name ?? s.product_id }));

const modeOf = (u: UsualProduct): FallbackMode => u.fallback_mode ?? (subsOf(u).length > 0 ? "only_listed" : "ask_any");

/** One line for the usual-product row: what happens when the product is out of stock. */
export function fallbackSentence(u: UsualProduct) {
  const subs = subsOf(u);
  const mode = modeOf(u);
  if (mode === "none") return "If out of stock: never substitute";
  if (!subs.length) return "If out of stock: I ask you";
  return `If out of stock: ${subs.map((s, i) => `${i + 1}. ${s.product_name}`).join(" → ")}${mode === "only_listed" ? " · no other products" : ""}`;
}

/** Ordered substitutes and the fallback policy for one usual product. Lives in the usual-product drawer. */
export function FallbackEditor({ usual, onSave }: { usual: UsualProduct; onSave: (update: UsualFallbackUpdate) => Promise<void> }) {
  const [editing, setEditing] = useState(false);
  const [subs, setSubs] = useState<SubstituteEntry[]>(() => subsOf(usual));
  const [mode, setMode] = useState<FallbackMode>(() => modeOf(usual));
  const [query, setQuery] = useState(usual.word ?? "");
  const [hits, setHits] = useState<KitchenCatalogProduct[]>([]);
  const [searching, setSearching] = useState(false);
  const [saving, setSaving] = useState(false);

  const start = () => {
    setSubs(subsOf(usual));
    setMode(modeOf(usual));
    setQuery(usual.word ?? "");
    setEditing(true);
  };

  useEffect(() => {
    if (!editing || !query.trim()) {
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
  }, [editing, query]);

  const addSub = (p: KitchenCatalogProduct) => {
    if (p.product_id === usual.product_id || subs.some((s) => s.product_id === p.product_id)) return;
    setSubs([...subs, { product_id: p.product_id, product_name: p.name }]);
    if (mode === "ask_any" && subs.length === 0) setMode("only_listed");
  };

  const moveSub = (idx: number, dir: -1 | 1) => {
    const target = idx + dir;
    if (target < 0 || target >= subs.length) return;
    const copy = [...subs];
    const [item] = copy.splice(idx, 1);
    copy.splice(target, 0, item!);
    setSubs(copy);
  };

  const removeSub = (pid: string) => {
    const next = subs.filter((s) => s.product_id !== pid);
    setSubs(next);
    if (next.length === 0 && mode === "only_listed") setMode("ask_any");
  };

  if (!editing) {
    return (
      <div className="usual-edit">
        <div className="usual-edit-row">
          <span className="eyebrow">If {usual.product_name ?? usual.word} is out of stock</span>
          <p className="text-[14px]">{fallbackSentence(usual).replace(/^If out of stock: /, "")}</p>
        </div>
        <div className="frow">
          <Button small onClick={start}>Change substitutes</Button>
        </div>
      </div>
    );
  }

  return (
    <div className="usual-edit">
      <div className="usual-edit-row">
        <span className="eyebrow">If {usual.product_name ?? usual.word} is out of stock</span>
        <div className="frow">
          {(["only_listed", "ask_any", "none"] as FallbackMode[]).map((k) => (
            <button key={k} type="button" className="tog" aria-pressed={mode === k} onClick={() => setMode(k)}>
              {FALLBACK_LABELS[k].short}
            </button>
          ))}
        </div>
        <p className="muted text-[12.5px]">{FALLBACK_LABELS[mode].desc}</p>
      </div>

      {mode !== "none" && (
        <div className="usual-edit-row">
          <span className="eyebrow">Approved substitutes in order</span>
          {subs.length === 0 ? (
            <p className="muted text-[13px]">No substitutes added yet. Pick one below so I know what to take instead.</p>
          ) : (
            <div className="grid gap-1.5">
              {subs.map((s, idx) => (
                <div key={s.product_id} className="sub-item">
                  <span className="sub-rank">#{idx + 1}</span>
                  <div className="min-w-0 flex-1"><b>{s.product_name}</b></div>
                  <div className="sub-acts">
                    <button type="button" className="sub-btn" disabled={idx === 0} onClick={() => moveSub(idx, -1)} aria-label="Move up">↑</button>
                    <button type="button" className="sub-btn" disabled={idx === subs.length - 1} onClick={() => moveSub(idx, 1)} aria-label="Move down">↓</button>
                    <button type="button" className="sub-btn del" onClick={() => removeSub(s.product_id)} aria-label="Remove substitute">×</button>
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
                      <Button small variant="ghost" onClick={() => addSub(h)}>+ Add as #{subs.length + 1}</Button>
                    </div>
                  ))}
              </div>
            )}
          </div>
        </div>
      )}

      <div className="frow justify-end pt-1">
        <Button small variant="ghost" onClick={() => setEditing(false)}>Cancel</Button>
        <Button
          small
          variant="primary"
          disabled={saving || (mode === "only_listed" && subs.length === 0)}
          onClick={async () => {
            setSaving(true);
            try {
              await onSave({ id: usual.id!, substitutes: mode === "none" ? [] : subs, fallback_mode: mode });
              setEditing(false);
            } catch {
              // the caller already showed the error
            } finally {
              setSaving(false);
            }
          }}
        >
          {saving ? "Saving…" : "Save"}
        </Button>
      </div>
    </div>
  );
}
