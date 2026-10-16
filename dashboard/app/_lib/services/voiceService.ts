/**
 * Voice service for PuffPAI dashboard.
 *
 * Provides TTS (text-to-speech) and STT (speech-to-text) with
 * automatic fallback from backend engines to browser Web Speech APIs.
 *
 * Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
 */

import { apiGet, apiPost } from "../api";

/* ── Types ──────────────────────────────────────────────────── */

export interface VoiceCapabilities {
  tts_available: boolean;
  stt_available: boolean;
  tts_engine: string;
  stt_engine: string;
  tts_sample_rate: number | null;
  stt_sample_rate: number | null;
  fallback: string;
}

export interface SynthesizeResult {
  success: boolean;
  audio_base64?: string;
  sample_rate?: number;
  format?: string;
  fallback?: string;
  message?: string;
}

export interface TranscribeResult {
  success: boolean;
  text?: string;
  fallback?: string;
  message?: string;
}

/* ── Capabilities probe ─────────────────────────────────────── */

let _cachedCapabilities: VoiceCapabilities | null = null;

export async function getVoiceCapabilities(): Promise<VoiceCapabilities> {
  if (_cachedCapabilities) return _cachedCapabilities;
  try {
    const caps = await apiGet<VoiceCapabilities>("/voice/capabilities");
    _cachedCapabilities = caps;
    return caps;
  } catch {
    // Backend unreachable — assume browser-only fallback
    return {
      tts_available: false,
      stt_available: false,
      tts_engine: "none",
      stt_engine: "none",
      tts_sample_rate: null,
      stt_sample_rate: null,
      fallback: "browser",
    };
  }
}

/* ── Backend TTS ────────────────────────────────────────────── */

export async function synthesize(text: string): Promise<SynthesizeResult> {
  try {
    return await apiPost<SynthesizeResult>("/voice/synthesize", { text });
  } catch {
    return { success: false, fallback: "browser", message: "Backend unreachable" };
  }
}

/* ── Backend STT ────────────────────────────────────────────── */

export async function transcribe(audioBase64: string): Promise<TranscribeResult> {
  try {
    return await apiPost<TranscribeResult>("/voice/transcribe", {
      audio_base64: audioBase64,
    });
  } catch {
    return { success: false, fallback: "browser", message: "Backend unreachable" };
  }
}

/* ── Browser TTS Fallback ───────────────────────────────────── */

/**
 * Speak text using the browser's built-in SpeechSynthesis API.
 * Returns a promise that resolves when speech ends.
 */
export function browserSpeak(
  text: string,
  onEnd?: () => void
): SpeechSynthesisUtterance | null {
  if (typeof window === "undefined" || !window.speechSynthesis) return null;

  // Cancel any in-progress speech
  window.speechSynthesis.cancel();

  const utterance = new SpeechSynthesisUtterance(text);
  utterance.rate = 1.0;
  utterance.pitch = 1.0;
  utterance.volume = 1.0;

  // Try to pick a good English voice
  const voices = window.speechSynthesis.getVoices();
  const preferred = voices.find(
    (v) =>
      v.lang.startsWith("en") &&
      (v.name.includes("Google") || v.name.includes("Samantha") || v.name.includes("Natural"))
  );
  if (preferred) utterance.voice = preferred;

  utterance.onend = () => onEnd?.();
  utterance.onerror = () => onEnd?.();

  window.speechSynthesis.speak(utterance);
  return utterance;
}

/** Stop any active browser speech. */
export function browserSpeakStop() {
  if (typeof window !== "undefined" && window.speechSynthesis) {
    window.speechSynthesis.cancel();
  }
}

/* ── Browser STT Fallback ───────────────────────────────────── */

// SpeechRecognition type for TypeScript
type SpeechRecognitionType = typeof window extends { SpeechRecognition: infer T }
  ? T
  : any;

/**
 * Start browser-based speech recognition.
 * Returns a controller object to stop recognition.
 */
