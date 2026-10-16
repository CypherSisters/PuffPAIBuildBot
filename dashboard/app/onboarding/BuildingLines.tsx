"use client";

/**
 * The Building screen's lines: what the build is doing,
 * line by line as it happens, then what it left waiting for her:
 *
 * - a thing Drawbridge held ("Drawbridge found something the plan did not
 *   show: <reason>. It is waiting for your yes.") with **Add it anyway** and
 *   **Leave it out** (POST /integrations/<id>/decide);
 * - "Windows needs its Linux feature turned on for <N> things you picked."
 *   with **Turn it on** (POST /box/install-wsl).
 *
 * When a route is not in this build (404) the line stays, without buttons.
 * Every word comes from the backend (wizard/service.py build_waiting); the
 * terminal asks the same questions with yes or no.
 */

import { useState } from "react";
import {
  decideIntegration,
  turnOnWsl,
  type BuildWaiting,
} from "../_lib/services/wizardService";
import { PuffButton } from "./ui";

const LINE = { color: "var(--text2)", fontSize: 14, lineHeight: 1.5, margin: 0 };
const BLOCK = { marginTop: 12, padding: "8px 0", borderTop: "1px solid var(--line)" };

/** True when a line says what her bot can now do in something it added
 * ("Taught your bot what it can do there: ..."): the screen then waits for
 * "Go on" instead of moving on before she could read it. */
export function keepOnScreen(lines: string[]): boolean {
  return lines.some((line) => /Taught your bot|things your bot can do|Your bot can do \d+ things?/i.test(line));
}

/** The streamed lines, without the ones a waiting block says itself. */
export function streamedLines(lines: string[], waiting: BuildWaiting[]): string[] {
  const said = new Set(waiting.flatMap((row) => row.lines));
  return lines.filter((line) => !said.has(line));
}

function WaitingBlock({
  row,
  onAnswered,
}: {
  row: BuildWaiting;
  onAnswered: (id: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState<string[]>([]);
  const [noButtons, setNoButtons] = useState(false);

  async function answer(call: () => Promise<{ ok: boolean; status: number; data: Record<string, unknown> }>) {
    setBusy(true);
    const reply = await call();
    setBusy(false);
    if (reply.status === 404) {
      setNoButtons(true);
      return;
    }
    const data = reply.data ?? {};
    const lines = [
      ...((data.lines as string[] | undefined) ?? []),
      (data.line as string | undefined) ?? (data.message as string | undefined) ?? "",
    ].filter(Boolean);
    setSaid(lines);
    if (reply.ok) {
      setNoButtons(true);
      onAnswered(row.id);
    }
  }

  const held = row.status === "held";
  return (
    <div style={BLOCK} data-testid={`building-waiting-${row.id}`}>
      {row.lines.map((line) => (
        <p key={line} style={LINE}>
          {line}
        </p>
      ))}
      {noButtons ? null : held ? (
        <div className="flex flex-wrap gap-2" style={{ marginTop: 8 }}>
          <PuffButton
            kind="primary"
            label={row.yes || "Add it anyway"}
            disabled={busy}
            onClick={() => void answer(() => decideIntegration(row.id, true))}
          />
          <PuffButton
            label={row.no || "Leave it out"}
            disabled={busy}
            onClick={() => void answer(() => decideIntegration(row.id, false))}
          />
        </div>
      ) : row.button ? (
        <div className="flex flex-wrap gap-2" style={{ marginTop: 8 }}>
          <PuffButton
            kind="primary"
            label={row.button}
            disabled={busy}
            onClick={() => void answer(turnOnWsl)}
          />
        </div>
      ) : null}
      {said.map((line) => (
        <p key={line} style={{ ...LINE, marginTop: 6 }}>
          {line}
        </p>
      ))}
    </div>
  );
}

export default function BuildingLines({
  lines,
  waiting,
  onAnswered,
}: {
  lines: string[];
  waiting: BuildWaiting[];
  onAnswered: (id: string) => void;
}) {
  const shown = streamedLines(lines, waiting);
  if (!shown.length && !waiting.length) return null;
  return (
    <div data-testid="building-lines" style={{ marginTop: 10 }}>
      {shown.map((line, i) => (
        <p key={`${i}-${line}`} style={LINE}>
          {line}
        </p>
      ))}
      {waiting.map((row) => (
        <WaitingBlock key={row.id} row={row} onAnswered={onAnswered} />
      ))}
    </div>
  );
}
