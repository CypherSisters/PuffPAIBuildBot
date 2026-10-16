/**
 * The bot's home town: the one place it learns where "here" is, for the
 * weather card and for updates that run at a local time.
 *
 * Only the town name is sent anywhere, once, to the town lookup
 * (geocoding-api.open-meteo.com), and only after the owner said yes to that
 * service on this device (the backend's one-time consent ticket). What is kept
 * is the town, a rough position (about 11 km) and the time zone.
 */

const API_URL = "/api/pai";

export type HomeView = {
  home: { label: string; latitude: number; longitude: number; timezone: string } | null;
  what_is_stored: string;
  town_lookup_allowed: boolean;
  town_lookup_note: string;
  ok?: boolean;
  message?: string;
  other_matches?: string[];
};

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    method,
    headers: { "Content-Type": "application/json" },
    cache: "no-store",
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const message =
      (data && ((data.message as string) || (data.error as string))) ||
      "That did not work.";
    throw new Error(message);
  }
  return data as T;
}

/** Save the town. `ticket` is the owner's one-time yes to the town lookup,
 *  needed only while that lookup is still switched off. */
export function setHomeTown(town: string, ticket?: string): Promise<HomeView> {
  const clean = town.trim().slice(0, 100);
  return call<HomeView>(
    "POST",
    "/home",
    ticket ? { town: clean, allow_lookup: true, ticket } : { town: clean },
  );
}
