import { NextRequest, NextResponse } from "next/server";
import { guardCredentialedRoute } from "@/app/_lib/server/routeGuard";

export const runtime = "nodejs";

const API_BASE_URL =
  process.env.PUFFPAI_BACKEND_URL ??
  "http://127.0.0.1:8000";
const API_KEY = process.env.PUFFPAI_API_KEY ?? "";

// Legacy direct-to-HA fallback. Kept only for installs that still set these;
// the PuffPAI path above is preferred because it means one credential instead
// of a second, unscoped HA token living in the dashboard's environment.
const HA_BASE_URL = process.env.HA_BASE_URL ?? "";
const HA_TOKEN = process.env.HA_ACCESS_TOKEN ?? "";

const ENTITY_RE = /^camera\.[a-z0-9_]+$/;

/**
 * Proxies a camera snapshot so the browser never sees any credential.
 * Usage: GET /api/camera/camera.front_door
 *
 * Fetches through the PuffPAI backend (/ha/camera/<entity_id>), which already
 * holds the Home Assistant token and validates the entity id before it reaches
 * HA. The dashboard polls this endpoint to simulate a live feed.
 */
export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ entityId: string }> }
) {
  // Household pairing gate (second layer behind middleware.ts). Camera frames
  // are the most privacy-sensitive thing the dashboard proxies.
  const denied = guardCredentialedRoute(req);
  if (denied) return denied;

  const { entityId } = await params;

  if (!ENTITY_RE.test(entityId)) {
    return NextResponse.json({ error: "invalid camera entity id" }, { status: 400 });
  }

  const viaPuffPAI = Boolean(API_KEY);
  if (!viaPuffPAI && !HA_TOKEN) {
    return NextResponse.json(
      { error: "No camera source configured: set PUFFPAI_API_KEY (preferred) or HA_ACCESS_TOKEN" },
      { status: 500 }
    );
  }

  const url = viaPuffPAI
    ? `${API_BASE_URL}/ha/camera/${entityId}`
    : `${HA_BASE_URL}/api/camera_proxy/${entityId}`;
  const authorization = `Bearer ${viaPuffPAI ? API_KEY : HA_TOKEN}`;

  try {
    const res = await fetch(url, {
      headers: { Authorization: authorization },
      // Battery cams (Reolink via hub) can take >10s to wake; the dashboard
      // probe allows 20s, so this must be the longer of the two hops minus
      // headroom — 8s guaranteed a dead tile for any sleeping camera.
      signal: AbortSignal.timeout(18000),
      cache: "no-store",
    });

    if (!res.ok) {
      const source = viaPuffPAI ? "PuffPAI" : "HA";
      return NextResponse.json(
        { error: `${source} returned ${res.status}` },
        { status: res.status }
      );
    }

    const imageBuffer = await res.arrayBuffer();
    const contentType = res.headers.get("content-type") ?? "image/jpeg";

    return new NextResponse(imageBuffer, {
      status: 200,
      headers: {
        "Content-Type": contentType,
        "Cache-Control": "no-cache, no-store, must-revalidate",
      },
    });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return NextResponse.json({ error: message }, { status: 502 });
  }
}
