"use client";

/**
 * Connecting GitHub: one flow, shared by the wizard's GitHub question and the
 * Developer page's GitHub tile.
 *
 *  - GitHub's own sign-in code when this build offers it (device_available):
 *    the code, a link to github.com, and a waiting line while it checks.
 *  - Otherwise a token box: a link to make one on GitHub and its four steps.
 *  - No vault yet: an inline "Set a vault PIN to keep your token safe" (the
 *    same PIN rules and recovery-code ceremony as the wizard's vault step),
 *    then the token box. Vault locked: an inline "Open your vault". Never a
 *    dead end.
 *  - Connected: "Connected as <login>", what the token can do beyond reading
 *    (extra_scopes), and how many GitHub searches are left.
 *
 * GitHubConnectView draws one state from its props (so every state can be
 * rendered and checked without a browser); GitHubConnect holds the state.
 *
 * THE TOKEN. Held in this component's state only while she types it, sent
 * once, and the field is cleared the moment it is sent. Never written to
 * localStorage, sessionStorage, a cookie, the address or the console.
 *
 * The avatar is her initial, not GitHub's picture: the page loads pictures
 * from this computer only.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import RecoveryCodeCeremony from "../../dashboard/vault/RecoveryCodeCeremony";
import { createVault } from "../../_lib/services/setupService";
import { unlockVault } from "../../_lib/services/vaultService";
import { pinRefusal } from "../../_lib/vaultPin";
import {
  disconnectGitHub,
  getGitHubStatus,
  getGitHubTokenHelp,
  pollGitHubDevice,
  refusalWords,
  sendGitHubToken,
  startGitHubDevice,
  type DeviceCode,
  type Disconnected,
  type GitHubStatus,
  type TokenHelp,
} from "../../_lib/services/githubService";

export type GitHubPhase = "loading" | "off" | "starting" | "device" | "token" | "connected";

const NUNITO = "var(--font-nunito), sans-serif";
const JBM = "var(--font-jbmono), monospace";

const small: React.CSSProperties = { margin: 0, fontFamily: NUNITO, fontSize: 13, lineHeight: 1.45, color: "var(--text2)" };
const button: React.CSSProperties = {
  fontFamily: NUNITO,
  fontWeight: 700,
  fontSize: 13,
  padding: "6px 14px",
  borderRadius: 999,
  border: "1px solid var(--line)",
  background: "var(--card2)",
  color: "var(--text)",
  cursor: "pointer",
  textDecoration: "none",
  display: "inline-flex",
  alignItems: "center",
  gap: 6,
};
const primary: React.CSSProperties = { ...button, background: "var(--magenta)", borderColor: "var(--magenta)", color: "#fff" };
const input: React.CSSProperties = {
  flex: "1 1 200px",
  minWidth: 0,
  background: "var(--card2)",
  border: "1px solid var(--line)",
  borderRadius: 10,
  padding: "7px 10px",
  color: "var(--text)",
  fontFamily: JBM,
  fontSize: 13,
};

/** The fallback steps when /github/token-help does not answer. */
export const TOKEN_STEPS = [
  "Open the link and sign in to GitHub. A free account is enough.",
  "Give the token a name, pick an end date, and choose read-only access.",
  "Under Repository access, Public repositories is enough. Pick a private project there only if your bot should use it.",
  "Press Generate token, copy it, and paste it here.",
];
export const TOKEN_PAGE = "https://github.com/settings/personal-access-tokens/new";
export const LOCKED_LINE = "Searches are public while your vault is locked.";
export const NO_VAULT_LINE = "No vault yet. Your token is kept in your vault, so set one first.";
export const VAULT_PIN_TITLE = "Set a vault PIN to keep your token safe";
export const UNLOCK_TITLE = "Open your vault to connect";

/** Only a github.com address becomes a link. */
export function githubLink(url: string | undefined): string {
  if (!url) return "";
  try {
    const u = new URL(url);
    return u.protocol === "https:" && (u.hostname === "github.com" || u.hostname.endsWith(".github.com")) ? u.href : "";
  } catch {
    return "";
  }
}

