"use client";

/* ═══════════════════════════════════════════════════════════════
   LEARNING — how far along the bot is with each book.

   Every word on this card comes from GET /knowledge/learning, whose `lines`
   are written by knowledge/progress.plain_lines (the browser's wording). The
   terminal's `puffpai knowledge status` and the bot's own answer to "are you
   done learning?" read the same records, so the three never disagree. This
   card adds no wording of its own except its title and button labels.

   It polls every 3 seconds while any book is being learned, and stops when
   none is. The buttons send only a book id the backend listed and a yes/no.
   ═══════════════════════════════════════════════════════════════ */

import { useCallback, useEffect, useRef, useState, type CSSProperties } from "react";
import MsIcon from "../../_components/MsIcon";
import { apiGet, apiPostRaw } from "../../_lib/api";

const FREDOKA = "var(--font-fredoka), sans-serif";
const NUNITO = "var(--font-nunito), sans-serif";

type Book = {
  id: string;
  name: string;
  stage: string;
  running: boolean;
  can_start: boolean;
  can_stop: boolean;
  quick: { done?: number; total?: number };
  meaning: { chars_done?: number; chars_total?: number };
  lines: string[];
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

const button: CSSProperties = {
  fontFamily: NUNITO,
  fontWeight: 700,
  fontSize: 12.5,
  color: "var(--text2)",
  background: "var(--card2)",
  border: "1px solid var(--line)",
  borderRadius: 999,
  padding: "6px 12px",
  cursor: "pointer",
};

function share(book: Book): number {
  if (book.stage === "quick") {
    const total = book.quick?.total || 0;
    return total ? Math.min(1, (book.quick?.done || 0) / total) : 0;
  }
  if (book.stage === "done") return 1;
  const total = book.meaning?.chars_total || 0;
  return total ? Math.min(1, (book.meaning?.chars_done || 0) / total) : 0;
}

function Bar({ value }: { value: number }) {
  return (
    <div
      aria-hidden
      style={{
        height: 6,
        borderRadius: 999,
        background: "var(--card2)",
        border: "1px solid var(--line)",
        overflow: "hidden",
        margin: "8px 0",
      }}
    >
      <div
        style={{
          width: `${Math.round(value * 100)}%`,
          height: "100%",
          background: "var(--mint)",
          transition: "width .6s ease",
        }}
      />
    </div>
  );
}

export default function LearningCard() {
  const [books, setBooks] = useState<Book[]>([]);
  const [busy, setBusy] = useState<string>("");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await apiGet<{ success: boolean; books: Book[] }>("/knowledge/learning");
      const list = res.books || [];
      setBooks(list);
      if (timer.current) clearTimeout(timer.current);
      if (list.some((b) => b.running)) timer.current = setTimeout(load, 3000);
    } catch {
      setBooks([]);
    }
  }, []);

  useEffect(() => {
    load();
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [load]);

  const deeper = async (id: string, on: boolean) => {
    setBusy(id);
    await apiPostRaw("/knowledge/learning/deeper", { id, on });
    setBusy("");
    setTimeout(load, 500);
  };

  if (!books.length) return null;

  return (
    <section style={panel} aria-label="Learning your books">
      <div className="flex items-center gap-2" style={{ marginBottom: 10 }}>
        <MsIcon name="auto_stories" size={20} />
        <h2 style={{ fontFamily: FREDOKA, fontSize: 18, margin: 0 }}>Learning your books</h2>
      </div>
      {books.map((book) => (
        <div key={book.id} style={{ padding: "10px 0", borderTop: "1px solid var(--line)" }}>
          {book.lines.map((line, i) => (
            <p key={i} style={{ fontFamily: NUNITO, fontSize: 14, margin: "2px 0", color: i ? "var(--text2)" : "var(--text)" }}>
              {line}
            </p>
          ))}
          {book.running && <Bar value={share(book)} />}
          <div className="flex gap-2" style={{ marginTop: 6 }}>
            {book.can_start && (
              <button type="button" style={button} disabled={busy === book.id} onClick={() => deeper(book.id, true)}>
                {book.stage === "stopped" ? "Carry on" : "Learn it fully"}
              </button>
            )}
            {book.can_stop && (
              <button type="button" style={button} disabled={busy === book.id} onClick={() => deeper(book.id, false)}>
                Stop
              </button>
            )}
          </div>
        </div>
      ))}
    </section>
  );
}
