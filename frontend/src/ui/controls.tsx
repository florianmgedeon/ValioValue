import { Icon } from "./Icon";

export function Toggle({ checked, onChange, label }: { checked: boolean; onChange: (next: boolean) => void; label: string }) {
  return <button type="button" className="toggle" role="switch" aria-checked={checked} aria-label={label} onClick={() => onChange(!checked)} />;
}

export function Segmented<T extends string>({ value, options, onChange, label }: {
  value: T;
  options: readonly { value: T; label: string }[];
  onChange: (next: T) => void;
  label?: string;
}) {
  return (
    <span className="seg" role="group" aria-label={label}>
      {options.map((o) => (
        <button key={o.value} type="button" aria-pressed={o.value === value} onClick={() => onChange(o.value)}>
          {o.label}
        </button>
      ))}
    </span>
  );
}

/** Preference weight 1–3. */
export function Weight({ value, onChange, max = 3 }: { value: number; onChange: (next: number) => void; max?: number }) {
  return (
    <span className="weight" role="group" aria-label="Weight">
      {Array.from({ length: max }, (_, i) => i + 1).map((n) => (
        <button key={n} type="button" className={n <= value ? "on" : ""} aria-label={`Weight ${n}`} aria-pressed={n === value} onClick={() => onChange(n)} />
      ))}
    </span>
  );
}

/** Accept / reject pair. `value`: true accepted, false rejected, undefined undecided. */
export function YesNo({ value, onChange, yesLabel = "Accept", noLabel = "Reject" }: {
  value: boolean | undefined;
  onChange: (next: boolean) => void;
  yesLabel?: string;
  noLabel?: string;
}) {
  return (
    <span className="yn">
      <button type="button" className="y" aria-label={yesLabel} aria-pressed={value === true} onClick={() => onChange(true)}>
        <Icon name="check" />
      </button>
      <button type="button" className="n" aria-label={noLabel} aria-pressed={value === false} onClick={() => onChange(false)}>
        <Icon name="x" />
      </button>
    </span>
  );
}

export function Typing() {
  return (
    <span className="typing" aria-label="Working">
      <i />
      <i />
      <i />
    </span>
  );
}
