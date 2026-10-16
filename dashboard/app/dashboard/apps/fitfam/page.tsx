"use client";

import { useState, useMemo } from "react";
import {
  mdiRunFast,
  mdiDumbbell,
  mdiFire,
  mdiTrophy,
  mdiPlus,
  mdiCheck,
  mdiClose,
  mdiAlertCircleOutline,
  mdiChartLine,
  mdiClockOutline,
  mdiStar,
  mdiOpenInNew,
  mdiWeightLifter,
  mdiClipboardListOutline,
} from "@mdi/js";
import Button from "@/app/_components/Button";
import Icon from "@/app/_components/Icon";
import { useAppData } from "@/app/_lib/hooks/useAppData";
import { AppPage, Panel, SectionBar } from "../_components/ui";
import {
  EXERCISE_DB,
  TEMPLATES,
  estimate1RM,
  weightForReps,
  rpeToRir,
  VOLUME_LANDMARKS,
  getExercise,
  type Exercise,
  type MuscleGroup,
} from "@/app/_lib/fitnessScience";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

type WorkoutEntry = {
  date: string;
  type: string;
  duration: number;
  notes: string;
};

type SetEntry = {
  id: string;
  date: string;
  exerciseId: string;
  weight: number;
  reps: number;
  rpe: number | null;
  notes: string;
};

type UserPrefs = {
  level: string;
  type: string;
  duration: number;
};

type FitUser = {
  name: string;
  age: number;
  emoji: string;
  prefs: UserPrefs;
  goals: string[];
  history: WorkoutEntry[];
  sets?: SetEntry[];
  streak: number;
  totalWorkouts: number;
};

type FitFamData = {
  users: Record<string, FitUser>;
};

/* ------------------------------------------------------------------ */
/*  Constants                                                          */
/* ------------------------------------------------------------------ */

const WORKOUT_TYPES: { value: string; label: string; emoji: string }[] = [
  { value: "calisthenics", label: "Calisthenics", emoji: "\u{1F938}" },
  { value: "kettlebell", label: "Kettlebell", emoji: "\u{1F3CB}\u{FE0F}" },
  { value: "rom", label: "ROM / Mobility", emoji: "\u{1F9D8}" },
  { value: "gym", label: "Gym", emoji: "\u{1F4AA}" },
  { value: "running", label: "Running", emoji: "\u{1F3C3}" },
];

const LEVEL_COLORS: Record<string, string> = {
  easy: "bg-[color-mix(in_srgb,var(--mint)_16%,transparent)] text-[var(--mint)]",
  mid: "bg-[color-mix(in_srgb,var(--lemon)_16%,transparent)] text-[var(--lemon)]",
  hard: "bg-[color-mix(in_srgb,var(--puff-red)_16%,transparent)] text-[var(--puff-red)]",
};

function workoutEmoji(type: string): string {
  return WORKOUT_TYPES.find((w) => w.value === type)?.emoji ?? "\u{1F3CB}\u{FE0F}";
}

function workoutLabel(type: string): string {
  return WORKOUT_TYPES.find((w) => w.value === type)?.label ?? type;
}

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
    });
  } catch {
    return iso;
  }
}

/* ------------------------------------------------------------------ */
/*  Page                                                               */
/* ------------------------------------------------------------------ */

