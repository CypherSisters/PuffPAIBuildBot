"use client";

/**
 * GLB avatar renderer using React Three Fiber — the "Custom 3D Model" engine.
 * Chest-up portrait framing with personality-driven visual effects. Loads any
 * user-supplied GLB (Ready Player Me, Avaturn, Blender export).
 *
 * Implements the shared AvatarEngineHandle: it has no viseme lip-sync model, so
 * speech is played as audio (server TTS via voiceService) while an internal
 * "speaking" flag drives head motion. This keeps the engine swappable — the
 * tile/overlay/panel drive it through the same ref as Puff and TalkingHead.
 *
 * Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
 */

import {
  forwardRef,
  useImperativeHandle,
  useState,
  useRef,
  useCallback,
  Suspense,
  useEffect,
} from "react";
import type {
  AvatarMood,
  AvatarView,
  PersonalityTraits,
} from "../../_interfaces/avatar";
import { DEFAULT_PERSONALITY } from "../../_interfaces/avatar";
import {
  synthesize,
  playPcmAudio,
  browserSpeak,
  browserSpeakStop,
} from "@/app/_lib/services/voiceService";
import type { AvatarEngineHandle } from "./engines/types";
import { Canvas, useFrame } from "@react-three/fiber";
import { useGLTF, OrbitControls } from "@react-three/drei";
import * as THREE from "three";
import { usePose } from "./usePose";
import { IDLE_POSE, SPEAKING_POSE, LISTENING_POSE } from "./poses";
import {
  usePersonalityEffects,
  type PersonalityEffects,
} from "./usePersonalityEffects";
import { useSpeechAnimation } from "./useSpeechAnimation";

/* ------------------------------------------------------------------ */
/*  Public handle interface                                           */
/* ------------------------------------------------------------------ */

/** Kept as an alias for back-compat; the engine now honours the full contract. */
export type AvaturnAvatarHandle = AvatarEngineHandle;

/* ------------------------------------------------------------------ */
/*  Props                                                             */
/* ------------------------------------------------------------------ */

interface Props {
  avatarUrl: string;
  mood?: AvatarMood;
  view?: AvatarView;
  mute?: boolean;
  personality?: PersonalityTraits;
  speaking?: boolean;
  isListening?: boolean;
  className?: string;
  style?: React.CSSProperties;
  onLoad?: () => void;
  onError?: (error: string) => void;
  onSpeakStart?: () => void;
  onSpeakEnd?: () => void;
}

/* ------------------------------------------------------------------ */
/*  Inner 3D model component (runs inside Canvas)                     */
/* ------------------------------------------------------------------ */

interface ModelProps {
  url: string;
  effects: PersonalityEffects;
  speaking: boolean;
  isListening: boolean;
  onLoaded: () => void;
  onLoadError: (msg: string) => void;
}

