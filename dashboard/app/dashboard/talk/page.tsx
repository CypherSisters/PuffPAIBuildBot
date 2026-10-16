"use client";

/**
 * /dashboard/talk — full-screen voice conversation with Puff (design 02 Talk).
 *
 * Full-bleed cloud scene, Puff floating in a glass circle with pulsing rings,
 * live status pill + transcript line, and the SAME offline voice loop the
 * AvatarTile / FloatingPuff panel uses:
 *
 *   mic (16 kHz PCM) → /voice/transcribe (Vosk) → /chat/send (Cortex)
 *                    → /voice/synthesize (Piper) → speakPcm lipsync
 *
 * Entering the page starts listening automatically (when voice is available);
 * after Puff answers she listens again — a continuous conversation. If she
 * hears nothing the loop parks in idle and a tap on Puff restarts it.
 * Leaving the page releases the mic and stops any speech.
 *
 * Composes the shared PuffCloudAvatar engine (never modifies it) exactly the
 * way FloatingPuff does: SVG engine mounted for audio + lipsync handle, the
 * hand-drawn logo as the visible skin.
 *
 * Copyright (c) 2026 Cypher Sisters. Apache-2.0 License.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import MsIcon from "@/app/_components/MsIcon";
import PuffCloudAvatar from "@/app/_components/Avatar/PuffCloudAvatar";
import type { AvatarEngineHandle } from "@/app/_components/Avatar/engines/types";
import { useAppDispatch } from "@/app/_stores/hooks";
import { setAvatarSpeaking, setIsListening } from "@/app/_stores/avatarSlice";
import {
  transcribe,
  synthesize,
  browserListen,
  browserSpeak,
  browserSpeakStop,
  playPcmAudio,
  getVoiceCapabilities,
  isBrowserSttSupported,
} from "@/app/_lib/services/voiceService";
import {
  startMicCapture,
  isMicCaptureSupported,
  type MicCaptureController,
} from "@/app/_lib/services/micCapture";
import { sendMessage } from "@/app/_lib/services/chatService";

type Phase = "idle" | "listening" | "thinking" | "speaking";

/** 12-bar waveform straight from the design (heights % · palette vars). */
const WAVE_BARS: Array<{ h: number; c: string }> = [
  { h: 40, c: "var(--pink)" },
  { h: 75, c: "var(--pink)" },
  { h: 100, c: "var(--magenta)" },
  { h: 55, c: "var(--lav)" },
  { h: 88, c: "var(--sky)" },
  { h: 35, c: "var(--sky)" },
  { h: 92, c: "var(--pink)" },
  { h: 48, c: "var(--magenta)" },
  { h: 70, c: "var(--lav)" },
  { h: 30, c: "var(--sky)" },
  { h: 82, c: "var(--pink)" },
  { h: 45, c: "var(--magenta)" },
];

