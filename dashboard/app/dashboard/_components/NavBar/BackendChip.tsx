"use client";

/**
 * The header chip that says WHICH BOT this dashboard is driving.
 *
 * The proxy's backend defaults to http://localhost:8000, which is also where a
 * household's existing PuffPAI listens. Point a second dashboard at the default
 * on that machine and every page looks right while driving the wrong box. This
 * chip is the at-a-glance answer: the bot's own name from its manifest, and the
 * host:port the proxy forwards to.
 *
 * It states what it does not know. An unreachable backend reads "no answer from
 * ...", never the last name it saw; an unconfigured build has no name, so it
 * says "unnamed build" rather than inventing one.
 *
 * Fetched once per mount, like /build/self elsewhere: the address cannot change
 * without restarting the dashboard, and the name changes when the owner runs
 * the wizard.
 */

import MsIcon from "../../../_components/MsIcon";
import {
  backendLabel,
  useBackendIdentity,
} from "../../../_lib/hooks/useBackendIdentity";

export default function BackendChip() {
  const identity = useBackendIdentity();
  const label = backendLabel(identity);
  const bad = !identity.loading && !identity.reachable;
  const stamp = identity.builtAt
    ? new Date(identity.builtAt * 1000).toLocaleDateString()
    : null;

  return (
    <div
      className="hidden flex-none items-center xl:flex"
      title={
        [
          identity.url ? `Backend: ${identity.url}` : "Backend address unknown",
          identity.configured ? null : "PUFFPAI_BACKEND_URL is not set, so this is the default.",
          identity.name ? `Bot: ${identity.name}` : "This build has no manifest yet.",
          identity.basedOn ? `Based on: ${identity.basedOn}` : null,
          stamp ? `Build: ${stamp}` : null,
        ]
          .filter(Boolean)
          .join("\n")
      }
      style={{
        gap: 8,
        height: 52,
        padding: "0 16px",
        borderRadius: 16,
        border: "1px solid var(--line)",
        background: "var(--card)",
        maxWidth: 260,
      }}
    >
      <MsIcon name={bad ? "cloud_off" : "smart_toy"} size={20} />
      <span className="min-w-0" style={{ lineHeight: 1.1 }}>
        <span
          className="block truncate"
          style={{
            font: "700 13px var(--font-nunito), sans-serif",
            color: bad ? "var(--lemon)" : "var(--text)",
          }}
        >
          {label}
        </span>
        <span
          className="block"
          style={{
            font: "700 10px var(--font-jbmono), monospace",
            letterSpacing: ".12em",
            color: "var(--text3)",
          }}
        >
          {stamp ? `BUILT ${stamp}` : "BACKEND"}
        </span>
      </span>
    </div>
  );
}
