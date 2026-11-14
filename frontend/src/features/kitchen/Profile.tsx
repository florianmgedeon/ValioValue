import { useEffect, useState } from "react";
import { apiUrl } from "../../api/client";
import { searchKitchenProducts, type KitchenCatalogProduct } from "../../api/kitchen";
import type { KitchenProfile, Schema } from "../../api/types";
import { useComposerText } from "../../app/composer";
import { navigate } from "../../app/route";
import { Button, Chip, Icon, KindTag, Mark, Paper, YesNo, eur, type Kind } from "../../ui";
import { useKitchenContext, useUsualOrder } from "../order/data";

type Suggestion = Schema["Suggestion"];
type Source = Schema["Source"];
type UsualProduct = Schema["UsualProduct"];
type FallbackMode = NonNullable<UsualProduct["fallback_mode"]>;
type SubstituteEntry = { product_id: string; product_name: string };

/** Backend suggestion kinds → the UI's kind colours. */
export const KIND_OF: Record<NonNullable<Suggestion["kind"]>, Kind> = { about: "about", usual: "usual", must: "must", prefer: "prefer", autonomy: "auto" };

const uploadSrc = (id: string) => apiUrl(`/uploads/${encodeURIComponent(id)}`);

export function DocMenu() {
  return (
    <Paper tilt="l">
      <h4>LUNCH · WEEK 47</h4>
      <div className="row"><span>MON</span><span>Salmon soup, rye bread</span></div>
      <div className="row"><span>TUE</span><span>Meatballs, mashed potatoes, lingonberry</span></div>
      <div className="row"><span>WED</span><span>Vegetable lasagne, salad</span></div>
      <div className="row"><span>THU</span><span>Pea soup, oven pancake</span></div>
      <div className="row"><span>FRI</span><span>Chicken curry, rice</span></div>
      <div style={{ marginTop: 6 }}>Every day: milk, oat drink, rye bread, butter, fruit</div>
      <div style={{ marginTop: 10, opacity: 0.85 }}>
        <Mark kind="about">420 lunches / day</Mark> · <Mark kind="must">Nut-free school</Mark>
      </div>
    </Paper>
  );
}

export function DocNote() {
  return (
    <Paper variant="hand" tilt="r">
      <div><b>KITCHEN – READ!</b></div>
      <div><Mark kind="must">NO NUTS – not even “may contain”!!</Mark></div>
      <div>3 kids with severe allergy</div>
      <div><Mark kind="usual">18 milk-free diets → oat drink</Mark></div>
    </Paper>
  );
}

/** Large dark card: photos and uploads go to `/kitchen/ingest` (learn, never order; decision W2). */
export function TeachCard({ onPhone, onUpload, busy }: { onPhone: () => void; onUpload: () => void; busy: boolean }) {
  return (
    <div className="photo-card">
      <span className="ic"><Icon name="cam" /></span>
      <span className="relative z-1 grid gap-1.5">
        <h2>Teach me with a photo</h2>
        <p>Menus, allergy notes, invoices, delivery notes. I read them and suggest what to remember about your kitchen. These photos never place an order, and nothing changes until you accept it.</p>
      </span>
      <span className="teach-steps">
        <span><b>1</b>Take a photo</span>
        <span><b>2</b>I suggest rules and usual products</span>
        <span><b>3</b>You accept or reject each one</span>
      </span>
      <span className="acts">
        <Button variant="primary" small icon="phone" onClick={onPhone} disabled={busy}>{busy ? "Reading…" : "Use your phone"}</Button>
        <Button small icon="clip" onClick={onUpload} disabled={busy}>Upload</Button>
      </span>
      <span className="stack" aria-hidden="true"><DocMenu /><DocNote /></span>
    </div>
  );
}

/** For a usual-product suggestion: the word, the product and the usual amount. Rules need no extra line, the title says it. */
function usualText(s: Suggestion): string | null {
  if (s.kind !== "usual") return null;
  const p = (s.payload ?? {}) as Record<string, unknown>;
  const what = (p.product_name as string) || (p.product_query as string) || "";
  const qty = p.typical_qty ? ` · ~${String(p.typical_qty)} ${unitText(p.unit as string)} / ${(p.per as string) || "week"}` : "";
  return `“${String(p.word ?? "")}”${what ? ` → ${what}` : ""}${qty}`;
}

