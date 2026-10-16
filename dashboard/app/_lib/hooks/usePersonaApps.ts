"use client";

/**
 * The apps the OWNER made — one fetch, shared by the rail and the home board.
 *
 * A separate call from /build/self on purpose: that endpoint serves an app's
 * id, name, blurb, route, icon and surfaces, and deliberately not its
 * vocabulary, and it cannot say which apps came from the owner's own
 * directory. GET /persona/list answers both (`is_user_persona` decides it on
 * disk), which is what a persona rail entry and a persona card both need.
 *
 * `null` means NOT KNOWN — the fetch has not landed, or the backend is
 * unreachable — and every reader treats it as "show nothing extra". That is
 * the safe direction here, and the opposite of the surface gates: inventing a
 * rail entry for a bot that may not exist is worse than the entry arriving a
 * beat late. An empty array is a real answer: this build has no persona.
 *
 * Refetched when this browser installs or deletes one, so the rail does not
 * need a reload to show what was just made.
 *
 * Copyright (c) Cypher Sisters. Apache-2.0 License.
 */

import { useEffect, useState } from "react";
import { onBuildChanged } from "../services/buildService";
import { listPersonas, type OwnerApp } from "../services/personaService";

export function usePersonaApps(): { apps: OwnerApp[] | null } {
  const [apps, setApps] = useState<OwnerApp[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = () => {
      listPersonas()
        .then((reply) => {
          if (!cancelled) setApps(reply.apps || []);
        })
        .catch(() => {
          if (!cancelled) setApps([]);
        });
    };
    load();
    const off = onBuildChanged(load);
    return () => {
      cancelled = true;
      off();
    };
  }, []);

  return { apps };
}