function AvatarModel({
  url,
  effects,
  speaking,
  isListening,
  onLoaded,
  onLoadError,
}: ModelProps) {
  const groupRef = useRef<THREE.Group>(null);
  const loadFired = useRef(false);

  const gltf = useGLTF(url);

  // Select pose based on state
  const pose = speaking
    ? SPEAKING_POSE
    : isListening
    ? LISTENING_POSE
    : IDLE_POSE;
  usePose(gltf, pose);

  // Speech-synced head animation (simulates talking without blend shapes)
  useSpeechAnimation({ gltf, speaking, isListening });

  // Signal load success
  useEffect(() => {
    if (gltf?.scene && !loadFired.current) {
      loadFired.current = true;
      onLoaded();
    }
  }, [gltf, onLoaded]);

  // Idle breathing animation — speed/amplitude driven by personality energy
  useFrame(({ clock }) => {
    if (!groupRef.current) return;
    const t = clock.getElapsedTime();
    const speed = 0.8 * effects.breathSpeed;
    const amp = 0.006 * effects.breathAmplitude;
    groupRef.current.position.y = -0.8 + Math.sin(t * speed) * amp;

    // Subtle speaking head bob
    if (speaking) {
      groupRef.current.rotation.x = Math.sin(t * 3) * 0.008;
    } else {
      groupRef.current.rotation.x = 0;
    }
  });

  // Smooth skin materials + apply personality-driven emissive glow
  useEffect(() => {
    if (!gltf?.scene) return;
    const emissiveColor = new THREE.Color(effects.emissiveColor);

    gltf.scene.traverse((child) => {
      if (!(child as THREE.Mesh).isMesh) return;
      const mesh = child as THREE.Mesh;
      const mat = mesh.material;
      if (!mat || !("roughness" in mat)) return;

      const stdMat = mat as THREE.MeshStandardMaterial;

      // Skin smoothing — reduce roughness for a softer, less plasticky look
      // and add subtle emissive to simulate subsurface scattering
      const name = mesh.name.toLowerCase();
      const isSkin = name.includes("body") || name.includes("head") || name.includes("face");

      if (isSkin) {
        // Smoother skin: lower roughness, slight metalness for specular highlights
        stdMat.roughness = Math.min(stdMat.roughness, 0.45);
        stdMat.metalness = Math.max(stdMat.metalness, 0.02);

        // Fake subsurface scattering via warm emissive tint
        stdMat.emissive = new THREE.Color("#ffd6c0");
        stdMat.emissiveIntensity = 0.04;
      } else {
        // Non-skin: personality-driven glow
        stdMat.emissive = emissiveColor;
        stdMat.emissiveIntensity = effects.emissiveIntensity;
      }

      stdMat.needsUpdate = true;
    });
  }, [effects.emissiveColor, effects.emissiveIntensity, gltf]);

  // Apply head/neck personality offsets via direct bone manipulation
  useEffect(() => {
    if (!gltf?.scene) return;
    const headBone = gltf.scene.getObjectByName("Head") as THREE.Bone | null;
    const neckBone = gltf.scene.getObjectByName("Neck") as THREE.Bone | null;

    // These are additive nudges on top of the pose system
    // The usePose hook will keep overriding, so we need a different approach:
    // We'll let the useFrame handle the head offset instead
  }, [effects.headOffset, effects.neckOffset, gltf]);

  return (
    <group ref={groupRef} position={[0, -0.8, 0]} scale={1}>
      <primitive object={gltf.scene} />
    </group>
  );
}

/* ------------------------------------------------------------------ */
/*  Loading fallback                                                   */
/* ------------------------------------------------------------------ */

function CanvasLoader() {
  const meshRef = useRef<THREE.Mesh>(null);
  useFrame(({ clock }) => {
    if (meshRef.current) {
      meshRef.current.rotation.y = clock.getElapsedTime() * 2;
      meshRef.current.rotation.x = clock.getElapsedTime() * 0.7;
    }
  });
  return (
    <mesh ref={meshRef}>
      <octahedronGeometry args={[0.15, 0]} />
      <meshStandardMaterial color="#00B4D8" wireframe />
    </mesh>
  );
}

/* ------------------------------------------------------------------ */
/*  Personality-reactive ambient light                                 */
/* ------------------------------------------------------------------ */

function PersonalityLight({ color }: { color: string }) {
  return <ambientLight intensity={1.2} color={color} />;
}

/* ------------------------------------------------------------------ */
/*  Main exported component                                           */
/* ------------------------------------------------------------------ */

