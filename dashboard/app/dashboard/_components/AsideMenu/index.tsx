"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
// an app's own page is a full page load (see AppLink)
import AppLink from "@/app/dashboard/apps/_lib/AppLink";
import { usePathname } from "next/navigation";
import MsIcon from "../../../_components/MsIcon";
import OverlayLayer from "../../../_components/OverlayLayer";
import { MenuAsideItem } from "../../../_interfaces";
import { apiGet } from "../../../_lib/api";
import CustomizeRail from "./CustomizeRail";
import { consentLine, countWords, onCount, onNames, type ConsentSummary } from "../../_lib/consentWords";

type Props = {
  menu: MenuAsideItem[];
  /** Every rail item, hidden ones too, for the Customize panel. */
  rail?: MenuAsideItem[];
  isAsideMobileExpanded: boolean;
  onRouteChange: () => void;
  onOverlayClick: () => void;
};

/** Active-route check: exact for the dashboard root, prefix for subpages. */
function isActiveHref(href: string | undefined, pathname: string): boolean {
  if (!href) return false;
  if (href === "/dashboard") return pathname === "/dashboard";
  return pathname === href || pathname.startsWith(`${href}/`);
}

/** Primary nav button (design line 540-544): 46-56px, radius 16, Nunito 600. */
function RailPrimaryItem({
  item,
  pathname,
  onRouteChange,
}: {
  item: MenuAsideItem;
  pathname: string;
  onRouteChange: () => void;
}) {
  const active = isActiveHref(item.href, pathname);
  return (
    <AppLink
      href={item.href ?? "#"}
      onClick={onRouteChange}
      className="flex flex-none items-center overflow-hidden whitespace-nowrap transition-colors"
      style={{
        gap: 14,
        minHeight: 46,
        height: "clamp(46px,4.6vh,56px)",
        padding: "0 16px",
        borderRadius: 16,
        font: "600 clamp(14px,1.05vw,17px) var(--font-nunito), sans-serif",
        background: active
          ? "linear-gradient(140deg, var(--pink), var(--magenta))"
          : "transparent",
        color: active ? "#fff" : "var(--text2)",
        boxShadow: active ? "0 10px 26px var(--glow1)" : "none",
      }}
    >
      {item.msIcon && <MsIcon name={item.msIcon} size={25} />}
      {item.label}
    </AppLink>
  );
}

/** Secondary nav row (design line 547-551): dimmed link; active row lights up. */
function RailSecondaryItem({
  item,
  pathname,
  onRouteChange,
}: {
  item: MenuAsideItem;
  pathname: string;
  onRouteChange: () => void;
}) {
  const active = isActiveHref(item.href, pathname);
  return (
    <AppLink
      href={item.href ?? "#"}
      onClick={onRouteChange}
      className="flex flex-none items-center overflow-hidden whitespace-nowrap transition-opacity"
      style={{
        gap: 14,
        minHeight: 36,
        height: "clamp(36px,4vh,48px)",
        padding: "0 16px",
        borderRadius: 16,
        font: "600 clamp(13px,1vw,16px) var(--font-nunito), sans-serif",
        opacity: active ? 1 : 0.62,
        background: active
          ? "linear-gradient(140deg, var(--pink), var(--magenta))"
          : "transparent",
        color: active ? "#fff" : "var(--text2)",
        boxShadow: active ? "0 10px 26px var(--glow1)" : "none",
      }}
    >
      {item.msIcon && <MsIcon name={item.msIcon} size={23} />}
      {item.label}
    </AppLink>
  );
}

/** What the live card reads: /system/privacy, the same live state the
 *  Settings privacy lines come from. Null until it answers. */
type PrivateState = {
  vault?: "open" | "locked" | "missing" | "not_plugged_in" | "unknown";
  outside_services_on?: number | null;
  consent?: ConsentSummary | null;
};

const VAULT_LINE: Record<string, string> = {
  locked: "Locked vault",
  open: "Vault is open",
  missing: "No vault yet",
  not_plugged_in: "Vault drive not plugged in",
};

