"use client";

import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import MsIcon from "../../../_components/MsIcon";
import SearchPill from "./SearchPill";
import WeatherChip from "./WeatherChip";
import OperatorChip from "./OperatorChip";
import BackendChip from "./BackendChip";
import { useAppDispatch, useAppSelector } from "../../../_stores/hooks";
import { setVoiceMode } from "../../../_stores/avatarSlice";

/**
 * Design topbar (spec §3b, design lines 572-593): search pill · clock+date ·
 * weather · [Arrange on /dashboard] · TTS toggle · theme toggle · Operator chip.
 *
 * Arrange protocol (WP-D contract): this bar dispatches
 *   window.dispatchEvent(new CustomEvent("puffpai:toggle-arrange"))
 * and listens for
 *   "puffpai:arrange-state"  (CustomEvent<{ arranging: boolean }>)
 * which the dashboard page should emit whenever its arrange state changes
 * (including once on mount) so the button highlight stays in sync.
 */

/** Live clock: JBMono time in sky + uppercase long date (design line 577-580). */
function ClockBlock() {
  const [now, setNow] = useState<Date | null>(null);
  useEffect(() => {
    setNow(new Date());
    const iv = setInterval(() => setNow(new Date()), 5_000);
    return () => clearInterval(iv);
  }, []);
  return (
    <div className="flex-none text-right" style={{ lineHeight: 1.1, whiteSpace: "nowrap" }}>
      <div
        style={{
          font: "700 clamp(22px,1.7vw,30px) var(--font-jbmono), monospace",
          color: "var(--sky)",
          letterSpacing: "-.02em",
        }}
      >
        {now
          ? now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
          : "--:--"}
      </div>
      <div
        style={{
          font: "700 11px var(--font-jbmono), monospace",
          letterSpacing: ".14em",
          color: "var(--text3)",
          marginTop: 4,
        }}
      >
        {now
          ? now
              .toLocaleDateString("en-US", {
                weekday: "long",
                month: "short",
                day: "numeric",
              })
              .toUpperCase()
          : ""}
      </div>
    </div>
  );
}

function VDivider() {
  return (
    <div
      className="hidden flex-none md:block"
      style={{ width: 1, height: 44, background: "var(--line)" }}
    />
  );
}

/** 52×52 glass icon button (theme toggle, TTS toggle, hamburger). */
function IconButton({
  glyph,
  title,
  onClick,
  active = false,
  className = "",
}: {
  glyph: string;
  title: string;
  onClick: () => void;
  active?: boolean;
  className?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      aria-label={title}
      className={`grid flex-none cursor-pointer place-items-center transition-transform hover:scale-105 ${className}`}
      style={{
        width: 52,
        height: 52,
        borderRadius: 16,
        border: "1px solid var(--line)",
        background: active
          ? "linear-gradient(140deg, var(--pink), var(--magenta))"
          : "var(--card)",
        color: active ? "#fff" : "var(--text)",
      }}
    >
      <MsIcon name={glyph} size={24} />
    </button>
  );
}

/** Arrange button — only rendered on /dashboard. */
function ArrangeButton() {
  const [arranging, setArranging] = useState(false);

  useEffect(() => {
    const onState = (e: Event) => {
      const detail = (e as CustomEvent<{ arranging?: boolean }>).detail;
      if (detail && typeof detail.arranging === "boolean") {
        setArranging(detail.arranging);
      }
    };
    window.addEventListener("puffpai:arrange-state", onState);
    return () => window.removeEventListener("puffpai:arrange-state", onState);
  }, []);

  const toggle = () => {
    setArranging((a) => !a); // optimistic; page's arrange-state event corrects
    window.dispatchEvent(new CustomEvent("puffpai:toggle-arrange"));
  };

  return (
    <button
      type="button"
      onClick={toggle}
      className="hidden flex-none cursor-pointer items-center sm:flex"
      style={{
        gap: 9,
        height: 52,
        padding: "0 20px",
        borderRadius: 16,
        border: "1px solid var(--line)",
        font: "700 16px var(--font-nunito), sans-serif",
        background: arranging
          ? "linear-gradient(140deg, var(--pink), var(--magenta))"
          : "var(--card)",
        color: arranging ? "#fff" : "var(--text)",
      }}
    >
      <MsIcon name={arranging ? "check" : "open_with"} size={22} />
      {arranging ? "Done" : "Customize"}
    </button>
  );
}

