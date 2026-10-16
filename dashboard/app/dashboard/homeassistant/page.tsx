"use client";

/**
 * Smart Home — the Home Assistant surface: overview, devices, lights, climate,
 * media, security, automations. Converted off the admin template (SectionMain,
 * SectionTitleLineWithButton, CardBox, CardBoxWidget, Button, Icon+mdi, and a
 * lot of gray/slate/cyan `dark:` class pairs and #00B4D8-family hexes) into the
 * design-match shell. Every service call, poll, tab and data path is unchanged.
 *
 * THE HONEST STATES ARE KEPT, AND ONE WAS ADDED.
 *
 * Kept: "Disconnected" when /ha/status says so, "No data" when no climate entity
 * reports a temperature, "Unavailable" per entity, and the "No lights / no
 * cameras / no automations found" empties. None of them are smoothed into
 * optimistic defaults, and no meter draws a zero that reads like a measurement —
 * absent readings get the striped placeholder instead.
 *
 * Added: the camera tiles used to render a dark box captioned "Camera feed via
 * HA proxy" with a pulsing red LIVE badge over it. There is no feed there — no
 * image, no stream, nothing is proxied — so the badge asserted a live camera
 * view that did not exist, which on a security page is the worst possible place
 * to be decorative. The tile now says plainly that previews are not in this
 * build and links the owner to Home Assistant, which does have the stream.
 */

import { useState, useEffect, useCallback, useRef, type CSSProperties } from "react";
import MsIcon from "../../_components/MsIcon";
import AddOwnTool from "../../_components/ToolServers/AddOwnTool";
import ConnectHomeAssistant from "../../_components/ToolServers/ConnectHomeAssistant";
import {
  getHAStatus,
  getHAEntities,
  callHAService,
  getHAAutomations,
  getHAScenes,
  getHADeviceOverview,
  approvePendingDevice,
  rejectPendingDevice,
  type HAEntity,
  type HAOverviewDevice,
  type HADeviceOverview,
} from "../../_lib/services/haService";

/* ------------------------------------------------------------------ */
/*  Shell vocabulary                                                   */
/* ------------------------------------------------------------------ */

const FREDOKA = "var(--font-fredoka), sans-serif";
const NUNITO = "var(--font-nunito), sans-serif";
const JBMONO = "var(--font-jbmono), monospace";
const RED = "#FF5C7A";

const STRIPES =
  "repeating-linear-gradient(135deg, var(--chip) 0 8px, transparent 8px 16px)";

const microLabel: CSSProperties = {
  fontFamily: JBMONO,
  fontWeight: 700,
  fontSize: 11,
  letterSpacing: ".14em",
  color: "var(--text3)",
  textTransform: "uppercase",
};

const panelBase: CSSProperties = {
  borderRadius: 26,
  border: "1px solid var(--line)",
  background: "var(--card)",
  backdropFilter: "blur(18px)",
  WebkitBackdropFilter: "blur(18px)",
  padding: "20px 22px",
  boxShadow: "var(--shadow)",
};

const bodyText: CSSProperties = {
  fontFamily: NUNITO,
  fontWeight: 500,
  fontSize: 15,
  lineHeight: 1.5,
  color: "var(--text2)",
};

const nameText: CSSProperties = {
  fontFamily: NUNITO,
  fontWeight: 700,
  fontSize: 15,
  color: "var(--text)",
};

const tintOf = (color: string, pct: number) =>
  `color-mix(in srgb, ${color} ${pct}%, transparent)`;

function Panel({
  children,
  style,
  className,
  accent,
}: {
  children: React.ReactNode;
  style?: CSSProperties;
  className?: string;
  /** Left accent bar colour, replacing the template's border-l-4 utilities. */
  accent?: string;
}) {
  return (
    <div
      className={className}
      style={{
        ...panelBase,
        ...(accent ? { borderLeft: `4px solid ${accent}` } : null),
        ...style,
      }}
    >
      {children}
    </div>
  );
}

function PillButton({
  label,
  icon,
  onClick,
  href,
  disabled,
  tone = "primary",
  title,
  size = "md",
}: {
  label?: string;
  icon: string;
  onClick?: () => void;
  href?: string;
  disabled?: boolean;
  tone?: "primary" | "quiet" | "danger";
  title?: string;
  size?: "md" | "sm";
}) {
  const bg =
    tone === "primary"
      ? "linear-gradient(140deg,var(--pink),var(--magenta))"
      : tone === "danger"
        ? tintOf(RED, 16)
        : "var(--card2)";
  const fg = tone === "primary" ? "#fff" : tone === "danger" ? RED : "var(--text2)";
  const style: CSSProperties = {
    height: size === "sm" ? 34 : 42,
    padding: label ? (size === "sm" ? "0 16px" : "0 20px") : "0 12px",
    borderRadius: 99,
    border: tone === "primary" ? "1px solid transparent" : "1px solid var(--line)",
    background: bg,
    color: fg,
    fontFamily: NUNITO,
    fontWeight: 700,
    fontSize: size === "sm" ? 13 : 15,
    textDecoration: "none",
  };
  const cls =
    "inline-flex cursor-pointer items-center justify-center gap-2 transition-transform hover:-translate-y-0.5 disabled:cursor-default disabled:opacity-50";
  if (href) {
    return (
      <a href={href} target="_blank" rel="noreferrer" title={title} className={cls} style={style}>
        <MsIcon name={icon} size={size === "sm" ? 17 : 19} />
        {label}
      </a>
    );
  }
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={title}
      aria-label={label ?? title}
      className={cls}
      style={style}
    >
      <MsIcon name={icon} size={size === "sm" ? 17 : 19} />
      {label}
    </button>
  );
}

/** Small round action button (power, transport, +/-). */
function RoundButton({
  icon,
  onClick,
  disabled,
  active,
  activeColor = "var(--pink)",
  size = 38,
  title,
}: {
  icon: string;
  onClick: () => void;
  disabled?: boolean;
  active?: boolean;
  activeColor?: string;
  size?: number;
  title?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={title}
      aria-label={title ?? icon}
      className="flex flex-none cursor-pointer items-center justify-center transition-transform hover:-translate-y-0.5 disabled:cursor-not-allowed disabled:opacity-40"
      style={{
        width: size,
        height: size,
        borderRadius: "50%",
        border: active ? "1px solid transparent" : "1px solid var(--line)",
        background: active ? activeColor : "var(--card2)",
        color: active ? "#fff" : "var(--text2)",
      }}
    >
      <MsIcon name={icon} size={Math.round(size * 0.52)} />
    </button>
  );
}

/** Status chip: a coloured pill of short state text. */
function StateChip({
  children,
  color = "var(--text3)",
}: {
  children: React.ReactNode;
  color?: string;
}) {
  return (
    <span
      className="inline-flex flex-none items-center gap-1.5"
      style={{
        padding: "4px 11px",
        borderRadius: 99,
        background: tintOf(color, 16),
        color,
        fontFamily: JBMONO,
        fontWeight: 700,
        fontSize: 11,
        letterSpacing: ".05em",
        textTransform: "uppercase",
      }}
    >
      {children}
    </span>
  );
}

/** Round icon badge in an entity row. */
function IconBadge({
  icon,
  color,
  size = 40,
  pulse,
}: {
  icon: string;
  color: string;
  size?: number;
  pulse?: boolean;
}) {
  return (
    <span
      className={`flex flex-none items-center justify-center ${pulse ? "puff-motion animate-pulse" : ""}`}
      style={{
        width: size,
        height: size,
        borderRadius: "50%",
        background: tintOf(color, 16),
        color,
      }}
    >
      <MsIcon name={icon} size={Math.round(size * 0.55)} />
    </span>
  );
}

