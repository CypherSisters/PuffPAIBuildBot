// Status → message mapping for the first-run setup check.
//
// WHY THIS EXISTS: the wizard used to treat every non-502 reply as a usable
// SetupStatus. A 401 (the dashboard's PUFFPAI_API_KEY not matching the
// backend's) therefore came back as `{ detail: "..." }`, the wizard read
// `data.ollama.selected` off it, the initial-load effect threw, and the
// "Checking your setup…" splash stayed up forever with nothing on screen to
// explain it. Every reply now maps to either a status or a named problem, so
// there is no path that leaves the spinner running.
//
// Pure functions only — no React, no fetch — so they can be unit tested.

export type SetupProblemKind = "backend-down" | "auth" | "bad-status";

export interface SetupProblem {
  kind: SetupProblemKind;
  /** Heading. */
  title: string;
  /** One paragraph of plain explanation. */
  body: string;
  /** Commands to run, in order. Rendered verbatim; keep them shell-neutral. */
  commands: string[];
}

/** Shaped like setupService's Reply, minus the generic, so this module does not
 *  depend on the service (and the service can keep depending on nothing). */
export interface StatusReply {
  ok: boolean;
  status: number;
  data: unknown;
}

export const BACKEND_DOWN: SetupProblem = {
  kind: "backend-down",
  title: "The PuffPAI backend isn’t running",
  body: "The dashboard can’t reach the local API. Start it in a terminal, then retry:",
  commands: ["puffpai serve"],
};

function authProblem(status: number): SetupProblem {
  return {
    kind: "auth",
    title: "The dashboard’s key doesn’t match the backend",
    body:
      `The backend refused the dashboard’s API key (HTTP ${status}). Print the ` +
      "key the backend is actually using, put that value in dashboard/.env.local " +
      "as PUFFPAI_API_KEY=…, then restart the dashboard:",
    commands: ["puffpai apikey", "npm start"],
  };
}

/** True when the reply means the backend itself is unreachable: the proxy 502s /
 *  504s, or the fetch failed or timed out (status 0). */
export function isBackendDown(reply: StatusReply): boolean {
  return reply.status === 0 || reply.status === 502 || reply.status === 504;
}

/** A SetupStatus is only usable if the shape the wizard reads off it is there.
 *  Anything else (an error envelope, HTML from a stray proxy, a truncated JSON
 *  body) is a problem, not a status. */
export function isUsableStatus(data: unknown): boolean {
  if (!data || typeof data !== "object") return false;
  const d = data as Record<string, unknown>;
  return (
    typeof d.complete === "boolean" &&
    !!d.ollama &&
    typeof d.ollama === "object" &&
    !!d.vault &&
    typeof d.vault === "object" &&
    !!d.ha &&
    typeof d.ha === "object" &&
    !!d.knowledge &&
    typeof d.knowledge === "object"
  );
}

/**
 * Classify a /setup/status reply. Returns null when the reply carries a usable
 * SetupStatus; otherwise the problem to render, always with a Retry affordance.
 */
export function classifySetupReply(reply: StatusReply): SetupProblem | null {
  if (isBackendDown(reply)) return BACKEND_DOWN;
  if (reply.status === 401 || reply.status === 403) return authProblem(reply.status);
  if (!reply.ok) {
    return {
      kind: "bad-status",
      title: `The backend answered HTTP ${reply.status}`,
      body:
        "That isn’t a status the wizard can act on. Check the backend’s log " +
        "(the terminal running `puffpai serve`), then retry.",
      commands: [],
    };
  }
  if (!isUsableStatus(reply.data)) {
    return {
      kind: "bad-status",
      title: "The backend’s reply wasn’t a setup status",
      body:
        `The request succeeded (HTTP ${reply.status}) but the body wasn’t the ` +
        "setup status the wizard expects. Something other than PuffPAI may be " +
        "answering on the API port. Check the backend’s log, then retry.",
      commands: [],
    };
  }
  return null;
}