/** Day/night toggle: writes puff-theme-override + flips the root attribute. */
function ThemeToggle() {
  const [theme, setTheme] = useState<"day" | "night">("night");

  useEffect(() => {
    const root = document.documentElement;
    const read = () =>
      setTheme(root.getAttribute("data-puff-theme") === "day" ? "day" : "night");
    read();
    // Keep the icon honest if Settings / the auto-clock script flips the theme.
    const mo = new MutationObserver(read);
    mo.observe(root, { attributes: true, attributeFilter: ["data-puff-theme"] });
    return () => mo.disconnect();
  }, []);

  const toggle = () => {
    const next = theme === "day" ? "night" : "day";
    try {
      localStorage.setItem("puff-theme-override", next);
    } catch {
      /* storage unavailable — still flip for this session */
    }
    document.documentElement.setAttribute("data-puff-theme", next);
    setTheme(next);
  };

  return (
    <IconButton
      glyph={theme === "day" ? "dark_mode" : "light_mode"}
      title={theme === "day" ? "Switch to night mode" : "Switch to day mode"}
      onClick={toggle}
    />
  );
}

/** Old NavBar's TTS toggle, kept as a compact button left of the theme toggle. */
function TtsToggle() {
  const dispatch = useAppDispatch();
  const voiceMode = useAppSelector((s) => s.avatar.voiceMode);
  return (
    <IconButton
      glyph={voiceMode ? "record_voice_over" : "voice_over_off"}
      title={voiceMode ? "Voice replies: on" : "Voice replies: off"}
      onClick={() => dispatch(setVoiceMode(!voiceMode))}
      active={voiceMode}
      className="hidden sm:grid"
    />
  );
}

type Props = {
  isMobileAsideOpen: boolean;
  onToggleMobileAside: () => void;
};

export default function NavBar({ isMobileAsideOpen, onToggleMobileAside }: Props) {
  const pathname = usePathname();
  const isDashboard = pathname === "/dashboard";

  return (
    <header
      className="flex flex-none items-center"
      style={{
        height: "clamp(72px,9vh,96px)",
        gap: "clamp(10px,1.2vw,20px)",
        padding: "0 clamp(18px,2vw,36px)",
        borderBottom: "1px solid var(--line)",
      }}
    >
      {/* Mobile hamburger — the rail is an off-canvas drawer below lg */}
      <IconButton
        glyph={isMobileAsideOpen ? "close" : "menu"}
        title={isMobileAsideOpen ? "Close menu" : "Open menu"}
        onClick={onToggleMobileAside}
        className="lg:hidden"
      />

      <SearchPill />

      {/* Push the instrument cluster right even when the pill maxes out */}
      <div className="min-w-0 flex-1" />

      {/* The bot's parking pocket. The home page has no hero any more, so
          without it the bubble floats over cards and forms. FloatingPuff measures [data-puff-dock] and parks
          here, small, on every page; below md she free-floats. */}
      <div
        data-puff-dock
        aria-hidden
        className="hidden h-full flex-none md:block"
        style={{ width: "72px" }}
      />

      {/* Which bot this dashboard is driving. Before the clock, because on a
          machine that runs two builds it is the first thing worth checking. */}
      <BackendChip />
      <div className="hidden flex-none md:block">
        <ClockBlock />
      </div>
      <VDivider />
      <div className="hidden md:flex">
        <WeatherChip />
      </div>
      <VDivider />

      {isDashboard && <ArrangeButton />}

      <TtsToggle />
      <ThemeToggle />
      <OperatorChip />
    </header>
  );
}
