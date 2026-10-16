/**
 * Hook that maps PersonalityTraits → visual effects in the R3F scene.
 *
 * Effects:
 * 1. Ambient light color blend based on dominant traits
 * 2. Breathing animation speed/amplitude tuning
 * 3. Head/Neck micro-pose offsets for personality expression
 *
 * Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
 */

import { useMemo } from "react";
import * as THREE from "three";
import type { PersonalityTraits } from "../../_interfaces/avatar";

const DEG = Math.PI / 180;

/* ── Trait → color mapping ─────────────────────────────────── */

interface TraitColor {
  key: keyof PersonalityTraits;
  color: THREE.Color;
  threshold: number; // Only contribute if trait is above this
}

const TRAIT_COLORS: TraitColor[] = [
  { key: "warmth", color: new THREE.Color("#ffe0b2"), threshold: 55 },
  { key: "energy", color: new THREE.Color("#00e5ff"), threshold: 55 },
  { key: "humor", color: new THREE.Color("#fff176"), threshold: 55 },
  { key: "confidence", color: new THREE.Color("#42a5f5"), threshold: 55 },
  { key: "creativity", color: new THREE.Color("#ff8a65"), threshold: 55 },
  { key: "empathy", color: new THREE.Color("#81c784"), threshold: 55 },
];

const NEUTRAL_COLOR = new THREE.Color("#e0f7fa"); // PuffPAI cyan base

/* ── Exported hook result ──────────────────────────────────── */

export interface PersonalityEffects {
  /** Blended ambient light color based on personality */
  ambientColor: string;
  /** Emissive intensity for personality glow (0–0.1) */
  emissiveIntensity: number;
  /** Emissive color hex */
  emissiveColor: string;
  /** Breathing speed multiplier (default 1.0) */
  breathSpeed: number;
  /** Breathing amplitude multiplier (default 1.0) */
  breathAmplitude: number;
  /** Head bone Euler offset (radians) for personality expression */
  headOffset: THREE.Euler;
  /** Neck bone Euler offset (radians) */
  neckOffset: THREE.Euler;
}

/* ── Hook ──────────────────────────────────────────────────── */

export function usePersonalityEffects(
  personality: PersonalityTraits
): PersonalityEffects {
  return useMemo(() => {
    // Blend ambient color from contributing traits
    const blended = NEUTRAL_COLOR.clone();
    let totalWeight = 1.0; // start with neutral weight

    for (const tc of TRAIT_COLORS) {
      const val = personality[tc.key];
      if (val > tc.threshold) {
        const weight = (val - tc.threshold) / 100; // 0–0.45 range
        blended.lerp(tc.color, weight * 0.4);
        totalWeight += weight;
      }
    }

    // Emissive glow — subtle, based on how "extreme" the personality is
    const deviation =
      Object.values(personality).reduce(
        (sum, v) => sum + Math.abs(v - 50),
        0
      ) / 8; // average deviation from center
    const emissiveIntensity = Math.min(0.08, deviation / 500);

    // Find dominant trait for emissive color
    let maxTrait: keyof PersonalityTraits = "warmth";
    let maxVal = 0;
    for (const [key, val] of Object.entries(personality) as [keyof PersonalityTraits, number][]) {
      if (val > maxVal) {
        maxVal = val;
        maxTrait = key;
      }
    }
    const dominantColor = TRAIT_COLORS.find((tc) => tc.key === maxTrait);
    const emissiveColor = dominantColor
      ? `#${dominantColor.color.getHexString()}`
      : "#ffffff";

    // Breathing dynamics based on energy trait
    const energy = personality.energy / 100;
    const breathSpeed = 0.6 + energy * 0.6; // 0.6–1.2
    const breathAmplitude = 0.8 + energy * 0.5; // 0.8–1.3

    // Head/neck micro-offsets based on personality
    const confidence = personality.confidence / 100;
    const warmth = personality.warmth / 100;
    const curiosity = personality.curiosity / 100;

    // Confidence → chin up; Warmth → slight tilt; Curiosity → slight forward lean
    const headX =
      -(confidence - 0.5) * 4 * DEG + // chin up when confident
      (curiosity - 0.5) * 3 * DEG; // lean forward when curious
    const headZ = (warmth - 0.5) * 4 * DEG; // tilt when warm

    const neckY = (warmth - 0.5) * 2 * DEG;
    const neckX = (curiosity - 0.5) * 2 * DEG;

    return {
      ambientColor: `#${blended.getHexString()}`,
      emissiveIntensity,
      emissiveColor,
      breathSpeed,
      breathAmplitude,
      headOffset: new THREE.Euler(headX, 0, headZ),
      neckOffset: new THREE.Euler(neckX, neckY, 0),
    };
  }, [personality]);
}
