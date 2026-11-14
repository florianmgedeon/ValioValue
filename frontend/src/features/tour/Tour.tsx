import { useCallback, useEffect, useLayoutEffect, useState } from "react";
import { createPortal } from "react-dom";
import { navigate, type Tab } from "../../app/route";
import { Button, Icon, type IconName } from "../../ui";

/**
 * Prototype: a short tour of the tabs, shown once after onboarding.
 * Anchors are elements with `data-tour="<id>"`. Start it with `startTour()`.
 */
interface Step {
  /** `data-tour` id to spotlight; none = centered card. */
  anchor?: string;
  /** Tab to show behind the spotlight. */
  tab?: Tab;
  /** Second route segment, e.g. `may` for `#kitchen/may`. */
  param?: string;
  icon: IconName;
  title: string;
  body: string;
}

const STEPS: Step[] = [
  {
    icon: "spark",
    title: "Your kitchen is set up.",
    body: "Three tabs, one idea: you say what the kitchen needs, I do the ordering within your rules. 20 seconds to see where things are?",
  },
  {
    anchor: "tab-order",
    tab: "order",
    icon: "cam",
    title: "Order",
    body: "Snap a photo of a list, tell me what you need, or just say \"the usual\": I show you what changed since last time first. Nothing goes to Valio Aimo without your OK.",
  },
  {
    anchor: "tab-activity",
    tab: "activity",
    icon: "doc",
    title: "Activity",
    body: "Every order, what I did and why: which product I picked, what I left out, who approved it. Check it any time later.",
  },
  {
    anchor: "kitchen-know",
    tab: "kitchen",
    icon: "bulb",
    title: "Kitchen: what I know",
    body: "What I learned from your documents and orders: your kitchen, your usual products. Correct me here, no signature needed. New things wait under \"To check\" until you say yes.",
  },
  {
    anchor: "kitchen-may",
    tab: "kitchen",
    param: "may",
    icon: "lock",
    title: "Kitchen: what I may do",
    body: "Never-order rules, preferences, substitutes and my limits. Only you change these, with your signature, and Valio Aimo checks them on every order.",
  },
  {
    anchor: "autonomy",
    tab: "kitchen",
    param: "may",
    icon: "hand",
    title: "What I may order alone",
    body: "Always visible up here. Start strict, loosen it when you trust me, take it back any time. Tap it to come back to this page.",
  },
];

const KEY = "tour.v2";
const EVENT = "tour:start";

function read(): string | null {
  try {
    return localStorage.getItem(KEY);
  } catch {
    return null;
  }
}
function write(v: string) {
  try {
    localStorage.setItem(KEY, v);
  } catch {
    /* private window: the tour just shows again next time */
  }
}

/** Ask for the tour on the next screen (called when onboarding ends). */
export function queueTour() {
  write("pending");
}
/** Forget that the tour was shown (demo: restarting onboarding shows it again afterwards). */
export function resetTour() {
  try {
    localStorage.removeItem(KEY);
  } catch {
    /* nothing stored */
  }
}
/** Start the tour now (help button, demo menu). */
export function startTour() {
  window.dispatchEvent(new Event(EVENT));
}

type Rect = { top: number; left: number; width: number; height: number };
const PAD = 6;
const CARD_W = 340;

function rectOf(anchor?: string): Rect | null {
  if (!anchor) return null;
  const el = document.querySelector(`[data-tour="${anchor}"]`);
  if (!el) return null;
  const r = el.getBoundingClientRect();
  if (!r.width || !r.height) return null;
  return { top: r.top - PAD, left: r.left - PAD, width: r.width + PAD * 2, height: r.height + PAD * 2 };
}

