"use client";

/**
 * Weather tile (design 4b.2) — standalone weather + air quality, split out
 * of the old combined reminders-weather tile. Live data from the backend's
 * /dashboard/weather: the built-in weather service (Open-Meteo, no key) for
 * the owner's town. Home Assistant is not needed for any of it.
 * Shows the cloud photo more strongly than other tiles (--img3).
 *
 * With no town set the backend fetches nothing and says so (needs_home), and
 * this tile asks for the town right here: the one place it is asked in the
 * browser. The town name is the only thing sent, once, to the town lookup,
 * and only after the owner's yes to that lookup on this device.
 */

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import DesignTile, { FREDOKA, StripesBlock, JBM, MicroLabel, NUNITO } from "./DesignTile";
import { askToSwitchOn } from "@/app/_lib/services/outsideService";
import { setHomeTown } from "@/app/_lib/services/homeService";

// Same-origin proxy path only, never an env-supplied base: such a
// base can point the browser past the /api/pai proxy and so past
// the pairing gate, the path allowlist and the cross-site check.
const API_BASE = "/api/pai";

interface AirRisk {
  level: number; // 0..3
  label: string; // Low / Moderate / High / Very High
  detail: string;
}

interface WeatherData {
  location?: string;
  // 200 + available:false is how the backend says "the outbound doors for
  // weather and air quality are shut" (api_server.dashboard_weather). It is an
  // answer, not a failure: the card says WHY and stops polling a route that
  // cannot reach anything until the owner opens a door in Settings.
  available?: boolean;
  reason?: string;
  message?: string;
  temp?: number | null;
  feels_like?: number | null;
  humidity?: number | null;
  condition?: string | null;
  high?: number;
  low?: number;
  us_aqi?: number;
  aqi_label?: string;
  air_breakdown?: Record<string, AirRisk>;
  // No town yet: nothing was fetched, and the card asks for one.
  needs_home?: boolean;
  weather_on?: boolean;
  offline_bot?: boolean;
  what_is_stored?: string;
  town_lookup_allowed?: boolean;
  town_lookup_sends?: string;
  /** Set when the owner skipped the town in the setup wizard: what to do now,
   *  in the backend's plain words. */
  town_later?: string;
}

const OUTSIDE_PAGE = "/dashboard/outside";

const small = {
  fontFamily: NUNITO,
  fontWeight: 600,
  fontSize: 12,
  lineHeight: 1.35,
  color: "var(--text2)",
} as const;

const button = {
  fontFamily: NUNITO,
  fontWeight: 700,
  fontSize: 12,
  background: "var(--chip)",
  color: "var(--text)",
  border: "none",
} as const;

/** The town, asked once. The yes to the town lookup is a separate, explicit
 *  press on the backend's own sentence (a one-time ticket for this device). */
