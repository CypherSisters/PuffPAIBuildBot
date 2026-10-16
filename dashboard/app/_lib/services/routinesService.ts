/**
 * Messages the bot sends on its own, and its one open offer.
 *
 * Backend routes live in src/puffpai/routines_api.py:
 *   GET  /announcements            recent messages (+ the speak-aloud switch)
 *   GET  /offers                   the one open offer, or null
 *   POST /offers/<id>/accept|decline
 *   POST /routines/speak           { on: boolean }
 *   GET  /routines/waiting         a routine run's questions (her yes to a command)
 *   GET  /routines                 the daily tasks (and the skills they can use)
 *   POST /routines/<id>/skill      { skill: string } the skill a daily task uses
 *
 * puff puff pass
 */

import { apiGet, apiPost } from "../api";

export interface Announcement {
  id: string;
  ts: string;
  text: string;
  title: string;
  source: string;
  /** True when this computer already said it out loud. */
  spoken?: boolean;
}

export interface AnnouncementFeed {
  announcements: Announcement[];
  speak_aloud: boolean;
  computer_speaks: boolean;
  voice_note: string;
}

export interface Offer {
  id: string;
  kind: string;
  question: string;
  detail: string;
  time: string;
}

export async function getAnnouncements(limit = 5): Promise<AnnouncementFeed> {
  return apiGet<AnnouncementFeed>(`/announcements?limit=${limit}`, { cache: "no-store" });
}

export async function getOffer(): Promise<Offer | null> {
  const res = await apiGet<{ offer: Offer | null }>("/offers", { cache: "no-store" });
  return res.offer ?? null;
}

export async function answerOffer(
  id: string,
  yes: boolean,
): Promise<{ ok: boolean; message: string }> {
  return apiPost(`/offers/${encodeURIComponent(id)}/${yes ? "accept" : "decline"}`, {});
}

/** A question a routine run is waiting on (answered with /chat/approve). */
export interface RoutineWaiting {
  id: string;
  title: string;
  command: string;
  folder: string;
  detail: string;
  routine: string;
}

export async function getRoutineWaiting(): Promise<RoutineWaiting[]> {
  const res = await apiGet<{ waiting: RoutineWaiting[] }>("/routines/waiting", { cache: "no-store" });
  return res.waiting ?? [];
}

export async function setSpeakAloud(on: boolean): Promise<AnnouncementFeed> {
  return apiPost<AnnouncementFeed>("/routines/speak", { on });
}

/** A daily task (the tool loop once a day) and the skill it uses. */
export interface DailyTask {
  id: string;
  label: string;
  when: string;
  on: boolean;
  prompt?: string;
  /** The skill folder it loads, or null for none. */
  skill?: string | null;
  /** Set when the last run left its skill out (it was not ready), in plain words. */
  skill_note?: string;
}

export interface SkillChoice {
  folder: string;
  name: string;
}

/** The daily tasks, and the installed skills one can use. */
export async function getDailyTasks(): Promise<{ tasks: DailyTask[]; skills: SkillChoice[] }> {
  const res = await apiGet<{ routines: (DailyTask & { kind: string })[]; skills?: SkillChoice[] }>(
    "/routines",
    { cache: "no-store" },
  );
  return {
    tasks: (res.routines ?? []).filter((r) => r.kind === "daily_prompt"),
    skills: res.skills ?? [],
  };
}

/** Pick the skill a daily task uses ("" for none). */
export async function setDailyTaskSkill(
  id: string,
  skill: string,
): Promise<{ ok: boolean; message?: string; routine?: DailyTask }> {
  return apiPost(`/routines/${encodeURIComponent(id)}/skill`, { skill });
}
