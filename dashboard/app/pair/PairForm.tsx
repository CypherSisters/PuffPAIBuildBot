"use client";

/**
 * The pairing screen. Two states, decided by GET /api/pair/status:
 *
 *   not paired -> type the 8-character code from the box, name the device, pair
 *   paired     -> show the current code (so this device can add another), the
 *                 list of paired devices, and a rotate button
 *
 * WHAT THIS COMPONENT NEVER HAS
 * -----------------------------
 * The cookie is HttpOnly, so this page cannot read it; the HMAC secret never
 * leaves the server; and the pairing code is fetched from /api/pair/code, which
 * answers only an already-paired caller. Nothing here is baked into the bundle
 * at build time -- there is no NEXT_PUBLIC_ read anywhere in this file, which
 * is what put the backend key in every browser once before.
 *
 * There is no password field, here or anywhere else in this dashboard. The code
 * is a one-time bearer credential with a one-hour life, not an account secret,
 * so it is typed once and never stored on the client.
 */

import { useCallback, useEffect, useState } from "react";
import { FRESH_CODE_SHORT, FRESH_CODE_WAYS, whereLines } from "./pairWords";

interface PairedDevice {
  id: string;
  name: string;
  createdAt: string;
  lastSeenAt: string | null;
}

type Phase = "checking" | "unpaired" | "paired";

/** "7xk4p2m9" -> "7XK4-P2M9" as the user types. Display only; the server
 *  normalises again and is the only thing that decides what a code is. */
function formatAsTyped(raw: string): string {
  const clean = raw.toUpperCase().replace(/[^0-9A-Z]/g, "").slice(0, 8);
  return clean.length > 4 ? `${clean.slice(0, 4)}-${clean.slice(4)}` : clean;
}

function when(iso: string | null): string {
  if (!iso) return "never";
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return "unknown";
  return new Date(t).toLocaleString();
}

type Props = {
  /** The address the proxy forwards to, from the server component. Shown so the
   *  owner can see WHICH bot this device is being paired to before typing a
   *  code -- on a machine running two builds, the port is the only difference
   *  visible from here. */
  backendUrl?: string;
  /** False when PUFFPAI_BACKEND_URL was not set and the fallback is in use. */
  backendConfigured?: boolean;
};

