import type { ReactNode } from "react";
import { Icon, type IconName } from "./Icon";
import { InfoTip } from "./InfoTip";

/** The five things the agent knows: signed musts and autonomy, ranking preferences, usual products, kitchen facts. */
export type Kind = "must" | "prefer" | "usual" | "about" | "auto";
export const KIND_LABEL: Record<Kind, string> = { must: "RULE", prefer: "PREFER", usual: "USUAL", about: "ABOUT", auto: "ORDER ALONE" };
/** Kinds that live in the signed mandate. Changing them needs a signature. */
export const SIGNED_KINDS: ReadonlySet<Kind> = new Set<Kind>(["must", "auto"]);

/** One sentence per kind, shown from the (i) on the tag. */
export const KIND_HELP: Record<Kind, { title: string; text: string }> = {
  must: { title: "A fixed rule", text: "Valio Aimo checks it on every order. A product that breaks it is never ordered, and I can't change the rule myself." },
  prefer: { title: "A preference", text: "When there's a choice, I pick what matches. It never blocks an order on its own." },
  usual: { title: "A usual product", text: "Something you order regularly. When you just say \"milk\", I take this one. If it's out of stock, I only take a substitute you signed off." },
  about: { title: "About your kitchen", text: "A fact like size, opening days or delivery times. Helps me plan quantities and dates." },
  auto: { title: "Order alone", text: "How much I may order without asking you. You set the limit and can take it back any time." },
};
const SIGNED_HELP = "This goes into your signed rules. Only you can change it, with your signature. I can't edit it, not even by mistake.";

/** `plain`: no explanation, e.g. inside a legend that already explains it. */
export function KindTag({ kind, plain = false }: { kind: Kind; plain?: boolean }) {
  const tag = <span className={`kind k-${kind}`}>{KIND_LABEL[kind]}</span>;
  return plain ? tag : <InfoTip title={KIND_HELP[kind].title} text={KIND_HELP[kind].text}>{tag}</InfoTip>;
}

export function Chip({ kind = "plain", icon, children }: { kind?: Kind | "plain" | "accent"; icon?: IconName; children: ReactNode }) {
  return (
    <span className={`chip k-${kind}`}>
      {icon && <Icon name={icon} />}
      {children}
    </span>
  );
}

export function SignedTag() {
  return (
    <InfoTip text={SIGNED_HELP}>
      <span className="signed">
        <Icon name="lock" />
        only you can change this
      </span>
    </InfoTip>
  );
}

/** A rule condition in mono, e.g. `allergens not_contains peanuts`. */
export function Cond({ children }: { children: ReactNode }) {
  return <span className="cond">{children}</span>;
}

export type TierKind = "auto" | "ask" | "block" | "live";
export function Tier({ tier, children }: { tier: TierKind; children: ReactNode }) {
  return <span className={`tier tier-${tier}`}>{children}</span>;
}
