import type { Suggestion } from "../../api/types";
import { KindTag, SignedTag, SIGNED_KINDS, YesNo, type Kind } from "../../ui";

/** Backend suggestion kinds → UI kinds (`autonomy` is `auto` in the UI). */
export function suggestionKind(s: Suggestion): Kind {
  return s.kind === "autonomy" ? "auto" : (s.kind ?? "about");
}

type Payload = Record<string, unknown>;
const UNIT: Record<string, string> = { L: "L", KG: "kg", SALES_UNIT: "packs" };

/** The small line under a LEARN card: what the suggestion means in practice. */
export function suggestionSub(s: Suggestion): string {
  const p = (s.payload ?? {}) as Payload;
  switch (s.kind) {
    case "must":
      return p.if_unknown === "exclude" ? "If the label is unknown: exclude" : p.if_unknown === "ask" ? "If the label is unknown: ask you" : "Valio Aimo checks it on every order";
    case "prefer": {
      const w = Number(p.weight ?? 1);
      return `${w >= 3 ? "Strong" : w === 2 ? "Medium" : "Light"} preference (${w} of 3)`;
    }
    case "usual": {
      const qty = p.typical_qty ? `about ${String(p.typical_qty)} ${UNIT[String(p.unit)] ?? ""} a ${String(p.per ?? "week")}`.replace(/\s+/g, " ") : "";
      const name = typeof p.product_name === "string" ? p.product_name : "";
      return [qty, name].filter(Boolean).join(" · ") || "I'll pick this product when you say the word";
    }
    case "autonomy": {
      const limits = (p.limits ?? {}) as Payload;
      const max = limits.max_order_total;
      return `${max ? `Up to €${String(max)} an order. ` : ""}Proposed from your order size, you decide.`;
    }
    default:
      return "Helps me plan quantities and dates";
  }
}

/**
 * One thing the agent learned, with where it read it. Used by onboarding and the Kitchen tab's suggestions (F2).
 * `decision`: true = keep (default), false = drop.
 */
export function LearnCard({ s, from, decision, onDecide, onHover, focused = false }: {
  s: Suggestion;
  /** Title of the document the quote is from. */
  from?: string;
  decision?: boolean;
  onDecide?: (keep: boolean) => void;
  onHover?: (on: boolean) => void;
  focused?: boolean;
}) {
  const kind = suggestionKind(s);
  const rejected = decision === false;
  return (
    <div
      className={`rise grid grid-cols-[auto_minmax(0,1fr)_auto] items-start gap-3.5 rounded-2xl border bg-bg p-4 transition-[border-color,opacity] ${focused ? "border-ink3" : "border-line"} ${rejected ? "opacity-45" : ""}`}
      onMouseEnter={() => onHover?.(true)}
      onMouseLeave={() => onHover?.(false)}
      onFocus={() => onHover?.(true)}
      onBlur={() => onHover?.(false)}
    >
      <KindTag kind={kind} />
      <div className="min-w-0">
        <div className={`text-[15px] font-semibold ${rejected ? "line-through" : ""}`}>{s.title}</div>
        {s.quote && (
          <div className="mt-1 flex flex-wrap items-baseline gap-1.5 text-[12.5px] text-ink3">
            {from ? `from ${from}:` : "from your document:"}
            <span className="font-hand text-[17px] leading-none text-ink2">“{s.quote}”</span>
          </div>
        )}
        <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-[12.5px] text-ink2">
          {suggestionSub(s)}
          {SIGNED_KINDS.has(kind) && (
            <>
              {" · "}
              <SignedTag />
            </>
          )}
        </div>
      </div>
      {onDecide && <YesNo value={decision !== false} onChange={onDecide} yesLabel={`Keep: ${s.title}`} noLabel={`Drop: ${s.title}`} />}
    </div>
  );
}