export function rateLine(status: GitHubStatus | null): string {
  if (!status?.rate) return "";
  const n = (v: number) => v.toLocaleString("en-US");
  return `GitHub searches left this ${status.rate_window}: ${n(status.rate.remaining)} of ${n(status.rate.limit)}`;
}

function Monogram({ login }: { login: string }) {
  return (
    <span
      aria-hidden
      data-gh-avatar
      style={{
        width: 30,
        height: 30,
        borderRadius: 999,
        display: "inline-grid",
        placeItems: "center",
        background: "var(--card2)",
        border: "1px solid var(--line)",
        fontFamily: NUNITO,
        fontWeight: 800,
        fontSize: 14,
        color: "var(--text)",
        flex: "none",
      }}
    >
      {(login[0] || "?").toUpperCase()}
    </span>
  );
}

export interface GitHubConnectViewProps {
  phase: GitHubPhase;
  status: GitHubStatus | null;
  device?: DeviceCode | null;
  help?: TokenHelp | null;
  token?: string;
  error?: string;
  busy?: boolean;
  /** Disconnect was pressed once: ask "Yes, disconnect" / "Keep it". */
  confirmingOff?: boolean;
  /** The tile offers Disconnect; the wizard does not. */
  canDisconnect?: boolean;
  onConnect?: () => void;
  onToken?: (value: string) => void;
  onSendToken?: () => void;
  onCancel?: () => void;
  onDisconnect?: () => void;
  onConfirmOff?: () => void;
  onKeep?: () => void;
  /** No vault yet: the PIN, typed twice, and making it. */
  vaultPin?: string;
  vaultAgain?: string;
  onVaultPin?: (value: string) => void;
  onVaultAgain?: (value: string) => void;
  onMakeVault?: () => void;
  /** The recovery code, shown once after the vault is made. */
  recovery?: { code: string; notice?: string } | null;
  onRecoveryDone?: (confirmed: boolean) => void;
  /** Vault locked: its PIN, and opening it. */
  unlockPin?: string;
  onUnlockPin?: (value: string) => void;
  onUnlock?: () => void;
  /** After a disconnect: what happened and where to revoke the token. */
  revoke?: { line: string; url: string } | null;
}

function VaultBox(p: GitHubConnectViewProps & { err: React.ReactNode }) {
  const s = p.status;
  if (p.recovery?.code) {
    return (
      <div data-gh-state="recovery" style={{ display: "grid", gap: 8 }}>
        <RecoveryCodeCeremony code={p.recovery.code} notice={p.recovery.notice} onDone={(ok) => p.onRecoveryDone?.(ok)} />
      </div>
    );
  }
  if (s && !s.vault_set_up) {
    return (
      <form
        data-gh-state="vault"
        style={{ display: "grid", gap: 8 }}
        onSubmit={(e) => {
          e.preventDefault();
          p.onMakeVault?.();
        }}
      >
        <strong style={{ fontFamily: NUNITO, fontSize: 15, color: "var(--text)" }}>{VAULT_PIN_TITLE}</strong>
        <p style={small}>
          {NO_VAULT_LINE} The vault is locked with a PIN only you know. You get a recovery code once, to write down.
        </p>
        <input type="password" aria-label="Vault PIN" placeholder="Vault PIN" autoComplete="new-password" value={p.vaultPin ?? ""} onChange={(e) => p.onVaultPin?.(e.target.value)} style={input} />
        <input type="password" aria-label="The same PIN again" placeholder="The same PIN again" autoComplete="new-password" value={p.vaultAgain ?? ""} onChange={(e) => p.onVaultAgain?.(e.target.value)} style={input} />
        <div className="flex flex-wrap" style={{ gap: 6 }}>
          <button type="submit" style={primary} disabled={p.busy || !(p.vaultPin ?? "")}>
            Set the PIN
          </button>
          <button type="button" style={button} onClick={p.onCancel}>
            Cancel
          </button>
        </div>
        {p.err}
      </form>
    );
  }
  return (
    <form
      data-gh-state="unlock"
      style={{ display: "grid", gap: 8 }}
      onSubmit={(e) => {
        e.preventDefault();
        p.onUnlock?.();
      }}
    >
      <strong style={{ fontFamily: NUNITO, fontSize: 15, color: "var(--text)" }}>{UNLOCK_TITLE}</strong>
      <p style={small}>Your token is kept in your vault, and your vault is locked. Type its PIN to open it.</p>
      <div className="flex flex-wrap" style={{ gap: 6 }}>
        <input type="password" aria-label="Vault PIN" placeholder="Vault PIN" autoComplete="current-password" value={p.unlockPin ?? ""} onChange={(e) => p.onUnlockPin?.(e.target.value)} style={input} />
        <button type="submit" style={primary} disabled={p.busy || !(p.unlockPin ?? "")}>
          Open it
        </button>
        <button type="button" style={button} onClick={p.onCancel}>
          Cancel
        </button>
      </div>
      {p.err}
    </form>
  );
}

