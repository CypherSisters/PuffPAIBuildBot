"use client";

/**
 * Core React wrapper for the TalkingHead 3D avatar engine.
 * Renders a full-body 3D human avatar with lip-sync, moods, and idle animations.
 *
 * Uses dynamic import to avoid SSR issues with Three.js.
 *
 * Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
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

/** Methods exposed via ref to parent components */
export interface TalkingHeadHandle {
  speakText: (text: string) => Promise<void>;
  /** Speak server-TTS PCM audio WITH lipsync (mouth actually moves). */
  speakPcm: (pcmBase64: string, sampleRate: number, text: string) => Promise<void>;
  stopSpeaking: () => void;
  setMood: (mood: AvatarMood) => void;
  setView: (view: AvatarView) => void;
  isReady: () => boolean;
}

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

type TalkingHeadInstance = any; // eslint-disable-line @typescript-eslint/no-explicit-any

const TalkingHeadAvatar = forwardRef<TalkingHeadHandle, Props>(
  function TalkingHeadAvatar(
    {
      avatarUrl,
      mood = "neutral",
      view = "upper",
      mute = false,
      className = "",
      style,
      onLoad,
      onError,
      onSpeakStart,
      onSpeakEnd,
    },
    ref
  ) {
    const containerRef = useRef<HTMLDivElement>(null);
    const headRef = useRef<TalkingHeadInstance | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const initRef = useRef(false);

    // Expose methods via ref
    useImperativeHandle(
      ref,
      () => ({
        speakText: async (text: string) => {
          if (!headRef.current) return;
          onSpeakStart?.();
          try {
            headRef.current.speakText(text);
          } catch (e) {
            console.error("[Avatar] speakText error:", e);
          }
        },
        speakPcm: async (pcmBase64: string, sampleRate: number, text: string) => {
          // Piper returns pcm_s16le mono. Decode → AudioBuffer, estimate
          // per-word timings across the clip, and hand it to TalkingHead's
          // speakAudio so the "en" lipsync module drives the visemes.
          if (!headRef.current) throw new Error("avatar not ready");
          const head = headRef.current;
          const raw = atob(pcmBase64);
          const n = Math.floor(raw.length / 2);
          const AC: typeof AudioContext =
            window.AudioContext || (window as any).webkitAudioContext;
          const ctx: AudioContext =
            head.audioCtx && head.audioCtx.state !== "closed"
              ? head.audioCtx
              : new AC();
          const buf = ctx.createBuffer(1, n, sampleRate);
          const ch = buf.getChannelData(0);
          for (let i = 0; i < n; i++) {
            const lo = raw.charCodeAt(2 * i);
            const hi = raw.charCodeAt(2 * i + 1);
            let s = (hi << 8) | lo;
            if (s >= 0x8000) s -= 0x10000;
            ch[i] = s / 32768;
          }
          const durMs = (n / sampleRate) * 1000;
          const words = text.split(/\s+/).filter(Boolean);
          const totalChars = Math.max(1, words.reduce((a, w) => a + w.length + 1, 0));
          const wtimes: number[] = [];
          const wdurations: number[] = [];
          let t = 0;
          for (const w of words) {
            const d = ((w.length + 1) / totalChars) * durMs;
            wtimes.push(t);
            wdurations.push(d * 0.9);
            t += d;
          }
          onSpeakStart?.();
          await new Promise<void>((resolve) => {
            try {
              head.speakAudio(
                { audio: buf, words, wtimes, wdurations },
                { lipsyncLang: "en" },
                null
              );
              // TalkingHead has no completion callback for speakAudio —
              // resolve when the clip length elapses (+small buffer).
              setTimeout(resolve, durMs + 300);
            } catch (e) {
              console.error("[Avatar] speakAudio error:", e);
              resolve();
            }
          });
          onSpeakEnd?.();
        },
        stopSpeaking: () => {
          if (!headRef.current) return;
          headRef.current.stop();
          onSpeakEnd?.();
        },
        setMood: (m: AvatarMood) => {
          if (!headRef.current) return;
          headRef.current.setMood(m);
        },
        setView: (v: AvatarView) => {
          if (!headRef.current) return;
          headRef.current.setView(v);
        },
        isReady: () => !!headRef.current,
      }),
      [onSpeakStart, onSpeakEnd]
    );

    // Initialize TalkingHead engine
    const initAvatar = useCallback(async () => {
      if (!containerRef.current || initRef.current) return;
      initRef.current = true;
      setLoading(true);
      setError(null);

      try {
        // Dynamic import to avoid SSR — talkinghead uses Three.js which needs DOM.
        // The old build failure (talkinghead.mjs:36 "Can't resolve
        // ./playback-worklet.js") is fixed at the source by scripts/postinstall.js,
        // which rewrites the library's `new URL('./playback-worklet.js',
        // import.meta.url)` to load the worklet from the static public/ copy. No
        // turbopackIgnore hack needed — Turbopack bundles this normally now.
        const { TalkingHead } = await import("talkinghead");

        const head = new TalkingHead(containerRef.current, {
          ttsEndpoint: null, // We handle TTS externally (Phase 2)
          sttEndpoint: null, // We handle STT externally (Phase 2)
          lipsyncLang: "en",
          // Lipsync on: the "en" module is statically mapped by
          // the postinstall patch (see next.config comment). This is what
          // makes the mouth move.
          lipsyncModules: ["en"],
          cameraView: view,
          cameraRotateEnable: true,
          cameraPanEnable: false,
          cameraZoomEnable: true,
          lightAmbientColor: 0xffffff,
          lightAmbientIntensity: 2.5,
          lightDirectColor: 0xaabbcc,
          lightDirectIntensity: 25,
          lightDirectPhi: 0.9,
          lightDirectTheta: 3.5,
          avatarMood: mood,
          avatarMute: mute,
          avatarIdleEyeContact: 0.6,
          avatarIdleHeadMove: 0.6,
          avatarSpeakingEyeContact: 0.8,
          avatarSpeakingHeadMove: 0.6,
          modelFPS: 30,
          modelPixelRatio: Math.min(window.devicePixelRatio, 2),
        });

        // Load the avatar model
        await head.showAvatar(
          {
            url: avatarUrl,
            body: "F",
            avatarMood: mood,
          },
          () => {
            // Progress callback — required by the library, no UI hook for it yet.
          }
        );

        headRef.current = head;
        setLoading(false);
        onLoad?.();
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        console.error("[Avatar] Init failed:", msg);
        setError(msg);
        setLoading(false);
        onError?.(msg);
        initRef.current = false; // Allow retry
      }
    }, [avatarUrl, mood, view, mute, onLoad, onError]);

    // Mount / unmount
    useEffect(() => {
      initAvatar();

      return () => {
        // Cleanup Three.js resources
        if (headRef.current) {
          try {
            headRef.current.stop();
          } catch {
            // Ignore cleanup errors
          }
          headRef.current = null;
        }
        initRef.current = false;
      };
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    // Sync mood prop changes
    useEffect(() => {
      if (headRef.current && !loading) {
        headRef.current.setMood(mood);
      }
    }, [mood, loading]);

    return (
      <div
        className={`relative overflow-hidden ${className}`}
        style={{ minHeight: "200px", height: "500px", ...style }}
      >
        {/* Three.js render target — must have bounded dimensions */}
        <div
          ref={containerRef}
          className="absolute inset-0"
        />

        {/* Loading overlay */}
        {loading && (
          <div className="absolute inset-0 flex flex-col items-center justify-center rounded-2xl bg-gradient-to-b from-[#0096C7]/20 to-[#005F8A]/30 backdrop-blur-sm">
            <div className="h-16 w-16 animate-spin rounded-full border-4 border-[#00B4D8] border-t-transparent" />
            <p className="mt-4 text-sm font-medium text-(--text)">
              Loading avatar...
            </p>
          </div>
        )}

        {/* Error overlay */}
        {error && !loading && (
          <div className="absolute inset-0 flex flex-col items-center justify-center rounded-2xl p-4 backdrop-blur-sm bg-(--puff-red)/20">
            <p className="text-center text-sm text-(--puff-red)">
              Avatar failed to load
            </p>
            <p className="mt-1 text-center text-xs text-(--puff-red)/70">
              {error}
            </p>
            <button
              onClick={() => {
                initRef.current = false;
                initAvatar();
              }}
              className="mt-3 rounded-full px-4 py-1.5 text-xs font-medium transition hover:bg-(--puff-red)/15 bg-(--puff-red)/30 text-(--puff-red)"
            >
              Retry
            </button>
          </div>
        )}
      </div>
    );
  }
);

export default TalkingHeadAvatar;
