"use client";

/**
 * "Add my own tool": the tools her bot has from tool servers, and two ways to
 * add one.
 *
 *  - A tool server: a web address (with an optional password, which goes into
 *    the vault) or a command that starts one on this computer
 *    (POST /mcp/servers). Its tools then work in chat.
 *  - A docs page (or pasted docs): the bot drafts a skill, she reads it (and
 *    may change it), and only "Yes, add this skill" keeps it
 *    (POST /mcp/learn, then /mcp/learn/<name>/keep).
 *
 * Each change is read back in one sentence and needs a "Yes". Each tool server
 * gets its own switch on What leaves your computer.
 */

import { useCallback, useEffect, useState } from "react";
import Button from "@/app/_components/Button";
import {
  addToolServer,
  discardLearnedSkill,
  keepLearnedSkill,
  learnFromDocs,
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

export default function AddOwnTool({ hideHomeAssistant = false }: { hideHomeAssistant?: boolean }) {
  const [servers, setServers] = useState<ToolServer[]>([]);
  const [ready, setReady] = useState(true);
  const [name, setName] = useState("");
  const [target, setTarget] = useState("");
  const [password, setPassword] = useState("");
  const [asking, setAsking] = useState(false);
  const [docs, setDocs] = useState("");
  const [skillName, setSkillName] = useState("");
  const [draft, setDraft] = useState<{ name: string; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  const load = useCallback(async () => {
    const r = await listToolServers();
    if (r.ok) {
      setServers(r.data.servers);
      setReady(r.data.ready);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // "Paste a link" in Add a tool sends a link that is not a project page
  // here (?tool=...): a tool server's address goes in the address box, any
  // other page in the docs box. Nothing is added until she says yes.
  useEffect(() => {
    const sent = new URLSearchParams(window.location.search).get("tool");
    if (!sent || !/^https?:\/\//i.test(sent.trim())) return;
    const link = sent.trim().slice(0, 500);
    if (/\/(mcp|sse)\/?$/i.test(link)) setTarget(link);
    else setDocs(link);
    document.getElementById("add-own-tool")?.scrollIntoView({ behavior: "smooth" });
  }, []);

  const add = async () => {
    setBusy(true);
    setNote(null);
    const r = await addToolServer(name.trim(), target.trim(), password);
    setBusy(false);
    setAsking(false);
    setPassword("");
    setNote(r.data?.message || "That did not work. Nothing was kept.");
    if (r.ok) {
      setName("");
      setTarget("");
    }
    load();
  };

  const remove = async (s: ToolServer) => {
    setBusy(true);
    const r = await removeToolServer(s.name);
    setBusy(false);
    setNote(r.data?.message || "That did not work.");
    load();
  };

  const learn = async () => {
    setBusy(true);
    setNote("Reading it and writing a draft. This can take a minute.");
    const isAddress = /^https?:\/\//i.test(docs.trim());
    const r = await learnFromDocs(isAddress ? docs.trim() : "", isAddress ? "" : docs, skillName.trim());
    setBusy(false);
    setNote(r.data?.message || "That did not work.");
    if (r.ok && r.data.name && r.data.text) setDraft({ name: r.data.name, text: r.data.text });
  };

  const keep = async () => {
    if (!draft) return;
    setBusy(true);
    const r = await keepLearnedSkill(draft.name, draft.text);
    setBusy(false);
    setNote(r.data?.message || "That did not work.");
    if (r.ok) {
      setDraft(null);
      setDocs("");
      setSkillName("");
    }
  };

  const discard = async () => {
    if (!draft) return;
    setBusy(true);
    const r = await discardLearnedSkill(draft.name);
    setBusy(false);
    setNote(r.data?.message || "That did not work.");
    setDraft(null);
  };

  const shown = servers.filter((s) => !(hideHomeAssistant && s.name === "home_assistant"));

  return (
    <div id="add-own-tool" style={{ scrollMarginTop: 16 }}>
    <Panel>
      <PanelTitle>Add my own tool</PanelTitle>
      {note && (
        <p style={{ ...small, color: "var(--text)", marginBottom: 10 }} data-tool-note="">
          {note}
        </p>
      )}
      {!ready && (
        <p style={{ ...small, marginBottom: 10 }}>
          Tool servers need one more part on this computer. In a terminal, type: pip install mcp
        </p>
      )}

      {shown.length > 0 && (
        <div style={{ display: "grid", gap: 8, marginBottom: 14 }}>
          {shown.map((s) => (
            <div key={s.name} className="flex flex-wrap items-center gap-3" style={wellStyle}>
              <div style={{ flex: 1, minWidth: 200 }}>
                <div style={{ ...small, color: "var(--text)", fontWeight: 700 }}>{s.label}</div>
                <p style={{ ...small, fontSize: 13 }}>
                  {s.tool_count} tool{s.tool_count === 1 ? "" : "s"}
                  {s.switched_on ? "" : ", switched off"}. {s.address}
                </p>
              </div>
              <Button label="Remove" color="lightDark" small roundedFull disabled={busy} onClick={() => remove(s)} />
            </div>
          ))}
        </div>
      )}

      <div style={{ display: "grid", gap: 8, maxWidth: 560 }}>
        <p style={small}>
          Have a tool server? Type its web address (it often ends in /mcp), or the command that starts it on this
          computer. If it needs a password, the password goes into your vault.
        </p>
        <input aria-label="Tool name" placeholder="A short name, like Garage" value={name} onChange={(e) => setName(e.target.value)} style={inputStyle} />
        <input
          aria-label="Tool address or command"
          placeholder="http://192.168.1.30:9000/mcp  or  npx -y some-tool-server"
          value={target}
          onChange={(e) => setTarget(e.target.value)}
          style={inputStyle}
        />
        <input
          aria-label="Tool password"
          type="password"
          autoComplete="off"
          placeholder="Password, if it needs one"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          style={inputStyle}
        />
        <div className="flex gap-2">
          <Button label="Add" color="info" small roundedFull disabled={busy || !target.trim()} onClick={() => setAsking(true)} />
        </div>
        {asking && (
          <div style={{ ...wellStyle, display: "grid", gap: 8 }}>
            <p style={{ ...small, color: "var(--text)" }}>
              Your bot will {/^https?:\/\//i.test(target.trim()) ? "use the tool server at" : "start this command:"}{" "}
              {target.trim()}, and use its tools in chat. It gets its own switch on What leaves your computer.
            </p>
            <div className="flex gap-2">
              <Button label="Yes, add it" color="info" small roundedFull disabled={busy} onClick={add} />
              <Button label="Not now" color="lightDark" small roundedFull disabled={busy} onClick={() => setAsking(false)} />
            </div>
          </div>
        )}
      </div>

      <div style={{ display: "grid", gap: 8, maxWidth: 560, marginTop: 18 }}>
        <p style={small}>
          No tool server? Give the address of its docs page, or paste the docs, and your bot drafts a skill for it.
          Nothing is added until you say yes.
        </p>
        <textarea
          aria-label="Docs page address or docs"
          placeholder="https://example.org/docs/api  or paste the docs here"
          value={docs}
          onChange={(e) => setDocs(e.target.value)}
          rows={3}
          style={{ ...inputStyle, resize: "vertical" }}
        />
        <input aria-label="Skill name" placeholder="A short name, if you like" value={skillName} onChange={(e) => setSkillName(e.target.value)} style={inputStyle} />
        <div className="flex gap-2">
          <Button label="Draft a skill" color="lightDark" small roundedFull disabled={busy || !docs.trim()} onClick={learn} />
        </div>
        {draft && (
          <div style={{ ...wellStyle, display: "grid", gap: 8 }}>
            <p style={{ ...small, color: "var(--text)" }}>Here is the draft. You can change it before you add it.</p>
            <textarea
              aria-label="Skill draft"
              value={draft.text}
              onChange={(e) => setDraft({ ...draft, text: e.target.value })}
              rows={14}
              style={{ ...inputStyle, fontFamily: "var(--font-jbmono), monospace", fontSize: 13, resize: "vertical" }}
            />
            <div className="flex gap-2">
              <Button label="Yes, add this skill" color="info" small roundedFull disabled={busy} onClick={keep} />
              <Button label="No, throw it away" color="lightDark" small roundedFull disabled={busy} onClick={discard} />
            </div>
          </div>
        )}
      </div>
    </Panel>
    </div>
  );
}