export default function FitFamPage() {
  const { data, loading, error, runAction } =
    useAppData<FitFamData>("fitfam");

  const userKeys = useMemo(
    () => (data ? Object.keys(data.users) : []),
    [data],
  );
  const [selectedUser, setSelectedUser] = useState<string>("");

  /* Log form state */
  const [wType, setWType] = useState(WORKOUT_TYPES[0].value);
  const [wDuration, setWDuration] = useState(30);
  const [wNotes, setWNotes] = useState("");
  const [showLogForm, setShowLogForm] = useState(false);

  /* Tab state — Overview / Strength / Templates */
  const [tab, setTab] = useState<"overview" | "strength" | "templates">(
    "overview",
  );

  /* Strength logger state */
  const [strengthExerciseId, setStrengthExerciseId] = useState<string>(
    EXERCISE_DB[0].id,
  );
  const [strengthWeight, setStrengthWeight] = useState<number>(135);
  const [strengthReps, setStrengthReps] = useState<number>(5);
  const [strengthRpe, setStrengthRpe] = useState<number>(8);
  const [strengthNotes, setStrengthNotes] = useState<string>("");

  /* Auto-select first user */
  const activeKey = selectedUser || userKeys[0] || "";
  const user: FitUser | null = data?.users[activeKey] ?? null;

  const recentHistory = useMemo(() => {
    if (!user) return [];
    return [...user.history]
      .sort((a, b) => new Date(b.date).getTime() - new Date(a.date).getTime())
      .slice(0, 14);
  }, [user]);

  /* ---- handlers ---- */
  async function logWorkout() {
    if (!activeKey) return;
    await runAction("fit_log_workout", {
      user: activeKey,
      workout_type: wType,
      duration: wDuration,
      notes: wNotes,
    });
    setWNotes("");
    setShowLogForm(false);
  }

  async function logSet() {
    if (!activeKey || !strengthExerciseId) return;
    await runAction("fit_log_set", {
      user: activeKey,
      exercise_id: strengthExerciseId,
      weight: strengthWeight,
      reps: strengthReps,
      rpe: strengthRpe || null,
      notes: strengthNotes,
    });
    setStrengthNotes("");
  }

  /* ---- derived: science-backed analytics ---- */

  // Personal records (best estimated 1RM per exercise)
  const personalRecords = useMemo(() => {
    if (!user?.sets) return {};
    const records: Record<string, { weight: number; reps: number; date: string; est1RM: number }> = {};
    for (const s of user.sets) {
      if (!s.weight || !s.reps) continue;
      const est = estimate1RM(s.weight, s.reps);
      if (!records[s.exerciseId] || est > records[s.exerciseId].est1RM) {
        records[s.exerciseId] = {
          weight: s.weight,
          reps: s.reps,
          date: s.date,
          est1RM: est,
        };
      }
    }
    return records;
  }, [user]);

  // Weekly volume per muscle group (sets in past 7 days)
  const weeklyVolume = useMemo(() => {
    if (!user?.sets) return {};
    const cutoff = Date.now() - 7 * 24 * 60 * 60 * 1000;
    const volume: Record<string, number> = {};
    for (const s of user.sets) {
      try {
        if (new Date(s.date).getTime() < cutoff) continue;
      } catch {
        continue;
      }
      const ex = getExercise(s.exerciseId);
      if (!ex) continue;
      // Each set counts 1.0 toward primary muscles, 0.5 toward secondary
      for (const m of ex.primary) volume[m] = (volume[m] || 0) + 1;
      for (const m of ex.secondary) volume[m] = (volume[m] || 0) + 0.5;
    }
    return volume;
  }, [user]);

  // Recent sets (last 10 across all exercises)
  const recentSets = useMemo(() => {
    if (!user?.sets) return [];
    return [...user.sets]
      .sort((a, b) => new Date(b.date).getTime() - new Date(a.date).getTime())
      .slice(0, 10);
  }, [user]);

  // Sets for the currently selected exercise (for trend display)
  const exerciseHistory = useMemo(() => {
    if (!user?.sets) return [];
    return user.sets
      .filter((s) => s.exerciseId === strengthExerciseId)
      .sort((a, b) => new Date(b.date).getTime() - new Date(a.date).getTime())
      .slice(0, 5);
  }, [user, strengthExerciseId]);

  /* ---- loading / error ---- */
  if (loading) {
    return (
      <AppPage>
        <div className="flex items-center justify-center py-20">
          <div className="h-10 w-10 animate-spin rounded-full border-4 border-[var(--mint)] border-t-transparent" />
        </div>
      </AppPage>
    );
  }

  if (error) {
    return (
      <AppPage>
        <Panel accent="--puff-red">
          <div className="flex items-center gap-2">
            <Icon path={mdiAlertCircleOutline} size="20" />
            <span>{error}</span>
          </div>
        </Panel>
      </AppPage>
    );
  }

  return (
    <AppPage>
      {/* ---- Hero ---- */}
      <div className="bokeh-container relative mb-6 overflow-hidden rounded-2xl bg-gradient-to-r from-[var(--magenta)] via-[var(--pink)] to-[color-mix(in_srgb,var(--pink)_55%,white)] p-6 text-(--puff-on-accent) shadow-lg">
        <div className="bokeh-bubble bokeh-bubble-1" />
        <div className="bokeh-bubble bokeh-bubble-2" />
        <div className="bokeh-bubble bokeh-bubble-3" />
        <div className="pointer-events-none absolute inset-x-0 top-0 h-1/2 rounded-t-2xl bg-gradient-to-b from-(--card)/20 to-transparent" />
        <div className="relative z-10 flex items-center gap-4">
          <div className="flex h-16 w-16 items-center justify-center rounded-full border-2 border-(--line) bg-[var(--card2)] text-3xl shadow-lg backdrop-blur-sm">
            {"\u{1F4AA}"}
          </div>
          <div className="flex-1">
            <h1 className="text-2xl font-bold drop-shadow-sm">FitFam</h1>
            <p className="text-sm text-(--puff-on-accent)/80">
              Family Fitness Tracker
            </p>
          </div>
          <Button
            icon={mdiOpenInNew}
            label="Start Workout"
            color="white"
            small
            roundedFull
            href="/dashboard/apps/fitfam/play"
          />
        </div>
      </div>

      {/* ---- Family Member Tabs ---- */}
      <div className="mb-6 flex gap-2">
        {userKeys.map((key) => {
          const u = data!.users[key];
          return (
            <button
              key={key}
              onClick={() => setSelectedUser(key)}
              className={`flex items-center gap-2 rounded-full px-4 py-2 text-sm font-semibold transition ${
                activeKey === key
                  ? "bg-[var(--mint)] text-(--puff-on-accent) shadow-md"
                  : "bg-[var(--card)] text-[var(--text2)] hover:bg-[var(--chip)]"
              }`}
            >
              <span className="text-lg">{u.emoji}</span>
              {u.name}
            </button>
          );
        })}
      </div>

      {!user ? (
        <Panel className="text-center text-[var(--text3)]">
          <div className="py-8">
            <Icon
              path={mdiRunFast}
              size="48"
              className="mx-auto mb-2 opacity-30"
            />
            <p>No family members found. Add members via PuffPAI chat.</p>
          </div>
        </Panel>
      ) : (
        <>
          {/* ============================================================ */}
          {/*  TAB BAR                                                       */}
          {/* ============================================================ */}
          <div className="mb-6 flex flex-wrap gap-2">
            <Button
              icon={mdiChartLine}
              label="Overview"
              color={tab === "overview" ? "success" : "whiteDark"}
              roundedFull
              small
              onClick={() => setTab("overview")}
            />
            <Button
              icon={mdiWeightLifter}
              label="Strength"
              color={tab === "strength" ? "success" : "whiteDark"}
              roundedFull
              small
              onClick={() => setTab("strength")}
            />
            <Button
              icon={mdiClipboardListOutline}
              label="Templates"
              color={tab === "templates" ? "success" : "whiteDark"}
              roundedFull
              small
              onClick={() => setTab("templates")}
            />
          </div>

          {tab === "overview" && (
          <>
          {/* ============================================================ */}
          {/*  STATS OVERVIEW                                               */}
          {/* ============================================================ */}
          <SectionBar title="Stats">
            <Button
              icon={mdiPlus}
              label="Log Workout"
              color="success"
              small
              roundedFull
              onClick={() => setShowLogForm((v) => !v)}
            />
          </SectionBar>

          <div className="mb-6 grid grid-cols-2 gap-4 lg:grid-cols-4">
            {/* Streak */}
            <Panel hoverable>
              <div className="flex items-center gap-3">
                <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-[color-mix(in_srgb,var(--lemon)_16%,transparent)]">
                  <Icon path={mdiFire} size="24" className="text-[var(--lemon)]" />
                </div>
                <div>
                  <p className="text-2xl font-bold text-[var(--text)]">
                    {user.streak}
                  </p>
                  <p className="text-xs text-[var(--text2)]">
                    Day Streak
                  </p>
                </div>
              </div>
            </Panel>

            {/* Total Workouts */}
            <Panel hoverable>
              <div className="flex items-center gap-3">
                <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-[color-mix(in_srgb,var(--mint)_16%,transparent)]">
                  <Icon
                    path={mdiTrophy}
                    size="24"
                    className="text-[var(--mint)]"
                  />
                </div>
                <div>
                  <p className="text-2xl font-bold text-[var(--text)]">
                    {user.totalWorkouts}
                  </p>
                  <p className="text-xs text-[var(--text2)]">
                    Total Workouts
                  </p>
                </div>
              </div>
            </Panel>

            {/* Preferred Type */}
            <Panel hoverable>
              <div className="flex items-center gap-3">
                <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-[color-mix(in_srgb,var(--sky)_16%,transparent)]">
                  <Icon
                    path={mdiDumbbell}
                    size="24"
                    className="text-[var(--sky)]"
                  />
                </div>
                <div>
                  <p className="text-sm font-bold text-[var(--text)]">
                    {workoutLabel(user.prefs.type)}
                  </p>
                  <p className="text-xs text-[var(--text2)]">
                    Preferred
                  </p>
                </div>
              </div>
            </Panel>

            {/* Level */}
            <Panel hoverable>
              <div className="flex items-center gap-3">
                <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-[color-mix(in_srgb,var(--magenta)_16%,transparent)]">
                  <Icon
                    path={mdiStar}
                    size="24"
                    className="text-[var(--magenta)]"
                  />
                </div>
                <div>
                  <span
                    className={`inline-block rounded-full px-2 py-0.5 text-xs font-bold uppercase ${
                      LEVEL_COLORS[user.prefs.level] ?? LEVEL_COLORS.mid
                    }`}
                  >
                    {user.prefs.level}
                  </span>
                  <p className="mt-0.5 text-xs text-[var(--text2)]">
                    Level
                  </p>
                </div>
              </div>
            </Panel>
          </div>

          {/* ============================================================ */}
          {/*  LOG WORKOUT FORM                                             */}
          {/* ============================================================ */}
          {showLogForm && (
            <Panel className="mb-6">
              <h3 className="mb-3 text-lg font-semibold text-[var(--text)]">
                Log Workout for {user.name}
              </h3>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                <select
                  value={wType}
                  onChange={(e) => setWType(e.target.value)}
                  className="rounded-lg border border-[var(--line)] bg-[var(--card)] px-3 py-2 text-sm focus:border-[var(--mint)] focus:outline-none focus:ring-2 focus:ring-[var(--mint)]/30"
                >
                  {WORKOUT_TYPES.map((w) => (
                    <option key={w.value} value={w.value}>
                      {w.emoji} {w.label}
                    </option>
                  ))}
                </select>

                {/* Duration */}
                <div>
                  <div className="flex items-center gap-2">
                    <input
                      type="range"
                      min={5}
                      max={120}
                      step={5}
                      value={wDuration}
                      onChange={(e) => setWDuration(Number(e.target.value))}
                      className="flex-1 accent-[var(--mint)]"
                    />
                    <span className="w-16 text-right text-sm font-semibold text-[var(--text)]">
                      {wDuration} min
                    </span>
                  </div>
                </div>

                <input
                  type="text"
                  placeholder="Notes (optional)"
                  value={wNotes}
                  onChange={(e) => setWNotes(e.target.value)}
                  className="rounded-lg border border-[var(--line)] bg-[var(--card)] px-3 py-2 text-sm focus:border-[var(--mint)] focus:outline-none focus:ring-2 focus:ring-[var(--mint)]/30"
                />
              </div>
              <div className="mt-3 flex gap-2">
                <Button
                  icon={mdiCheck}
                  label="Log"
                  color="success"
                  small
                  roundedFull
                  onClick={logWorkout}
                />
                <Button
                  icon={mdiClose}
                  label="Cancel"
                  color="whiteDark"
                  small
                  roundedFull
                  onClick={() => setShowLogForm(false)}
                />
              </div>
            </Panel>
          )}

          {/* ============================================================ */}
          {/*  WORKOUT HISTORY                                              */}
          {/* ============================================================ */}
          <SectionBar title="Recent Workouts">
            <span className="text-sm text-[var(--text3)]">
              Last {recentHistory.length} sessions
            </span>
          </SectionBar>

          {recentHistory.length === 0 ? (
            <Panel className="text-center text-[var(--text3)]">
              <div className="py-8">
                <Icon
                  path={mdiDumbbell}
                  size="48"
                  className="mx-auto mb-2 opacity-30"
                />
                <p>No workouts logged yet. Start your journey!</p>
              </div>
            </Panel>
          ) : (
            <>
              {/* Heatmap-style grid (last 14 days) */}
              <div className="mb-4 flex flex-wrap gap-1.5">
                {recentHistory.map((entry, idx) => (
                  <div
                    key={idx}
                    title={`${formatDate(entry.date)} - ${workoutLabel(entry.type)} (${entry.duration}min)`}
                    className="group relative flex h-10 w-10 items-center justify-center rounded-lg bg-[var(--mint)]/20 text-sm transition hover:scale-110 hover:bg-[var(--mint)]/40/10/30"
                    style={{
                      opacity: Math.max(0.4, 1 - idx * 0.05),
                    }}
                  >
                    <span className="text-base">
                      {workoutEmoji(entry.type)}
                    </span>
                  </div>
                ))}
              </div>

              {/* Detail list */}
              <Panel>
                <div className="divide-y divide-[var(--line)]">
                  {recentHistory.map((entry, idx) => (
                    <div
                      key={idx}
                      className="flex items-center gap-3 py-3"
                    >
                      <div className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-xl bg-[var(--mint)]/10 text-xl">
                        {workoutEmoji(entry.type)}
                      </div>
                      <div className="min-w-0 flex-1">
                        <p className="font-medium text-[var(--text)]">
                          {workoutLabel(entry.type)}
                        </p>
                        {entry.notes && (
                          <p className="truncate text-xs text-[var(--text3)]">
                            {entry.notes}
                          </p>
                        )}
                      </div>
                      <div className="flex items-center gap-3 text-sm text-[var(--text2)]">
                        <span className="flex items-center gap-1">
                          <Icon path={mdiClockOutline} size="14" />
                          {entry.duration}m
                        </span>
                        <span className="whitespace-nowrap text-xs">
                          {formatDate(entry.date)}
                        </span>
                      </div>
                    </div>
                  ))}
                </div>
              </Panel>
            </>
          )}
          </>
          )}

          {tab === "strength" && (
            <StrengthTab
              user={user}
              recentSets={recentSets}
              personalRecords={personalRecords}
              weeklyVolume={weeklyVolume}
              exerciseHistory={exerciseHistory}
              strengthExerciseId={strengthExerciseId}
              setStrengthExerciseId={setStrengthExerciseId}
              strengthWeight={strengthWeight}
              setStrengthWeight={setStrengthWeight}
              strengthReps={strengthReps}
              setStrengthReps={setStrengthReps}
              strengthRpe={strengthRpe}
              setStrengthRpe={setStrengthRpe}
              strengthNotes={strengthNotes}
              setStrengthNotes={setStrengthNotes}
              logSet={logSet}
            />
          )}

          {tab === "templates" && <TemplatesTab />}
        </>
      )}
    </AppPage>
  );
}

