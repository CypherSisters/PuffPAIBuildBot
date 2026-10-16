"use client";

/**
 * Vault (Zone 1) — converted off the admin template into the design-match
 * shell (glass cards, Fredoka/Nunito/JetBrains Mono, theme tokens). Every data
 * contract is unchanged: /vault/mfa-status decides "never created" vs "created
 * and locked", /vault/unlock serves both unlocking and first-time setup
 * (`initialize: true`), and the item list comes from /vault/contents.
 *
 * TWO TOKEN BUGS FIXED HERE (the vault is useless when either bites):
 *
 *  1. After a successful store, this page dispatched `fetchContents()` with NO
 *     argument. That thunk sends X-Vault-Token only when given one, so the
 *     refresh 403'd with "Vault is locked" and the item the owner had just
 *     encrypted did not appear — the vault looked like it had silently dropped
 *     it. The store path now refreshes with `fetchContents(vaultToken)`, the
 *     same way the delete path already did.
 *
 *  2. Store and delete are back on the shared service functions, which now send
 *     the token themselves (see _lib/services/vaultService.ts). Deleting goes
 *     through the `removeItem` thunk again — previously impossible, because that
 *     thunk sent no token and 403'd, which is why this page grew its own copy of
 *     the fetch. One tokened path each, no duplicates to drift apart.
 *
 * The PIN is never echoed, never logged, and the reveal toggle is per-field.
 *
 * TRUE WORDS. This page said "AES-256-GCM" (the vault is Fernet:
 * AES-128-CBC with an HMAC-SHA256 tamper check, keys from PBKDF2 at 600,000
 * rounds), said the PIN "cannot be recovered or reset" while it threw away the
 * recovery code /vault/unlock returns on create, and asked for "at least 4
 * digits" with an 8-character cap while the vault itself refuses anything under
 * 8 characters - so a 10-digit PIN could not even be typed into the unlock box.
 * Create now shows the recovery code once and has it typed back
 * (RecoveryCodeCeremony, same helpers as onboarding), an open vault can make a
 * new code, and the PIN rules match zone_vault.validate_pin.
 *
 * FORGOT YOUR PIN. The wizard made the owner write down a
 * recovery code, and nothing on this page took it. The locked view now has
 * "Forgot your PIN? Use your recovery code": the code and a new PIN (the
 * wizard's rules, _lib/vaultPin.ts) go to POST /vault/recover in the request
 * body. On success the vault is open, the old code and old PIN are retired,
 * and the NEW code goes through the same RecoveryCodeCeremony. A cooldown
 * (429) is shown as a wait, with the backend's own seconds. The page reaches
 * the route through the paired, key-bearing proxy like every vault call.
 */

import { useState, useEffect, useRef, useCallback, type CSSProperties } from "react";
import MsIcon from "../../_components/MsIcon";
import { useAppDispatch, useAppSelector } from "../../_stores/hooks";
import {
  unlock,
  lock,
  fetchContents,
  removeItem,
  clearVaultError,
  sessionOpened,
} from "../../_stores/vaultSlice";
import {
  getVaultStatus,
  recoverVault,
  storeVaultItem,
} from "../../_lib/services/vaultService";
import { API_URL } from "../../_lib/api";
import { RECOVERY_NOT_CONFIRMED, normalizeRecoveryCode } from "../../_lib/recoveryCode";
import { PIN_MAX_LENGTH, pinRefusal } from "../../_lib/vaultPin";
import RecoveryCodeCeremony from "./RecoveryCodeCeremony";

// The SERVER locks the vault after this long without use, wherever it was
// opened (security/vault.py IDLE_LOCK_SECONDS). The page reads the real value
// from /vault/mfa-status; this is only the value until that answers.
const AUTO_LOCK_SECONDS = 5 * 60; // 5 minutes
const MAX_ATTEMPTS = 5;
const LOCKOUT_SECONDS = 5 * 60; // 5 minutes
// A new PIN, here or in "Forgot your PIN?", is held to the setup wizard's
// rules (_lib/vaultPin.ts, the same file the onboarding wizard uses): never
// looser than the backend, which checks them again.
const MAX_PIN_LENGTH = PIN_MAX_LENGTH;

/** What an item is called on screen. Internal names stay in the API. */
function itemLabel(key: string): string {
  return key === "auto_detected_pii" ? "Personal details I noticed" : key;
}

/* ── shell vocabulary ───────────────────────────────────────────────── */

const FREDOKA = "var(--font-fredoka), sans-serif";
const NUNITO = "var(--font-nunito), sans-serif";
const JBMONO = "var(--font-jbmono), monospace";
const RED = "#FF5C7A";

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
  padding: "24px 28px",
  boxShadow: "var(--shadow)",
};

