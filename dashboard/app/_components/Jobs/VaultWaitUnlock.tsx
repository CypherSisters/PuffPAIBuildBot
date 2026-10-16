"use client";

/**
 * "Unlock your vault to let Toolsmith go on." with a PIN box and Unlock,
 * right where she is: the wizard's Building screen, its last screen (the
 * build's progress), the bot page and a job's page. Shown only while the
 * backend says Toolsmith waits for the vault (`vault_wait` on GET
 * /build/status and GET /wizard/build).
 *
 * Her PIN goes once to the existing POST /vault/unlock (the same route the
 * Vault page and the Toolsmith card use) and is cleared from this box at
 * once; it is never stored, logged or shown. Opening the vault makes
 * Toolsmith's key ready and starts again any job left waiting for it
 * (security/toolsmith_cloud.py on_vault_opened); `onOpened` lets the page
 * read its status again.
 *
 * Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
 */

import { useEffect, useRef, useState } from "react";
import { apiPostRaw } from "@/app/_lib/api";

const NUNITO = "var(--font-nunito), sans-serif";

const box: React.CSSProperties = {
  border: "1px solid var(--line)",
  borderRadius: 18,
  background: "var(--card)",
  padding: "10px 16px",
  fontFamily: NUNITO,
  fontSize: 14,
  color: "var(--text)",
};

const button: React.CSSProperties = {
  padding: "6px 16px",
  borderRadius: 999,
  border: "2px solid var(--accent, #7c5cff)",
  background: "var(--card2)",
  fontFamily: NUNITO,
  fontWeight: 700,
  fontSize: 14,
  color: "var(--text)",
  cursor: "pointer",
  whiteSpace: "nowrap",
};

export default function VaultWaitUnlock({
  line,
  onOpened,
}: {
  /** The backend's own line; nothing is drawn when it is empty. */
  line?: string;
  onOpened?: () => void | Promise<void>;
}) {
  const [pin, setPin] = useState("");
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState("");
  const [opened, setOpened] = useState(false);
  // After her unlock the line clears; if it comes back later (the vault is
  // needed again), the PIN box is shown again.
  const cleared = useRef(false);
  useEffect(() => {
    if (!opened) return;
    if (!line) {
      cleared.current = true;
    } else if (cleared.current) {
      cleared.current = false;
      setOpened(false);
      setSaid("");
    }
  }, [line, opened]);

  if (!line && !opened) return null;

  async function unlock() {
    const p = pin;
    setPin("");
    if (!p || busy) return;
    setBusy(true);
    let ok = false;
    let words = "";
    try {
      const r = await apiPostRaw<{ success?: boolean; message?: string }>("/vault/unlock", { pin: p });
      ok = r.ok && !!r.data?.success;
      words = r.data?.message || "";
    } catch {
      words = "BuildBot could not be reached.";
    }
    setBusy(false);
    if (!ok) {
      setSaid(words || "That PIN did not open your vault. Try again.");
      return;
    }
    setOpened(true);
    setSaid("Your vault is open. Toolsmith goes on.");
    await onOpened?.();
  }

  return (
    <section aria-label="Unlock your vault" style={box} data-testid="vault-wait-unlock">
      {opened ? (
        <p role="status" style={{ margin: 0 }}>
          {said}
        </p>
      ) : (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void unlock();
          }}
          style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}
        >
          <span style={{ flex: "1 1 220px" }}>{line}</span>
          <input
            type="password"
            aria-label="Your vault PIN"
            placeholder="Vault PIN"
            autoComplete="off"
            spellCheck={false}
            value={pin}
            onChange={(e) => setPin(e.target.value)}
            disabled={busy}
            style={{ padding: "6px 10px", borderRadius: 10, minWidth: 140 }}
          />
          <button type="submit" style={button} disabled={busy || !pin}>
            Unlock
          </button>
          {said ? (
            <p role="status" style={{ margin: 0, flexBasis: "100%", color: "var(--text2)" }}>
              {said}
            </p>
          ) : null}
        </form>
      )}
    </section>
  );
}
