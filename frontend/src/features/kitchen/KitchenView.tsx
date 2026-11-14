import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { setAutonomyLevel } from "../../api/autonomy";
import { acceptKitchenSuggestions, getKitchenHistory, ingestKitchenDocuments, patchKitchenProfile, rejectKitchenSuggestion } from "../../api/kitchen";
import { getMandateHistory } from "../../api/mandate";
import { listOrders } from "../../api/orders";
import type { KitchenProfile, Mandate, Schema, UploadMeta } from "../../api/types";
import { useComposer } from "../../app/composer";
import { usePhoneCamera } from "../../app/PhoneCamera";
import { keys, queryClient, SIGNER_NAME, useAutonomy, useKitchen, useMandate } from "../../app/queries";
import { useSignedChange } from "../../app/signing";
import { Button, Drawer, Icon, Typing, useToast } from "../../ui";
import { AutonomySection, LEVELS } from "./Autonomy";
import "./kitchen.css";
import { applyOps, nextId, plural, ruleLabel, upsert, type Grant, type HardRule, type LevelChange, type Op, type Preference, type RuleGroup } from "./model";
import { About, Sources, Suggestions, TeachCard, UsualSection } from "./Profile";
import { errText, MustSection, PreferSection, type RowFlags } from "./Rules";

type Suggestion = Schema["Suggestion"];

const IMPORTANCE_TEXT: Record<number, string> = { 1: "nice to have", 2: "important", 3: "very important" };

