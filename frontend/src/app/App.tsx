import { QueryClientProvider } from "@tanstack/react-query";
import { lazy, Suspense, useEffect, type ReactNode } from "react";
import { ToastProvider } from "../ui";
import { Composer, ComposerProvider } from "./composer";
import { Header } from "./Header";
import { PhoneCameraProvider } from "./PhoneCamera";
import { queryClient, useKitchen } from "./queries";
import { navigate, useHashRoute, type Route } from "./route";
import { SignProvider } from "./signing";
import { ActivityView } from "../features/activity/ActivityView";
import { DemoMenu, OnboardingView } from "../features/onboarding";
import OrderView from "../features/order/OrderView";
import KitchenView from "../features/kitchen/KitchenView";

const UiGallery = import.meta.env.DEV ? lazy(() => import("./UiGallery")) : null;

/** One line per screen. F1–F4 replace their Placeholder with the real view. */
function Screen({ route }: { route: Route }): ReactNode {
  switch (route.tab) {
    case "order":
      return <OrderView start={route.param} />;
    case "activity":
      return <ActivityView orderId={route.param} />;
    case "kitchen":
      return <KitchenView />;
    case "onboard":
      return <OnboardingView />;
    case "ui":
      return UiGallery ? <Suspense fallback={null}><UiGallery /></Suspense> : null;
  }
}

function Shell() {
  const route = useHashRoute();
  const kitchen = useKitchen();

  // A kitchen without a profile version has not been onboarded yet (F3).
  const needsOnboarding = kitchen.isSuccess && !kitchen.data.version;
  useEffect(() => {
    if (needsOnboarding && (route.tab === "order" || route.tab === "kitchen")) navigate("onboard");
  }, [needsOnboarding, route.tab]);

  const showTabs = route.tab !== "onboard";
  return (
    <>
      <Header tab={route.tab} showTabs={showTabs} />
      <Screen route={route} />
      <Composer />
      {showTabs && <DemoMenu />}
    </>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <SignProvider>
          <PhoneCameraProvider>
            <ComposerProvider>
              <Shell />
            </ComposerProvider>
          </PhoneCameraProvider>
        </SignProvider>
      </ToastProvider>
    </QueryClientProvider>
  );
}
