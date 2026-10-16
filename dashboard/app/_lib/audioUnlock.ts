"use client";

/**
 * Global Web Audio context manager.
 *
 * Browsers refuse to play audio until the user has interacted with the
 * page (autoplay policy). We create a singleton AudioContext and "unlock"
 * it on the first user gesture, then reuse it for everything that needs
 * to make sound (alarm timers, notification chimes, etc).
 */

let _ctx: AudioContext | null = null;
let _unlocked = false;

export function getAudioContext(): AudioContext | null {
  if (typeof window === "undefined") return null;
  if (_ctx) {
    if (_ctx.state === "suspended") {
      _ctx.resume().catch(() => {});
    }
    return _ctx;
  }
  const AudioCtx =
    (window as any).AudioContext || (window as any).webkitAudioContext;
  if (!AudioCtx) return null;
  try {
    _ctx = new AudioCtx();
    return _ctx;
  } catch {
    return null;
  }
}

export function unlockAudio() {
  const ctx = getAudioContext();
  if (!ctx) return;
  if (_unlocked && ctx.state !== "suspended") return;
  if (ctx.state === "suspended") {
    ctx.resume().catch(() => {});
  }
  try {
    const buffer = ctx.createBuffer(1, 1, 22050);
    const src = ctx.createBufferSource();
    src.buffer = buffer;
    src.connect(ctx.destination);
    src.start(0);
    _unlocked = true;
  } catch {
    // ignore
  }
}

/**
 * Install global one-shot listeners that unlock the audio context on
 * the first click/keypress/touch anywhere in the document.
 */
export function installAudioUnlockListeners() {
  if (typeof window === "undefined") return;
  if (_unlocked) return;

  const handler = () => {
    unlockAudio();
    window.removeEventListener("click", handler);
    window.removeEventListener("keydown", handler);
    window.removeEventListener("touchstart", handler);
  };
  window.addEventListener("click", handler, { once: false });
  window.addEventListener("keydown", handler, { once: false });
  window.addEventListener("touchstart", handler, { once: false });
}

/**
 * Play a short beep pattern. Call this once an alarm or timer fires.
 * The 'sound' arg picks one of: chime, alert, gentle, bell, none.
 */
export function playAlarmSound(sound: string = "chime") {
  if (typeof window === "undefined") return;
  if (sound === "none") return;
  const ctx = getAudioContext();
  if (!ctx) return;
  if (ctx.state === "suspended") {
    ctx.resume().catch(() => {});
  }
  try {
    const presets: Record<
      string,
      { freq: number; beeps: number; gap: number; type: OscillatorType }
    > = {
      chime:  { freq: 880,  beeps: 3, gap: 0.18, type: "sine" },
      alert:  { freq: 1200, beeps: 5, gap: 0.12, type: "square" },
      gentle: { freq: 660,  beeps: 2, gap: 0.25, type: "sine" },
      bell:   { freq: 1046, beeps: 4, gap: 0.20, type: "triangle" },
    };
    const preset = presets[sound] || presets.chime;
    // Repeat the pattern 3 times so it's hard to miss
    const repeats = 3;
    const patternDur = preset.beeps * preset.gap + 0.2;
    const start0 = ctx.currentTime;

    for (let r = 0; r < repeats; r++) {
      const repStart = start0 + r * (patternDur + 0.3);
      for (let i = 0; i < preset.beeps; i++) {
        const start = repStart + i * preset.gap;
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.type = preset.type;
        osc.frequency.value = preset.freq;
        gain.gain.setValueAtTime(0, start);
        gain.gain.linearRampToValueAtTime(0.35, start + 0.01);
        gain.gain.linearRampToValueAtTime(0, start + 0.12);
        osc.connect(gain);
        gain.connect(ctx.destination);
        osc.start(start);
        osc.stop(start + 0.13);
      }
    }
  } catch {
    // ignore
  }
}

export function showBrowserNotification(title: string, body: string = "") {
  if (typeof window === "undefined" || !("Notification" in window)) return;
  if (Notification.permission !== "granted") return;
  try {
    new Notification(title, {
      body: body || "PuffPAI",
      icon: "/favicon.ico",
      tag: "puffpai-alarm",
    });
  } catch {
    // ignore
  }
}

export function requestNotificationPermission() {
  if (typeof window === "undefined" || !("Notification" in window)) return;
  if (Notification.permission === "default") {
    Notification.requestPermission().catch(() => {});
  }
}
