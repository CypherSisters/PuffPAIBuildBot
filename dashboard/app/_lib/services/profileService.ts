import { apiGet, apiPut } from '../api';
import type { UserProfile, AISettings } from '../../_interfaces/api';

export async function getProfile(): Promise<UserProfile> {
  const res = await apiGet<{ success: boolean; profile: UserProfile }>('/profile');
  return res.profile;
}

export async function updateProfile(profile: UserProfile): Promise<{ success: boolean }> {
  return apiPut('/profile', profile);
}

export async function getAISettings(): Promise<AISettings> {
  const res = await apiGet<{ success: boolean; settings: AISettings }>('/profile/ai');
  return res.settings;
}

export async function updateAISettings(settings: Partial<AISettings>): Promise<{ success: boolean }> {
  return apiPut('/profile/ai', settings);
}

/*
 * The owner's own 3D avatar model is NOT here.
 *
 * It used to be: a /profile/avatar-model endpoint that this build's backend
 * does not have. The contract that does exist is /avatar/custom (GET status,
 * POST a .glb, DELETE it), and it lives in avatarService.ts next to this file.
 * One upload path, not two — the version with two silently 404'd.
 *
 * The distinction that matters: /profile/ai holds SETTINGS and will 400 and
 * name any field it does not accept, which is why the model URL was never a
 * setting. The file is the state; the browser only remembers which avatar is
 * picked.
 */
