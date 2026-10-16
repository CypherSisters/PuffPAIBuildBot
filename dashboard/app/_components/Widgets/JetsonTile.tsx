"use client";

/**
 * This-box tile (design 4b.3) — live GPU / VRAM / TEMP / POWER meters.
 *
 * Named for the board it was written against, but it promises none: the route
 * behind it reads Linux sysfs and reports `available: false` where a file is
 * not there, and this tile then shows the design's placeholders instead of
 * zeros. BuildBot runs on any hardware, so the title says so.
 *
 * Data: GET /api/system/stats (same-origin Next route, WP-E) with contract
 * { available:boolean, gpu:{pct}, mem:{usedGb,totalGb}, tempC, powerW,
 *   uptimeSec, history:[{t,gpu}] } — polled every 5s. When the route says
 * unavailable (Windows dev, sysfs unreadable) the tile shows the design's
 * striped placeholder, never fake numbers.
 */

import { useEffect, useState } from "react";
import DesignTile, { StripesBlock, JBM, MicroStat } from "./DesignTile";

interface SystemStats {
  available: boolean;
  gpu?: { pct: number };
  mem?: { usedGb: number; totalGb: number };
  tempC?: number;
  powerW?: number;
  uptimeSec?: number;
  history?: { t: number; gpu: number }[];
}

function fmtUptime(sec: number): string {
  const d = Math.floor(sec / 86400);
  const h = Math.floor((sec % 86400) / 3600);
  const m = Math.floor((sec % 3600) / 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

function Meter({
  label,
  value,
  color,
  pct,
  gradient,
}: {
  label: string;
  value: string;
  color: string;
  pct: number;
  gradient: string;
}) {
  const width = Math.max(0, Math.min(100, pct));
  return (
    <div>
      <div
        className="mb-1.5 flex justify-between"
        style={{
          fontFamily: JBM,
          fontWeight: 700,
          fontSize: 13,
          color: "var(--text2)",
        }}
      >
        <span>{label}</span>
        <span style={{ color }}>{value}</span>
      </div>
      <div
        className="h-2 overflow-hidden rounded-full"
        style={{ background: "var(--chip)" }}
      >
        <div
          className="h-full rounded-full transition-all duration-700"
          style={{ width: `${width}%`, background: gradient }}
        />
      </div>
    </div>
  );
}

export default function JetsonTile({ title = "This computer" }: { title?: string }) {
  const [stats, setStats] = useState<SystemStats | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const load = () => {
      fetch("/api/system/stats", {
        cache: "no-store",
        signal: AbortSignal.timeout(8000),
      })
        .then((r) => (r.ok ? r.json() : null))
        .then((d) => {
          if (cancelled) return;
          if (d && typeof d.available === "boolean") {
            setStats(d);
            setFailed(false);
          } else setFailed(true);
        })
        .catch(() => {
          if (!cancelled) setFailed(true);
        });
    };
    load();
    const iv = setInterval(load, 5000);
    return () => {
      cancelled = true;
      clearInterval(iv);
    };
  }, []);

  const live = stats?.available ? stats : null;
  const memPct =
    live?.mem && live.mem.totalGb > 0
      ? (100 * live.mem.usedGb) / live.mem.totalGb
      : 0;

  return (
    <DesignTile
      icon="memory"
      iconColor="var(--sky)"
      title={title}
      img="/design-cloud-1.jpg"
      stat={
        live?.uptimeSec != null ? (
          <MicroStat color="var(--mint)">{fmtUptime(live.uptimeSec)}</MicroStat>
        ) : undefined
      }
    >
      {live ? (
        <div className="grid grid-cols-2 gap-x-[18px] gap-y-4">
          <Meter
            label="GPU"
            value={`${Math.round(live.gpu?.pct ?? 0)}%`}
            color="var(--pink)"
            pct={live.gpu?.pct ?? 0}
            gradient="linear-gradient(90deg, var(--pink), var(--magenta))"
          />
          <Meter
            label="VRAM"
            value={`${(live.mem?.usedGb ?? 0).toFixed(1)} / ${Math.round(live.mem?.totalGb ?? 0)} GB`}
            color="var(--lav)"
            pct={memPct}
            gradient="linear-gradient(90deg, var(--lav), var(--sky))"
          />
          <Meter
            label="TEMP"
            value={`${Math.round(live.tempC ?? 0)}°C`}
            color="var(--mint)"
            pct={((live.tempC ?? 0) / 90) * 100}
            gradient="linear-gradient(90deg, var(--mint), var(--sky))"
          />
          <Meter
            label="POWER"
            value={`${Math.round(live.powerW ?? 0)} W`}
            color="var(--lemon)"
            pct={((live.powerW ?? 0) / 60) * 100}
            gradient="linear-gradient(90deg, var(--lemon), var(--pink))"
          />
        </div>
      ) : (
        <div className="flex h-full flex-col gap-2.5">
          <StripesBlock
            label={
              failed
                ? "Could not read this computer just now"
                : stats
                  ? "This computer does not share these numbers"
                  : "Reading this computer"
            }
            style={{ flex: 1 }}
          />
        </div>
      )}
    </DesignTile>
  );
}
