"use client";

/**
 * What leaves your computer.
 *
 * One row per outside service, in the backend's own words: what it is for,
 * what leaves, where it goes, and a switch. Off is one tap. On shows the
 * backend's sentence first and needs a "Yes, switch it on" for that sentence;
 * the page never writes its own version of what will leave. Below the list,
 * every request BuildBot made in the last day.
 *
 * The page re-reads everything from the backend after every switch, so what
 * it shows is always what the backend holds, never what the page assumed.
 */

import { useCallback, useEffect, useState } from "react";
import Button from "@/app/_components/Button";
import {
  askToSwitchOn,
  getOutside,
  getOutsideLog,
  hideNotice,
  switchOff,
  switchOn,
  type OutsideLogRow,
  type OutsidePage,
  type OutsideService,
} from "@/app/_lib/services/outsideService";
import {
  AppHeader,
  AppPage,
  JBM,
  NUNITO,
  Panel,
  SectionBar,
  wellStyle,
} from "@/app/dashboard/apps/_components/ui";

type Pending = { id: string; ticket: string; sentence: string };

const small: React.CSSProperties = {
  margin: 0,
  fontFamily: NUNITO,
  fontSize: 13,
  lineHeight: 1.45,
  color: "var(--text2)",
};

function StateChip({ service }: { service: OutsideService }) {
  const on = service.state === "on";
  return (
    <span
      style={{
        fontFamily: JBM,
        fontSize: 11,
        fontWeight: 700,
        letterSpacing: ".08em",
        textTransform: "uppercase",
        padding: "3px 8px",
        borderRadius: 999,
        border: "1px solid var(--line)",
        background: on ? "var(--mint)" : "var(--card2)",
        color: on ? "#10231a" : "var(--text3)",
        whiteSpace: "nowrap",
      }}
    >
      {on ? "On" : service.state === "not added" ? "Not added" : "Off"}
    </span>
  );
}

function ServiceRow({
  service,
  pending,
  busy,
  onOff,
  onAsk,
  onYes,
  onNotNow,
}: {
  service: OutsideService;
  pending: Pending | null;
  busy: boolean;
  onOff: () => void;
  onAsk: () => void;
  onYes: () => void;
  onNotNow: () => void;
}) {
  const used =
    service.last_used &&
    `Last used ${service.last_used} - ${service.today.sent} sent, ${service.today.stopped} stopped in the last day`;
  return (
    <div style={{ ...wellStyle, display: "grid", gap: 6 }}>
      <div className="flex flex-wrap items-center gap-3">
        <StateChip service={service} />
        <strong style={{ fontFamily: NUNITO, fontSize: 15, color: "var(--text)" }}>
          {service.name}
        </strong>
        <span style={{ ...small, color: "var(--text3)" }}>{service.state_line}</span>
        <div className="flex-1" />
        {service.can_switch && service.switch_to === "off" && (
          <Button label="Switch off" color="lightDark" small roundedFull
                  disabled={busy} onClick={onOff} />
        )}
        {service.can_switch && service.switch_to === "on" && !pending && (
          <Button label="Switch on" color="info" small roundedFull
                  disabled={busy} onClick={onAsk} />
        )}
      </div>
      {service.for && <p style={small}>For: {service.for}</p>}
      <p style={small}>What leaves your computer: {service.what_leaves}</p>
      {service.websites.length > 0 && (
        <p style={small}>Goes to: {service.websites.join(", ")}</p>
      )}
      {service.state === "on" && service.turned_on_line && (
        <p style={small}>{service.turned_on_line}</p>
      )}
      {used && <p style={small}>{used}.</p>}
      {!service.can_switch && service.why_cannot_switch && (
        <p style={{ ...small, color: "var(--text3)" }}>{service.why_cannot_switch}</p>
      )}
      {pending && (
        <div
          style={{
            borderLeft: "3px solid var(--lemon)",
            paddingLeft: 10,
            display: "grid",
            gap: 8,
          }}
        >
          <p style={{ ...small, color: "var(--text)" }}>{pending.sentence}</p>
          <div className="flex gap-2">
            <Button label="Yes, switch it on" color="info" small roundedFull
                    disabled={busy} onClick={onYes} />
            <Button label="Not now" color="lightDark" small roundedFull
                    disabled={busy} onClick={onNotNow} />
          </div>
        </div>
      )}
    </div>
  );
}

