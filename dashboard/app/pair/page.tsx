import React from "react";
import type { Metadata } from "next";
import { getPageTitle } from "../_lib/config";
import PairForm from "./PairForm";
import { DEFAULT_BACKEND_URL } from "./pairWords";

export const metadata: Metadata = {
  title: getPageTitle("Pair this device"),
};

/**
 * Read the address on every request, never at build time. Without this, Next
 * built the page ahead of time and printed whatever PUFFPAI_BACKEND_URL the
 * BUILD saw (usually none, so "localhost:8000") on every bot, whatever
 * `puffpai start` later set for the running server.
 */
export const dynamic = "force-dynamic";

/**
 * /pair -- the one page an unpaired device can reach.
 *
 * Deliberately outside /dashboard: everything under /dashboard is gated, and a
 * gate whose own unlock screen sits behind it is a locked room with the key
 * inside. app/_lib/server/pairing.ts exempts exactly this path and the
 * /api/pair/* routes it calls; nothing else.
 *
 * A server component with no data fetching of its own -- it must render for a
 * caller carrying no credential at all, so there is nothing here for a missing
 * cookie to fail on.
 *
 * It does read one thing: the backend address the proxy is configured with
 * (the same variable and the same fallback as app/api/pai/[...path]/route.ts),
 * and whether it was set at all, so the form can say which bot this device is
 * about to be paired to, and say so plainly when it is only the fallback.
 * Read here rather than fetched, because the only route that could answer is
 * gated and this page is the one place nobody is paired yet. The address is
 * not a secret (a loopback port), and NOTHING keyed is called: the bot's NAME
 * is shown only after pairing, in the dashboard header.
 */
export default function PairPage() {
  const configured = Boolean(process.env.PUFFPAI_BACKEND_URL);
  return (
    <PairForm
      backendUrl={process.env.PUFFPAI_BACKEND_URL ?? DEFAULT_BACKEND_URL}
      backendConfigured={configured}
    />
  );
}
