/**
 * Redux slice for PuffPAI Digital Human Avatar state.
 *
 * Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
 */

import { createSlice, PayloadAction } from "@reduxjs/toolkit";
import type { AvatarMood, PersonalityTraits, PersonalityTrait } from "../_interfaces/avatar";
import { DEFAULT_PERSONALITY } from "../_interfaces/avatar";
import type { AvatarEngineId } from "../_components/Avatar/engines/types";

/**
 * Last-resort model URL. No 3D model ships with this build: the default avatar
 * is Puff, the cloud logo. A household that wants a 3D face points `avatarUrl`
 * at a hosted URL or uploads its own GLB. With this empty and no custom model,
 * <AvatarStage> draws Puff plus a one-line hint pointing at the upload.
 */
export const DEFAULT_AVATAR_MODEL_URL = "";

interface AvatarSliceState {
  /** Show/hide the avatar across all placements */
  visible: boolean;
  /** Which avatar body to render: Puff the cloud mascot, or the 3D human */
  character: AvatarEngineId;
  /** Current emotional state */
  mood: AvatarMood;
  /** Avatar is currently speaking */
  speaking: boolean;
  /** Voice input/output mode active */
  voiceMode: boolean;
  /**
   * The user's explicitly chosen custom GLB URL. Empty string means "no custom
   * model": the engine's own default is used instead, then
   * `DEFAULT_AVATAR_MODEL_URL`. Keeping this empty by default is what makes
   * each engine's `defaultModelUrl` reachable, and what lets blanking the field
   * in Settings actually clear the choice (back to Puff).
   */
  avatarUrl: string;
  /** Which TTS engine to use */
  ttsSource: "backend" | "browser";
  /** Display size mode */
  avatarSize: "compact" | "full";
  /** Microphone is currently listening */
  isListening: boolean;
  /** Avatar model is loading */
  loading: boolean;
  /** Last error message */
  error: string | null;
  /** Personality trait values (0-100 each) */
  personality: PersonalityTraits;
}

const initialState: AvatarSliceState = {
  visible: true,
  character: "puff",
  mood: "neutral",
  speaking: false,
  voiceMode: false,
  avatarUrl: "",
  ttsSource: "backend",
  avatarSize: "compact",
  isListening: false,
  loading: false,
  error: null,
  personality: DEFAULT_PERSONALITY,
};

const avatarSlice = createSlice({
  name: "avatar",
  initialState,
  reducers: {
    setAvatarVisible(state, action: PayloadAction<boolean>) {
      state.visible = action.payload;
    },
    setAvatarCharacter(state, action: PayloadAction<AvatarEngineId>) {
      state.character = action.payload;
    },
    setAvatarMood(state, action: PayloadAction<AvatarMood>) {
      state.mood = action.payload;
    },
    setAvatarSpeaking(state, action: PayloadAction<boolean>) {
      state.speaking = action.payload;
    },
    setVoiceMode(state, action: PayloadAction<boolean>) {
      state.voiceMode = action.payload;
    },
    setAvatarUrl(state, action: PayloadAction<string>) {
      state.avatarUrl = action.payload;
    },
    setTtsSource(state, action: PayloadAction<"backend" | "browser">) {
      state.ttsSource = action.payload;
    },
    setAvatarSize(state, action: PayloadAction<"compact" | "full">) {
      state.avatarSize = action.payload;
    },
    setIsListening(state, action: PayloadAction<boolean>) {
      state.isListening = action.payload;
    },
    setAvatarLoading(state, action: PayloadAction<boolean>) {
      state.loading = action.payload;
    },
    setAvatarError(state, action: PayloadAction<string | null>) {
      state.error = action.payload;
    },
    setPersonalityTrait(
      state,
      action: PayloadAction<{ trait: PersonalityTrait; value: number }>
    ) {
      state.personality[action.payload.trait] = Math.max(
        0,
        Math.min(100, action.payload.value)
      );
    },
    setPersonality(state, action: PayloadAction<PersonalityTraits>) {
      state.personality = action.payload;
    },
  },
});

export const {
  setAvatarVisible,
  setAvatarCharacter,
  setAvatarMood,
  setAvatarSpeaking,
  setVoiceMode,
  setAvatarUrl,
  setTtsSource,
  setAvatarSize,
  setIsListening,
  setAvatarLoading,
  setAvatarError,
  setPersonalityTrait,
  setPersonality,
} = avatarSlice.actions;

export default avatarSlice.reducer;
