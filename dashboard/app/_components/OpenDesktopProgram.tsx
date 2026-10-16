"use client";

/**
 * Open for a job whose app is a desktop program (no page): Toolsmith
 * installed it and it became one of her programs (kind "desktop"). Matched
 * by the job's program id, else by its path among GET /programs rows; Open
 * calls POST /programs/<id>/launch. A 404 says so plainly.
 */

import { useEffect, useState } from "react";
import { launchProgram, listPrograms } from "@/app/dashboard/developer/addYourOwnService";
import { NUNITO, Pill } from "@/app/dashboard/apps/_components/ui";

export default function OpenDesktopProgram({ program, name }: { program: { id: string; path: string }; name: string }) {
  const [id, setId] = useState(program.id);
  const [said, setSaid] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    void listPrograms().then((r) => {
      if (!alive || !r.ok) return;
      const want = program.path.toLowerCase();
      const row =
        r.data.programs.find((p) => p.id === program.id) ||
        (want ? r.data.programs.find((p) => p.kind === "desktop" && p.path.toLowerCase() === want) : undefined);
      if (row) setId(row.id);
    });
    return () => {
      alive = false;
    };
  }, [program.id, program.path]);

  async function open() {
    setBusy(true);
    const r = await launchProgram(id);
    setBusy(false);
    setSaid(
      r.ok
        ? `Opening ${name}.`
        : r.missing
          ? "Opening programs from here is coming soon. Open it from your computer's Start menu."
          : r.message || `${name} did not open.`,
    );
  }

  return (
    <div className="mt-1 flex flex-wrap items-center gap-2" data-open-program={id}>
      <Pill tone="accent" disabled={busy || !id} onClick={() => void open()}>
        Open
      </Pill>
      <span style={{ fontFamily: NUNITO, fontSize: 12, color: "var(--text2)" }}>Opens in its own window.</span>
      {said && <span style={{ fontFamily: NUNITO, fontSize: 12, color: "var(--text2)" }}>{said}</span>}
    </div>
  );
}
