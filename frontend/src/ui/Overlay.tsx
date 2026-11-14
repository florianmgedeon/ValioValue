import { useEffect, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Button } from "./Button";

function useEscape(onClose: () => void) {
  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);
}

export function Modal({ open, onClose, width, label, children }: {
  open: boolean;
  onClose: () => void;
  width?: number;
  label: string;
  children: ReactNode;
}) {
  useEscape(onClose);
  if (!open) return null;
  return createPortal(
    <div className="scrim" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal rise" role="dialog" aria-modal="true" aria-label={label} style={width ? { width: `min(${width}px, 100%)` } : undefined}>
        {children}
      </div>
    </div>,
    document.body,
  );
}

export function Drawer({ open, onClose, title, icon, children }: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  icon?: ReactNode;
  children: ReactNode;
}) {
  useEscape(onClose);
  if (!open) return null;
  return createPortal(
    <>
      <div className="scrim" onMouseDown={onClose} />
      <aside className="drawer rise" role="dialog" aria-modal="true">
        <div className="flex items-center gap-2.5">
          {icon}
          <h2>{title}</h2>
          <span className="flex-1" />
          <Button small onClick={onClose}>Close</Button>
        </div>
        {children}
      </aside>
    </>,
    document.body,
  );
}
