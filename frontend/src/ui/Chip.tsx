import type { ReactNode } from "react";
import { Icon, type IconName } from "./Icon";

/** The five things the agent knows: signed musts and autonomy, ranking preferences, usual products, kitchen facts. */
export type Kind = "must" | "prefer" | "usual" | "about" | "auto";
export const KIND_LABEL: Record<Kind, string> = { must: "MUST", prefer: "PREFER", usual: "USUAL", about: "ABOUT", auto: "AUTONOMY" };
/** Kinds that live in the signed mandate. Changing them needs a signature. */
export const SIGNED_KINDS: ReadonlySet<Kind> = new Set<Kind>(["must", "auto"]);

export function KindTag({ kind }: { kind: Kind }) {
  return <span className={`kind k-${kind}`}>{KIND_LABEL[kind]}</span>;
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
    <span className="signed">
      <Icon name="lock" />
      signed rule
    </span>
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