export function Suggestions({ items, sources, onAccept, onReject, busy }: {
  items: Suggestion[];
  sources: Source[];
  onAccept: (s: Suggestion) => void;
  onReject: (s: Suggestion) => void;
  busy: string | null;
}) {
  if (!items.length) return null;
  const src = (id?: string) => sources.find((x) => x.id === id)?.title;
  return (
    <section className="sec" style={{ borderColor: "var(--accent)" }}>
      <div className="sec-h">
        <Icon name="bulb" />
        <h2>New from your documents</h2>
        <span className="spacer" />
        <span className="muted text-[13px]">I never turn these on without you</span>
      </div>
      {items.map((s) => {
        const usual = usualText(s);
        return (
          <div key={s.id} className="rule new" aria-busy={busy === s.id}>
            <div className="grid gap-1">
              <div className="t">{s.kind && <KindTag kind={KIND_OF[s.kind]} />}{s.title}</div>
              <div className="s">
                {usual && <span>{usual}</span>}
                {src(s.source_id) && <span>from {src(s.source_id)}</span>}
                {(s.kind === "must" || s.kind === "prefer" || s.kind === "autonomy") && <span>· needs your OK</span>}
              </div>
              {s.quote && !s.quote.startsWith(s.title ?? "\u0000") && <div className="q">“{s.quote}”</div>}
            </div>
            <YesNo value={undefined} onChange={(yes) => (yes ? onAccept(s) : onReject(s))} />
          </div>
        );
      })}
    </section>
  );
}

export function About({ profile, onSave }: { profile: KitchenProfile; onSave: (text: string) => Promise<void> }) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(profile.about?.text ?? "");
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    if (!editing) setText(profile.about?.text ?? "");
  }, [profile.about?.text, editing]);
  const facts = profile.about?.facts ?? [];
  return (
    <section className="sec">
      <div className="sec-h">
        <h2>About your kitchen</h2>
        <span className="spacer" />
        <Button variant="link" className="text-[13px]" onClick={() => setEditing((e) => !e)}>{editing ? "Cancel" : "Edit"}</Button>
        <p>Helps me read your requests and plan amounts.</p>
      </div>
      {editing ? (
        <div className="grid gap-2.5 px-5 pb-4">
          <textarea className="field" value={text} onChange={(e) => setText(e.target.value)} aria-label="About your kitchen" />
          <div className="frow">
            <span className="muted text-[13px]">Saved to your kitchen profile.</span>
            <span className="spacer" />
            <Button
              variant="primary"
              small
              disabled={saving || text.trim() === (profile.about?.text ?? "").trim()}
              onClick={async () => {
                setSaving(true);
                try {
                  await onSave(text);
                  setEditing(false);
                } finally {
                  setSaving(false);
                }
              }}
            >
              {saving ? "Saving…" : "Save"}
            </Button>
          </div>
        </div>
      ) : (
        <p className="about-text">{profile.about?.text || <span className="muted">Nothing yet. Tell me about your kitchen below.</span>}</p>
      )}
      {facts.length > 0 && (
        <div className="about-facts">
          {facts.map((f) => <Chip key={f.id ?? f.label} kind="about">{f.label}</Chip>)}
        </div>
      )}
    </section>
  );
}

export function unitText(u?: string) {
  return u === "KG" ? "kg" : u === "SALES_UNIT" ? "packs" : u ?? "";
}

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

