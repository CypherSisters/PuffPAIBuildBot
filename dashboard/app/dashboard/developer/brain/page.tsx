"use client";

/**
 * Developer, Zone 2: the Brain, with Home Assistant.
 *
 * Shows the model in use, where it runs and where that address came from,
 * whether it answers, this computer's memory and processor, and which models
 * fit. Three changes:
 *
 *  - Pick another model that is already on this computer
 *    (POST /setup/ollama with the model name only; the address is never
 *    changed from here, because pointing the bot at another computer lets
 *    more out, and that stays a setup question). It is used after a restart.
 *  - Connect my Home Assistant (ConnectHomeAssistant: its own tool server at
 *    /api/mcp, POST /mcp/home-assistant). The token is typed into a hidden
 *    box, sent once into the vault, never shown again and never put in a
 *    command. Home Assistant's tools then work in chat.
 *  - Add my own tool (AddOwnTool: a tool server, or a skill drafted from a
 *    docs page that she approves).
 *
 * Each change is read back in a sentence first and needs a "Yes".
 */

import { useCallback, useEffect, useState } from "react";
import Button from "@/app/_components/Button";
import AddOwnTool from "@/app/_components/ToolServers/AddOwnTool";
import ConnectHomeAssistant from "@/app/_components/ToolServers/ConnectHomeAssistant";
import { getBuildSelf, type BuildSelf } from "@/app/_lib/services/buildService";
import { getBrainMenu, saveOllama, type BrainMenu } from "@/app/_lib/services/setupService";
import { AppHeader, AppPage, Panel, PanelTitle, wellStyle } from "@/app/dashboard/apps/_components/ui";
import UseCliPanel from "../_components/UseCliPanel";
import { BackToDeveloper, Fact, GoTo, Path, small } from "../_components/parts";
import { getBrainPlace, type BrainPlace } from "../developerService";

type Ask = { kind: "model"; id: string; label: string };

export default function DeveloperBrainPage() {
  const [place, setPlace] = useState<BrainPlace | null>(null);
  const [build, setBuild] = useState<BuildSelf | null>(null);
  const [menu, setMenu] = useState<BrainMenu | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [ask, setAsk] = useState<Ask | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  const load = useCallback(async () => {
    const [p, b] = await Promise.all([getBrainPlace(), getBuildSelf()]);
    setPlace(p);
    setBuild(b);
    setLoaded(true);
    const m = await getBrainMenu();
    setMenu(m.ok ? m.data.menu : null);
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const brain = build?.zones.brain;
  const device = build?.device;
  const answering = brain?.backend_status === "connected";

  const useModel = async (id: string) => {
    setBusy(true);
    setNote(null);
    const r = await saveOllama(id);
    setBusy(false);
    setAsk(null);
    setNote(
      r.ok
        ? `Saved. ${place?.restart_line ?? "A new model is used after your bot restarts."} Use CLI shows the stop and start commands.`
        : "That did not save. Nothing was changed.",
    );
    load();
  };

  const options = menu?.options ?? [];

  return (
    <AppPage>
      <BackToDeveloper />
      <AppHeader title="Zone 2: Brain" lede="The model your bot thinks with, where it runs, Home Assistant and your own tools.">
        <UseCliPanel page="brain" />
      </AppHeader>

      {loaded && !place && (
        <Panel accent="--lemon">
          <p style={small}>Your bot did not answer, so the Brain cannot be shown right now.</p>
        </Panel>
      )}
      {note && (
        <Panel accent="--sky">
          <p style={{ ...small, color: "var(--text)" }} data-brain-note="">
            {note}
          </p>
        </Panel>
      )}

      {place && (
        <Panel>
          <PanelTitle>Right now</PanelTitle>
          <Fact label="Model in use">{place.model || "None chosen yet"}</Fact>
          {place.saved_model && place.saved_model !== place.model && (
            <Fact label="After a restart">{place.saved_model}</Fact>
          )}
          <Fact label="Runs at">
            <Path>{place.address}</Path> (from {place.address_from})
          </Fact>
          <Fact label="Answering">{brain ? (answering ? "Yes" : "Not right now") : "Could not be checked"}</Fact>
          {place.model_file && (
            <Fact label="Model file">
              <Path>{place.model_file}</Path>
            </Fact>
          )}
          {brain?.limits && <Fact label="What it can do">{brain.limits}</Fact>}
          {device && (
            <Fact label="This computer">
              {[
                device.total_ram_gb ? `${device.total_ram_gb} GB of memory` : null,
                device.cpu_model,
                device.logical_cores ? `${device.logical_cores} cores` : null,
                device.accelerator_name,
              ]
                .filter(Boolean)
                .join(", ") || "Could not be read"}
            </Fact>
          )}
          <p style={{ ...small, marginTop: 8 }}>{place.only_ollama_line}</p>
        </Panel>
      )}

      <Panel>
        <PanelTitle>Pick another model</PanelTitle>
        {!menu && <p style={small}>{loaded ? "The list of models that fit could not be read." : "Checking which models fit."}</p>}
        {menu?.message && <p style={small}>{menu.message}</p>}
        <div style={{ display: "grid", gap: 8 }}>
          {options.map((o) => {
            const inUse = o.id === place?.saved_model;
            return (
              <div key={o.id} className="flex flex-wrap items-center gap-3" style={wellStyle}>
                <div style={{ flex: 1, minWidth: 200 }}>
                  <div style={{ ...small, color: "var(--text)", fontWeight: 700 }}>{o.label}</div>
                  <p style={{ ...small, fontSize: 13 }}>
                    {o.fit === "comfortable" ? "Fits comfortably" : "Fits, but tight"}
                    {o.disk_gb ? `, ${o.disk_gb} GB on disk` : ""}
                    {o.installed ? "" : ". Not on this computer yet: downloading it from this page is not built yet."}
                  </p>
                </div>
                {inUse ? (
                  <span style={{ ...small, fontWeight: 700 }}>Chosen</span>
                ) : o.installed ? (
                  <Button
                    label="Use this one"
                    color="lightDark"
                    small
                    roundedFull
                    disabled={busy}
                    onClick={() => setAsk({ kind: "model", id: o.id, label: o.label })}
                  />
                ) : null}
              </div>
            );
          })}
        </div>
        {ask?.kind === "model" && (
          <div style={{ ...wellStyle, marginTop: 10, display: "grid", gap: 8 }}>
            <p style={{ ...small, color: "var(--text)" }}>
              Your bot will think with {ask.label} after it restarts. Its address stays the same.
            </p>
            <div className="flex gap-2">
              <Button label="Yes, use it" color="info" small roundedFull disabled={busy} onClick={() => useModel(ask.id)} />
              <Button label="Not now" color="lightDark" small roundedFull disabled={busy} onClick={() => setAsk(null)} />
            </div>
          </div>
        )}
      </Panel>

      {place?.home_assistant?.problem && (
        <Panel>
          <PanelTitle>Home Assistant</PanelTitle>
          {/* A kept token this computer cannot open: said, never "set up". */}
          <p role="status" style={small}>{place.home_assistant.problem}</p>
        </Panel>
      )}
      <ConnectHomeAssistant title="Home Assistant" />
      <AddOwnTool hideHomeAssistant />

      <div style={{ display: "grid", gap: 10 }}>
        <GoTo href="/dashboard/homeassistant" title="Smart Home page">
          Devices waiting for your yes, and everything Home Assistant shares with your bot.
        </GoTo>
        <GoTo href="/dashboard/device" title="Device page">
          How hard this computer is working right now.
        </GoTo>
      </div>
    </AppPage>
  );
}