export function GitHubConnectView(p: GitHubConnectViewProps) {
  const s = p.status;
  const noVault = !!s && !s.vault_set_up;
  const locked = !noVault && !!s?.vault_locked;
  const err = p.error ? (
    <p role="alert" data-gh-error style={{ ...small, color: "var(--puff-red)" }}>
      {p.error}
    </p>
  ) : null;

  if (p.phase === "loading") {
    return (
      <div data-gh-state="loading" style={{ display: "grid", gap: 6 }}>
        <p style={small}>Checking GitHub...</p>
        {err}
      </div>
    );
  }

  if (p.phase === "connected" && s) {
    const rate = rateLine(s);
    return (
      <div data-gh-state="connected" style={{ display: "grid", gap: 8 }}>
        <div className="flex items-center" style={{ gap: 10 }}>
          <Monogram login={s.login} />
          <div style={{ display: "grid", gap: 1, minWidth: 0 }}>
            <strong style={{ fontFamily: NUNITO, fontSize: 15, color: "var(--text)" }}>
              Connected as {s.login || "your account"}
            </strong>
            <span style={small}>
              {s.can_read_private && !locked
                ? "Your bot can use your own projects, private ones too."
                : s.can_read_private
                  ? "Your bot can use your public projects. Open your vault to use your private ones."
                  : "Your bot can use your public projects."}
            </span>
          </div>
        </div>
        {locked ? (
          <p data-gh-locked style={{ ...small, color: "var(--lemon)" }}>
            {LOCKED_LINE}
          </p>
        ) : null}
        {s.extra_scopes.length ? (
          <p data-gh-extra style={{ ...small, borderLeft: "3px solid var(--lemon)", paddingLeft: 10 }}>
            This token can do more than read: <span style={{ fontFamily: JBM }}>{s.extra_scopes.join(", ")}</span>.
            Your bot only reads. A read-only token is safer; you can make one and connect again.
          </p>
        ) : null}
        {rate ? (
          <p data-gh-rate style={{ ...small, fontSize: 12, color: "var(--text3)" }}>
            {rate}
          </p>
        ) : null}
        {p.canDisconnect ? (
          <div className="flex flex-wrap items-center" style={{ gap: 6 }}>
            {p.confirmingOff ? (
              <>
                <span style={small}>Disconnect GitHub? Your bot goes back to public searches.</span>
                <button type="button" style={button} disabled={p.busy} onClick={p.onConfirmOff}>
                  Yes, disconnect
                </button>
                <button type="button" style={button} disabled={p.busy} onClick={p.onKeep}>
                  Keep it
                </button>
              </>
            ) : (
              <button type="button" style={button} disabled={p.busy} onClick={p.onDisconnect}>
                Disconnect
              </button>
            )}
          </div>
        ) : null}
        {err}
      </div>
    );
  }

  if (p.phase === "device" && p.device) {
    const link = githubLink(p.device.verification_uri);
    return (
      <div data-gh-state="device" style={{ display: "grid", gap: 8 }}>
        <p style={small}>Type this code on GitHub:</p>
        <div
          data-gh-code
          style={{ fontFamily: JBM, fontSize: 24, fontWeight: 700, letterSpacing: ".12em", color: "var(--text)" }}
        >
          {p.device.user_code}
        </div>
        <div className="flex flex-wrap items-center" style={{ gap: 8 }}>
          {link ? (
            <a href={link} target="_blank" rel="noopener noreferrer" style={primary}>
              Open github.com
            </a>
          ) : (
            <span style={{ ...small, fontFamily: JBM }}>{p.device.verification_uri}</span>
          )}
          <button type="button" style={button} onClick={p.onCancel}>
            Cancel
          </button>
        </div>
        <p data-gh-waiting style={{ ...small, color: "var(--text3)" }}>
          Waiting for GitHub. This moves on by itself.
        </p>
        {err}
      </div>
    );
  }

  if (p.phase === "token" && (noVault || locked || p.recovery?.code)) {
    return <VaultBox {...p} err={err} />;
  }

  if (p.phase === "token") {
    const link = githubLink(p.help?.url) || TOKEN_PAGE;
    const steps = p.help?.steps?.length ? p.help.steps.slice(0, 6) : TOKEN_STEPS;
    return (
      <form
        data-gh-state="token"
        style={{ display: "grid", gap: 8 }}
        onSubmit={(e) => {
          e.preventDefault();
          p.onSendToken?.();
        }}
      >
        <a href={link} target="_blank" rel="noopener noreferrer" style={{ ...button, justifySelf: "start" }}>
          Make a token on GitHub
        </a>
        <ol style={{ ...small, margin: 0, paddingLeft: 18, listStyle: "decimal" }}>
          {steps.map((step) => (
            <li key={step}>{step}</li>
          ))}
        </ol>
        <div className="flex flex-wrap" style={{ gap: 6 }}>
          <input
            type="password"
            aria-label="GitHub token"
            placeholder="Paste the token"
            autoComplete="off"
            spellCheck={false}
            value={p.token ?? ""}
            maxLength={255}
            onChange={(e) => p.onToken?.(e.target.value)}
            style={input}
          />
          <button type="submit" style={primary} disabled={p.busy || !(p.token ?? "").trim()}>
            Connect
          </button>
          <button type="button" style={button} onClick={p.onCancel}>
            Cancel
          </button>
        </div>
        <p style={{ ...small, fontSize: 12, color: "var(--text3)" }}>Kept in your vault, never in this browser.</p>
        {err}
      </form>
    );
  }

  // Not connected (or starting).
  return (
    <div data-gh-state="off" style={{ display: "grid", gap: 8 }}>
      <p style={small}>
        <strong style={{ color: "var(--text)" }}>Not connected.</strong> Your bot searches GitHub as a visitor.
      </p>
      {p.revoke?.line ? (
        <p data-gh-revoke style={small}>
          {p.revoke.line}{" "}
          {githubLink(p.revoke.url) ? (
            <a href={githubLink(p.revoke.url)} target="_blank" rel="noopener noreferrer" style={{ color: "var(--magenta)" }}>
              Where to revoke it on github.com
            </a>
          ) : null}
        </p>
      ) : null}
      {noVault ? (
        <p data-gh-novault style={{ ...small, color: "var(--text3)" }}>
          No vault yet.
        </p>
      ) : null}
      {locked ? (
        <p data-gh-locked style={{ ...small, color: "var(--lemon)" }}>
          {LOCKED_LINE}
        </p>
      ) : null}
      {rateLine(s) ? (
        <p data-gh-rate style={{ ...small, fontSize: 12, color: "var(--text3)" }}>
          {rateLine(s)}
        </p>
      ) : null}
      {p.onConnect ? (
        <button type="button" style={{ ...primary, justifySelf: "start" }} disabled={p.busy || p.phase === "starting"} onClick={p.onConnect}>
          {p.phase === "starting" ? "Starting..." : "Connect"}
        </button>
      ) : null}
      {err}
    </div>
  );
}

