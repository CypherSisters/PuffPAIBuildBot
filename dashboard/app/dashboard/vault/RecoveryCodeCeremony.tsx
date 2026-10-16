"use client";

/**
 * The recovery-code ceremony on the Vault page: the same rules as the
 * onboarding wizard's vault step (app/onboarding/OnboardingWizard.tsx), built
 * on the same helpers (app/_lib/recoveryCode.ts).
 *
 *  - The code is shown once. It lives in this component's props and in the
 *    parent's React state for as long as this screen is mounted, and nowhere
 *    else: not storage, not the URL, not the console.
 *  - The owner types it back. A match is checked with recoveryCodeMatches,
 *    which may be stricter than the backend, never looser.
 *  - There is an honest way out ("I can't do this right now"), and its price is
 *    said before it is taken: the code on screen is gone, the PIN becomes the
 *    only way in, and a new code can be made on this page while the vault is
 *    open.
 *
 * The Vault page used to throw this code away: it created the vault through
 * /vault/unlock, which returns `recovery_code` exactly once, and never read it.
 */

import { useState, type CSSProperties } from "react";
import MsIcon from "../../_components/MsIcon";
import {
  RECOVERY_CONFIRM_FAILED,
  recoveryCodeMatches,
} from "../../_lib/recoveryCode";

const FREDOKA = "var(--font-fredoka), sans-serif";
const NUNITO = "var(--font-nunito), sans-serif";
const JBMONO = "var(--font-jbmono), monospace";
const RED = "#FF5C7A";

const text: CSSProperties = {
  margin: "0 0 12px",
  fontFamily: NUNITO,
  fontWeight: 500,
  fontSize: 16,
  lineHeight: 1.5,
  color: "var(--text2)",
};

export default function RecoveryCodeCeremony({
  code,
  notice,
  onDone,
}: {
  /** The code exactly as the backend returned it. */
  code: string;
  /** The backend's own wording for this moment, shown verbatim when present. */
  notice?: string;
  /** confirmed=true when the owner typed it back; false when they skipped. */
  onDone: (confirmed: boolean) => void;
}) {
  const [typed, setTyped] = useState("");
  const [error, setError] = useState("");

  const confirm = () => {
    if (!recoveryCodeMatches(code, typed)) {
      setError(RECOVERY_CONFIRM_FAILED);
      return;
    }
    setTyped("");
    onDone(true);
  };

  return (
    <div
      role="dialog"
      aria-modal="false"
      aria-labelledby="vault-recovery-title"
      style={{
        borderRadius: 28,
        border: "1px solid var(--pink)",
        background: "var(--card)",
        padding: "24px 28px",
        boxShadow: "var(--shadow)",
      }}
    >
      <h2
        id="vault-recovery-title"
        style={{ margin: "0 0 10px", fontFamily: FREDOKA, fontWeight: 600, fontSize: 26, color: "var(--text)" }}
      >
        Write down your recovery code
      </h2>
      {notice ? <p style={text}>{notice}</p> : null}
      <p style={text}>
        If you forget your PIN, this code is the only other way to open your vault. Nobody else
        has a copy, and it will not be shown again.
      </p>
      <div
        style={{
          margin: "4px 0 16px",
          padding: "16px 18px",
          borderRadius: 14,
          border: "1px solid var(--pink)",
          background: "var(--chip)",
          fontFamily: JBMONO,
          fontSize: 20,
          letterSpacing: ".08em",
          color: "var(--text)",
          wordBreak: "break-all",
        }}
      >
        {code}
      </div>
      <label
        htmlFor="vault-recovery-typed"
        style={{ display: "block", marginBottom: 6, fontFamily: NUNITO, fontWeight: 700, fontSize: 15, color: "var(--text)" }}
      >
        Type the recovery code back in
      </label>
      <input
        id="vault-recovery-typed"
        type="text"
        autoComplete="off"
        spellCheck={false}
        value={typed}
        onChange={(e) => {
          setTyped(e.target.value);
          setError("");
        }}
        onKeyDown={(e) => e.key === "Enter" && confirm()}
        style={{
          width: "100%",
          borderRadius: 14,
          border: "1px solid var(--line)",
          background: "var(--card2)",
          color: "var(--text)",
          fontFamily: JBMONO,
          fontSize: 16,
          padding: "12px 14px",
        }}
      />
      <p style={{ ...text, fontSize: 14, color: "var(--text3)", margin: "8px 0 0" }}>
        Capitals and dashes do not matter. Typing it back now is how you find out that what you
        wrote down is right.
      </p>
      {error && (
        <p style={{ margin: "12px 0 0", fontFamily: NUNITO, fontWeight: 600, fontSize: 15, color: RED }}>
          {error}
        </p>
      )}
      <div className="mt-5 flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={confirm}
          className="flex cursor-pointer items-center gap-2"
          style={{
            padding: "12px 22px",
            borderRadius: 99,
            border: "none",
            background: "var(--pink)",
            color: "#fff",
            fontFamily: NUNITO,
            fontWeight: 800,
            fontSize: 16,
          }}
        >
          <MsIcon name="check" size={18} />
          I&apos;ve written it down, continue
        </button>
        <button
          type="button"
          onClick={() => {
            setTyped("");
            onDone(false);
          }}
          className="cursor-pointer"
          style={{
            padding: "12px 18px",
            borderRadius: 99,
            border: "1px solid var(--line)",
            background: "transparent",
            color: "var(--text2)",
            fontFamily: NUNITO,
            fontWeight: 700,
            fontSize: 15,
          }}
        >
          I can&apos;t do this right now
        </button>
      </div>
      <p style={{ ...text, fontSize: 14, color: "var(--text3)", margin: "12px 0 0" }}>
        If you skip this, the code on this screen is gone and your PIN becomes the only way in. You
        can make a new code on this page later, while the vault is open.
      </p>
    </div>
  );
}
