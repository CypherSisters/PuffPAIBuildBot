"use client";

import { useMemo } from "react";
import {
  mdiBrain,
  mdiTrophy,
  mdiStar,
  mdiLightningBolt,
  mdiHistory,
  mdiChartBar,
  mdiAlertCircleOutline,
  mdiPlayCircle,
  mdiGamepadVariant,
  mdiEarth,
  mdiMovie,
  mdiMusic,
  mdiFootball,
  mdiLaptop,
  mdiLeaf,
  mdiBookOpenVariant,
  mdiShuffle,
  mdiSchool,
} from "@mdi/js";
import Button from "@/app/_components/Button";
import Icon from "@/app/_components/Icon";
import { useAppData } from "@/app/_lib/hooks/useAppData";
import { AppPage, Panel, SectionBar } from "../_components/ui";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

type TriviaResult = {
  date: string;
  mode: string;
  category: string;
  difficulty: string;
  score: number;
  bestStreak: number;
  totalQuestions: number;
};

type TriviaData = {
  results: TriviaResult[];
};

/* ------------------------------------------------------------------ */
/*  Constants                                                          */
/* ------------------------------------------------------------------ */

const MODE_META: Record<string, { label: string; icon: string; color: string }> = {
  brain: {
    label: "Brain",
    icon: mdiBrain,
    color: "bg-[color-mix(in_srgb,var(--magenta)_16%,transparent)] text-[var(--magenta)]",
  },
  speed: {
    label: "Speed",
    icon: mdiLightningBolt,
    color: "bg-[color-mix(in_srgb,var(--lemon)_16%,transparent)] text-[var(--lemon)]",
  },
  survival: {
    label: "Survival",
    icon: mdiStar,
    color: "bg-[color-mix(in_srgb,var(--puff-red)_16%,transparent)] text-[var(--puff-red)]",
  },
  duel: {
    label: "Duel",
    icon: mdiGamepadVariant,
    color: "bg-[color-mix(in_srgb,var(--sky)_16%,transparent)] text-[var(--sky)]",
  },
};

const CATEGORY_META: Record<string, { label: string; icon: string; color: string }> = {
  science: { label: "Science", icon: mdiSchool, color: "bg-[color-mix(in_srgb,var(--sky)_16%,transparent)] text-[var(--sky)]" },
  history: { label: "History", icon: mdiHistory, color: "bg-[color-mix(in_srgb,var(--lemon)_16%,transparent)] text-[var(--lemon)]" },
  geography: { label: "Geography", icon: mdiEarth, color: "bg-[color-mix(in_srgb,var(--mint)_16%,transparent)] text-[var(--mint)]" },
  movies: { label: "Movies", icon: mdiMovie, color: "bg-[color-mix(in_srgb,var(--pink)_16%,transparent)] text-[var(--pink)]" },
  music: { label: "Music", icon: mdiMusic, color: "bg-[color-mix(in_srgb,var(--magenta)_16%,transparent)] text-[var(--magenta)]" },
  sports: { label: "Sports", icon: mdiFootball, color: "bg-[color-mix(in_srgb,var(--lemon)_16%,transparent)] text-[var(--lemon)]" },
  technology: { label: "Technology", icon: mdiLaptop, color: "bg-[color-mix(in_srgb,var(--sky)_16%,transparent)] text-[var(--sky)]" },
  nature: { label: "Nature", icon: mdiLeaf, color: "bg-[color-mix(in_srgb,var(--mint)_16%,transparent)] text-[var(--mint)]" },
  literature: { label: "Literature", icon: mdiBookOpenVariant, color: "bg-[color-mix(in_srgb,var(--magenta)_16%,transparent)] text-[var(--magenta)]" },
  random: { label: "Random", icon: mdiShuffle, color: "bg-[var(--chip)] text-[var(--text2)]" },
};

