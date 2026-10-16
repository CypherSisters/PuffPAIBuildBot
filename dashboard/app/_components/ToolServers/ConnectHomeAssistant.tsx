"use client";

/**
 * "Connect my Home Assistant", in one place for the Developer Brain page, the
 * Smart Home page and the wizard's last step.
 *
 * Home Assistant has its own tool server (its "Model Context Protocol Server"
 * integration, at /api/mcp). This asks for Home Assistant's address and a
 * long-lived access token, reads the change back in one sentence, and on
 * "Yes" sends both once (POST /mcp/home-assistant). The token goes straight
 * into the vault; it is typed into a hidden box, cleared after sending, and
 * never shown again. Home Assistant's tools then work in chat.
 */

import { useCallback, useEffect, useState } from "react";
import Button from "@/app/_components/Button";
import {
  connectHomeAssistant,
  listToolServers,
  removeToolServer,
  type ToolServer,
} from "@/app/_lib/services/mcpService";
import { Panel, PanelTitle, inputStyle, wellStyle } from "@/app/dashboard/apps/_components/ui";

const small: React.CSSProperties = {
  fontFamily: "var(--font-nunito), sans-serif",
  fontSize: 14,
  lineHeight: 1.5,
  color: "var(--text2)",
  margin: 0,
};

export default function ConnectHomeAssistant({ title = "Connect my Home Assistant" }: { title?: string }) {
  const [ha, setHa] = useState<ToolServer | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [address, setAddress] = useState("");
  const [token, setToken] = useState("");
  const [keepLocked, setKeepLocked] = useState(true);
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  const load = useCallback(async () => {
    const r = await listToolServers();
    const found = r.ok ? r.data.servers.find((s) => s.name === "home_assistant") ?? null : null;
    setHa(found);
    if (r.ok && r.data.home_assistant_address) {
      setAddress((now) => now || r.data.home_assistant_address);
    }
    setLoaded(true);
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const connect = async () => {
    setBusy(true);
    setNote(null);
    const r = await connectHomeAssistant(address.trim(), token, keepLocked);
    setBusy(false);
    setAsking(false);
    setToken("");
    setNote(r.data?.message || (r.ok ? "Home Assistant is connected." : "That did not work. Nothing was kept."));
    load();
  };

  const remove = async () => {
    setBusy(true);
    const r = await removeToolServer("home_assistant");
    setBusy(false);
    setNote(r.data?.message || "That did not work.");
    load();
  };

  return (
    <Panel>
      <PanelTitle>{title}</PanelTitle>
      {note && (
        <p style={{ ...small, color: "var(--text)", marginBottom: 10 }} data-ha-note="">
          {note}
        </p>
      )}
      {ha ? (
        <div style={{ display: "grid", gap: 8 }}>
          <p style={small}>
            Connected at {ha.address.replace(/\/api\/mcp$/, "")}. Your bot can use {ha.tool_count} of its tools in
            chat{ha.switched_on ? "" : ", but it is switched off on What leaves your computer"}.
          </p>
          <div className="flex gap-2">
            <Button label="Remove it" color="lightDark" small roundedFull disabled={busy} onClick={remove} />
          </div>
        </div>
      ) : (
        <div style={{ display: "grid", gap: 8, maxWidth: 520 }}>
          <p style={small}>
            {loaded ? "" : "Checking. "}
            First switch on Home Assistant&apos;s own tool server: in Home Assistant open Settings, Devices and
            services, Add integration, and pick Model Context Protocol Server. Then make a long-lived access token
            (your profile, Security) and paste it here. The token goes straight into your vault.
          </p>
          <input
            aria-label="Home Assistant address"
            placeholder="http://homeassistant.local:8123"
            value={address}
            onChange={(e) => setAddress(e.target.value)}
            style={inputStyle}
          />
          <input
            aria-label="Home Assistant token"
            type="password"
            autoComplete="off"
            placeholder="Long-lived access token"
            value={token}
            onChange={(e) => setToken(e.target.value)}
            style={inputStyle}
          />
          <label style={{ ...small, display: "flex", gap: 8, alignItems: "center" }}>
            <input type="checkbox" checked={keepLocked} onChange={(e) => setKeepLocked(e.target.checked)} />
            Keep my house working while my vault is locked
          </label>
          <div className="flex gap-2">
            <Button
              label="Connect"
              color="info"
              small
              roundedFull
              disabled={busy || !address.trim() || !token}
              onClick={() => setAsking(true)}
            />
          </div>
          {asking && (
            <div style={{ ...wellStyle, display: "grid", gap: 8 }}>
              <p style={{ ...small, color: "var(--text)" }}>
                Your bot will use Home Assistant at {address.trim()} and lock its token in your vault. Each use is
                written on What leaves your computer, where it has its own switch.
              </p>
              <div className="flex gap-2">
                <Button label="Yes, connect it" color="info" small roundedFull disabled={busy} onClick={connect} />
                <Button label="Not now" color="lightDark" small roundedFull disabled={busy} onClick={() => setAsking(false)} />
              </div>
            </div>
          )}
        </div>
      )}
    </Panel>
  );
}
