/**
 * React Three Fiber hook that applies a Pose to a GLTF skeleton with
 * smooth quaternion interpolation (slerp).
 *
 * Rest-pose quaternions are captured once on first load so that every
 * pose is applied as an additive offset — the T-pose is never lost.
 *
 * Usage:
 *   const gltf = useGLTF(url);
 *   usePose(gltf, IDLE_POSE);
 *
 * Switching the `pose` argument triggers a smooth animated transition.
 *
 * Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
 */

import { useEffect, useRef } from "react";
import { useFrame } from "@react-three/fiber";
import * as THREE from "three";
import type { GLTF } from "three-stdlib";
import type { Pose } from "./poses";

/* ------------------------------------------------------------------ */
/*  Internal types                                                     */
/* ------------------------------------------------------------------ */

interface BoneCache {
  bone: THREE.Bone;
  /** Original rest-pose quaternion (captured once, never mutated). */
  restQuaternion: THREE.Quaternion;
}

/* ------------------------------------------------------------------ */
/*  Hook                                                               */
/* ------------------------------------------------------------------ */

/**
 * Smoothly drives a GLTF skeleton toward the given {@link Pose}.
 *
 * @param gltf      Loaded GLTF object (from `useGLTF`).
 * @param pose      Target pose — bone offsets applied on top of the rest pose.
 * @param lerpSpeed Slerp factor per frame (0–1). Lower = smoother. Default 0.06.
 */
export function usePose(gltf: GLTF, pose: Pose, lerpSpeed = 0.06): void {
  const cacheRef = useRef<Map<string, BoneCache>>(new Map());
  const targetQuats = useRef<Map<string, THREE.Quaternion>>(new Map());
  const cacheBuilt = useRef(false);

  /* ---- Build bone cache + compute initial targets together ---- */
  useEffect(() => {
    if (!gltf?.scene) return;

    // Only capture rest quaternions once (the first time the scene is available)
    if (!cacheBuilt.current) {
      const cache = new Map<string, BoneCache>();
      gltf.scene.traverse((child) => {
        if ((child as THREE.Bone).isBone) {
          const bone = child as THREE.Bone;
          cache.set(bone.name, {
            bone,
            restQuaternion: bone.quaternion.clone(),
          });
        }
      });
      cacheRef.current = cache;
      cacheBuilt.current = true;
    }

    // Compute target quaternions from pose + cached rest quaternions
    const targets = new Map<string, THREE.Quaternion>();
    for (const entry of pose.bones) {
      const cached = cacheRef.current.get(entry.bone);
      if (!cached) continue;

      // Convert the Euler offset to a quaternion
      const offsetQuat = new THREE.Quaternion().setFromEuler(entry.rotation);

      // Compose: rest * offset  (offset is applied in bone-local space)
      const targetQuat = cached.restQuaternion.clone().multiply(offsetQuat);
      targets.set(entry.bone, targetQuat);
    }
    targetQuats.current = targets;
  }, [gltf, pose]);

  /* ---- Smoothly interpolate toward target each frame ---- */
  useFrame(() => {
    targetQuats.current.forEach((targetQuat, boneName) => {
      const cached = cacheRef.current.get(boneName);
      if (!cached) return;
      cached.bone.quaternion.slerp(targetQuat, lerpSpeed);
    });
  });
}
