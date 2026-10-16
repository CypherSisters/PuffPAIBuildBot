export default function DashboardLoading() {
  return (
    <div className="p-6">
      {/* Top progress bar */}
      <div className="fixed inset-x-0 top-14 z-50 h-1 overflow-hidden bg-(--puff-surface-deep)">
        {/* puff-motion: arbitrary-value animate-[...] escapes the motion
            governor's selector list, so opt in via its hook class. */}
        <div className="puff-motion h-full w-1/3 animate-[shimmer_1.5s_ease-in-out_infinite] rounded-r-full bg-gradient-to-r from-[#00B4D8] via-[#0096C7] to-[#00B4D8]" />
      </div>

      {/* Hero skeleton */}
      <div className="mb-6 animate-pulse rounded-2xl bg-gradient-to-r p-6 from-(--puff-surface-deep) via-(--puff-surface-deep) to-(--puff-surface-deep)" style={{ height: 160 }} />

      {/* Section title skeleton */}
      <div className="mb-4 flex items-center gap-3">
        <div className="h-6 w-6 rounded bg-(--chip)" />
        <div className="h-6 w-40 rounded bg-(--chip)" />
      </div>

      {/* Cards skeleton */}
      <div className="mb-6 grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-4">
        {[1, 2, 3, 4].map((i) => (
          <div
            key={i}
            className="animate-pulse rounded-2xl p-6 shadow-sm backdrop-blur-sm bg-(--puff-surface-deep)"
            style={{ animationDelay: `${i * 100}ms` }}
          >
            <div className="mb-3 flex items-center justify-between">
              <div className="h-5 w-16 rounded-full bg-(--chip)" />
              <div className="h-7 w-7 rounded bg-(--chip)" />
            </div>
            <div className="flex items-center justify-between">
              <div>
                <div className="mb-2 h-4 w-20 rounded bg-(--chip)" />
                <div className="h-8 w-16 rounded bg-(--chip)" />
              </div>
              <div className="h-12 w-12 rounded-full bg-(--chip)" />
            </div>
          </div>
        ))}
      </div>

      {/* Quick actions skeleton */}
      <div className="mb-4 flex items-center gap-3">
        <div className="h-6 w-6 rounded bg-(--chip)" />
        <div className="h-6 w-32 rounded bg-(--chip)" />
      </div>
      <div className="mb-6 grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">
        {[1, 2, 3, 4, 5].map((i) => (
          <div
            key={i}
            className="animate-pulse rounded-2xl p-4 shadow-sm backdrop-blur-sm bg-(--puff-surface-deep)"
            style={{ animationDelay: `${i * 80}ms` }}
          >
            <div className="flex flex-col items-center gap-2">
              <div className="h-12 w-12 rounded-full bg-(--chip)" />
              <div className="h-4 w-16 rounded bg-(--chip)" />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