function TownForm({ wx, onSaved }: { wx: WeatherData; onSaved: () => void }) {
  const [town, setTown] = useState("");
  const [asking, setAsking] = useState<{ ticket: string; sentence: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");

  const save = async (ticket?: string) => {
    setBusy(true);
    setNote("");
    try {
      const out = await setHomeTown(town, ticket);
      setAsking(null);
      const others = out.other_matches?.length
        ? ` Not the right one? Add the state or country, like ${out.other_matches[0]}.`
        : "";
      setNote(`Saved: ${out.home?.label ?? town}.${others}`);
      onSaved();
    } catch (err) {
      setNote(err instanceof Error ? err.message : "That did not work.");
    } finally {
      setBusy(false);
    }
  };

  const submit = async () => {
    if (town.trim().length < 2) {
      setNote("Please type a town name.");
      return;
    }
    if (wx.town_lookup_allowed) {
      await save();
      return;
    }
    setBusy(true);
    setNote("");
    try {
      const asked = await askToSwitchOn("geocode");
      setAsking({ ticket: asked.ticket, sentence: asked.sentence });
    } catch (err) {
      setNote(err instanceof Error ? err.message : "That did not work.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-1.5 overflow-auto" data-testid="weather-town">
      {wx.town_later ? (
        <div style={{ ...small, color: "var(--text)" }} data-testid="weather-town-later">
          {wx.town_later}
        </div>
      ) : null}
      <div style={{ ...small, color: "var(--text)", fontSize: 13 }}>
        Which town is home? The weather is for your town.
      </div>
      {!asking ? (
        <form
          className="flex gap-1.5"
          onSubmit={(e) => {
            e.preventDefault();
            void submit();
          }}
        >
          <input
            aria-label="Your town"
            value={town}
            maxLength={100}
            placeholder="Springfield, Missouri"
            onChange={(e) => setTown(e.target.value)}
            className="min-w-0 flex-1 rounded-lg px-2 py-1"
            style={{ ...small, color: "var(--text)", background: "var(--chip)", border: "none" }}
          />
          <button type="submit" disabled={busy} className="rounded-lg px-2 py-1" style={button}>
            {busy ? "Wait" : "Find my town"}
          </button>
        </form>
      ) : (
        <div className="flex flex-col gap-1.5">
          <div style={small}>{asking.sentence}</div>
          <div className="flex gap-1.5">
            <button
              type="button"
              disabled={busy}
              className="rounded-lg px-2 py-1"
              style={button}
              onClick={() => void save(asking.ticket)}
            >
              Yes, look it up
            </button>
            <button
              type="button"
              disabled={busy}
              className="rounded-lg px-2 py-1"
              style={{ ...button, background: "transparent" }}
              onClick={() => setAsking(null)}
            >
              No
            </button>
          </div>
        </div>
      )}
      <div style={small}>
        {wx.town_lookup_sends ?? "Only the town name you type is sent, once."}{" "}
        {wx.what_is_stored ?? ""}
      </div>
      {wx.weather_on === false ? (
        <div style={small}>
          The weather forecast itself is switched off.{" "}
          <Link href={OUTSIDE_PAGE} style={{ textDecoration: "underline" }}>
            Switch it on under What leaves your computer
          </Link>
          .
        </div>
      ) : null}
      {note ? <div style={{ ...small, color: "var(--text)" }}>{note}</div> : null}
    </div>
  );
}

/* Design shows Pollen + Mold first; Dust + Asthma follow when present. */
const AIR_ORDER: { key: string; label: string }[] = [
  { key: "pollen", label: "Pollen" },
  { key: "mold", label: "Mold" },
  { key: "dust", label: "Dust" },
  { key: "asthma", label: "Asthma" },
];

const RISK_COLORS = ["var(--mint)", "var(--lemon)", "var(--lemon)", "var(--puff-red)"];

export default function WeatherTile({ title }: { title?: string }) {
  const [wx, setWx] = useState<WeatherData | null>(null);
  const [failed, setFailed] = useState(false);
  const [epoch, setEpoch] = useState(0);
  const reload = useCallback(() => setEpoch((n) => n + 1), []);

  useEffect(() => {
    let cancelled = false;
    let iv: ReturnType<typeof setInterval> | undefined;
    const load = () => {
      fetch(`${API_BASE}/dashboard/weather`, { signal: AbortSignal.timeout(15000) })
        .then((r) => (r.ok ? r.json() : null))
        .then((d) => {
          if (cancelled) return;
          if (d) {
            setWx(d);
            setFailed(false);
            // Doors shut: stop the ten-minute retry. Nothing about a closed
            // door changes without the owner, and a poll that will keep
            // failing is noise in the log rather than data on the tile.
            if (d.available === false && iv) {
              clearInterval(iv);
              iv = undefined;
            }
          } else setFailed(true);
        })
        .catch(() => {
          if (!cancelled) setFailed(true);
        });
    };
    load();
    iv = setInterval(load, 10 * 60_000);
    return () => {
      cancelled = true;
      if (iv) clearInterval(iv);
    };
  }, [epoch]);

  const cond = wx?.condition
    ? wx.condition.charAt(0).toUpperCase() + wx.condition.slice(1)
    : null;

  const chips = wx?.air_breakdown
    ? AIR_ORDER.filter(({ key }) => wx.air_breakdown![key]).slice(0, 4)
    : [];

  // The doors are shut and HA gave nothing either: there is no weather to draw,
  // so the tile says so in the backend's own words. With HA reachable (a LAN
  // destination, not a door) the temperature is real and stays — only the
  // open-meteo half is missing, and the reason goes where the air chips were.
  const shut = wx?.available === false;
  // The backend's sentence first: `reason` is a log line, `message` is words.
  const shutReason = wx?.message || "The weather forecast is switched off.";
  const nothingToShow = shut && wx?.temp == null;
  const askTown = !!wx?.needs_home && !wx?.offline_bot;
  // The census never told us the city. Whatever HA labels this location is the
  // only honest label; with no label the tile is titled by what it is.
  const label = title || wx?.location?.trim() || "Weather";

  return (
    <DesignTile img="/design-cloud-4.jpg" imgOpacity="var(--img3)">
      <div className="flex h-full min-h-0 flex-col">
        <MicroLabel>{label}</MicroLabel>

        {!wx && failed ? (
          <div className="mt-3 flex-1">
            <StripesBlock label="Weather offline" style={{ height: "100%" }} />
          </div>
        ) : askTown && wx ? (
          <div className="mt-2 flex min-h-0 flex-1">
            <TownForm wx={wx} onSaved={reload} />
          </div>
        ) : nothingToShow ? (
          <div className="mt-3 flex min-h-0 flex-1 flex-col gap-1.5">
            <div style={{ ...small, fontSize: 13, color: "var(--text)" }}>{shutReason}</div>
            {!wx?.offline_bot ? (
              <Link href={OUTSIDE_PAGE} style={{ ...small, textDecoration: "underline" }}>
                Open What leaves your computer
              </Link>
            ) : null}
          </div>
        ) : (
          <>
            <div className="mt-1.5 flex items-start justify-between">
              <div
                style={{
                  fontFamily: FREDOKA,
                  fontWeight: 600,
                  fontSize: 56,
                  lineHeight: 1,
                  color: "var(--text)",
                }}
              >
                {wx?.temp != null ? `${Math.round(wx.temp)}°` : "--°"}
              </div>
              <div
                className="text-right"
                style={{
                  fontFamily: NUNITO,
                  fontWeight: 600,
                  fontSize: 13,
                  lineHeight: 1.7,
                  color: "var(--text2)",
                }}
              >
                {wx?.high != null ? (
                  <>
                    H {Math.round(wx.high)}° L {wx.low != null ? Math.round(wx.low) : "--"}°
                    <br />
                  </>
                ) : null}
                {wx?.humidity != null ? (
                  <>
                    Hum {Math.round(wx.humidity)}%
                    <br />
                  </>
                ) : null}
                {wx?.us_aqi != null ? <>AQI {Math.round(wx.us_aqi)}</> : null}
              </div>
            </div>

            <div
              className="mt-1"
              style={{
                fontFamily: NUNITO,
                fontWeight: 600,
                fontSize: 16,
                color: "var(--text2)",
              }}
            >
              {cond
                ? wx?.feels_like != null
                  ? `${cond} · feels like ${Math.round(wx.feels_like)}°`
                  : cond
                : "…"}
            </div>

            <div className="flex-1" />

            <div className="flex flex-col gap-1.5">
              {shut && chips.length === 0 && wx?.us_aqi == null ? (
                <div
                  className="rounded-xl px-3 py-2"
                  style={{
                    background: "var(--chip)",
                    fontFamily: NUNITO,
                    fontWeight: 600,
                    fontSize: 12,
                    lineHeight: 1.35,
                    color: "var(--text2)",
                  }}
                >
                  {shutReason}
                </div>
              ) : chips.length > 0
                ? chips.map(({ key, label }) => {
                    const risk = wx!.air_breakdown![key];
                    return (
                      <div
                        key={key}
                        title={risk.detail}
                        className="rounded-xl px-3 py-2"
                        style={{
                          background: "var(--chip)",
                          fontFamily: NUNITO,
                          fontWeight: 700,
                          fontSize: 13,
                          color: "var(--text)",
                        }}
                      >
                        {label}{" "}
                        <span
                          style={{
                            color: RISK_COLORS[Math.min(3, Math.max(0, risk.level))],
                          }}
                        >
                          {risk.label}
                        </span>
                      </div>
                    );
                  })
                : wx?.us_aqi != null && (
                    <div
                      className="rounded-xl px-3 py-2"
                      style={{
                        background: "var(--chip)",
                        fontFamily: NUNITO,
                        fontWeight: 700,
                        fontSize: 13,
                        color: "var(--text)",
                      }}
                    >
                      Air quality{" "}
                      <span style={{ color: "var(--mint)" }}>
                        {wx.aqi_label ?? `AQI ${Math.round(wx.us_aqi)}`}
                      </span>
                    </div>
                  )}
            </div>
          </>
        )}
      </div>
    </DesignTile>
  );
}
