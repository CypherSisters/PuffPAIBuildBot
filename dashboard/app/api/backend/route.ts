/**
 * GET /api/backend -- which backend THIS dashboard is pointed at.
 *
 * Response: 200 { "url": "http://127.0.0.1:8000", "configured": boolean }
 *
 * One value, and it is the one the browser cannot know: PUFFPAI_BACKEND_URL is
 * read server-side by the proxy (app/api/pai/[...path]/route.ts) and defaults to
 * http://localhost:8000. On a machine running more than one PuffPAI - a stock
 * BuildBot on 8000 and somebody's live bot on the same port - that default is
 * how a dashboard silently drives the wrong box. So the UI says which one it is
 * talking to, and this route is where it learns the address.
 *
 * `configured` is false when nothing set the variable and the default is in
 * play, which is exactly the case worth showing differently.
 *
 * NO KEY IS USED HERE and no upstream call is made: the bot's NAME comes from
 * /build/self through the keyed proxy, which is already gated. This route
 * publishes the address only, and it is gated too - gateKindFor() is
 * default-deny, so an unpaired caller gets 401 like everywhere else.
 *
 * puff puff pass
 */
import { NextResponse, type NextRequest } from "next/server";
import { guardCredentialedRoute } from "@/app/_lib/server/routeGuard";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const DEFAULT_BACKEND = "http://localhost:8000";

export async function GET(req: NextRequest) {
  // Second layer, like every other route that reads process.env: the middleware
  // gates this path too. Cheaper than reasoning about whether an address is
  // sensitive - it is behind pairing either way.
  const denied = guardCredentialedRoute(req);
  if (denied) return denied;

  const configured = Boolean(process.env.PUFFPAI_BACKEND_URL);
  return NextResponse.json(
    { url: process.env.PUFFPAI_BACKEND_URL ?? DEFAULT_BACKEND, configured },
    { headers: { "Cache-Control": "no-store" } },
  );
}
