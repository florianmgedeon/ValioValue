import type { ReactNode } from "react";
import type { Kind } from "./Chip";

/** Looks like a sheet of paper: printed, handwritten (`hand`, with tape) or an invoice (`inv`). */
export function Paper({ variant = "print", tilt, className = "", children }: {
  variant?: "print" | "hand" | "inv";
  tilt?: "l" | "r";
  className?: string;
  children: ReactNode;
}) {
  const cls = ["paper", variant === "print" ? "" : variant, tilt ? `tilt-${tilt}` : "", className].join(" ");
  return (
    <div className={cls}>
      {variant === "hand" && <div className="tape" />}
      {children}
    </div>
  );
}

/** A highlighted quote inside a Paper. `on` colours it by kind, `focus` outlines it (hovered card). */
export function Mark({ kind, on = true, focus = false, children }: { kind: Kind; on?: boolean; focus?: boolean; children: ReactNode }) {
  return <mark className={`hl k-${kind} ${on ? "on" : ""} ${focus ? "focus" : ""}`}>{children}</mark>;
}
