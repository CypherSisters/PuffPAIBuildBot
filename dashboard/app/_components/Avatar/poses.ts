/**
 * Pose definitions for the PuffPAI Avaturn 3D avatar.
 *
 * Each pose is a named collection of bone rotation offsets (Euler angles in
 * radians) that are composed on top of the skeleton's rest-pose quaternions.
 * This keeps the T-pose as the canonical base and makes poses additive.
 *
 * The Avaturn model uses Mixamo-compatible bone naming (52 joints).
 * Key bones: LeftArm, RightArm, LeftForeArm, RightForeArm, LeftHand,
 * RightHand, LeftShoulder, RightShoulder, Spine, Neck, Head, etc.
 *
 * Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
 */

import * as THREE from "three";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

/** A single bone rotation offset applied on top of the rest pose. */
export interface BoneRotation {
  /** Mixamo bone name (e.g. "LeftArm", "RightForeArm"). */
  bone: string;
  /** Euler rotation offset (radians) composed onto the rest quaternion. */
  rotation: THREE.Euler;
}

/** A named pose is an array of bone rotation offsets. */
export interface Pose {
  name: string;
  bones: BoneRotation[];
}

/* ------------------------------------------------------------------ */
/*  Helper — degrees to radians                                        */
/* ------------------------------------------------------------------ */

const DEG = Math.PI / 180;

/* ------------------------------------------------------------------ */
/*  Poses                                                              */
/* ------------------------------------------------------------------ */

/**
 * Natural idle pose: arms relaxed at sides, slight elbow bend,
 * hands angled so they don't clip into the thighs.
 *
 * NOTE: The Avaturn model's rest pose already has arms partially down
 * (not a strict T-pose). Large Z-rotation offsets cause "broken arm"
 * artifacts. These values are tuned for a standard Avaturn/Ready Player Me skeleton.
 */
export const IDLE_POSE: Pose = {
  name: "idle",
  bones: [
    // Spine — very slight natural forward lean
    { bone: "Spine1", rotation: new THREE.Euler(2 * DEG, 0, 0) },

    // Upper arms — gentle downward nudge (model is already mostly down)
    { bone: "LeftArm", rotation: new THREE.Euler(5 * DEG, 0, 10 * DEG) },
    { bone: "RightArm", rotation: new THREE.Euler(5 * DEG, 0, -10 * DEG) },

    // Forearms — subtle natural elbow bend
    { bone: "LeftForeArm", rotation: new THREE.Euler(0, -8 * DEG, -5 * DEG) },
    { bone: "RightForeArm", rotation: new THREE.Euler(0, 8 * DEG, 5 * DEG) },

    // Hands — relaxed wrist angle
    { bone: "LeftHand", rotation: new THREE.Euler(3 * DEG, 0, -3 * DEG) },
    { bone: "RightHand", rotation: new THREE.Euler(3 * DEG, 0, 3 * DEG) },
  ],
};

/**
 * Thinking pose: one hand near chin, other arm relaxed.
 * (Future — ready to wire up when gesture system is built.)
 */
export const THINKING_POSE: Pose = {
  name: "thinking",
  bones: [
    // Left arm stays relaxed (same as idle)
    { bone: "LeftArm", rotation: new THREE.Euler(5 * DEG, 0, 10 * DEG) },
    { bone: "LeftForeArm", rotation: new THREE.Euler(0, -8 * DEG, -5 * DEG) },
    { bone: "LeftHand", rotation: new THREE.Euler(3 * DEG, 0, -3 * DEG) },

    // Right arm raised, hand near chin
    { bone: "RightArm", rotation: new THREE.Euler(-25 * DEG, 15 * DEG, -20 * DEG) },
    { bone: "RightForeArm", rotation: new THREE.Euler(0, 30 * DEG, 40 * DEG) },
    { bone: "RightHand", rotation: new THREE.Euler(10 * DEG, 0, 5 * DEG) },
  ],
};

/**
 * Wave gesture: right arm up and out, forearm raised.
 * (Future — for greeting animations.)
 */
export const WAVE_POSE: Pose = {
  name: "wave",
  bones: [
    // Left arm relaxed (same as idle)
    { bone: "LeftArm", rotation: new THREE.Euler(5 * DEG, 0, 10 * DEG) },
    { bone: "LeftForeArm", rotation: new THREE.Euler(0, -8 * DEG, -5 * DEG) },
    { bone: "LeftHand", rotation: new THREE.Euler(3 * DEG, 0, -3 * DEG) },

    // Right arm raised outward and up for a wave
    { bone: "RightArm", rotation: new THREE.Euler(-15 * DEG, 0, -30 * DEG) },
    { bone: "RightForeArm", rotation: new THREE.Euler(0, -20 * DEG, 35 * DEG) },
    { bone: "RightHand", rotation: new THREE.Euler(0, 0, 10 * DEG) },
  ],
};

/**
 * Speaking pose: slight head nod and tilt — gives a conversational feel.
 * Arms stay in idle position.
 */
export const SPEAKING_POSE: Pose = {
  name: "speaking",
  bones: [
    ...IDLE_POSE.bones,
    { bone: "Head", rotation: new THREE.Euler(2 * DEG, 0, -2 * DEG) },
    { bone: "Neck", rotation: new THREE.Euler(1 * DEG, 0, 0) },
  ],
};

/**
 * Listening pose: attentive slight head tilt.
 */
export const LISTENING_POSE: Pose = {
  name: "listening",
  bones: [
    ...IDLE_POSE.bones,
    { bone: "Head", rotation: new THREE.Euler(-1 * DEG, 0, 3 * DEG) },
    { bone: "Neck", rotation: new THREE.Euler(0, 2 * DEG, 0) },
  ],
};
