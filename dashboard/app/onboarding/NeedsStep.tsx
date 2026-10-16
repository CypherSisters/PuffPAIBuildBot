"use client";

/**
 * "Finding out what a <kind> bot needs", between the
 * name and the plan. The same screen `puffpai setup` prints: the lines as they
 * happen, the jobs with a tick box each, the held words, "Add something in
 * your own words", exactly which words would leave this computer, then
 * "Start looking" (or "Only look on this computer"), and the looking lines.
 *
 * Every word comes from the backend (GET/POST /wizard/needs, which reads
 * wizard/wording.py); the words written here are only what shows if an older
 * backend leaves one out. Nothing is decided in the browser: it sends ticks,
 * additions, allowed words and which button was pressed.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  chooseNeeds,
  getNeeds,
  type NeedsChoice,
  type NeedsView,
} from "../_lib/services/wizardService";
import { PuffButton, Toggle, inputStyle } from "./ui";

const H1 = { color: "var(--text)", fontSize: 24, fontWeight: 800, margin: 0, lineHeight: 1.25 };
const HELP = { color: "var(--text2)", fontSize: 14, lineHeight: 1.5, margin: "0 0 8px" };
const QUIET = { color: "var(--text3)", fontSize: 13, lineHeight: 1.5 };
const LINE = { color: "var(--text2)", fontSize: 14, lineHeight: 1.6, margin: 0 };

const POLL_MS = 800;
/** How long the last looking lines stay on screen once looking is done. */
const LAST_LINES_MS = 2500;

