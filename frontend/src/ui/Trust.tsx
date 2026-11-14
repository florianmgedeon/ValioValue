import { useState, type ReactNode } from "react";
import { Icon } from "./Icon";

/** A labelled hash, monospace, with copy. */
export function HashRow({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="hashrow">
      <div className="flex items-center gap-2">
        <span className="eyebrow" style={{ fontSize: 10.5 }}>{label}</span>
        <span className="flex-1" />
        <button
          type="button"
          className="text-ink3 hover:text-ink"
          aria-label={`Copy ${label}`}
          onClick={() => {
            void navigator.clipboard?.writeText(value).then(() => {
              setCopied(true);
              window.setTimeout(() => setCopied(false), 1200);
            });
          }}
        >
          {copied ? <Icon name="check" size={14} /> : <Icon name="copy" size={14} />}
        </button>
      </div>
      <code>{value}</code>
    </div>
  );
}

/** Cedar policy text with light colouring: comments, keywords, strings, annotations. */
export function CedarBlock({ text }: { text: string }) {
  const parts: ReactNode[] = [];
  text.split("\n").forEach((line, i) => {
    if (i) parts.push("\n");
    const comment = line.indexOf("//");
    const code = comment >= 0 ? line.slice(0, comment) : line;
    code.split(/("(?:[^"\\]|\\.)*"|@\w+|\b(?:permit|forbid|when|unless)\b)/).forEach((tok, j) => {
      if (!tok) return;
      const cls = tok.startsWith('"') ? "s" : tok.startsWith("@") ? "a" : /^(permit|forbid|when|unless)$/.test(tok) ? "k" : null;
      parts.push(cls ? <span key={`${i}-${j}`} className={cls}>{tok}</span> : tok);
    });
    if (comment >= 0) parts.push(<span key={`${i}-c`} className="c">{line.slice(comment)}</span>);
  });
  return <pre className="cedar">{parts}</pre>;
}

export type DiffKind = "add" | "del" | "chg";
export function DiffList({ lines }: { lines: { text: string; kind: DiffKind }[] }) {
  return (
    <div className="diff">
      {lines.map((l, i) => (
        <div key={i} className={l.kind}>{l.text}</div>
      ))}
    </div>
  );
}