const DIFFICULTY_COLORS: Record<string, string> = {
  easy: "bg-[color-mix(in_srgb,var(--mint)_16%,transparent)] text-[var(--mint)]",
  mid: "bg-[color-mix(in_srgb,var(--lemon)_16%,transparent)] text-[var(--lemon)]",
  hard: "bg-[color-mix(in_srgb,var(--puff-red)_16%,transparent)] text-[var(--puff-red)]",
};

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

/* ------------------------------------------------------------------ */
/*  Page                                                               */
/* ------------------------------------------------------------------ */

export default function TriviaPage() {
  const { data, loading, error } = useAppData<TriviaData>("trivia");

  /* ---- Computed stats ---- */
  const stats = useMemo(() => {
    const results = data?.results ?? [];
    if (results.length === 0) {
      return {
        totalGames: 0,
        avgScore: 0,
        bestScore: 0,
        bestCategory: "",
        bestStreak: 0,
        favCategory: "",
      };
    }

    const totalGames = results.length;
    const avgScore = Math.round(
      results.reduce((s, r) => s + r.score, 0) / totalGames,
    );

    let bestScore = 0;
    let bestCategory = "";
    let bestStreak = 0;

    for (const r of results) {
      if (r.score > bestScore) {
        bestScore = r.score;
        bestCategory = r.category;
      }
      if (r.bestStreak > bestStreak) {
        bestStreak = r.bestStreak;
      }
    }

    /* Favorite category = most played */
    const catCounts: Record<string, number> = {};
    for (const r of results) {
      catCounts[r.category] = (catCounts[r.category] || 0) + 1;
    }
    const favCategory = Object.entries(catCounts).sort(
      (a, b) => b[1] - a[1],
    )[0]?.[0] ?? "";

    return { totalGames, avgScore, bestScore, bestCategory, bestStreak, favCategory };
  }, [data?.results]);

  /* ---- Category breakdown ---- */
  const categoryBreakdown = useMemo(() => {
    const results = data?.results ?? [];
    const map: Record<string, { count: number; totalScore: number }> = {};
    for (const r of results) {
      if (!map[r.category]) map[r.category] = { count: 0, totalScore: 0 };
      map[r.category].count += 1;
      map[r.category].totalScore += r.score;
    }
    return Object.entries(map)
      .map(([cat, val]) => ({
        category: cat,
        count: val.count,
        avgScore: Math.round(val.totalScore / val.count),
      }))
      .sort((a, b) => b.count - a.count);
  }, [data?.results]);

  const maxCatCount = Math.max(1, ...categoryBreakdown.map((c) => c.count));

  /* Sorted results (newest first) */
  const sortedResults = useMemo(
    () => {
      const results = data?.results ?? [];
      return [...results].sort(
        (a, b) => new Date(b.date).getTime() - new Date(a.date).getTime(),
      );
    },
    [data?.results],
  );

  /* ---- loading / error ---- */
  if (loading) {
    return (
      <AppPage>
        <div className="flex items-center justify-center py-20">
          <div className="h-10 w-10 animate-spin rounded-full border-4 border-[var(--magenta)] border-t-transparent" />
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
            {"\u{1F9E0}"}
          </div>
          <div className="flex-1">
            <h1 className="text-2xl font-bold drop-shadow-sm">Trivia</h1>
            <p className="text-sm text-(--puff-on-accent)/80">
              Quiz Game Stats & Play
            </p>
          </div>
          <Button
            icon={mdiPlayCircle}
            label="Quick Play"
            color="white"
            small
            roundedFull
            href="/dashboard/apps/trivia/play"
          />
        </div>
      </div>

      {/* ============================================================== */}
      {/*  STATS DASHBOARD                                                */}
      {/* ============================================================== */}
      <SectionBar title="Stats Overview">
        <span className="text-sm text-[var(--text3)]">
          {stats.totalGames} games played
        </span>
      </SectionBar>

      <div className="mb-6 grid grid-cols-2 gap-4 lg:grid-cols-5">
        {/* Total Games */}
        <Panel hoverable>
          <div className="text-center">
            <div className="mx-auto mb-2 flex h-12 w-12 items-center justify-center rounded-xl bg-[color-mix(in_srgb,var(--magenta)_16%,transparent)]">
              <Icon path={mdiGamepadVariant} size="24" className="text-[var(--magenta)]" />
            </div>
            <p className="text-2xl font-bold text-[var(--text)]">
              {stats.totalGames}
            </p>
            <p className="text-xs text-[var(--text2)]">
              Games Played
            </p>
          </div>
        </Panel>

        {/* Average Score */}
        <Panel hoverable>
          <div className="text-center">
            <div className="mx-auto mb-2 flex h-12 w-12 items-center justify-center rounded-xl bg-[color-mix(in_srgb,var(--sky)_16%,transparent)]">
              <Icon path={mdiChartBar} size="24" className="text-[var(--sky)]" />
            </div>
            <p className="text-2xl font-bold text-[var(--text)]">
              {stats.avgScore}
            </p>
            <p className="text-xs text-[var(--text2)]">
              Avg Score
            </p>
          </div>
        </Panel>

        {/* Best Score */}
        <Panel hoverable>
          <div className="text-center">
            <div className="mx-auto mb-2 flex h-12 w-12 items-center justify-center rounded-xl bg-[color-mix(in_srgb,var(--lemon)_16%,transparent)]">
              <Icon path={mdiTrophy} size="24" className="text-[var(--lemon)]" />
            </div>
            <p className="text-2xl font-bold text-[var(--text)]">
              {stats.bestScore}
            </p>
            <p className="text-xs text-[var(--text2)]">
              Best Score
              {stats.bestCategory && (
                <span className="block">
                  ({CATEGORY_META[stats.bestCategory]?.label ?? stats.bestCategory})
                </span>
              )}
            </p>
          </div>
        </Panel>

        {/* Best Streak */}
        <Panel hoverable>
          <div className="text-center">
            <div className="mx-auto mb-2 flex h-12 w-12 items-center justify-center rounded-xl bg-[color-mix(in_srgb,var(--lemon)_16%,transparent)]">
              <Icon path={mdiLightningBolt} size="24" className="text-[var(--lemon)]" />
            </div>
            <p className="text-2xl font-bold text-[var(--text)]">
              {stats.bestStreak}
            </p>
            <p className="text-xs text-[var(--text2)]">
              Best Streak
            </p>
          </div>
        </Panel>

        {/* Favorite Category */}
        <Panel hoverable>
          <div className="text-center">
            <div className="mx-auto mb-2 flex h-12 w-12 items-center justify-center rounded-xl bg-[color-mix(in_srgb,var(--mint)_16%,transparent)]">
              <Icon
                path={
                  CATEGORY_META[stats.favCategory]?.icon ?? mdiStar
                }
                size="24"
                className="text-[var(--mint)]"
              />
            </div>
            <p className="text-sm font-bold text-[var(--text)]">
              {CATEGORY_META[stats.favCategory]?.label || "N/A"}
            </p>
            <p className="text-xs text-[var(--text2)]">
              Favorite
            </p>
          </div>
        </Panel>
      </div>

      {/* ============================================================== */}
      {/*  CATEGORY BREAKDOWN                                             */}
      {/* ============================================================== */}
      <SectionBar title="Category Breakdown">
        <span className="text-sm text-[var(--text3)]">
          {categoryBreakdown.length} categories
        </span>
      </SectionBar>

      {categoryBreakdown.length === 0 ? (
        <Panel className="mb-6 text-center text-[var(--text3)]">
          <p className="py-4">Play some games to see category stats!</p>
        </Panel>
      ) : (
        <div className="mb-6 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {categoryBreakdown.map((cat) => {
            const meta = CATEGORY_META[cat.category];
            return (
              <Panel key={cat.category} hoverable>
                <div className="flex items-center gap-3">
                  <div
                    className={`flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-xl ${
                      meta?.color ?? "bg-[var(--chip)] text-[var(--text2)]"
                    }`}
                  >
                    <Icon path={meta?.icon ?? mdiStar} size="20" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-semibold text-[var(--text)]">
                        {meta?.label ?? cat.category}
                      </span>
                      <span className="text-xs text-[var(--text3)]">
                        {cat.count} games | avg {cat.avgScore}
                      </span>
                    </div>
                    <div className="mt-1.5 h-2 w-full overflow-hidden rounded-full bg-[var(--chip)]">
                      <div
                        className="h-full rounded-full bg-[var(--magenta)] transition-all"
                        style={{
                          width: `${(cat.count / maxCatCount) * 100}%`,
                        }}
                      />
                    </div>
                  </div>
                </div>
              </Panel>
            );
          })}
        </div>
      )}

      {/* ============================================================== */}
      {/*  GAME HISTORY TABLE                                             */}
      {/* ============================================================== */}
      <SectionBar title="Game History">
        <span className="text-sm text-[var(--text3)]">
          Newest first
        </span>
      </SectionBar>

      {sortedResults.length === 0 ? (
        <Panel className="text-center text-[var(--text3)]">
          <div className="py-8">
            <Icon
              path={mdiBrain}
              size="48"
              className="mx-auto mb-2 opacity-30"
            />
            <p>No trivia games yet. Hit Quick Play to start!</p>
          </div>
        </Panel>
      ) : (
        <Panel style={{ padding: 0, overflow: "hidden" }}>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-[var(--line)]">
                  <th className="px-4 py-3 font-semibold text-[var(--text2)]">
                    Date
                  </th>
                  <th className="px-4 py-3 font-semibold text-[var(--text2)]">
                    Mode
                  </th>
                  <th className="px-4 py-3 font-semibold text-[var(--text2)]">
                    Category
                  </th>
                  <th className="px-4 py-3 font-semibold text-[var(--text2)]">
                    Difficulty
                  </th>
                  <th className="px-4 py-3 text-right font-semibold text-[var(--text2)]">
                    Score
                  </th>
                  <th className="px-4 py-3 text-right font-semibold text-[var(--text2)]">
                    Streak
                  </th>
                  <th className="px-4 py-3 text-right font-semibold text-[var(--text2)]">
                    Q&apos;s
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--line)]">
                {sortedResults.map((r, idx) => {
                  const mode = MODE_META[r.mode];
                  const cat = CATEGORY_META[r.category];
                  const diff = DIFFICULTY_COLORS[r.difficulty] ?? DIFFICULTY_COLORS.mid;
                  return (
                    <tr
                      key={idx}
                      className="transition hover:bg-[var(--chip)]"
                    >
                      <td className="whitespace-nowrap px-4 py-3 text-[var(--text2)]">
                        {formatDate(r.date)}
                      </td>
                      <td className="px-4 py-3">
                        <span
                          className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${
                            mode?.color ?? "bg-[var(--chip)] text-[var(--text2)]"
                          }`}
                        >
                          <Icon path={mode?.icon ?? mdiBrain} size="12" />
                          {mode?.label ?? r.mode}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        <span
                          className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${
                            cat?.color ?? "bg-[var(--chip)] text-[var(--text2)]"
                          }`}
                        >
                          <Icon path={cat?.icon ?? mdiStar} size="12" />
                          {cat?.label ?? r.category}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        <span
                          className={`rounded-full px-2 py-0.5 text-xs font-bold uppercase ${diff}`}
                        >
                          {r.difficulty}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-right font-bold text-[var(--magenta)]">
                        {r.score.toLocaleString()}
                      </td>
                      <td className="px-4 py-3 text-right text-[var(--text2)]">
                        {r.bestStreak}
                      </td>
                      <td className="px-4 py-3 text-right text-[var(--text2)]">
                        {r.totalQuestions}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Panel>
      )}
    </AppPage>
  );
}
