"use client";

/**
 * IdleMotionGuard — puts the scene to sleep when nobody is using it.
 *
 * After IDLE_MINUTES without pointer/keyboard/touch input it sets
 * html.puff-idle, which pauses every ambient animation loop via the motion
 * governor in css/_theme.css. Any input removes the class again; paused
 * animations resume mid-keyframe, so waking is instant and seamless.
 *
 * Why this exists: the always-on loops keep Chromium compositing at 60fps
 * forever. On the Jetson appliance display that pinned the GPU at 99%
 * (~28W on VDD_GPU_SOC), throttled the SoC and slowed LLM inference —
 * anyone leaving the dashboard open reproduces it. A static frame costs
 * nothing.
 *
 * Voice counts as presence: this is a voice appliance, so a hands-free
 * conversation produces no input events at all. While Puff is speaking or
 * listening the timer is held, and a voice turn STARTING while idle wakes
 * the screen just like a key press. The SVG lipsync engine is JS-driven
 * and never paused regardless.
 *
 * Known gap, deliberately tolerated: the LLM "thinking"/streaming window
 * sets neither redux flag, so a single think longer than the full timeout
 * can let puff-idle appear mid-conversation. Every conditional live
 * indicator (thinking rings, streaming caret, mic blink — .puff-live in
 * css/_theme.css) is exempt from the idle pause, so nothing the user is
 * actually watching freezes, and the reply starting clears it anyway.
 *
 * QA / tuning: localStorage puffpai_idle_minutes overrides the default
 * (fractions fine, '0' disables idle pausing entirely; blank/garbage falls
 * back to the default). Read once on mount.
 */

import { useEffect, useRef } from "react";
import { useAppSelector } from "../_stores/hooks";

const IDLE_KEY = "puffpai_idle_minutes";
const DEFAULT_IDLE_MINUTES = 2;

/** Input events that count as "someone is here". Passive capture listeners —
 *  the handler is a timestamp write, so even pointermove spam is free. */
const ACTIVITY_EVENTS = [
  "pointerdown",
  "pointermove",
  "keydown",
  "wheel",
  "touchstart",
] as const;

export default function IdleMotionGuard() {
  const speaking = useAppSelector((s) => s.avatar.speaking);
  const listening = useAppSelector((s) => s.avatar.isListening);
  const voiceLive = speaking || listening;

  const voiceLiveRef = useRef(voiceLive);
  const activityRef = useRef<(() => void) | null>(null);

  // Any voice-state edge is presence: a turn starting while idle wakes the
  // screen; a turn ending restarts the countdown from now.
  useEffect(() => {
    voiceLiveRef.current = voiceLive;
    activityRef.current?.();
  }, [voiceLive]);

  useEffect(() => {
    let timeoutMs = DEFAULT_IDLE_MINUTES * 60_000;
    try {
      // Blank or garbage falls back to the default — only an explicit
      // number counts, and only an explicit <= 0 disables the feature.
      const raw = window.localStorage.getItem(IDLE_KEY);
      const v = raw === null || raw.trim() === "" ? NaN : Number(raw);
      if (Number.isFinite(v)) timeoutMs = v * 60_000;
    } catch {
      /* storage unavailable — keep the default */
    }
    if (timeoutMs <= 0) return; // '0' = idle pausing disabled

    const root = document.documentElement;
    let last = Date.now();
    let idle = false;
    let timer: number | null = null;

    const arm = (ms: number) => {
      if (timer !== null) window.clearTimeout(timer);
      timer = window.setTimeout(check, ms);
    };

    // One timer, re-armed only when it fires — activity() never touches it
    // while awake, so a mousemove storm costs nothing but a Date.now().
    const check = () => {
      timer = null;
      if (voiceLiveRef.current) {
        last = Date.now();
        arm(timeoutMs);
        return;
      }
      const remaining = last + timeoutMs - Date.now();
      if (remaining > 250) {
        arm(remaining);
        return;
      }
      idle = true;
      root.classList.add("puff-idle");
      // No re-arm: the next activity() restarts the clock.
    };

    const activity = () => {
      last = Date.now();
      if (idle) {
        idle = false;
        root.classList.remove("puff-idle");
        arm(timeoutMs);
      }
    };
    activityRef.current = activity;

    // Hidden tab/window: nothing is visible, sleep immediately. (Chromium
    // throttles hidden tabs anyway; this makes it explicit and instant.)
    const onVisibility = () => {
      if (document.visibilityState === "hidden") {
        if (timer !== null) {
          window.clearTimeout(timer);
          timer = null;
        }
        idle = true;
        root.classList.add("puff-idle");
      } else {
        activity();
      }
    };

    for (const ev of ACTIVITY_EVENTS)
      window.addEventListener(ev, activity, { capture: true, passive: true });
    document.addEventListener("visibilitychange", onVisibility);
    arm(timeoutMs);

    return () => {
      activityRef.current = null;
      for (const ev of ACTIVITY_EVENTS)
        window.removeEventListener(ev, activity, { capture: true });
      document.removeEventListener("visibilitychange", onVisibility);
      if (timer !== null) window.clearTimeout(timer);
      root.classList.remove("puff-idle");
    };
  }, []);

  return null;
}