export function Tour() {
  const [step, setStep] = useState<number | null>(null);
  const [rect, setRect] = useState<Rect | null>(null);

  const close = useCallback(() => {
    write("done");
    setStep(null);
  }, []);

  useEffect(() => {
    if (read() === "pending") window.setTimeout(() => setStep(0), 400);
    const h = () => setStep(0);
    window.addEventListener(EVENT, h);
    return () => window.removeEventListener(EVENT, h);
  }, []);

  const s = step === null ? null : STEPS[step];

  useEffect(() => {
    if (s?.tab) navigate(s.tab, s.param);
  }, [s]);

  useLayoutEffect(() => {
    if (!s) return;
    const update = () => setRect(rectOf(s.anchor));
    update();
    // After the tab switch re-renders: bring an anchor far down the page into the upper third, then measure.
    const t = window.setTimeout(() => {
      const el = s.anchor ? document.querySelector(`[data-tour="${s.anchor}"]`) : null;
      const r = el?.getBoundingClientRect();
      if (r && (r.top > window.innerHeight * 0.55 || r.bottom < 0)) window.scrollBy({ top: r.top - window.innerHeight * 0.3 });
      update();
    }, 60);
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);
    return () => {
      window.clearTimeout(t);
      window.removeEventListener("resize", update);
      window.removeEventListener("scroll", update, true);
    };
  }, [s]);

  useEffect(() => {
    if (step === null) return;
    const h = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
      if (e.key === "ArrowRight") setStep((i) => (i === null ? i : Math.min(i + 1, STEPS.length - 1)));
      if (e.key === "ArrowLeft") setStep((i) => (i === null ? i : Math.max(i - 1, 0)));
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [step, close]);

  if (step === null || !s) return null;
  const last = step === STEPS.length - 1;
  const spot = s.anchor ? rect : null;

  // Card under the spotlight, kept inside the viewport. Narrow screens: a sheet at the bottom.
  const vw = window.innerWidth;
  const narrow = vw < 560;
  // Not enough room below the spotlight: the card goes above it.
  const above = !!spot && window.innerHeight - (spot.top + spot.height) < 260;
  const cardStyle: React.CSSProperties = narrow
    ? { left: 12, right: 12, bottom: "calc(12px + env(safe-area-inset-bottom))" }
    : spot
      ? { ...(above ? { bottom: window.innerHeight - spot.top + 14 } : { top: spot.top + spot.height + 14 }), left: Math.max(16, Math.min(spot.left + spot.width / 2 - CARD_W / 2, vw - CARD_W - 16)), width: CARD_W }
      : { inset: 0, margin: "auto", width: 420, height: "fit-content" };
  const arrowLeft = spot && !narrow ? spot.left + spot.width / 2 - (cardStyle.left as number) : null;

  return createPortal(
    <div className="fixed inset-0 z-[65]" role="dialog" aria-modal="true" aria-label={`Tour: ${s.title}`}>
      {spot ? (
        <div
          className="pointer-events-none fixed rounded-full transition-all duration-300"
          style={{ ...spot, boxShadow: "0 0 0 9999px rgba(0,0,0,.48), 0 0 0 3px var(--accent)" }}
        />
      ) : (
        <div className="fixed inset-0" style={{ background: "rgba(0,0,0,.48)" }} />
      )}
      <div className="rise fixed rounded-[20px] bg-bg p-5 text-ink shadow-float" style={cardStyle} key={step}>
        {arrowLeft !== null && (
          <span className={`absolute ${above ? "-bottom-[7px]" : "-top-[7px]"} size-3.5 rotate-45 bg-bg`} style={{ left: arrowLeft - 7 }} aria-hidden="true" />
        )}
        <div className="flex items-center gap-2.5">
          <span className="grid size-8 place-items-center rounded-full bg-accent-soft text-accent">
            <Icon name={s.icon} size={16} />
          </span>
          <b className="text-[17px]">{s.title}</b>
          <span className="flex-1" />
          {step > 0 && <span className="text-xs text-ink3">{step} of {STEPS.length - 1}</span>}
        </div>
        <p className="mt-2.5 text-[14.5px] leading-snug text-ink2">{s.body}</p>
        <div className="mt-4 flex items-center gap-2">
          {step > 0 && (
            <div className="flex gap-1" aria-hidden="true">
              {STEPS.slice(1).map((_, i) => (
                <span key={i} className={`h-1.5 rounded-full transition-all ${i + 1 === step ? "w-4 bg-accent" : "w-1.5 bg-line"}`} />
              ))}
            </div>
          )}
          <span className="flex-1" />
          {!last && (
            <button type="button" className="px-2 text-sm font-semibold text-ink3" onClick={close}>
              {step === 0 ? "Not now" : "Skip"}
            </button>
          )}
          <Button small variant="primary" iconAfter={last ? undefined : "arrow"} onClick={() => (last ? close() : setStep(step + 1))}>
            {step === 0 ? "Show me" : last ? "Got it" : "Next"}
          </Button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
