import { useEffect, useRef, useState, type ReactNode } from "react";

/** Wraps a tag or word; hover or tap shows a one-sentence explanation below it. */
export function InfoTip({ text, title, children }: { text: string; title?: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    if (!open) return;
    const h = (e: PointerEvent) => !ref.current?.contains(e.target as Node) && setOpen(false);
    window.addEventListener("pointerdown", h);
    return () => window.removeEventListener("pointerdown", h);
  }, [open]);

  return (
    <span ref={ref} className="relative inline-flex" onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}>
      <button
        type="button"
        className="inline-flex cursor-help items-center gap-1 decoration-dotted underline-offset-2"
        aria-expanded={open}
        onClick={() => setOpen(true)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
      >
        {children}
        <span aria-hidden="true" className="grid size-3.5 place-items-center rounded-full border border-current text-[9px] leading-none font-bold opacity-50">i</span>
      </button>
      {open && (
        <span role="tooltip" className="rise absolute top-[calc(100%+8px)] left-0 z-40 w-[260px] rounded-xl bg-ink px-3.5 py-2.5 text-left text-[13px] leading-snug font-normal tracking-normal text-bg normal-case shadow-float">
          {title && <b className="mb-0.5 block">{title}</b>}
          {text}
        </span>
      )}
    </span>
  );
}
