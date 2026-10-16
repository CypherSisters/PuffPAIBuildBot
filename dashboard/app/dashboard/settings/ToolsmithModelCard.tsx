"use client";

/**
 * Toolsmith's model: "On this computer (private, slower)" or "Anthropic
 * (faster, your choice)". Toolsmith is the only agent that may use a cloud
 * model; everything else in BuildBot stays on this computer.
 *
 * In the wizard (`inWizard`) it is drawn without its title, frame and the two
 * choices (the wizard asks those itself) and opens on Anthropic; `onChosen`
 * hears each saved choice.
 *
 * The consent text comes from the server (the same sentence her yes is
 * recorded against). The key box sends the key once, in a POST body; it is
 * cleared right after and never shown again. Turning it off is one click.
 *
 * A locked vault (423 "vault_locked") shows a PIN box right here: her PIN
 * opens the vault through POST /vault/unlock, then the same save is tried
 * again. Until then the pasted key waits only in this card's memory (a ref,
 * never stored, never shown) and is dropped on Cancel, on the retry, or when
 * the card closes. Every other refusal shows the server's own words, and
 * `onRefused` hears it (the wizard keeps her wish for Anthropic).
 *
 * Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { apiPostRaw } from "@/app/_lib/api";
import {
  chooseKeep,
  forgetKey,
  getToolsmithModel,
  chooseAnthropic,
  chooseLocal,
  type ToolsmithModel,
} from "@/app/_lib/services/toolsmithModelService";

const FREDOKA = "var(--font-fredoka), sans-serif";
const NUNITO = "var(--font-nunito), sans-serif";

const cardStyle: React.CSSProperties = {
  borderRadius: 30,
  border: "1px solid var(--line)",
  background: "var(--card)",
  backdropFilter: "blur(18px)",
  WebkitBackdropFilter: "blur(18px)",
  padding: "26px 28px",
  boxShadow: "var(--shadow)",
};

const text: React.CSSProperties = {
  fontFamily: NUNITO,
  fontWeight: 500,
  fontSize: 15,
  lineHeight: 1.5,
  color: "var(--text2)",
};

const button = (on: boolean): React.CSSProperties => ({
  padding: "10px 16px",
  borderRadius: 16,
  border: on ? "2px solid var(--accent, #7c5cff)" : "1px solid var(--line)",
  background: on ? "var(--card2)" : "transparent",
  fontFamily: NUNITO,
  fontWeight: 600,
  fontSize: 15,
  color: "var(--text)",
  cursor: "pointer",
});

const VAULT_LOCKED = "Your vault is locked. Type your vault PIN to save the key.";

export default function ToolsmithModelCard({
  inWizard = false,
  onChosen,
  onRefused,
}: {
  inWizard?: boolean;
  onChosen?: (where: "local" | "cloud") => void;
  /** A save to Anthropic that was refused (not a locked vault: that asks for her PIN here). */
  onRefused?: (code: string, message: string) => void;
} = {}) {
  const [st, setSt] = useState<ToolsmithModel | null>(null);
  const [pick, setPick] = useState<"local" | "cloud">(inWizard ? "cloud" : "local");
  const [model, setModel] = useState("claude-opus-5-5");
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [say, setSay] = useState("");
  const [needPin, setNeedPin] = useState(false);
  const [pin, setPin] = useState("");
  // The pasted key, only while her PIN is asked for; never stored or shown.
  const heldKey = useRef("");

  useEffect(
    () => () => {
      heldKey.current = "";
    },
    [],
  );

  const load = useCallback(async () => {
    const r = await getToolsmithModel();
    if (r.data) {
      setSt(r.data);
      // Her yes was to an earlier sentence: open on Anthropic so the new one shows.
      if (!inWizard) setPick(r.data.consent_again ? "cloud" : r.data.where);
      if (r.data.model) setModel(r.data.model);
    } else if (r.message) {
      setSay(r.message);
    }
  }, [inWizard]);

  useEffect(() => {
    void load();
  }, [load]);

  const done = (r: { ok: boolean; data: ToolsmithModel | null; message: string }) => {
    if (r.data) {
      setSt(r.data);
      if (!inWizard) setPick(r.data.where);
    }
    setSay(r.ok ? "" : r.message || "That did not work. Try again.");
    if (r.ok && r.data) onChosen?.(r.data.where);
  };

  const toLocal = async () => {
    setBusy(true);
    done(await chooseLocal());
    setBusy(false);
  };

  const save = async (k: string | undefined) => {
    const r = await chooseAnthropic(model, k);
    if (!r.ok && r.status === 423 && r.code === "vault_locked") {
      heldKey.current = k ?? "";
      setNeedPin(true);
      setSay(r.message || VAULT_LOCKED);
      return;
    }
    heldKey.current = "";
    setNeedPin(false);
    done(r);
    if (!r.ok) onRefused?.(r.code, r.message || "That did not work. Try again.");
  };

  const toAnthropic = async () => {
    setBusy(true);
    const k = key.trim() || undefined;
    setKey("");
    await save(k);
    setBusy(false);
  };

  /** Her PIN opens the vault (the existing route), then the same save again. */
  const unlockAndSave = async () => {
    const p = pin;
    setPin("");
    if (!p || busy) return;
    setBusy(true);
    let opened = false;
    let words = "";
    try {
      const r = await apiPostRaw<{ success?: boolean; message?: string }>("/vault/unlock", { pin: p });
      opened = r.ok && !!r.data?.success;
      words = r.data?.message || "";
    } catch {
      words = "BuildBot could not be reached.";
    }
    if (!opened) {
      setBusy(false);
      setSay(words || "That PIN did not open your vault. Try again.");
      return;
    }
    await save(heldKey.current || undefined);
    setBusy(false);
  };

  const cancelPin = () => {
    heldKey.current = "";
    setPin("");
    setNeedPin(false);
    setSay("Nothing was saved.");
  };

  const needsKey = !st || st.key_state === "" || st.key_state === "missing";

  return (
    <div style={inWizard ? undefined : cardStyle} data-walk={inWizard ? undefined : "settings-toolsmith"}>
      {!inWizard && (
      <>
      <div style={{ fontFamily: FREDOKA, fontWeight: 600, fontSize: 24, color: "var(--text)" }}>
        Toolsmith&rsquo;s model
      </div>
      <p style={{ ...text, margin: "6px 0 14px" }}>
        {st?.line || "Toolsmith's model: on this computer"}
      </p>
      <div className="flex flex-wrap gap-3" role="radiogroup" aria-label="Toolsmith's model">
        <button
          type="button"
          role="radio"
          aria-checked={pick === "local"}
          style={button(pick === "local")}
          disabled={busy}
          onClick={() => (st?.where === "cloud" || st?.consent_again ? void toLocal() : setPick("local"))}
        >
          On this computer (private, slower)
        </button>
        <button
          type="button"
          role="radio"
          aria-checked={pick === "cloud"}
          style={button(pick === "cloud")}
          disabled={busy}
          onClick={() => setPick("cloud")}
        >
          Anthropic (faster, your choice)
        </button>
      </div>
      </>
      )}

      {pick === "cloud" && (
        <div style={{ marginTop: 16 }}>
          <p style={{ ...text, margin: "0 0 12px" }}>{st?.consent_text}</p>
          <label style={{ ...text, display: "block", marginBottom: 10 }}>
            Model{" "}
            <select
              value={model}
              onChange={(e) => setModel(e.target.value)}
              disabled={busy}
              style={{ marginLeft: 8, padding: "6px 10px", borderRadius: 10 }}
            >
              {(st?.models.length ? st.models : [
                { id: "claude-opus-5-5", name: "Opus 5.5" },
                { id: "claude-sonnet-5-5", name: "Sonnet 5.5" },
              ]).map((m) => (
                <option key={m.id} value={m.id}>
                  Claude {m.name}
                </option>
              ))}
            </select>
          </label>
          <label style={{ ...text, display: "block", marginBottom: 12 }}>
            {needsKey ? "Your Anthropic key" : "Your Anthropic key is in your vault. Paste a new one to replace it"}
            <input
              type="password"
              autoComplete="off"
              spellCheck={false}
              value={key}
              onChange={(e) => setKey(e.target.value)}
              disabled={busy}
              style={{ display: "block", width: "100%", marginTop: 6, padding: "8px 10px", borderRadius: 10 }}
            />
          </label>
          <div className="flex flex-wrap gap-3">
            <button
              type="button"
              style={button(true)}
              disabled={busy || (needsKey && !key.trim())}
              onClick={() => void toAnthropic()}
            >
              {st?.where === "cloud" ? "Save" : "Use Anthropic for Toolsmith"}
            </button>
            {!needsKey && (
              <button type="button" style={button(false)} disabled={busy} onClick={async () => {
                setBusy(true);
                done(await forgetKey());
                setBusy(false);
              }}>
                Remove the key
              </button>
            )}
          </div>
        </div>
      )}

      {!inWizard && st?.where === "cloud" && st.keep_choices.length > 0 && (
        <div style={{ marginTop: 16 }}>
          <p style={{ ...text, margin: "0 0 8px" }}>
            {st.keep_question || "Keep Toolsmith's key ready while I'm building"}
          </p>
          <div className="flex flex-wrap gap-3" role="radiogroup" aria-label={st.keep_question}>
            {st.keep_choices.map((c) => (
              <button
                key={c.id}
                type="button"
                role="radio"
                aria-checked={st.keep === c.id}
                style={button(st.keep === c.id)}
                disabled={busy}
                onClick={async () => {
                  if (st.keep === c.id) return;
                  setBusy(true);
                  const r = await chooseKeep(c.id);
                  if (r.data) setSt(r.data);
                  setSay(r.ok ? "" : r.message || "That did not work. Try again.");
                  setBusy(false);
                }}
              >
                {c.id === "1h" ? `${c.name} (default)` : c.name}
              </button>
            ))}
          </div>
          {st.keep === "lock" && (
            <p style={{ ...text, margin: "8px 0 0" }}>It still lets go of the key after 24 hours.</p>
          )}
        </div>
      )}

      {st?.spent_line && <p style={{ ...text, margin: "14px 0 0" }}>{st.spent_line}</p>}
      {st?.lease_line && (
        <p role="status" style={{ ...text, margin: "10px 0 0" }}>{st.lease_line}</p>
      )}
      {say && (
        <p role="status" style={{ ...text, margin: "12px 0 0", color: "var(--text)" }}>
          {say}
        </p>
      )}
      {needPin && (
        <form
          style={{ marginTop: 10 }}
          onSubmit={(e) => {
            e.preventDefault();
            void unlockAndSave();
          }}
        >
          <label style={{ ...text, display: "block", marginBottom: 10 }}>
            Your vault PIN
            <input
              type="password"
              autoComplete="off"
              spellCheck={false}
              value={pin}
              onChange={(e) => setPin(e.target.value)}
              disabled={busy}
              style={{ display: "block", width: "100%", marginTop: 6, padding: "8px 10px", borderRadius: 10 }}
            />
          </label>
          <div className="flex flex-wrap gap-3">
            <button type="submit" style={button(true)} disabled={busy || !pin}>
              Open the vault and save the key
            </button>
            <button type="button" style={button(false)} disabled={busy} onClick={cancelPin}>
              Cancel
            </button>
          </div>
        </form>
      )}
    </div>
  );
}
