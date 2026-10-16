"use client";

/**
 * /dashboard/profile — your name, who greets you, and how this browser is
 * talking to the box. Converted off the admin template (SectionMain,
 * SectionTitleLineWithButton, CardBox, Button, Icon+mdi, dark: pairs) into the
 * design-match shell.
 *
 * THE PASSWORD FORM IS GONE: it was the worst flaw on this page.
 *
 * `_components/PasswordForm.tsx` was upstream admin-template scaffolding,
 * rendered here under the comment "Password Form (kept as-is)". Its submit
 * handler was `alert(JSON.stringify(values, null, 2))`: type a password, press
 * Save, and the appliance printed it back in a modal dialog for anyone standing
 * at the kiosk to read. Nothing was hashed, stored or checked, because there was
 * nothing to check against — this appliance HAS NO ACCOUNTS (see
 * app/login/page.tsx; access is per-device pairing). So the form is deleted, not
 * restyled: a password field that cannot authenticate anything, and echoes what
 * it is given, only ever teaches the household to type real passwords into it.
 * The same handler in _components/GreetingCard.tsx (a "Notifications" switch
 * that persisted nothing) went with it.
 *
 * Data contracts unchanged: GET/PUT /profile, GET/PUT /profile/ai, and
 * checkHealth() against /health. The vault pill on the greeting card is new —
 * it reads `vault.initialized` from /setup/status instead of the old
 * unconditional "Encrypted" badge.
 */

import { useState, useEffect, useCallback, type CSSProperties } from "react";
import MsIcon from "../../_components/MsIcon";
import GreetingCard from "./_components/GreetingCard";
import ProfileForm from "./_components/ProfileForm";
import AICustomizationForm from "./_components/AICustomizationForm";
import { getProfile, getAISettings } from "../../_lib/services/profileService";
import { getSetupStatus } from "../../_lib/services/setupService";
import { checkHealth, API_URL } from "../../_lib/api";
import type { UserProfile, AISettings } from "../../_interfaces/api";

/* ── shell vocabulary ───────────────────────────────────────────────── */

const FREDOKA = "var(--font-fredoka), sans-serif";
const NUNITO = "var(--font-nunito), sans-serif";
const JBMONO = "var(--font-jbmono), monospace";
const RED = "#FF5C7A";

const STRIPES =
  "repeating-linear-gradient(135deg, var(--chip) 0 8px, transparent 8px 16px)";

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

