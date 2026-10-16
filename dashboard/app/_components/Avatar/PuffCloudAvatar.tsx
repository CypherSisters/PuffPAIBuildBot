"use client";

/**
 * Puff — the PuffPAI cloud mascot as a live, talking avatar.
 *
 * Pure inline SVG (no Three.js, no GLB download): idle she is pixel-for-pixel
 * the logo in public/puffpai-mascot.svg. While she speaks, her smile morphs
 * into an open mouth driven straight off the audio waveform through a Web
 * Audio AnalyserNode — no viseme model, just honest amplitude.
 *
 * Implements the SAME handle contract as TalkingHeadAvatar, so the three
 * renderers (overlay, tile, panel) can swap between the digital human and
 * Puff without touching their ref/sink/speak code.
 *
 * Copyright (c) 2026 Cypher Sisters. Apache-2.0 License.
 */

import {
  useEffect,
  useRef,
  useImperativeHandle,
  forwardRef,
  useState,
  useCallback,
} from "react";
import type { AvatarMood, AvatarView } from "../../_interfaces/avatar";
import type { AvatarEngineHandle } from "./engines/types";
import {
  synthesize,
  browserSpeak,
  browserSpeakStop,
} from "@/app/_lib/services/voiceService";

interface Props {
  avatarUrl: string;
  mood?: AvatarMood;
  view?: AvatarView;
  mute?: boolean;
  className?: string;
  style?: React.CSSProperties;
  onLoad?: () => void;
  onError?: (error: string) => void;
  onSpeakStart?: () => void;
  onSpeakEnd?: () => void;
}

/* ── Mouth geometry ─────────────────────────────────────────────
 * One path does both states. The shape is a lens: the logo's smile
 * curve (M236 138 q20 16 40 0) as the TOP edge, and a second curve
 * back to the start as the BOTTOM edge. At openness 0 both curves
 * have the same control point, so the lens has zero area — the fill
 * disappears and the round 10px stroke draws exactly the original
 * smile. Opening pulls the top curve up and the bottom curve down,
 * and the fill grows into it. No attribute swapping needed.
 */

// Palette matched to the original hand-drawn Puff: marker-black
// outline, mid-magenta body, lighter top puffs and highlight swishes.
const OUTLINE = "#221226";
const MOUTH_STROKE = "#2b1136";
const TONGUE_FILL = "#7a2a58";

/** Mouth corners and the resting control-point Y of the logo smile. */
const CORNER_L = 236;
const CORNER_R = 276;
const CORNER_Y = 138;
const REST_CY = 154; // 138 + 16, i.e. the "q20 16" control

function mouthPath(open: number): string {
  const top = REST_CY - 12 * open;
  const bottom = REST_CY + 34 * open;
  return (
    `M${CORNER_L} ${CORNER_Y} ` +
    `Q256 ${top.toFixed(2)} ${CORNER_R} ${CORNER_Y} ` +
    `Q256 ${bottom.toFixed(2)} ${CORNER_L} ${CORNER_Y} Z`
  );
}

/** Soft inner-mouth arc, sized to stay inside the lens at every openness. */
function tonguePath(open: number): string {
  const top = 146 + 6 * open;
  const dip = 146 + 22 * open;
  return `M248 ${top.toFixed(2)} Q256 ${dip.toFixed(2)} 264 ${top.toFixed(2)} Z`;
}

const CLOSED_MOUTH_D = mouthPath(0);
const CLOSED_TONGUE_D = tonguePath(0);

/* ── Component ──────────────────────────────────────────────── */

