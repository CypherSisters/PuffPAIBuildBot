import { apiGet, apiPost, API_URL } from '../api';
import type { VaultItem } from '../../_interfaces/api';

export interface VaultStatus {
  /** Whether the backend vault module is loaded at all. */
  vault_available?: boolean;
  /**
   * Whether a vault has ever been created (a PIN was set and a key salt
   * written). Older backends omit this field; callers must treat
   * ``undefined`` as "unknown", not as "not set up".
   */
  vault_initialized?: boolean;
  mfa_enabled?: boolean;
  /** How long the server leaves the vault open without use, then locks it. */
  auto_lock_seconds?: number;
  auto_lock_minutes?: number;
  /** The server's own sentence about that, shown as it is. */
  auto_lock_line?: string;
  /** The vault's folder or drive is missing (never offer a new vault then). */
  vault_missing?: boolean;
  vault_missing_message?: string;
}

/**
 * Read vault availability / initialization state. This is the only
 * endpoint that reports initialization without requiring a PIN, so the UI
 * uses it to tell "never created" apart from "created and locked".
 */
export async function getVaultStatus(): Promise<VaultStatus> {
  return apiGet<VaultStatus>('/vault/mfa-status');
}

export async function unlockVault(pin: string): Promise<{ success: boolean; error?: string }> {
  return apiPost('/vault/unlock', { pin });
}

/** What POST /vault/recover answers. The fields are the backend's own. */
export interface VaultRecoverData {
  success?: boolean;
  message?: string;
  session_token?: string;
  recovery_code?: string;
  recovery_code_notice?: string;
  throttled?: boolean;
  retry_after?: number;
  weak_password?: boolean;
  recovery_available?: boolean;
}

/**
 * Open the vault with the recovery code and set a new PIN in the same call.
 *
 * Both go in the request BODY, never in the address, so neither lands in a
 * browser history, a proxy log or a server access log. Nothing here keeps
 * them: no storage, no console, no store. The status is returned with the
 * body because the page says different things for a cooldown (429), a PIN
 * the rules refuse (400) and a code that does not match (401).
 */
export async function recoverVault(
  recoveryCode: string,
  newPin: string,
): Promise<{ status: number; data: VaultRecoverData }> {
  const res = await fetch(`${API_URL}/vault/recover`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ recovery_code: recoveryCode, new_pin: newPin }),
    cache: 'no-store',
  });
  let data: VaultRecoverData = {};
  try {
    data = (await res.json()) as VaultRecoverData;
  } catch {
    data = {};
  }
  return { status: res.status, data };
}

export async function lockVault(): Promise<{ success: boolean }> {
  return apiPost('/vault/lock');
}

/**
 * Fetch vault contents. The backend requires the unlock session token in
 * the X-Vault-Token header, and returns the list under ``items`` (not
 * ``contents``). Callers must pass the token returned by /vault/unlock.
 */
export async function getVaultContents(token?: string | null): Promise<VaultItem[]> {
  // X-Vault-Token is the vault UNLOCK SESSION, not the backend API key; the
  // proxy relays X-* headers upstream. The backend key is attached by the proxy
  // server-side, so no Authorization header is set here.
  const headers: HeadersInit = { 'Content-Type': 'application/json' };
  if (token) headers['X-Vault-Token'] = token;

  const res = await fetch(`${API_URL}/vault/contents`, { method: 'GET', headers });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ message: res.statusText }));
    throw new Error(err.message || res.statusText);
  }
  const data: { success: boolean; items?: VaultItem[]; contents?: VaultItem[] } = await res.json();
  // Prefer ``items`` (current API); fall back to ``contents`` for older builds.
  return data.items || data.contents || [];
}

/**
 * Store a text item in the vault.
 *
 * The unlock session token is REQUIRED by the backend: POST /vault/store reads
 * it from `X-Vault-Token` (or a `session_token` body field) and answers 403
 * "Vault is locked. Unlock first." without it. This used to go through apiPost,
 * which sends neither, so every store from an unlocked page was rejected as
 * though the vault had re-locked itself. apiPost/apiDelete carry no header
 * argument, so — exactly as getVaultContents above already does — the fetch is
 * spelled out here instead. X-Vault-Token is the vault session, NOT the backend
 * API key: the /api/pai proxy relays X-* headers upstream and attaches the key
 * server-side, so the browser still never holds a credential.
 */
export async function storeVaultItem(
  key: string,
  value: string,
  type: string,
  token?: string | null,
): Promise<{ success: boolean; message?: string }> {
  const headers: HeadersInit = { 'Content-Type': 'application/json' };
  if (token) headers['X-Vault-Token'] = token;

  const res = await fetch(`${API_URL}/vault/store`, {
    method: 'POST',
    headers,
    body: JSON.stringify({ key, value, type }),
  });
  const data = await res.json().catch(() => ({ success: false, message: res.statusText }));
  if (!res.ok) throw new Error(data.message || data.error || res.statusText);
  return data;
}

/** Delete a vault item. Same 403-without-token contract as storeVaultItem. */
export async function deleteVaultItem(
  key: string,
  token?: string | null,
): Promise<{ success: boolean; message?: string }> {
  const headers: HeadersInit = { 'Content-Type': 'application/json' };
  if (token) headers['X-Vault-Token'] = token;

  const res = await fetch(`${API_URL}/vault/item/${encodeURIComponent(key)}`, {
    method: 'DELETE',
    headers,
  });
  const data = await res.json().catch(() => ({ success: false, message: res.statusText }));
  if (!res.ok) throw new Error(data.message || data.error || res.statusText);
  return data;
}
