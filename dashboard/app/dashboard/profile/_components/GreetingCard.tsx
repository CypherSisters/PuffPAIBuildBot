"use client";

/**
 * The greeting card at the top of /dashboard/profile, in the design-match
 * shell's vocabulary (glass panel, Fredoka display, theme tokens).
 *
 * TWO THINGS WERE REMOVED HERE RATHER THAN RESTYLED:
 *
 *  1. A Formik "Notifications" switch that persisted NOTHING. It posted to
 *     `alert(JSON.stringify(values))` — an admin-template demo handler — so the
 *     control could be flipped, it looked saved, and no notification preference
 *     existed anywhere to save it to. A switch that lies about being wired is
 *     worse than no switch.
 *
 *  2. Nothing about accounts. There are none on this appliance (see
 *     app/login/page.tsx): access is per-device pairing, so there is no
 *     password to change and no profile to sign out of.
 *
 * The heading is "Profile", not the greeting it used to be. The CLI, the nav and
 * this card now agree on one name for the page: `puffpai` tells an owner to
 * "open Profile", and a page whose only large text was "Welcome, <name>" left
 * them looking for a heading that did not exist. The greeting kept its words; it
 * just is not the <h1> any more.
 *
 * The two pills used to be hard-coded to "Local AI" and "Encrypted". "Local AI"
 * is a true property of the build and stays. "Encrypted" was a claim about the
 * VAULT, printed whether or not a vault had ever been created — so it now comes
 * from /setup/status's `vault.initialized`, passed in by the page, and says
 * "No vault yet" when there isn't one. A padlock badge that appears regardless
 * of state teaches the owner to ignore padlocks.
 */

import type { CSSProperties } from "react";
import { useAppSelector } from "../../../_stores/hooks";
import MsIcon from "../../../_components/MsIcon";

const FREDOKA = "var(--font-fredoka), sans-serif";
const NUNITO = "var(--font-nunito), sans-serif";
const JBMONO = "var(--font-jbmono), monospace";

type Props = {
  className?: string;
  /**
   * Whether a vault has ever been created, from /setup/status.
   * `null` = not asked yet or the backend is unreachable — which renders as
   * "vault status unknown", never as a reassuring padlock.
   */
  vaultInitialized?: boolean | null;
};

function Pill({
  icon,
  label,
  color,
}: {
  icon: string;
  label: string;
  color: string;
}) {
  return (
    <span
      className="inline-flex items-center gap-2"
      style={{
        padding: "9px 18px",
        borderRadius: 99,
        background: `color-mix(in srgb, ${color} 16%, transparent)`,
        border: `1px solid color-mix(in srgb, ${color} 34%, transparent)`,
        color,
        fontFamily: JBMONO,
        fontWeight: 700,
        fontSize: 12,
        letterSpacing: ".08em",
        textTransform: "uppercase",
      }}
    >
      <MsIcon name={icon} size={17} />
      {label}
    </span>
  );
}

const panelBase: CSSProperties = {
  borderRadius: 28,
  border: "1px solid var(--line)",
  background: "var(--card)",
  backdropFilter: "blur(18px)",
  WebkitBackdropFilter: "blur(18px)",
  padding: "26px 28px",
  boxShadow: "var(--shadow)",
};

const GreetingCard = ({ className, vaultInitialized = null }: Props) => {
  const userName = useAppSelector((state) => state.main.userName);

  const vaultPill =
    vaultInitialized === true
      ? { icon: "verified_user", label: "Vault encrypted", color: "var(--mint)" }
      : vaultInitialized === false
        ? { icon: "add_moderator", label: "No vault yet", color: "var(--lemon)" }
        : { icon: "help", label: "Vault status unknown", color: "var(--text3)" };

  return (
    <div className={className} style={panelBase}>
      <div className="flex flex-col items-center gap-6 sm:flex-row sm:items-center">
        {/* Puff, the mascot — the same image the dashboard greets you with. */}
        <div className="relative shrink-0">
          <div
            className="flex items-center justify-center"
            style={{
              width: 108,
              height: 108,
              borderRadius: "50%",
              background: "linear-gradient(140deg,var(--pink),var(--magenta))",
              boxShadow: "0 0 36px color-mix(in srgb, var(--pink) 40%, transparent)",
            }}
          >
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src="/puffpai-logo.png"
              alt="Puff, the PuffPAI cloud mascot"
              style={{ width: 72, height: 72, objectFit: "contain" }}
            />
          </div>
        </div>

        <div className="min-w-0 flex-1 text-center sm:text-left">
          <h1
            style={{
              margin: 0,
              fontFamily: FREDOKA,
              fontWeight: 600,
              fontSize: "clamp(24px,2.2vw,34px)",
              letterSpacing: "-.5px",
              color: "var(--text)",
            }}
          >
            Profile
          </h1>
          <p
            style={{
              margin: "6px 0 16px",
              fontFamily: NUNITO,
              fontWeight: 500,
              fontSize: 17,
              lineHeight: 1.45,
              color: "var(--text2)",
            }}
          >
            Welcome, {userName}. Everything on this page is stored on your own
            box. No account, no password, nothing to sign out of.
          </p>
          <div className="flex flex-wrap justify-center gap-2.5 sm:justify-start">
            <Pill icon="memory" label="Local AI" color="var(--sky)" />
            <Pill {...vaultPill} />
          </div>
        </div>
      </div>
    </div>
  );
};

export default GreetingCard;