const shortDate = (iso?: string) => (iso ? new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short" }) : "");

/** Kitchen tab (F2): what the agent knows (profile, unsigned) and may do (mandate, signed). */
export default function KitchenView() {
  const toast = useToast();
  const sign = useSignedChange();
  const camera = usePhoneCamera();
  const kitchen = useKitchen();
  const mandate = useMandate();
  const autonomy = useAutonomy();
  const orders = useQuery({ queryKey: [...keys.orders, "all"], queryFn: () => listOrders({ limit: 200 }) });

  const [ops, setOps] = useState<Op[]>([]);
  const [levelChange, setLevelChange] = useState<LevelChange | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [reading, setReading] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);

  /** Photos (phone or upload) and typed sentences on this tab only teach: `/kitchen/ingest`, never `/chat` (W2). */
  const ingest = async (req: { upload_ids?: string[]; text?: string }, what: string) => {
    setReading(true);
    try {
      const out = (await ingestKitchenDocuments({ ...req, by: SIGNER_NAME })) as { results?: { suggestions?: unknown[]; note?: string; hint?: string }[] };
      const n = (out.results ?? []).reduce((s, r) => s + (r.suggestions?.length ?? 0), 0);
      const extra = out.results?.map((r) => r.note || r.hint).find(Boolean);
      toast(`${what} read. ${n ? plural(n, "new suggestion") : "Nothing new to suggest"}, nothing ordered.${extra ? ` ${extra}` : ""}`);
      await queryClient.invalidateQueries({ queryKey: keys.kitchen });
      if (n) window.scrollTo({ top: 0, behavior: "smooth" });
    } catch (e) {
      toast(`I could not read it: ${errText(e)}`);
    } finally {
      setReading(false);
    }
  };

  useEffect(() => camera.onPhoto("kitchen", (u: UploadMeta) => void ingest({ upload_ids: [u.upload_id!] }, "Photo")));

  useComposer({
    onSubmit: (text) => void ingest({ text }, "Got it. Your note was"),
    placeholder: "Teach me something about your kitchen…",
    hints: ["We're vegetarian on Wednesdays from now on."],
    camera: "kitchen",
  });

  const view = useMemo(() => (mandate.data ? applyOps(mandate.data, ops) : null), [mandate.data, ops]);
  const flags: RowFlags = useMemo(() => {
    const changed = new Set<string>();
    const fresh = new Set<string>();
    for (const o of ops) {
      if (o.t === "patchRule" || o.t === "patchGrant") changed.add(o.id);
      if (o.t === "addRule") fresh.add(o.rule.id!);
      if (o.t === "addGrant") fresh.add(o.grant.id!);
    }
    return { changed, fresh };
  }, [ops]);

  if (kitchen.isLoading || mandate.isLoading || autonomy.isLoading) {
    return <main className="kt"><Typing /></main>;
  }
  const failed = [kitchen, mandate, autonomy].find((q) => q.isError);
  if (failed || !kitchen.data || !mandate.data || !view || !autonomy.data) {
    return (
      <main className="kt">
        <div className="err">Could not load your kitchen: {failed ? errText(failed.error) : "no data"}</div>
        <Button className="justify-self-start" onClick={() => void Promise.all([kitchen.refetch(), mandate.refetch(), autonomy.refetch()])}>Try again</Button>
      </main>
    );
  }

  const profile: KitchenProfile = kitchen.data;
  const m: Mandate = mandate.data;
  const a = autonomy.data;
  const level = (a.level ?? 0) as 0 | 1 | 2;
  const original = (group: RuleGroup, id?: string) =>
    ((group === "must" ? m.mandatory_rules : m.preferences) as (HardRule | Preference)[] | undefined)?.find((r) => r.id === id);
  const today = shortDate(new Date().toISOString());

  // ---- rule edits (pending, signed together)
  const toggleRule = (group: RuleGroup) => (r: HardRule | Preference) => {
    const next = r.enabled === false;
    if (r.id && flags.fresh.has(r.id) && !next) {
      // switching off a rule that was only drafted: just drop the draft
      return setOps((o) => o.filter((x) => !(x.t === "addRule" && x.rule.id === r.id) && !(x.t === "patchRule" && x.id === r.id)));
    }
    const orig = original(group, r.id);
    setOps((o) => upsert(o, {
      key: `${group}:${r.id}:enabled`, t: "patchRule", group, id: r.id!, patch: { enabled: next },
      text: `${next ? "Turn on" : "Turn off"} ${group === "must" ? "must" : "preference"}: ${ruleLabel(r)}`, kind: next ? "add" : "del",
    }, !!orig && next === (orig.enabled !== false)));
  };
  const unknownRule = (r: HardRule, v: "exclude" | "ask") => {
    const orig = original("must", r.id) as HardRule | undefined;
    setOps((o) => upsert(o, {
      key: `must:${r.id}:if_unknown`, t: "patchRule", group: "must", id: r.id!, patch: { if_unknown: v },
      text: `${ruleLabel(r)}: if the label doesn’t say, ${v === "ask" ? "ask me" : "leave it out"}`, kind: "chg",
    }, !!orig && (orig.if_unknown ?? "exclude") === v));
  };
  const weightRule = (r: Preference, w: number) => {
    const orig = original("prefer", r.id) as Preference | undefined;
    setOps((o) => upsert(o, {
      key: `prefer:${r.id}:weight`, t: "patchRule", group: "prefer", id: r.id!, patch: { weight: w },
      text: `${ruleLabel(r)}: ${IMPORTANCE_TEXT[w] ?? w}${orig ? ` (was ${IMPORTANCE_TEXT[orig.weight ?? 1] ?? orig.weight})` : ""}`, kind: "chg",
    }, !!orig && (orig.weight ?? 1) === w));
  };
  const addRule = (group: RuleGroup) => (rule: HardRule | Preference, label: string) => {
    const list = group === "must" ? view.mandatory_rules : view.preferences;
    const id = nextId(group === "must" ? "m" : "p", list.map((x) => x.id));
    const full = { ...rule, id, label, enabled: true, ...(group === "must" ? { source: `You, ${today}` } : {}) } as HardRule | Preference;
    setOps((o) => [...o, { key: `add:${group}:${id}`, t: "addRule", group, rule: full, text: `New ${group === "must" ? "must" : "preference"}: ${label}`, kind: "add" }]);
  };

  // ---- grants
  const grantIds = [...view.autonomy.grants.map((g) => g.id), ...(a.grants ?? []).map((g) => g.id)];
  const toggleGrant = (g: Grant) => {
    const next = g.enabled === false;
    if (g.id && flags.fresh.has(g.id) && !next) {
      return setOps((o) => o.filter((x) => !(x.t === "addGrant" && x.grant.id === g.id) && !(x.t === "patchGrant" && x.id === g.id)));
    }
    const orig = m.autonomy?.grants?.find((x) => x.id === g.id);
    setOps((o) => upsert(o, {
      key: `grant:${g.id}:enabled`, t: "patchGrant", id: g.id!, patch: { enabled: next },
      text: `${next ? "Turn on" : "Turn off"} permission: ${g.label ?? ""}`, kind: next ? "add" : "del",
    }, !!orig && next === (orig.enabled !== false)));
  };
  const addGrant = (g: Grant) => {
    const id = nextId("g", grantIds);
    const grant = { ...g, id, enabled: true, source: `You, ${today}` } as Grant;
    const lim = g.limits?.max_order_total != null ? `up to €${g.limits.max_order_total} an order` : g.limits?.max_line_total != null ? `up to €${g.limits.max_line_total} a line` : `up to €${g.limits?.max_unit_price} per unit`;
    setOps((o) => [...o, { key: `add:grant:${id}`, t: "addGrant", grant, text: `New permission: ${(g.label ?? "products").toLowerCase()} alone, ${lim}`, kind: "add" }]);
  };

  // ---- level and limit (signed on their own: the backend builds g1)
  const g1 = view.autonomy.grants.find((g) => g.id === "g1");
  const currentLimit = g1?.limits?.max_order_total;
  const describeLevel = (n: 0 | 1 | 2, limit?: number): LevelChange | null => {
    const lvlChanged = n !== level;
    const limChanged = limit != null && limit !== currentLimit;
    if (!lvlChanged && !limChanged) return null;
    const parts = [];
    if (lvlChanged) parts.push(`I may order alone: ${LEVELS[n]!.name} (was: ${LEVELS[level]!.name})`);
    if (limChanged) parts.push(`Limit: up to €${(limit as number).toLocaleString("en-GB")} an order (was €${(currentLimit ?? 0).toLocaleString("en-GB")})`);
    return { level: n, max_order_total: limChanged ? limit : undefined, text: parts.join(" · "), kind: lvlChanged ? (n > level ? "add" : "del") : "chg" };
  };
  const onLevel = (n: 0 | 1 | 2) => setLevelChange((lc) => describeLevel(n, lc?.max_order_total));
  const onLimit = (v: number) => setLevelChange((lc) => describeLevel(lc?.level ?? level, v));

  const extras = [
    ...view.autonomy.grants.filter((g) => g.id !== "g1").map((g) => ({ grant: g, inMandate: true, source: (g as { source?: string }).source })),
    ...(a.grants ?? [])
      .filter((g) => g.kind === "extra" && !view.autonomy.grants.some((x) => x.id === g.id))
      .map((g) => ({ grant: { id: g.id, label: g.label, enabled: g.enabled ?? true } as Grant, inMandate: false, summary: g.summary, source: g.source })),
  ];

  // ---- suggestions (accept: the backend answers 409 for signed kinds, then the sign modal opens)
  const accept = async (s: Suggestion) => {
    setBusy(s.id ?? null);
    try {
      const out = (await sign({
        title: `Accept: ${s.title ?? s.kind}`,
        call: (mandate_hash) => acceptKitchenSuggestions({ ids: [s.id], by: SIGNER_NAME, mandate_hash }),
      })) as { profile?: KitchenProfile; mandate?: unknown } | null;
      if (out && !out.mandate) toast("Added to your kitchen profile");
    } catch (e) {
      toast(errText(e));
    } finally {
      setBusy(null);
    }
  };
  const reject = async (s: Suggestion) => {
    setBusy(s.id ?? null);
    try {
      await rejectKitchenSuggestion(s.id!, { by: SIGNER_NAME });
      await queryClient.invalidateQueries({ queryKey: keys.kitchen });
    } catch (e) {
      toast(errText(e));
    } finally {
      setBusy(null);
    }
  };

  const saveAbout = async (text: string) => {
    try {
      const p = await patchKitchenProfile({ about: { text }, by: SIGNER_NAME });
      queryClient.setQueryData(keys.kitchen, (old: KitchenProfile | undefined) => ({ ...old, ...p }));
      await queryClient.invalidateQueries({ queryKey: keys.kitchen });
      toast("Saved to your kitchen profile");
    } catch (e) {
      toast(errText(e));
      throw e;
    }
  };

  const saveUsual = async (update: { id: string; substitutes: { product_id: string; product_name: string }[]; fallback_mode: "ask_any" | "only_listed" | "none" }) => {
    try {
      const p = await patchKitchenProfile({ usual: { update: [update] }, by: SIGNER_NAME });
      queryClient.setQueryData(keys.kitchen, (old: KitchenProfile | undefined) => ({ ...old, ...p }));
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: keys.kitchen }),
        queryClient.invalidateQueries({ queryKey: keys.autonomy }),
      ]);
      toast("Saved fallback rules to your kitchen profile");
    } catch (e) {
      toast(errText(e));
      throw e;
    }
  };

  // ---- review & sign
  const pendingCount = ops.length + (levelChange ? 1 : 0);
  const review = async () => {
    try {
      if (ops.length) {
        const next = applyOps(m, ops);
        const res = await sign({
          title: plural(ops.length, "change") + " to your rules",
          lines: ops.map((o) => ({ text: o.text, kind: o.kind })),
          mandate: next as unknown as Record<string, unknown>,
        });
        if (!res) return;
        setOps([]);
      }
      if (levelChange) {
        const lc = levelChange;
        const res = await sign({
          title: lc.text,
          call: (mandate_hash) => setAutonomyLevel({ level: lc.level, max_order_total: lc.max_order_total, by: SIGNER_NAME, mandate_hash }),
        });
        if (!res) return;
        setLevelChange(null);
      }
    } catch (e) {
      toast(`Could not save: ${errText(e)}`);
    }
  };

  const proposed = (profile.suggestions ?? []).filter((s) => s.status === "proposed");
  const name = m.display_name || "Your kitchen";
  const learnedSomething = (profile.sources ?? []).length > 0;
  return (
    <main className="kt">
      <div className="k-head">
        <div>
          <h1>{name}</h1>
          <p>{reading ? "Reading…" : "What I know about your kitchen. Teach me more with a photo or a sentence below."}</p>
        </div>
        <span className="spacer" />
        <Button small variant="ghost" onClick={() => setHistoryOpen(true)}>History</Button>
      </div>

      {!learnedSomething && <TeachCard onPhone={() => camera.open("kitchen")} onUpload={() => camera.upload("kitchen")} busy={reading} />}

      <Suggestions items={proposed} sources={profile.sources ?? []} onAccept={(s) => void accept(s)} onReject={(s) => void reject(s)} busy={busy} />

      <AutonomySection
        autonomy={a}
        g1={g1}
        extras={extras}
        musts={view.mandatory_rules}
        pendingLevel={levelChange}
        changedGrants={flags.changed}
        freshGrants={flags.fresh}
        onLevel={onLevel}
        onLimit={onLimit}
        onToggleGrant={toggleGrant}
        onAddGrant={addGrant}
        locked={toast}
      />

      <div className="k-grid">
        <MustSection rules={view.mandatory_rules} flags={flags} onToggle={toggleRule("must")} onUnknown={unknownRule} onAdd={addRule("must")} />
        <PreferSection rules={view.preferences} flags={flags} onToggle={toggleRule("prefer")} onWeight={weightRule} onAdd={addRule("prefer")} />
      </div>

      <UsualSection profile={profile} onSaveUsual={saveUsual} />

      <div className="k-grid">
        <About profile={profile} onSave={saveAbout} />
        <Sources profile={profile} orders={orders.data?.length ?? null} />
      </div>

      {pendingCount > 0 && (
        <div className="k-changebar rise" role="status">
          <span>
            <b>{plural(pendingCount, "change")} to your rules</b> · not saved yet
          </span>
          <Button variant="link" className="text-[13px]" onClick={() => { setOps([]); setLevelChange(null); }}>Discard</Button>
          <Button small onClick={() => void review()}>Check & save</Button>
        </div>
      )}

      <HistoryDrawer open={historyOpen} onClose={() => setHistoryOpen(false)} />
    </main>
  );
}