function PillButton({
  label,
  icon,
  onClick,
  disabled,
  tone = "quiet",
}: {
  label: string;
  icon: string;
  onClick: () => void;
  disabled?: boolean;
  tone?: "primary" | "quiet";
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="inline-flex cursor-pointer items-center gap-2 transition-transform hover:-translate-y-0.5 disabled:cursor-default disabled:opacity-50"
      style={{
        height: 42,
        padding: "0 20px",
        borderRadius: 99,
        border: tone === "primary" ? "1px solid transparent" : "1px solid var(--line)",
        background:
          tone === "primary"
            ? "linear-gradient(140deg,var(--pink),var(--magenta))"
            : "var(--card2)",
        color: tone === "primary" ? "#fff" : "var(--text2)",
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

function SectionHeading({ icon, children }: { icon: string; children: React.ReactNode }) {
  return (
    <div className="mb-5 flex items-center gap-2.5">
      <MsIcon name={icon} size={22} style={{ color: "var(--pink)" }} />
      <div
        style={{
          fontFamily: FREDOKA,
          fontWeight: 600,
          fontSize: 24,
          color: "var(--text)",
        }}
      >
        {children}
      </div>
    </div>
  );
}

/** One label/value row inside a readout card. */
function InfoRow({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div
      className="flex flex-wrap items-center justify-between gap-2"
      style={{
        padding: "13px 16px",
        borderRadius: 16,
        background: "var(--card2)",
        border: "1px solid var(--line)",
      }}
    >
      <span style={microLabel}>{label}</span>
      {children}
    </div>
  );
}

/** Striped placeholder + honest microcopy, the shell's loading/empty idiom. */
function Striped({ note, lines = 2 }: { note: string; lines?: number }) {
  return (
    <div style={panelBase} className="flex flex-col gap-3">
      {Array.from({ length: lines }, (_, i) => (
        <div key={i} style={{ height: 58, borderRadius: 18, background: STRIPES }} />
      ))}
      <div style={{ ...microLabel, fontSize: 10 }}>{note}</div>
    </div>
  );
}

export default function ProfilePage() {
  // --- Profile & AI settings state ---
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [aiSettings, setAiSettings] = useState<AISettings | null>(null);
  const [loadingProfile, setLoadingProfile] = useState(true);
  const [loadingAI, setLoadingAI] = useState(true);
  const [profileError, setProfileError] = useState<string | null>(null);
  const [aiError, setAiError] = useState<string | null>(null);

  // Whether a vault has ever been created — drives the greeting card's second
  // pill. null = not asked yet / backend unreachable, which renders as unknown.
  const [vaultInitialized, setVaultInitialized] = useState<boolean | null>(null);

  // --- Connection state ---
  // There is deliberately no API-key state here. The dashboard server holds the
  // backend key (PUFFPAI_API_KEY, see dashboard/.env.example) and attaches it in
  // the /api/pai proxy; the browser never receives or stores it.
  const [testingConnection, setTestingConnection] = useState(false);
  const [connectionOk, setConnectionOk] = useState<boolean | null>(null);
  const [lastSync, setLastSync] = useState<string | null>(null);

  // --- Fetch profile ---
  const fetchProfile = useCallback(async () => {
    setLoadingProfile(true);
    setProfileError(null);
    try {
      const data = await getProfile();
      setProfile(data);
      setLastSync(new Date().toLocaleTimeString());
    } catch (err) {
      setProfileError(err instanceof Error ? err.message : "Failed to load profile");
    } finally {
      setLoadingProfile(false);
    }
  }, []);

  // --- Fetch AI settings ---
  const fetchAISettings = useCallback(async () => {
    setLoadingAI(true);
    setAiError(null);
    try {
      const data = await getAISettings();
      setAiSettings(data);
    } catch (err) {
      setAiError(err instanceof Error ? err.message : "Failed to load AI settings");
    } finally {
      setLoadingAI(false);
    }
  }, []);

  /** Vault state for the greeting pill. `ollama=0` skips the model probe — this
   *  page only needs one boolean and should not wait on a network round trip to
   *  Ollama to draw a badge. */
  const fetchVaultState = useCallback(async () => {
    const reply = await getSetupStatus(false);
    setVaultInitialized(
      reply.ok && typeof reply.data?.vault?.initialized === "boolean"
        ? reply.data.vault.initialized
        : null,
    );
  }, []);

  useEffect(() => {
    fetchProfile();
    fetchAISettings();
    void fetchVaultState();
  }, [fetchProfile, fetchAISettings, fetchVaultState]);

  // --- Test connection ---
  const handleTestConnection = async () => {
    setTestingConnection(true);
    const healthy = await checkHealth();
    setConnectionOk(healthy);
    if (healthy) {
      setLastSync(new Date().toLocaleTimeString());
    }
    setTestingConnection(false);
  };

  // --- Callbacks from child forms to refresh data ---
  const onProfileSaved = (updated: UserProfile) => {
    setProfile(updated);
    setLastSync(new Date().toLocaleTimeString());
  };

  const onAISettingsSaved = (updated: AISettings) => {
    setAiSettings(updated);
    setLastSync(new Date().toLocaleTimeString());
  };

  const statusColor =
    connectionOk === true ? "var(--mint)" : connectionOk === false ? RED : "var(--text3)";
  const statusText =
    connectionOk === true ? "Connected" : connectionOk === false ? "Disconnected" : "Not checked";

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
      {/* ── greeting ───────────────────────────────────────────────── */}
      <GreetingCard vaultInitialized={vaultInitialized} />

      {/* ── you + connection ───────────────────────────────────────── */}
      <div className="grid grid-cols-1 items-start gap-5 lg:grid-cols-2">
        {loadingProfile ? (
          <Striped note="Reading your profile…" />
        ) : profileError ? (
          <div style={panelBase}>
            <SectionHeading icon="person">Who you are</SectionHeading>
            <div className="flex flex-col items-start gap-3">
              <div
                className="flex items-center gap-2"
                style={{ fontFamily: NUNITO, fontWeight: 600, fontSize: 16, color: RED }}
              >
                <MsIcon name="error" size={20} />
                {profileError}
              </div>
              <PillButton label="Retry" icon="refresh" onClick={fetchProfile} />
            </div>
          </div>
        ) : (
          <ProfileForm profile={profile!} onSaved={onProfileSaved} />
        )}

        {/* How this browser reaches the box */}
        <div style={panelBase}>
          <SectionHeading icon="lan">This connection</SectionHeading>

          <p
            style={{
              margin: "0 0 16px",
              fontFamily: NUNITO,
              fontWeight: 500,
              fontSize: 16,
              lineHeight: 1.5,
              color: "var(--text2)",
            }}
          >
            The backend key lives on the dashboard server and is attached to every
            request there. Your browser never receives it, so there is nothing on
            this page to copy, paste or leak.
          </p>

          <div className="flex flex-col gap-2.5">
            <InfoRow label="Backend">
              <span
                style={{
                  fontFamily: JBMONO,
                  fontWeight: 700,
                  fontSize: 13,
                  color: "var(--text)",
                }}
              >
                {API_URL}
              </span>
            </InfoRow>
            <InfoRow label="Status">
              <span className="flex items-center gap-2">
                <span
                  style={{
                    width: 9,
                    height: 9,
                    borderRadius: "50%",
                    background: statusColor,
                    display: "inline-block",
                  }}
                />
                <span
                  style={{
                    fontFamily: JBMONO,
                    fontWeight: 700,
                    fontSize: 13,
                    color: statusColor,
                  }}
                >
                  {statusText}
                </span>
              </span>
            </InfoRow>
            <InfoRow label="Last read">
              <span
                style={{
                  fontFamily: JBMONO,
                  fontWeight: 700,
                  fontSize: 13,
                  color: lastSync ? "var(--text)" : "var(--text3)",
                }}
              >
                {lastSync || "never"}
              </span>
            </InfoRow>
          </div>

          <div className="mt-5 flex flex-wrap gap-3">
            <PillButton
              label={testingConnection ? "Testing…" : "Test connection"}
              icon="network_check"
              tone="primary"
              onClick={handleTestConnection}
              disabled={testingConnection}
            />
            <PillButton
              label="Refresh all"
              icon="refresh"
              onClick={() => {
                handleTestConnection();
                fetchProfile();
                fetchAISettings();
                void fetchVaultState();
              }}
            />
          </div>
        </div>
      </div>

      {/* ── AI personality ─────────────────────────────────────────── */}
      <div
        style={{
          fontFamily: FREDOKA,
          fontWeight: 600,
          fontSize: "clamp(22px,2vw,30px)",
          letterSpacing: "-.4px",
          color: "var(--text)",
          margin: "4px 0 -4px",
        }}
      >
        Your assistant
      </div>

      {loadingAI ? (
        <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
          <Striped note="Reading assistant settings…" lines={3} />
          <Striped note="Reading model settings…" lines={3} />
        </div>
      ) : aiError ? (
        <div style={panelBase}>
          <div className="flex flex-col items-start gap-3">
            <div
              className="flex items-center gap-2"
              style={{ fontFamily: NUNITO, fontWeight: 600, fontSize: 16, color: RED }}
            >
              <MsIcon name="error" size={20} />
              {aiError}
            </div>
            <PillButton label="Retry" icon="refresh" onClick={fetchAISettings} />
          </div>
        </div>
      ) : (
        <AICustomizationForm settings={aiSettings!} onSaved={onAISettingsSaved} />
      )}
    </section>
  );
}
