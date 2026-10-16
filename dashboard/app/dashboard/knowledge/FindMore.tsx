"use client";

/* ═══════════════════════════════════════════════════════════════
   FIND MORE KNOWLEDGE — search, look, say yes, download.

   The same pipeline as `puffpai sources search|add`, behind the same backend
   routes (GET /knowledge/find, POST /knowledge/add). Nothing downloads until
   the owner has seen what it is, how big it is and its license, and pressed
   the yes button; the backend refuses an add without that yes, so this panel
   can only ever be stricter than the server, never looser. The browser never
   sends an address: only an id the search returned.
   ═══════════════════════════════════════════════════════════════ */

import { useEffect, useRef, useState, type CSSProperties, type FormEvent } from "react";
import MsIcon from "../../_components/MsIcon";
import { apiGet, apiPostRaw } from "../../_lib/api";

const FREDOKA = "var(--font-fredoka), sans-serif";
const NUNITO = "var(--font-nunito), sans-serif";
const JBMONO = "var(--font-jbmono), monospace";

type Found = {
  id: string;
  name: string;
  what_it_is: string;
  size_words: string;
  license_plain: string;
  fetchable: boolean;
  why_not: string;
  reason?: string;
  from_place?: string;
  text_book?: boolean;
};

type FindResponse = {
  success: boolean;
  terms?: string[];
  searched?: string;
  note?: string;
  snapshot_date?: string | null;
  looked_in?: string;
  candidates?: Found[];
  message?: string;
};

type JobState = {
  state?: "idle" | "running" | "done" | "failed";
  name?: string;
  downloaded?: number;
  total?: number | null;
  message?: string;
};

const panel: CSSProperties = {
  borderRadius: 28,
  border: "1px solid var(--line)",
  background: "var(--card)",
  backdropFilter: "blur(18px)",
  WebkitBackdropFilter: "blur(18px)",
  padding: "20px 24px",
  boxShadow: "var(--shadow)",
  marginBottom: 20,
};

const row: CSSProperties = {
  padding: "12px 14px",
  borderRadius: 18,
  background: "var(--card2)",
  border: "1px solid var(--line)",
};

const small: CSSProperties = {
  fontFamily: NUNITO,
  fontWeight: 500,
  fontSize: 13.5,
  color: "var(--text3)",
  margin: "4px 0 0",
};

const button: CSSProperties = {
  fontFamily: NUNITO,
  fontWeight: 700,
  fontSize: 13.5,
  color: "var(--text)",
  background: "var(--card2)",
  border: "1px solid var(--line)",
  borderRadius: 999,
  padding: "7px 14px",
  cursor: "pointer",
};

/** Ids the search hands out: a list id or a Kiwix book id. Anything else is
 *  never sent (the backend checks the same rule). */
const ID_RE = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,190}$/;

/** The library a result comes from, as the start of a line ("the Kiwix
 *  library" becomes "The Kiwix library"). */
function placeName(place: string): string {
  return place.charAt(0).toUpperCase() + place.slice(1);
}

function percent(job: JobState): string {
  if (!job.total || !job.downloaded) return "";
  const p = Math.min(100, Math.floor((job.downloaded / job.total) * 100));
  return ` ${p}%`;
}

