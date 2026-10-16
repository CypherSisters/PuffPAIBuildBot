"use client";

/**
 * Agent Team — the seven-agent roster, the message bus, a task box, the activity
 * feed and quick actions. Converted off the admin template (SectionMain,
 * SectionTitleLineWithButton, CardBox, Button, Icon+mdi, gray/slate `dark:`
 * pairs, Tailwind gradient stops) into the design-match shell.
 *
 * THE RETIRED AGENTS ARE GONE FROM THE UI.
 *
 * `cortex` and `bridge` are not agents in this build. They are the
 * `evolves_from` lineage fields on the roster in src/puffpai/agents/registry.py
 * — Concierge evolved from Cortex, Steward from Bridge — and the build
 * constructs SEVEN agents: concierge, steward, sentinel (shown as Drawbridge),
 * archivist, majordomo, caretaker, toolsmith. Three places put the retired
 * names back on
 * screen, and all three are fixed here:
 *
 *  1. The backend-unreachable fallback hard-coded tiles for "Cortex", "Bridge"
 *     and Drawbridge — a roster that has not existed for a long time, shown
 *     exactly when the owner is least able to check. It now lists the real seven,
 *     each plainly marked as not reporting rather than drawn as idle-and-fine.
 *  2. `AGENT_META.cortex` was the default for any unknown agent id, so anything
 *     unrecognised rendered as a brain-icon card labelled "Cortex". The default
 *     is now a neutral, honestly-labelled entry.
 *  3. The "create automations" button posted its task to agent id `bridge`.
 *     Automations are Steward's (home/tools) — a retired id is not a routing
 *     target. It now sends `steward`.
 *
 * The internal ids the API speaks are untouched: `sentinel` remains the id and
 * Drawbridge remains the only name shown. LEGACY_ALIAS
 * stays because the ACTIVITY LOG still contains historical entries written under
 * the embryo names — mapping them forward is how old rows keep the right label,
 * which is different from advertising them as live agents.
 */

import { useEffect, useState, useCallback, type CSSProperties } from "react";
import MsIcon from "../../_components/MsIcon";
import {
  getAgentStatuses,
  getAgentActivity,
  sendAgentTask,
  type AgentStatus,
  type AgentActivity,
  type TeamAgent,
  type AgentBus,
  type AgentCapability,
} from "../../_lib/services/agentService";
import Link from "next/link";
import { useBuildStatus } from "../../_lib/hooks/useBuildStatus";
import { STATUS_TONE, jobHref, jobLabel } from "../../_lib/buildJobs";

/* ── shell vocabulary ───────────────────────────────────────────── */

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
  borderRadius: 28,
  border: "1px solid var(--line)",
  background: "var(--card)",
  backdropFilter: "blur(18px)",
  WebkitBackdropFilter: "blur(18px)",
  padding: "20px 24px",
  boxShadow: "var(--shadow)",
};

const bodyText: CSSProperties = {
  fontFamily: NUNITO,
  fontWeight: 500,
  fontSize: 15,
  lineHeight: 1.5,
  color: "var(--text2)",
};

const fieldStyle: CSSProperties = {
  borderRadius: 14,
  border: "1px solid var(--line)",
  background: "var(--card2)",
  color: "var(--text)",
  fontFamily: NUNITO,
  fontWeight: 600,
  fontSize: 15,
  padding: "11px 14px",
  outline: "none",
};

const tintOf = (color: string, pct: number) =>
  `color-mix(in srgb, ${color} ${pct}%, transparent)`;

function Panel({
  children,
  style,
  className,
}: {
  children: React.ReactNode;
  style?: CSSProperties;
  className?: string;
}) {
  return (
    <div className={className} style={{ ...panelBase, ...style }}>
      {children}
    </div>
  );
}

function PillButton({
  label,
  icon,
  onClick,
  disabled,
  tone = "primary",
  title,
}: {
  label?: string;
  icon: string;
  onClick: () => void;
  disabled?: boolean;
  tone?: "primary" | "quiet" | "danger";
  title?: string;
}) {
  const bg =
    tone === "primary"
      ? "linear-gradient(140deg,var(--pink),var(--magenta))"
      : tone === "danger"
        ? tintOf(RED, 16)
        : "var(--card2)";
  const fg = tone === "primary" ? "#fff" : tone === "danger" ? RED : "var(--text2)";
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={title}
      aria-label={label ?? title}
      className="inline-flex cursor-pointer items-center justify-center gap-2 transition-transform hover:-translate-y-0.5 disabled:cursor-default disabled:opacity-50"
      style={{
        height: 42,
        padding: label ? "0 20px" : "0 12px",
        borderRadius: 99,
        border: tone === "primary" ? "1px solid transparent" : "1px solid var(--line)",
        background: bg,
        color: fg,
        fontFamily: NUNITO,
        fontWeight: 700,
        fontSize: 15,
      }}
    >
      <MsIcon name={icon} size={19} />
      {label}
    </button>
  );
}

function SubHeading({ children }: { children: React.ReactNode }) {
  return (
    <div
      className="mb-3"
      style={{
        fontFamily: FREDOKA,
        fontWeight: 600,
        fontSize: 21,
        color: "var(--text)",
      }}
    >
      {children}
    </div>
  );
}

/** A small labelled figure, used all over the result panels. */
function Figure({
  value,
  label,
  color = "var(--text)",
}: {
  value: React.ReactNode;
  label: string;
  color?: string;
}) {
  return (
    <div
      className="text-center"
      style={{
        padding: "12px 10px",
        borderRadius: 18,
        background: "var(--card2)",
        border: "1px solid var(--line)",
      }}
    >
      <div
        style={{
          fontFamily: FREDOKA,
          fontWeight: 600,
          fontSize: 24,
          lineHeight: 1.1,
          color,
        }}
      >
        {value}
      </div>
      <div style={{ ...microLabel, marginTop: 2, fontSize: 10 }}>{label}</div>
    </div>
  );
}

/** A chip, for tags and short states. */
function Chip({
  children,
  color = "var(--text2)",
  mono,
}: {
  children: React.ReactNode;
  color?: string;
  mono?: boolean;
}) {
  return (
    <span
      className="inline-flex max-w-full flex-none items-center justify-center whitespace-nowrap"
      style={{
        padding: "5px 12px",
        borderRadius: 99,
        background: "var(--chip)",
        color,
        fontFamily: mono ? JBMONO : NUNITO,
        fontWeight: 700,
        fontSize: 12,
        lineHeight: 1,
      }}
    >
      {children}
    </span>
  );
}

/* ── Agent definitions ────────────────────────────────────────────
   THE ROSTER IS SEVEN (src/puffpai/agents/registry.py). There is deliberately
   no `cortex` or `bridge` entry — those are lineage, not agents. `toolsmith` is
   the seventh master agent (WF-7, the generic tool tier) and IS constructed by
   this build. Colours are theme tokens so the cards follow the day/night
   palette. */
const AGENT_META: Record<
  string,
  { label: string; role: string; icon: string; color: string }
