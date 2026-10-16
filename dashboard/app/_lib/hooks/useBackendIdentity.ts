"use client";

/**
 * "Which bot is this dashboard driving?"
 *
 * Two facts, from two places, because neither half knows the other:
 *
 *   - the ADDRESS is server-side only (PUFFPAI_BACKEND_URL, read by the proxy),
 *     so it comes from /api/backend;
 *   - the NAME and the build stamp come from the backend itself, through the
 *     keyed proxy, so they are what THAT box says about itself.
 *
 * Why this exists: the proxy's default backend is http://localhost:8000, which
 * is also where a household's live PuffPAI listens. A dashboard pointed at the
 * wrong one looks completely normal - real data, real answers, the wrong house.
 * Naming the backend on screen is the cheap fix, and it is honest about not
 * knowing: an unreachable backend reports `reachable: false` rather than
 * borrowing the last name it saw.
 */

import { useEffect, useState } from "react";
import { getBuildSelf } from "../services/buildService";

export type BackendIdentity = {
  /** The address the proxy forwards to, or null until known. */
  url: string | null;
  /** True when PUFFPAI_BACKEND_URL was actually set; false means the default. */
  configured: boolean;
  /** The bot's own name from its manifest. Empty on an unconfigured build. */
  name: string;
  /** What this build is based on, e.g. "buildbot". */
  basedOn: string;
  /** The manifest's generation stamp - this build's id, in date form. */
  builtAt: number | null;
  /** False when /build/self did not answer at all. */
  reachable: boolean;
  loading: boolean;
};

const EMPTY: BackendIdentity = {
  url: null,
  configured: false,
  name: "",
  basedOn: "",
  builtAt: null,
  reachable: false,
  loading: true,
};

export function useBackendIdentity(): BackendIdentity {
  const [state, setState] = useState<BackendIdentity>(EMPTY);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      let url: string | null = null;
      let configured = false;
      try {
        const res = await fetch("/api/backend", { cache: "no-store" });
        if (res.ok) {
          const data = (await res.json()) as { url?: string; configured?: boolean };
          url = typeof data.url === "string" ? data.url : null;
          configured = Boolean(data.configured);
        }
      } catch {
        /* the address is a nicety; the name below is the load-bearing part */
      }
      const build = await getBuildSelf();
      if (cancelled) return;
      setState({
        url,
        configured,
        name: build?.build?.name ?? "",
        basedOn: build?.build?.based_on ?? "",
        builtAt: typeof build?.generated_at === "number" ? build.generated_at : null,
        reachable: build !== null,
        loading: false,
      });
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return state;
}

/** One short line for a header or a footer: "HomeBot at 127.0.0.1:8000".
 *
 *  The host and port are what distinguish two bots on one machine, so they are
 *  always shown; the scheme is not, because it is always http on a loopback
 *  appliance and it costs characters a phone header does not have. */
export function backendLabel(id: BackendIdentity): string {
  const where = id.url ? id.url.replace(/^https?:\/\//, "") : "unknown address";
  if (id.loading) return "checking...";
  if (!id.reachable) return `no answer from ${where}`;
  const who = id.name || "unnamed build";
  return `${who} at ${where}`;
}
