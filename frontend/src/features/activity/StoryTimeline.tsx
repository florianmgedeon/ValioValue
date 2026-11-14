import type { ReactNode } from "react";
import { apiUrl } from "../../api/client";
import { Chip, Cond, Icon, Tier, eur, type IconName } from "../../ui";
import { counts, type DecidedLine, type StoryModel } from "./model";
import { formatDay, formatTime } from "./format";


function Step({ icon, node = "", title, children }: { icon: IconName; node?: string; title: ReactNode; children?: ReactNode }) {
  return (
    <li>
      <span className={`act-node ${node}`}><Icon name={icon} /></span>
      <div className="act-body">
        <b>{title}</b>
        {children}
      </div>
    </li>
  );
}

function OutcomeChip({ line, grantLabel }: { line: DecidedLine; grantLabel: (id: string) => string | undefined }) {
  switch (line.outcome) {
    case "alone": {
      const label = line.grant ? grantLabel(line.grant) : undefined;
      return <Chip kind="auto" icon="lock">Ordered alone{label ? ` · ${label}` : ""}</Chip>;
    }
    case "you":
      return <Chip kind="about" icon="hand">You decided</Chip>;
    case "blocked":
      return <Chip kind="must" icon="ban">Blocked{line.rule ? ` · ${line.rule}` : ""}</Chip>;
    default:
      return null;
  }
}

export function StoryTimeline({ model, grantLabel, ruleLabel }: {
  model: StoryModel;
  grantLabel: (id: string) => string | undefined;
  ruleLabel: (id: string) => string | undefined;
}) {
  const c = counts(model);
  const meta = [
    `${c.lines} ${c.lines === 1 ? "line" : "lines"}`,
    !model.reduced || c.alone ? `${c.alone} alone` : null,
    !model.reduced || c.you ? `${c.you} by you` : null,
    !model.reduced || c.blocked ? `${c.blocked} blocked` : null,
  ].filter(Boolean);
  const anyBlocked = c.blocked > 0 || model.tier === "block";
  const u = model.understood;

  return (
    <>
      <div className="act-story-h">
        <div className="min-w-0 flex-1">
          <div className="eyebrow">
            Order #{model.orderId}
            {model.createdAt && ` · ${formatDay(model.createdAt)}, ${formatTime(model.createdAt)}`}
          </div>
          <h2>{model.title || model.label || "Order"}</h2>
          <p>{meta.join(" · ")}</p>
        </div>
        {model.label && model.tier && <Tier tier={model.tier}><span className="text-[13px]">{model.label}</span></Tier>}
      </div>

      <ol className="act-tl">
        {model.asked && (
          <Step icon={model.asked.attachments.length ? "cam" : "chat"} title="You asked">
            {model.asked.text && <span className="act-muted">{model.asked.attachments.length ? model.asked.text : `“${model.asked.text}”`}</span>}
            {model.asked.attachments.length > 0 && (
              <div className="flex flex-wrap gap-2">
                {model.asked.attachments.map((id) => (
                  <img key={id} className="act-thumb" src={apiUrl(`/uploads/${encodeURIComponent(id)}`)} alt="Photo you sent" loading="lazy"
                    onError={(e) => { e.currentTarget.style.display = "none"; }} />
                ))}
              </div>
            )}
          </Step>
        )}

        {u && (
          <Step icon="sig" title="I understood, you confirmed">
            <span className="act-muted">
              {[u.items !== undefined ? `${u.items} ${u.items === 1 ? "item" : "items"}` : u.text, u.signedBy ? `confirmed by ${u.signedBy}` : null]
                .filter(Boolean).join(" · ")}
            </span>
          </Step>
        )}

        {model.searched.length > 0 && (
          <Step icon="search" node="usual" title="I searched and picked">
            <span className="act-muted">Products per line: catalog → after your musts → in stock → picked</span>
            {model.searched.map((l, i) => {
              // Red only while nothing replaced the blocked product.
              const mine = model.decided.filter((d) => d.itemId && d.itemId === l.itemId);
              const blocked = mine.some((d) => d.outcome === "blocked") && !mine.some((d) => d.outcome !== "blocked");
              return (
                <div key={l.itemId ?? i} className={`act-why${blocked ? " blocked" : ""}`}>
                  <span className="font-semibold">{l.name}</span>
                  <span className="act-f">{l.funnel ?? ""}</span>
                  <span className="act-w">{model.reduced ? l.why && <span>{l.why}.</span> : l.why}</span>
                </div>
              );
            })}
          </Step>
        )}

        {(model.decided.length > 0 || model.decidedNotes.length > 0) && (
          <Step icon="shield" node={anyBlocked ? "block" : c.you ? "you" : "ok"} title="Valio Aimo decided, line by line">
            <span className="act-muted">
              Checked against your rules
            </span>
            {!model.reduced && model.decided.map((l, i) => (
              <div key={i} className={`act-why${l.outcome === "blocked" ? " blocked" : ""}`}>
                <span>{l.name}</span>
                <span><OutcomeChip line={l} grantLabel={grantLabel} /></span>
                {(l.why || l.by) && l.outcome !== "blocked" && (
                  <span className="act-w">{[l.why, l.outcome === "you" && l.by ? `by ${l.by}` : null].filter(Boolean).join(" · ")}</span>
                )}
                {l.outcome === "blocked" && (l.rule || l.evidence) && (
                  <span className="act-w">
                    {l.rule && ruleLabel(l.rule) && <span>{ruleLabel(l.rule)}.</span>}
                    {l.evidence && <>Evidence: <Cond>{l.evidence}</Cond></>}
                  </span>
                )}
              </div>
            ))}
            {model.reduced && model.decidedNotes.map((t, i) => (
              <div key={i} className={`act-why${/block/i.test(t) ? " blocked" : ""}`}>
                <span>{t}</span>
                <span />
              </div>
            ))}
          </Step>
        )}

        {model.confirmed && (
          <Step icon="check" node="ok" title={`Confirmed${model.confirmed.total !== undefined ? ` · ${eur(model.confirmed.total)}` : ""}`}>
            <span className="act-muted">
              Prices locked, confirmed by Valio Aimo.
            </span>
          </Step>
        )}

        {model.learned && (
          <Step icon="bulb" node="usual" title="I learned">
            <span className="act-muted">{model.learned}</span>
          </Step>
        )}
      </ol>
    </>
  );
}