> = {
  // The chat tool loop: the agent she talks to. Its card carries the bot's
  // own name from the backend (display_name); this is only the fallback.
  chat: {
    label: "The one you talk to",
    role: "Chat, and the tools it uses for you",
    icon: "forum",
    color: "var(--pink)",
  },
  concierge: {
    label: "Concierge",
    role: "Front desk, routing",
    icon: "headset_mic",
    color: "var(--pink)",
  },
  steward: {
    label: "Steward",
    role: "Home and tools",
    icon: "home_iot_device",
    color: "var(--sky)",
  },
  // Display name is Drawbridge; `sentinel` is the
  // internal agent id the API speaks and must not change.
  sentinel: {
    label: "Drawbridge",
    role: "Privacy and security",
    icon: "verified_user",
    color: "var(--lemon)",
  },
  archivist: {
    label: "Archivist",
    role: "Archive and knowledge",
    icon: "inventory_2",
    color: "var(--mint)",
  },
  majordomo: {
    label: "Majordomo",
    role: "Orchestration, scheduling",
    icon: "event_note",
    color: "var(--lav)",
  },
  caretaker: {
    label: "Caretaker",
    role: "Health and wellbeing",
    icon: "monitor_heart",
    color: "var(--magenta)",
  },
  toolsmith: {
    label: "Toolsmith",
    role: "Generic tools and the workspace",
    icon: "handyman",
    color: "var(--sky)",
  },
};

/** Roster order, matching registry.py's ROSTER. Used for the fallback list so
 *  the seven appear in canonical order rather than object-key order. */
const ROSTER_IDS = [
  "concierge",
  "steward",
  "sentinel",
  "archivist",
  "majordomo",
  "caretaker",
  "toolsmith",
] as const;

/** Neutral meta for an id that is not on the roster. Previously this defaulted
 *  to AGENT_META.cortex, which labelled unknown agents "Cortex" — a retired
 *  name, asserted about something we cannot identify. */
const UNKNOWN_META = {
  label: "Unrecognised agent",
  role: "Not on this build's roster",
  icon: "help",
  color: "var(--text3)",
};

// The `agent` here is only the display tag + colour: POST /agents/task routes
// on the task TEXT (see _match_task_action), so these map to the product-team
// agent each request naturally belongs to, not the retired embryo names.
const QUICK_ACTIONS = [
  // Caretaker - health
  { label: "System Health", agent: "caretaker", task: "Run a full system health check on all services", icon: "monitor_heart" },
  // Steward - home / devices / automations
  { label: "Device Analytics", agent: "steward", task: "Show device analytics - unavailable devices and low batteries", icon: "insights" },
  { label: "Suggest Automations", agent: "steward", task: "Suggest automations based on my devices", icon: "smart_toy" },
  { label: "Home Summary", agent: "steward", task: "Give me a full environment summary - temps, lights, who is home", icon: "device_thermostat" },
  { label: "Discover Devices", agent: "steward", task: "Discover all devices and show a full inventory", icon: "home_iot_device" },
  { label: "Morning Routine", agent: "steward", task: "Good morning - lights on, weather report", icon: "wb_sunny" },
  { label: "Leaving Home", agent: "steward", task: "Leaving home - lock up and turn off lights", icon: "directions_run" },
  { label: "Toggle Lights", agent: "steward", task: "Toggle all lights", icon: "lightbulb" },
  // Drawbridge (agent id `sentinel`) - security / privacy
  { label: "Security Audit", agent: "sentinel", task: "Run a full security audit - check all locks, doors, cameras", icon: "lock" },
  { label: "Privacy Scan", agent: "sentinel", task: "Run a privacy audit on all configs and data files", icon: "policy" },
  { label: "Network Inventory", agent: "sentinel", task: "Show all network connected devices", icon: "lan" },
  { label: "Exposure Check", agent: "sentinel", task: "Check if any services are externally exposed", icon: "shield" },
];

/* ── Helper components ────────────────────────────────────────── */

function StatusBadge({ status }: { status: string }) {
  const map: Record<string, { color: string; icon: string }> = {
    active: { color: "var(--mint)", icon: "check_circle" },
    working: { color: "var(--sky)", icon: "schedule" },
    completed: { color: "var(--mint)", icon: "check_circle" },
    idle: { color: "var(--text3)", icon: "radio_button_unchecked" },
    queued: { color: "var(--lemon)", icon: "schedule" },
    error: { color: RED, icon: "error" },
  };
  const s = map[status] || map.idle;
  return (
    <span
      className="inline-flex flex-none items-center gap-1.5"
      style={{
        padding: "5px 12px",
        borderRadius: 99,
        background: tintOf(s.color, 16),
        color: s.color,
        fontFamily: JBMONO,
        fontWeight: 700,
        fontSize: 11,
        letterSpacing: ".06em",
        textTransform: "uppercase",
      }}
    >
      <MsIcon name={s.icon} size={14} />
      {status}
    </span>
  );
}

function timeAgo(ts: string | null): string {
  if (!ts) return "never";
  const diff = Date.now() - new Date(ts).getTime();
  if (diff < 60_000) return "just now";
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
  return `${Math.floor(diff / 86_400_000)}d ago`;
}

/* ── Stat row (label left, value right) ───────────────────────── */
function Stat({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div
      className="flex items-center justify-between gap-3"
      style={{ padding: "7px 0", borderBottom: "1px solid var(--line)" }}
    >
      <span style={microLabel}>{label}</span>
      <span
        style={{
          fontFamily: NUNITO,
          fontWeight: 700,
          fontSize: 15,
          color: color || "var(--text)",
        }}
      >
        {value}
      </span>
    </div>
  );
}

function capitalize(s: string): string {
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : s;
}

/** The agent bus can hand back strings carrying an em dash (a health note, a
 *  model class). This dashboard renders no em dashes, so normalise them to a
 *  hyphen at the render boundary. Built via fromCharCode so the source file
 *  itself stays em-dash-free. */
const EM_DASH = String.fromCharCode(0x2014);
function stripEmDash(s: string): string {
  return s.split(EM_DASH).join("-");
}

/**
 * Health values arrive loosely typed from the agent bus: scalars, string
 * arrays (unit lists) and small objects (e.g. `restart_policy`). `String(val)`
 * turned the objects into the literal "[object Object]"; flatten them to a
 * readable "key: value · key: value" instead.
 */
function formatHealthValue(val: unknown): string {
  if (val === null || val === undefined) return "-";
  if (Array.isArray(val)) return val.map((v) => formatHealthValue(v)).join(", ");
  if (typeof val === "object") {
    return Object.entries(val as Record<string, unknown>)
      .map(([k, v]) => `${k.replace(/_/g, " ")}: ${formatHealthValue(v)}`)
      .join(" · ");
  }
  return stripEmDash(String(val));
}

/* ── Bus status strip ─────────────────────────────────────────── */
function BusStrip({ bus }: { bus: AgentBus }) {
  return (
    <Panel style={{ borderRadius: 24, padding: "16px 22px" }}>
      <div className="flex flex-wrap items-center gap-x-7 gap-y-3">
        <div className="flex items-center gap-2">
          <MsIcon name="lan" size={19} style={{ color: "var(--pink)" }} />
          <span
            style={{
              fontFamily: FREDOKA,
              fontWeight: 600,
              fontSize: 18,
              color: "var(--text)",
            }}
          >
            Message bus
          </span>
        </div>
        <Stat2 label="Agents" value={String(bus.agent_count)} />
        <div className="flex items-center gap-2">
          <span style={microLabel}>Bus</span>
          <span
            className="inline-flex items-center gap-1.5"
            style={{
              fontFamily: JBMONO,
              fontWeight: 700,
              fontSize: 12,
              color: bus.enabled ? "var(--mint)" : "var(--text3)",
            }}
          >
            <MsIcon
              name={bus.enabled ? "check_circle" : "radio_button_unchecked"}
              size={14}
            />
            {bus.enabled ? "enabled" : "disabled"}
          </span>
        </div>
        <Stat2 label="Reviewer" value={bus.reviewer_display || capitalize(bus.reviewer)} />
        <div className="flex items-center gap-2">
          <span style={microLabel}>Reviewer wired</span>
          <span
            className="inline-flex items-center gap-1.5"
            style={{
              fontFamily: JBMONO,
              fontWeight: 700,
              fontSize: 12,
              color: bus.reviewer_wired ? "var(--mint)" : RED,
            }}
          >
            <MsIcon name={bus.reviewer_wired ? "check_circle" : "cancel"} size={14} />
            {bus.reviewer_wired ? "wired" : "unwired"}
          </span>
        </div>
      </div>
    </Panel>
  );
}

