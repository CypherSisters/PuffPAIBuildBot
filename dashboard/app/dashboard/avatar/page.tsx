"use client";

/**
 * Avatar — design-match restyle (04 Avatar), "Choose who you talk to".
 *
 * Keeps the existing engine/persona/voice logic: the engine grid is the real
 * avatar-engine registry (Puff SVG / Digital Human / custom GLB), selection
 * persists exactly like the profile picker (redux + localStorage + saved AI
 * settings), the live stage renders the chosen engine with graceful fallback,
 * personality sliders are the real redux traits persisted to localStorage,
 * and "Preview voice" drives the existing TTS test path (lip-synced when the
 * engine supports it).
 *
 * Copyright (c) 2026 Cypher Sisters. Apache-2.0 License.
 */

import { useCallback, useEffect, useRef, useState, type CSSProperties } from "react";
import Link from "next/link";
import MsIcon from "../../_components/MsIcon";
import { useAppDispatch, useAppSelector } from "@/app/_stores/hooks";
import {
  setAvatarCharacter,
  setAvatarSpeaking,
  setPersonalityTrait,
  setTtsSource,
} from "@/app/_stores/avatarSlice";
import {
  speak as voiceSpeak,
  getVoiceCapabilities,
  isBrowserTtsSupported,
  type VoiceCapabilities,
} from "@/app/_lib/services/voiceService";
import { getAISettings, updateAISettings } from "@/app/_lib/services/profileService";
import type { PersonalityTrait, PersonalityTraitConfig } from "@/app/_interfaces/avatar";
import AvatarStage from "../../_components/Avatar/AvatarStage";
import { listEngines } from "../../_components/Avatar/engines/registry";
import type { AvatarEngineHandle, AvatarEngineId } from "../../_components/Avatar/engines/types";
import useAvatarCharacter, {
  AVATAR_CHARACTER_KEY,
} from "../../_components/Avatar/useAvatarCharacter";

/* ── Design-language shorthands ────────────────────────────── */

const FREDOKA = "var(--font-fredoka), sans-serif";
const NUNITO = "var(--font-nunito), sans-serif";
const JBM = "var(--font-jbmono), monospace";

const STRIPES =
  "repeating-linear-gradient(135deg, var(--chip) 0 10px, transparent 10px 20px)";

const MICRO: CSSProperties = {
  font: `700 11px ${JBM}`,
  letterSpacing: ".14em",
  color: "var(--text3)",
  textTransform: "uppercase",
};

/** Short truthful trait line per engine (design's "WARM · SOFT-SPOKEN" slot). */
const ENGINE_TRAITS: Record<string, string> = {
  puff: "CLOUD MASCOT · ALWAYS WORKS",
  human: "3D HUMAN · VISEME LIP-SYNC",
  glb: "CUSTOM GLB · YOUR MODEL",
};

/** The real personality traits (redux + localStorage), design-styled. */
const PERSONALITY_TRAITS: (Pick<PersonalityTraitConfig, "key" | "lowLabel" | "highLabel"> & {
  gradient: string;
})[] = [
  { key: "warmth", lowLabel: "Cold", highLabel: "Warm", gradient: "linear-gradient(90deg, var(--pink), var(--lav))" },
  { key: "energy", lowLabel: "Calm", highLabel: "Energetic", gradient: "linear-gradient(90deg, var(--sky), var(--mint))" },
  { key: "humor", lowLabel: "Serious", highLabel: "Playful", gradient: "linear-gradient(90deg, var(--pink), var(--lav))" },
  { key: "formality", lowLabel: "Casual", highLabel: "Formal", gradient: "linear-gradient(90deg, var(--sky), var(--mint))" },
  { key: "curiosity", lowLabel: "Practical", highLabel: "Curious", gradient: "linear-gradient(90deg, var(--pink), var(--lav))" },
  { key: "confidence", lowLabel: "Humble", highLabel: "Confident", gradient: "linear-gradient(90deg, var(--sky), var(--mint))" },
  { key: "empathy", lowLabel: "Analytical", highLabel: "Empathetic", gradient: "linear-gradient(90deg, var(--pink), var(--lav))" },
  { key: "creativity", lowLabel: "Logical", highLabel: "Creative", gradient: "linear-gradient(90deg, var(--sky), var(--mint))" },
];

