"use client";

import { useRouter } from "next/navigation";
import { mdiArrowLeft } from "@mdi/js";
import Icon from "@/app/_components/Icon";

export default function TriviaPlayPage() {
  const router = useRouter();

  return (
    <div className="relative flex h-screen flex-col bg-[#0B1120]">
      {/* Minimal top bar */}
      <div className="flex items-center gap-3 px-4 py-2 bg-[#0B1120]/80 backdrop-blur-sm border-b border-(--line) z-10">
        <button
          onClick={() => router.push("/dashboard/apps/trivia")}
          className="flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium text-(--text2) transition hover:bg-(--puff-card-hover) hover:text-(--puff-text-strong)"
        >
          <Icon path={mdiArrowLeft} size="18" />
          Back to Stats
        </button>
        <span className="text-sm font-semibold text-(--puff-text-strong)">
          🧠 AeroTrivia
        </span>
      </div>

      {/* Game iframe */}
      <iframe
        src="/trivia-app.html"
        className="flex-1 w-full border-0"
        title="AeroTrivia Game"
        allow="autoplay"
      />
    </div>
  );
}
