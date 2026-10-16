"use client";

import { ReactNode, useEffect, useState } from "react";
import { useBuildMenu } from "./_lib/useBuildMenu";
import NavBar from "./_components/NavBar";
import AsideMenu from "./_components/AsideMenu";
import FooterBar from "./_components/FooterBar";
import DeviceDiscoveryToast from "../_components/DeviceDiscoveryToast";
import ExternalLinkGuard from "../_components/ExternalLinkGuard";
import OnboardingGate from "../_components/OnboardingGate";
import FloatingPuff from "./_components/FloatingPuff";
import WelcomeTour from "./_components/WelcomeTour";
import Walkthrough from "./_components/Walkthrough";
import WakeUp from "@/app/_components/Persona/WakeUp";
import {
  installAudioUnlockListeners,
  requestNotificationPermission,
} from "../_lib/audioUnlock";

type Props = {
  children: ReactNode;
};

/**
 * App shell: a full-height flex row of the
 * glass rail (AsideMenu) + main column (topbar NavBar, scrollable content).
 *
 * The content wrapper is the scroll container (not the body): legacy pages
 * scroll under a always-visible topbar, and the dashboard's kiosk grid can
 * fill the remaining height with its own internal scrolling.
 * Below lg the rail becomes an off-canvas drawer behind the hamburger.
 */
export default function LayoutAuthenticated({ children }: Props) {
  const [isAsideMobileExpanded, setIsAsideMobileExpanded] = useState(false);

  // The rail lists what THIS build carries, not the catalogue. Two gates:
  // which apps are installed (`apps/<id>/app.json` via /build/self) and which
  // surfaces the interview loaded. Fails open — while the fetch is in flight,
  // and if the backend never answers, this is the full menu.
  const { menu, rail } = useBuildMenu();

  // EMBED MODE: when a /dashboard/* route is rendered inside an iframe, draw
  // ONLY the page content — clean full-bleed, no rail, no topbar.
  //
  // This was added for the "show me X" overlay, which framed a dashboard route
  // and, without the guard, got the rail and topbar redrawn inside the frame
  // plus another overlay framing the route again: infinite nesting. That
  // overlay is gone from this build (its two backend routes are not here), but
  // the guard stays — chrome inside a frame is wrong however the frame got
  // there, and this costs one boolean. Resolved after mount so SSR and the
  // first client render agree (avoids a hydration mismatch); the brief chrome
  // flash inside the frame is sub-100ms on the kiosk.
  const [embedded, setEmbedded] = useState(false);
  useEffect(() => {
    try {
      // A page opened in its own window ("Own window" on the Developer
      // command line) is drawn the same way: just the page.
      const ownWindow = new URLSearchParams(window.location.search).get("window") === "1";
      setEmbedded(window.self !== window.top || ownWindow);
    } catch {
      // Cross-origin parent throws on access → we are definitely framed.
      setEmbedded(true);
    }
  }, []);

  // Unlock the audio context on first user interaction (browser autoplay
  // policy) and request notification permission for alarm reminders.
  useEffect(() => {
    installAudioUnlockListeners();
    requestNotificationPermission();
  }, []);

  // QA hook: ?puffAside=1 opens the mobile drawer on load so headless
  // screenshot runs can verify it (mirrors the ?puffTheme override).
  useEffect(() => {
    try {
      if (new URLSearchParams(window.location.search).has("puffAside")) {
        setIsAsideMobileExpanded(true);
      }
    } catch {
      /* no-op */
    }
  }, []);

  // Framed: content only. No rail, no topbar, no FloatingPuff.
  if (embedded) {
    return <div className="h-dvh overflow-y-auto">{children}</div>;
  }

  // First-run: if the backend says setup was never completed, the shell is
  // replaced by a redirect to /onboarding. Wrapped around the chrome rather
  // than the content so a first-run box never paints a rail and a topbar over
  // a bot that does not exist yet. The embedded branch above returns before
  // this on purpose — a framed route must never redirect the frame into the
  // wizard.
  return (
    <OnboardingGate>
    <div className="flex h-dvh overflow-hidden">
      <AsideMenu
        menu={menu}
        rail={rail}
        isAsideMobileExpanded={isAsideMobileExpanded}
        onRouteChange={() => setIsAsideMobileExpanded(false)}
        onOverlayClick={() => setIsAsideMobileExpanded(false)}
      />

      <div className="flex min-w-0 flex-1 flex-col">
        <NavBar
          isMobileAsideOpen={isAsideMobileExpanded}
          onToggleMobileAside={() => setIsAsideMobileExpanded((v) => !v)}
        />
        <div className="min-h-0 flex-1 overflow-y-auto">
          {children}
          <FooterBar />
        </div>
      </div>

      {/* Global toast that fires when a new device lands in the pending
          queue. Mounted at the layout level so it shows on every page. */}
      <DeviceDiscoveryToast />
      {/* Renders nothing until a link tries to leave the app. On the KIOSK
          (chromium --kiosk: no tabs, no back button) a target="_blank" is a
          dead end with no way out, so every external link is intercepted at
          the document in the capture phase and shown in an escapable overlay
          instead. Mounted here so it covers every dashboard page, including
          links that only exist at runtime inside chat answers and RSS items. */}
      <ExternalLinkGuard />
      {/* Puff herself — floating, draggable, on every page (except the full
          avatar page, which owns the stage). */}
      <FloatingPuff />
      {/* The new bot wakes up first (once per build, and from Setup's "Meet my
          bot again"); its "Show me around" starts the welcome tour. The tour
          also shows from Setup's "Show me around again". Both render nothing
          otherwise. */}
      <WakeUp />
      <WelcomeTour />
      {/* The walk around the pages: from the end of the tour and from
          Settings ("Show me around"). Renders nothing until started. */}
      <Walkthrough />
      {/* No "pull it up on screen" overlay. It polled GET /dashboard/display
          for a queued item and fetched /vault/view/<token> for a gated one, and
          this build's backend has neither route — so it polled a 404 on a timer
          forever and could never show anything. The framed-render guard above
          stays: it is cheap, and it is the right behaviour for any iframe. */}
    </div>
    </OnboardingGate>
  );
}