export default function AvatarPage() {
  const dispatch = useAppDispatch();
  const avatarRef = useRef<AvatarEngineHandle>(null);
  const character = useAvatarCharacter();
  const { avatarUrl, mood, speaking, personality, ttsSource } = useAppSelector(
    (state) => state.avatar
  );

  const [aiName, setAiName] = useState("Puff");
  const [caps, setCaps] = useState<VoiceCapabilities | null>(null);
  const [avatarReady, setAvatarReady] = useState(false);
  const [testText, setTestText] = useState("");

  const engines = listEngines();

  // Real AI name + voice engine facts from the backend
  useEffect(() => {
    getAISettings()
      .then((s) => {
        // Any real custom name wins over the Puff default.
        if (s?.ai_name) setAiName(s.ai_name);
      })
      .catch(() => {});
    getVoiceCapabilities()
      .then(setCaps)
      .catch(() => setCaps(null));
  }, []);

  // Restore persisted personality once (same key AvatarPanel used)
  useEffect(() => {
    try {
      const saved = localStorage.getItem("puffpai_personality");
      if (saved) {
        const parsed = JSON.parse(saved);
        Object.entries(parsed).forEach(([key, val]) => {
          dispatch(
            setPersonalityTrait({
              trait: key as PersonalityTrait,
              value: val as number,
            })
          );
        });
      }
    } catch {
      // ignore corrupt data
    }
  }, [dispatch]);

  // Persist personality on change
  useEffect(() => {
    try {
      localStorage.setItem("puffpai_personality", JSON.stringify(personality));
    } catch {
      // storage disabled
    }
  }, [personality]);

  /** Select an engine — same persistence flow as the profile picker. */
  const selectEngine = useCallback(
    (id: AvatarEngineId) => {
      setAvatarReady(false);
      dispatch(setAvatarCharacter(id));
      try {
        localStorage.setItem(AVATAR_CHARACTER_KEY, id);
      } catch {
        // storage disabled — the profile setting still persists server-side
      }
      updateAISettings({ avatar_character: id }).catch(() => {
        // offline — localStorage + redux keep the choice for this device
      });
    },
    [dispatch]
  );

  const handleTraitChange = useCallback(
    (trait: PersonalityTrait, value: number) => {
      dispatch(setPersonalityTrait({ trait, value }));
    },
    [dispatch]
  );

  /** Existing TTS test: lip-synced through the engine when ready. */
  const handlePreviewVoice = useCallback(() => {
    const text =
      testText.trim() ||
      `Hi, I'm ${aiName}. I live right here on your computer. Nothing leaves your computer without your consent.`;
    if (avatarRef.current?.isReady()) {
      avatarRef.current.speakText(text);
    } else {
      voiceSpeak(
        text,
        () => dispatch(setAvatarSpeaking(true)),
        () => dispatch(setAvatarSpeaking(false))
      );
    }
  }, [testText, aiName, dispatch]);

  const piperAvailable = caps?.tts_available === true;
  const browserTts = isBrowserTtsSupported();
  const engineLabel = engines.find((e) => e.id === character)?.label ?? "Puff";

  return (
    <section
      className="puff-avatar-panel grid grid-cols-1 gap-5 lg:grid-cols-[minmax(0,1fr)_clamp(330px,34vw,620px)] lg:gap-[clamp(16px,1.8vw,28px)]"
      style={{
        padding: "clamp(16px,2.4vh,26px) clamp(18px,2vw,36px) clamp(18px,2.8vh,30px)",
        minHeight: "calc(100vh - 7.5rem)",
      }}
    >
      {/* The legacy `.dark input` rule paints form fields slate-800
          !important; this page is themed by --puff vars, so neutralise it. */}
      <style>{`
        .puff-avatar-panel input[type="text"] {
          background-color: var(--card2) !important;
          color: var(--text) !important;
          border-color: var(--line) !important;
        }
        .puff-avatar-panel input[type="text"]::placeholder { color: var(--text3) !important; }
      `}</style>
      {/* ── Left: engine chooser ─────────────────────────────── */}
      <div className="flex min-h-0 min-w-0 flex-col">
        <h1
          style={{
            margin: "0 0 6px",
            font: `600 clamp(30px,3vw,44px) ${FREDOKA}`,
            letterSpacing: "-.8px",
            color: "var(--text)",
          }}
        >
          Choose who you talk to
        </h1>
        <p
          style={{
            margin: "0 0 24px",
            font: `500 clamp(15px,1.2vw,19px) ${NUNITO}`,
            color: "var(--text2)",
          }}
        >
          Every avatar is the same private model underneath. Only the face and
          voice change.
        </p>

        <div className="grid flex-1 grid-cols-2 content-start gap-[18px] xl:grid-cols-3">
          {engines.map((engine) => {
            const selected = engine.id === character;
            return (
              <button
                key={engine.id}
                onClick={() => selectEngine(engine.id)}
                className="flex cursor-pointer flex-col gap-3 text-left transition hover:opacity-95"
                style={{
                  borderRadius: 26,
                  border: selected
                    ? "2px solid var(--pink)"
                    : "1px solid var(--line)",
                  background: "var(--card)",
                  backdropFilter: "blur(18px)",
                  padding: 18,
                  boxShadow: selected ? "0 0 40px var(--glow1)" : undefined,
                }}
                title={engine.description}
              >
                {/* Art slot: real art for Puff; honest striped slot for the
                    3D engines (no committed preview renders yet) */}
                {engine.id === "puff" ? (
                  <div
                    className="grid w-full place-items-center"
                    style={{
                      height: "clamp(130px,20vh,220px)",
                      borderRadius: 18,
                      background:
                        "linear-gradient(160deg, var(--chip), transparent)",
                    }}
                  >
                    <img
                      src="/puffpai-logo.png"
                      alt=""
                      style={{
                        maxHeight: "85%",
                        maxWidth: "80%",
                        objectFit: "contain",
                        filter: "drop-shadow(0 10px 24px var(--glow1))",
                        animation: "puffFloat 6s ease-in-out infinite",
                      }}
                    />
                  </div>
                ) : (
                  <div
                    className="grid w-full place-items-center"
                    style={{
                      height: "clamp(130px,20vh,220px)",
                      borderRadius: 18,
                      background: STRIPES,
                    }}
                  >
                    <span
                      style={{
                        font: `600 12px ${JBM}`,
                        color: "var(--text3)",
                        textAlign: "center",
                        padding: "0 10px",
                      }}
                    >
                      avatar art
                      <br />
                      goes here
                    </span>
                  </div>
                )}

                <div className="flex items-center gap-2">
                  <div style={{ font: `600 22px ${FREDOKA}`, color: "var(--text)" }}>
                    {engine.label}
                  </div>
                  {selected && (
                    <MsIcon
                      name="check_circle"
                      size={20}
                      style={{ color: "var(--pink)" }}
                    />
                  )}
                </div>
                <div style={{ font: `600 13px ${JBM}`, color: "var(--text3)" }}>
                  {ENGINE_TRAITS[engine.id] ?? engine.label.toUpperCase()}
                </div>
              </button>
            );
          })}

          {/* Make your own — the real custom-model flow (GLB URL in Profile) */}
          <Link
            href="/dashboard/profile"
            className="flex cursor-pointer flex-col items-center justify-center gap-3 transition hover:opacity-80"
            style={{
              borderRadius: 26,
              border: "2px dashed var(--line)",
              background: "transparent",
              padding: 18,
              minHeight: "clamp(180px,24vh,280px)",
            }}
            title="Bring your own GLB model (Ready Player Me, Avaturn, Blender). Set it up in Profile"
          >
            <MsIcon name="add" size={44} style={{ color: "var(--text3)" }} />
            <div style={{ font: `600 20px ${FREDOKA}`, color: "var(--text2)" }}>
              Make your own
            </div>
            <div style={{ ...MICRO, textAlign: "center" }}>
              BRING A GLB · IN PROFILE
            </div>
          </Link>
        </div>
      </div>

      {/* ── Right: current companion panel ───────────────────── */}
      <div
        className="flex min-h-0 flex-col overflow-y-auto"
        style={{
          borderRadius: 32,
          border: "1px solid var(--line)",
          background: "var(--card)",
          backdropFilter: "blur(18px)",
          boxShadow: "var(--shadow)",
          padding: "26px 30px",
        }}
      >
        <div className="mb-5 flex items-center gap-5">
          {character === "puff" ? (
            <img
              src="/puffpai-logo.png"
              alt=""
              style={{
                width: 96,
                height: 96,
                flex: "none",
                borderRadius: 28,
                objectFit: "contain",
                background: "var(--chip)",
                padding: 8,
                boxShadow: "0 0 40px var(--glow1)",
              }}
            />
          ) : (
            <div
              className="grid flex-none place-items-center"
              style={{
                width: 96,
                height: 96,
                borderRadius: 28,
                background: STRIPES,
                boxShadow: "0 0 40px var(--glow1)",
              }}
            >
              <MsIcon
                name="face_retouching_natural"
                size={40}
                style={{ color: "var(--text3)" }}
              />
            </div>
          )}
          <div className="min-w-0">
            <div
              className="truncate"
              style={{ font: `600 clamp(26px,2.2vw,34px) ${FREDOKA}`, color: "var(--text)" }}
            >
              {aiName}
            </div>
            <div style={{ font: `600 15px ${NUNITO}`, color: "var(--text2)" }}>
              Your current companion · {engineLabel}
            </div>
          </div>
        </div>

        {/* Live stage — the real engine, with graceful fallback */}
        <div
          className="relative mb-5 flex-none overflow-hidden"
          style={{
            borderRadius: 22,
            border: "1px solid var(--line)",
            background: "var(--card2)",
            height: "clamp(190px,26vh,300px)",
          }}
        >
          <AvatarStage
            engineId={character}
            ref={avatarRef}
            avatarUrl={avatarUrl}
            mood={mood}
            className="h-full w-full"
            style={{ width: "100%", height: "100%" }}
            onLoad={() => setAvatarReady(true)}
            onError={() => setAvatarReady(false)}
            onSpeakStart={() => dispatch(setAvatarSpeaking(true))}
            onSpeakEnd={() => dispatch(setAvatarSpeaking(false))}
          />
          <div className="absolute left-3 top-3 flex items-center gap-2">
            <span
              className="inline-flex items-center gap-1.5"
              style={{
                padding: "6px 12px",
                borderRadius: 99,
                background: avatarReady
                  ? "color-mix(in srgb, var(--mint) 16%, transparent)"
                  : "color-mix(in srgb, var(--lemon) 16%, transparent)",
                color: avatarReady ? "var(--mint)" : "var(--lemon)",
                font: `700 11px ${JBM}`,
                letterSpacing: ".1em",
                backdropFilter: "blur(8px)",
              }}
            >
              <span
                style={{
                  width: 7,
                  height: 7,
                  borderRadius: "50%",
                  background: "currentColor",
                  animation: avatarReady ? undefined : "puffBlink 1.2s infinite",
                }}
              />
              {avatarReady ? "READY" : "LOADING"}
            </span>
            {speaking && (
              <span
                className="inline-flex items-center gap-1.5"
                style={{
                  padding: "6px 12px",
                  borderRadius: 99,
                  background: "color-mix(in srgb, var(--sky) 16%, transparent)",
                  color: "var(--sky)",
                  font: `700 11px ${JBM}`,
                  letterSpacing: ".1em",
                  backdropFilter: "blur(8px)",
                }}
              >
                <span
                  className="puff-live"
                  style={{
                    width: 7,
                    height: 7,
                    borderRadius: "50%",
                    background: "currentColor",
                    animation: "puffBlink 0.8s infinite",
                  }}
                />
                SPEAKING
              </span>
            )}
          </div>
        </div>

        {/* VOICE — the real engines on this device */}
        <div style={{ ...MICRO, marginBottom: 12 }}>Voice</div>
        <div className="mb-5 flex flex-wrap gap-2.5">
          {caps === null ? (
            <span
              style={{
                width: 150,
                height: 43,
                borderRadius: 99,
                background: STRIPES,
                display: "inline-block",
              }}
              title="Checking voice engines…"
            />
          ) : (
            <>
              {piperAvailable && (
                <VoiceChip
                  label={`${aiName}, ${prettyEngine(caps.tts_engine)} (on-device)`}
                  active={ttsSource === "backend"}
                  onClick={() => dispatch(setTtsSource("backend"))}
                />
              )}
              {browserTts && (
                <VoiceChip
                  label="Browser voice"
                  active={ttsSource === "browser" || !piperAvailable}
                  onClick={() => dispatch(setTtsSource("browser"))}
                />
              )}
              {!piperAvailable && !browserTts && (
                <span style={{ font: `600 13px ${NUNITO}`, color: "var(--text3)" }}>
                  No speech engine available
                </span>
              )}
            </>
          )}
        </div>

        {/* PERSONALITY — the real trait set, persisted */}
        <div style={{ ...MICRO, marginBottom: 14 }}>Personality</div>
        <div className="mb-5 grid grid-cols-1 gap-x-6 gap-y-4 2xl:grid-cols-2">
          {PERSONALITY_TRAITS.map((t) => (
            <TraitSlider
              key={t.key}
              lowLabel={t.lowLabel}
              highLabel={t.highLabel}
              gradient={t.gradient}
              value={personality[t.key]}
              onChange={(v) => handleTraitChange(t.key, v)}
            />
          ))}
        </div>

        <div className="flex-1" />

        {/* Preview voice — existing TTS test */}
        <input
          type="text"
          value={testText}
          onChange={(e) => setTestText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") handlePreviewVoice();
          }}
          placeholder={`Type something for ${aiName} to say…`}
          className="mb-3 w-full flex-none outline-none"
          style={{
            height: 48,
            borderRadius: 16,
            background: "var(--card2)",
            border: "1px solid var(--line)",
            padding: "0 18px",
            font: `500 15px ${NUNITO}`,
            color: "var(--text)",
          }}
        />
        <button
          onClick={handlePreviewVoice}
          disabled={speaking}
          className="flex-none cursor-pointer transition hover:opacity-90 disabled:opacity-50"
          style={{
            height: 64,
            borderRadius: 22,
            border: "none",
            background: "linear-gradient(140deg, var(--pink), var(--magenta))",
            color: "#fff",
            font: `700 20px ${NUNITO}`,
            boxShadow: "0 12px 34px var(--glow1)",
          }}
        >
          {speaking ? "Speaking…" : "Preview voice"}
        </button>
      </div>
    </section>
  );
}

