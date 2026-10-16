/**
 * Avatar TypeScript interfaces for PuffPAI Digital Human.
 *
 * Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
 */

export interface AvatarConfig {
  url: string;
  body?: "M" | "F";
  lipsyncLang?: string;
  ttsLang?: string;
  ttsVoice?: string;
  ttsRate?: number;
  ttsPitch?: number;
  ttsVolume?: number;
  avatarMood?: string;
  avatarMute?: boolean;
  avatarIdleEyeContact?: number;
  avatarIdleHeadMove?: number;
  avatarSpeakingEyeContact?: number;
  avatarSpeakingHeadMove?: number;
}

export type AvatarMood =
  | "neutral"
  | "happy"
  | "angry"
  | "sad"
  | "fear"
  | "disgust"
  | "love"
  | "sleep";

export type AvatarView = "full" | "mid" | "upper" | "head";

export interface AvatarState {
  visible: boolean;
  mood: AvatarMood;
  speaking: boolean;
  voiceMode: boolean;
  avatarUrl: string;
  ttsSource: "backend" | "browser";
  avatarSize: "compact" | "full";
  isListening: boolean;
  loading: boolean;
  error: string | null;
}

export interface Viseme {
  id: number;
  time: number;
  duration: number;
}

export interface WordTiming {
  word: string;
  start: number;
  end: number;
}

export interface VoiceCapabilities {
  tts_available: boolean;
  stt_available: boolean;
  tts_engine: string;
  voices: string[];
}

export interface SynthesizeResponse {
  audio_base64: string;
  sample_rate: number;
  visemes: Viseme[];
  words: WordTiming[];
}

export interface AvatarSettings {
  avatar_3d_url: string;
  avatar_3d_mood: AvatarMood;
  voice_id: string;
  tts_source: "backend" | "browser";
  camera_view: AvatarView;
}

/* ── Personality system ─────────────────────────────────────── */

export interface PersonalityTraits {
  warmth: number;      // 0–100  cold ↔ warm
  energy: number;      // 0–100  calm ↔ energetic
  humor: number;       // 0–100  serious ↔ playful
  formality: number;   // 0–100  casual ↔ formal
  curiosity: number;   // 0–100  practical ↔ curious
  confidence: number;  // 0–100  humble ↔ confident
  empathy: number;     // 0–100  analytical ↔ empathetic
  creativity: number;  // 0–100  logical ↔ creative
}

export type PersonalityTrait = keyof PersonalityTraits;

export interface PersonalityTraitConfig {
  key: PersonalityTrait;
  label: string;
  lowLabel: string;
  highLabel: string;
  icon: string;
  color: string;
}

export const DEFAULT_PERSONALITY: PersonalityTraits = {
  warmth: 50,
  energy: 50,
  humor: 50,
  formality: 50,
  curiosity: 50,
  confidence: 50,
  empathy: 50,
  creativity: 50,
};