/** Section header inside a tab. */
function SubHeading({ icon, children }: { icon: string; children: React.ReactNode }) {
  return (
    <div className="mb-4 flex items-center gap-2.5">
      <MsIcon name={icon} size={21} style={{ color: "var(--pink)" }} />
      <div
        style={{
          fontFamily: FREDOKA,
          fontWeight: 600,
          fontSize: 23,
          color: "var(--text)",
        }}
      >
        {children}
      </div>
    </div>
  );
}

/** Stat tile. `available: false` draws the stripes instead of a fake zero. */
function StatTile({
  label,
  value,
  note,
  icon,
  color,
  available = true,
}: {
  label: string;
  value: string;
  note: string;
  icon: string;
  color: string;
  available?: boolean;
}) {
  return (
    <Panel className="flex items-center gap-4">
      <IconBadge icon={icon} color={color} size={46} />
      <div className="min-w-0">
        <div style={microLabel}>{label}</div>
        {available ? (
          <div
            style={{
              fontFamily: FREDOKA,
              fontWeight: 600,
              fontSize: 26,
              lineHeight: 1.2,
              color: "var(--text)",
            }}
          >
            {value}
          </div>
        ) : (
          <div style={{ height: 28, width: 62, borderRadius: 10, background: STRIPES, margin: "3px 0" }} />
        )}
        <div style={{ fontFamily: JBMONO, fontWeight: 600, fontSize: 12, color: "var(--text3)" }}>
          {note}
        </div>
      </div>
    </Panel>
  );
}

/** Striped "nothing here" panel. */
function EmptyPanel({
  icon,
  children,
  className,
}: {
  icon: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <Panel className={className}>
      <div
        className="flex flex-col items-center justify-center gap-2 py-8 text-center"
        style={{ borderRadius: 18, background: STRIPES }}
      >
        <MsIcon name={icon} size={32} style={{ color: "var(--text3)" }} />
        <span style={{ ...microLabel, fontSize: 12 }}>{children}</span>
      </div>
    </Panel>
  );
}

/* ------------------------------------------------------------------ */
/*  Constants                                                          */
/* ------------------------------------------------------------------ */

const REFRESH_INTERVAL_MS = 10_000;

type TabKey = "overview" | "lights" | "climate" | "media" | "security" | "automations" | "devices";

const TABS: { key: TabKey; label: string; icon: string }[] = [
  { key: "overview", label: "Overview", icon: "home" },
  { key: "devices", label: "Devices", icon: "devices" },
  { key: "lights", label: "Lights", icon: "lightbulb" },
  { key: "climate", label: "Climate", icon: "device_thermostat" },
  { key: "media", label: "Media", icon: "volume_up" },
  { key: "security", label: "Security", icon: "videocam" },
  { key: "automations", label: "Automations", icon: "settings" },
];

/* Room definitions — group entities by room. Colours are theme tokens so the
   tiles follow the day/night palette. */
const ROOM_DEFINITIONS: {
  name: string;
  id: string;
  patterns: string[];
  icon: string;
  color: string;
}[] = [
  { name: "Bedroom", id: "bedroom", patterns: ["bedroom"], icon: "bed", color: "var(--sky)" },
  {
    name: "Living Room",
    id: "living_room",
    patterns: ["living_room", "livingroom"],
    icon: "weekend",
    color: "var(--lav)",
  },
  {
    name: "Outside",
    id: "outside",
    patterns: ["garden", "yard", "outdoor", "porch"],
    icon: "videocam",
    color: "var(--mint)",
  },
  {
    name: "Voice Assistant",
    id: "voice_assistant",
    patterns: ["home_assistant_voice"],
    icon: "record_voice_over",
    color: "var(--magenta)",
  },
  {
    name: "Network",
    id: "network",
    patterns: ["router", "_wan_", "wifi"],
    icon: "router",
    color: "var(--lemon)",
  },
];

/* ------------------------------------------------------------------ */
/*  Main Page Component                                                */
/* ------------------------------------------------------------------ */