/* ── Bits ──────────────────────────────────────────────────── */

function prettyEngine(raw: string): string {
  // "PiperTTS" → "Piper", "VoskSTT" → "Vosk"
  return raw.replace(/(TTS|STT)$/i, "") || raw;
}

function VoiceChip({
  label,
  active,
  onClick,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className="cursor-pointer transition hover:opacity-90"
      style={
        active
          ? {
              padding: "12px 20px",
              borderRadius: 99,
              border: "none",
              background: "linear-gradient(140deg, var(--pink), var(--magenta))",
              color: "#fff",
              font: `700 15px ${NUNITO}`,
            }
          : {
              padding: "12px 20px",
              borderRadius: 99,
              background: "var(--card2)",
              border: "1px solid var(--line)",
              font: `700 15px ${NUNITO}`,
              color: "var(--text2)",
            }
      }
    >
      {label}
    </button>
  );
}

function TraitSlider({
  lowLabel,
  highLabel,
  gradient,
  value,
  onChange,
}: {
  lowLabel: string;
  highLabel: string;
  gradient: string;
  value: number;
  onChange: (v: number) => void;
}) {
  return (
    <div>
      <div
        className="mb-2 flex justify-between"
        style={{ font: `700 14px ${NUNITO}`, color: "var(--text2)" }}
      >
        <span>{lowLabel}</span>
        <span>{highLabel}</span>
      </div>
      <input
        type="range"
        min={0}
        max={100}
        step={1}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full cursor-pointer appearance-none
          [&::-webkit-slider-thumb]:h-[26px] [&::-webkit-slider-thumb]:w-[26px]
          [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:rounded-full
          [&::-webkit-slider-thumb]:bg-(--puff-text-strong)
          [&::-webkit-slider-thumb]:shadow-[0_4px_14px_rgba(0,0,0,.3)]
          [&::-moz-range-thumb]:h-[26px] [&::-moz-range-thumb]:w-[26px]
          [&::-moz-range-thumb]:rounded-full [&::-moz-range-thumb]:border-0
          [&::-moz-range-thumb]:bg-(--puff-text-strong)
          [&::-moz-range-thumb]:shadow-[0_4px_14px_rgba(0,0,0,.3)]"
        style={{
          height: 10,
          borderRadius: 99,
          background: `${gradient} 0 0 / ${value}% 100% no-repeat, var(--chip)`,
        }}
      />
    </div>
  );
}
