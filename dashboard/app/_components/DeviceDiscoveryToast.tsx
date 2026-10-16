"use client";

/**
 * DeviceDiscoveryToast
 * ---------------------
 * Polls /devices/pending every 10 seconds. When a new device appears
 * (pending count increases), slides a toast in from the top-right
 * announcing the discovery. The toast offers a "Review" button that
 * links to the security / smart-home page, and auto-dismisses after
 * 15 seconds.
 *
 * Mount once at the dashboard layout level.
 */

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import Icon from "./Icon";
import {
  mdiWifiStar,
  mdiClose,
  mdiShieldAlertOutline,
} from "@mdi/js";
import { API_URL } from "../_lib/api";

interface PendingDevice {
  entity_id: string;
  domain: string;
  friendly_name: string;
  manufacturer?: string | null;
  model?: string | null;
  suggested_action?: string;
}

const POLL_MS = 10_000;
const AUTO_DISMISS_MS = 15_000;

export default function DeviceDiscoveryToast() {
  const [visibleDevice, setVisibleDevice] = useState<PendingDevice | null>(null);
  const seenIdsRef = useRef<Set<string>>(new Set());
  const bootstrappedRef = useRef<boolean>(false);
  const autoDismissRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    let cancelled = false;

    const pollOnce = async () => {
      try {
        // No Authorization header: the same-origin /api/pai proxy attaches
        // the backend key server-side, behind the pairing gate.
        const res = await fetch(`${API_URL}/devices/pending`, {
          method: "GET",
        });
        if (!res.ok) return;
        const data: { devices?: PendingDevice[] } = await res.json();
        const devices = data.devices || [];
        if (cancelled) return;

        // First poll: record all current devices WITHOUT notifying —
        // we only want to alert on NEW arrivals, not on whatever was
        // already sitting there when the page loaded.
        if (!bootstrappedRef.current) {
          devices.forEach((d) => seenIdsRef.current.add(d.entity_id));
          bootstrappedRef.current = true;
          return;
        }

        // Find first unseen device and show it
        const unseen = devices.find(
          (d) => !seenIdsRef.current.has(d.entity_id),
        );
        if (unseen) {
          seenIdsRef.current.add(unseen.entity_id);
          setVisibleDevice(unseen);
        }
      } catch {
        // network hiccup — try again next tick
      }
    };

    // First poll immediately, then on interval
    pollOnce();
    const id = setInterval(pollOnce, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  // Auto-dismiss timer whenever a new toast appears
  useEffect(() => {
    if (!visibleDevice) return;
    if (autoDismissRef.current) clearTimeout(autoDismissRef.current);
    autoDismissRef.current = setTimeout(() => {
      setVisibleDevice(null);
    }, AUTO_DISMISS_MS);
    return () => {
      if (autoDismissRef.current) clearTimeout(autoDismissRef.current);
    };
  }, [visibleDevice]);

  if (!visibleDevice) return null;

  const subtitle =
    visibleDevice.manufacturer && visibleDevice.model
      ? `${visibleDevice.manufacturer} · ${visibleDevice.model}`
      : visibleDevice.manufacturer || visibleDevice.model || visibleDevice.domain;

  return (
    <div
      className="fixed top-20 right-6 z-50 w-[380px] animate-slide-in-right"
      role="alert"
      aria-live="assertive"
    >
      <div className="rounded-lg border border-(--lemon)/50 bg-gradient-to-br from-(--puff-surface-deep) to-(--puff-surface-deep) shadow-2xl overflow-hidden">
        {/* Top band */}
        <div className="h-1 bg-gradient-to-r from-(--lemon) via-(--pink) to-(--lav)" />

        <div className="p-4">
          <div className="flex items-start gap-3">
            <div className="shrink-0 rounded-full bg-(--lemon)/20 p-2">
              <Icon path={mdiWifiStar} size="22" className="text-(--lemon)" />
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 text-[11px] font-bold uppercase tracking-wider text-(--lemon)">
                <Icon path={mdiShieldAlertOutline} size="14" />
                New Device On Your Network
              </div>
              <div className="mt-1 text-base font-semibold text-(--puff-text-strong) truncate">
                {visibleDevice.friendly_name}
              </div>
              <div className="text-xs text-(--text2) truncate">{subtitle}</div>
              <div className="mt-3 flex gap-2">
                <Link
                  href="/dashboard/homeassistant"
                  className="flex-1 text-center text-xs font-semibold bg-(--lemon) hover:bg-(--lemon) text-(--puff-on-accent) rounded px-3 py-1.5"
                  onClick={() => setVisibleDevice(null)}
                >
                  Review Device
                </Link>
                <button
                  className="text-xs font-semibold border border-(--line) hover:border-(--line) text-(--text) rounded px-3 py-1.5"
                  onClick={() => setVisibleDevice(null)}
                >
                  Dismiss
                </button>
              </div>
            </div>
            <button
              onClick={() => setVisibleDevice(null)}
              className="shrink-0 p-1 rounded hover:bg-(--puff-card-hover) text-(--text2)"
              aria-label="Close"
            >
              <Icon path={mdiClose} size="18" />
            </button>
          </div>
        </div>
      </div>

      <style jsx>{`
        @keyframes slide-in-right {
          from {
            transform: translateX(420px);
            opacity: 0;
          }
          to {
            transform: translateX(0);
            opacity: 1;
          }
        }
        .animate-slide-in-right {
          animation: slide-in-right 320ms cubic-bezier(0.16, 1, 0.3, 1);
        }
      `}</style>
    </div>
  );
}