export default function OutsidePageView() {
  const [page, setPage] = useState<OutsidePage | null>(null);
  const [log, setLog] = useState<OutsideLogRow[]>([]);
  const [filter, setFilter] = useState<string>("");
  const [pending, setPending] = useState<Pending | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [p, l] = await Promise.all([getOutside(), getOutsideLog(24)]);
      setPage(p);
      setLog(l.rows || []);
      setProblem(null);
    } catch (err) {
      setProblem(err instanceof Error ? err.message : "This page did not load.");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const act = useCallback(
    async (work: () => Promise<unknown>) => {
      setBusy(true);
      try {
        await work();
      } catch (err) {
        setProblem(err instanceof Error ? err.message : "That did not work.");
      } finally {
        await load();
        setBusy(false);
      }
    },
    [load],
  );

  if (!page) {
    return (
      <AppPage>
        <AppHeader title="What leaves your computer" />
        {problem && <p style={small}>{problem}</p>}
      </AppPage>
    );
  }

  const names = Array.from(new Set(log.map((r) => r.service)));
  const shown = filter ? log.filter((r) => r.service === filter) : log;

  return (
    <AppPage>
      <AppHeader title={page.title} lede={page.line} />
      {problem && (
        <Panel accent="--lemon">
          <p style={small}>{problem}</p>
        </Panel>
      )}
      {page.notice && (
        <Panel accent="--lemon">
          <div className="flex flex-wrap items-center gap-3">
            <p style={{ ...small, color: "var(--text)", flex: 1 }}>{page.notice}</p>
            {page.notice_can_ok && (
              <Button label="OK" color="lightDark" small roundedFull
                      disabled={busy} onClick={() => act(() => hideNotice())} />
            )}
          </div>
        </Panel>
      )}

      <div style={{ display: "grid", gap: 10 }}>
        {page.services.map((service) => (
          <ServiceRow
            key={service.id}
            service={service}
            busy={busy}
            pending={pending && pending.id === service.id ? pending : null}
            onOff={() => act(() => switchOff(service.id))}
            onAsk={() =>
              act(async () => {
                const asked = await askToSwitchOn(service.id);
                setPending({ id: service.id, ticket: asked.ticket, sentence: asked.sentence });
              })
            }
            onYes={() =>
              act(async () => {
                const yes = pending;
                setPending(null);
                if (yes) await switchOn(yes.id, yes.ticket);
              })
            }
            onNotNow={() => setPending(null)}
          />
        ))}
      </div>

      <Panel>
        <p style={{ ...small, color: "var(--text)" }}>{page.not_seen_line}</p>
        <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
          {page.not_seen.map((line) => (
            <li key={line} style={small}>{line}</li>
          ))}
        </ul>
        <p style={{ ...small, marginTop: 8 }}>{page.home_network_line}</p>
      </Panel>

      <SectionBar title="Every request in the last day">
        <select
          aria-label="Show one service"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          style={{
            background: "var(--card2)",
            border: "1px solid var(--line)",
            borderRadius: 10,
            color: "var(--text)",
            fontFamily: NUNITO,
            fontSize: 13,
            padding: "4px 8px",
          }}
        >
          <option value="">All services</option>
          {names.map((n) => (
            <option key={n} value={n}>{n}</option>
          ))}
        </select>
      </SectionBar>
      <p style={small}>{page.log_line}</p>
      {shown.length === 0 ? (
        <p style={small}>No requests left this computer in the last day.</p>
      ) : (
        <div style={{ ...wellStyle, display: "grid", gap: 4 }}>
          {shown.map((row, i) => (
            <p
              key={`${row.time}-${i}`}
              style={{
                ...small,
                fontFamily: JBM,
                fontSize: 12,
                color: row.outcome === "done" ? "var(--text2)" : "var(--text3)",
              }}
            >
              {row.line}
            </p>
          ))}
        </div>
      )}
    </AppPage>
  );
}