export default function PairForm({ backendUrl, backendConfigured = false }: Props) {
  const [phase, setPhase] = useState<Phase>("checking");
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [cooldown, setCooldown] = useState(0);

  const [currentCode, setCurrentCode] = useState<string | null>(null);
  const [expiresAt, setExpiresAt] = useState<string | null>(null);
  const [devices, setDevices] = useState<PairedDevice[]>([]);
  const [self, setSelf] = useState<string | null>(null);
  // The address in this browser's address bar. Read after hydration (window
  // does not exist during SSR), so the first paint matches the server.
  const [pageOrigin, setPageOrigin] = useState<string | null>(null);

  const loadPaired = useCallback(async () => {
    try {
      const [codeRes, devRes] = await Promise.all([
        fetch("/api/pair/code", { cache: "no-store" }),
        fetch("/api/pair/devices", { cache: "no-store" }),
      ]);
      if (codeRes.ok) {
        const d = (await codeRes.json()) as {
          code?: string;
          expiresAt?: string;
        };
        setCurrentCode(d?.code ?? null);
        setExpiresAt(d?.expiresAt ?? null);
      }
      if (devRes.ok) {
        const d = (await devRes.json()) as {
          devices?: PairedDevice[];
          self?: string | null;
        };
        setDevices(Array.isArray(d?.devices) ? d.devices : []);
        setSelf(d?.self ?? null);
      }
    } catch {
      /* a display failure is not a security failure; leave the panel empty */
    }
  }, []);

  useEffect(() => {
    let live = true;
    fetch("/api/pair/status", { cache: "no-store" })
      .then((r) => r.json())
      .then((d: { paired?: boolean }) => {
        if (!live) return;
        setPhase(d?.paired ? "paired" : "unpaired");
        if (d?.paired) void loadPaired();
      })
      .catch(() => {
        // Fail toward showing the form: a device that cannot ask is a device
        // that has not been let in.
        if (live) setPhase("unpaired");
      });
    return () => {
      live = false;
    };
  }, [loadPaired]);

  useEffect(() => {
    setPageOrigin(typeof window === "undefined" ? null : window.location.origin);
  }, []);

  useEffect(() => {
    if (cooldown <= 0) return;
    const t = setTimeout(() => setCooldown((s) => s - 1), 1000);
    return () => clearTimeout(t);
  }, [cooldown]);

  const submit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      if (busy || cooldown > 0 || !code.trim()) return;
      setBusy(true);
      setError(null);
      try {
        const res = await fetch("/api/pair", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ code, name }),
        });
        if (res.ok) {
          setCode("");
          setPhase("paired");
          void loadPaired();
          // Land where they were trying to go. A full navigation, not a client
          // route change, so the new cookie is on the very first request.
          const next = new URLSearchParams(window.location.search).get("next");
          window.location.href = next && next.startsWith("/") && !next.startsWith("//")
            ? next
            : "/dashboard";
          return;
        }
        if (res.status === 429) {
          const wait = Number(res.headers.get("Retry-After")) || 60;
          setCooldown(wait);
          setError(
            `Too many wrong codes. Try again in ${wait} seconds, or ${FRESH_CODE_SHORT}.`,
          );
        } else if (res.status === 403) {
          setError("That request did not come from this page. Reload and try again.");
        } else if (res.status === 500) {
          setError("Pairing is unavailable -- the appliance could not read its pairing file.");
        } else {
          setError("That code was not right, or it has expired. Codes last one hour.");
        }
      } catch {
        setError("Could not reach the dashboard. Is it still running?");
      } finally {
        setBusy(false);
      }
    },
    [busy, code, cooldown, loadPaired, name],
  );

  const rotate = useCallback(async () => {
    setBusy(true);
    try {
      const res = await fetch("/api/pair/rotate", { method: "POST" });
      if (res.ok) {
        const d = (await res.json()) as { code?: string; expiresAt?: string };
        setCurrentCode(d?.code ?? null);
        setExpiresAt(d?.expiresAt ?? null);
      }
    } catch {
      /* leave the old code on screen rather than blanking it */
    } finally {
      setBusy(false);
    }
  }, []);

  const revoke = useCallback(
    async (id: string) => {
      setBusy(true);
      try {
        const res = await fetch("/api/pair/devices/revoke", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ id }),
        });
        if (res.ok && id === self) {
          // Revoked itself. Its cookie is already cleared and already dead.
          window.location.href = "/pair";
          return;
        }
        await loadPaired();
      } catch {
        /* the list refresh below is the feedback; nothing else to say */
      } finally {
        setBusy(false);
      }
    },
    [loadPaired, self],
  );

  const where = whereLines(pageOrigin, backendUrl, backendConfigured);

  const shell = (children: React.ReactNode) => (
    <main className="flex min-h-screen items-center justify-center p-6">
      <div className="w-full max-w-lg rounded-2xl border border-(--line) bg-(--puff-surface-deep) p-8 shadow-2xl backdrop-blur">
        {children}
        <div className="mt-6 border-t border-(--line) pt-3 text-xs text-(--text2)">
          {where.page && <p className="font-mono">{where.page}</p>}
          <p className="font-mono">{where.bot}</p>
          {where.botNote && <p className="mt-1">{where.botNote}</p>}
        </div>
      </div>
    </main>
  );

  if (phase === "checking") {
    return shell(<p className="text-sm text-(--text2)">Checking this device...</p>);
  }

  if (phase === "paired") {
    return shell(
      <>
        <h1 className="mb-1 text-xl font-semibold text-(--puff-text-strong)">This device is paired</h1>
        <p className="mb-6 text-sm text-(--text2)">
          To add another phone or laptop, open this page on it and type the code
          below.
        </p>

        <div className="mb-6 rounded-xl border border-(--line) bg-(--chip) p-4">
          <div className="font-mono text-3xl tracking-widest text-[#00B4D8]">
            {currentCode ?? "----.----"}
          </div>
          <p className="mt-2 text-xs text-(--text2)">
            {expiresAt ? `Expires ${when(expiresAt)}.` : "Expires one hour after it is issued."}{" "}
            A new one is issued automatically when this one runs out.
          </p>
          <button
            type="button"
            onClick={rotate}
            disabled={busy}
            className="mt-3 rounded-lg border border-(--line) px-3 py-1.5 text-xs text-(--text) hover:bg-(--chip) disabled:opacity-50"
          >
            Issue a new code now
          </button>
        </div>

        <h2 className="mb-2 text-sm font-semibold text-(--puff-text-strong)">Paired devices</h2>
        <ul className="mb-6 space-y-2">
          {devices.length === 0 && (
            <li className="text-xs text-(--text2)">No devices listed.</li>
          )}
          {devices.map((d) => (
            <li
              key={d.id}
              className="flex items-center justify-between rounded-lg border border-(--line) px-3 py-2"
            >
              <span className="text-sm text-(--text)">
                {d.name || "Unnamed device"}
                {d.id === self && (
                  <span className="ml-2 text-xs text-[#00B4D8]">this device</span>
                )}
                <span className="block text-xs text-(--text2)">
                  last seen {when(d.lastSeenAt)}
                </span>
              </span>
              <button
                type="button"
                onClick={() => revoke(d.id)}
                disabled={busy}
                className="rounded-lg border border-(--puff-red)/40 px-2.5 py-1 text-xs text-(--puff-red) hover:bg-(--puff-red)/10 disabled:opacity-50"
              >
                Revoke
              </button>
            </li>
          ))}
        </ul>

        <a href="/dashboard" className="text-sm text-[#00B4D8] hover:underline">
          Back to the dashboard
        </a>
      </>,
    );
  }

  return shell(
    <>
      <h1 className="mb-1 text-xl font-semibold text-(--puff-text-strong)">Pair this device</h1>
      <div className="mb-6 text-sm text-(--text2)">
        <p>
          This page only answers phones and laptops you have let in. Type the
          pairing code from the computer running your bot. To get one:
        </p>
        <ul className="mt-2 space-y-1">
          {FRESH_CODE_WAYS.map((way) => (
            <li key={way} className="rounded-lg bg-(--chip) px-3 py-2 text-xs text-(--text)">
              {way}
            </li>
          ))}
        </ul>
      </div>

      <form onSubmit={submit}>
        <label htmlFor="pair-code" className="mb-1 block text-xs text-(--text2)">
          Pairing code
        </label>
        <input
          id="pair-code"
          name="code"
          value={code}
          onChange={(e) => setCode(formatAsTyped(e.target.value))}
          autoComplete="off"
          autoCapitalize="characters"
          spellCheck={false}
          inputMode="text"
          placeholder="XXXX-XXXX"
          className="mb-4 w-full rounded-lg border border-(--line) bg-(--chip) px-3 py-2 font-mono text-lg tracking-widest text-(--puff-text-strong) outline-none focus:border-[#00B4D8]"
        />

        <label htmlFor="pair-name" className="mb-1 block text-xs text-(--text2)">
          Name this device (optional)
        </label>
        <input
          id="pair-name"
          name="name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          maxLength={64}
          autoComplete="off"
          placeholder="kitchen tablet"
          className="mb-4 w-full rounded-lg border border-(--line) bg-(--chip) px-3 py-2 text-sm text-(--puff-text-strong) outline-none focus:border-[#00B4D8]"
        />

        {error && (
          <p className="mb-4 rounded-lg border border-(--puff-red)/30 bg-(--puff-red)/10 px-3 py-2 text-xs text-(--puff-red)">
            {error}
          </p>
        )}

        <button
          type="submit"
          disabled={busy || cooldown > 0 || !code.trim()}
          className="w-full rounded-lg bg-[#00B4D8] px-4 py-2.5 font-semibold text-(--puff-on-accent) disabled:opacity-40"
        >
          {cooldown > 0 ? `Wait ${cooldown}s` : busy ? "Pairing..." : "Pair"}
        </button>
      </form>

      <p className="mt-6 text-xs text-(--text2)">
        Codes are good for one hour and for as many devices as you pair in that
        hour. A paired device stays paired for a year, and you can revoke any of
        them from this page afterwards.
      </p>
    </>,
  );
}
