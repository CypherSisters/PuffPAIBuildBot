// Base API client for the PuffPAI backend.
//
// THE BROWSER NEVER HOLDS THE BACKEND KEY.
//
// Every call below goes to the same-origin proxy at /api/pai, which attaches
// `Authorization: Bearer <PUFFPAI_API_KEY>` server-side
// (app/api/pai/[...path]/route.ts) behind the pairing gate. So this module
// sends no Authorization header at all, and there are deliberately no
// browser-side key getter/setter helpers any more. (Their old names are
// asserted absent by scripts/check-no-secret-in-bundle.js, so they are not
// spelled out anywhere under app/ — including in prose like this.)
//
//   * a NEXT_PUBLIC_ env var is INLINED into the client bundle by Next, so
//     reading the key from one shipped the owner's backend credential to every
//     browser on the LAN in plain JavaScript;
//   * localStorage was no better. It kept the key in the browser where any
//     script on the page could read it, and it bought nothing once the proxy
//     started attaching the key itself.
//
// If a future call genuinely needs a credential the proxy cannot supply, add it
// to the proxy, not to this file. Nothing here should ever learn the key again.
//
// The base is a hardcoded relative path, never read from an env var.
// An env-supplied base could point the browser straight at the backend (or at
// another origin), which bypasses the proxy and therefore the pairing gate, the
// path allowlist and the cross-site check — and would send X-Vault-Token to
// whatever host was configured. Same-origin-relative is the only safe value, so
// it is the only value.
const API_URL = '/api/pai';

// Removing the code that WROTE the key does not remove the copies it already
// left behind: any browser that ever used the old Profile > API Key card still
// has the appliance's backend credential sitting in localStorage. Deleting the
// writer and leaving the copy would be the worst of both worlds, so purge it on
// module load. Safe to run forever — it is a no-op once the key is gone, and
// nothing reads that entry any more.
if (typeof window !== 'undefined') {
  try {
    localStorage.removeItem('puffpai_api_key');
  } catch {
    /* storage disabled or partitioned: nothing to clean up */
  }
}

function getHeaders(): HeadersInit {
  return { 'Content-Type': 'application/json' };
}

export async function apiGet<T = any>(
  endpoint: string,
  opts?: { cache?: RequestCache },
): Promise<T> {
  const res = await fetch(`${API_URL}${endpoint}`, {
    method: 'GET',
    headers: getHeaders(),
    cache: opts?.cache,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(err.error || res.statusText);
  }
  return res.json();
}

export async function apiPost<T = any>(endpoint: string, body?: any): Promise<T> {
  const res = await fetch(`${API_URL}${endpoint}`, {
    method: 'POST',
    headers: getHeaders(),
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(err.error || res.statusText);
  }
  return res.json();
}

/**
 * POST that keeps the HTTP status and the raw body instead of throwing.
 *
 * Some backend routes (e.g. POST /dashboard/cameras) answer non-2xx with a
 * human-readable `message` and no `error` key — apiPost would discard both.
 * Use this when the status code is part of the contract.
 */
export async function apiPostRaw<T = any>(
  endpoint: string,
  body?: any
): Promise<{ status: number; ok: boolean; data: T | null }> {
  const res = await fetch(`${API_URL}${endpoint}`, {
    method: 'POST',
    headers: getHeaders(),
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = (await res.json().catch(() => null)) as T | null;
  return { status: res.status, ok: res.ok, data };
}

export async function apiPut<T = any>(endpoint: string, body?: any): Promise<T> {
  const res = await fetch(`${API_URL}${endpoint}`, {
    method: 'PUT',
    headers: getHeaders(),
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(err.error || res.statusText);
  }
  return res.json();
}

/** An error from one of these helpers, carrying the status the caller needs.
 *
 *  Added for one distinction that matters: a 404 from this dashboard's own proxy
 *  means "that route is not in this build", which is a thing to hide a control
 *  for, while a 400 or a 500 is a thing to SHOW the owner. Without the status
 *  the two are one string and callers guess. */
export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/** PATCH — a partial edit of something that already exists. Used by the persona
 *  editor, where every field is optional and an absent one means "leave it". */
export async function apiPatch<T = any>(endpoint: string, body?: any): Promise<T> {
  const res = await fetch(`${API_URL}${endpoint}`, {
    method: 'PATCH',
    headers: getHeaders(),
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }));
    throw new ApiError(err.error || res.statusText, res.status);
  }
  return res.json();
}

// `body` is optional because most DELETEs name their target in the path. The
// ones that carry a typed confirmation (DELETE /persona/<id> wants
// {"confirm": "<id>"}) need it, and a confirmation belongs in the body rather
// than a query string, where it would land in every access log on the way.
export async function apiDelete<T = any>(endpoint: string, body?: any): Promise<T> {
  const res = await fetch(`${API_URL}${endpoint}`, {
    method: 'DELETE',
    headers: getHeaders(),
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(err.error || res.statusText);
  }
  return res.json();
}

// SSE streaming for chat
export function apiStream(endpoint: string, body: any, onChunk: (text: string) => void, onDone: () => void) {
  fetch(`${API_URL}${endpoint}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...body, stream: true }),
  }).then(async (res) => {
    if (!res.ok) throw new Error(res.statusText);
    const reader = res.body?.getReader();
    const decoder = new TextDecoder();
    if (!reader) { onDone(); return; }

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      const text = decoder.decode(value);
      const lines = text.split('\n');
      for (const line of lines) {
        if (line.startsWith('data: ')) {
          const data = line.slice(6);
          if (data === '[DONE]') { onDone(); return; }
          try {
            const parsed = JSON.parse(data);
            const content = parsed.choices?.[0]?.delta?.content || '';
            if (content) onChunk(content);
          } catch { /* skip non-JSON lines */ }
        }
      }
    }
    onDone();
  }).catch(() => onDone());
}

// Health check
export async function checkHealth(): Promise<boolean> {
  try {
    const res = await fetch(`${API_URL}/health`);
    return res.ok;
  } catch {
    return false;
  }
}

export { API_URL };
