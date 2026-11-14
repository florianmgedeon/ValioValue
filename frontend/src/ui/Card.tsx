import type { HTMLAttributes, ReactNode } from "react";

export type CardVariant = "plain" | "decision" | "block" | "receipt";

export function Card({ variant = "plain", className = "", children, ...rest }: HTMLAttributes<HTMLDivElement> & { variant?: CardVariant }) {
  return (
    <div className={`card ${variant === "plain" ? "" : `card-${variant}`} ${className}`} {...rest}>
      {children}
    </div>
  );
}

export function CardHeader({ title, children }: { title?: ReactNode; children?: ReactNode }) {
  return (
    <div className="card-h">
      {title !== undefined && <h3>{title}</h3>}
      {children}
    </div>
  );
}

export function CardFooter({ children }: { children: ReactNode }) {
  return <div className="card-f">{children}</div>;
}
