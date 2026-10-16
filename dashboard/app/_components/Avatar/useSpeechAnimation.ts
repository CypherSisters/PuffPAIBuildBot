/**
 * Hook that drives subtle head bone animation during speech.
 *
 * Since the Avaturn model lacks jaw bones and morph targets (blend shapes),
 * we simulate talking by:
 * 1. Micro-rotating the Head bone (nod) at a cadence that mimics mouth movement
 * 2. Adding slight Neck sway for natural "conversational body language"
 * 3. Optionally analyzing real audio amplitude to drive the motion intensity
 *
 * When the model is re-exported with ARKit blend shapes, this can be replaced
 * with proper viseme-based lip sync.
 *
 * Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
 */

import { useRef, useEffect, useCallback } from "react";
import { useFrame } from "@react-three/fiber";
import * as THREE from "three";
import type { GLTF } from "three-stdlib";

/* ------------------------------------------------------------------ */
/*  Public interface                                                    */
/* ------------------------------------------------------------------ */

interface SpeechAnimationOptions {
  /** The loaded GLTF scene */
  gltf: GLTF;
  /** Whether the avatar is currently speaking */
  speaking: boolean;
  /** Whether the avatar is listening (mic active) */
  isListening: boolean;
  /** Overall intensity multiplier (0–1). Default 1. */
  intensity?: number;
}

/* ------------------------------------------------------------------ */
/*  Internal state                                                     */
/* ------------------------------------------------------------------ */

interface BoneRefs {
  head: THREE.Bone | null;
  neck: THREE.Bone | null;
  spine1: THREE.Bone | null;
  headRestQuat: THREE.Quaternion;
  neckRestQuat: THREE.Quaternion;
}

/* ------------------------------------------------------------------ */
/*  Hook                                                               */
/* ------------------------------------------------------------------ */

export function useSpeechAnimation({
  gltf,
  speaking,
  isListening,
  intensity = 1,
}: SpeechAnimationOptions): void {
  const bonesRef = useRef<BoneRefs | null>(null);
  const smoothAmplitude = useRef(0);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const dataArrayRef = useRef<Uint8Array | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const sourceRef = useRef<MediaElementAudioSourceNode | null>(null);

  // Capture bone references once
  useEffect(() => {
    if (!gltf?.scene || bonesRef.current) return;

    const head = gltf.scene.getObjectByName("Head") as THREE.Bone | null;
    const neck = gltf.scene.getObjectByName("Neck") as THREE.Bone | null;
    const spine1 = gltf.scene.getObjectByName("Spine1") as THREE.Bone | null;

    if (head) {
      bonesRef.current = {
        head,
        neck,
        spine1,
        headRestQuat: head.quaternion.clone(),
        neckRestQuat: neck ? neck.quaternion.clone() : new THREE.Quaternion(),
      };
    }
  }, [gltf]);

  // Try to hook into browser SpeechSynthesis audio for amplitude analysis
  const connectAudioAnalyser = useCallback(() => {
    // Browser SpeechSynthesis doesn't expose an AudioNode, so we can't
    // directly analyze it. Instead we'll use a procedural cadence that
    // looks natural. If we later switch to Web Audio TTS, we can pipe
    // the audio through an AnalyserNode here.
  }, []);

  useEffect(() => {
    if (speaking) {
      connectAudioAnalyser();
    }
    return () => {
      // Cleanup analyser if we had one
      if (audioCtxRef.current) {
        try { audioCtxRef.current.close(); } catch { /* ignore */ }
        audioCtxRef.current = null;
        analyserRef.current = null;
        sourceRef.current = null;
      }
    };
  }, [speaking, connectAudioAnalyser]);

  // Per-frame animation
  useFrame(({ clock }) => {
    const bones = bonesRef.current;
    if (!bones?.head) return;

    const t = clock.getElapsedTime();
    const scale = intensity;

    if (speaking) {
      // Procedural speech cadence — multiple overlapping sine waves
      // to approximate the rhythm of natural speech
      const jawOpen =
        (Math.sin(t * 8.5) * 0.35 +     // primary syllable rhythm (~4 Hz)
         Math.sin(t * 13.2) * 0.25 +     // secondary faster variation
         Math.sin(t * 5.1) * 0.2 +       // slower phrase-level modulation
         Math.sin(t * 21.0) * 0.1) *     // high-freq micro-movement
        0.5 + 0.5; // normalize to 0–1

      // Smooth the amplitude to avoid jittering
      smoothAmplitude.current += (jawOpen - smoothAmplitude.current) * 0.3;
      const amp = smoothAmplitude.current * scale;

      // Head: nod down slightly when "mouth opens" (simulates jaw)
      const headNod = amp * 0.025; // ~1.4 degrees max
      const headTilt = Math.sin(t * 2.3) * 0.008 * scale; // gentle side-tilt

      // Apply as additive rotation on top of current pose
      const headOffset = new THREE.Quaternion().setFromEuler(
        new THREE.Euler(headNod, 0, headTilt)
      );
      bones.head.quaternion.multiply(headOffset);

      // Neck: subtle sway during speech
      if (bones.neck) {
        const neckSway = Math.sin(t * 1.7) * 0.006 * scale;
        const neckNod = Math.sin(t * 3.1) * 0.004 * scale;
        const neckOffset = new THREE.Quaternion().setFromEuler(
          new THREE.Euler(neckNod, neckSway, 0)
        );
        bones.neck.quaternion.multiply(neckOffset);
      }
    } else if (isListening) {
      // Subtle attentive head movement while listening
      const attentiveTilt = Math.sin(t * 1.2) * 0.005 * scale;
      const attentiveNod = Math.sin(t * 0.8) * 0.003 * scale;
      const headOffset = new THREE.Quaternion().setFromEuler(
        new THREE.Euler(attentiveNod, 0, attentiveTilt)
      );
      bones.head.quaternion.multiply(headOffset);
    } else {
      // Decay smoothly back to rest when not speaking
      smoothAmplitude.current *= 0.9;
    }
  });
}