/* ------------------------------------------------------------------ */
/*  Strength Tab                                                       */
/* ------------------------------------------------------------------ */

interface StrengthTabProps {
  user: FitUser;
  recentSets: SetEntry[];
  personalRecords: Record<string, { weight: number; reps: number; date: string; est1RM: number }>;
  weeklyVolume: Record<string, number>;
  exerciseHistory: SetEntry[];
  strengthExerciseId: string;
  setStrengthExerciseId: (id: string) => void;
  strengthWeight: number;
  setStrengthWeight: (n: number) => void;
  strengthReps: number;
  setStrengthReps: (n: number) => void;
  strengthRpe: number;
  setStrengthRpe: (n: number) => void;
  strengthNotes: string;
  setStrengthNotes: (s: string) => void;
  logSet: () => Promise<void>;
}

function StrengthTab(props: StrengthTabProps) {
  const {
    user,
    recentSets,
    personalRecords,
    weeklyVolume,
    exerciseHistory,
    strengthExerciseId,
    setStrengthExerciseId,
    strengthWeight,
    setStrengthWeight,
    strengthReps,
    setStrengthReps,
    strengthRpe,
    setStrengthRpe,
    strengthNotes,
    setStrengthNotes,
    logSet,
  } = props;

  const selectedExercise = getExercise(strengthExerciseId);
  const lastSet = exerciseHistory[0];
  const estimated1RM =
    strengthWeight && strengthReps
      ? estimate1RM(strengthWeight, strengthReps)
      : 0;
  const rir = strengthRpe ? rpeToRir(strengthRpe) : null;

  // Group exercises by pattern for the dropdown
  const exercisesByPattern = useMemo(() => {
    const groups: Record<string, Exercise[]> = {};
    for (const ex of EXERCISE_DB) {
      if (!groups[ex.pattern]) groups[ex.pattern] = [];
      groups[ex.pattern].push(ex);
    }
    return groups;
  }, []);

  return (
    <>
      <SectionBar title="Strength Training" />

      {/* Set Logger */}
      <Panel className="mb-6">
        <div className="p-4">
          <h3 className="mb-3 text-lg font-semibold text-[var(--text)]">
            Log a Set for {user.name}
          </h3>
          <div className="grid gap-3 lg:grid-cols-5">
            {/* Exercise picker */}
            <div className="lg:col-span-2">
              <label className="mb-1 block text-xs font-medium text-[var(--text2)]">
                Exercise
              </label>
              <select
                value={strengthExerciseId}
                onChange={(e) => setStrengthExerciseId(e.target.value)}
                className="w-full rounded-lg border border-[var(--line)] bg-[var(--card)] px-3 py-2 text-sm focus:border-[var(--mint)] focus:outline-none focus:ring-2 focus:ring-[var(--mint)]/30"
              >
                {Object.entries(exercisesByPattern).map(([pattern, exs]) => (
                  <optgroup key={pattern} label={pattern.replace("-", " ")}>
                    {exs.map((ex) => (
                      <option key={ex.id} value={ex.id}>
                        {ex.name} ({ex.equipment})
                      </option>
                    ))}
                  </optgroup>
                ))}
              </select>
            </div>

            {/* Weight */}
            <div>
              <label className="mb-1 block text-xs font-medium text-[var(--text2)]">
                Weight (lb)
              </label>
              <input
                type="number"
                value={strengthWeight}
                onChange={(e) => setStrengthWeight(Number(e.target.value))}
                step={2.5}
                className="w-full rounded-lg border border-[var(--line)] bg-[var(--card)] px-3 py-2 text-sm"
              />
            </div>

            {/* Reps */}
            <div>
              <label className="mb-1 block text-xs font-medium text-[var(--text2)]">
                Reps
              </label>
              <input
                type="number"
                value={strengthReps}
                onChange={(e) => setStrengthReps(Number(e.target.value))}
                min={1}
                className="w-full rounded-lg border border-[var(--line)] bg-[var(--card)] px-3 py-2 text-sm"
              />
            </div>

            {/* RPE */}
            <div>
              <label className="mb-1 block text-xs font-medium text-[var(--text2)]">
                RPE (1-10)
              </label>
              <input
                type="number"
                value={strengthRpe}
                onChange={(e) => setStrengthRpe(Number(e.target.value))}
                step={0.5}
                min={1}
                max={10}
                className="w-full rounded-lg border border-[var(--line)] bg-[var(--card)] px-3 py-2 text-sm"
              />
              {rir !== null && (
                <p className="mt-1 text-[10px] text-[var(--text2)]">
                  ≈ {rir} reps in reserve
                </p>
              )}
            </div>
          </div>

          {/* Notes + Log button */}
          <div className="mt-3 flex flex-col gap-2 sm:flex-row">
            <input
              type="text"
              placeholder="Notes (optional: form cue, soreness, etc.)"
              value={strengthNotes}
              onChange={(e) => setStrengthNotes(e.target.value)}
              className="flex-1 rounded-lg border border-[var(--line)] bg-[var(--card)] px-3 py-2 text-sm"
            />
            <Button
              icon={mdiCheck}
              label="Log Set"
              color="success"
              onClick={logSet}
            />
          </div>

          {/* Live feedback */}
          {estimated1RM > 0 && (
            <div className="mt-4 rounded-lg border border-[color-mix(in_srgb,var(--mint)_40%,transparent)] bg-[color-mix(in_srgb,var(--mint)_16%,transparent)] p-3 text-sm">
              <p className="text-[var(--mint)]">
                <span className="font-semibold">Estimated 1-rep max:</span>{" "}
                {estimated1RM} lb
                {personalRecords[strengthExerciseId] && (
                  <>
                    {" · "}
                    {estimated1RM > personalRecords[strengthExerciseId].est1RM ? (
                      <span className="font-bold">NEW PR!</span>
                    ) : (
                      <>
                        current PR: {personalRecords[strengthExerciseId].est1RM} lb
                      </>
                    )}
                  </>
                )}
              </p>
              <p className="mt-1 text-xs text-[var(--mint)]">
                Suggested rep ranges:{" "}
                <strong>{weightForReps(estimated1RM, 5)}lb x 5</strong>{" "}
                (strength) ·{" "}
                <strong>{weightForReps(estimated1RM, 8)}lb x 8</strong>{" "}
                (hypertrophy) ·{" "}
                <strong>{weightForReps(estimated1RM, 12)}lb x 12</strong>{" "}
                (endurance)
              </p>
            </div>
          )}

          {/* Form cues for selected exercise */}
          {selectedExercise && (
            <div className="mt-4 rounded-lg border border-[color-mix(in_srgb,var(--sky)_40%,transparent)] bg-[color-mix(in_srgb,var(--sky)_16%,transparent)] p-3">
              <p className="text-xs font-semibold uppercase text-[var(--sky)]">
                Form Cues: {selectedExercise.name}
              </p>
              <ul className="mt-1 space-y-0.5 text-xs text-[var(--sky)]">
                {selectedExercise.formCues.map((cue, i) => (
                  <li key={i}>• {cue}</li>
                ))}
              </ul>
              {selectedExercise.safety && (
                <p className="mt-2 text-xs italic text-[var(--lemon)]">
                  ⚠ {selectedExercise.safety}
                </p>
              )}
            </div>
          )}

          {/* Last 5 sets of this exercise */}
          {exerciseHistory.length > 0 && (
            <div className="mt-4">
              <p className="mb-2 text-xs font-semibold uppercase text-[var(--text2)]">
                Last 5 sets of {selectedExercise?.name}
              </p>
              <div className="flex flex-wrap gap-2">
                {exerciseHistory.map((s) => (
                  <span
                    key={s.id}
                    className="rounded-md bg-[var(--chip)] px-2 py-1 text-xs text-[var(--text2)]"
                  >
                    {s.weight}×{s.reps}
                    {s.rpe ? ` @${s.rpe}` : ""}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      </Panel>

      {/* Personal Records */}
      <SectionBar title="Personal Records" />
      {Object.keys(personalRecords).length === 0 ? (
        <Panel className="text-center text-[var(--text3)]">
          <p className="py-6">
            No PRs yet. Log your first set above to get started.
          </p>
        </Panel>
      ) : (
        <Panel className="mb-6">
          <div className="divide-y divide-[var(--line)]">
            {Object.entries(personalRecords)
              .sort((a, b) => b[1].est1RM - a[1].est1RM)
              .map(([exId, pr]) => {
                const ex = getExercise(exId);
                return (
                  <div key={exId} className="flex items-center justify-between py-3">
                    <div>
                      <p className="font-medium text-[var(--text)]">
                        {ex?.name || exId}
                      </p>
                      <p className="text-xs text-[var(--text2)]">
                        {pr.weight}lb × {pr.reps} reps
                      </p>
                    </div>
                    <div className="text-right">
                      <p className="text-lg font-bold text-[var(--mint)]">
                        {pr.est1RM}lb
                      </p>
                      <p className="text-[10px] text-[var(--text3)]">
                        est. 1RM
                      </p>
                    </div>
                  </div>
                );
              })}
          </div>
        </Panel>
      )}

      {/* Weekly Volume by Muscle Group */}
      <SectionBar title="Weekly Volume" />
      <Panel className="mb-6">
        <div className="p-4">
          <p className="mb-3 text-xs text-[var(--text2)]">
            Working sets per muscle group over the last 7 days. Green bars are
            in the optimal range (MEV–MAV).
          </p>
          <div className="space-y-2">
            {(Object.keys(VOLUME_LANDMARKS) as MuscleGroup[]).map((muscle) => {
              const sets = weeklyVolume[muscle] || 0;
              const lm = VOLUME_LANDMARKS[muscle];
              const pct = Math.min(100, (sets / lm.mrv) * 100);
              const inRange = sets >= lm.mev && sets <= lm.mav;
              const overReached = sets > lm.mav && sets <= lm.mrv;
              const overTrained = sets > lm.mrv;
              const color = overTrained
                ? "bg-[var(--puff-red)]"
                : overReached
                ? "bg-[color-mix(in_srgb,var(--lemon)_16%,transparent)]"
                : inRange
                ? "bg-[var(--mint)]"
                : sets > 0
                ? "bg-[color-mix(in_srgb,var(--sky)_16%,transparent)]"
                : "bg-[var(--chip)]";
              return (
                <div key={muscle}>
                  <div className="mb-1 flex items-center justify-between text-xs">
                    <span className="font-medium capitalize text-[var(--text)]">
                      {muscle}
                    </span>
                    <span className="font-mono text-[var(--text2)]">
                      {sets.toFixed(1)} / {lm.mav}-{lm.mrv} sets
                    </span>
                  </div>
                  <div className="h-2 w-full overflow-hidden rounded-full bg-[var(--chip)]">
                    <div
                      className={`h-full ${color} transition-all`}
                      style={{ width: `${pct}%` }}
                    />
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </Panel>

      {/* Recent Sets */}
      <SectionBar title="Recent Sets" />
      {recentSets.length === 0 ? (
        <Panel className="text-center text-[var(--text3)]">
          <p className="py-6">No sets logged yet.</p>
        </Panel>
      ) : (
        <Panel>
          <div className="divide-y divide-[var(--line)]">
            {recentSets.map((s) => {
              const ex = getExercise(s.exerciseId);
              return (
                <div key={s.id} className="flex items-center justify-between py-2">
                  <div>
                    <p className="text-sm font-medium text-[var(--text)]">
                      {ex?.name || s.exerciseId}
                    </p>
                    <p className="text-[11px] text-[var(--text3)]">
                      {new Date(s.date).toLocaleString()}
                    </p>
                  </div>
                  <div className="text-right text-sm">
                    <span className="font-semibold text-[var(--text)]">
                      {s.weight}lb × {s.reps}
                    </span>
                    {s.rpe && (
                      <span className="ml-2 rounded-md bg-[color-mix(in_srgb,var(--lemon)_16%,transparent)] px-1.5 py-0.5 text-[10px] font-bold text-[var(--lemon)]">
                        RPE {s.rpe}
                      </span>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </Panel>
      )}
    </>
  );
}

/* ------------------------------------------------------------------ */
/*  Templates Tab                                                      */
/* ------------------------------------------------------------------ */

function TemplatesTab() {
  const [openTemplate, setOpenTemplate] = useState<string | null>(null);

  return (
    <>
      <SectionBar title="Workout Templates" />
      <p className="mb-4 text-sm text-[var(--text2)]">
        Evidence-based programs you can run as-is. Pick the one that matches
        your weekly availability and goal.
      </p>
      <div className="grid gap-4 md:grid-cols-2">
        {TEMPLATES.map((t) => {
          const isOpen = openTemplate === t.id;
          return (
            <Panel key={t.id} hoverable>
              <div className="p-4">
                <div className="mb-2 flex items-start justify-between">
                  <div>
                    <h3 className="text-base font-bold text-[var(--text)]">
                      {t.name}
                    </h3>
                    <p className="mt-0.5 text-xs uppercase tracking-wide text-[var(--text2)]">
                      {t.level} · {t.daysPerWeek} days/week
                    </p>
                  </div>
                </div>
                <p className="mb-3 text-sm text-[var(--text2)]">
                  {t.description}
                </p>
                <button
                  onClick={() => setOpenTemplate(isOpen ? null : t.id)}
                  className="text-xs font-semibold text-[var(--mint)] hover:text-[var(--mint)]"
                >
                  {isOpen ? "Hide details" : "View workout days →"}
                </button>
                {isOpen && (
                  <div className="mt-3 space-y-3 border-t border-[var(--line)] pt-3">
                    {t.days.map((day) => (
                      <div key={day.name}>
                        <p className="text-sm font-semibold text-[var(--text)]">
                          {day.name}
                          {day.targetSets && (
                            <span className="ml-2 text-xs font-normal text-[var(--text2)]">
                              {day.targetSets} sets ×{" "}
                              {day.targetRepsLow === day.targetRepsHigh
                                ? day.targetRepsLow
                                : `${day.targetRepsLow}-${day.targetRepsHigh}`}{" "}
                              reps
                            </span>
                          )}
                        </p>
                        <ul className="mt-1 space-y-0.5 text-xs text-[var(--text2)]">
                          {day.exerciseIds.map((id) => {
                            const ex = getExercise(id);
                            return <li key={id}>• {ex?.name || id}</li>;
                          })}
                        </ul>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </Panel>
          );
        })}
      </div>
    </>
  );
}