export default function GitHubConnect({
  autoStart = false,
  canDisconnect = false,
  onConnected,
  onCancel,
}: {
  /** Go straight to the code or the token box (the wizard's Connect). */
  autoStart?: boolean;
  canDisconnect?: boolean;
  onConnected?: (status: GitHubStatus) => void;
  /** Cancel pressed while connecting. */
  onCancel?: () => void;
}) {
  const [phase, setPhase] = useState<GitHubPhase>("loading");
  const [status, setStatus] = useState<GitHubStatus | null>(null);
  const [device, setDevice] = useState<DeviceCode | null>(null);
  const [help, setHelp] = useState<TokenHelp | null>(null);
  const [token, setToken] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirmingOff, setConfirmingOff] = useState(false);
  const [vaultPin, setVaultPin] = useState("");
  const [vaultAgain, setVaultAgain] = useState("");
  const [unlockPin, setUnlockPin] = useState("");
  const [recovery, setRecovery] = useState<{ code: string; notice?: string } | null>(null);
  const [revoke, setRevoke] = useState<{ line: string; url: string } | null>(null);
  const polling = useRef(0);

  const stopPolling = () => {
    polling.current += 1;
  };

  const refresh = useCallback(async (): Promise<GitHubStatus | null> => {
    const r = await getGitHubStatus();
    if (!r.ok) {
      setError(r.status === 0 ? "No answer from this computer." : "GitHub status could not be read.");
      return null;
    }
    setStatus(r.data);
    return r.data;
  }, []);

  const connected = useCallback(
    (s: GitHubStatus) => {
      stopPolling();
      setDevice(null);
      setError("");
      setPhase("connected");
      onConnected?.(s);
    },
    [onConnected],
  );

  const begin = useCallback(
    async (s: GitHubStatus | null) => {
      setError("");
      setPhase("starting");
      if (s?.device_available) {
        const r = await startGitHubDevice();
        if (r.ok && r.data.user_code) {
          setDevice(r.data);
          setPhase("device");
          const run = ++polling.current;
          const every = Math.max(5, Number(r.data.interval) || 5) * 1000;
          const until = Date.now() + Math.max(60, Number(r.data.expires_in) || 900) * 1000;
          const tick = async () => {
            if (run !== polling.current) return;
            if (Date.now() > until) {
              setDevice(null);
              setPhase("off");
              setError("The code ran out. Press Connect for a new one.");
              return;
            }
            const p = await pollGitHubDevice();
            if (run !== polling.current) return;
            if (p.ok && p.data.connected) {
              const now = await refresh();
              if (now?.connected) return connected(now);
            } else if (!p.ok && p.data.pending !== true) {
              setDevice(null);
              setPhase("off");
              setError(refusalWords(p.data, p.status));
              return;
            }
            window.setTimeout(() => void tick(), every);
          };
          window.setTimeout(() => void tick(), every);
          return;
        }
        // No code: the token box still works.
      }
      const h = await getGitHubTokenHelp();
      setHelp(h.ok ? h.data : null);
      setPhase("token");
    },
    [connected, refresh],
  );

  useEffect(() => {
    let alive = true;
    void (async () => {
      const s = await refresh();
      if (!alive) return;
      if (s?.connected) setPhase("connected");
      else if (autoStart) await begin(s);
      else setPhase("off");
    })();
    return () => {
      alive = false;
      stopPolling();
      setToken("");
      setVaultPin("");
      setVaultAgain("");
      setUnlockPin("");
      setRecovery(null);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function sendToken() {
    setRevoke(null);
    const value = token.trim();
    setToken("");
    if (!value || busy) return;
    setBusy(true);
    const r = await sendGitHubToken(value);
    setBusy(false);
    if (!r.ok) {
      setError(refusalWords(r.data, r.status));
      return;
    }
    const s = await refresh();
    if (s?.connected) connected(s);
    else setError("GitHub did not accept that token. Check it and try again.");
  }

  /** No vault yet: make it with the wizard's own PIN rules, then the
   *  recovery code once, then the token box. */
  async function makeVault() {
    const pin = vaultPin;
    const again = vaultAgain;
    const refusal = pinRefusal(pin);
    if (refusal) {
      setError(refusal);
      return;
    }
    if (pin !== again) {
      setError("The two PINs are not the same. Type them again.");
      return;
    }
    setVaultPin("");
    setVaultAgain("");
    setBusy(true);
    const reply = await createVault(pin);
    if (reply.ok && reply.data?.success) {
      // Opened at once with the PIN she just set, so she is not asked again.
      try {
        await unlockVault(pin);
      } catch {
        /* the "Open your vault" box comes next instead */
      }
    }
    setBusy(false);
    if (reply.status === 409 || reply.data?.already_initialized) {
      setError("");
      await refresh();
      return;
    }
    if (!reply.ok || !reply.data?.success) {
      setError(reply.data?.message || "The vault was not made. Nothing was changed.");
      return;
    }
    setError("");
    if (reply.data.recovery_code) {
      setRecovery({ code: reply.data.recovery_code, notice: reply.data.recovery_code_notice });
      return;
    }
    await refresh();
  }

  async function recoveryDone() {
    setRecovery(null);
    const s = await refresh();
    if (s && !s.connected && s.device_available) await begin(s);
  }

  /** Vault locked: open it here, then the token box. */
  async function unlock() {
    const pin = unlockPin;
    setUnlockPin("");
    if (!pin || busy) return;
    setBusy(true);
    let ok = false;
    try {
      ok = !!(await unlockVault(pin))?.success;
    } catch {
      ok = false;
    }
    setBusy(false);
    if (!ok) {
      setError("That PIN did not open your vault. Try again.");
      return;
    }
    setError("");
    const s = await refresh();
    if (s && !s.connected && s.device_available) await begin(s);
  }

  function cancel() {
    stopPolling();
    setToken("");
    setVaultPin("");
    setVaultAgain("");
    setUnlockPin("");
    setDevice(null);
    setError("");
    setPhase("off");
    onCancel?.();
  }

  async function disconnect() {
    setBusy(true);
    const r = await disconnectGitHub();
    setBusy(false);
    setConfirmingOff(false);
    if (!r.ok) {
      setError(refusalWords(r.data, r.status));
      return;
    }
    const d: Disconnected = r.data || {};
    setRevoke({ line: d.line || "Disconnected.", url: d.revoke_url || "" });
    await refresh();
    setError("");
    setPhase("off");
  }

  return (
    <GitHubConnectView
      phase={phase}
      status={status}
      device={device}
      help={help}
      token={token}
      error={error}
      busy={busy}
      confirmingOff={confirmingOff}
      canDisconnect={canDisconnect}
      onConnect={() => void begin(status)}
      onToken={setToken}
      onSendToken={() => void sendToken()}
      onCancel={cancel}
      onDisconnect={() => setConfirmingOff(true)}
      onConfirmOff={() => void disconnect()}
      onKeep={() => setConfirmingOff(false)}
      vaultPin={vaultPin}
      vaultAgain={vaultAgain}
      onVaultPin={setVaultPin}
      onVaultAgain={setVaultAgain}
      onMakeVault={() => void makeVault()}
      recovery={recovery}
      onRecoveryDone={() => void recoveryDone()}
      unlockPin={unlockPin}
      onUnlockPin={setUnlockPin}
      onUnlock={() => void unlock()}
      revoke={revoke}
    />
  );
}
