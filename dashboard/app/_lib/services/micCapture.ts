/**
 * Offline microphone capture for PuffPAI voice input.
 *
 * Captures speaker audio from the default mic, downsamples to 16 kHz mono
 * PCM16, and returns it base64-encoded — exactly what the backend Vosk STT
 * (/voice/transcribe) expects. This is the CORE offline voice path; browser
 * SpeechRecognition (which needs the cloud) is only a fallback.
 *
 * Auto-stops after `silenceMs` of quiet once speech has started, or at
 * `maxMs`. Call controller.stop() to end an utterance manually.
 *
 * Copyright (c) 2026 Cypher Sisters. Apache-2.0 License.
 */

export interface MicCaptureResult {
  pcmBase64: string;
  sampleRate: number;
  durationMs: number;
  /** True if we ever detected speech-level audio */
  heardSpeech: boolean;
}

export interface MicCaptureController {
  /** Finish the utterance now and resolve `done` with the audio so far. */
  stop: () => void;
  /** Abort: releases the mic, `done` resolves null. */
  cancel: () => void;
  /** Resolves when capture ends (stop, silence, max length, or cancel). */
  done: Promise<MicCaptureResult | null>;
}

const TARGET_RATE = 16000;

export function isMicCaptureSupported(): boolean {
  return (
    typeof window !== "undefined" &&
    !!navigator.mediaDevices?.getUserMedia &&
    !!(window.AudioContext || (window as any).webkitAudioContext)
  );
}

export async function startMicCapture(opts?: {
  maxMs?: number;
  silenceMs?: number;
  /** RMS threshold that counts as speech (default 0.012) */
  speechThreshold?: number;
  /** Live level meter callback (0..1-ish RMS) */
  onLevel?: (rms: number) => void;
}): Promise<MicCaptureController> {
  const maxMs = opts?.maxMs ?? 20000;
  const silenceMs = opts?.silenceMs ?? 1400;
  const threshold = opts?.speechThreshold ?? 0.012;

  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
  });

  const AC: typeof AudioContext =
    window.AudioContext || (window as any).webkitAudioContext;
  const ctx = new AC();
  const source = ctx.createMediaStreamSource(stream);
  // ScriptProcessor is deprecated but universally supported and fine for
  // single-utterance capture; swap for an AudioWorklet when we do always-on.
  const proc = ctx.createScriptProcessor(4096, 1, 1);

  const chunks: Float32Array[] = [];
  let heardSpeech = false;
  let lastLoudAt = performance.now();
  const startedAt = performance.now();
  let finished = false;

  let resolveDone!: (r: MicCaptureResult | null) => void;
  const done = new Promise<MicCaptureResult | null>((res) => (resolveDone = res));

  const cleanup = () => {
    try { proc.disconnect(); } catch {}
    try { source.disconnect(); } catch {}
    try { stream.getTracks().forEach((t) => t.stop()); } catch {}
    try { void ctx.close(); } catch {}
  };

  const finish = (aborted: boolean) => {
    if (finished) return;
    finished = true;
    cleanup();
    if (aborted) {
      resolveDone(null);
      return;
    }
    const inputRate = ctx.sampleRate;
    const total = chunks.reduce((a, c) => a + c.length, 0);
    const all = new Float32Array(total);
    let off = 0;
    for (const c of chunks) {
      all.set(c, off);
      off += c.length;
    }
    // Downsample to 16 kHz mono via averaging (simple anti-alias)
    const ratio = inputRate / TARGET_RATE;
    const outLen = Math.floor(all.length / ratio);
    const pcm = new Int16Array(outLen);
    for (let i = 0; i < outLen; i++) {
      const start = Math.floor(i * ratio);
      const end = Math.min(all.length, Math.floor((i + 1) * ratio));
      let sum = 0;
      for (let j = start; j < end; j++) sum += all[j];
      const sample = end > start ? sum / (end - start) : 0;
      const s = Math.max(-1, Math.min(1, sample));
      pcm[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }
    // Base64-encode in chunks (avoids call-stack limits on long clips)
    const bytes = new Uint8Array(pcm.buffer);
    let bin = "";
    const STEP = 0x8000;
    for (let i = 0; i < bytes.length; i += STEP) {
      bin += String.fromCharCode.apply(
        null,
        bytes.subarray(i, i + STEP) as unknown as number[]
      );
    }
    resolveDone({
      pcmBase64: btoa(bin),
      sampleRate: TARGET_RATE,
      durationMs: performance.now() - startedAt,
      heardSpeech,
    });
  };

  proc.onaudioprocess = (e) => {
    if (finished) return;
    const data = e.inputBuffer.getChannelData(0);
    chunks.push(new Float32Array(data));
    let sum = 0;
    for (let i = 0; i < data.length; i++) sum += data[i] * data[i];
    const rms = Math.sqrt(sum / data.length);
    opts?.onLevel?.(rms);
    const now = performance.now();
    if (rms >= threshold) {
      heardSpeech = true;
      lastLoudAt = now;
    }
    if (now - startedAt >= maxMs) {
      finish(false);
    } else if (heardSpeech && now - lastLoudAt >= silenceMs) {
      finish(false); // trailing silence — utterance over
    }
  };

  source.connect(proc);
  // ScriptProcessor must be connected to keep firing; route through a muted
  // gain so the mic is never echoed to the speakers.
  const mute = ctx.createGain();
  mute.gain.value = 0;
  proc.connect(mute);
  mute.connect(ctx.destination);

  return {
    stop: () => finish(false),
    cancel: () => finish(true),
    done,
  };
}