/** YOUR PRIVATE STUFF card (design line 557-568). It reads the real vault
 *  state and how many outside services are on, again on every page change
 *  and every minute, and opens the Developer area. It used to be fixed text
 *  that said "Locked vault" whatever the vault was. */
function PrivateStuffCard({
  onRouteChange,
  pathname,
}: {
  onRouteChange: () => void;
  pathname: string;
}) {
  const [live, setLive] = useState<PrivateState | null>(null);

  useEffect(() => {
    let alive = true;
    const read = () =>
      apiGet<PrivateState>("/system/privacy", { cache: "no-store" })
        .then((p) => alive && setLive(p))
        .catch(() => alive && setLive({}));
    read();
    const timer = setInterval(read, 60_000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [pathname]);

  const vault = live?.vault;
  // The count, the names and the consent line all come from the backend's
  // one consent summary, the same "What leaves your computer" and the home
  // page show. Nothing here is fixed text.
  const on = onCount(live);
  const title = live === null ? "Checking your vault" : VAULT_LINE[vault ?? ""] ?? "Vault not checked";
  const sub = countWords(live);
  const names = onNames(live);
  const promise = consentLine(live);
  const locked = vault === "locked";

  return (
    <Link
      href="/dashboard/developer"
      onClick={onRouteChange}
      data-private-card={vault ?? "unknown"}
      className="block flex-none transition-transform hover:scale-[1.015]"
      style={{
        marginTop: 16,
        padding: 16,
        borderRadius: 20,
        background: "var(--card)",
        border: "1px solid var(--line)",
        color: "var(--text)",
      }}
    >
      <div
        style={{
          font: "700 11px/1 var(--font-jbmono), monospace",
          letterSpacing: ".14em",
          color: "var(--text3)",
          marginBottom: 12,
        }}
      >
        YOUR PRIVATE STUFF
      </div>
      <div className="flex" style={{ gap: 6, marginBottom: 12 }}>
        <div
          className="flex-1"
          style={{
            height: 8,
            borderRadius: 99,
            background: "var(--magenta)",
            opacity: locked ? 1 : 0.35,
            boxShadow: locked ? "0 0 14px var(--magenta)" : "none",
          }}
        />
        <div
          className="flex-1"
          style={{ height: 8, borderRadius: 99, background: "var(--sky)", opacity: 0.28 }}
        />
        <div
          className="flex-1"
          style={{
            height: 8,
            borderRadius: 99,
            background: "var(--lemon)",
            opacity: typeof on === "number" && on > 0 ? 0.8 : 0.16,
          }}
        />
      </div>
      <div className="flex items-center" style={{ gap: 9 }}>
        <MsIcon
          name={vault === "open" ? "lock_open" : "shield_lock"}
          size={20}
          style={{ color: "var(--magenta)" }}
        />
        <div>
          <div style={{ font: "700 15px var(--font-nunito), sans-serif", lineHeight: 1.1 }}>
            {title}
          </div>
          <div style={{ font: "500 12px var(--font-nunito), sans-serif", color: "var(--text3)" }}>
            {sub}
          </div>
        </div>
      </div>
      {promise && (
        <div
          data-consent-line
          style={{ marginTop: 10, font: "600 12px/1.35 var(--font-nunito), sans-serif", color: "var(--text2)" }}
        >
          {promise}
        </div>
      )}
      {names.length > 0 && (
        // Three lines at most, so the menu above keeps its room; the whole
        // list is in the tooltip and on every page the card leads to.
        <div
          data-outside-on={names.length}
          title={names.join(", ")}
          style={{
            marginTop: 6,
            font: "500 11px/1.35 var(--font-nunito), sans-serif",
            color: "var(--text3)",
            display: "-webkit-box",
            WebkitLineClamp: 3,
            WebkitBoxOrient: "vertical",
            overflow: "hidden",
          }}
        >
          On: {names.join(", ")}
        </div>
      )}
    </Link>
  );
}

/**
 * The design rail (spec §3a / design lines 533-569). Static glass column on
 * lg+; slide-in overlay drawer below lg (small screens must still navigate).
 */
export default function AsideMenu({
  menu,
  rail = [],
  isAsideMobileExpanded = false,
  onRouteChange,
  onOverlayClick,
}: Props) {
  const pathname = usePathname();
  const [customizing, setCustomizing] = useState(false);

  const primary = menu.filter((m) => m.railGroup === "primary");
  const secondary = menu.filter((m) => m.railGroup === "secondary");
  const settings = menu.filter((m) => m.railGroup === "settings");

  return (
    <>
      <aside
        className={`fixed inset-y-0 z-50 flex w-[264px] flex-col transition-all duration-300 lg:static lg:z-auto lg:h-full lg:w-[clamp(212px,15vw,264px)] lg:flex-none ${
          isAsideMobileExpanded ? "left-0" : "-left-[264px] lg:left-0"
        }`}
        style={{
          background: "var(--railBg)",
          backdropFilter: "blur(22px)",
          WebkitBackdropFilter: "blur(22px)",
          borderRight: "1px solid var(--line)",
          padding: "26px 18px 20px",
        }}
      >
        {/* Brand row (design line 534-537) */}
        <div className="flex flex-wrap items-center" style={{ gap: 12, padding: "0 8px 22px" }}>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src="/puffpai-logo.png"
            alt="PuffPAI"
            style={{
              width: "clamp(40px,3vw,54px)",
              height: "clamp(40px,3vw,54px)",
              objectFit: "contain",
              filter: "drop-shadow(0 0 16px var(--glow1))",
              animation: "puffFloat 6s ease-in-out infinite",
            }}
          />
          <div
            className="whitespace-nowrap"
            style={{
              font: "600 clamp(19px,1.5vw,27px) var(--font-fredoka), sans-serif",
              letterSpacing: "-.3px",
              color: "var(--text)",
            }}
          >
            Puff<span style={{ color: "var(--pink)" }}>PAI</span>
          </div>
        </div>

        {/* Nav column */}
        {customizing ? (
          <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
            <CustomizeRail rail={rail} onDone={() => setCustomizing(false)} />
          </div>
        ) : (
        <nav className="flex min-h-0 flex-1 flex-col overflow-y-auto" style={{ gap: 2 }}>
          {primary.map((item) => (
            <RailPrimaryItem
              key={item.href}
              item={item}
              pathname={pathname}
              onRouteChange={onRouteChange}
            />
          ))}

          <div style={{ height: 1, background: "var(--line)", margin: "10px 12px", flex: "none" }} />

          <div className="flex flex-none flex-col" style={{ gap: 2 }}>
            {secondary.map((item) => (
              <RailSecondaryItem
                key={item.href}
                item={item}
                pathname={pathname}
                onRouteChange={onRouteChange}
              />
            ))}
          </div>

          <div className="min-h-2 flex-1" />

          {settings.map((item) => (
            <RailPrimaryItem
              key={item.href}
              item={item}
              pathname={pathname}
              onRouteChange={onRouteChange}
            />
          ))}
          {rail.length > 0 && (
            <button
              type="button"
              onClick={() => setCustomizing(true)}
              className="flex flex-none cursor-pointer items-center border-0 bg-transparent"
              style={{
                gap: 8,
                padding: "8px 16px 0",
                font: "700 11px/1 var(--font-jbmono), monospace",
                letterSpacing: ".14em",
                color: "var(--text3)",
              }}
            >
              <MsIcon name="tune" size={15} />
              CUSTOMIZE
            </button>
          )}
        </nav>
        )}

        <PrivateStuffCard onRouteChange={onRouteChange} pathname={pathname} />
      </aside>

      {/* Mobile drawer backdrop */}
      {isAsideMobileExpanded && (
        <div className="lg:hidden">
          <OverlayLayer zIndex="z-40" onClick={onOverlayClick} />
        </div>
      )}
    </>
  );
}
