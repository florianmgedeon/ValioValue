import { useEffect, useState } from "react";
import { apiUrl } from "../../api/client";
import type { KitchenProfile, Schema } from "../../api/types";
import { Button, Chip, Icon, KindTag, Mark, Paper, YesNo, type Kind } from "../../ui";

type Suggestion = Schema["Suggestion"];
type Source = Schema["Source"];

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
        <KindTag kind="about" />
        <h2>About us</h2>
        <span className="spacer" />
        <Button variant="link" className="text-[13px]" onClick={() => setEditing((e) => !e)}>{editing ? "Cancel" : "Edit"}</Button>
        <p>Shapes how I read your requests. Not enforced.</p>
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

export function Usual({ profile }: { profile: KitchenProfile }) {
  const items = (profile.usual ?? []).filter((u) => (u.status ?? "active") === "active");
  const src = (id?: string) => profile.sources?.find((x) => x.id === id)?.title;
  return (
    <section className="sec">
      <div className="sec-h">
        <KindTag kind="usual" />
        <h2>Usual products</h2>
        <span className="spacer" />
        <span className="muted text-[13px]">{items.length} words I know</span>
        <p>When you say the word, I pick this product. Together with the usual amounts they make your usual order.</p>
      </div>
      {items.map((u) => (
        <div key={u.id} className="usual">
          <span className="w">{u.word}</span>
          <div>
            <div className="p">{u.product_name || <span className="muted">no product yet</span>}</div>
            {src(u.source_id) && <div className="m">from {src(u.source_id)}</div>}
          </div>
          <span className="qty">{u.typical_qty != null ? `${u.typical_qty} ${unitText(u.unit)} / ${u.per || "week"}` : ""}</span>
        </div>
      ))}
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
        <h2>Sources</h2>
        <span className="spacer" />
        <span className="muted text-[13px]">everything I learned from</span>
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
