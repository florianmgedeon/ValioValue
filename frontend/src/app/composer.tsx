import { createContext, useContext, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { Icon } from "../ui";
import { usePhoneCamera, type CameraTarget } from "./PhoneCamera";

/** What the composer does on the current screen. A feature registers it with `useComposer` while mounted. */
export interface ComposerConfig {
  onSubmit: (text: string) => void;
  placeholder?: string;
  hints?: string[];
  /** Where a photo from the camera button goes. Default "order". */
  camera?: CameraTarget;
  /** Bigger bar with a label, for a start screen where the chat is one of the two main ways in. */
  prominent?: boolean;
}

interface ComposerApi {
  config: ComposerConfig | null;
  setConfig: (c: ComposerConfig | null) => void;
  text: string;
  setText: (t: string) => void;
}

const ComposerContext = createContext<ComposerApi | null>(null);

export function ComposerProvider({ children }: { children: ReactNode }) {
  const [config, setConfig] = useState<ComposerConfig | null>(null);
  const [text, setText] = useState("");
  return <ComposerContext.Provider value={{ config, setConfig, text, setText }}>{children}</ComposerContext.Provider>;
}

function useApi() {
  const api = useContext(ComposerContext);
  if (!api) throw new Error("ComposerProvider missing");
  return api;
}

/** Register the composer behaviour for this screen. `config` may change on every render. */
export function useComposer(config: ComposerConfig | null) {
  const { setConfig } = useApi();
  const ref = useRef(config);
  ref.current = config;
  const hintsKey = config?.hints?.join("|");
  const placeholder = config?.placeholder;
  const camera = config?.camera;
  const prominent = config?.prominent;
  const enabled = config !== null;
  useEffect(() => {
    if (!enabled) {
      setConfig(null);
      return;
    }
    setConfig({
      onSubmit: (t) => ref.current?.onSubmit(t),
      placeholder,
      hints: hintsKey ? hintsKey.split("|") : [],
      camera,
      prominent,
    });
    return () => setConfig(null);
  }, [setConfig, enabled, hintsKey, placeholder, camera, prominent]);
}

/** Prefill the composer (e.g. "the usual, but ") and focus it. */
export function useComposerText() {
  const { setText } = useApi();
  return (t: string) => {
    setText(t);
    window.setTimeout(() => {
      const el = document.getElementById("composer-input") as HTMLTextAreaElement | null;
      el?.focus();
      el?.setSelectionRange(t.length, t.length);
    }, 0);
  };
}

/** Textarea that grows with its content (up to 40% of the viewport, then scrolls). Enter sends, Shift+Enter adds a line. */
function AutoGrowInput({ big, placeholder, value, onChange, onEnter }: {
  big: boolean;
  placeholder: string;
  value: string;
  onChange: (t: string) => void;
  onEnter: () => void;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [value, big]);
  return (
    <textarea
      ref={ref}
      id="composer-input"
      rows={1}
      className={`min-w-0 flex-1 resize-none self-center overflow-y-auto bg-transparent px-1.5 outline-none max-h-[40vh] ${big ? "min-h-12 py-3 text-lg leading-6" : "min-h-10 py-2 text-base leading-6"}`}
      autoComplete="off"
      aria-label="Message"
      placeholder={placeholder}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
          e.preventDefault();
          onEnter();
        }
      }}
    />
  );
}

type SpeechCtor = new () => {
  lang: string;
  interimResults: boolean;
  onresult: ((e: { results: ArrayLike<ArrayLike<{ transcript: string }>> }) => void) | null;
  onend: (() => void) | null;
  start: () => void;
};

/** Fixed bottom bar: camera, attach, text, voice (Web Speech API, package 3.5), send. Hidden when no screen registered. */
export function Composer() {
  const { config, text, setText } = useApi();
  const camera = usePhoneCamera();
  const [listening, setListening] = useState(false);
  if (!config) return null;

  const send = (t: string) => {
    const v = t.trim();
    if (!v) return;
    setText("");
    config.onSubmit(v);
  };
  const w = window as unknown as { SpeechRecognition?: SpeechCtor; webkitSpeechRecognition?: SpeechCtor };
  const Speech = w.SpeechRecognition || w.webkitSpeechRecognition;
  const listen = () => {
    if (!Speech || listening) return;
    const r = new Speech();
    r.lang = "en-GB";
    r.interimResults = false;
    r.onresult = (e) => setText(e.results[0]?.[0]?.transcript ?? "");
    r.onend = () => setListening(false);
    setListening(true);
    r.start();
  };
  const big = !!config.prominent;
  const btn = big ? "size-12" : "size-10";

  return (
    <div
      className="fixed inset-x-0 bottom-0 z-25 pt-6"
      style={{
        paddingBottom: "calc(16px + env(safe-area-inset-bottom))",
        paddingLeft: "max(16px, env(safe-area-inset-left))",
        paddingRight: "max(16px, env(safe-area-inset-right))",
        background: "linear-gradient(to top, var(--bg) 60%, transparent)",
      }}
    >
      {big && (
        <div className="mx-auto mb-2 max-w-[880px] px-2 text-[15px] font-bold text-ink">
          Type or speak your order
        </div>
      )}
      {!!config.hints?.length && (
        <div className={`mx-auto mb-2.5 flex max-w-[880px] flex-wrap gap-2 ${big ? "justify-start px-1" : "justify-center"}`}>
          {config.hints.map((h) => (
            <button key={h} type="button" className="rounded-full bg-surface px-3 py-1.5 text-[13px] font-medium text-ink2 hover:bg-surface2 hover:text-ink" onClick={() => send(h)}>
              “{h.length > 40 ? h.slice(0, 38) + "…" : h}”
            </button>
          ))}
        </div>
      )}
      <form
        className={`mx-auto flex max-w-[880px] items-end gap-1.5 border bg-bg shadow-float ${big ? "rounded-[30px] border-2 border-ink p-2.5" : "rounded-[26px] border-line p-2"}`}
        onSubmit={(e) => {
          e.preventDefault();
          send(text);
        }}
      >
        {!big && (
          <button type="button" className="grid size-10 flex-none place-items-center rounded-full bg-ink text-bg" aria-label="Take a photo" onClick={() => camera.open(config.camera ?? "order")}>
            <Icon name="cam" />
          </button>
        )}
        <button type="button" className="grid size-10 flex-none place-items-center rounded-full text-ink2 hover:bg-surface" aria-label="Attach a file" onClick={() => camera.upload(config.camera ?? "order")}>
          <Icon name="clip" />
        </button>
        <AutoGrowInput
          big={big}
          placeholder={listening ? "Listening…" : config.placeholder ?? "What do you need?"}
          value={text}
          onChange={setText}
          onEnter={() => send(text)}
        />
        {Speech && (
          <button type="button" className={`grid ${btn} flex-none place-items-center rounded-full ${listening ? "animate-pulse-soft bg-must text-white" : "text-ink2 hover:bg-surface"}`} aria-label="Speak" onClick={listen}>
            <Icon name="mic" />
          </button>
        )}
        <button type="submit" className={`grid ${btn} flex-none place-items-center rounded-full bg-accent text-white`} aria-label="Send">
          <Icon name="up" />
        </button>
      </form>
    </div>
  );
}
