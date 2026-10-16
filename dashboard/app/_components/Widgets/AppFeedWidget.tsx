"use client";

/**
 * The "app-feed" dashboard widget: one installed app's live card, compact.
 *
 * It holds no address of its own - only an app id - and reads the card the
 * backend already fetched (GET /apps/<id>/feed through the paired proxy). The
 * credit line is always drawn. When the owner has not allowed the app's
 * outside service, the tile says so plainly instead of showing anything.
 */

import { useEffect, useState } from "react";
import FeedCardBody, { CreditLine } from "../Feed/FeedCardBody";
import { getFeed, type FeedState } from "@/app/_lib/services/feedService";
import { FEED_SOON_MS, feedPollMs } from "@/app/_lib/cardGrid";
import DesignTile, { NUNITO } from "./DesignTile";
import type { Widget } from "./WidgetBoard";

export default function AppFeedWidget({ widget }: { widget: Widget }) {
  const appId = String(widget.props?.app_id ?? "");
  const [state, setState] = useState<FeedState | null>(null);
  const [missing, setMissing] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let soon = 0;
    // Read again within seconds while a switched-on card has no data yet
    // (its first fetch is running on the box), then every five minutes.
    const load = () => {
      let got: FeedState | null = null;
      getFeed(appId)
        .then((s) => {
          got = s;
          if (cancelled) return;
          setState(s);
          setMissing(s === null);
        })
        .catch(() => {
          if (!cancelled) setMissing(true);
        })
        .finally(() => {
          if (cancelled) return;
          const wait = feedPollMs(got, soon);
          if (wait === FEED_SOON_MS) soon += 1;
          timer = setTimeout(load, wait);
        });
    };
    load();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [appId]);

  const note = { fontFamily: NUNITO, fontWeight: 500, fontSize: 13,
                 color: "var(--text3)", margin: 0 } as const;

  return (
    <DesignTile
      icon="rss_feed"
      iconColor="var(--sky)"
      title={String(widget.props?.title ?? state?.name ?? "Live")}
      href={`/dashboard/apps/${appId}`}
      bodyClassName="min-h-0 flex-1 overflow-y-auto"
    >
      {missing && <p style={note}>This live card is not available right now.</p>}
      {state && (state.card ? (
        <FeedCardBody appId={appId} card={state.card} compact />
      ) : (
        <p style={note}>{state.message}</p>
      ))}
      {state && <CreditLine credit={state.credit} />}
    </DesignTile>
  );
}