/* ── Inline stat (label + value on one line) ──────────────────── */
function Stat2({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center gap-2">
      <span style={microLabel}>{label}</span>
      <span
        style={{
          fontFamily: NUNITO,
          fontWeight: 700,
          fontSize: 15,
          color: "var(--text)",
        }}
      >
        {value}
      </span>
    </div>
  );
}

/** Legacy activity-log ids → the product-team agent each became.
 *  The activity log still HOLDS HISTORICAL ROWS written under the embryo names,
 *  so without this those rows would answer to one name in the roster and another
 *  in the feed. This maps old data forward; it does not make them agents. */
const LEGACY_ALIAS: Record<string, string> = {
  cortex: "concierge",
  bridge: "steward",
  sentinel: "sentinel",
};

/** What to call an agent on screen: the build's display name if the backend
 *  sent one, then the built-in label, then the raw id. Never renders blank. */
function agentLabel(id: string, team: TeamAgent[]): string {
  const canonical = LEGACY_ALIAS[id] || id;
  const row = team.find((t) => t.name === canonical);
  return (
    row?.display_name ||
    AGENT_META[canonical]?.label ||
    AGENT_META[id]?.label ||
    id
  );
}

/** Visual meta (icon/colour) for an id, resolving legacy embryo ids
 *  (cortex/bridge) to the product-team agent they became - so historical feed
 *  rows tint like the roster cards instead of showing a stale colour. */
function agentMetaFor(id: string) {
  const canonical = LEGACY_ALIAS[id] || id;
  return AGENT_META[canonical] || AGENT_META[id] || UNKNOWN_META;
}

/** Human wording + colour for the feed's raw event ids. Anything unmapped is
 *  rendered by replacing underscores with spaces, so a new event type still
 *  reads cleanly. */
const EVENT_META: Record<string, { label: string; color: string }> = {
  task_started: { label: "started", color: "var(--sky)" },
  task_queued: { label: "queued", color: "var(--lemon)" },
  task_completed: { label: "completed", color: "var(--mint)" },
  task_unhandled: { label: "no handler", color: "var(--text3)" },
  task_rerouted: { label: "rerouted", color: "var(--lav)" },
  consent_requested: { label: "consent asked", color: "var(--lemon)" },
  consent_resolved: { label: "consent resolved", color: "var(--mint)" },
};

/* ── Capability status styling ────────────────────────────────── */
const CAP_STATUS: Record<string, { color: string; label: string }> = {
  live: { color: "var(--mint)", label: "live" },
  "needs-auth": { color: "var(--lemon)", label: "needs setup" },
  offline: { color: "var(--text3)", label: "offline" },
  planned: { color: "var(--text3)", label: "planned" },
};

/** One capability line in an agent's "Handles" list: a status dot, the human
 *  label, a write badge when it can change state, and a note on hover. */
function CapabilityRow({ cap }: { cap: AgentCapability }) {
  const s = CAP_STATUS[cap.status] || CAP_STATUS.live;
  const writes = cap.access !== "read";
  return (
    <div className="flex items-center gap-2 py-1" title={cap.note || cap.label}>
      <span
        className="h-1.5 w-1.5 flex-none rounded-full"
        style={{ background: s.color }}
      />
      <span
        className="flex-1 truncate"
        style={{ fontFamily: NUNITO, fontWeight: 600, fontSize: 13, color: "var(--text2)" }}
      >
        {cap.label}
      </span>
      {writes && (
        <span
          className="inline-flex flex-none items-center justify-center"
          style={{
            padding: "2px 7px",
            borderRadius: 6,
            background: "var(--chip)",
            color: "var(--lav)",
            fontFamily: JBMONO,
            fontWeight: 700,
            fontSize: 9,
            letterSpacing: ".06em",
            textTransform: "uppercase",
            lineHeight: 1.2,
          }}
        >
          {cap.access}
        </span>
      )}
      {cap.status !== "live" && (
        <span
          className="flex-none"
          style={{ fontFamily: JBMONO, fontWeight: 700, fontSize: 10, color: s.color }}
        >
          {s.label}
        </span>
      )}
    </div>
  );
}

