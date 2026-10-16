/**
 * Pluggable avatar engine contract.
 *
 * Every avatar renderer (Puff SVG, TalkingHead 3D, R3F/GLB, …) implements the
 * SAME imperative handle and prop shape, so the placements (tile, overlay,
 * panel) drive any engine through one ref without knowing which is mounted.
 * New engines are added by registering a descriptor — no changes to callers.
 *
 * Copyright (c) 2026 Cypher Sisters. Apache-2.0 License.
 */

import type { ComponentType, RefAttributes } from "react";
import type { AvatarMood, AvatarView } from "../../../_interfaces/avatar";

/**
 * Imperative methods every engine exposes via ref. This is the historical
 * TalkingHeadHandle, promoted to the shared engine contract. Engines that can't
 * honour a capability (e.g. an SVG engine has no camera view) implement the
 * method as a safe no-op rather than omitting it, so callers never branch.
 */
export interface AvatarEngineHandle {
  /** Speak text using the engine's own TTS path (with lipsync where supported). */
  speakText: (text: string) => Promise<void>;
  /** Speak server-TTS PCM audio WITH lipsync (mouth moves). */
  speakPcm: (pcmBase64: string, sampleRate: number, text: string) => Promise<void>;
  stopSpeaking: () => void;
  setMood: (mood: AvatarMood) => void;
  setView: (view: AvatarView) => void;
  isReady: () => boolean;
}

/** Props every engine component accepts. Engines ignore what they don't use. */
export interface AvatarEngineProps {
  /** Model/source URL. GLB path for 3D engines; ignored by the SVG engine. */
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

/**
 * What an engine can do. Drives the picker UI (which controls to show) and lets
 * callers feature-detect instead of hard-coding engine ids.
 */
export interface AvatarEngineCapabilities {
  /** Mouth actually tracks speech audio/visemes. */
  lipsync: boolean;
  /** Responds to setMood(). */
  mood: boolean;
  /** Responds to setView() (camera framing). */
  view: boolean;
  /** Reacts to the personality trait sliders. */
  personality: boolean;
  /** What kind of user-supplied model this engine can load, if any. */
  customModel: "glb" | "image" | "none";
}

/** A React component that satisfies the engine contract. */
export type AvatarEngineComponent = ComponentType<
  AvatarEngineProps & RefAttributes<AvatarEngineHandle>
>;

/** Stable engine identifiers persisted in the store / profile / localStorage. */
export type AvatarEngineId = "puff" | "human" | "glb";

/**
 * Registry entry describing one engine. `Component` is a lazily-loaded
 * (`next/dynamic`, ssr:false) client component so heavy 3D engines never enter
 * the SSR bundle and only download when actually selected.
 */
export interface AvatarEngineDescriptor {
  id: AvatarEngineId;
  /** Short name for the picker. */
  label: string;
  /** One-line description for the picker. */
  description: string;
  capabilities: AvatarEngineCapabilities;
  /** Lazily-loaded renderer. */
  Component: AvatarEngineComponent;
  /**
   * Default model URL for this engine, if it needs one. The SVG engine has none.
   */
  defaultModelUrl?: string;
  /**
   * When true the engine can only run in a browser with WebGL / heavy deps and
   * should degrade to the fallback engine if it throws while loading.
   */
  requiresWebGL?: boolean;
}