/** Strip markdown so Piper doesn't read asterisks and pound signs aloud. */
function sanitizeForSpeech(text: string): string {
  return text
    .replace(/```[\s\S]*?```/g, " code omitted. ")
    .replace(/`([^`]*)`/g, "$1")
    .replace(/!\[[^\]]*\]\([^)]*\)/g, "")
    .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/[*_~>|]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

const PILL: Record<
  Phase,
  { label: string; color: string; bg: string }
> = {
  listening: {
    label: "LISTENING · LIVE",
    color: "var(--mint)",
    bg: "rgba(110,231,183,.16)",
  },
  thinking: {
    label: "THINKING",
    color: "var(--lav)",
    bg: "color-mix(in srgb, var(--lav) 16%, transparent)",
  },
  speaking: {
    label: "SPEAKING",
    color: "var(--pink)",
    bg: "color-mix(in srgb, var(--pink) 16%, transparent)",
  },
  idle: {
    label: "IDLE · TAP PUFF",
    color: "var(--text3)",
    bg: "var(--chip)",
  },
};

export default function TalkPage() {
  const router = useRouter();
  const dispatch = useAppDispatch();

  const avatarRef = useRef<AvatarEngineHandle>(null);
  const micRef = useRef<MicCaptureController | null>(null);
  const browserListenRef = useRef<{ stop: () => void } | null>(null);
  const convIdRef = useRef<string | null>(null);
  const mountedRef = useRef(true);
  const startedRef = useRef(false);
  const restartTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const [phase, setPhase] = useState<Phase>("idle");
  const [lastUser, setLastUser] = useState<string | null>(null);
  const [replyText, setReplyText] = useState<string | null>(null);
  const [caption, setCaption] = useState<string | null>(null);
  // null = capabilities still probing; then true/false.
  const [voiceReady, setVoiceReady] = useState<boolean | null>(null);
  // True when the OFFLINE path (local mic capture → backend Vosk) is in use —
  // gates the "turned into words on this computer" line to when it is true.
  const [sttLocal, setSttLocal] = useState(false);

  const endTurn = useCallback(
    (message?: string) => {
      if (!mountedRef.current) return;
      setPhase("idle");
      if (message) setCaption(message);
      dispatch(setIsListening(false));
      dispatch(setAvatarSpeaking(false));
    },
    [dispatch]
  );

  /** One full voice turn: listen → transcribe → think → speak. Auto-chains
   *  into the next turn after a spoken reply (continuous conversation). */
  const runVoiceTurn = useCallback(async () => {
    if (!mountedRef.current) return;
    setCaption(null);
    setReplyText(null);
    setPhase("listening");
    dispatch(setIsListening(true));

    // ── 1. Listen ──────────────────────────────────────────────
    let transcript = "";
    try {
      const caps = await getVoiceCapabilities();
      if (caps.stt_available && isMicCaptureSupported()) {
        // Offline path: local capture → backend Vosk
        const controller = await startMicCapture({
          maxMs: 20000,
          silenceMs: 1400,
        });
        micRef.current = controller;
        const audio = await controller.done;
        micRef.current = null;
        if (!audio || !audio.heardSpeech) {
          endTurn("Didn't catch that. Tap Puff to try again.");
          return;
        }
        setPhase("thinking");
        dispatch(setIsListening(false));
        const stt = await transcribe(audio.pcmBase64);
        transcript = (stt.success && stt.text) || "";
      } else {
        // Fallback: browser SpeechRecognition (needs internet in Chrome)
        transcript = await new Promise<string>((resolve) => {
          const c = browserListen(
            (text) => resolve(text),
            () => resolve(""),
            () => resolve("")
          );
          browserListenRef.current = c;
          if (!c) resolve("");
        });
        browserListenRef.current = null;
        setPhase("thinking");
        dispatch(setIsListening(false));
      }
    } catch (e) {
      console.warn("[Talk] mic error:", e);
      endTurn("Microphone unavailable. Check browser permissions.");
      return;
    }

    transcript = transcript.trim();
    if (!transcript) {
      endTurn("Didn't catch that. Tap Puff to try again.");
      return;
    }
    if (!mountedRef.current) return;
    setLastUser(transcript);
    setPhase("thinking");

    // ── 2. Think (full Cortex agent via /chat/send; persists history) ──
    let reply = "";
    try {
      const result = await sendMessage(transcript, convIdRef.current ?? undefined);
      convIdRef.current = result.conversation_id;
      reply = result.response || "";
    } catch (e) {
      console.warn("[Talk] chat error:", e);
      endTurn("I couldn't reach the backend. Is PuffPAI running?");
      return;
    }
    if (!mountedRef.current) return;
    if (!reply.trim()) {
      endTurn("I didn't get a response. Tap Puff to try again.");
      return;
    }

    // ── 3. Speak (Piper TTS → lipsync; graceful fallbacks) ─────
    setReplyText(reply);
    setPhase("speaking");
    dispatch(setAvatarSpeaking(true));
    const speech = sanitizeForSpeech(reply);
    try {
      const tts = await synthesize(speech);
      if (tts.success && tts.audio_base64) {
        const rate = tts.sample_rate || 22050;
        if (avatarRef.current?.isReady()) {
          await avatarRef.current.speakPcm(tts.audio_base64, rate, speech);
        } else {
          await playPcmAudio(tts.audio_base64, rate);
        }
      } else {
        await new Promise<void>((resolve) => {
          browserSpeak(speech, resolve);
          setTimeout(resolve, 30000); // safety net if browser TTS dies silently
        });
      }
    } catch (e) {
      console.warn("[Talk] TTS error:", e);
    }
    endTurn();

    // ── 4. Keep the conversation going ─────────────────────────
    if (mountedRef.current) {
      restartTimerRef.current = setTimeout(() => {
        if (mountedRef.current) void runVoiceTurn();
      }, 600);
    }
  }, [dispatch, endTurn]);

  // Voice availability probe + auto-start listening on entry.
  useEffect(() => {
    mountedRef.current = true;
    getVoiceCapabilities().then((caps) => {
      if (!mountedRef.current) return;
      const local = caps.stt_available && isMicCaptureSupported();
      const ready = local || isBrowserSttSupported();
      setSttLocal(local);
      setVoiceReady(ready);
      if (ready && !startedRef.current) {
        startedRef.current = true;
        void runVoiceTurn();
      }
    });
    return () => {
      mountedRef.current = false;
    };
  }, [runVoiceTurn]);

  // Leaving the page releases the mic and stops any speech.
  useEffect(() => {
    return () => {
      if (restartTimerRef.current) clearTimeout(restartTimerRef.current);
      micRef.current?.cancel();
      browserListenRef.current?.stop();
      avatarRef.current?.stopSpeaking();
      browserSpeakStop();
      dispatch(setIsListening(false));
      dispatch(setAvatarSpeaking(false));
    };
  }, [dispatch]);

  const stopEverything = useCallback(() => {
    if (restartTimerRef.current) clearTimeout(restartTimerRef.current);
    micRef.current?.cancel();
    micRef.current = null;
    browserListenRef.current?.stop();
    browserListenRef.current = null;
    avatarRef.current?.stopSpeaking();
    browserSpeakStop();
    endTurn();
  }, [endTurn]);

  /** Tap Puff: idle → listen; listening → finish utterance now;
   *  speaking → interrupt; thinking → hang tight. */
  const handlePuffTap = useCallback(() => {
    if (phase === "listening") {
      micRef.current?.stop();
      browserListenRef.current?.stop();
      return;
    }
    if (phase === "speaking") {
      avatarRef.current?.stopSpeaking();
      browserSpeakStop();
      endTurn();
      return;
    }
    if (phase === "thinking") return;
    if (voiceReady) void runVoiceTurn();
  }, [phase, voiceReady, runVoiceTurn, endTurn]);

  const handleCancel = useCallback(() => {
    stopEverything();
    if (typeof window !== "undefined" && window.history.length > 1) {
      router.back();
    } else {
      router.push("/dashboard");
    }
  }, [router, stopEverything]);

  const active = phase === "listening" || phase === "speaking";
  const pill =
    voiceReady === false
      ? {
          label: "VOICE OFFLINE",
          color: "var(--lemon)",
          bg: "color-mix(in srgb, var(--lemon) 16%, transparent)",
        }
      : PILL[phase];

  /* Transcript line: her reply while speaking, your words while she thinks,
     honest placeholders otherwise. */
  let transcriptNode: React.ReactNode;
  if (phase === "speaking" && replyText) {
    transcriptNode = (
      <span
        style={{
          display: "-webkit-box",
          WebkitLineClamp: 3,
          WebkitBoxOrient: "vertical",
          overflow: "hidden",
        }}
      >
        {replyText}
      </span>
    );
  } else if ((phase === "thinking" || phase === "listening") && lastUser) {
    transcriptNode = (
      <>
        &ldquo;{lastUser}
        <span style={{ color: "var(--text3)" }}>&hellip;</span>&rdquo;
      </>
    );
  } else if (phase === "listening") {
    transcriptNode = (
      <span style={{ color: "var(--text3)" }}>Say something&hellip;</span>
    );
  } else if (phase === "thinking") {
    transcriptNode = (
      <span style={{ color: "var(--text3)" }}>Thinking&hellip;</span>
    );
  } else if (voiceReady === false) {
    transcriptNode = (
      <span style={{ color: "var(--text3)" }}>
        Voice is offline. Type instead.
      </span>
    );
  } else if (caption) {
    transcriptNode = <span style={{ color: "var(--text3)" }}>{caption}</span>;
  } else {
    transcriptNode = (
      <span style={{ color: "var(--text3)" }}>Say something&hellip;</span>
    );
  }

  return (
    <section
      className="relative grid place-items-center overflow-hidden"
      style={{ height: "calc(100dvh - 3.5rem)" }}
    >
      {/* Cloud scene + vignette to the page background. objectPosition keeps
          the crop on the photo's smooth pastel band: `cover` on a 442×612
          portrait across a full-bleed screen magnifies its three cloud
          shapes into hard coloured blobs either side of Puff. */}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src="/design-cloud-2.jpg"
        alt=""
        aria-hidden
        className="absolute inset-0 h-full w-full object-cover"
        style={{ opacity: "var(--img4)", objectPosition: "center 88%" }}
      />
      <div
        className="absolute inset-0"
        aria-hidden
        style={{
          background:
            "radial-gradient(700px 500px at 50% 45%, transparent, var(--puff-vignette, var(--bg)) 78%)",
        }}
      />

      <div className="relative flex flex-col items-center px-[60px] text-center">
        {/* Puff + pulse rings (rings only animate while a turn is live) */}
        {/* The outermost ring sits at inset -110, so the design's 34px gap
            leaves the status pill sitting on top of it. 120px clears the
            ring stack with room to spare. */}
        <div
          className="relative mb-[120px] grid place-items-center"
          style={{
            width: "clamp(180px,26vh,340px)",
            height: "clamp(180px,26vh,340px)",
          }}
        >
          {[
            { inset: -30, border: "3px solid var(--pink)", delay: "0s" },
            { inset: -70, border: "3px solid var(--sky)", delay: ".7s" },
            { inset: -110, border: "2px solid var(--lav)", delay: "1.4s" },
          ].map((ring, i) => (
            <div
              key={i}
              aria-hidden
              // puff-live: these rings only animate during a live turn, and the
              // thinking phase has no Redux voice flag to hold IdleMotionGuard
              // off — so they must outrank the ambient puffPulse pause.
              className="puff-live pointer-events-none absolute rounded-full"
              style={{
                inset: ring.inset,
                border: ring.border,
                ...(active || phase === "thinking"
                  ? {
                      animation: `puffPulse 2.6s ease-out ${ring.delay} infinite`,
                    }
                  : { opacity: 0.22 }),
              }}
            />
          ))}
          <button
            type="button"
            onClick={handlePuffTap}
            aria-label={
              phase === "listening"
                ? "Done talking"
                : phase === "speaking"
                  ? "Stop Puff speaking"
                  : "Tap to talk to Puff"
            }
            title={
              phase === "listening"
                ? "Done talking"
                : phase === "speaking"
                  ? "Stop speaking"
                  : "Tap to talk"
            }
            className="relative h-full w-full cursor-pointer rounded-full transition-transform duration-150 hover:scale-[1.03] active:scale-95"
            style={{
              background: "var(--card)",
              border: "1px solid var(--line)",
              backdropFilter: "blur(18px)",
              boxShadow: "0 0 90px var(--glow1)",
            }}
          >
            {/* Shared SVG engine mounted for audio + lipsync handle; the
                visible skin is the hand-drawn Puff (FloatingPuff pattern). */}
            <PuffCloudAvatar
              ref={avatarRef}
              avatarUrl=""
              className="pointer-events-none h-full w-full [&_svg]:invisible"
            />
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src="/puffpai-logo.png"
              alt="Puff"
              className="pointer-events-none absolute inset-[6%] h-[88%] w-[88%] object-contain"
              style={{
                animation: "puffFloat 6s ease-in-out infinite",
                filter: "drop-shadow(0 0 24px var(--glow1))",
              }}
            />
          </button>
        </div>

        {/* Status pill — real voice state */}
        <div
          className="mb-[20px] inline-flex items-center gap-[9px] rounded-full px-5 py-[9px]"
          style={{
            background: pill.bg,
            color: pill.color,
            font: "700 14px var(--font-jbmono), monospace",
            letterSpacing: ".12em",
          }}
        >
          <span
            className={`inline-block h-[9px] w-[9px] rounded-full ${active ? "animate-pulse" : ""}`}
            style={{ background: "currentcolor" }}
          />
          {pill.label}
        </div>

        {/* Live transcript line */}
        <div
          style={{
            font: "500 clamp(26px,2.8vw,46px)/1.3 var(--font-fredoka), sans-serif",
            maxWidth: "min(1100px,86vw)",
            textWrap: "pretty",
            color: "var(--text)",
          }}
        >
          {transcriptNode}
        </div>

        {/* Waveform — animates only while listening/speaking */}
        <div className="mt-[40px] flex h-[78px] items-end gap-[7px]" aria-hidden>
          {WAVE_BARS.map((bar, i) => (
            <span
              key={i}
              className="w-2 rounded-full"
              style={{
                height: `${bar.h}%`,
                background: bar.c,
                ...(active
                  ? {
                      animation: `puffWave 1s ease-in-out ${(i * 0.07).toFixed(2)}s infinite`,
                    }
                  : { opacity: 0.55 }),
              }}
            />
          ))}
        </div>

        {/* Cancel / Type instead */}
        <div className="mt-[44px] flex gap-4">
          <button
            type="button"
            onClick={handleCancel}
            className="flex h-[72px] cursor-pointer items-center gap-[10px] rounded-full px-[34px] transition hover:brightness-105 active:scale-95"
            style={{
              border: "1px solid var(--line)",
              background: "var(--card)",
              color: "var(--text)",
              font: "700 19px var(--font-nunito), sans-serif",
              backdropFilter: "blur(18px)",
            }}
          >
            <MsIcon name="close" size={24} />
            Cancel
          </button>
          <button
            type="button"
            onClick={() => router.push("/dashboard/chat")}
            className="flex h-[72px] cursor-pointer items-center gap-[10px] rounded-full px-[34px] text-(--puff-text-strong) transition hover:brightness-110 active:scale-95"
            style={{
              background: "linear-gradient(140deg,var(--pink),var(--magenta))",
              font: "700 19px var(--font-nunito), sans-serif",
              boxShadow: "0 12px 36px var(--glow1)",
            }}
          >
            <MsIcon name="keyboard" size={24} />
            Type instead
          </button>
        </div>

        {/* Privacy footer — claim only what's true right now */}
        <div
          className="mt-[34px] flex items-center gap-2"
          style={{
            font: "500 16px var(--font-nunito), sans-serif",
            color: "var(--text3)",
          }}
        >
          <span
            className="h-[7px] w-[7px] rounded-full"
            style={{ background: "var(--magenta)" }}
          />
          {sttLocal
            ? "Your voice is turned into words on this computer"
            : voiceReady === null
              ? "Checking the voice engines"
              : voiceReady
                ? "Your browser's own speech service is hearing you, because the voice engines on this computer are off"
                : "Voice engines are offline"}
        </div>
      </div>
    </section>
  );
}