const AvaturnAvatar = forwardRef<AvaturnAvatarHandle, Props>(
  function AvaturnAvatar(
    {
      avatarUrl,
      mood = "neutral",
      mute = false,
      personality = DEFAULT_PERSONALITY,
      speaking = false,
      isListening = false,
      className = "",
      style,
      onLoad,
      onError,
      onSpeakStart,
      onSpeakEnd,
    },
    ref
  ) {
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [currentMood, setCurrentMood] = useState<AvatarMood>(mood);
    // This engine has no viseme model; internal speaking drives head motion
    // while audio plays. OR-ed with the external `speaking` prop.
    const [internalSpeaking, setInternalSpeaking] = useState(false);
    const readyRef = useRef(false);

    const effects = usePersonalityEffects(personality);

    useEffect(() => {
      setCurrentMood(mood);
    }, [mood]);

    // Play server-TTS PCM (no lipsync — audio + head motion). Falls back to
    // browser speech if the backend returns nothing.
    const speakAudio = useCallback(
      async (pcmBase64: string, sampleRate: number, text: string) => {
        onSpeakStart?.();
        setInternalSpeaking(true);
        try {
          if (mute) {
            // muted: hold the speaking pose for the clip's rough duration
            const ms = (pcmBase64.length * 0.75) / 2 / sampleRate * 1000;
            await new Promise((r) => setTimeout(r, ms));
          } else {
            await playPcmAudio(pcmBase64, sampleRate);
          }
        } catch {
          if (!mute) await new Promise<void>((r) => browserSpeak(text, r));
        } finally {
          setInternalSpeaking(false);
          onSpeakEnd?.();
        }
      },
      [mute, onSpeakStart, onSpeakEnd]
    );

    useImperativeHandle(
      ref,
      (): AvatarEngineHandle => ({
        speakText: async (text: string) => {
          const res = await synthesize(text);
          if (res.success && res.audio_base64) {
            await speakAudio(res.audio_base64, res.sample_rate || 22050, text);
          } else if (!mute) {
            onSpeakStart?.();
            setInternalSpeaking(true);
            await new Promise<void>((r) => browserSpeak(text, r));
            setInternalSpeaking(false);
            onSpeakEnd?.();
          }
        },
        speakPcm: (pcmBase64: string, sampleRate: number, text: string) =>
          speakAudio(pcmBase64, sampleRate, text),
        stopSpeaking: () => {
          browserSpeakStop();
          setInternalSpeaking(false);
          onSpeakEnd?.();
        },
        setMood: (m: AvatarMood) => setCurrentMood(m),
        setView: () => {
          /* fixed portrait framing — no camera views in this engine */
        },
        isReady: () => readyRef.current,
      }),
      [mute, speakAudio, onSpeakStart, onSpeakEnd]
    );

    const handleLoaded = useCallback(() => {
      readyRef.current = true;
      setLoading(false);
      setError(null);
      onLoad?.();
    }, [onLoad]);

    const handleError = useCallback(
      (msg: string) => {
        readyRef.current = false;
        setLoading(false);
        setError(msg);
        onError?.(msg);
      },
      [onError]
    );

    const handleRetry = useCallback(() => {
      readyRef.current = false;
      setLoading(true);
      setError(null);
      window.location.reload();
    }, []);

    return (
      <div
        className={`relative overflow-hidden ${className}`}
        style={{ minHeight: "200px", height: "400px", ...style }}
      >
        <Canvas
          gl={{ alpha: true, antialias: true }}
          camera={{ position: [0, 1.05, 1.0], fov: 30, near: 0.1, far: 50 }}
          style={{ background: "transparent" }}
        >
          {/* Personality-reactive ambient lighting */}
          <PersonalityLight color={effects.ambientColor} />
          {/* Key light — main illumination from upper-right */}
          <directionalLight
            position={[3, 5, 4]}
            intensity={1.8}
            color="#e0f0f5"
            castShadow
          />
          {/* Fill light — softer from the left to reduce harsh shadows */}
          <directionalLight
            position={[-2, 3, -3]}
            intensity={0.8}
            color="#d4eaf0"
          />
          {/* Rim light — subtle backlight for depth separation */}
          <directionalLight
            position={[0, 2, -4]}
            intensity={0.4}
            color="#80deea"
          />
          <hemisphereLight args={["#d6eef5", "#4a6670", 0.9]} />

          <Suspense fallback={<CanvasLoader />}>
            <AvatarModel
              url={avatarUrl}
              effects={effects}
              speaking={speaking || internalSpeaking}
              isListening={isListening}
              onLoaded={handleLoaded}
              onLoadError={handleError}
            />
          </Suspense>

          <OrbitControls
            enablePan={false}
            enableZoom={true}
            minPolarAngle={Math.PI / 3}
            maxPolarAngle={Math.PI / 2.2}
            target={[0, 0.85, 0]}
            minDistance={0.6}
            maxDistance={2.0}
          />
        </Canvas>

        {loading && (
          <div className="absolute inset-0 flex flex-col items-center justify-center rounded-2xl bg-gradient-to-b from-[#0096C7]/20 to-[#005F8A]/30 backdrop-blur-sm">
            <div className="h-16 w-16 animate-spin rounded-full border-4 border-[#00B4D8] border-t-transparent" />
            <p className="mt-4 text-sm font-medium text-(--text)">
              Loading avatar...
            </p>
          </div>
        )}

        {error && !loading && (
          <div className="absolute inset-0 flex flex-col items-center justify-center rounded-2xl p-4 backdrop-blur-sm bg-(--puff-red)/20">
            <p className="text-center text-sm text-(--puff-red)">
              Avatar failed to load
            </p>
            <p className="mt-1 text-center text-xs text-(--puff-red)/70">{error}</p>
            <button
              onClick={handleRetry}
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

export default AvaturnAvatar;
