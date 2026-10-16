import { NextRequest } from "next/server";
import { guardCredentialedRoute } from "@/app/_lib/server/routeGuard";

export const runtime = "nodejs";

const API_URL =
  process.env.PUFFPAI_BACKEND_URL ||
  "http://localhost:8000";

/**
 * Proxy ZIM asset requests to the PuffPAI backend.
 *
 * This avoids browser "URL safety check" blocks that occur when
 * cross-origin media (video/audio) is loaded via dangerouslySetInnerHTML.
 *
 * Usage: /api/knowledge/asset?collection=<col>&path=<asset_path>
 */
export async function GET(req: NextRequest) {
  // Household pairing gate (second layer behind middleware.ts).
  const denied = guardCredentialedRoute(req);
  if (denied) return denied;

  const { searchParams } = req.nextUrl;
  const collection = searchParams.get("collection");
  const assetPath = searchParams.get("path");

  if (!collection || !assetPath) {
    return new Response("Missing collection or path parameter", { status: 400 });
  }

  try {
    const backendUrl = `${API_URL}/knowledge/library/${encodeURIComponent(collection)}/asset?path=${encodeURIComponent(assetPath)}`;

    // Forward the browser's Range header so <video> seeking gets a 206 with the
    // requested byte slice instead of the whole file re-downloaded from zero.
    const range = req.headers.get("range");
    const upstream = await fetch(backendUrl, {
      headers: range ? { Range: range } : {},
    });

    // 200 (full) and 206 (partial) are both success; anything else is an error.
    if (upstream.status !== 200 && upstream.status !== 206) {
      return new Response("Asset not found", { status: upstream.status });
    }

    // Mirror the upstream response verbatim: status (200/206) plus the headers
    // that make a media element seekable. Never hardcode 200/Accept-Ranges —
    // that told the browser seeking was supported when the body was always the
    // whole file, so scrubbing a talk re-fetched from the start.
    const headers = new Headers();
    headers.set(
      "Content-Type",
      upstream.headers.get("content-type") || "application/octet-stream",
    );
    headers.set("Cache-Control", "public, max-age=86400");
    for (const h of ["accept-ranges", "content-range", "content-length"]) {
      const v = upstream.headers.get(h);
      if (v) headers.set(h, v);
    }

    return new Response(upstream.body, { status: upstream.status, headers });
  } catch (err) {
    console.error("Asset proxy error:", err);
    return new Response("Proxy error", { status: 502 });
  }
}
