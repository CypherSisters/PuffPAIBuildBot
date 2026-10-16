/**
 * Owner-supplied avatar — the only face, because none ships.
 *
 * No .glb is bundled, so this is how an owner gets a 3D head at all. The
 * file lives on the backend under PUFFPAI_DATA_DIR (localStorage holds only
 * *which* avatar is picked, which is why an uploaded head and any model a
 * build adds locally are both just URLs to the renderer). Nothing here opens a second rendering
 * path: the custom model is a URL handed to the same GLTF loader the bundled
 * heads go through.
 *
 * The model URL is same-origin through /api/pai, so the loader's own fetch —
 * which cannot set an Authorization header — is authenticated by the proxy.
 *
 * Copyright (c) Cypher Sisters. Apache-2.0 License.
 */
import { API_URL, apiGet, apiDelete } from "../api";

/** Fixed route. No path parameter, so there is nothing to walk out of. */
export const CUSTOM_AVATAR_PATH = `${API_URL}/avatar/custom/model.glb`;

export interface CustomAvatarMeta {
  stored_name: string;
  original_name: string;
  size_bytes: number;
  uploaded_at: string;
}

export interface CustomAvatarStatus {
  success: boolean;
  present: boolean;
  avatar: CustomAvatarMeta | null;
  max_bytes: number;
  max_mb: number;
  allowed_extensions: string[];
}

export interface CustomAvatarUploadResult {
  success: boolean;
  avatar?: CustomAvatarMeta;
  error?: string;
  max_mb?: number;
}

/**
 * The URL to render the owner's avatar from.
 *
 * The slot is overwritten in place, so the upload timestamp rides along as a
 * cache-buster: without it a replaced avatar would keep showing the old face
 * until the browser felt like revalidating.
 */
export function customAvatarUrl(uploadedAt?: string): string {
  return uploadedAt
    ? `${CUSTOM_AVATAR_PATH}?v=${encodeURIComponent(uploadedAt)}`
    : CUSTOM_AVATAR_PATH;
}

/** True when a stored selection points at the owner's own avatar. */
export function isCustomAvatarUrl(url: string): boolean {
  return url.split("?")[0] === CUSTOM_AVATAR_PATH;
}

export async function getCustomAvatarStatus(): Promise<CustomAvatarStatus> {
  return apiGet<CustomAvatarStatus>("/avatar/custom");
}

/**
 * Upload a .glb. Returns the server's verdict rather than throwing, because a
 * rejection is something the owner needs to read, not a crash.
 *
 * The server revalidates everything checked here. The client-side extension
 * and size checks exist to save an owner a 32 MB round trip, not as the gate.
 */
export async function uploadCustomAvatar(
  file: File,
): Promise<CustomAvatarUploadResult> {
  const formData = new FormData();
  formData.append("file", file);

  // No Authorization here: /api/pai attaches the backend key server-side.
  // FormData also must not get an explicit Content-Type — the browser has to
  // set the multipart boundary itself.
  try {
    const res = await fetch(`${API_URL}/avatar/custom`, {
      method: "POST",
      body: formData,
    });
    const body = (await res
      .json()
      .catch(() => ({}))) as CustomAvatarUploadResult;
    if (!res.ok) {
      return {
        success: false,
        error: body.error || `Upload failed (${res.status})`,
        max_mb: body.max_mb,
      };
    }
    return body;
  } catch {
    return { success: false, error: "Could not reach the backend." };
  }
}

export async function deleteCustomAvatar(): Promise<{ success: boolean }> {
  return apiDelete<{ success: boolean }>("/avatar/custom");
}