/** The usual products and amounts in one block, with inline substitute & fallback rules and "Order the usual". */
export function UsualSection({
  profile,
  onSaveUsual,
}: {
  profile: KitchenProfile;
  onSaveUsual?: (update: { id: string; substitutes: SubstituteEntry[]; fallback_mode: FallbackMode }) => Promise<void>;
}) {
  const setText = useComposerText();
  const usual = useUsualOrder();
  const k = useKitchenContext();
  const items = (profile.usual ?? []).filter((u) => (u.status ?? "active") === "active");
  const src = (id?: string) => profile.sources?.find((x) => x.id === id)?.title;
  const est = usual.data?.estimate_total ?? null;
  const fits = est !== null && k.limit !== null ? est <= k.limit : null;
  const canOrder = items.some((u) => u.typical_qty != null);

  const [editingId, setEditingId] = useState<string | null>(null);
  const [subs, setSubs] = useState<SubstituteEntry[]>([]);
  const [mode, setMode] = useState<FallbackMode>("ask_any");
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<KitchenCatalogProduct[]>([]);
  const [searching, setSearching] = useState(false);
  const [saving, setSaving] = useState(false);

  const openEditor = (u: UsualProduct) => {
    if (editingId === u.id) {
      setEditingId(null);
      return;
    }
    const currentSubs = (u.substitutes ?? [])
      .filter((s): s is { product_id: string; product_name?: string | null } => !!s.product_id)
      .map((s) => ({ product_id: s.product_id, product_name: s.product_name ?? s.product_id }));
    setEditingId(u.id ?? null);
    setSubs(currentSubs);
    setMode(u.fallback_mode ?? (currentSubs.length > 0 ? "only_listed" : "ask_any"));
    setQuery(u.word ?? "");
    setHits([]);
  };

  useEffect(() => {
    if (!editingId || !query.trim()) {
      setHits([]);
      return;
    }
    let cancelled = false;
    setSearching(true);
    const t = setTimeout(() => {
      void searchKitchenProducts(query.trim(), 6)
        .then((res) => {
          if (!cancelled) setHits(res.products ?? []);
        })
        .catch(() => {
          if (!cancelled) setHits([]);
        })
        .finally(() => {
          if (!cancelled) setSearching(false);
        });
    }, 180);
    return () => {
      cancelled = true;
      clearTimeout(t);
    };
  }, [editingId, query]);

  const addSub = (p: KitchenCatalogProduct, primaryId?: string | null) => {
    if (p.product_id === primaryId || subs.some((s) => s.product_id === p.product_id)) return;
    const next = [...subs, { product_id: p.product_id, product_name: p.name }];
    setSubs(next);
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

  return (
    <section className="sec usual-sec">
      <div className="sec-h">
        <h2>Your usual products</h2>
        <p>When you say the word, I pick this product. With the usual amounts they make your usual order.</p>
      </div>
      {items.length === 0 && <div className="rule"><span className="muted text-sm">Nothing yet. Show me an invoice or a delivery note and I'll learn them.</span></div>}
      {items.map((u) => {
        const uSubs = (u.substitutes ?? []).filter((s) => !!s.product_id);
        const uMode: FallbackMode = u.fallback_mode ?? (uSubs.length > 0 ? "only_listed" : "ask_any");
        const isEditing = editingId === u.id;
        const fallbackSentence =
          uMode === "none"
            ? "If out of stock: never substitute"
            : uSubs.length > 0
              ? `If out of stock: ${uSubs.map((s, idx) => `${idx + 1}. ${s.product_name ?? s.product_id}`).join(" → ")}${uMode === "only_listed" ? " · no other products" : ""}`
              : "If out of stock: I ask you";
        return (
          <div key={u.id} className="usual-wrap">
            <div className="usual">
              <span className="w">{u.word}</span>
              <div className="min-w-0 grid gap-0.5">
                <div className="p">{u.product_name || <span className="muted">no product yet</span>}</div>
                <div className="s">
                  <span>{fallbackSentence}</span>
                  {onSaveUsual && u.id && (
                    <button type="button" className="link" onClick={() => openEditor(u)}>
                      {isEditing ? "close" : "change"}
                    </button>
                  )}
                  {src(u.source_id) && <span>· from {src(u.source_id)}</span>}
                </div>
              </div>
              <span className="qty">{u.typical_qty != null ? `${u.typical_qty} ${unitText(u.unit)} / ${u.per || "week"}` : ""}</span>
            </div>

            {isEditing && u.id && onSaveUsual && (
              <div className="usual-edit">
                <div className="usual-edit-row">
                  <span className="eyebrow">If {u.product_name ?? u.word} is out of stock</span>
                  <div className="frow">
                    {(["only_listed", "ask_any", "none"] as FallbackMode[]).map((mKey) => (
                      <button
                        key={mKey}
                        type="button"
                        className="tog"
                        aria-pressed={mode === mKey}
                        onClick={() => setMode(mKey)}
                      >
                        {FALLBACK_LABELS[mKey].short}
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
                            <div className="min-w-0 flex-1">
                              <b>{s.product_name}</b>
                            </div>
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
                          placeholder={`Search Valio Aimo for "${u.word}" substitutes…`}
                          aria-label="Search catalog for substitute products"
                        />
                      </div>
                      {searching && <div className="muted text-xs px-1">Searching…</div>}
                      {!searching && hits.length > 0 && (
                        <div className="sub-hits">
                          {hits
                            .filter((h) => h.product_id !== u.product_id && !subs.some((s) => s.product_id === h.product_id))
                            .slice(0, 4)
                            .map((h) => (
                              <div key={h.product_id} className="sub-hit">
                                <div className="min-w-0">
                                  <div className="font-medium text-[13.5px]">{h.name}</div>
                                </div>
                                <Button small variant="ghost" onClick={() => addSub(h, u.product_id)}>+ Add as #{subs.length + 1}</Button>
                              </div>
                            ))}
                        </div>
                      )}
                    </div>
                  </div>
                )}

                <div className="frow justify-end pt-1">
                  <Button small variant="ghost" onClick={() => setEditingId(null)}>Cancel</Button>
                  <Button
                    small
                    variant="primary"
                    disabled={saving || (mode === "only_listed" && subs.length === 0)}
                    onClick={async () => {
                      setSaving(true);
                      try {
                        await onSaveUsual({
                          id: u.id!,
                          substitutes: mode === "none" ? [] : subs,
                          fallback_mode: mode,
                        });
                        setEditingId(null);
                      } finally {
                        setSaving(false);
                      }
                    }}
                  >
                    {saving ? "Saving…" : "Save"}
                  </Button>
                </div>
              </div>
            )}
          </div>
        );
      })}
      {canOrder && (
        <div className="usual-foot">
          <span className="est">
            {est !== null ? <>The usual order ≈ <b className="num">{eur(est)}</b>. </> : null}
            {fits === true && "Inside your limit: I can order it alone."}
            {fits === false && "Above your limit: you approve the cart."}
          </span>
          <Button small onClick={() => { navigate("order"); setText("the usual, but "); }}>Adjust</Button>
          <Button variant="primary" small onClick={() => navigate("order", "usual")}>Order the usual</Button>
        </div>
      )}
    </section>
  );
}

function SourceThumb({ s }: { s: Source }) {
  if (s.upload_id && s.type === "photo") return <img src={uploadSrc(s.upload_id)} alt="" loading="lazy" />;
  const icon = s.type === "photo" ? "cam" : s.type === "text" ? "chat" : "doc";
  return <Icon name={icon} size={28} className="text-ink3" />;
}

export function Sources({ profile, orders }: { profile: KitchenProfile; orders: number | null }) {
  const docs = (profile.sources ?? []).filter((s) => s.type === "photo" || s.type === "pdf" || s.type === "text");
  const corrections = (profile.sources ?? []).filter((s) => s.type === "correction").length;
  return (
    <section className="sec">
      <div className="sec-h">
        <h2>What I learned from</h2>
      </div>
      <div className="sources">
        {docs.map((s) => {
          const inner = (
            <>
              <div className="th"><SourceThumb s={s} /></div>
              <span className="line-clamp-2">{s.title}</span>
            </>
          );
          return s.upload_id ? (
            <a key={s.id} className="src" href={uploadSrc(s.upload_id)} target="_blank" rel="noreferrer">{inner}</a>
          ) : (
            <div key={s.id} className="src">{inner}</div>
          );
        })}
        <div className="src"><div className="th n">{orders ?? "–"}</div>orders</div>
        <div className="src"><div className="th n">{corrections}</div>your corrections</div>
      </div>
    </section>
  );
}