type HistoryRow = { at: string; what: "mandate" | "profile"; version: number; text: string; by?: string };

/** `GET /mandate/history` and `GET /kitchen/history`, merged by date, newest first. */
function HistoryDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const q = useQuery({
    queryKey: [...keys.kitchen, "history-merged"],
    enabled: open,
    queryFn: async (): Promise<HistoryRow[]> => {
      const [mh, kh] = await Promise.all([getMandateHistory(), getKitchenHistory()]);
      const rows: HistoryRow[] = [
        ...mh.map((x) => ({
          at: x.updated_at ?? "",
          what: "mandate" as const,
          version: x.version ?? 0,
          by: x.updated_by,
          text: `${plural(x.mandatory_rules?.length ?? 0, "must")} · ${plural(x.preferences?.length ?? 0, "preference")} · ${plural(x.autonomy?.grants?.length ?? 0, "permission")}`,
        })),
        ...(((kh.history as { version?: number; at?: string; by?: string; summary?: string }[] | undefined) ?? []).map((h) => ({
          at: h.at ?? "", what: "profile" as const, version: h.version ?? 0, by: h.by, text: h.summary ?? "",
        }))),
      ];
      return rows.sort((x, y) => (x.at < y.at ? 1 : x.at > y.at ? -1 : 0));
    },
  });
  return (
    <Drawer open={open} onClose={onClose} title="History">
      <p className="text-ink3">Every change to your rules and your kitchen profile.</p>
      {q.isLoading && <Typing />}
      {q.isError && <div className="err">{errText(q.error)}</div>}
      <div className="k-hist">
        {q.data?.map((r, i) => (
          <div key={i}>
            <span className={`chip ${r.what === "mandate" ? "k-auto" : "k-about"}`}>{r.what === "mandate" ? <><Icon name="lock" />Rules</> : <>Kitchen</>}</span>
            <div>
              <div>{r.text}</div>
              <div className="when">{r.at ? new Date(r.at).toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : ""}{r.by ? ` · ${r.by}` : ""}</div>
            </div>
          </div>
        ))}
      </div>
    </Drawer>
  );
}
