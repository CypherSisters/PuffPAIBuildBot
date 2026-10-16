"use client";

/**
 * The home page's greeting: this bot's icon, name and its own hello, in its
 * own accent colour (GET /bot/persona). A wedding bot and a 3D printing bot
 * look and sound different here before any look is picked; once she picks a
 * look, the look's colour wins (useBotAccent). This is the home page's one
 * greeting: the things below it do not say the name again.
 */

import { useEffect, useState } from "react";
import MsIcon from "../MsIcon";
import { useBotAccent } from "./useBotAccent";
import {
  getBotPersona,
  PERSONA_CHANGED,
  type BotPersona,
} from "@/app/_lib/services/botPersonaService";

export default function PersonaHeader() {
  const [p, setP] = useState<BotPersona | null>(null);

  useEffect(() => {
    let cancelled = false;
    getBotPersona().then((got) => {
      if (!cancelled) setP(got);
    });
    const onChange = (e: Event) => {
      const got = (e as CustomEvent<BotPersona | null>).detail;
      if (got) setP(got);
    };
    window.addEventListener(PERSONA_CHANGED, onChange);
    return () => {
      cancelled = true;
      window.removeEventListener(PERSONA_CHANGED, onChange);
    };
  }, []);

  const accent = useBotAccent(p?.accent);

  if (!p) return null;

  return (
    <header
      className="flex items-center gap-4"
      style={{ margin: "0 0 clamp(10px,1.2vw,18px)" }}
      aria-label={`${p.name} says hello`}
    >
      <span
        className="flex shrink-0 items-center justify-center"
        style={{
          width: 64,
          height: 64,
          borderRadius: 20,
          background: `color-mix(in srgb, ${accent} 22%, transparent)`,
          border: `2px solid ${accent}`,
          color: accent,
        }}
      >
        <MsIcon name={p.icon || "smart_toy"} size={38} fill />
      </span>
      <div className="min-w-0">
        <div
          style={{
            fontFamily: "var(--font-fredoka), sans-serif",
            fontWeight: 600,
            fontSize: "clamp(24px,2.2vw,36px)",
            color: "var(--text)",
            lineHeight: 1.1,
          }}
        >
          {p.name}
          {p.kind && (
            <span
              style={{
                marginLeft: 10,
                fontSize: "0.5em",
                fontWeight: 600,
                color: accent,
                verticalAlign: "middle",
              }}
            >
              your {p.kind}
            </span>
          )}
        </div>
        <p
          style={{
            margin: "4px 0 0",
            fontFamily: "var(--font-nunito), sans-serif",
            fontWeight: 600,
            fontSize: 17,
            color: "var(--text2)",
          }}
        >
          {p.greeting}
        </p>
      </div>
    </header>
  );
}
