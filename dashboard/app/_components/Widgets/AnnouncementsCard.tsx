"use client";

/**
 * "From your bot": messages the bot sent on its own (a daily update, a watch,
 * a reminder), and its one open offer ("Would you like a daily garden update
 * at 8 am?") with Yes / No thanks.
 *
 * Renders nothing when there is no offer and no message, so a stock build's
 * home page does not grow an empty box.
 *
 * A daily task that needs to run a command waits here for her Yes or No
 * (any paired device may answer; no answer means it does not run).
 *
 * Each daily task is listed with the skill it uses, and she can pick another
 * skill (or none) right here.
 *
 * Speaking aloud: when the owner switches "Read updates out loud" on, a new
 * message that this computer did not already say (spoken: false) is read
 * through the same speak path the chat uses. Only messages that arrive while
 * the page is open are read; an old backlog is never read out on page load.
 *
 * puff puff pass
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  answerOffer,
  getAnnouncements,
  getDailyTasks,
  getOffer,
  getRoutineWaiting,
  setDailyTaskSkill,
  setSpeakAloud,
  type Announcement,
  type DailyTask,
  type Offer,
  type RoutineWaiting,
  type SkillChoice,
} from "@/app/_lib/services/routinesService";
import { approveAsk } from "@/app/_lib/services/chatService";
import { speak } from "@/app/_lib/services/voiceService";
import { pickToSpeak } from "@/app/_lib/services/announcementsSpeak";

const POLL_MS = 30_000;

function when(ts: string): string {
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return "";
  const time = d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  if (d.toDateString() === new Date().toDateString()) return `Today ${time}`;
  const day = d.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" });
  return `${day} ${time}`;
}

export default function AnnouncementsCard() {
  const [items, setItems] = useState<Announcement[]>([]);
  const [offer, setOffer] = useState<Offer | null>(null);
  const [speakOn, setSpeakOn] = useState(false);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [reply, setReply] = useState("");
  const [waiting, setWaiting] = useState<RoutineWaiting[]>([]);
  const [tasks, setTasks] = useState<DailyTask[]>([]);
  const [skills, setSkills] = useState<SkillChoice[]>([]);
  const seen = useRef<Set<string> | null>(null);

  const load = useCallback(async () => {
    try {
      const [feed, open, asks, daily] = await Promise.all([
        getAnnouncements(5),
        getOffer(),
        getRoutineWaiting().catch(() => [] as RoutineWaiting[]),
        getDailyTasks().catch(() => null),
      ]);
      setWaiting(asks);
      if (daily) {
        setTasks(daily.tasks);
        setSkills(daily.skills);
      }
      setItems(feed.announcements);
      setSpeakOn(feed.speak_aloud);
      setNote(feed.voice_note || "");
      setOffer(open);
      // First load only remembers; never read a backlog (see announcementsSpeak).
      const picked = pickToSpeak(seen.current, feed.announcements, feed.speak_aloud);
      seen.current = picked.seen;
      for (const a of picked.toSpeak) await speak(a.text);
    } catch {
      /* backend unreachable: keep what is shown */
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, [load]);

  const answer = async (yes: boolean) => {
    if (!offer) return;
    setBusy(true);
    try {
      const res = await answerOffer(offer.id, yes);
      setReply(res.message);
      setOffer(null);
    } catch (e) {
      setReply(e instanceof Error ? e.message : "That didn't work. Please try again.");
    } finally {
      setBusy(false);
    }
  };

  const toggleSpeak = async () => {
    try {
      const res = await setSpeakAloud(!speakOn);
      setSpeakOn(res.speak_aloud);
      setNote(res.voice_note || "");
    } catch {
      /* leave the switch where it was */
    }
  };

  const answerWaiting = async (id: string, yes: boolean) => {
    const ok = await approveAsk(id, yes);
    setWaiting((prev) => prev.filter((w) => w.id !== id));
    setReply(ok ? (yes ? "Going ahead." : "Okay, it was not run.") : "That question is no longer waiting.");
  };

  const pickSkill = async (id: string, skill: string) => {
    try {
      const res = await setDailyTaskSkill(id, skill);
      if (res.ok && res.routine) {
        setTasks((prev) => prev.map((t) => (t.id === id ? { ...t, skill: res.routine?.skill ?? null, skill_note: "" } : t)));
        setReply(skill ? `It now uses ${skill}.` : "It now runs without a skill.");
      } else {
        setReply(res.message || "That didn't work. Please try again.");
      }
    } catch (e) {
      setReply(e instanceof Error ? e.message : "That didn't work. Please try again.");
    }
  };

  if (!offer && items.length === 0 && !reply && waiting.length === 0 && tasks.length === 0) return null;

  return (
    <div
      className="mb-3 flex-none rounded-3xl border px-5 py-3"
      style={{ borderColor: "var(--line)", background: "var(--card)" }}
    >
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs font-semibold" style={{ color: "var(--text2)" }}>
          From your bot
        </p>
        <button
          type="button"
          onClick={toggleSpeak}
          className="text-[11px] underline-offset-2 hover:underline"
          style={{ color: "var(--text3)" }}
          aria-pressed={speakOn}
        >
          {speakOn ? "Reading updates out loud: on" : "Read updates out loud: off"}
        </button>
      </div>

      {offer && (
        <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
          <div className="min-w-0 flex-1">
            <p className="text-sm" style={{ color: "var(--text)" }}>{offer.question}</p>
            <p className="text-[11px]" style={{ color: "var(--text3)" }}>{offer.detail}</p>
          </div>
          <div className="flex flex-shrink-0 gap-2">
            <button
              type="button"
              disabled={busy}
              onClick={() => answer(true)}
              className="rounded-md border px-3 py-1 text-xs font-medium disabled:opacity-40"
              style={{ borderColor: "var(--mint)", color: "var(--mint)" }}
            >
              Yes
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => answer(false)}
              className="rounded-md border px-3 py-1 text-xs disabled:opacity-40"
              style={{ borderColor: "var(--line)", color: "var(--text3)" }}
            >
              No thanks
            </button>
          </div>
        </div>
      )}

      {waiting.map((w) => (
        <div key={w.id} className="mt-2 flex flex-wrap items-center justify-between gap-2">
          <div className="min-w-0 flex-1">
            <p className="text-sm" style={{ color: "var(--text)" }}>
              {w.routine ? `${w.routine}: ` : ""}
              {w.title}
            </p>
            {w.command && (
              <code className="block text-[11px]" style={{ color: "var(--text2)", wordBreak: "break-all" }}>
                {w.command}
              </code>
            )}
            {w.detail && <p className="text-[11px]" style={{ color: "var(--text3)" }}>{w.detail}</p>}
          </div>
          <div className="flex flex-shrink-0 gap-2">
            <button
              type="button"
              onClick={() => answerWaiting(w.id, true)}
              className="rounded-md border px-3 py-1 text-xs font-medium"
              style={{ borderColor: "var(--mint)", color: "var(--mint)" }}
            >
              Yes, go ahead
            </button>
            <button
              type="button"
              onClick={() => answerWaiting(w.id, false)}
              className="rounded-md border px-3 py-1 text-xs"
              style={{ borderColor: "var(--line)", color: "var(--text3)" }}
            >
              No
            </button>
          </div>
        </div>
      ))}

      {tasks.map((t) => (
        <div key={t.id} className="mt-2 flex flex-wrap items-center justify-between gap-2">
          <div className="min-w-0 flex-1">
            <p className="text-sm" style={{ color: "var(--text)" }}>
              {t.label}
              <span className="ml-2 text-[11px]" style={{ color: "var(--text3)" }}>
                {t.when}
                {t.on ? "" : " · off"}
              </span>
            </p>
            {t.prompt && (
              <p className="text-[11px]" style={{ color: "var(--text3)" }}>{t.prompt}</p>
            )}
            {t.skill_note && (
              <p className="text-[11px]" style={{ color: "var(--text2)" }}>{t.skill_note}</p>
            )}
          </div>
          <label className="flex flex-shrink-0 items-center gap-1.5 text-[11px]" style={{ color: "var(--text3)" }}>
            Uses
            <select
              value={t.skill ?? ""}
              onChange={(e) => pickSkill(t.id, e.target.value)}
              className="rounded-md border px-2 py-0.5 text-[11px]"
              style={{ borderColor: "var(--line)", background: "var(--card)", color: "var(--text2)" }}
            >
              <option value="">no skill</option>
              {t.skill && !skills.some((k) => k.folder === t.skill) && (
                <option value={t.skill}>{t.skill} (not installed)</option>
              )}
              {skills.map((k) => (
                <option key={k.folder} value={k.folder}>
                  {k.name}
                </option>
              ))}
            </select>
          </label>
        </div>
      ))}

      {reply && (
        <p className="mt-2 text-[11px]" style={{ color: "var(--text2)" }}>{reply}</p>
      )}

      {items.length > 0 && (
        <ul className="mt-2 space-y-1.5">
          {items.slice(0, 3).map((a) => (
            <li key={a.id} className="text-xs leading-snug" style={{ color: "var(--text)" }}>
              <span className="mr-2 text-[10px]" style={{ color: "var(--text3)" }}>
                {when(a.ts)}
                {a.title ? ` · ${a.title}` : ""}
              </span>
              {a.text}
            </li>
          ))}
        </ul>
      )}

      {speakOn && note && (
        <p className="mt-1.5 text-[10px]" style={{ color: "var(--text3)" }}>{note}</p>
      )}
    </div>
  );
}
