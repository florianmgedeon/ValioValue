import type { ButtonHTMLAttributes, ReactNode } from "react";
import { Icon, type IconName } from "./Icon";

type Variant = "primary" | "ghost" | "inverse" | "link";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  small?: boolean;
  icon?: IconName;
  iconAfter?: IconName;
  children?: ReactNode;
}

export function Button({ variant = "ghost", small, icon, iconAfter, className = "", children, type = "button", ...rest }: ButtonProps) {
  const cls = variant === "link" ? "link" : `btn btn-${variant}${small ? " btn-small" : ""}`;
  return (
    <button type={type} className={`${cls} ${className}`} {...rest}>
      {icon && <Icon name={icon} />}
      {children}
      {iconAfter && <Icon name={iconAfter} />}
    </button>
  );
}
