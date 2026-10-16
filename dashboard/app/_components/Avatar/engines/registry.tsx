"use client";

/**
 * Avatar engine registry.
 *
 * The single source of truth for which avatar engines exist and what each can
 * do. Placements resolve an engine by id and render it through <AvatarStage>;
 * the picker reads this list to build its UI. Adding an engine is one entry
 * here plus a component that implements the AvatarEngineHandle contract.
 *
 * No GLB ships with this repository: the default avatar is Puff, the cloud
 * logo. The 3D engines render a household's own model (uploaded or a LAN URL);
 * with none set, the stage draws Puff and shows a one-line hint.
 *
 * Every engine is loaded with next/dynamic (ssr:false): the SVG engine is cheap
 * and always safe, while the Three.js engines (~MBs) only download when a user
 * actually selects them. Ref forwards through dynamic() (React 19 treats ref as
 * a normal prop), so callers keep one imperative handle regardless of engine.
 *
 * Copyright (c) 2026 Cypher Sisters. Apache-2.0 License.
 */

import dynamic from "next/dynamic";
import type {
  AvatarEngineComponent,
  AvatarEngineDescriptor,
  AvatarEngineId,
} from "./types";

/** Shared loading skeleton for the heavier engines. */
function EngineLoading() {
  return (
    <div className="flex h-full min-h-[200px] items-center justify-center bg-gradient-to-b from-[#0096C7]/20 to-[#005F8A]/30">
      <div className="h-12 w-12 animate-spin rounded-full border-4 border-[#00B4D8] border-t-transparent" />
    </div>
  );
}

const PuffCloudEngine = dynamic(() => import("../PuffCloudAvatar"), {
  ssr: false,
}) as AvatarEngineComponent;

const TalkingHeadEngine = dynamic(() => import("../TalkingHeadAvatar"), {
  ssr: false,
  loading: () => <EngineLoading />,
}) as AvatarEngineComponent;

const GlbEngine = dynamic(() => import("../AvaturnAvatar"), {
  ssr: false,
  loading: () => <EngineLoading />,
}) as AvatarEngineComponent;

/**
 * Ordered engine list. Puff is first and is the guaranteed-safe fallback:
 * pure inline SVG + Web Audio, no WebGL, no downloads, always works.
 */
export const AVATAR_ENGINES: readonly AvatarEngineDescriptor[] = [
  {
    id: "puff",
    label: "Puff",
    description: "The PuffPAI cloud mascot. Inline SVG, audio-driven mouth. Always works.",
    capabilities: {
      lipsync: true, // amplitude-driven, not viseme, but the mouth tracks speech
      mood: false,
      view: false,
      personality: false,
      customModel: "none",
    },
    Component: PuffCloudEngine,
  },
  {
    id: "human",
    label: "Digital Human",
    description:
      "Full 3D human with viseme lip-sync, moods and idle motion (TalkingHead / Three.js).",
    capabilities: {
      lipsync: true,
      mood: true,
      view: true,
      personality: false,
      customModel: "glb", // Ready Player Me / GLB URL
    },
    Component: TalkingHeadEngine,
    requiresWebGL: true,
  },
  {
    id: "glb",
    label: "Custom 3D Model",
    description:
      "Bring your own GLB (Ready Player Me, Avaturn, Blender). Lightweight R3F renderer, personality-reactive.",
    capabilities: {
      lipsync: false, // simulated head motion only — no viseme model
      mood: true,
      view: false,
      personality: true,
      customModel: "glb",
    },
    Component: GlbEngine,
    requiresWebGL: true,
  },
] as const;

/** The engine used whenever a saved/selected id is missing or an engine fails. */
export const FALLBACK_ENGINE_ID: AvatarEngineId = "puff";

const BY_ID = new Map<string, AvatarEngineDescriptor>(
  AVATAR_ENGINES.map((e) => [e.id, e])
);

/** Type guard: is this string a registered engine id? */
export function isAvatarEngineId(value: unknown): value is AvatarEngineId {
  return typeof value === "string" && BY_ID.has(value);
}

/**
 * Resolve a descriptor by id, always returning a usable engine: unknown ids
 * fall back to Puff so a stale/typo'd setting never blanks the avatar.
 */
export function resolveEngine(id: string | null | undefined): AvatarEngineDescriptor {
  if (id && BY_ID.has(id)) return BY_ID.get(id)!;
  return BY_ID.get(FALLBACK_ENGINE_ID)!;
}

/** All engines, for building pickers/galleries. */
export function listEngines(): readonly AvatarEngineDescriptor[] {
  return AVATAR_ENGINES;
}