/* ── Team agent card ──────────────────────────────────────────── */
function TeamAgentCard({ agent }: { agent: TeamAgent }) {
  const meta = AGENT_META[agent.name] || UNKNOWN_META;
  const caps = agent.capabilities || [];
  const recent = agent.recent || [];
  const working = agent.status === "working";
  // Health keys already shown elsewhere or rendered as the ok indicator.
  const extraHealth = Object.entries(agent.health || {}).filter(
    ([key]) => !["agent", "ok", "scope"].includes(key)
  );
  return (
    <Panel style={{ position: "relative", overflow: "hidden", borderRadius: 26 }}>
      <div
        style={{
          position: "absolute",
          top: 0,
          left: 0,
          right: 0,
          height: 3,
          background: `linear-gradient(90deg, ${meta.color}, transparent)`,
        }}
      />
      {/* ── Header: name + role + live status ── */}
      <div className="mb-3 flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3">
          <span
            className="flex flex-none items-center justify-center"
            style={{
              width: 42,
              height: 42,
              borderRadius: 14,
              background: tintOf(meta.color, 16),
              color: meta.color,
            }}
          >
            <MsIcon name={meta.icon} size={22} />
          </span>
          <div className="min-w-0">
            <div
              style={{
                fontFamily: FREDOKA,
                fontWeight: 600,
                fontSize: 20,
                lineHeight: 1.15,
                color: "var(--text)",
              }}
            >
              {agent.display_name || meta.label || capitalize(agent.name)}
            </div>
            <div style={microLabel}>{meta.role}</div>
          </div>
        </div>
        <StatusBadge status={agent.status} />
      </div>

      {/* ── Identity chips: scope · workflow (lineage dropped - the
            "← Cortex/Bridge" tags re-introduced the retired names). */}
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Chip>{agent.scope}</Chip>
        {agent.workflow && <Chip mono>{agent.workflow}</Chip>}
      </div>

      {/* ── What it's doing right now ── */}
      <div
        className="mb-3 flex items-start gap-2.5"
        style={{
          padding: "11px 14px",
          borderRadius: 16,
          background: "var(--card2)",
          border: "1px solid var(--line)",
        }}
      >
        <MsIcon
          name={working ? "pending" : agent.task ? "task_alt" : "bedtime"}
          size={17}
          style={{
            color: working ? "var(--pink)" : "var(--text3)",
            flex: "none",
            marginTop: 2,
          }}
        />
        <div className="min-w-0">
          <div style={{ ...microLabel, fontSize: 10 }}>
            {working ? "Currently" : agent.task ? `Last job, ${timeAgo(agent.timestamp)}` : "Status"}
          </div>
          <div
            className="truncate"
            style={{ fontFamily: NUNITO, fontWeight: 600, fontSize: 14, color: "var(--text)" }}
          >
            {agent.task || `Idle - last active ${timeAgo(agent.timestamp)}`}
          </div>
        </div>
      </div>

      {/* ── Its newest work (installs, pages, feeds, routines, tool calls) ── */}
      {recent.length > 1 && (
        <div className="mb-3">
          <div style={{ ...microLabel, fontSize: 10, marginBottom: 4 }}>Recently</div>
          {recent.slice(1).map((r, i) => (
            <div
              key={`${r.timestamp}-${i}`}
              className="flex items-center justify-between gap-2"
              style={{ fontFamily: NUNITO, fontSize: 13, color: "var(--text2)", padding: "2px 0" }}
            >
              <span className="truncate" title={r.reason || undefined}>
                {r.task}
                {r.status === "error" && r.reason ? `: ${r.reason}` : ""}
              </span>
              <span style={{ fontFamily: JBMONO, fontSize: 11, color: "var(--text3)", flex: "none" }}>
                {r.status === "error" ? "did not work, " : ""}
                {timeAgo(r.timestamp || null)}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* ── What it handles (from the capability registry) ── */}
      {caps.length > 0 && (
        <div>
          <div style={{ ...microLabel, fontSize: 10, marginBottom: 2 }}>
            Handles · {caps.length}
          </div>
          <div className="divide-y" style={{ borderColor: "var(--line)" }}>
            {caps.map((c) => (
              <CapabilityRow key={c.id} cap={c} />
            ))}
          </div>
        </div>
      )}

      {agent.model_class && (
        <div style={{ ...microLabel, fontSize: 10, marginTop: 12, textTransform: "none", letterSpacing: 0 }}>
          {stripEmDash(agent.model_class).replace(/\s*\+\s*/g, ", ")}
        </div>
      )}

      {/* ── Health footer ── */}
      <div style={{ marginTop: 12, paddingTop: 12, borderTop: "1px solid var(--line)" }}>
        <div className="flex flex-wrap items-center gap-2">
          <span
            className="inline-flex items-center gap-1.5"
            style={{
              fontFamily: JBMONO,
              fontWeight: 700,
              fontSize: 12,
              color: agent.health?.ok ? "var(--mint)" : RED,
            }}
          >
            <MsIcon name={agent.health?.ok ? "check_circle" : "cancel"} size={14} />
            {agent.health?.ok ? "ok" : "fault"}
          </span>
          {extraHealth.map(([key, val]) => {
            const isBool = typeof val === "boolean";
            const good = isBool ? val : true;
            return (
              <span
                key={key}
                className="inline-flex max-w-full items-center gap-1.5"
                style={{
                  padding: "4px 10px",
                  borderRadius: 99,
                  background: "var(--chip)",
                  color: isBool && good ? "var(--mint)" : "var(--text2)",
                  fontFamily: JBMONO,
                  fontWeight: 600,
                  fontSize: 11,
                }}
              >
                <span className="whitespace-nowrap">{key.replace(/_/g, " ")}</span>
                {!isBool && (
                  <span
                    className="max-w-[16rem] truncate"
                    style={{ fontWeight: 700, color: "var(--text)" }}
                    title={formatHealthValue(val)}
                  >
                    {formatHealthValue(val)}
                  </span>
                )}
              </span>
            );
          })}
        </div>
      </div>
    </Panel>
  );
}

/* ── Result Panel - renders structured data per action type ──── */
function ResultPanel({
  taskResult,
  onClose,
  onExecuteAction,
}: {
  taskResult: { routed_to: string; status: string; result?: any } | null;
  onClose: () => void;
  onExecuteAction: (task: string, agent: string, params: Record<string, any>) => void;
}) {
  const [selectedSuggestions, setSelectedSuggestions] = useState<number[]>([]);

  if (!taskResult || !taskResult.result) return null;
  const r = taskResult.result;
  const data = r.result || {};
  const action: string = r.action || "";
  const agent = taskResult.routed_to;
  const meta = AGENT_META[agent] || UNKNOWN_META;

  const heading = (text: string) => (
    <div style={{ ...microLabel, marginBottom: 8 }}>{text}</div>
  );

  return (
    <Panel style={{ position: "relative", overflow: "hidden" }}>
      <div
        style={{
          position: "absolute",
          top: 0,
          left: 0,
          right: 0,
          height: 3,
          background: `linear-gradient(90deg, ${meta.color}, transparent)`,
        }}
      />
      <div className="mb-4 flex items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2.5">
          <span
            className="flex flex-none items-center justify-center"
            style={{
              width: 32,
              height: 32,
              borderRadius: 11,
              background: tintOf(meta.color, 16),
              color: meta.color,
            }}
          >
            <MsIcon name={meta.icon} size={18} />
          </span>
          <span
            style={{
              fontFamily: FREDOKA,
              fontWeight: 600,
              fontSize: 19,
              color: "var(--text)",
            }}
          >
            {meta.label}
          </span>
          <span style={microLabel}>{action}</span>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close"
          className="cursor-pointer"
          style={{ background: "none", border: "none", color: "var(--text3)", padding: 2 }}
        >
          <MsIcon name="close" size={19} />
        </button>
      </div>

      {/* ── System Health ──────────────────────────────────── */}
      {action === "system-health" && (
        <div>
          <p style={{ ...bodyText, margin: "0 0 14px", color: "var(--text)" }}>
            {data.summary}
          </p>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
            {Object.entries(data.services || {}).map(([name, info]: [string, any]) => {
              const up = info.status === "online" || info.status === "available";
              return (
                <div
                  key={name}
                  style={{
                    padding: 14,
                    borderRadius: 18,
                    background: "var(--card2)",
                    border: "1px solid var(--line)",
                  }}
                >
                  <div className="mb-1 flex items-center justify-between gap-2">
                    <span style={{ ...microLabel, fontSize: 10 }}>
                      {name.replace(/_/g, " ")}
                    </span>
                    <span
                      style={{
                        fontFamily: JBMONO,
                        fontWeight: 700,
                        fontSize: 11,
                        color: up ? "var(--mint)" : RED,
                      }}
                    >
                      {info.status}
                    </span>
                  </div>
                  {info.models && (
                    <p className="truncate" style={{ ...bodyText, margin: 0, fontSize: 13 }}>
                      {info.models.join(", ")}
                    </p>
                  )}
                  {info.uptime && (
                    <p style={{ ...bodyText, margin: 0, fontSize: 13 }}>{info.uptime}</p>
                  )}
                  {info.message && (
                    <p className="truncate" style={{ ...bodyText, margin: 0, fontSize: 13 }}>
                      {info.message}
                    </p>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* ── Security Audit ─────────────────────────────────── */}
      {action === "security-audit" && (
        <div>
          <div className="mb-4 flex items-center gap-4">
            <div
              style={{
                fontFamily: FREDOKA,
                fontWeight: 600,
                fontSize: 38,
                lineHeight: 1,
                color: "var(--text)",
              }}
            >
              {data.security_score}
            </div>
            <div style={microLabel}>Security score</div>
          </div>
          <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4">
            <Figure value={data.locks} label="Locks" />
            <Figure value={data.doors} label="Doors" />
            <Figure value={data.cameras} label="Cameras" />
            <Figure value={data.motion_sensors} label="Motion" />
          </div>
          {data.issues && (
            <div>
              {heading("Issues")}
              {data.issues.map((issue: string, i: number) => (
                <p
                  key={i}
                  style={{
                    ...bodyText,
                    margin: "4px 0",
                    color: issue.includes("All clear") ? "var(--mint)" : "var(--lemon)",
                    fontWeight: 600,
                  }}
                >
                  {issue}
                </p>
              ))}
            </div>
          )}
          {data.issues && !data.issues.every((i: string) => i.includes("All clear")) && (
            <div className="mt-4">
              <PillButton
                label="Fix all security issues"
                icon="lock"
                onClick={() =>
                  onExecuteAction(
                    "Fix security issues - lock all locks, close doors",
                    "sentinel",
                    { actions: ["lock_all", "close_doors", "arm_alarm"] }
                  )
                }
              />
            </div>
          )}
        </div>
      )}

      {/* ── Device Analytics ───────────────────────────────── */}
      {action === "device-analytics" && (
        <div>
          <Stat label="Total entities" value={String(data.total_entities)} />
          <Stat
            label="Unavailable"
            value={String(data.unavailable_count)}
            color={data.unavailable_count > 0 ? "var(--lemon)" : "var(--mint)"}
          />
          {data.low_battery?.length > 0 && (
            <div className="mt-3">
              {heading("Low battery")}
              {data.low_battery.map((d: any, i: number) => (
                <p key={i} style={{ ...bodyText, margin: "3px 0", color: "var(--lemon)", fontWeight: 600 }}>
                  {d.name}: {d.battery}
                </p>
              ))}
            </div>
          )}
          <div className="mt-3">
            {heading("By domain")}
            <div className="grid grid-cols-2 gap-x-4">
              {Object.entries(data.by_domain || {}).map(([domain, count]: [string, any]) => (
                <div key={domain} className="flex justify-between py-0.5">
                  <span style={{ ...bodyText, fontSize: 13 }}>{domain}</span>
                  <span style={{ fontFamily: JBMONO, fontWeight: 700, fontSize: 13, color: "var(--text)" }}>
                    {count}
                  </span>
                </div>
              ))}
            </div>
          </div>
          {data.unavailable_count > 0 && data.unavailable_devices?.length > 0 && (
            <div className="mt-3">
              {heading("Unavailable devices")}
              {data.unavailable_devices.map((name: string, i: number) => (
                <p key={i} style={{ ...bodyText, margin: "2px 0", fontSize: 13 }}>
                  {name}
                </p>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── Suggest Automations ────────────────────────────── */}
      {action === "suggest-automations" && (
        <div>
          <Stat label="Existing automations" value={String(data.existing_automations)} />
          <Stat label="New suggestions" value={String(data.suggestions_count)} />
          <div className="mt-3 flex flex-col gap-3">
            {(data.suggestions || []).map((s: any, i: number) => {
              const on = selectedSuggestions.includes(i);
              const prioColor =
                s.priority === "high"
                  ? RED
                  : s.priority === "medium"
                    ? "var(--lemon)"
                    : "var(--text3)";
              return (
                <div
                  key={i}
                  className="cursor-pointer"
                  style={{
                    padding: 14,
                    borderRadius: 20,
                    border: on ? "2px solid var(--pink)" : "1px solid var(--line)",
                    background: on ? tintOf("var(--pink)", 10) : "var(--card2)",
                  }}
                  onClick={() =>
                    setSelectedSuggestions((prev) =>
                      prev.includes(i) ? prev.filter((x) => x !== i) : [...prev, i]
                    )
                  }
                >
                  <div className="mb-1.5 flex items-center gap-2.5">
                    <MsIcon
                      name={on ? "check_circle" : "radio_button_unchecked"}
                      size={19}
                      style={{ color: on ? "var(--pink)" : "var(--text3)", flex: "none" }}
                    />
                    <span
                      className="min-w-0 flex-1"
                      style={{ fontFamily: NUNITO, fontWeight: 700, fontSize: 15, color: "var(--text)" }}
                    >
                      {s.name}
                    </span>
                    <span
                      className="flex-none"
                      style={{
                        padding: "3px 10px",
                        borderRadius: 99,
                        background: tintOf(prioColor, 16),
                        color: prioColor,
                        fontFamily: JBMONO,
                        fontWeight: 700,
                        fontSize: 10,
                        textTransform: "uppercase",
                      }}
                    >
                      {s.priority}
                    </span>
                  </div>
                  <p style={{ ...bodyText, margin: "2px 0 0 29px", fontSize: 13 }}>
                    <b style={{ color: "var(--text)" }}>Trigger:</b> {s.trigger}
                  </p>
                  <p style={{ ...bodyText, margin: "2px 0 0 29px", fontSize: 13 }}>
                    <b style={{ color: "var(--text)" }}>Action:</b> {s.action}
                  </p>
                  <p
                    style={{
                      margin: "4px 0 0 29px",
                      fontFamily: JBMONO,
                      fontSize: 12,
                      color: "var(--text3)",
                    }}
                  >
                    {s.entities}
                  </p>
                </div>
              );
            })}
          </div>
          {(data.suggestions || []).length > 0 && (
            <div className="mt-4 flex flex-wrap items-center gap-3">
              <PillButton
                label={`Create ${
                  selectedSuggestions.length > 0 ? selectedSuggestions.length : "selected"
                } automation${selectedSuggestions.length !== 1 ? "s" : ""}`}
                icon="add_task"
                disabled={selectedSuggestions.length === 0}
                onClick={() => {
                  const selected = selectedSuggestions
                    .map((i: number) => data.suggestions[i])
                    .filter(Boolean);
                  if (selected.length > 0) {
                    onExecuteAction(
                      `Create ${selected.length} automation(s): ${selected
                        .map((s: any) => s.name)
                        .join(", ")}`,
                      // Steward owns home + tools. This used to send "bridge",
                      // a RETIRED agent id (Steward's lineage, not an agent).
                      "steward",
                      { suggestions: selected }
                    );
                  }
                }}
              />
              <PillButton
                label="Select all"
                icon="select_all"
                tone="quiet"
                onClick={() => {
                  const all = data.suggestions || [];
                  setSelectedSuggestions(all.map((_: any, i: number) => i));
                }}
              />
              {selectedSuggestions.length > 0 && (
                <PillButton
                  label="Clear"
                  icon="close"
                  tone="quiet"
                  onClick={() => setSelectedSuggestions([])}
                />
              )}
            </div>
          )}
        </div>
      )}

      {/* ── Environment Summary ────────────────────────────── */}
      {action === "environment-summary" && (
        <div>
          <Stat label="Lights" value={data.lights || ""} />
          {(data.lights_on || []).length > 0 && (
            <div className="mb-3 mt-2">
              {heading("Lights on")}
              <div className="flex flex-wrap gap-2">
                {data.lights_on.map((name: string, i: number) => (
                  <Chip key={i} color="var(--lemon)">
                    {name}
                  </Chip>
                ))}
              </div>
            </div>
          )}
          {Object.entries(data.temperatures || {}).length > 0 && (
            <div className="mt-2">
              {heading("Temperatures")}
              {Object.entries(data.temperatures).map(([name, val]: [string, any]) => (
                <Stat key={name} label={name} value={`${val}°`} />
              ))}
            </div>
          )}
          {(data.people || []).length > 0 && (
            <div className="mt-2">
              {heading("People")}
              {data.people.map((p: any, i: number) => (
                <Stat
                  key={i}
                  label={p.name}
                  value={p.state}
                  color={p.state === "home" ? "var(--mint)" : "var(--text3)"}
                />
              ))}
            </div>
          )}
          {(data.climate || []).length > 0 && (
            <div className="mt-2">
              {heading("Climate")}
              {data.climate.map((c: any, i: number) => (
                <Stat
                  key={i}
                  label={c.name}
                  value={`${c.mode} - ${c.current_temp || "?"}° to ${c.target_temp || "?"}°`}
                />
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── Discover Devices ───────────────────────────────── */}
      {action === "discover-devices" && (
        <div>
          <Stat label="Total entities" value={String(data.total_entities)} />
          <Stat
            label="Needs attention"
            value={String(data.needs_attention)}
            color={data.needs_attention > 0 ? "var(--lemon)" : "var(--mint)"}
          />
          <div className="mt-3">
            {heading("By domain")}
            <div className="grid grid-cols-2 gap-x-4">
              {Object.entries(data.by_domain || {}).map(([domain, count]: [string, any]) => (
                <div key={domain} className="flex justify-between py-0.5">
                  <span style={{ ...bodyText, fontSize: 13 }}>{domain}</span>
                  <span style={{ fontFamily: JBMONO, fontWeight: 700, fontSize: 13, color: "var(--text)" }}>
                    {count}
                  </span>
                </div>
              ))}
            </div>
          </div>
          {Object.entries(data.manufacturers || {}).length > 0 && (
            <div className="mt-3">
              {heading("Manufacturers")}
              {Object.entries(data.manufacturers).map(([mfr, count]: [string, any]) => (
                <Stat key={mfr} label={mfr} value={String(count)} />
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── List Workflows ─────────────────────────────────── */}
      {action === "list-workflows" && (
        <div>
          <div className="mb-3 grid grid-cols-3 gap-3">
            <Figure value={data.total} label="Total" />
            <Figure value={data.active} label="Active" color="var(--mint)" />
            <Figure value={data.inactive} label="Inactive" color="var(--text3)" />
          </div>
          <div className="max-h-64 overflow-y-auto">
            {(data.workflows || []).map((w: any) => (
              <Stat
                key={w.id}
                label={w.name}
                value={w.active ? "active" : "inactive"}
                color={w.active ? "var(--mint)" : "var(--text3)"}
              />
            ))}
          </div>
        </div>
      )}

      {/* ── Privacy Audit ──────────────────────────────────── */}
      {action === "privacy-audit" && (
        <div>
          <div className="mb-4 grid grid-cols-3 gap-3">
            <Figure value={data.alerts} label="Alerts" color={RED} />
            <Figure value={data.warnings} label="Warnings" color="var(--lemon)" />
            <Figure value={data.info} label="Info" color="var(--sky)" />
          </div>
          <div>
            {(data.issues || []).map((issue: string, i: number) => {
              const color = issue.startsWith("ALERT")
                ? RED
                : issue.startsWith("WARN")
                  ? "var(--lemon)"
                  : issue.startsWith("INFO")
                    ? "var(--sky)"
                    : "var(--mint)";
              return (
                <p key={i} style={{ ...bodyText, margin: "4px 0", color, fontWeight: 600 }}>
                  {issue}
                </p>
              );
            })}
          </div>
          <div className="mt-3">
            {heading("Areas checked")}
            <div className="flex flex-wrap gap-2">
              {(data.areas_checked || []).map((area: string, i: number) => (
                <Chip key={i}>{area}</Chip>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* ── Network Inventory ──────────────────────────────── */}
      {action === "network-inventory" && (
        <div>
          <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4">
            <Figure value={data.total_devices} label="Total" />
            <Figure value={data.home} label="Home" color="var(--mint)" />
            <Figure value={data.away} label="Away" color="var(--text3)" />
            <Figure value={data.unidentified} label="Unidentified" color="var(--lemon)" />
          </div>
          <div className="max-h-48 overflow-y-auto">
            {(data.devices || []).map((d: any, i: number) => (
              <Stat
                key={i}
                label={d.ip ? `${d.name} · ${d.ip}` : d.name}
                value={d.state}
                color={d.home ? "var(--mint)" : "var(--text3)"}
              />
            ))}
          </div>
        </div>
      )}

      {/* ── Exposure Check ─────────────────────────────────── */}
      {action === "check-exposure" && (
        <div>
          <div className="mb-4 flex flex-wrap items-center gap-3">
            <span style={microLabel}>Risk level</span>
            <span
              style={{
                padding: "6px 16px",
                borderRadius: 99,
                background: tintOf(
                  data.risk_level === "high"
                    ? RED
                    : data.risk_level === "medium"
                      ? "var(--lemon)"
                      : "var(--mint)",
                  16
                ),
                color:
                  data.risk_level === "high"
                    ? RED
                    : data.risk_level === "medium"
                      ? "var(--lemon)"
                      : "var(--mint)",
                fontFamily: JBMONO,
                fontWeight: 700,
                fontSize: 12,
              }}
            >
              {data.risk_level?.toUpperCase()}
            </span>
            <span style={microLabel}>{data.warnings} warning(s)</span>
          </div>
          <div>
            {Object.entries(data.checks || {}).map(([service, status]: [string, any]) => (
              <Stat
                key={service}
                label={service.replace(/_/g, " ")}
                value={String(status)}
                color={
                  String(status).includes("WARNING")
                    ? "var(--lemon)"
                    : String(status).includes("good")
                      ? "var(--mint)"
                      : "var(--text2)"
                }
              />
            ))}
          </div>
        </div>
      )}

      {/* ── Good Morning / Leaving Home / Toggle Lights ────── */}
      {["good-morning", "leaving-home", "control-lights"].includes(action) && (
        <div>
          {Object.entries(data).map(([key, val]: [string, any]) => (
            <Stat key={key} label={key.replace(/_/g, " ")} value={String(val)} />
          ))}
        </div>
      )}

      {/* ── Create Automations Result ──────────────────────── */}
      {action === "create-automations" && (
        <div>
          <div className="mb-4 grid grid-cols-3 gap-3">
            <Figure value={data.created} label="Created" color="var(--mint)" />
            <Figure value={data.failed} label="Failed" color={RED} />
            <Figure value={data.total} label="Total" />
          </div>
          {(data.details || []).map((d: any, i: number) => (
            <Stat
              key={i}
              label={d.name}
              value={`${d.status} (${d.triggers} trigger, ${d.actions} action)`}
              color="var(--mint)"
            />
          ))}
          {(data.errors || []).map((e: any, i: number) => (
            <Stat key={`err-${i}`} label={e.name} value={e.error} color={RED} />
          ))}
        </div>
      )}

      {/* ── Fix Security Result ──────────────────────────────── */}
      {action === "fix-security" && (
        <div>
          <div className="mb-4 grid grid-cols-2 gap-3">
            <Figure value={data.fixes_applied} label="Fixed" color="var(--mint)" />
            <Figure value={data.fixes_failed} label="Failed" color={RED} />
          </div>
          {data.new_security_score && (
            <div className="mb-3 text-center">
              <span style={microLabel}>New security score </span>
              <span
                style={{
                  fontFamily: FREDOKA,
                  fontWeight: 600,
                  fontSize: 22,
                  color: "var(--mint)",
                }}
              >
                {data.new_security_score}
              </span>
            </div>
          )}
          {(data.details || []).map((d: any, i: number) => (
            <Stat key={i} label={d.name} value={d.action} color="var(--mint)" />
          ))}
          {(data.errors || []).map((e: any, i: number) => (
            <Stat key={`err-${i}`} label={e.entity} value={e.error} color={RED} />
          ))}
        </div>
      )}

      {/* ── Fallback for unknown actions ────────────────────── */}
      {![
        "system-health",
        "security-audit",
        "device-analytics",
        "suggest-automations",
        "environment-summary",
        "discover-devices",
        "list-workflows",
        "privacy-audit",
        "network-inventory",
        "check-exposure",
        "good-morning",
        "leaving-home",
        "control-lights",
        "create-automations",
        "fix-security",
      ].includes(action) &&
        data && (
          <div>
            {Object.entries(data).map(([key, val]: [string, any]) => (
              <Stat
                key={key}
                label={key}
                value={typeof val === "object" ? JSON.stringify(val) : String(val)}
              />
            ))}
          </div>
        )}
    </Panel>
  );
}

/* ── The bot's jobs: Majordomo per job, Toolsmith and Drawbridge lines ── */
const BUILD_AGENT_COLOR: Record<string, string> = {
  Majordomo: "var(--lav)",
  Toolsmith: "var(--sky)",
  Drawbridge: "var(--mint)",
};

const agentTag: CSSProperties = {
  fontFamily: JBMONO,
  fontWeight: 700,
  fontSize: 11,
  letterSpacing: ".08em",
  textTransform: "uppercase",
  minWidth: 92,
};

/** What the team is doing for each job (GET /build/status, the same record
 *  and the same words as the home page): Majordomo's line per job, then
 *  Toolsmith's and Drawbridge's latest lines. Reads again every few seconds
 *  while it builds; shows nothing when the bot has no jobs. */
function BuildingNow() {
  const { status } = useBuildStatus();
  if (!status || (status.jobs.length === 0 && status.lines.length === 0)) return null;
  const lines = status.lines.filter((l) => l.agent !== "Majordomo" || status.jobs.length === 0).slice(-12);
  return (
    <Panel style={{ borderRadius: 24, padding: "16px 22px" }}>
      <span style={microLabel}>{status.running ? "Building now" : "Your bot's jobs"}</span>
      <ul style={{ margin: "10px 0 0", padding: 0, listStyle: "none", display: "grid", gap: 4 }}>
        {status.jobs.map((job) => (
          <li key={job.id} className="flex flex-wrap items-baseline gap-2" style={{ ...bodyText, fontSize: 14 }}>
            <span style={{ ...agentTag, color: BUILD_AGENT_COLOR.Majordomo }}>Majordomo</span>
            <Link href={jobHref(job.id)} style={{ color: "var(--text)", fontWeight: 700, textDecoration: "none" }}>
              {stripEmDash(job.name)}
            </Link>
            <span style={{ fontSize: 12, fontWeight: 700, color: `var(${STATUS_TONE[job.status]})` }}>
              {jobLabel(status.words, job)}
            </span>
            {job.reason && job.status !== "working" && (
              <span style={{ color: "var(--text2)" }}>{stripEmDash(job.reason)}</span>
            )}
          </li>
        ))}
        {lines.map((l, i) => (
          <li key={`${i}-${l.text}`} className="flex flex-wrap items-baseline gap-2" style={{ ...bodyText, fontSize: 14 }}>
            <span style={{ ...agentTag, color: BUILD_AGENT_COLOR[l.agent] || "var(--text3)" }}>{l.agent}</span>
            <span style={{ color: "var(--text)" }}>{stripEmDash(l.text)}</span>
          </li>
        ))}
      </ul>
    </Panel>
  );
}

/* ── Page component ───────────────────────────────────────────── */
export default function AgentsPage() {
  const [agents, setAgents] = useState<AgentStatus[]>([]);
  const [team, setTeam] = useState<TeamAgent[]>([]);
  const [bus, setBus] = useState<AgentBus | null>(null);
  const [activity, setActivity] = useState<AgentActivity[]>([]);
  const [loading, setLoading] = useState(true);
  const [taskText, setTaskText] = useState("");
  const [targetAgent, setTargetAgent] = useState("auto");
  const [priority, setPriority] = useState("medium");
  const [sending, setSending] = useState(false);
  const [taskResult, setTaskResult] = useState<any>(null);

  const refresh = useCallback(async () => {
    try {
      const [statusRes, activityRes] = await Promise.all([
        getAgentStatuses(),
        getAgentActivity(30),
      ]);
      setAgents(statusRes.agents);
      setTeam(statusRes.team ?? []);
      setBus(statusRes.bus ?? null);
      setActivity(activityRes.activity);
    } catch {
      // backend may be offline
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 5000);
    return () => clearInterval(interval);
  }, [refresh]);

  const executeTask = async (
    text: string,
    agent: string,
    prio: string,
    params?: Record<string, any>
  ) => {
    setSending(true);
    setTaskResult(null);
    try {
      const res = await sendAgentTask(text, agent, prio, params);
      setTaskResult(res.task);
      setTaskText("");
      refresh();
    } catch (err) {
      setTaskResult({
        routed_to: "system",
        status: "error",
        result: {
          action: "error",
          result: { error: err instanceof Error ? err.message : String(err) },
        },
      });
    } finally {
      setSending(false);
    }
  };

  const handleSendTask = () => {
    if (!taskText.trim() || sending) return;
    executeTask(taskText, targetAgent, priority);
  };

  const handleQuickAction = (action: (typeof QUICK_ACTIONS)[0]) => {
    setTargetAgent(action.agent);
    setTaskText(action.task);
    executeTask(action.task, action.agent, "medium");
  };

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
      {/* ── Header ─────────────────────────────────────────────── */}
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
            Your agent team
          </h1>
          <p style={{ ...bodyText, margin: "6px 0 0", fontSize: 19 }}>
            Seven specialists. Puff decides which one takes a job.
          </p>
        </div>
        <div style={{ flex: 1 }} />
        <PillButton icon="refresh" tone="quiet" title="Refresh" onClick={refresh} />
      </div>

      <BuildingNow />

      {team.length > 0 ? (
        <>
          {/* ── Bus status strip ─────────────────────────────────── */}
          {bus && <BusStrip bus={bus} />}

          {/* ── Product Team Agents ──────────────────────────────── */}
          <div className="grid grid-cols-1 gap-5 md:grid-cols-2 xl:grid-cols-3">
            {team.map((a) => (
              <TeamAgentCard key={a.name} agent={a} />
            ))}
          </div>
        </>
      ) : (
        /* ── Backend has not reported a team ──────────────────────
           Either a status read that has not landed yet, or a backend that is
           down. Previously this drew three hard-coded tiles for "Cortex",
           "Bridge" and Drawbridge: a roster retired long ago, presented as the
           truth exactly when the owner cannot check. It now names the REAL seven
           (registry.py ROSTER order) and says they are not reporting — a
           striped, dimmed card is a missing reading, not an idle agent. Any
           agent the /agents status route DID report is shown with its real
           status instead. */
        <>
          <div
            className="flex flex-wrap items-center gap-3"
            style={{
              ...panelBase,
              borderRadius: 24,
              padding: "14px 20px",
              background: STRIPES,
            }}
          >
            <MsIcon name="cloud_off" size={20} style={{ color: "var(--text3)" }} />
            <span style={{ ...microLabel, fontSize: 12 }}>
              {loading ? "Asking the box who is on duty" : "Agent bus not reporting"}
            </span>
          </div>
          <div className="grid grid-cols-1 gap-5 md:grid-cols-2 xl:grid-cols-3">
            {ROSTER_IDS.map((id) => {
              const reported = agents.find((a) => a.agent === id);
              const meta = AGENT_META[id];
              return (
                <Panel
                  key={id}
                  style={{
                    position: "relative",
                    overflow: "hidden",
                    borderRadius: 26,
                    opacity: reported ? 1 : 0.62,
                  }}
                >
                  <div
                    style={{
                      position: "absolute",
                      top: 0,
                      left: 0,
                      right: 0,
                      height: 3,
                      background: `linear-gradient(90deg, ${meta.color}, transparent)`,
                    }}
                  />
                  <div className="mb-3 flex items-start justify-between gap-3">
                    <div className="flex min-w-0 items-center gap-3">
                      <span
                        className="flex flex-none items-center justify-center"
                        style={{
                          width: 42,
                          height: 42,
                          borderRadius: 14,
                          background: tintOf(meta.color, 16),
                          color: meta.color,
                        }}
                      >
                        <MsIcon name={meta.icon} size={22} />
                      </span>
                      <div className="min-w-0">
                        <div
                          style={{
                            fontFamily: FREDOKA,
                            fontWeight: 600,
                            fontSize: 20,
                            lineHeight: 1.15,
                            color: "var(--text)",
                          }}
                        >
                          {meta.label}
                        </div>
                        <div style={microLabel}>{meta.role}</div>
                      </div>
                    </div>
                    {reported ? (
                      <StatusBadge status={reported.status} />
                    ) : (
                      <span
                        className="flex-none"
                        style={{
                          padding: "5px 12px",
                          borderRadius: 99,
                          background: "var(--chip)",
                          color: "var(--text3)",
                          fontFamily: JBMONO,
                          fontWeight: 700,
                          fontSize: 11,
                          letterSpacing: ".06em",
                          textTransform: "uppercase",
                        }}
                      >
                        no reading
                      </span>
                    )}
                  </div>
                  {reported?.task ? (
                    <p className="truncate" style={{ ...bodyText, margin: 0 }}>
                      {reported.task}
                    </p>
                  ) : (
                    <div style={{ height: 34, borderRadius: 14, background: STRIPES }} />
                  )}
                  <div style={{ ...microLabel, fontSize: 10, marginTop: 10 }}>
                    {reported
                      ? `Last active ${timeAgo(reported.timestamp)}`
                      : "This agent has not checked in"}
                  </div>
                </Panel>
              );
            })}
          </div>
        </>
      )}

      {/* ── Task Input ─────────────────────────────────────────── */}
      <Panel>
        <SubHeading>Send a task</SubHeading>
        <div className="flex flex-col gap-3 sm:flex-row">
          <input
            /* Same legacy trap as the knowledge search: main.css forces
               `.dark input[type="text"]` to a slate box, which reads as a
               navy slab on the day palette. kb-viewer-search is the themed
               glass field (css/_theme.css). */
            type="text"
            className="kb-viewer-search flex-1 text-sm"
            placeholder="Describe a job for the team…"
            value={taskText}
            onChange={(e) => setTaskText(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleSendTask()}
          />
          <select
            style={fieldStyle}
            value={targetAgent}
            onChange={(e) => setTargetAgent(e.target.value)}
          >
            <option value="auto">Let Puff choose</option>
            {(team.length > 0
              ? team
                  .filter((t) => t.name !== "chat") // the chat is not a task target
                  .map((t) => ({ value: t.name, label: t.display_name || t.name }))
              : /* The full seven, not a three-name subset — and never the retired
                   ids, which POST /agents/task would not recognise anyway. */
                ROSTER_IDS.map((id) => ({ value: id, label: AGENT_META[id].label }))
            ).map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
          <select
            style={fieldStyle}
            value={priority}
            onChange={(e) => setPriority(e.target.value)}
          >
            <option value="low">Low</option>
            <option value="medium">Medium</option>
            <option value="high">High</option>
          </select>
          <PillButton
            icon="send"
            label={sending ? "Running…" : "Send"}
            onClick={handleSendTask}
            disabled={!taskText.trim() || sending}
          />
        </div>
      </Panel>

      {/* ── Result Panel ───────────────────────────────────────── */}
      <ResultPanel
        taskResult={taskResult}
        onClose={() => setTaskResult(null)}
        onExecuteAction={(task, agent, params) => executeTask(task, agent, "medium", params)}
      />

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
        {/* ── Activity Feed ──────────────────────────────────── */}
        <div className="lg:col-span-2">
          <Panel>
            <SubHeading>What just happened</SubHeading>
            <div className="flex max-h-96 flex-col gap-2 overflow-y-auto">
              {activity.length === 0 && !loading && (
                <div
                  className="flex flex-col items-center gap-2 py-8 text-center"
                  style={{ borderRadius: 18, background: STRIPES }}
                >
                  <MsIcon name="history" size={30} style={{ color: "var(--text3)" }} />
                  <span style={{ ...microLabel, fontSize: 12 }}>
                    Nothing yet. Send a task
                  </span>
                </div>
              )}
              {activity.map((item, i) => {
                const m = agentMetaFor(item.agent);
                const ev = EVENT_META[item.event] || {
                  label: (item.event || "").replace(/_/g, " "),
                  color: "var(--text2)",
                };
                return (
                  <div
                    key={`${item.timestamp}-${i}`}
                    style={{
                      borderLeft: `3px solid ${m.color}`,
                      paddingLeft: 12,
                      paddingTop: 7,
                      paddingBottom: 7,
                    }}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <div className="flex min-w-0 items-center gap-2">
                        <span
                          className="truncate"
                          style={{
                            fontFamily: NUNITO,
                            fontWeight: 700,
                            fontSize: 14,
                            color: "var(--text)",
                          }}
                        >
                          {agentLabel(item.agent, team)}
                        </span>
                        <span
                          className="flex-none"
                          style={{
                            padding: "2px 9px",
                            borderRadius: 99,
                            background: "var(--chip)",
                            color: ev.color,
                            fontFamily: JBMONO,
                            fontWeight: 700,
                            fontSize: 10,
                          }}
                        >
                          {ev.label}
                        </span>
                      </div>
                      <span className="flex-none" style={{ ...microLabel, fontSize: 10 }}>
                        {timeAgo(item.timestamp)}
                      </span>
                    </div>
                    {item.task && (
                      <p style={{ ...bodyText, margin: "2px 0 0", fontSize: 14 }}>
                        {item.task}
                      </p>
                    )}
                  </div>
                );
              })}
            </div>
          </Panel>
        </div>

        {/* ── Quick Actions ──────────────────────────────────── */}
        <div>
          <Panel>
            <SubHeading>Quick jobs</SubHeading>
            <div className="flex max-h-96 flex-col gap-1 overflow-y-auto">
              {QUICK_ACTIONS.map((action) => {
                const color = AGENT_META[action.agent]?.color || "var(--text3)";
                return (
                  <button
                    key={action.label}
                    type="button"
                    className="flex w-full cursor-pointer items-center gap-3 text-left transition-transform hover:-translate-y-0.5 disabled:cursor-default disabled:opacity-50"
                    style={{
                      padding: "10px 14px",
                      borderRadius: 16,
                      background: "var(--card2)",
                      border: "1px solid var(--line)",
                    }}
                    onClick={() => handleQuickAction(action)}
                    disabled={sending}
                  >
                    <MsIcon name={action.icon} size={19} style={{ color, flex: "none" }} />
                    <span
                      className="min-w-0 flex-1 truncate"
                      style={{
                        fontFamily: NUNITO,
                        fontWeight: 700,
                        fontSize: 14,
                        color: "var(--text)",
                      }}
                    >
                      {action.label}
                    </span>
                    <span
                      className="flex-none"
                      style={{ fontFamily: JBMONO, fontWeight: 700, fontSize: 11, color }}
                    >
                      {agentLabel(action.agent, team)}
                    </span>
                  </button>
                );
              })}
            </div>
          </Panel>
        </div>
      </div>
    </section>
  );
}