const fieldStyle: CSSProperties = {
  width: "100%",
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

const pinFieldStyle: CSSProperties = {
  ...fieldStyle,
  fontFamily: JBMONO,
  fontSize: 20,
  letterSpacing: ".3em",
  textAlign: "center",
  padding: "13px 44px 13px 14px",
};

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
        ? `color-mix(in srgb, ${RED} 16%, transparent)`
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

/** Three status tiles across the top. */
function StatTile({
  label,
  value,
  note,
  icon,
  colorVar,
}: {
  label: string;
  value: string;
  note: string;
  icon: string;
  colorVar: string;
}) {
  return (
    <div
      style={{
        ...panelBase,
        borderRadius: 26,
        padding: 22,
        display: "flex",
        alignItems: "center",
        gap: 16,
      }}
    >
      <span
        className="flex shrink-0 items-center justify-center"
        style={{
          width: 48,
          height: 48,
          borderRadius: 16,
          background: `color-mix(in srgb, ${colorVar} 16%, transparent)`,
          color: colorVar,
        }}
      >
        <MsIcon name={icon} size={26} />
      </span>
      <div className="min-w-0">
        <div style={microLabel}>{label}</div>
        <div
          style={{
            fontFamily: FREDOKA,
            fontWeight: 600,
            fontSize: 26,
            lineHeight: 1.2,
            color: "var(--text)",
          }}
        >
          {value}
        </div>
        <div style={{ fontFamily: JBMONO, fontWeight: 600, fontSize: 12, color: "var(--text3)" }}>
          {note}
        </div>
      </div>
    </div>
  );
}

/**
 * Vault lifecycle as far as the UI is concerned.
 *  - "checking"  we have not yet heard back from /vault/mfa-status
 *  - "new"       no vault has ever been created; nothing is locked
 *  - "existing"  a vault exists and needs a PIN (or is already unlocked)
 */
type SetupState = "checking" | "new" | "existing";

export default function VaultPage() {
  const dispatch = useAppDispatch();
  const {
    isUnlocked,
    contents,
    loading: vaultLoading,
    error: reduxError,
  } = useAppSelector((s) => s.vault);

  // Local UI state
  const [pin, setPin] = useState("");
  const [showPin, setShowPin] = useState(false);
  const [error, setError] = useState("");
  const [successBanner, setSuccessBanner] = useState(false);
  const [vaultToken, setVaultToken] = useState<string | null>(null);
  const [failedAttempts, setFailedAttempts] = useState(0);
  const [lockoutUntil, setLockoutUntil] = useState<number | null>(null);
  const [lockoutRemaining, setLockoutRemaining] = useState(0);
  const [shakeError, setShakeError] = useState(false);
  const [backendOffline, setBackendOffline] = useState(false);

  // How long the server leaves the vault open without use (from the server).
  const [autoLockSeconds, setAutoLockSeconds] = useState(AUTO_LOCK_SECONDS);
  const [autoLockLine, setAutoLockLine] = useState("");

  // Vault setup state
  const [setupState, setSetupState] = useState<SetupState>("checking");
  const [confirmPin, setConfirmPin] = useState("");
  const [createLoading, setCreateLoading] = useState(false);

  // Auto-lock timer
  const [autoLockRemaining, setAutoLockRemaining] = useState(AUTO_LOCK_SECONDS);

  // Recovery-code ceremony. The code lives here and nowhere else, only while
  // the ceremony is on screen; see RecoveryCodeCeremony.
  const [recoveryCode, setRecoveryCode] = useState("");
  const [recoveryNotice, setRecoveryNotice] = useState("");
  const [recoveryMessage, setRecoveryMessage] = useState("");
  const [replaceAsk, setReplaceAsk] = useState(false);
  const [replaceLoading, setReplaceLoading] = useState(false);

  // "Forgot your PIN? Use your recovery code." The code and the new PIN live
  // in these fields while the form is on screen and are cleared on every
  // answer; they go to the backend in a request body only.
  const [recoverOpen, setRecoverOpen] = useState(false);
  const [recoverCode, setRecoverCode] = useState("");
  const [recoverPin, setRecoverPin] = useState("");
  const [recoverPinAgain, setRecoverPinAgain] = useState("");
  const [recoverBusy, setRecoverBusy] = useState(false);
  const [recoverError, setRecoverError] = useState("");
  const [recoverWaitUntil, setRecoverWaitUntil] = useState<number | null>(null);
  const [recoverWaitLeft, setRecoverWaitLeft] = useState(0);
  const autoLockRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const unlockTimeRef = useRef<number>(0);
  const handleLockRef = useRef<() => void>(() => {});

  // Store item form
  const [showStoreForm, setShowStoreForm] = useState(false);
  const [storeKey, setStoreKey] = useState("");
  const [storeValue, setStoreValue] = useState("");
  const [storeType, setStoreType] = useState("text");
  const [storeLoading, setStoreLoading] = useState(false);
  const [storeError, setStoreError] = useState("");
  const [deleteLoading, setDeleteLoading] = useState<string | null>(null);

  // View item modal state
  const [viewKey, setViewKey] = useState<string | null>(null);
  const [viewContent, setViewContent] = useState<string>("");
  const [viewContentType, setViewContentType] = useState<string>("text");
  const [viewLoading, setViewLoading] = useState<boolean>(false);
  const [viewError, setViewError] = useState<string>("");

  const handleViewItem = useCallback(
    async (key: string) => {
      if (!vaultToken) {
        setError("Vault session expired. Please unlock again.");
        return;
      }
      setViewKey(key);
      setViewLoading(true);
      setViewError("");
      setViewContent("");
      try {
        // Vault unlock session only. The backend API key is attached by the
        // /api/pai proxy server-side; the browser never holds it.
        const headers: HeadersInit = {};
        headers["X-Vault-Token"] = vaultToken;
        const res = await fetch(
          `${API_URL}/vault/item/${encodeURIComponent(key)}`,
          { method: "GET", headers },
        );
        const data = await res.json();
        if (!res.ok || !data.success) {
          setViewError(data.message || "Failed to read item");
          return;
        }
        setViewContentType(data.content_type || "text");
        if (data.content_type === "json") {
          setViewContent(JSON.stringify(data.content, null, 2));
        } else {
          setViewContent(String(data.content ?? ""));
        }
      } catch (e) {
        setViewError(String(e));
      } finally {
        setViewLoading(false);
      }
    },
    [vaultToken],
  );

  const closeViewModal = useCallback(() => {
    setViewKey(null);
    setViewContent("");
    setViewError("");
    setViewContentType("text");
  }, []);

  // Determine on load whether a vault has ever been created. Without this
  // the page showed a PIN prompt for a vault that does not exist, which
  // reads as "you forgot a PIN" when in fact none was ever set.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const status = await getVaultStatus();
        if (cancelled) return;
        if (typeof status.auto_lock_seconds === "number" && status.auto_lock_seconds > 0) {
          setAutoLockSeconds(status.auto_lock_seconds);
        }
        if (status.auto_lock_line) setAutoLockLine(status.auto_lock_line);
        if (status.vault_available === false) {
          setBackendOffline(true);
          setSetupState("existing");
        } else if (status.vault_missing) {
          // The vault's drive is not plugged in: never offer a new vault.
          setError(status.vault_missing_message || "Your vault drive is not plugged in.");
          setSetupState("existing");
        } else if (status.vault_initialized === false) {
          setSetupState("new");
        } else {
          // ``true``, or absent on older backends: assume a vault exists.
          // An unlock attempt returning needs_initialization corrects this.
          setSetupState("existing");
        }
      } catch {
        if (cancelled) return;
        setBackendOffline(true);
        setSetupState("existing");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Sync redux error to local error
  useEffect(() => {
    if (reduxError) {
      setError(reduxError);
      dispatch(clearVaultError());
    }
  }, [reduxError, dispatch]);

  // Lockout countdown
  useEffect(() => {
    if (!lockoutUntil) return;
    const id = setInterval(() => {
      const remaining = Math.max(0, Math.ceil((lockoutUntil - Date.now()) / 1000));
      setLockoutRemaining(remaining);
      if (remaining <= 0) {
        setLockoutUntil(null);
        setFailedAttempts(0);
        setError("");
      }
    }, 1000);
    return () => clearInterval(id);
  }, [lockoutUntil]);

  // Recovery cooldown countdown: the backend said how long (429 retry_after).
  useEffect(() => {
    if (!recoverWaitUntil) return;
    const id = setInterval(() => {
      const left = Math.max(0, Math.ceil((recoverWaitUntil - Date.now()) / 1000));
      setRecoverWaitLeft(left);
      if (left <= 0) {
        setRecoverWaitUntil(null);
        setRecoverError("");
      }
    }, 1000);
    return () => clearInterval(id);
  }, [recoverWaitUntil]);

  // Auto-lock countdown
  const startAutoLockTimer = useCallback(() => {
    if (autoLockRef.current) clearInterval(autoLockRef.current);
    unlockTimeRef.current = Date.now();
    setAutoLockRemaining(autoLockSeconds);
    autoLockRef.current = setInterval(() => {
      const elapsed = Math.floor((Date.now() - unlockTimeRef.current) / 1000);
      const remaining = Math.max(0, autoLockSeconds - elapsed);
      setAutoLockRemaining(remaining);
      if (remaining <= 0) {
        handleLockRef.current();
      }
    }, 1000);
  }, [autoLockSeconds]);

  const stopAutoLockTimer = useCallback(() => {
    if (autoLockRef.current) {
      clearInterval(autoLockRef.current);
      autoLockRef.current = null;
    }
  }, []);

  // Cleanup on unmount
  useEffect(() => {
    return () => stopAutoLockTimer();
  }, [stopAutoLockTimer]);

  const formatTime = (seconds: number): string => {
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    return `${m}:${s.toString().padStart(2, "0")}`;
  };

  const fetchVaultContents = useCallback(
    async (token: string) => {
      try {
        // Pass the unlock session token through Redux so the list
        // actually populates — without this, getVaultContents gets a 403
        // and state.contents stays [].
        await dispatch(fetchContents(token)).unwrap();
      } catch {
        setError("Failed to load vault contents");
      }
    },
    [dispatch]
  );

  // Shared success path for both unlocking an existing vault and creating
  // a new one — /vault/unlock serves both, with `initialize: true` for setup.
  const openSession = useCallback(
    (sessionToken: string, enteredPin: string) => {
      setVaultToken(sessionToken || null);
      setFailedAttempts(0);
      setPin("");
      setConfirmPin("");
      setSetupState("existing");
      setSuccessBanner(true);

      // Dispatch to redux to sync state
      dispatch(unlock(enteredPin));

      // Fetch contents with token
      fetchVaultContents(sessionToken || "");

      // Start auto-lock timer
      startAutoLockTimer();

      // Hide success banner after 5 seconds
      setTimeout(() => setSuccessBanner(false), 5000);
    },
    [dispatch, fetchVaultContents, startAutoLockTimer],
  );

  const postUnlock = async (body: Record<string, unknown>) => {
    const headers: HeadersInit = { "Content-Type": "application/json" };

    const res = await fetch(`${API_URL}/vault/unlock`, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
    });
    return res.json();
  };

  const handleUnlock = async () => {
    if (lockoutUntil && Date.now() < lockoutUntil) return;
    if (!pin.trim()) {
      setError("Please enter your PIN");
      return;
    }

    setError("");
    setBackendOffline(false);

    try {
      const data = await postUnlock({ pin });

      if (data.success) {
        openSession(data.session_token || "", pin);
        return;
      }

      // Backend telling us no vault exists yet. Switch to the setup view
      // rather than counting this as a failed PIN attempt.
      if (data.needs_initialization) {
        setSetupState("new");
        setError("");
        setPin("");
        return;
      }

      const newAttempts = failedAttempts + 1;
      setFailedAttempts(newAttempts);
      setError(data.message || data.error || "Invalid PIN");

      // Trigger shake animation
      setShakeError(true);
      setTimeout(() => setShakeError(false), 600);

      // Lockout after MAX_ATTEMPTS
      if (newAttempts >= MAX_ATTEMPTS) {
        const until = Date.now() + LOCKOUT_SECONDS * 1000;
        setLockoutUntil(until);
        setLockoutRemaining(LOCKOUT_SECONDS);
        setError("");
      }
    } catch {
      setBackendOffline(true);
      setError("Vault service unavailable");
    }
  };

  const handleCreateVault = async () => {
    const refusal = pinRefusal(pin);
    if (refusal) {
      setError(refusal);
      return;
    }
    if (pin !== confirmPin) {
      setError("The two PINs do not match.");
      return;
    }

    setError("");
    setBackendOffline(false);
    setCreateLoading(true);

    try {
      const data = await postUnlock({ pin, initialize: true });
      if (data.success) {
        openSession(data.session_token || "", pin);
        // Shown exactly once by the backend; this page used to drop it.
        if (typeof data.recovery_code === "string" && data.recovery_code) {
          setRecoveryMessage("");
          setRecoveryNotice(data.recovery_code_notice || "");
          setRecoveryCode(data.recovery_code);
        }
      } else {
        setError(data.message || data.error || "Could not create the vault");
      }
    } catch {
      setBackendOffline(true);
      setError("Vault service unavailable");
    } finally {
      setCreateLoading(false);
    }
  };

  // A new recovery code for an open vault. It replaces the old one at once,
  // so it is asked for twice (the button, then "Replace my recovery code").
  const handleNewRecoveryCode = async () => {
    setReplaceLoading(true);
    setRecoveryMessage("");
    try {
      const res = await fetch(`${API_URL}/vault/recovery-code`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Vault-Token": vaultToken || "",
        },
        body: JSON.stringify({ replace: true }),
      });
      const data = await res.json();
      if (data.success && typeof data.recovery_code === "string" && data.recovery_code) {
        setRecoveryNotice(data.recovery_code_notice || "");
        setRecoveryCode(data.recovery_code);
      } else {
        setRecoveryMessage(data.message || "I could not make a new recovery code.");
      }
    } catch {
      setRecoveryMessage("I could not reach the vault just now.");
    } finally {
      setReplaceLoading(false);
      setReplaceAsk(false);
    }
  };

  const closeRecover = () => {
    setRecoverOpen(false);
    setRecoverCode("");
    setRecoverPin("");
    setRecoverPinAgain("");
    setRecoverError("");
  };

  // Forgot your PIN: the recovery code opens the vault and a new PIN is set in
  // the same call (POST /vault/recover). On success the old code and the old
  // PIN are both retired, and the NEW code is shown once, with the same
  // write-it-down ceremony as when the vault was made.
  const handleRecover = async () => {
    if (recoverBusy || (recoverWaitUntil && Date.now() < recoverWaitUntil)) return;
    if (!normalizeRecoveryCode(recoverCode)) {
      setRecoverError("Type the recovery code you wrote down.");
      return;
    }
    const refusal = pinRefusal(recoverPin);
    if (refusal) {
      setRecoverError(refusal);
      return;
    }
    if (recoverPin !== recoverPinAgain) {
      setRecoverError("The two new PINs are not the same. Type them again.");
      return;
    }

    setRecoverBusy(true);
    setRecoverError("");
    setBackendOffline(false);
    const newPin = recoverPin;
    // The PIN fields are cleared on every answer; the code only when it has
    // been used or refused, so a PIN the rules turn down does not mean typing
    // twenty-five characters again.
    setRecoverPin("");
    setRecoverPinAgain("");
    try {
      const { status, data } = await recoverVault(recoverCode, newPin);
      if (status === 200 && data.success && data.session_token) {
        const token = data.session_token;
        closeRecover();
        setVaultToken(token);
        setFailedAttempts(0);
        setLockoutUntil(null);
        setError("");
        setSetupState("existing");
        dispatch(sessionOpened());
        fetchVaultContents(token);
        startAutoLockTimer();
        setRecoveryMessage("");
        if (typeof data.recovery_code === "string" && data.recovery_code) {
          setRecoveryNotice(data.recovery_code_notice || "");
          setRecoveryCode(data.recovery_code);
        }
        return;
      }
      if (status === 429) {
        const wait = Math.max(1, Number(data.retry_after) || 60);
        setRecoverWaitUntil(Date.now() + wait * 1000);
        setRecoverWaitLeft(wait);
        setRecoverError(
          data.message ||
            `Too many tries. Wait ${wait} seconds and try again. This is a pause, not a verdict on your code.`,
        );
        return;
      }
      if (status === 400 && data.weak_password) {
        setRecoverError(
          `${data.message || "That PIN is not allowed."} Nothing was changed, and your recovery code still works.`,
        );
        return;
      }
      setRecoverCode("");
      setRecoverError(data.message || "The vault could not be recovered. Nothing was changed.");
    } catch {
      setRecoverError("I could not reach the vault just now. Nothing was changed.");
    } finally {
      setRecoverBusy(false);
    }
  };

  const finishRecovery = (confirmed: boolean) => {
    setRecoveryCode("");
    setRecoveryNotice("");
    setRecoveryMessage(
      confirmed
        ? "Recovery code checked. Keep it somewhere safe, away from this computer."
        : RECOVERY_NOT_CONFIRMED
    );
  };

  const handleLock = useCallback(async () => {
    stopAutoLockTimer();
    setVaultToken(null);
    setSuccessBanner(false);
    setShowStoreForm(false);

    try {
      dispatch(lock());
    } catch {
      // Lock locally even if backend fails
    }
  }, [dispatch, stopAutoLockTimer]);

  // Keep the ref in sync so the interval can call the latest handleLock
  useEffect(() => {
    handleLockRef.current = handleLock;
  }, [handleLock]);

  const handleStoreItem = async () => {
    if (!storeKey.trim()) {
      setStoreError("Key is required");
      return;
    }
    if (!storeValue.trim()) {
      setStoreError("Value is required");
      return;
    }

    setStoreLoading(true);
    setStoreError("");

    try {
      // storeVaultItem sends X-Vault-Token; without it the backend 403s.
      const data = await storeVaultItem(storeKey, storeValue, storeType, vaultToken);
      if (data.success) {
        setStoreKey("");
        setStoreValue("");
        setStoreType("text");
        setShowStoreForm(false);
        // Refresh WITH the token. Calling fetchContents() bare — as this did —
        // 403s, so the freshly stored item never showed up.
        await dispatch(fetchContents(vaultToken ?? undefined));
      } else {
        setStoreError(data.message || "Failed to store item");
      }
    } catch (e) {
      setStoreError(e instanceof Error ? e.message : "Vault service unavailable");
    } finally {
      setStoreLoading(false);
    }
  };

  const handleDeleteItem = async (key: string) => {
    setDeleteLoading(key);
    try {
      // The removeItem thunk now carries the token, so it deletes AND prunes
      // state.contents in one dispatch — no second untokened request.
      await dispatch(removeItem({ key, token: vaultToken })).unwrap();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Vault service unavailable");
    } finally {
      setDeleteLoading(null);
    }
  };

  const handleRetry = () => {
    setBackendOffline(false);
    setError("");
  };

  const typeIcon = (type: string) => {
    switch (type) {
      case "credential":
        return "key";
      case "json":
        return "data_object";
      case "document":
        return "description";
      default:
        return "description";
    }
  };

  const typeColor = (type: string) => {
    switch (type) {
      case "credential":
        return "var(--lemon)";
      case "json":
        return "var(--sky)";
      default:
        return "var(--lav)";
    }
  };

  const eyeToggle = (onClick: () => void) => (
    <button
      type="button"
      onClick={onClick}
      aria-label={showPin ? "Hide PIN" : "Show PIN"}
      className="absolute right-3 top-1/2 -translate-y-1/2 cursor-pointer"
      style={{ background: "none", border: "none", color: "var(--text3)", padding: 0 }}
    >
      <MsIcon name={showPin ? "visibility_off" : "visibility"} size={20} />
    </button>
  );

  const recoverWaiting = recoverWaitUntil !== null && recoverWaitLeft > 0;

  const recoverForm = (
    <form
      aria-labelledby="vault-recover-title"
      onSubmit={(e) => {
        e.preventDefault();
        void handleRecover();
      }}
      className="mx-auto flex max-w-xs flex-col gap-3 text-left"
    >
      <h3
        id="vault-recover-title"
        style={{ margin: "0 0 2px", fontFamily: FREDOKA, fontWeight: 600, fontSize: 20, color: "var(--text)" }}
      >
        Use your recovery code
      </h3>
      <p style={{ margin: 0, fontFamily: NUNITO, fontWeight: 500, fontSize: 14, lineHeight: 1.5, color: "var(--text2)" }}>
        Type the code you wrote down when the vault was made, and choose a new PIN. Your old PIN and
        this code both stop working, and you get a new code to write down.
      </p>
      <div>
        <label htmlFor="vault-recover-code" style={{ ...microLabel, display: "block", marginBottom: 6 }}>
          Recovery code
        </label>
        <div className="relative">
          <input
            id="vault-recover-code"
            type={showPin ? "text" : "password"}
            value={recoverCode}
            onChange={(e) => {
              setRecoverCode(e.target.value);
              setRecoverError("");
            }}
            autoComplete="off"
            autoCapitalize="characters"
            spellCheck={false}
            maxLength={64}
            placeholder="XXXXX-XXXXX-XXXXX-XXXXX-XXXXX"
            disabled={recoverBusy}
            style={{ ...pinFieldStyle, fontSize: 15, letterSpacing: ".06em" }}
          />
          {eyeToggle(() => setShowPin(!showPin))}
        </div>
        <p style={{ margin: "6px 0 0", fontFamily: NUNITO, fontSize: 13, color: "var(--text3)" }}>
          Capitals and dashes do not matter.
        </p>
      </div>
      <div>
        <label htmlFor="vault-recover-pin" style={{ ...microLabel, display: "block", marginBottom: 6 }}>
          New PIN
        </label>
        <input
          id="vault-recover-pin"
          type={showPin ? "text" : "password"}
          value={recoverPin}
          onChange={(e) => {
            setRecoverPin(e.target.value);
            setRecoverError("");
          }}
          autoComplete="new-password"
          placeholder="At least 8 characters, or 10 numbers"
          maxLength={MAX_PIN_LENGTH}
          disabled={recoverBusy}
          style={{ ...pinFieldStyle, paddingRight: 14 }}
        />
      </div>
      <div>
        <label htmlFor="vault-recover-pin-again" style={{ ...microLabel, display: "block", marginBottom: 6 }}>
          New PIN again
        </label>
        <input
          id="vault-recover-pin-again"
          type={showPin ? "text" : "password"}
          value={recoverPinAgain}
          onChange={(e) => {
            setRecoverPinAgain(e.target.value);
            setRecoverError("");
          }}
          autoComplete="new-password"
          placeholder="Type it once more"
          maxLength={MAX_PIN_LENGTH}
          disabled={recoverBusy}
          style={{ ...pinFieldStyle, paddingRight: 14 }}
        />
      </div>
      {recoverError && (
        <p role="alert" style={{ margin: 0, fontFamily: NUNITO, fontWeight: 600, fontSize: 15, color: RED }}>
          {recoverError}
        </p>
      )}
      <div className="mt-2 flex flex-wrap items-center justify-center gap-3">
        <button
          type="submit"
          disabled={recoverBusy || recoverWaiting}
          className="inline-flex cursor-pointer items-center justify-center gap-2 disabled:cursor-default disabled:opacity-50"
          style={{
            height: 42,
            padding: "0 20px",
            borderRadius: 99,
            border: "1px solid transparent",
            background: "linear-gradient(140deg,var(--pink),var(--magenta))",
            color: "#fff",
            fontFamily: NUNITO,
            fontWeight: 700,
            fontSize: 15,
          }}
        >
          <MsIcon name="key" size={19} />
          {recoverBusy
            ? "Opening…"
            : recoverWaiting
              ? `Wait ${formatTime(recoverWaitLeft)}`
              : "Open with my code"}
        </button>
        <PillButton label="Back" icon="arrow_back" tone="quiet" onClick={closeRecover} disabled={recoverBusy} />
      </div>
    </form>
  );

  const forgotLink = (
    <button
      type="button"
      onClick={() => {
        setPin("");
        setError("");
        setRecoverOpen(true);
      }}
      className="mt-4 cursor-pointer"
      style={{
        background: "none",
        border: "none",
        padding: 0,
        fontFamily: NUNITO,
        fontWeight: 700,
        fontSize: 15,
        color: "var(--pink)",
        textDecoration: "underline",
      }}
    >
      Forgot your PIN? Use your recovery code
    </button>
  );

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
      {/* ── header ─────────────────────────────────────────────────── */}
      <div className="flex flex-wrap items-end gap-5">
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
            Your vault
          </h1>
          <p
            style={{
              margin: "6px 0 0",
              fontFamily: NUNITO,
              fontWeight: 500,
              fontSize: 19,
              color: "var(--text2)",
            }}
          >
            Your private stuff, locked with a PIN. It stays on this computer.
          </p>
        </div>
        <div style={{ flex: 1 }} />
        {isUnlocked && (
          <PillButton label="Lock vault" icon="lock" tone="danger" onClick={() => handleLock()} />
        )}
      </div>

      {/* ── recovery code: shown once, typed back ─────────────────────── */}
      {recoveryCode && (
        <RecoveryCodeCeremony
          code={recoveryCode}
          notice={recoveryNotice}
          onDone={finishRecovery}
        />
      )}
      {!recoveryCode && recoveryMessage && (
        <div
          role="status"
          style={{ ...panelBase, padding: "14px 20px", fontFamily: NUNITO, fontWeight: 600, fontSize: 15, color: "var(--text2)" }}
        >
          {recoveryMessage}
        </div>
      )}

      {/* ── status tiles ───────────────────────────────────────────── */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <StatTile
          label="Vault status"
          value={
            setupState === "checking"
              ? "Checking…"
              : setupState === "new"
                ? "Not set up"
                : isUnlocked
                  ? "Unlocked"
                  : "Locked"
          }
          note={isUnlocked ? `session ${formatTime(autoLockRemaining)} left` : "PIN required"}
          icon={
            setupState === "new" ? "add_moderator" : isUnlocked ? "lock_open" : "lock"
          }
          colorVar={
            setupState === "new"
              ? "var(--sky)"
              : isUnlocked
                ? "var(--mint)"
                : "var(--lemon)"
          }
        />
        <StatTile
          label="Stored items"
          value={
            setupState === "new"
              ? "None yet"
              : isUnlocked
                ? String(contents.length)
                : "Encrypted"
          }
          note={isUnlocked ? "readable this session" : "unlock to list"}
          icon="inventory_2"
          colorVar="var(--lav)"
        />
        <StatTile
          label="Encryption"
          value="AES-128"
          note="with a tamper check · key made from your PIN"
          icon="enhanced_encryption"
          colorVar="var(--pink)"
        />
      </div>

      {/* ── backend offline banner ─────────────────────────────────── */}
      {backendOffline && (
        <div
          className="flex flex-wrap items-center gap-3"
          style={{
            ...panelBase,
            padding: "16px 20px",
            border: `1px solid color-mix(in srgb, ${RED} 40%, transparent)`,
            background: `color-mix(in srgb, ${RED} 12%, transparent)`,
          }}
        >
          <MsIcon name="error" size={22} style={{ color: RED }} />
          <span
            style={{ fontFamily: NUNITO, fontWeight: 600, fontSize: 16, color: "var(--text2)" }}
          >
            <b style={{ color: "var(--text)" }}>Vault service unavailable.</b> The backend
            may be offline or unreachable.
          </span>
          <div style={{ flex: 1 }} />
          <PillButton label="Retry" icon="refresh" tone="quiet" onClick={handleRetry} />
        </div>
      )}

      {setupState === "checking" ? (
        /* --- Still reading vault state: say nothing about locks yet --- */
        <div style={panelBase}>
          <div className="mx-auto max-w-md py-10 text-center">
            <MsIcon
              name="refresh"
              size={30}
              className="puff-motion animate-pulse"
              style={{ color: "var(--text3)" }}
            />
            <p style={{ ...microLabel, marginTop: 12 }}>Checking vault status</p>
          </div>
        </div>
      ) : setupState === "new" ? (
        /* --- No vault has ever been created --- */
        <div style={panelBase}>
          <div className="mx-auto max-w-md py-6 text-center">
            <span
              className="mx-auto mb-5 flex items-center justify-center"
              style={{
                width: 76,
                height: 76,
                borderRadius: "50%",
                background: "linear-gradient(140deg,var(--pink),var(--magenta))",
                color: "#fff",
              }}
            >
              <MsIcon name="add_moderator" size={38} />
            </span>
            <h2
              style={{
                margin: "0 0 8px",
                fontFamily: FREDOKA,
                fontWeight: 600,
                fontSize: 26,
                color: "var(--text)",
              }}
            >
              No vault yet
            </h2>
            <p
              style={{
                margin: "0 0 8px",
                fontFamily: NUNITO,
                fontWeight: 500,
                fontSize: 16,
                lineHeight: 1.5,
                color: "var(--text2)",
              }}
            >
              This computer has no vault yet. Choose a PIN to start one. What you
              put in it is encrypted and stays on this computer.
            </p>
            <p
              style={{
                margin: "0 0 22px",
                fontFamily: NUNITO,
                fontWeight: 500,
                fontSize: 14,
                color: "var(--text3)",
              }}
            >
              Your PIN is not stored anywhere. When the vault is made you get a
              recovery code to write down: it is the only other way in if you
              forget your PIN. Nobody else has a copy.
            </p>

            <div className="mx-auto flex max-w-xs flex-col gap-3 text-left">
              <div>
                <div style={{ ...microLabel, marginBottom: 6 }}>Choose a PIN</div>
                <div className="relative">
                  <input
                    type={showPin ? "text" : "password"}
                    value={pin}
                    onChange={(e) => {
                      setPin(e.target.value);
                      setError("");
                    }}
                    placeholder="At least 8 characters, or 10 numbers"
                    maxLength={MAX_PIN_LENGTH}
                    disabled={createLoading}
                    style={pinFieldStyle}
                  />
                  {eyeToggle(() => setShowPin(!showPin))}
                </div>
              </div>
              <div>
                <div style={{ ...microLabel, marginBottom: 6 }}>Confirm PIN</div>
                <input
                  type={showPin ? "text" : "password"}
                  value={confirmPin}
                  onChange={(e) => {
                    setConfirmPin(e.target.value);
                    setError("");
                  }}
                  onKeyDown={(e) => e.key === "Enter" && handleCreateVault()}
                  placeholder="Re-enter PIN"
                  maxLength={MAX_PIN_LENGTH}
                  disabled={createLoading}
                  style={{ ...pinFieldStyle, paddingRight: 14 }}
                />
              </div>
            </div>

            {error && !backendOffline && (
              <p
                style={{
                  marginTop: 14,
                  fontFamily: NUNITO,
                  fontWeight: 600,
                  fontSize: 15,
                  color: RED,
                }}
              >
                {error}
              </p>
            )}

            <div className="mt-5 flex justify-center">
              <PillButton
                label={createLoading ? "Creating…" : "Create vault"}
                icon="add_moderator"
                onClick={handleCreateVault}
                disabled={createLoading}
              />
            </div>
          </div>
        </div>
      ) : !isUnlocked ? (
        /* --- PIN Entry --- */
        <div style={panelBase}>
          <div className="mx-auto max-w-md py-6 text-center" data-walk="vault-unlock">
            <span
              className="mx-auto mb-5 flex items-center justify-center"
              style={{
                width: 76,
                height: 76,
                borderRadius: "50%",
                background: "linear-gradient(140deg,var(--pink),var(--magenta))",
                color: "#fff",
              }}
            >
              <MsIcon name="lock" size={38} />
            </span>
            <h2
              style={{
                margin: "0 0 8px",
                fontFamily: FREDOKA,
                fontWeight: 600,
                fontSize: 26,
                color: "var(--text)",
              }}
            >
              Vault is locked
            </h2>
            <p
              style={{
                margin: "0 0 22px",
                fontFamily: NUNITO,
                fontWeight: 500,
                fontSize: 16,
                lineHeight: 1.5,
                color: "var(--text2)",
              }}
            >
              Enter your PIN to open your vault. Nothing in it is sent anywhere:
              it is opened on this computer, for this session only.
            </p>

            {recoverOpen ? (
              recoverForm
            ) : (
            <>
            {/* Lockout Message */}
            {lockoutUntil && Date.now() < lockoutUntil ? (
              <div
                className="mb-2 flex items-center justify-center gap-2"
                style={{
                  padding: "16px 20px",
                  borderRadius: 20,
                  border: `1px solid color-mix(in srgb, ${RED} 40%, transparent)`,
                  background: `color-mix(in srgb, ${RED} 12%, transparent)`,
                  color: RED,
                  fontFamily: NUNITO,
                  fontWeight: 700,
                  fontSize: 15,
                }}
              >
                <MsIcon name="error" size={20} />
                Too many failed attempts. Try again in {formatTime(lockoutRemaining)}.
              </div>
            ) : (
              <>
                <div className={`mb-4 ${shakeError ? "animate-shake" : ""}`}>
                  <div className="relative mx-auto max-w-xs">
                    <input
                      type={showPin ? "text" : "password"}
                      value={pin}
                      onChange={(e) => {
                        setPin(e.target.value);
                        setError("");
                      }}
                      onKeyDown={(e) => e.key === "Enter" && handleUnlock()}
                      placeholder="Enter PIN"
                      maxLength={MAX_PIN_LENGTH}
                      disabled={vaultLoading}
                      style={pinFieldStyle}
                    />
                    {eyeToggle(() => setShowPin(!showPin))}
                  </div>
                  {error && !backendOffline && (
                    <p
                      style={{
                        marginTop: 10,
                        fontFamily: NUNITO,
                        fontWeight: 600,
                        fontSize: 15,
                        color: RED,
                      }}
                    >
                      {error}
                      {failedAttempts > 0 && failedAttempts < MAX_ATTEMPTS && (
                        <span style={{ ...microLabel, marginLeft: 8 }}>
                          {failedAttempts}/{MAX_ATTEMPTS} attempts
                        </span>
                      )}
                    </p>
                  )}
                </div>

                <div className="flex justify-center">
                  <PillButton
                    label={vaultLoading ? "Unlocking…" : "Unlock vault"}
                    icon="lock_open"
                    onClick={handleUnlock}
                    disabled={vaultLoading}
                  />
                </div>
              </>
            )}
            {forgotLink}
            </>
            )}
          </div>
        </div>
      ) : (
        /* --- Unlocked Vault Contents --- */
        <>
          {/* Success + auto-lock strip */}
          <div
            className="flex flex-wrap items-center gap-3"
            style={{
              ...panelBase,
              padding: "16px 22px",
              border: successBanner
                ? "1px solid color-mix(in srgb, var(--mint) 40%, transparent)"
                : "1px solid var(--line)",
              background: successBanner
                ? "color-mix(in srgb, var(--mint) 10%, transparent)"
                : "var(--card)",
            }}
          >
            <MsIcon
              name={successBanner ? "lock_open" : "schedule"}
              size={22}
              style={{ color: successBanner ? "var(--mint)" : "var(--text3)" }}
            />
            <span
              style={{ fontFamily: NUNITO, fontWeight: 600, fontSize: 16, color: "var(--text2)" }}
            >
              {successBanner
                ? "Unlocked. Your vault is readable for this session."
                : "Vault open."}{" "}
              Auto-locks in{" "}
              <span
                style={{
                  fontFamily: JBMONO,
                  fontWeight: 700,
                  color:
                    autoLockRemaining <= 60
                      ? RED
                      : autoLockRemaining <= 300
                        ? "var(--lemon)"
                        : "var(--mint)",
                }}
              >
                {formatTime(autoLockRemaining)}
              </span>
              {autoLockLine ? (
                <span style={{ display: "block", fontWeight: 500, fontSize: 14, color: "var(--text3)" }}>
                  {autoLockLine}
                </span>
              ) : null}
            </span>
            <div style={{ flex: 1 }} />
            {vaultToken && !recoveryCode && (
              replaceAsk ? (
                <>
                  <span style={{ fontFamily: NUNITO, fontWeight: 600, fontSize: 14, color: "var(--text2)" }}>
                    Your old recovery code will stop working.
                  </span>
                  <PillButton
                    label={replaceLoading ? "Making…" : "Replace my recovery code"}
                    icon="key"
                    onClick={() => void handleNewRecoveryCode()}
                    disabled={replaceLoading}
                  />
                  <PillButton label="Keep the old one" icon="close" tone="quiet" onClick={() => setReplaceAsk(false)} />
                </>
              ) : (
                <PillButton label="New recovery code" icon="key" onClick={() => setReplaceAsk(true)} />
              )
            )}
            {vaultToken && <span style={microLabel}>Session active</span>}
          </div>

          {/* Contents header + add button */}
          <div className="flex flex-wrap items-center gap-3">
            <div
              style={{
                fontFamily: FREDOKA,
                fontWeight: 600,
                fontSize: 24,
                color: "var(--text)",
              }}
            >
              Vault contents
            </div>
            <div style={{ flex: 1 }} />
            <PillButton
              label={showStoreForm ? "Cancel" : "Store item"}
              icon={showStoreForm ? "close" : "add"}
              tone={showStoreForm ? "quiet" : "primary"}
              onClick={() => {
                setShowStoreForm(!showStoreForm);
                setStoreError("");
              }}
            />
          </div>

          {/* Store Item Form */}
          {showStoreForm && (
            <div style={panelBase}>
              <div style={{ ...microLabel, marginBottom: 14 }}>Store new item</div>
              <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                <div>
                  <div style={{ ...microLabel, marginBottom: 6 }}>Key / name</div>
                  <input
                    type="text"
                    value={storeKey}
                    onChange={(e) => setStoreKey(e.target.value)}
                    placeholder="e.g. ha_api_token"
                    style={fieldStyle}
                  />
                </div>
                <div>
                  <div style={{ ...microLabel, marginBottom: 6 }}>Type</div>
                  <select
                    value={storeType}
                    onChange={(e) => setStoreType(e.target.value)}
                    style={fieldStyle}
                  >
                    <option value="text">Text</option>
                    <option value="json">JSON</option>
                    <option value="credential">Credential</option>
                  </select>
                </div>
              </div>
              <div className="mt-4">
                <div style={{ ...microLabel, marginBottom: 6 }}>Value</div>
                <textarea
                  value={storeValue}
                  onChange={(e) => setStoreValue(e.target.value)}
                  placeholder="This is encrypted before it touches the disk…"
                  rows={3}
                  style={{ ...fieldStyle, resize: "vertical" }}
                />
              </div>
              {storeError && (
                <p
                  style={{
                    marginTop: 10,
                    fontFamily: NUNITO,
                    fontWeight: 600,
                    fontSize: 15,
                    color: RED,
                  }}
                >
                  {storeError}
                </p>
              )}
              <div className="mt-4 flex gap-3">
                <PillButton
                  label={storeLoading ? "Storing…" : "Encrypt & store"}
                  icon="enhanced_encryption"
                  onClick={handleStoreItem}
                  disabled={storeLoading}
                />
                <PillButton
                  label="Cancel"
                  icon="close"
                  tone="quiet"
                  onClick={() => {
                    setShowStoreForm(false);
                    setStoreError("");
                  }}
                />
              </div>
            </div>
          )}

          {/* Contents list */}
          {contents.length === 0 ? (
            <div style={panelBase}>
              <div
                className="flex flex-col items-center justify-center gap-3 text-center"
                style={{
                  minHeight: 180,
                  borderRadius: 18,
                  background:
                    "repeating-linear-gradient(135deg, var(--chip) 0 8px, transparent 8px 16px)",
                }}
              >
                <MsIcon name="shield_lock" size={34} style={{ color: "var(--text3)" }} />
                <span style={{ ...microLabel, fontSize: 12 }}>
                  Nothing stored yet. Use Store item
                </span>
              </div>
            </div>
          ) : (
            <div style={{ ...panelBase, padding: "18px 20px" }}>
              <div className="flex flex-col gap-2.5">
                {contents.map((item) => {
                  // Infer a display type for items the API returns without one
                  // (raw doc_* files from the PII auto-vault pipeline).
                  const displayType =
                    item.type ||
                    (item.key.startsWith("doc_") ? "document" : "text");
                  const modifiedRaw = item.modified || item.last_modified || "";
                  const modifiedDisplay = modifiedRaw
                    ? new Date(modifiedRaw).toLocaleString()
                    : "date not known";
                  const sizeDisplay =
                    typeof item.size === "number"
                      ? `${(item.size / 1024).toFixed(1)} KB`
                      : String(item.size);
                  const tint = typeColor(displayType);
                  return (
                    <div
                      key={item.key}
                      className="flex flex-wrap items-center gap-3"
                      style={{
                        padding: "14px 16px",
                        borderRadius: 20,
                        background: "var(--card2)",
                        border: "1px solid var(--line)",
                      }}
                    >
                      <MsIcon
                        name={typeIcon(displayType)}
                        size={22}
                        style={{ color: tint, flex: "none" }}
                      />
                      <button
                        type="button"
                        onClick={() => handleViewItem(item.key)}
                        title="Click to view contents"
                        className="min-w-0 flex-1 cursor-pointer text-left"
                        style={{
                          background: "none",
                          border: "none",
                          padding: 0,
                          fontFamily: NUNITO,
                          fontWeight: 700,
                          fontSize: 16,
                          color: "var(--text)",
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                        }}
                      >
                        {itemLabel(item.key)}
                      </button>
                      <span
                        style={{
                          padding: "5px 12px",
                          borderRadius: 99,
                          background: `color-mix(in srgb, ${tint} 16%, transparent)`,
                          color: tint,
                          fontFamily: JBMONO,
                          fontWeight: 700,
                          fontSize: 11,
                          letterSpacing: ".1em",
                          textTransform: "uppercase",
                          flex: "none",
                        }}
                      >
                        {displayType === "auto_detected_pii" ? "from chat" : displayType}
                      </span>
                      <span
                        style={{
                          fontFamily: JBMONO,
                          fontWeight: 600,
                          fontSize: 12,
                          color: "var(--text3)",
                          flex: "none",
                        }}
                      >
                        {sizeDisplay} · {modifiedDisplay}
                      </span>
                      <div className="flex gap-2" style={{ flex: "none" }}>
                        <PillButton
                          icon="visibility"
                          tone="quiet"
                          title="View contents"
                          onClick={() => handleViewItem(item.key)}
                        />
                        <PillButton
                          icon="delete"
                          tone="danger"
                          title="Delete item"
                          disabled={deleteLoading === item.key}
                          onClick={() => handleDeleteItem(item.key)}
                        />
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </>
      )}

      {/* ── View Item Modal ────────────────────────────────────────────── */}
      {viewKey && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center p-4"
          style={{ background: "rgba(0,0,0,.6)", backdropFilter: "blur(6px)" }}
          onClick={closeViewModal}
        >
          <div
            className="flex max-h-[85vh] w-full max-w-4xl flex-col"
            onClick={(e) => e.stopPropagation()}
            style={{
              borderRadius: 28,
              border: "1px solid var(--line)",
              background: "var(--puff-surface-deep)",
              backdropFilter: "blur(22px)",
              WebkitBackdropFilter: "blur(22px)",
              boxShadow: "var(--shadow)",
            }}
          >
            {/* Header */}
            <div
              className="flex items-center justify-between gap-3"
              style={{ padding: "18px 24px", borderBottom: "1px solid var(--line)" }}
            >
              <div className="flex min-w-0 items-center gap-3">
                <MsIcon
                  name="shield_lock"
                  size={22}
                  style={{ color: "var(--magenta)", flex: "none" }}
                />
                <div className="min-w-0">
                  <div style={microLabel}>Vault item</div>
                  <div
                    style={{
                      fontFamily: NUNITO,
                      fontWeight: 700,
                      fontSize: 17,
                      color: "var(--text)",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {viewKey}
                  </div>
                </div>
              </div>
              <button
                type="button"
                onClick={closeViewModal}
                aria-label="Close"
                className="cursor-pointer"
                style={{
                  background: "none",
                  border: "none",
                  color: "var(--text3)",
                  padding: 4,
                }}
              >
                <MsIcon name="close" size={22} />
              </button>
            </div>

            {/* Body */}
            <div className="flex-1 overflow-auto" style={{ padding: 24 }}>
              {viewLoading && (
                <div className="py-8 text-center">
                  <MsIcon
                    name="refresh"
                    size={30}
                    className="puff-motion animate-pulse"
                    style={{ color: "var(--text3)" }}
                  />
                  <p style={{ ...microLabel, marginTop: 12 }}>Decrypting</p>
                </div>
              )}
              {!viewLoading && viewError && (
                <div
                  style={{
                    padding: "16px 18px",
                    borderRadius: 18,
                    border: `1px solid color-mix(in srgb, ${RED} 40%, transparent)`,
                    background: `color-mix(in srgb, ${RED} 12%, transparent)`,
                  }}
                >
                  <div
                    className="mb-1 flex items-center gap-2"
                    style={{ fontFamily: NUNITO, fontWeight: 700, fontSize: 16, color: RED }}
                  >
                    <MsIcon name="error" size={19} />
                    Could not load item
                  </div>
                  <div
                    style={{
                      fontFamily: NUNITO,
                      fontWeight: 500,
                      fontSize: 15,
                      color: "var(--text2)",
                    }}
                  >
                    {viewError}
                  </div>
                </div>
              )}
              {!viewLoading && !viewError && (
                <pre
                  className="whitespace-pre-wrap break-words"
                  style={{
                    margin: 0,
                    padding: 18,
                    borderRadius: 18,
                    border: "1px solid var(--line)",
                    background: "var(--card2)",
                    fontFamily: JBMONO,
                    fontSize: 13,
                    lineHeight: 1.6,
                    color: "var(--text)",
                  }}
                >
                  {viewContent}
                </pre>
              )}
            </div>

            {/* Footer */}
            <div
              className="flex flex-wrap items-center justify-between gap-3"
              style={{ padding: "14px 24px", borderTop: "1px solid var(--line)" }}
            >
              <div className="flex items-center gap-2">
                <MsIcon name="verified_user" size={16} style={{ color: "var(--mint)" }} />
                <span style={microLabel}>
                  {viewContentType === "json" ? "Structured JSON" : "Plain text"} · decrypted
                  locally · never left this device
                </span>
              </div>
              <PillButton label="Close" icon="close" tone="quiet" onClick={closeViewModal} />
            </div>
          </div>
        </div>
      )}

      {/* Shake animation style */}
      <style jsx>{`
        @keyframes shake {
          0%,
          100% {
            transform: translateX(0);
          }
          10%,
          30%,
          50%,
          70%,
          90% {
            transform: translateX(-4px);
          }
          20%,
          40%,
          60%,
          80% {
            transform: translateX(4px);
          }
        }
        .animate-shake {
          animation: shake 0.5s ease-in-out;
        }
      `}</style>
    </section>
  );
}