const PuffCloudAvatar = forwardRef<AvatarEngineHandle, Props>(
  function PuffCloudAvatar(props, ref) {
    const {
      className = "",
      style,
      onLoad,
      onSpeakStart,
      onSpeakEnd,
    } = props;

    const mouthRef = useRef<SVGPathElement>(null);
    const tongueRef = useRef<SVGPathElement>(null);
    const mountedRef = useRef(false);
    const loadedRef = useRef(false);

    // Animation state lives in refs — the mouth is written straight to the
    // DOM every frame, so speaking never triggers a React re-render.
    const openRef = useRef(0);
    const rafRef = useRef<number | null>(null);
    const audioCtxRef = useRef<AudioContext | null>(null);
    const sourceRef = useRef<AudioBufferSourceNode | null>(null);
    // Resolver for the utterance currently being awaited, so an interruption
    // (stopSpeaking / unmount) releases the caller instead of hanging it.
    const endUtteranceRef = useRef<(() => void) | null>(null);
    // Bumped per utterance; a superseded one must not clean up after the new.
    const utteranceIdRef = useRef(0);

    const [speaking, setSpeaking] = useState(false);

    /** Push the current openness into the DOM. */
    const paint = useCallback(() => {
      const o = openRef.current;
      mouthRef.current?.setAttribute("d", mouthPath(o));
      const tongue = tongueRef.current;
      if (tongue) {
        tongue.setAttribute("d", tonguePath(o));
        tongue.setAttribute("opacity", (o * 0.85).toFixed(3));
      }
    }, []);

    const stopRaf = useCallback(() => {
      if (rafRef.current !== null) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
    }, []);

    /** Ease the mouth shut after an utterance instead of snapping. */
    const releaseMouth = useCallback(() => {
      stopRaf();
      const step = () => {
        openRef.current *= 0.8;
        if (openRef.current < 0.005) {
          openRef.current = 0;
          paint();
          rafRef.current = null;
          return;
        }
        paint();
        rafRef.current = requestAnimationFrame(step);
      };
      rafRef.current = requestAnimationFrame(step);
    }, [paint, stopRaf]);

    /** Tear down the audio graph for the current utterance. */
    const teardownAudio = useCallback(() => {
      const src = sourceRef.current;
      sourceRef.current = null;
      if (src) {
        try {
          src.onended = null;
          src.stop();
        } catch {
          // already stopped
        }
      }
      const ctx = audioCtxRef.current;
      audioCtxRef.current = null;
      if (ctx && ctx.state !== "closed") {
        void ctx.close().catch(() => {});
      }
    }, []);

    useEffect(() => {
      mountedRef.current = true;
      if (!loadedRef.current) {
        loadedRef.current = true;
        onLoad?.();
      }
      return () => {
        mountedRef.current = false;
        stopRaf();
        teardownAudio();
        browserSpeakStop();
        endUtteranceRef.current?.();
      };
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    /* ── Speech ─────────────────────────────────────────────── */

    /**
     * Play Piper PCM (s16le mono, base64) and flap the mouth from its RMS.
     * Fast attack / slow decay keeps it lively without jittering per frame.
     */
    const speakPcm = useCallback(
      async (pcmBase64: string, sampleRate: number, _text: string) => {
        if (typeof window === "undefined") return;
        // One utterance at a time: release any previous awaiter first.
        const myId = ++utteranceIdRef.current;
        stopRaf();
        teardownAudio();
        endUtteranceRef.current?.();

        const raw = atob(pcmBase64);
        const n = Math.floor(raw.length / 2);
        if (n === 0) return;

        const AC: typeof AudioContext =
          window.AudioContext ||
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          (window as any).webkitAudioContext;
        if (!AC) return;
        const ctx = new AC();
        audioCtxRef.current = ctx;

        const buffer = ctx.createBuffer(1, n, sampleRate);
        const channel = buffer.getChannelData(0);
        for (let i = 0; i < n; i++) {
          const lo = raw.charCodeAt(2 * i);
          const hi = raw.charCodeAt(2 * i + 1);
          let s = (hi << 8) | lo;
          if (s >= 0x8000) s -= 0x10000;
          channel[i] = s / 32768;
        }

        const source = ctx.createBufferSource();
        source.buffer = buffer;
        const analyser = ctx.createAnalyser();
        analyser.fftSize = 1024;
        source.connect(analyser);
        analyser.connect(ctx.destination);
        sourceRef.current = source;

        const samples = new Float32Array(analyser.fftSize);
        const tick = () => {
          analyser.getFloatTimeDomainData(samples);
          let sum = 0;
          for (let i = 0; i < samples.length; i++) sum += samples[i] * samples[i];
          const rms = Math.sqrt(sum / samples.length);
          const target = Math.max(0, Math.min(1, rms * 8));
          openRef.current =
            target > openRef.current
              ? openRef.current + (target - openRef.current) * 0.5
              : openRef.current * 0.85;
          paint();
          rafRef.current = requestAnimationFrame(tick);
        };

        setSpeaking(true);
        onSpeakStart?.();

        await new Promise<void>((resolve) => {
          let done = false;
          const finish = () => {
            if (done) return;
            done = true;
            endUtteranceRef.current = null;
            resolve();
          };
          endUtteranceRef.current = finish;
          source.onended = finish;
          try {
            void ctx.resume().catch(() => {});
            source.start();
            rafRef.current = requestAnimationFrame(tick);
          } catch (e) {
            console.error("[PuffCloudAvatar] playback error:", e);
            finish();
          }
        });

        if (utteranceIdRef.current !== myId) return; // superseded — hands off
        teardownAudio();
        releaseMouth();
        if (mountedRef.current) setSpeaking(false);
        onSpeakEnd?.();
      },
      [onSpeakStart, onSpeakEnd, paint, releaseMouth, stopRaf, teardownAudio]
    );

    /**
     * Speak text. Piper first (keeps her real voice); browser TTS only if the
     * backend can't answer — and then the mouth runs on a gentle sine, since
     * SpeechSynthesis gives us no waveform to analyse.
     */
    const speakText = useCallback(
      async (text: string) => {
        if (!text.trim()) return;
        const tts = await synthesize(text);
        if (tts.success && tts.audio_base64) {
          await speakPcm(tts.audio_base64, tts.sample_rate || 22050, text);
          return;
        }

        // Browser fallback: fake the flap.
        const myId = ++utteranceIdRef.current;
        stopRaf();
        endUtteranceRef.current?.();
        setSpeaking(true);
        onSpeakStart?.();
        const start =
          typeof performance !== "undefined" ? performance.now() : Date.now();
        const tick = () => {
          const now =
            typeof performance !== "undefined" ? performance.now() : Date.now();
          const t = (now - start) / 1000;
          openRef.current = 0.3 + 0.3 * Math.sin(t * 2 * Math.PI * 8);
          paint();
          rafRef.current = requestAnimationFrame(tick);
        };
        rafRef.current = requestAnimationFrame(tick);

        await new Promise<void>((resolve) => {
          let done = false;
          const finish = () => {
            if (done) return;
            done = true;
            endUtteranceRef.current = null;
            resolve();
          };
          endUtteranceRef.current = finish;
          const utterance = browserSpeak(text, finish);
          if (!utterance) finish();
          else setTimeout(finish, 30000); // safety net if browser TTS dies
        });

        if (utteranceIdRef.current !== myId) return; // superseded — hands off
        releaseMouth();
        if (mountedRef.current) setSpeaking(false);
        onSpeakEnd?.();
      },
      [onSpeakStart, onSpeakEnd, paint, releaseMouth, speakPcm, stopRaf]
    );

    useImperativeHandle(
      ref,
      () => ({
        speakText,
        speakPcm,
        stopSpeaking: () => {
          stopRaf();
          const finish = endUtteranceRef.current;
          teardownAudio();
          browserSpeakStop();
          openRef.current = 0;
          paint();
          if (finish) {
            // The in-flight speakPcm/speakText resumes and reports the end.
            finish();
            return;
          }
          if (mountedRef.current) setSpeaking(false);
          onSpeakEnd?.();
        },
        setMood: () => {
          // Puff's expression is fixed for now — she's relentlessly cheerful.
        },
        setView: () => {
          // No camera to move.
        },
        isReady: () => mountedRef.current,
      }),
      [speakText, speakPcm, onSpeakEnd, paint, stopRaf, teardownAudio]
    );

    /* ── Render ─────────────────────────────────────────────── */

    return (
      <div
        className={`relative flex items-center justify-center overflow-hidden ${className}`}
        style={style}
      >
        <style>{`
          @keyframes puffCloudBob {
            0%, 100% { transform: translateY(-4px); }
            50%      { transform: translateY(4px); }
          }
          .puff-cloud-bob {
            animation: puffCloudBob 4s ease-in-out infinite;
          }
          .puff-cloud-bob-speaking {
            animation-duration: 2.4s;
          }
          @media (prefers-reduced-motion: reduce) {
            .puff-cloud-bob { animation: none; }
          }
        `}</style>
        <svg
          viewBox="0 0 512 360"
          width="100%"
          height="100%"
          preserveAspectRatio="xMidYMid meet"
          role="img"
          aria-label="Puff, the PuffPAI cloud mascot"
        >
          <defs>
            <linearGradient id="puffCloudBody" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stopColor="#d883e6" />
              <stop offset="1" stopColor="#c964d9" />
            </linearGradient>
          </defs>

          <g
            className={`puff-cloud-bob${
              speaking ? " puff-cloud-bob-speaking" : ""
            }`}
          >
            {/* Marker outline: the whole silhouette re-drawn dark and fat
                underneath, so the union of puffs gets one bold sticker rim —
                the thing the original hand-drawn Puff has and flat blobs
                don't. */}
            <g fill={OUTLINE} stroke={OUTLINE} strokeWidth="16">
              <ellipse cx="120" cy="210" rx="105" ry="78" />
              <ellipse cx="392" cy="215" rx="108" ry="75" />
              <ellipse cx="256" cy="120" rx="120" ry="95" />
              <ellipse cx="150" cy="140" rx="85" ry="65" />
              <ellipse cx="362" cy="145" rx="85" ry="62" />
              <ellipse cx="256" cy="228" rx="150" ry="88" />
            </g>

            {/* back puffs (lighter rim) */}
            <g fill="#efb3f2">
              <ellipse cx="120" cy="210" rx="105" ry="78" />
              <ellipse cx="392" cy="215" rx="108" ry="75" />
              <ellipse cx="256" cy="120" rx="120" ry="95" />
              <ellipse cx="150" cy="140" rx="85" ry="65" />
              <ellipse cx="362" cy="145" rx="85" ry="62" />
            </g>

            {/* main body */}
            <g fill="url(#puffCloudBody)">
              <ellipse cx="124" cy="214" rx="96" ry="70" />
              <ellipse cx="388" cy="219" rx="99" ry="67" />
              <ellipse cx="256" cy="126" rx="110" ry="86" />
              <ellipse cx="154" cy="146" rx="76" ry="57" />
              <ellipse cx="358" cy="150" rx="76" ry="55" />
              <ellipse cx="256" cy="228" rx="150" ry="88" />
            </g>

            {/* face: happy closed eyes */}
            <g
              stroke={MOUTH_STROKE}
              strokeWidth="10"
              strokeLinecap="round"
              fill="none"
            >
              <path d="M212 106 q14 -16 28 0" />
              <path d="M272 106 q14 -16 28 0" />
            </g>

            {/* mouth: smile at rest, opens with the audio */}
            <path
              ref={mouthRef}
              d={CLOSED_MOUTH_D}
              fill={MOUTH_STROKE}
              stroke={MOUTH_STROKE}
              strokeWidth="10"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
            <path
              ref={tongueRef}
              d={CLOSED_TONGUE_D}
              fill={TONGUE_FILL}
              opacity="0"
            />

            {/* lashes */}
            <g stroke="#2b1136" strokeWidth="7" strokeLinecap="round">
              <path d="M209 88 l-6 -10" />
              <path d="M222 84 l-3 -11" />
              <path d="M236 84 l2 -11" />
              <path d="M281 84 l-2 -11" />
              <path d="M295 84 l3 -11" />
              <path d="M308 88 l6 -10" />
            </g>

            {/* highlight swishes — light, like the drawing's marker strokes */}
            <g
              fill="none"
              stroke="#eba6ef"
              strokeWidth="10"
              strokeLinecap="round"
            >
              <path d="M150 196 q14 -14 30 -4" />
              <path d="M330 200 q14 -12 28 -2" />
              <path d="M240 208 q12 -12 26 -3" />
            </g>

            {/* outlined mini-puff hands (signature detail — middle puff raised, if you know you know) */}
            <g fill="#d883e6" stroke={OUTLINE} strokeWidth="10" strokeLinejoin="round" strokeLinecap="round">
              <path
                transform="translate(146 266) rotate(-6) scale(0.92)"
                d="M -34 0 a 14 14 0 0 1 24 -17 L -12 -23 a 12 12 0 0 1 24 0 L 12 -17 a 14 14 0 0 1 24 17 a 34 12 0 0 1 -68 0 z"
              />
              <path
                transform="translate(382 250) rotate(7)"
                d="M -34 0 a 14 14 0 0 1 24 -17 L -12 -23 a 12 12 0 0 1 24 0 L 12 -17 a 14 14 0 0 1 24 17 a 34 12 0 0 1 -68 0 z"
              />
            </g>
          </g>
        </svg>
      </div>
    );
  }
);

export default PuffCloudAvatar;
