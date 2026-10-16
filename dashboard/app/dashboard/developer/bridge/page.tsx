"use client";

/**
 * Developer, Zone 3: the Bridge.
 *
 * Shows the internet setting and the Bridge choice in the backend's words
 * (/build/self), how many outside services are on (/system/privacy), the
 * paired devices (the same card Settings shows, with its two-step Revoke, but
 * without the pairing code), and, as plain text, what BuildBot does and does
 * not do with your own gear.
 *
 * Nothing secret appears on a Developer page, so adding a device is a link to
 * the pairing page (/pair). That page shows the code only to a device that is
 * already paired, the same as everywhere else; this page asks nothing extra
 * and shows nothing that page would not.
 *
 * Switching an outside service on stays on "What leaves your computer", with
 * the yes it asks for there; this page links to it instead of copying it.
 * Changing the internet setting or the Bridge choice is a setup question.
 */

import { useEffect, useState } from "react";
import { getBuildSelf, type BuildSelf } from "@/app/_lib/services/buildService";
import { AppHeader, AppPage, Panel, PanelTitle } from "@/app/dashboard/apps/_components/ui";
import PairedDevicesCard from "@/app/dashboard/settings/PairedDevicesCard";
import UseCliPanel from "../_components/UseCliPanel";
import { BackToDeveloper, Fact, GoTo, small } from "../_components/parts";
import { getPrivacy, internetWords, type PrivacyState } from "../developerService";
import { consentLine, onCount, onNames } from "../../_lib/consentWords";

export default function DeveloperBridgePage() {
  const [build, setBuild] = useState<BuildSelf | null>(null);
  const [privacy, setPrivacy] = useState<PrivacyState | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let alive = true;
    Promise.all([getBuildSelf(), getPrivacy()]).then(([b, p]) => {
      if (!alive) return;
      setBuild(b);
      setPrivacy(p);
      setLoaded(true);
    });
    return () => {
      alive = false;
    };
  }, []);

  const bridge = build?.zones.bridge;
  // Count, names and the consent line from the backend's one consent
  // summary: the same as "What leaves your computer" and the home page.
  const on = onCount(privacy);
  const names = onNames(privacy);
  const promise = consentLine(privacy);

  return (
    <AppPage>
      <BackToDeveloper />
      <AppHeader title="Zone 3: Bridge" lede="What may leave your computer, who can switch things on, and your own gear.">
        <UseCliPanel page="bridge" />
      </AppHeader>

      {loaded && !build && (
        <Panel accent="--lemon">
          <p style={small}>Your bot did not answer, so the Bridge cannot be shown right now.</p>
        </Panel>
      )}

      {bridge && (
        <Panel>
          <PanelTitle>Right now</PanelTitle>
          <Fact label="Internet">{internetWords(bridge.run_egress)}</Fact>
          <Fact label="Bridge choice">{bridge.label || "Not chosen yet"}</Fact>
          {bridge.summary && <p style={{ ...small, marginTop: 4 }}>{bridge.summary}</p>}
          {promise && (
            <p data-consent-line style={{ ...small, color: "var(--text)", fontWeight: 700, marginTop: 4 }}>
              {promise}
            </p>
          )}
          <Fact label="Outside services on">
            <span data-outside-count={on === null ? "unknown" : on}>
              {on === null ? "Could not be read" : on === 0 ? "None" : String(on)}
            </span>
          </Fact>
          {names.length > 0 && <Fact label="Which ones">{names.join(", ")}</Fact>}
          <p style={{ ...small, marginTop: 8 }}>
            To change the internet setting or the Bridge choice, run Setup again. Use CLI shows the command.
          </p>
        </Panel>
      )}

      <div style={{ display: "grid", gap: 10 }}>
        <GoTo href="/dashboard/outside" title="What leaves your computer">
          Every outside service with its switch, and every request your bot made in the last day.
        </GoTo>
        <GoTo href="/dashboard/keys" title="Sign-up keys page">
          The free keys some outside services need, kept in your vault.
        </GoTo>
      </div>

      <Panel>
        <PanelTitle>Your own gear</PanelTitle>
        <div style={{ display: "grid", gap: 8 }}>
          <p style={small}>
            <strong>Your firewall or router.</strong> BuildBot does not change your firewall. Your firewall keeps
            doing its job. A list of websites to copy into it is not built yet.
          </p>
          <p style={small}>
            <strong>A filter on your home network</strong> (Pi-hole, AdGuard and the like) works on its own, next to
            your bot. BuildBot does not change it.
          </p>
          <p style={small}>
            <strong>A proxy.</strong> Proxies are not supported. Your bot connects straight to each website, so its
            safety checks can see where every request really goes.
          </p>
          <p style={small}>
            <strong>Your home network.</strong> The Bridge never scans your home network or lists the devices on it.
          </p>
        </div>
      </Panel>

      <PairedDevicesCard botName={build?.build.name} showAddDevice={false} />

      <GoTo href="/pair" title="Add a device">
        The pairing page shows the code a new screen or phone types in to talk to{" "}
        {build?.build.name?.trim() || "your bot"}.
      </GoTo>
    </AppPage>
  );
}
