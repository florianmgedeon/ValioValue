import { navigate } from "../../app/route";
import { Chip, Icon, eur } from "../../ui";
import { fmtDay, useKitchenContext } from "./data";
import { STEPS, type LiveState, type Step } from "./orderReducer";

const LABEL: Record<Step, string> = {
  asked: "You asked",
  understood: "You confirmed my list",
  searching: "I searched Valio Aimo",
  checking: "Valio Aimo checked every line",
  done: "Confirmed",
};

function nowLine(L: LiveState): [string, "" | "wait" | "block" | "done"] {
  const key = L.now ?? L.step ?? "asked";
  switch (key) {
    case "asked": return ["Reading your request…", ""];
    case "understood": return ["Waiting for your OK", "wait"];
    case "searching": return ["Searching the catalog…", ""];
    case "checking": return ["Valio Aimo is checking every line…", ""];
    case "wait":
      if (L.waiting) return [`${L.waiting} decision${L.waiting > 1 ? "s wait" : " waits"} for you`, "wait"];
      return [L.step === "understood" ? "Waiting for your OK" : "Waiting for you", "wait"];
    case "block": return ["Blocked by Valio Aimo", "block"];
    case "done": return ["Confirmed by Valio Aimo", "done"];
  }
}

/** "This order": status, total against the level limit, the five steps, delivery. */
export function LivePanel({ live }: { live: LiveState }) {
  const k = useKitchenContext();
  const [text, cls] = nowLine(live);
  const cur = live.step ? STEPS.indexOf(live.step) : -1;
  const limit = k.limit;
  const total = live.total;
  const over = total !== null && limit !== null && total > limit;
  return (
    <div className="live" aria-live="polite">
      <div className="eyebrow">This order</div>
      <div className={`now ${cls}`}>
        {cls === "done" ? <Icon name="check" /> : cls === "block" ? <Icon name="ban" /> : <span className="dot" />}
        {text}
      </div>
      {live.activity && live.typing && <div className="activity">{live.activity}</div>}
      {total !== null && (
        <>
          <div>
            <div className="big">{eur(total)}</div>
            <div className="text-[13px] text-ink3">{live.lines} line{live.lines === 1 ? "" : "s"}{live.note ? ` · ${live.note}` : ""}</div>
          </div>
          {limit !== null && (
            <div className={`limitbar ${over ? "over" : ""}`}>
              <div className="track"><i style={{ width: `${Math.min(100, (total / limit) * 100)}%` }} /></div>
              <span>{over ? `${eur(total - limit)} over your ${eur(limit)} limit: you approve the cart` : `Inside your ${eur(limit)} limit`}</span>
            </div>
          )}
        </>
      )}
      <ol className="steps">
        {STEPS.map((s, i) => (
          <li key={s} className={cur > i || live.step === "done" ? "done" : cur === i ? "now" : ""}>
            <span className="d" />
            {LABEL[s]}
          </li>
        ))}
      </ol>
      <dl className="kv">
        <dt>Delivery</dt><dd>{fmtDay(live.deliverBy)}</dd>
      </dl>
    </div>
  );
}

/** "I'm using from your kitchen": musts (locked), preferences, usual products, level. Shown in both modes. */
export function KitchenPanel() {
  const k = useKitchenContext();
  return (
    <div className="panel">
      <div className="eyebrow">I'm using from your kitchen</div>
      <div className="minilist">
        {k.musts.length > 0 && <div>{k.musts.map((r) => <Chip key={r.id ?? r.label} kind="must" icon="lock">{r.label ?? r.id}</Chip>)}</div>}
        {k.prefers.length > 0 && <div>{k.prefers.map((p) => <Chip key={p.id ?? p.label} kind="prefer">{p.label ?? p.id}</Chip>)}</div>}
        <div><Chip kind="usual">{k.usualCount} usual product{k.usualCount === 1 ? "" : "s"}</Chip></div>
        {k.level !== null && (
          <div><Chip kind="auto" icon="lock">Level {k.level}{k.limit !== null ? ` · alone up to ${eur(k.limit)}` : k.levelName ? ` · ${k.levelName}` : ""}</Chip></div>
        )}
      </div>
      <button className="link justify-self-start text-[13px]" onClick={() => navigate("kitchen")}>Open kitchen profile</button>
    </div>
  );
}
