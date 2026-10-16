import type { Metadata, Viewport } from "next";
import localFont from "next/font/local";
import "../css/main.css";
import StoreProvider from "./_stores/StoreProvider";
import IdleMotionGuard from "./_components/IdleMotionGuard";
import RootStateGuard from "./_components/RootStateGuard";
import { LOOK_BOOT_SCRIPT } from "./_lib/looks";
import LookSync from "./_components/LookSync";

// Design-system type roles (display / body / stats). The families are
// *vendored* under app/_fonts/ (OFL 1.1, see _fonts/LICENSE-fonts.txt) and
// declared through next/font/local, so the intended look survives a build on a
// box with no egress. Next's Google-fonts helper used to declare them here, but
// it downloads the families at build time — which breaks every build without
// network, and is the wrong default for an offline-first product.
//
// Each helper publishes its own CSS variable (--font-*-file); css/_fonts.css
// then composes the three variables the theme actually reads
// (--font-fredoka / --font-nunito / --font-jbmono) as "our file first, then
// the operating-system stack", so a page still renders in the right role if a
// font file ever fails to load.
const fredoka = localFont({
  // Two-axis variable font (wght 300–700, wdth 75–125). We only vary weight;
  // wdth stays at its 100 default.
  src: "./_fonts/fredoka/Fredoka[wdth,wght].woff2",
  weight: "300 700",
  style: "normal",
  display: "swap",
  variable: "--font-fredoka-file",
});

const nunito = localFont({
  // Variable font, wght 200–1000. Italics are synthesized by the browser; the
  // upstream Nunito-Italic file is not vendored (nothing in the dashboard sets
  // italic body copy, and it would nearly double the font payload).
  src: "./_fonts/nunito/Nunito[wght].woff2",
  weight: "200 1000",
  style: "normal",
  display: "swap",
  variable: "--font-nunito-file",
});

const jbMono = localFont({
  // JetBrains Mono ships static instances upstream, not a variable font, so we
  // vendor exactly the weights the dashboard asks for: 400 (bare
  // font-family uses), 500, 600 and 700 (see the `font:` shorthands in
  // app/dashboard/**, app/onboarding/** and app/send/page.tsx).
  src: [
    { path: "./_fonts/jetbrains-mono/JetBrainsMono-Regular.woff2", weight: "400", style: "normal" },
    { path: "./_fonts/jetbrains-mono/JetBrainsMono-Medium.woff2", weight: "500", style: "normal" },
    { path: "./_fonts/jetbrains-mono/JetBrainsMono-SemiBold.woff2", weight: "600", style: "normal" },
    { path: "./_fonts/jetbrains-mono/JetBrainsMono-Bold.woff2", weight: "700", style: "normal" },
  ],
  display: "swap",
  variable: "--font-jbmono-file",
});

const title = "PuffPAI: Privacy-First AI Dashboard";

const description = "PuffPAI: Privacy-first, offline-capable AI assistant for edge hardware by Cypher Sisters";

export const metadata: Metadata = {
  title,
  description,
  applicationName: "Puff",
  // Web App Manifest lives at app/manifest.ts (auto-linked by Next). These
  // fields make iOS "Add to Home Screen" open /send fullscreen like an app
  // with the Puff cloud icon — iOS ignores the manifest for install and reads
  // these apple-specific tags instead.
  appleWebApp: {
    capable: true,
    statusBarStyle: "black-translucent",
    title: "Puff",
  },
  icons: {
    icon: "/favicon.png",
    apple: "/puffpai-icon-180.png",
  },
};