export function browserListen(
  onResult: (text: string) => void,
  onError?: (error: string) => void,
  onEnd?: () => void
): { stop: () => void } | null {
  if (typeof window === "undefined") return null;

  const SpeechRecognition =
    (window as any).SpeechRecognition ||
    (window as any).webkitSpeechRecognition;

  if (!SpeechRecognition) {
    onError?.("SpeechRecognition not supported in this browser");
    return null;
  }

  const recognition = new SpeechRecognition();
  recognition.continuous = false;
  recognition.interimResults = false;
  recognition.lang = "en-US";
  recognition.maxAlternatives = 1;

  recognition.onresult = (event: any) => {
    const transcript = event.results[0]?.[0]?.transcript || "";
    onResult(transcript);
  };

  recognition.onerror = (event: any) => {
    if (event.error !== "aborted") {
      onError?.(event.error || "Recognition error");
    }
  };

  recognition.onend = () => {
    onEnd?.();
  };

  recognition.start();

  return {
    stop: () => {
      try {
        recognition.stop();
      } catch {
        // Already stopped
      }
    },
  };
}

/* ── Audio Playback Helpers ─────────────────────────────────── */

/**
 * Play raw PCM 16-bit audio from a base64 string via Web Audio API.
 * Returns a promise that resolves when playback ends.
 */
export async function playPcmAudio(
  base64Audio: string,
  sampleRate = 22050
): Promise<void> {
  const raw = atob(base64Audio);
  const bytes = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) {
    bytes[i] = raw.charCodeAt(i);
  }

  // Convert 16-bit PCM to Float32 for Web Audio
  const int16 = new Int16Array(bytes.buffer);
  const float32 = new Float32Array(int16.length);
  for (let i = 0; i < int16.length; i++) {
    float32[i] = int16[i] / 32768;
  }

  const ctx = new AudioContext();
  const buffer = ctx.createBuffer(1, float32.length, sampleRate);
  buffer.copyToChannel(float32, 0);

  const source = ctx.createBufferSource();
  source.buffer = buffer;
  source.connect(ctx.destination);

  return new Promise<void>((resolve) => {
    source.onended = () => {
      ctx.close();
      resolve();
    };
    source.start();
  });
}

/* ── High-Level Speak Function ──────────────────────────────── */

/* ── Avatar lipsync sink ────────────────────────────────────── */

/**
 * When the avatar overlay is on screen it registers a sink here; server-TTS
 * audio then plays through the avatar (with mouth movement) instead of a
 * bare AudioContext. Unregister with null.
 */
type AvatarSink = (
  pcmBase64: string,
  sampleRate: number,
  text: string
) => Promise<void>;

let _avatarSink: AvatarSink | null = null;

export function registerAvatarSink(sink: AvatarSink | null) {
  _avatarSink = sink;
}

/**
 * Smart speak: backend PiperTTS through the avatar's mouth when she's
 * visible, plain audio otherwise, browser TTS as the last resort.
 */
export async function speak(
  text: string,
  onStart?: () => void,
  onEnd?: () => void
): Promise<void> {
  onStart?.();

  // Try backend TTS first
  const result = await synthesize(text);

  if (result.success && result.audio_base64) {
    const rate = result.sample_rate || 22050;
    // Preferred: route through the avatar for lipsync
    if (_avatarSink) {
      try {
        await _avatarSink(result.audio_base64, rate, text);
        onEnd?.();
        return;
      } catch {
        // Avatar not ready — fall through to plain playback
      }
    }
    try {
      await playPcmAudio(result.audio_base64, rate);
      onEnd?.();
      return;
    } catch {
      // Fall through to browser TTS
    }
  }

  // Browser fallback
  return new Promise<void>((resolve) => {
    browserSpeak(text, () => {
      onEnd?.();
      resolve();
    });
    // Safety timeout — if browser TTS fails silently
    setTimeout(() => {
      onEnd?.();
      resolve();
    }, 30000);
  });
}

/* ── High-Level Listen Function ─────────────────────────────── */

/**
 * Smart listen: uses browser SpeechRecognition (most reliable for real-time).
 * Backend Vosk is better for batch audio processing.
 */
export function listen(
  onResult: (text: string) => void,
  onError?: (error: string) => void,
  onEnd?: () => void
): { stop: () => void } | null {
  return browserListen(onResult, onError, onEnd);
}

/* ── Browser Support Detection ──────────────────────────────── */

export function isBrowserTtsSupported(): boolean {
  return typeof window !== "undefined" && "speechSynthesis" in window;
}

export function isBrowserSttSupported(): boolean {
  return (
    typeof window !== "undefined" &&
    ("SpeechRecognition" in window || "webkitSpeechRecognition" in window)
  );
}
