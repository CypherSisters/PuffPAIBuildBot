"use client";

/**
 * Developer, Zone 1: the Vault.
 *
 * Shows what is true today: locked or open, the folder it lives in and
 * whether that drive is there, items waiting for the next unlock, how it is
 * locked, and what stays outside it. Nothing secret is ever shown here: no
 * PIN, no recovery code, no item names, no key values.
 *
 * Every change links to the page that already makes it, with the yes that
 * page asks for (lock and unlock, a new recovery code and a forgotten PIN on
 * the Vault page; keys that keep working while the vault is locked on the
 * Sign-up keys page). Where the vault lives is changed here (ChangePlace),
 * from a paired device, with the PIN.
 */

import { useCallback, useEffect, useState } from "react";
import { AppHeader, AppPage, Panel, PanelTitle } from "@/app/dashboard/apps/_components/ui";
import UseCliPanel from "../_components/UseCliPanel";
import { BackToDeveloper, Fact, GoTo, Path, small } from "../_components/parts";
import { getPrivacy, getVaultPlace, vaultWords, type PrivacyState, type VaultPlace } from "../developerService";
import { getVaultLocation, type VaultLocation } from "@/app/_lib/services/vaultPlaceService";
import ChangePlace from "./ChangePlace";

export default function DeveloperVaultPage() {
  const [place, setPlace] = useState<VaultPlace | null>(null);
  const [privacy, setPrivacy] = useState<PrivacyState | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [location, setLocation] = useState<VaultLocation | null>(null);

  const load = useCallback(() => {
    let alive = true;
    Promise.all([getVaultPlace(), getPrivacy(), getVaultLocation()]).then(([v, p, l]) => {
      if (!alive) return;
      setPlace(v);
      setPrivacy(p);
      setLocation(l);
      setLoaded(true);
    });
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => load(), [load]);

  const waiting = place?.waiting ?? 0;

  return (
    <AppPage>
      <BackToDeveloper />
      <AppHeader title="Zone 1: Vault" lede="Where your private things are kept, and how they are locked.">
        <UseCliPanel page="vault" />
      </AppHeader>

      {loaded && !place && (
        <Panel accent="--lemon">
          <p style={small}>Your bot did not answer, so the vault cannot be shown right now.</p>
        </Panel>
      )}

      {place && (
        <Panel>
          <PanelTitle>Right now</PanelTitle>
          <Fact label="Vault">{place.drive_there ? vaultWords(place.state) : place.line}</Fact>
          <Fact label="Kept in">
            <Path>{place.folder}</Path>
          </Fact>
          <Fact label="That folder">{place.line}</Fact>
          {location && (
            <Fact label="Kept">
              {location.is_default
                ? "On this computer, in BuildBot's own folder"
                : location.network
                  ? `In a network folder. ${location.network_line}`
                  : "In a folder you chose"}
            </Fact>
          )}
          {place.state === "locked" && (
            <Fact label="Waiting">
              {waiting === 0
                ? "Nothing is waiting to go into the vault."
                : `${waiting} ${waiting === 1 ? "thing waits" : "things wait"} to go into the vault the next time you open it.`}
            </Fact>
          )}
        </Panel>
      )}

      <Panel>
        <PanelTitle>How it is locked</PanelTitle>
        <p style={small}>
          Your PIN goes through 600,000 rounds of scrambling before it can open the lock, so guessing is slow.
          Your PIN and your recovery code are never written down anywhere on this computer.
        </p>
        <p style={{ ...small, marginTop: 8 }}>
          Password managers and programs that lock a whole drive (BitLocker, VeraCrypt, KeePass, Bitwarden,
          1Password) cannot be used as the vault itself. Your vault can live inside a drive you already lock:
          unlock that drive yourself first, then BuildBot keeps its vault inside it.
        </p>
      </Panel>

      {location && <ChangePlace place={location} onChanged={() => load()} />}

      {privacy?.lines && privacy.lines.length > 0 && (
        <Panel>
          <PanelTitle>What stays private, and what does not</PanelTitle>
          <ul style={{ margin: 0, paddingLeft: 18, display: "grid", gap: 4 }}>
            {privacy.lines.map((line) => (
              <li key={line} style={small}>
                {line}
              </li>
            ))}
          </ul>
        </Panel>
      )}

      <div style={{ display: "grid", gap: 10 }}>
        <GoTo href="/dashboard/vault" title="Vault page">
          Lock or open the vault, make a new recovery code, or open it if you forgot your PIN.
        </GoTo>
        <GoTo href="/dashboard/keys" title="Sign-up keys page">
          Keys kept in the vault, and which ones keep working while it is locked.
        </GoTo>
      </div>
    </AppPage>
  );
}