export default function HomeAssistantPage() {
  // Connection state
  const [connected, setConnected] = useState(false);
  const [haUrl, setHaUrl] = useState("http://homeassistant.local:8123");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // Entity state
  const [entities, setEntities] = useState<HAEntity[]>([]);
  const [automations, setAutomations] = useState<HAEntity[]>([]);
  const [scenes, setScenes] = useState<HAEntity[]>([]);
  const [deviceOverview, setDeviceOverview] = useState<HADeviceOverview | null>(null);

  // UI state
  const [activeTab, setActiveTab] = useState<TabKey>("overview");
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [toastMessage, setToastMessage] = useState("");
  const [toastColor, setToastColor] = useState<"success" | "danger">("success");

  const refreshTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  /* ---------------------------------------------------------------- */
  /*  Data fetching                                                    */
  /* ---------------------------------------------------------------- */

  const showToast = useCallback((msg: string, color: "success" | "danger") => {
    setToastMessage(msg);
    setToastColor(color);
    setTimeout(() => setToastMessage(""), 4000);
  }, []);

  const fetchAll = useCallback(async () => {
    try {
      const [statusRes, entitiesRes, autoRes, sceneRes, deviceRes] = await Promise.allSettled([
        getHAStatus(),
        getHAEntities(),
        getHAAutomations(),
        getHAScenes(),
        getHADeviceOverview(),
      ]);

      if (statusRes.status === "fulfilled") {
        setConnected(statusRes.value.connected);
        if (statusRes.value.ha_url) setHaUrl(statusRes.value.ha_url);
      } else {
        setConnected(false);
      }

      if (entitiesRes.status === "fulfilled") {
        setEntities(entitiesRes.value.entities || []);
      }

      if (autoRes.status === "fulfilled") {
        // getHAAutomations returns HAAutomation[]; the panel reads entity_id/state/
        // attributes defensively, so bridge the type.
        setAutomations(autoRes.value as unknown as HAEntity[]);
      }

      if (sceneRes.status === "fulfilled") {
        setScenes(sceneRes.value);
      }

      if (deviceRes.status === "fulfilled") {
        setDeviceOverview(deviceRes.value);
      }

      setLastUpdated(new Date());
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to connect to Home Assistant");
      setConnected(false);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAll();
    refreshTimerRef.current = setInterval(fetchAll, REFRESH_INTERVAL_MS);
    return () => {
      if (refreshTimerRef.current) clearInterval(refreshTimerRef.current);
    };
  }, [fetchAll]);

  /* ---------------------------------------------------------------- */
  /*  Entity helpers                                                   */
  /* ---------------------------------------------------------------- */

  const byDomain = (domain: string) =>
    entities.filter((e) => e.entity_id.startsWith(domain + "."));

  const lights = byDomain("light");
  const climates = byDomain("climate");
  const mediaPlayers = byDomain("media_player");
  const cameras = byDomain("camera");
  const binarySensors = byDomain("binary_sensor");
  const switches = byDomain("switch");

  const lightsOn = lights.filter((l) => l.state === "on").length;
  const mediaPlaying = mediaPlayers.filter((m) =>
    ["playing", "paused"].includes(m.state)
  ).length;
  const motionDetected = binarySensors.filter(
    (b) =>
      b.state === "on" &&
      (b.attributes.device_class === "motion" || b.entity_id.includes("motion"))
  ).length;

  // Average temperature from climate entities
  const climateTemps = climates
    .map((c) => c.attributes.current_temperature)
    .filter((t): t is number => typeof t === "number");
  const avgTemp =
    climateTemps.length > 0
      ? Math.round((climateTemps.reduce((a, b) => a + b, 0) / climateTemps.length) * 10) / 10
      : null;

  // Room grouping
  function getEntitiesForRoom(patterns: string[]): HAEntity[] {
    return entities.filter((e) =>
      patterns.some((p) => e.entity_id.toLowerCase().includes(p.toLowerCase()))
    );
  }

  /* ---------------------------------------------------------------- */
  /*  Service call helpers                                             */
  /* ---------------------------------------------------------------- */

  const toggleLight = async (entityId: string, currentState: string) => {
    try {
      const service = currentState === "on" ? "turn_off" : "turn_on";
      await callHAService("light", service, { entity_id: entityId });
      showToast(`Light ${service === "turn_on" ? "turned on" : "turned off"}`, "success");
      setTimeout(fetchAll, 500);
    } catch {
      showToast("Failed to toggle light", "danger");
    }
  };

  const setBrightness = async (entityId: string, brightness: number) => {
    try {
      await callHAService("light", "turn_on", {
        entity_id: entityId,
        brightness: Math.round(brightness * 2.55),
      });
      setTimeout(fetchAll, 500);
    } catch {
      showToast("Failed to set brightness", "danger");
    }
  };

  const setClimateTemp = async (entityId: string, temperature: number) => {
    try {
      await callHAService("climate", "set_temperature", {
        entity_id: entityId,
        temperature,
      });
      showToast(`Temperature set to ${temperature}`, "success");
      setTimeout(fetchAll, 500);
    } catch {
      showToast("Failed to set temperature", "danger");
    }
  };

  const setHvacMode = async (entityId: string, mode: string) => {
    try {
      await callHAService("climate", "set_hvac_mode", {
        entity_id: entityId,
        hvac_mode: mode,
      });
      showToast(`HVAC mode set to ${mode}`, "success");
      setTimeout(fetchAll, 500);
    } catch {
      showToast("Failed to set HVAC mode", "danger");
    }
  };

  const mediaControl = async (entityId: string, service: string) => {
    try {
      await callHAService("media_player", service, { entity_id: entityId });
      setTimeout(fetchAll, 500);
    } catch {
      showToast(`Failed: ${service}`, "danger");
    }
  };

  const setVolume = async (entityId: string, volume: number) => {
    try {
      await callHAService("media_player", "volume_set", {
        entity_id: entityId,
        volume_level: volume,
      });
      setTimeout(fetchAll, 500);
    } catch {
      showToast("Failed to set volume", "danger");
    }
  };

  const toggleAutomation = async (entityId: string, currentState: string) => {
    try {
      const service = currentState === "on" ? "turn_off" : "turn_on";
      await callHAService("automation", service, { entity_id: entityId });
      showToast(`Automation ${service === "turn_on" ? "enabled" : "disabled"}`, "success");
      setTimeout(fetchAll, 500);
    } catch {
      showToast("Failed to toggle automation", "danger");
    }
  };

  const triggerAutomation = async (entityId: string) => {
    try {
      await callHAService("automation", "trigger", { entity_id: entityId });
      showToast("Automation triggered", "success");
    } catch {
      showToast("Failed to trigger automation", "danger");
    }
  };

  const activateScene = async (entityId: string) => {
    try {
      await callHAService("scene", "turn_on", { entity_id: entityId });
      showToast("Scene activated", "success");
    } catch {
      showToast("Failed to activate scene", "danger");
    }
  };

  const approveDevice = async (
    entityId: string,
    securityMode: "balanced" | "lockdown",
  ) => {
    try {
      await approvePendingDevice(entityId, securityMode);
      showToast(
        `Device approved (${securityMode === "lockdown" ? "locked down" : "balanced"})`,
        "success",
      );
      setTimeout(fetchAll, 500);
    } catch {
      showToast("Failed to approve device", "danger");
    }
  };

  const rejectDevice = async (entityId: string) => {
    try {
      await rejectPendingDevice(entityId);
      showToast("Device rejected", "success");
      setTimeout(fetchAll, 500);
    } catch {
      showToast("Failed to reject device", "danger");
    }
  };

  /* ---------------------------------------------------------------- */
  /*  Render                                                           */
  /* ---------------------------------------------------------------- */

  return (
    <section
      style={{
        padding:
          "clamp(16px,2.4vh,26px) clamp(18px,2vw,36px) clamp(18px,2.8vh,30px)",
        display: "flex",
        flexDirection: "column",
        gap: "clamp(14px,2vh,22px)",
      }}
    >
      {/* ---- Header ---- */}
      <div className="flex flex-wrap items-end gap-4">
        <div>
          <h1
            style={{
              margin: 0,
              fontFamily: FREDOKA,
              fontWeight: 600,
              fontSize: "clamp(28px,2.6vw,44px)",
              letterSpacing: "-.8px",
              color: "var(--text)",
            }}
          >
            Your home
          </h1>
          <p style={{ ...bodyText, margin: "6px 0 0", fontSize: 19 }}>
            Lights, warmth, sound and locks, all handled on this box.
          </p>
        </div>
        <div style={{ flex: 1 }} />
        <div className="flex flex-wrap items-center gap-3">
          {/* Connection indicator — "Disconnected" is a real answer, not a
              loading state, so it is stated rather than hidden. */}
          <span className="inline-flex items-center gap-2">
            <span
              style={{
                width: 10,
                height: 10,
                borderRadius: "50%",
                display: "inline-block",
                background: connected ? "var(--mint)" : RED,
                boxShadow: `0 0 8px ${connected ? "var(--mint)" : RED}`,
              }}
            />
            <span
              style={{
                fontFamily: JBMONO,
                fontWeight: 700,
                fontSize: 12,
                letterSpacing: ".06em",
                textTransform: "uppercase",
                color: connected ? "var(--mint)" : RED,
              }}
            >
              {connected ? "Connected" : "Disconnected"}
            </span>
          </span>
          <PillButton label="Open HA" icon="open_in_new" href={haUrl} />
          <PillButton icon="refresh" tone="quiet" title="Refresh" onClick={fetchAll} />
        </div>
      </div>

      {/* ---- Toast ---- */}
      {toastMessage && (
        <div
          className="flex items-center justify-between gap-3"
          style={{
            padding: "14px 18px",
            borderRadius: 20,
            border: `1px solid ${tintOf(
              toastColor === "success" ? "var(--mint)" : RED,
              40
            )}`,
            background: tintOf(toastColor === "success" ? "var(--mint)" : RED, 12),
          }}
        >
          <div
            className="flex items-center gap-2"
            style={{
              fontFamily: NUNITO,
              fontWeight: 700,
              fontSize: 15,
              color: toastColor === "success" ? "var(--mint)" : RED,
            }}
          >
            <MsIcon name={toastColor === "success" ? "check_circle" : "error"} size={19} />
            {toastMessage}
          </div>
          <button
            type="button"
            onClick={() => setToastMessage("")}
            aria-label="Dismiss"
            className="cursor-pointer"
            style={{ background: "none", border: "none", color: "var(--text3)", padding: 2 }}
          >
            <MsIcon name="close" size={18} />
          </button>
        </div>
      )}

      {/* ---- Error banner ---- */}
      {error && !loading && (
        <div
          className="flex flex-wrap items-center gap-3"
          style={{
            ...panelBase,
            border: `1px solid ${tintOf(RED, 40)}`,
            background: tintOf(RED, 12),
          }}
        >
          <MsIcon name="error" size={24} style={{ color: RED, flex: "none" }} />
          <div className="min-w-0 flex-1">
            <div style={{ fontFamily: NUNITO, fontWeight: 700, fontSize: 17, color: "var(--text)" }}>
              Home Assistant unreachable
            </div>
            <div style={{ ...bodyText, fontSize: 14 }}>
              {error}. Check that the PuffPAI backend is running and that Home
              Assistant is set up.
            </div>
          </div>
          <PillButton label="Retry" icon="refresh" tone="danger" onClick={fetchAll} />
        </div>
      )}

      {/* ---- Connect my Home Assistant, and her own tools ----
          Home Assistant's own tool server (/api/mcp) with the token in the
          vault, so its tools work in chat; and any other tool she has. */}
      <ConnectHomeAssistant />
      <AddOwnTool hideHomeAssistant />

      {/* ---- Tab Navigation ---- */}
      <div className="flex flex-wrap gap-2.5">
        {TABS.map((tab) => {
          const on = activeTab === tab.key;
          const badgeCount = tab.key === "devices" ? deviceOverview?.counts.new ?? 0 : 0;
          return (
            <button
              key={tab.key}
              type="button"
              onClick={() => setActiveTab(tab.key)}
              aria-pressed={on}
              className="inline-flex cursor-pointer items-center gap-2 whitespace-nowrap transition-transform hover:-translate-y-0.5"
              style={{
                height: 42,
                padding: "0 20px",
                borderRadius: 99,
                border: on ? "1px solid transparent" : "1px solid var(--line)",
                background: on
                  ? "linear-gradient(140deg,var(--pink),var(--magenta))"
                  : "var(--card2)",
                color: on ? "#fff" : "var(--text2)",
                fontFamily: NUNITO,
                fontWeight: 700,
                fontSize: 15,
                boxShadow: on ? "0 10px 26px var(--glow1)" : "none",
              }}
            >
              <MsIcon name={tab.icon} size={18} />
              {tab.label}
              {badgeCount > 0 && (
                <span
                  className="inline-flex items-center justify-center"
                  style={{
                    minWidth: 20,
                    padding: "2px 6px",
                    borderRadius: 99,
                    background: on ? "rgba(255,255,255,.28)" : tintOf(RED, 22),
                    color: on ? "#fff" : RED,
                    fontFamily: JBMONO,
                    fontWeight: 700,
                    fontSize: 11,
                  }}
                >
                  {badgeCount}
                </span>
              )}
            </button>
          );
        })}
      </div>

      {/* ---- Loading skeleton ---- */}
      {loading && (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {[0, 1, 2, 3].map((i) => (
            <Panel key={i}>
              <div style={{ height: 66, borderRadius: 18, background: STRIPES }} />
            </Panel>
          ))}
        </div>
      )}

      {/* ---- Tab content ---- */}
      {!loading && activeTab === "overview" && (
        <OverviewTab
          lightsOn={lightsOn}
          lightsTotal={lights.length}
          avgTemp={avgTemp}
          mediaPlaying={mediaPlaying}
          motionDetected={motionDetected}
          rooms={ROOM_DEFINITIONS}
          getEntitiesForRoom={getEntitiesForRoom}
        />
      )}

      {!loading && activeTab === "devices" && (
        <DevicesTab
          overview={deviceOverview}
          onApprove={approveDevice}
          onReject={rejectDevice}
        />
      )}

      {!loading && activeTab === "lights" && (
        <LightsTab
          lights={lights}
          switches={switches}
          onToggle={toggleLight}
          onBrightness={setBrightness}
        />
      )}

      {!loading && activeTab === "climate" && (
        <ClimateTab
          climates={climates}
          binarySensors={binarySensors}
          onSetTemp={setClimateTemp}
          onSetMode={setHvacMode}
        />
      )}

      {!loading && activeTab === "media" && (
        <MediaTab players={mediaPlayers} onControl={mediaControl} onVolume={setVolume} />
      )}

      {!loading && activeTab === "security" && (
        <SecurityTab cameras={cameras} binarySensors={binarySensors} haUrl={haUrl} />
      )}

      {!loading && activeTab === "automations" && (
        <AutomationsTab
          automations={automations}
          scenes={scenes}
          onToggle={toggleAutomation}
          onTrigger={triggerAutomation}
          onActivateScene={activateScene}
        />
      )}

      {/* ---- Last updated footer ---- */}
      {lastUpdated && (
        <div className="flex items-center gap-2.5">
          <MsIcon name="refresh" size={16} style={{ color: "var(--text3)" }} />
          <span style={{ ...microLabel, fontSize: 10 }}>
            Read at {lastUpdated.toLocaleTimeString()} · refreshes every 10 seconds
          </span>
        </div>
      )}
    </section>
  );
}

/* ================================================================== */
/*  OVERVIEW TAB                                                       */
/* ================================================================== */

function OverviewTab({
  lightsOn,
  lightsTotal,
  avgTemp,
  mediaPlaying,
  motionDetected,
  rooms,
  getEntitiesForRoom,
}: {
  lightsOn: number;
  lightsTotal: number;
  avgTemp: number | null;
  mediaPlaying: number;
  motionDetected: number;
  rooms: typeof ROOM_DEFINITIONS;
  getEntitiesForRoom: (patterns: string[]) => HAEntity[];
}) {
  const roomCards = rooms
    .map((room) => ({ room, roomEntities: getEntitiesForRoom(room.patterns) }))
    .filter(({ roomEntities }) => roomEntities.length > 0);

  return (
    <>
      {/* Status tiles */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatTile
          label="Lights"
          value={String(lightsTotal)}
          note={lightsOn > 0 ? `${lightsOn} on` : "all off"}
          icon="lightbulb"
          color="var(--lemon)"
        />
        {/* No climate entity reporting a temperature is a real answer. It draws
            the stripes and says so, rather than a confident 0°. */}
        <StatTile
          label="Climate"
          value={avgTemp !== null ? `${avgTemp}°` : ""}
          note={avgTemp !== null ? "average indoors" : "no data"}
          icon="device_thermostat"
          color="var(--sky)"
          available={avgTemp !== null}
        />
        <StatTile
          label="Media"
          value={String(mediaPlaying)}
          note={mediaPlaying > 0 ? "playing now" : "idle"}
          icon="volume_up"
          color="var(--mint)"
        />
        <StatTile
          label="Motion"
          value={String(motionDetected)}
          note={motionDetected > 0 ? "movement seen" : "all clear"}
          icon="sensors"
          color={motionDetected > 0 ? RED : "var(--mint)"}
        />
      </div>

      {/* Rooms */}
      <div className="mt-2">
        <SubHeading icon="home">Rooms</SubHeading>
        {roomCards.length === 0 ? (
          <EmptyPanel icon="home">No rooms matched the entities on this box</EmptyPanel>
        ) : (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {roomCards.map(({ room, roomEntities }) => {
              const roomLights = roomEntities.filter((e) => e.entity_id.startsWith("light."));
              const roomClimate = roomEntities.find((e) => e.entity_id.startsWith("climate."));
              const roomMotion = roomEntities.filter(
                (e) =>
                  e.entity_id.startsWith("binary_sensor.") &&
                  (e.attributes.device_class === "motion" || e.entity_id.includes("motion"))
              );
              const roomOccupancy = roomEntities.filter(
                (e) =>
                  e.entity_id.startsWith("binary_sensor.") &&
                  (e.attributes.device_class === "occupancy" ||
                    e.entity_id.includes("occupancy"))
              );
              const lightsOnCount = roomLights.filter((l) => l.state === "on").length;
              const hasMotion = roomMotion.some((m) => m.state === "on");
              const isOccupied = roomOccupancy.some((o) => o.state === "on");

              return (
                <Panel key={room.id} className="transition-transform hover:-translate-y-0.5">
                  <div className="flex items-start gap-4">
                    <IconBadge icon={room.icon} color={room.color} size={48} />
                    <div className="min-w-0 flex-1">
                      <div style={{ ...nameText, fontSize: 17 }}>{room.name}</div>
                      <div style={microLabel}>{roomEntities.length} entities</div>
                      <div className="mt-2.5 flex flex-wrap gap-2">
                        {roomClimate && (
                          <StateChip color="var(--sky)">
                            <MsIcon name="device_thermostat" size={13} />
                            {roomClimate.attributes.current_temperature ?? "--"}&deg;
                          </StateChip>
                        )}
                        {roomLights.length > 0 && (
                          <StateChip
                            color={lightsOnCount > 0 ? "var(--lemon)" : "var(--text3)"}
                          >
                            <MsIcon name="lightbulb" size={13} />
                            {lightsOnCount}/{roomLights.length} on
                          </StateChip>
                        )}
                        {hasMotion && (
                          <StateChip color={RED}>
                            <MsIcon name="sensors" size={13} />
                            Motion
                          </StateChip>
                        )}
                        {isOccupied && (
                          <StateChip color="var(--mint)">
                            <MsIcon name="group" size={13} />
                            Occupied
                          </StateChip>
                        )}
                      </div>
                    </div>
                  </div>
                </Panel>
              );
            })}
          </div>
        )}
      </div>
    </>
  );
}

/* ================================================================== */
/*  DEVICES TAB                                                        */
/* ================================================================== */

const DOMAIN_ICONS: Record<string, string> = {
  light: "lightbulb",
  switch: "toggle_on",
  climate: "device_thermostat",
  media_player: "volume_up",
  camera: "videocam",
  binary_sensor: "sensors",
  sensor: "sensors",
  fan: "mode_fan",
  lock: "lock",
  cover: "blinds",
};

function SecurityModeChip({ mode }: { mode: string | null }) {
  if (!mode) return null;
  const colors: Record<string, string> = {
    lockdown: RED,
    balanced: "var(--sky)",
    open: "var(--text3)",
    allow: "var(--text3)",
    auto: "var(--mint)",
  };
  const icon =
    mode === "lockdown" ? "lock" : mode === "balanced" ? "verified_user" : "help";
  return (
    <StateChip color={colors[mode] ?? "var(--text3)"}>
      <MsIcon name={icon} size={13} />
      {capitalize(mode)}
    </StateChip>
  );
}

function DeviceRow({ device }: { device: HAOverviewDevice }) {
  return (
    <div
      className="flex items-center gap-3"
      style={{
        padding: "11px 14px",
        borderRadius: 16,
        background: "var(--card2)",
        border: "1px solid var(--line)",
      }}
    >
      <MsIcon
        name={DOMAIN_ICONS[device.domain] ?? "devices"}
        size={20}
        style={{ color: "var(--text3)", flex: "none" }}
      />
      <div className="min-w-0 flex-1">
        <div className="truncate" style={nameText}>
          {device.name}
        </div>
        <div style={microLabel}>
          {device.domain} · {device.state}
        </div>
      </div>
      {device.security_mode && <SecurityModeChip mode={device.security_mode} />}
    </div>
  );
}

function DevicesTab({
  overview,
  onApprove,
  onReject,
}: {
  overview: HADeviceOverview | null;
  onApprove: (entityId: string, mode: "balanced" | "lockdown") => void;
  onReject: (entityId: string) => void;
}) {
  if (!overview) {
    return <EmptyPanel icon="devices">No device data from this box</EmptyPanel>;
  }

  const newDevices = overview.devices.filter((d) => d.is_new);
  const managed = overview.devices.filter((d) => d.managed);
  const unmanaged = overview.devices.filter((d) => !d.managed && !d.is_new);

  return (
    <>
      {/* Count tiles */}
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <StatTile
          label="Total"
          value={String(overview.counts.total)}
          note="on the network"
          icon="devices"
          color="var(--sky)"
        />
        <StatTile
          label="New"
          value={String(overview.counts.new)}
          note={overview.counts.new > 0 ? "needs your say" : "all reviewed"}
          icon="fiber_new"
          color={overview.counts.new > 0 ? RED : "var(--mint)"}
        />
        <StatTile
          label="Managed"
          value={String(overview.counts.managed)}
          note="approved by you"
          icon="verified_user"
          color="var(--mint)"
        />
        <StatTile
          label="Unmanaged"
          value={String(overview.counts.unmanaged)}
          note="not managed"
          icon="help"
          color="var(--lemon)"
        />
      </div>

      {/* New devices — prominent */}
      <div className="mt-2">
        <SubHeading icon="fiber_new">New devices</SubHeading>
        {newDevices.length === 0 ? (
          <EmptyPanel icon="check_circle">
            Nothing new. Everything on the network has been reviewed
          </EmptyPanel>
        ) : (
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            {newDevices.map((device) => (
              <Panel key={device.entity_id} accent={RED}>
                <div className="flex items-start gap-3">
                  <IconBadge
                    icon={DOMAIN_ICONS[device.domain] ?? "devices"}
                    color={RED}
                  />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="truncate" style={{ ...nameText, fontSize: 17 }}>
                        {device.name}
                      </span>
                      <StateChip color={RED}>New</StateChip>
                    </div>
                    <div style={{ ...microLabel, textTransform: "none", letterSpacing: 0 }}>
                      {device.entity_id} · {device.state}
                    </div>
                  </div>
                </div>

                <div className="mt-4 flex flex-wrap gap-2.5">
                  <PillButton
                    label="Approve (balanced)"
                    icon="verified_user"
                    size="sm"
                    onClick={() => onApprove(device.entity_id, "balanced")}
                  />
                  <PillButton
                    label="Approve (locked down)"
                    icon="lock"
                    size="sm"
                    tone="quiet"
                    onClick={() => onApprove(device.entity_id, "lockdown")}
                  />
                  <PillButton
                    label="Reject"
                    icon="close"
                    size="sm"
                    tone="danger"
                    onClick={() => onReject(device.entity_id)}
                  />
                </div>
              </Panel>
            ))}
          </div>
        )}
      </div>

      {/* Managed devices */}
      <div className="mt-2">
        <SubHeading icon="verified_user">Managed ({managed.length})</SubHeading>
        {managed.length === 0 ? (
          <EmptyPanel icon="verified_user">Nothing approved yet</EmptyPanel>
        ) : (
          <Panel style={{ padding: "14px 16px" }}>
            <div className="flex flex-col gap-2">
              {managed.map((device) => (
                <DeviceRow key={device.entity_id} device={device} />
              ))}
            </div>
          </Panel>
        )}
      </div>

      {/* Unmanaged devices */}
      <div className="mt-2">
        <SubHeading icon="help">Unmanaged ({unmanaged.length})</SubHeading>
        {unmanaged.length === 0 ? (
          <EmptyPanel icon="help">No unmanaged devices</EmptyPanel>
        ) : (
          <Panel style={{ padding: "14px 16px" }}>
            <div className="flex flex-col gap-2">
              {unmanaged.map((device) => (
                <DeviceRow key={device.entity_id} device={device} />
              ))}
            </div>
          </Panel>
        )}
      </div>
    </>
  );
}

/* ================================================================== */
/*  LIGHTS TAB                                                         */
/* ================================================================== */

function LightsTab({
  lights,
  switches,
  onToggle,
  onBrightness,
}: {
  lights: HAEntity[];
  switches: HAEntity[];
  onToggle: (entityId: string, state: string) => void;
  onBrightness: (entityId: string, brightness: number) => void;
}) {
  return (
    <>
      <div>
        <SubHeading icon="lightbulb">Lights</SubHeading>
        {lights.length === 0 ? (
          <EmptyPanel icon="lightbulb">No lights on this box</EmptyPanel>
        ) : (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {lights.map((light) => {
              const isOn = light.state === "on";
              const isUnavailable = light.state === "unavailable";
              const brightness = light.attributes.brightness
                ? Math.round((light.attributes.brightness / 255) * 100)
                : null;
              const supportsBrightness =
                light.attributes.supported_features !== undefined &&
                (light.attributes.supported_features & 1) !== 0;
              const accent = isUnavailable
                ? RED
                : isOn
                  ? "var(--lemon)"
                  : "var(--line)";

              return (
                <Panel key={light.entity_id} accent={accent}>
                  <div className="flex items-center justify-between gap-3">
                    <div className="flex min-w-0 items-center gap-3">
                      <IconBadge
                        icon={isOn ? "light_mode" : "lightbulb"}
                        color={isUnavailable ? RED : isOn ? "var(--lemon)" : "var(--text3)"}
                      />
                      <div className="min-w-0">
                        <div className="truncate" style={nameText}>
                          {friendlyName(light)}
                        </div>
                        <div style={microLabel}>
                          {isUnavailable
                            ? "Unavailable"
                            : isOn
                              ? brightness !== null
                                ? `On · ${brightness}%`
                                : "On"
                              : "Off"}
                        </div>
                      </div>
                    </div>
                    <RoundButton
                      icon="power_settings_new"
                      title={isOn ? "Turn off" : "Turn on"}
                      onClick={() => onToggle(light.entity_id, light.state)}
                      disabled={isUnavailable}
                      active={isOn}
                      activeColor="var(--lemon)"
                      size={40}
                    />
                  </div>

                  {/* Brightness slider */}
                  {(supportsBrightness || brightness !== null) && !isUnavailable && (
                    <div className="mt-3 flex items-center gap-3">
                      <MsIcon name="lightbulb" size={15} style={{ color: "var(--text3)" }} />
                      <input
                        type="range"
                        min={0}
                        max={100}
                        value={brightness ?? 0}
                        onChange={(e) =>
                          onBrightness(light.entity_id, parseInt(e.target.value, 10))
                        }
                        className="flex-1"
                        style={{ accentColor: "var(--lemon)" }}
                        aria-label={`Brightness for ${friendlyName(light)}`}
                      />
                      <MsIcon name="light_mode" size={15} style={{ color: "var(--lemon)" }} />
                    </div>
                  )}
                </Panel>
              );
            })}
          </div>
        )}
      </div>

      {/* Switches section */}
      {switches.length > 0 && (
        <div className="mt-2">
          <SubHeading icon="toggle_on">Switches</SubHeading>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {switches.map((sw) => {
              const isOn = sw.state === "on";
              const isUnavailable = sw.state === "unavailable";
              return (
                <Panel key={sw.entity_id}>
                  <div className="flex items-center justify-between gap-3">
                    <div className="flex min-w-0 items-center gap-3">
                      <MsIcon
                        name={isOn ? "toggle_on" : "toggle_off"}
                        size={26}
                        style={{
                          color: isUnavailable
                            ? RED
                            : isOn
                              ? "var(--mint)"
                              : "var(--text3)",
                          flex: "none",
                        }}
                      />
                      <div className="min-w-0">
                        <div className="truncate" style={nameText}>
                          {friendlyName(sw)}
                        </div>
                        <div style={microLabel}>
                          {isUnavailable ? "Unavailable" : isOn ? "On" : "Off"}
                        </div>
                      </div>
                    </div>
                    <RoundButton
                      icon="power_settings_new"
                      title={isOn ? "Turn off" : "Turn on"}
                      onClick={() => onToggle(sw.entity_id, sw.state)}
                      disabled={isUnavailable}
                      active={isOn}
                      activeColor="var(--mint)"
                      size={34}
                    />
                  </div>
                </Panel>
              );
            })}
          </div>
        </div>
      )}
    </>
  );
}

/* ================================================================== */
/*  CLIMATE TAB                                                        */
/* ================================================================== */

function ClimateTab({
  climates,
  binarySensors,
  onSetTemp,
  onSetMode,
}: {
  climates: HAEntity[];
  binarySensors: HAEntity[];
  onSetTemp: (entityId: string, temp: number) => void;
  onSetMode: (entityId: string, mode: string) => void;
}) {
  const hvacModes = ["off", "heat", "cool", "heat_cool"];
  const hvacIcons: Record<string, string> = {
    off: "power_settings_new",
    heat: "local_fire_department",
    cool: "ac_unit",
    heat_cool: "mode_fan",
  };
  const hvacColors: Record<string, string> = {
    off: "var(--text3)",
    heat: RED,
    cool: "var(--sky)",
    heat_cool: "var(--lav)",
  };

  return (
    <div>
      <SubHeading icon="device_thermostat">Climate</SubHeading>
      {climates.length === 0 ? (
        <EmptyPanel icon="device_thermostat">
          No thermostats or climate entities on this box
        </EmptyPanel>
      ) : (
        <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
          {climates.map((climate) => {
            const currentTemp = climate.attributes.current_temperature;
            const targetTemp = climate.attributes.temperature;
            const hvacMode = climate.state;
            const name = friendlyName(climate);
            const hasCurrent = currentTemp !== undefined && currentTemp !== null;

            // Find associated motion/occupancy sensors
            const roomKey = climate.entity_id.split(".")[1];
            const motionSensor = binarySensors.find(
              (b) =>
                b.entity_id.includes(roomKey) &&
                (b.attributes.device_class === "motion" || b.entity_id.includes("motion"))
            );
            const occupancySensor = binarySensors.find(
              (b) =>
                b.entity_id.includes(roomKey) &&
                (b.attributes.device_class === "occupancy" ||
                  b.entity_id.includes("occupancy"))
            );

            return (
              <Panel key={climate.entity_id}>
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div style={{ fontFamily: FREDOKA, fontWeight: 600, fontSize: 21, color: "var(--text)" }}>
                      {name}
                    </div>
                    <div style={microLabel}>Mode {capitalize(hvacMode)}</div>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {motionSensor && (
                      <StateChip
                        color={motionSensor.state === "on" ? RED : "var(--text3)"}
                      >
                        <MsIcon name="directions_walk" size={13} />
                        {motionSensor.state === "on" ? "Motion" : "Clear"}
                      </StateChip>
                    )}
                    {occupancySensor && (
                      <StateChip
                        color={occupancySensor.state === "on" ? "var(--mint)" : "var(--text3)"}
                      >
                        <MsIcon name="group" size={13} />
                        {occupancySensor.state === "on" ? "Occupied" : "Empty"}
                      </StateChip>
                    )}
                  </div>
                </div>

                {/* Temperature display */}
                <div className="mt-4 flex flex-wrap items-center justify-center gap-8">
                  <div className="text-center">
                    <div style={microLabel}>Now</div>
                    {hasCurrent ? (
                      <div
                        style={{
                          fontFamily: FREDOKA,
                          fontWeight: 600,
                          fontSize: 48,
                          lineHeight: 1.05,
                          color: "var(--sky)",
                        }}
                      >
                        {currentTemp}
                        <span style={{ fontSize: 24 }}>&deg;</span>
                      </div>
                    ) : (
                      /* This thermostat reports no current temperature. Stripes,
                         not a zero that reads like a measurement. */
                      <div
                        style={{ height: 48, width: 84, borderRadius: 14, background: STRIPES, margin: "4px 0" }}
                      />
                    )}
                  </div>
                  <div style={{ width: 1, height: 62, background: "var(--line)" }} />
                  <div className="text-center">
                    <div style={microLabel}>Target</div>
                    <div className="flex items-center gap-2.5">
                      <RoundButton
                        icon="remove"
                        title="Cooler"
                        size={34}
                        onClick={() =>
                          targetTemp !== undefined && onSetTemp(climate.entity_id, targetTemp - 1)
                        }
                        disabled={targetTemp === undefined}
                      />
                      <div
                        style={{
                          fontFamily: FREDOKA,
                          fontWeight: 600,
                          fontSize: 32,
                          color: "var(--text)",
                          minWidth: 62,
                        }}
                      >
                        {targetTemp ?? "--"}
                        <span style={{ fontSize: 18 }}>&deg;</span>
                      </div>
                      <RoundButton
                        icon="add"
                        title="Warmer"
                        size={34}
                        onClick={() =>
                          targetTemp !== undefined && onSetTemp(climate.entity_id, targetTemp + 1)
                        }
                        disabled={targetTemp === undefined}
                      />
                    </div>
                  </div>
                </div>

                {/* HVAC mode selector */}
                <div className="mt-4 flex flex-wrap justify-center gap-2.5">
                  {hvacModes.map((mode) => {
                    const on = hvacMode === mode;
                    const c = hvacColors[mode];
                    return (
                      <button
                        key={mode}
                        type="button"
                        onClick={() => onSetMode(climate.entity_id, mode)}
                        aria-pressed={on}
                        className="inline-flex cursor-pointer items-center gap-1.5 transition-transform hover:-translate-y-0.5"
                        style={{
                          height: 34,
                          padding: "0 14px",
                          borderRadius: 99,
                          border: on ? `2px solid ${c}` : "1px solid var(--line)",
                          background: on ? tintOf(c, 16) : "var(--card2)",
                          color: on ? c : "var(--text2)",
                          fontFamily: NUNITO,
                          fontWeight: 700,
                          fontSize: 13,
                        }}
                      >
                        <MsIcon name={hvacIcons[mode] || "power_settings_new"} size={16} />
                        {capitalize(mode === "heat_cool" ? "Auto" : mode)}
                      </button>
                    );
                  })}
                </div>
              </Panel>
            );
          })}
        </div>
      )}
    </div>
  );
}

/* ================================================================== */
/*  MEDIA TAB                                                          */
/* ================================================================== */

function MediaTab({
  players,
  onControl,
  onVolume,
}: {
  players: HAEntity[];
  onControl: (entityId: string, service: string) => void;
  onVolume: (entityId: string, volume: number) => void;
}) {
  return (
    <div>
      <SubHeading icon="volume_up">Media players</SubHeading>
      {players.length === 0 ? (
        <EmptyPanel icon="volume_up">No media players on this box</EmptyPanel>
      ) : (
        <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
          {players.map((player) => {
            const isPlaying = player.state === "playing";
            const isPaused = player.state === "paused";
            const isIdle = player.state === "idle" || player.state === "off";
            const isUnavailable = player.state === "unavailable";
            const volume = player.attributes.volume_level;
            const volumePercent = volume !== undefined ? Math.round(volume * 100) : null;
            const mediaTitle = player.attributes.media_title || null;
            const mediaArtist = player.attributes.media_artist || null;
            const accent = isPlaying
              ? "var(--mint)"
              : isPaused
                ? "var(--lemon)"
                : "var(--line)";

            return (
              <Panel key={player.entity_id} accent={accent}>
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div style={{ ...nameText, fontSize: 17 }}>{friendlyName(player)}</div>
                    <div style={microLabel}>{capitalize(player.state)}</div>
                  </div>
                  <StateChip
                    color={
                      isPlaying ? "var(--mint)" : isPaused ? "var(--lemon)" : "var(--text3)"
                    }
                  >
                    {capitalize(player.state)}
                  </StateChip>
                </div>

                {/* Now playing info */}
                {(mediaTitle || mediaArtist) && (
                  <div
                    className="mt-3"
                    style={{
                      padding: "12px 14px",
                      borderRadius: 16,
                      background: "var(--card2)",
                      border: "1px solid var(--line)",
                    }}
                  >
                    {mediaTitle && <div style={nameText}>{mediaTitle}</div>}
                    {mediaArtist && <div style={microLabel}>{mediaArtist}</div>}
                  </div>
                )}

                {/* Transport controls */}
                <div className="mt-4 flex items-center justify-center gap-3">
                  <RoundButton
                    icon="skip_previous"
                    title="Previous"
                    size={36}
                    onClick={() => onControl(player.entity_id, "media_previous_track")}
                    disabled={isUnavailable}
                  />
                  {isPlaying ? (
                    <RoundButton
                      icon="pause"
                      title="Pause"
                      size={50}
                      active
                      onClick={() => onControl(player.entity_id, "media_pause")}
                    />
                  ) : (
                    <RoundButton
                      icon="play_arrow"
                      title="Play"
                      size={50}
                      active
                      activeColor="var(--mint)"
                      onClick={() => onControl(player.entity_id, "media_play")}
                      disabled={isUnavailable}
                    />
                  )}
                  <RoundButton
                    icon="stop"
                    title="Stop"
                    size={36}
                    onClick={() => onControl(player.entity_id, "media_stop")}
                    disabled={isUnavailable || isIdle}
                  />
                  <RoundButton
                    icon="skip_next"
                    title="Next"
                    size={36}
                    onClick={() => onControl(player.entity_id, "media_next_track")}
                    disabled={isUnavailable}
                  />
                </div>

                {/* Volume */}
                {volumePercent !== null && (
                  <div className="mt-4 flex items-center gap-3">
                    <button
                      type="button"
                      onClick={() => onControl(player.entity_id, "volume_mute")}
                      title="Mute"
                      aria-label="Mute"
                      className="flex-none cursor-pointer"
                      style={{
                        background: "none",
                        border: "none",
                        color: "var(--text3)",
                        padding: 0,
                      }}
                    >
                      <MsIcon
                        name={volumePercent === 0 ? "volume_off" : "volume_down"}
                        size={19}
                      />
                    </button>
                    <input
                      type="range"
                      min={0}
                      max={100}
                      value={volumePercent}
                      onChange={(e) =>
                        onVolume(player.entity_id, parseInt(e.target.value, 10) / 100)
                      }
                      className="flex-1"
                      style={{ accentColor: "var(--pink)" }}
                      aria-label={`Volume for ${friendlyName(player)}`}
                    />
                    <MsIcon name="volume_up" size={19} style={{ color: "var(--text3)", flex: "none" }} />
                    <span
                      className="flex-none text-right"
                      style={{ ...microLabel, minWidth: "3ch" }}
                    >
                      {volumePercent}%
                    </span>
                  </div>
                )}
              </Panel>
            );
          })}
        </div>
      )}
    </div>
  );
}

/* ================================================================== */
/*  SECURITY TAB                                                       */
/* ================================================================== */

function SecurityTab({
  cameras,
  binarySensors,
  haUrl,
}: {
  cameras: HAEntity[];
  binarySensors: HAEntity[];
  haUrl: string;
}) {
  const motionSensors = binarySensors.filter(
    (b) => b.attributes.device_class === "motion" || b.entity_id.includes("motion")
  );
  const occupancySensors = binarySensors.filter(
    (b) =>
      b.attributes.device_class === "occupancy" || b.entity_id.includes("occupancy")
  );
  const personSensors = binarySensors.filter(
    (b) =>
      b.attributes.device_class === "presence" ||
      b.entity_id.includes("person") ||
      b.entity_id.includes("vehicle") ||
      b.entity_id.includes("animal")
  );

  /** One sensor row — shared by motion, occupancy and detection. */
  const sensorPanel = (
    sensor: HAEntity,
    icon: string,
    onLabel: string,
    offLabel: string,
    onColor: string,
    offColor: string,
  ) => {
    const active = sensor.state === "on";
    return (
      <Panel key={sensor.entity_id} accent={active ? onColor : "var(--line)"}>
        <div className="flex items-center gap-3">
          <IconBadge
            icon={icon}
            color={active ? onColor : "var(--text3)"}
            pulse={active}
          />
          <div className="min-w-0 flex-1">
            <div className="truncate" style={nameText}>
              {friendlyName(sensor)}
            </div>
            <div style={microLabel}>{active ? onLabel : offLabel}</div>
          </div>
          <StateChip color={active ? onColor : offColor}>
            {active ? onLabel : offLabel}
          </StateChip>
        </div>
      </Panel>
    );
  };

  return (
    <>
      {/* Cameras */}
      <div>
        <SubHeading icon="videocam">Cameras</SubHeading>
        {cameras.length === 0 ? (
          <EmptyPanel icon="videocam">No cameras on this box</EmptyPanel>
        ) : (
          <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
            {cameras.map((cam) => (
              <Panel key={cam.entity_id}>
                <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
                  <div className="flex min-w-0 items-center gap-3">
                    <IconBadge icon="videocam" color="var(--magenta)" />
                    <div className="min-w-0">
                      <div className="truncate" style={{ ...nameText, fontSize: 17 }}>
                        {friendlyName(cam)}
                      </div>
                      <div style={microLabel}>{capitalize(cam.state)}</div>
                    </div>
                  </div>
                  <StateChip
                    color={
                      cam.state === "idle" || cam.state === "recording"
                        ? "var(--mint)"
                        : "var(--text3)"
                    }
                  >
                    {capitalize(cam.state)}
                  </StateChip>
                </div>

                {/* NO PREVIEW IS RENDERED HERE, AND THE TILE SAYS SO.
                    This used to be a dark box labelled "Camera feed via HA
                    proxy" with a pulsing red LIVE badge on top of it. No image
                    was ever fetched and nothing was proxied, so the badge
                    asserted a live security camera view that did not exist. The
                    honest version names the gap and points at Home Assistant,
                    which does serve the stream. */}
                <div
                  className="flex flex-col items-center justify-center gap-3 text-center"
                  style={{
                    minHeight: 170,
                    borderRadius: 20,
                    background: STRIPES,
                    border: "1px solid var(--line)",
                    padding: 20,
                  }}
                >
                  <MsIcon name="videocam_off" size={38} style={{ color: "var(--text3)" }} />
                  <span style={{ ...microLabel, fontSize: 11 }}>
                    Live preview not in this build
                  </span>
                  <PillButton
                    label="Watch in Home Assistant"
                    icon="open_in_new"
                    size="sm"
                    tone="quiet"
                    href={haUrl}
                  />
                </div>
              </Panel>
            ))}
          </div>
        )}
      </div>

      {/* Motion sensors */}
      <div className="mt-2">
        <SubHeading icon="sensors">Motion</SubHeading>
        {motionSensors.length === 0 ? (
          <EmptyPanel icon="sensors">No motion sensors on this box</EmptyPanel>
        ) : (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {motionSensors.map((s) =>
              sensorPanel(s, "sensors", "Motion", "Clear", RED, "var(--mint)")
            )}
          </div>
        )}
      </div>

      {/* Occupancy */}
      {occupancySensors.length > 0 && (
        <div className="mt-2">
          <SubHeading icon="group">Occupancy</SubHeading>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {occupancySensors.map((s) =>
              sensorPanel(s, "group", "Occupied", "Empty", "var(--mint)", "var(--text3)")
            )}
          </div>
        </div>
      )}

      {/* Person / vehicle / animal detection */}
      {personSensors.length > 0 && (
        <div className="mt-2">
          <SubHeading icon="directions_walk">Detection</SubHeading>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {personSensors.map((s) =>
              sensorPanel(s, "directions_walk", "Detected", "Clear", RED, "var(--mint)")
            )}
          </div>
        </div>
      )}
    </>
  );
}

/* ================================================================== */
/*  AUTOMATIONS TAB                                                    */
/* ================================================================== */

function AutomationsTab({
  automations,
  scenes,
  onToggle,
  onTrigger,
  onActivateScene,
}: {
  automations: HAEntity[];
  scenes: HAEntity[];
  onToggle: (entityId: string, state: string) => void;
  onTrigger: (entityId: string) => void;
  onActivateScene: (entityId: string) => void;
}) {
  return (
    <>
      {/* Automations */}
      <div>
        <SubHeading icon="settings">Automations</SubHeading>
        {automations.length === 0 ? (
          <EmptyPanel icon="settings">No automations on this box</EmptyPanel>
        ) : (
          <div className="flex flex-col gap-3">
            {automations.map((auto) => {
              const isOn = auto.state === "on";
              const lastTriggered =
                auto.attributes?.last_triggered ||
                (auto as unknown as { last_triggered?: string }).last_triggered;

              return (
                <Panel key={auto.entity_id}>
                  <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                    <div className="flex min-w-0 flex-1 items-center gap-3">
                      <IconBadge
                        icon="settings"
                        color={isOn ? "var(--lav)" : "var(--text3)"}
                      />
                      <div className="min-w-0 flex-1">
                        <div className="truncate" style={nameText}>
                          {friendlyName(auto)}
                        </div>
                        <div style={microLabel}>
                          {lastTriggered
                            ? `Last ran ${formatRelativeTime(lastTriggered)}`
                            : "Never run"}
                        </div>
                      </div>
                    </div>
                    <div className="flex flex-wrap items-center gap-2.5">
                      <button
                        type="button"
                        onClick={() => onToggle(auto.entity_id, auto.state)}
                        aria-pressed={isOn}
                        className="inline-flex cursor-pointer items-center gap-1.5 transition-transform hover:-translate-y-0.5"
                        style={{
                          height: 34,
                          padding: "0 14px",
                          borderRadius: 99,
                          border: isOn ? "1px solid transparent" : "1px solid var(--line)",
                          background: isOn ? tintOf("var(--lav)", 18) : "var(--card2)",
                          color: isOn ? "var(--lav)" : "var(--text2)",
                          fontFamily: NUNITO,
                          fontWeight: 700,
                          fontSize: 13,
                        }}
                      >
                        <MsIcon name={isOn ? "check_circle" : "flash_off"} size={16} />
                        {isOn ? "Enabled" : "Disabled"}
                      </button>
                      <PillButton
                        label="Run now"
                        icon="bolt"
                        size="sm"
                        onClick={() => onTrigger(auto.entity_id)}
                      />
                    </div>
                  </div>
                </Panel>
              );
            })}
          </div>
        )}
      </div>

      {/* Scenes */}
      {scenes.length > 0 && (
        <div className="mt-2">
          <SubHeading icon="bolt">Scenes</SubHeading>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {scenes.map((scene) => (
              <Panel
                key={scene.entity_id}
                className="transition-transform hover:-translate-y-0.5"
              >
                <div className="flex items-center justify-between gap-3">
                  <div className="flex min-w-0 items-center gap-3">
                    <IconBadge icon="bolt" color="var(--lav)" />
                    <div className="truncate" style={nameText}>
                      {friendlyName(scene)}
                    </div>
                  </div>
                  <PillButton
                    label="Set"
                    icon="bolt"
                    size="sm"
                    onClick={() => onActivateScene(scene.entity_id)}
                  />
                </div>
              </Panel>
            ))}
          </div>
        </div>
      )}
    </>
  );
}

/* ================================================================== */
/*  Helpers                                                            */
/* ================================================================== */

function friendlyName(entity: {
  attributes?: { friendly_name?: string };
  friendly_name?: string;
  entity_id?: string;
}): string {
  return (
    entity.attributes?.friendly_name ||
    entity.friendly_name ||
    entity.entity_id?.split(".").pop()?.replace(/_/g, " ") ||
    entity.entity_id ||
    "Unknown"
  );
}

function capitalize(s: string): string {
  if (!s) return s;
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function formatRelativeTime(isoString: string): string {
  try {
    const then = new Date(isoString).getTime();
    const now = Date.now();
    const diffSec = Math.floor((now - then) / 1000);
    if (diffSec < 0) return "just now";
    if (diffSec < 60) return "just now";
    if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
    if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h ago`;
    return `${Math.floor(diffSec / 86400)}d ago`;
  } catch {
    return "unknown";
  }
}
