import { navigate } from "../../app/route";
import { startTour } from "../tour/Tour";

/** Dev / demo only: go back to onboarding. The kitchen profile stays; onboarding adds to it. */
export function DemoMenu() {
  if (!import.meta.env.DEV) return null;
  return (
    <div className="fixed bottom-[calc(16px+env(safe-area-inset-bottom))] left-[max(16px,env(safe-area-inset-left))] z-30 hidden text-xs text-ink3 min-[1240px]:block">
      Demo ·{" "}
      <button type="button" className="link" onClick={() => navigate("onboard")}>
        restart onboarding
      </button>
      {" · "}
      <button type="button" className="link" onClick={startTour}>
        show tour
      </button>
    </div>
  );
}