export default function FindMore() {
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<FindResponse | null>(null);
  const [asking, setAsking] = useState<Found | null>(null);
  const [job, setJob] = useState<JobState>({ state: "idle" });
  const [problem, setProblem] = useState("");
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => () => {
    if (timer.current) clearInterval(timer.current);
  }, []);

  async function search(e: FormEvent) {
    e.preventDefault();
    const words = query.trim().slice(0, 200);
    if (!words) return;
    setBusy(true);
    setProblem("");
    setAsking(null);
    try {
      const data = await apiGet<FindResponse>(
        `/knowledge/find?q=${encodeURIComponent(words)}`,
        { cache: "no-store" },
      );
      setResult(data);
    } catch {
      setProblem("The search did not work this time. Try again in a moment.");
    } finally {
      setBusy(false);
    }
  }

  function watch() {
    if (timer.current) clearInterval(timer.current);
    timer.current = setInterval(async () => {
      try {
        const state = await apiGet<JobState>("/knowledge/add/status", { cache: "no-store" });
        setJob(state);
        if (state.state === "done" || state.state === "failed") {
          if (timer.current) clearInterval(timer.current);
        }
      } catch {
        /* keep the last known state; the next tick tries again */
      }
    }, 1500);
  }

  async function yes(item: Found) {
    if (!ID_RE.test(item.id) || !item.fetchable) return;
    setProblem("");
    const res = await apiPostRaw<{ success: boolean; message?: string }>("/knowledge/add", {
      id: item.id,
      size_acknowledged: true,
    });
    setAsking(null);
    if (!res.ok || !res.data?.success) {
      setProblem(res.data?.message || "That could not be added.");
      return;
    }
    setJob({ state: "running", name: item.name });
    watch();
  }

  const running = job.state === "running";

  return (
    <div style={panel}>
      <div className="mb-1 flex items-center gap-2.5">
        <MsIcon name="travel_explore" size={21} style={{ color: "var(--sky)" }} />
        <div style={{ fontFamily: FREDOKA, fontWeight: 600, fontSize: 23, color: "var(--text)" }}>
          Find more knowledge
        </div>
      </div>
      <p style={{ fontFamily: NUNITO, fontWeight: 500, fontSize: 14.5, color: "var(--text2)", margin: "0 0 12px" }}>
        Look for free books your bot can read offline. Nothing downloads until you say yes.
      </p>

      <form onSubmit={search} className="flex flex-wrap items-center gap-2" data-walk="knowledge-books">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          maxLength={200}
          placeholder="What should it know about? (for example: birds, cooking, rockets)"
          aria-label="What should it know about"
          style={{
            flex: "1 1 260px",
            fontFamily: NUNITO,
            fontSize: 15,
            color: "var(--text)",
            background: "var(--card2)",
            border: "1px solid var(--line)",
            borderRadius: 14,
            padding: "9px 12px",
          }}
        />
        <button type="submit" disabled={busy || !query.trim()} style={button}>
          {busy ? "Looking..." : "Look"}
        </button>
      </form>

      {problem && <p style={{ ...small, color: "var(--pink)" }}>{problem}</p>}

      {(running || job.state === "done" || job.state === "failed") && (
        <div style={{ ...row, marginTop: 12 }}>
          <div style={{ fontFamily: NUNITO, fontWeight: 700, fontSize: 14.5, color: "var(--text)" }}>
            {running ? `Downloading ${job.name ?? ""}${percent(job)}` : job.name}
          </div>
          {job.message && <p style={small}>{job.message}</p>}
        </div>
      )}

      {result && (
        <div className="mt-3 flex flex-col gap-1.5">
          {result.terms && result.terms.length > 0 && (
            <p style={small}>
              Looked for: {result.terms.join(", ")}
              {result.looked_in
                ? ` (in ${result.looked_in})`
                : result.searched === "live"
                  ? " (in the live Kiwix library)"
                  : ` (in the Kiwix list saved on ${result.snapshot_date ?? "an unknown date"})`}
            </p>
          )}
          {result.note && <p style={small}>{result.note}</p>}
          {(result.candidates ?? []).map((item) => (
            <div key={item.id} style={row}>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span style={{ fontFamily: NUNITO, fontWeight: 700, fontSize: 15, color: "var(--text)" }}>
                  {item.name}
                </span>
                <span style={{ fontFamily: JBMONO, fontSize: 12, color: "var(--text2)" }}>
                  {item.size_words}
                </span>
              </div>
              <p style={{ ...small, color: "var(--text2)" }}>{item.what_it_is}</p>
              {item.from_place && <p style={small}>From: {placeName(item.from_place)}</p>}
              <p style={small}>License: {item.license_plain}</p>
              {item.fetchable ? (
                asking?.id === item.id ? (
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    <span style={{ ...small, margin: 0, color: "var(--text)" }}>
                      {item.text_book ? "Fetch" : "Download"} {item.size_words}
                      {item.from_place ? ` from ${item.from_place}` : ""}? After that it is read
                      on this computer.
                    </span>
                    <button type="button" style={button} disabled={running} onClick={() => yes(item)}>
                      Yes, download it
                    </button>
                    <button type="button" style={button} onClick={() => setAsking(null)}>
                      Not now
                    </button>
                  </div>
                ) : (
                  <div className="mt-2">
                    <button type="button" style={button} disabled={running} onClick={() => setAsking(item)}>
                      Add
                    </button>
                  </div>
                )
              ) : (
                <p style={small}>Cannot be added here: {item.why_not}</p>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
