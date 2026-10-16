import { NextRequest } from "next/server";
import { guardCredentialedRoute } from "@/app/_lib/server/routeGuard";

export const runtime = "nodejs";

const API_URL =
  process.env.PUFFPAI_BACKEND_URL ||
  "http://localhost:8000";
// Server-side only (never NEXT_PUBLIC_): the same key the /api/pai proxy
// sends. Empty means none is attached and the backend refuses.
const API_KEY = process.env.PUFFPAI_API_KEY ?? "";

/**
 * Proxy uploaded-document "source material" requests to the PuffPAI backend.
 *
 * Lets the dashboard embed a user-uploaded document's original file (e.g. a
 * PDF) in an <embed>/<iframe> from the same origin, so the browser doesn't
 * need to attach an API key to the media request. The backend endpoint
 * (GET /knowledge/library/<name>/source) needs the key (it is not
 * public), so this route, behind the pairing gate, adds it here.
 *
 * Usage: /api/knowledge/source?name=<collection>[&format=text]
 */
export async function GET(req: NextRequest) {
  // Household pairing gate (second layer behind middleware.ts). Uploaded
  // documents are household material; an unpaired LAN device must not be able
  // to pull them through this same-origin proxy.
  const denied = guardCredentialedRoute(req);
  if (denied) return denied;

  const { searchParams } = req.nextUrl;
  const name = searchParams.get("name");
  const format = searchParams.get("format");

  if (!name) {
    return new Response("Missing name parameter", { status: 400 });
  }

  try {
    let backendUrl = `${API_URL}/knowledge/library/${encodeURIComponent(name)}/source`;
    if (format) backendUrl += `?format=${encodeURIComponent(format)}`;

    const upstream = await fetch(backendUrl, {
      headers: API_KEY ? { Authorization: `Bearer ${API_KEY}` } : {},
    });

    if (!upstream.ok) {
      return new Response("Source not found", { status: upstream.status });
    }

    const contentType =
      upstream.headers.get("content-type") || "application/octet-stream";

    return new Response(upstream.body, {
      status: 200,
      headers: {
        "Content-Type": contentType,
        "Cache-Control": "private, max-age=3600",
      },
    });
  } catch (err) {
    console.error("Source proxy error:", err);
    return new Response("Proxy error", { status: 502 });
  }
}