export default function NeedsStep({ onDone }: { onDone: () => void }) {
  const [view, setView] = useState<NeedsView | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [typed, setTyped] = useState("");
  const polling = useRef(false);
  const finished = useRef(false);

  // Ask how far it has got until there is something for the owner to do
  // (the job list) or looking has finished (then the plan comes next).
  const poll = useCallback(async () => {
    if (polling.current) return;
    polling.current = true;
    try {
      while (!finished.current) {
        const reply = await getNeeds();
        if (!reply.ok || !reply.data?.phase) {
          setError(
            reply.status === 0
              ? "No answer from this computer."
              : reply.data?.message || "This step could not be drawn.",
          );
          return;
        }
        setError("");
        setView(reply.data);
        if (reply.data.phase === "parts" && reply.data.done) {
          finished.current = true;
          // The last line ("Checking what fits your jobs ... 2 kept, 595 left
          // out") is said as looking ends: it stays on screen long enough to
          // read before the plan comes.
          await new Promise((resolve) => setTimeout(resolve, LAST_LINES_MS));
          onDone();
          return;
        }
        if (reply.data.phase === "needs" && reply.data.done) return;
        await new Promise((resolve) => setTimeout(resolve, POLL_MS));
      }
    } finally {
      polling.current = false;
    }
  }, [onDone]);

  useEffect(() => {
    finished.current = false;
    void poll();
    return () => {
      finished.current = true;
    };
  }, [poll]);

  async function send(choice: NeedsChoice) {
    setBusy(true);
    const reply = await chooseNeeds(choice);
    setBusy(false);
    if (!reply.ok || reply.data?.success === false) {
      setError(reply.data?.message || "That was not accepted. Nothing was changed.");
      return;
    }
    setError("");
    setView(reply.data);
    if (choice.look) void poll();
  }

  if (!view) {
    return (
      <div>
        <h1 style={H1}>Finding out what your bot needs</h1>
        <p style={{ ...LINE, marginTop: 10 }}>Reading what you wrote ...</p>
        {error ? <Problem text={error} /> : null}
      </div>
    );
  }

  const lines = (
    <div style={{ margin: "10px 0 12px" }} data-testid="needs-lines">
      {view.lines.map((line, i) => (
        <p key={`${i}-${line}`} style={LINE}>
          {line}
        </p>
      ))}
    </div>
  );

  if (view.phase === "parts" || !view.done) {
    return (
      <div>
        <h1 style={H1}>{view.title}</h1>
        {lines}
        {view.phase === "parts" ? (
          <p style={QUIET}>This page moves on by itself when it is done.</p>
        ) : null}
        <Problem text={error} />
      </div>
    );
  }

  const ticked = (id: string, on: boolean) =>
    view.jobs.filter((job) => (job.id === id ? on : job.ticked)).map((job) => job.id);
  const buttons = view.buttons?.length
    ? view.buttons
    : [
        { value: "online", label: "Start looking" },
        { value: "here", label: "Only look on this computer" },
      ];

  return (
    <div>
      <h1 style={H1}>{view.title}</h1>
      {lines}
      <p style={HELP}>
        {view.intro || "Here is what your bot needs. Untick anything you do not want, or add your own."}
      </p>
      <section data-testid="needs-jobs">
        {view.jobs.map((job) => (
          <Toggle
            key={job.id}
            id={`job-${job.id}`}
            checked={job.ticked}
            disabled={busy}
            onChange={(on) => void send({ ticked: ticked(job.id, on) })}
            title={job.name}
            description={job.chip}
          />
        ))}
      </section>
      <form
        className="flex flex-wrap gap-2"
        style={{ marginTop: 10 }}
        onSubmit={(event) => {
          event.preventDefault();
          const text = typed.trim();
          if (!text) return;
          setTyped("");
          void send({ added: [text] });
        }}
      >
        <input
          aria-label={view.add || "Add something in your own words"}
          placeholder={view.add || "Add something in your own words"}
          value={typed}
          maxLength={120}
          onChange={(e) => setTyped(e.target.value)}
          style={{ ...inputStyle, flex: "1 1 18rem" }}
        />
        <PuffButton type="submit" label="Add" disabled={busy || !typed.trim()} />
      </form>
      {view.held.length ? (
        <section style={{ marginTop: 12 }} data-testid="needs-held">
          <div style={HELP}>{view.held_line || "Not searched unless you tick it:"}</div>
          {view.held.map((row) => (
            <Toggle
              key={row.word}
              id={`held-${row.word}`}
              checked={row.allowed}
              disabled={busy || row.allowed}
              onChange={(on) => (on ? void send({ allowed_words: [row.word] }) : undefined)}
              title={row.word}
            />
          ))}
        </section>
      ) : null}
      {view.helper_offer ? (
        <section style={{ marginTop: 12 }} data-testid="needs-helper">
          <p style={HELP}>{view.helper_offer}</p>
          <div className="flex flex-wrap gap-2">
            {(view.helper_buttons ?? []).map((button, i) => (
              <PuffButton
                key={button.value}
                kind={i === 0 ? "primary" : undefined}
                label={button.label}
                disabled={busy}
                onClick={() => void send({ get_helper: button.value === "get" })}
              />
            ))}
          </div>
        </section>
      ) : null}
      {view.helper_line ? <p style={LINE}>{view.helper_line}</p> : null}
      <section style={{ marginTop: 14 }} data-testid="needs-sends">
        {(view.before_button ?? []).map((line) => (
          <p key={line} style={LINE}>
            {line}
          </p>
        ))}
      </section>
      <Problem text={error} />
      <div className="flex flex-wrap gap-2" style={{ marginTop: 16 }}>
        {buttons.map((button, i) => (
          <PuffButton
            key={button.value}
            kind={i === 0 ? "primary" : undefined}
            label={button.label}
            disabled={busy}
            onClick={() => void send({ look: button.value === "online" ? "online" : "here" })}
          />
        ))}
      </div>
    </div>
  );
}

function Problem({ text }: { text: string }) {
  if (!text) return null;
  return (
    <div role="alert" style={{ color: "var(--puff-red)", fontSize: 14, marginTop: 8 }}>
      {text}
    </div>
  );
}