// Without this the phone renders /send at a 980px desktop viewport and scales
// it down — tap targets end up unusable. App Router serves this as the
// <meta name="viewport"> tag. themeColor tints the iOS/Android status bar and
// standalone chrome to the dark galaxy background.
export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: "#160B28",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <StoreProvider>
      {/* suppressHydrationWarning: data-puff-theme depends on the client's
          local clock, so the server can never render it to match. */}
      {/* The three next/font variable classes go on <html> — which *is* :root,
          so the composed --font-fredoka / --font-nunito / --font-jbmono in
          css/_fonts.css can read them. */}
      <html
        lang="en"
        className={`dark style-basic ${fredoka.variable} ${nunito.variable} ${jbMono.variable}`}
        suppressHydrationWarning
      >
        <body className={`aero-bg antialiased text-(--text)`}>
          {/* Material Symbols Rounded ligature font (icons). React 19 hoists
              this <link> into <head>; preloading avoids a flash of raw
              ligature text ("mic", "settings") before the font arrives. */}
          <link
            rel="preload"
            href="/fonts/material-symbols-rounded.woff2"
            as="font"
            type="font/woff2"
            crossOrigin="anonymous"
          />
          {/* Auto day/night theme: pastel sky 07–19h, starry moonlit night
              otherwise. Runs before hydration to avoid a theme flash.
              QA override: ?puffTheme=day|night pins the theme (persists via
              localStorage); ?puffTheme=auto returns to the clock.
              Display mode: ?puffMode=kiosk pins html.puff-kiosk (persists via
              localStorage, same as the Settings "Kiosk display" toggle) — the
              motion governor in css/_theme.css then keeps the scene static so
              an always-on appliance screen never pins the GPU;
              ?puffMode=normal clears it. Applied here, pre-hydration, so a
              kiosk boot never plays a single animated frame. */}
          <script
            dangerouslySetInnerHTML={{
              __html:
                "(function(){function s(){try{var q=new URLSearchParams(location.search).get('puffTheme');if(q==='auto'){localStorage.removeItem('puff-theme-override');}else if(q==='day'||q==='night'){localStorage.setItem('puff-theme-override',q);}var o=localStorage.getItem('puff-theme-override');if(o==='day'||o==='night'){document.documentElement.setAttribute('data-puff-theme',o);return;}}catch(e){}var h=new Date().getHours();document.documentElement.setAttribute('data-puff-theme',(h>=7&&h<19)?'day':'night');}s();setInterval(s,600000);try{var r=document.documentElement;var m=new URLSearchParams(location.search).get('puffMode');if(m==='kiosk'){localStorage.setItem('puffpai_display_mode','kiosk');}else if(m==='normal'||m==='auto'){localStorage.removeItem('puffpai_display_mode');}r.classList.toggle('puff-kiosk',localStorage.getItem('puffpai_display_mode')==='kiosk');r.classList.toggle('puff-big-touch',localStorage.getItem('puffpai_big_touch')==='1');r.classList.toggle('puff-no-motion',localStorage.getItem('puffpai_ambient_motion')==='0');}catch(e){}})();",
            }}
          />
          {/* The owner's chosen look (the Looks page), applied before first
              paint from this browser's saved copy: allowlisted CSS custom
              properties only, each value re-checked (app/_lib/looks.ts).
              ?puffLook=default shows the bot's own look for this session. */}
          <script dangerouslySetInnerHTML={{ __html: LOOK_BOOT_SCRIPT }} />
          {/* Hidden-tab rAF shim. Chrome never fires requestAnimationFrame
              in hidden/background tabs, but React schedules its streamed-
              content reveal ($RC/$RV) and Suspense retries through rAF —
              so any page loaded while the tab is not visible sits on its
              loading skeleton forever (kiosk displays, background tabs,
              minimized windows). While hidden, route rAF through a short
              setTimeout; visible tabs keep native behavior. Must run
              before Next's runtime scripts, hence inline at body start. */}
          <script
            dangerouslySetInnerHTML={{
              __html:
                "(function(){var raf=window.requestAnimationFrame.bind(window);var caf=window.cancelAnimationFrame.bind(window);var shim=new Set();window.requestAnimationFrame=function(cb){if(document.visibilityState!=='hidden')return raf(cb);var id=window.setTimeout(function(){shim.delete(id);cb(performance.now());},50);shim.add(id);return id;};window.cancelAnimationFrame=function(id){if(shim.has(id)){shim.delete(id);window.clearTimeout(id);return;}caf(id);};})();",
            }}
          />
          {/* Top cloud banner (design line 531) — fixed, fades to transparent,
              behind all content; styled as .puff-cloud-banner in _theme.css.
              A real element (not body::before) so night keeps its starfield. */}
          <div className="puff-cloud-banner" aria-hidden />
          {/* Pauses the ambient animation loops after a few minutes without
              input (voice counts as presence) — see the motion governor in
              css/_theme.css and the GPU story in IdleMotionGuard itself. */}
          <IdleMotionGuard />
          {/* Re-applies data-puff-theme + the display-mode classes after
              hydration strips them off <html>, and moves the auto theme across
              the 07:00 / 19:00 boundary without a reload. */}
          <RootStateGuard />
          {/* Asks the backend once which look is in use and keeps this
              browser's saved copy the same (app/_lib/looks.ts). */}
          <LookSync />
          {children}
        </body>
      </html>
    </StoreProvider>
  );
}
