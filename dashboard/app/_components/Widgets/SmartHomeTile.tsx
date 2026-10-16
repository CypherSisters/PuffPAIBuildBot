"use client";

/**
 * Smart home tile (design 4b.4) — three subcards from live HA entities:
 * brightest ON light (pink gradient), climate current→target, doors/locks.
 * Uses the shared HA service; HA unreachable → honest offline striping.
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import { getHAEntities, type HAEntity } from "@/app/_lib/services/haService";
import MsIcon from "../MsIcon";
import DesignTile, { StripesBlock, JBM, MicroStat, NUNITO } from "./DesignTile";

const POLL_MS = 30_000;

function SubCard({
  icon,
  iconColor,
  name,
  value,
  valueColor,
  highlight = false,
}: {
  icon: string;
  iconColor: string;
  name: string;
  value: string;
  valueColor: string;
  highlight?: boolean;
}) {
  return (
    <div
      className="flex min-h-[96px] flex-col justify-between rounded-[20px] border px-3.5 py-4"
      style={{
        borderColor: "var(--line)",
        background: highlight
          ? "linear-gradient(150deg, color-mix(in srgb, var(--pink) 22%, transparent), transparent)"
          : "var(--card2)",
      }}
    >
      <MsIcon name={icon} size={26} style={{ color: iconColor }} />
      <div>
        <div
          className="truncate"
          style={{
            fontFamily: NUNITO,
            fontWeight: 700,
            fontSize: 15,
            color: "var(--text)",
          }}
        >
          {name}
        </div>
        <div
          style={{
            fontFamily: JBM,
            fontWeight: 700,
            fontSize: 12,
            color: valueColor,
          }}
        >
          {value}
        </div>
      </div>
    </div>
  );
}

export default function SmartHomeTile({ title = "Smart home" }: { title?: string }) {
  const [entities, setEntities] = useState<HAEntity[] | null>(null);
  const [offline, setOffline] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const load = () => {
      getHAEntities()
        .then((d) => {
          if (!cancelled) {
            setEntities(Array.isArray(d?.entities) ? d.entities : []);
            setOffline(false);
          }
        })
        .catch(() => {
          if (!cancelled) setOffline(true);
        });
    };
    load();
    const iv = setInterval(load, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(iv);
    };
  }, []);

  const lights = (entities ?? []).filter((e) => e.entity_id.startsWith("light."));
  const lightsOn = lights.filter((e) => e.state === "on");
  const topLight =
    lightsOn.length > 0
      ? [...lightsOn].sort(
          (a, b) => (b.attributes?.brightness ?? 0) - (a.attributes?.brightness ?? 0)
        )[0]
      : lights[0] ?? null;
  const lightName =
    topLight?.attributes?.friendly_name ??
    topLight?.entity_id.split(".")[1]?.replace(/_/g, " ") ??
    "Lights";
  const lightPct =
    topLight?.attributes?.brightness != null
      ? Math.round((topLight.attributes.brightness / 255) * 100)
      : null;
  const lightOn = topLight?.state === "on";

  const climate = (entities ?? []).find((e) => e.entity_id.startsWith("climate."));
  const climCur = climate?.attributes?.current_temperature;
  const climSet = climate?.attributes?.temperature;

  const locks = (entities ?? []).filter((e) => e.entity_id.startsWith("lock."));
  const doorSensors = (entities ?? []).filter(
    (e) =>
      e.entity_id.startsWith("binary_sensor.") &&
      e.attributes?.device_class === "door"
  );
  const secTotal = locks.length + doorSensors.length;
  const unsecured =
    locks.filter((e) => e.state === "unlocked").length +
    doorSensors.filter((e) => e.state === "on").length;

  return (
    <DesignTile
      icon="home"
      iconColor="var(--mint)"
      title={title}
      img="/design-cloud-5.jpg"
      href="/dashboard/homeassistant"
      stat={
        lights.length > 0 ? (
          <MicroStat size={12}>
            {lightsOn.length} OF {lights.length} ON
          </MicroStat>
        ) : undefined
      }
    >
      {entities === null ? (
        <StripesBlock
          label={offline ? "Smart home offline" : "Connecting…"}
          style={{ height: "100%" }}
        />
      ) : entities.length === 0 ? (
        <StripesBlock label="No devices" style={{ height: "100%" }} />
      ) : (
        <div className="grid grid-cols-3 gap-3">
          {lights.length > 0 ? (
            <SubCard
              icon="light"
              iconColor={lightOn ? "var(--pink)" : "var(--text3)"}
              name={lightName}
              value={lightOn ? (lightPct != null ? `ON · ${lightPct}%` : "ON") : "OFF"}
              valueColor={lightOn ? "var(--pink)" : "var(--text3)"}
              highlight={lightOn}
            />
          ) : (
            <StripesBlock label="No lights" style={{ minHeight: 96 }} />
          )}
          {climate ? (
            <SubCard
              icon="thermostat"
              iconColor="var(--text3)"
              name="Climate"
              value={`${climCur != null ? Math.round(climCur) : "—"}° → ${climSet != null ? Math.round(climSet) : "—"}°`}
              valueColor="var(--text3)"
            />
          ) : (
            <StripesBlock label="No climate" style={{ minHeight: 96 }} />
          )}
          {secTotal > 0 ? (
            <SubCard
              icon="lock"
              iconColor="var(--text3)"
              name="Doors"
              value={unsecured > 0 ? `${unsecured} UNSECURED` : "ALL LOCKED"}
              valueColor={unsecured > 0 ? "var(--lemon)" : "var(--mint)"}
            />
          ) : (
            <StripesBlock label="No locks" style={{ minHeight: 96 }} />
          )}
        </div>
      )}
    </DesignTile>
  );
}
