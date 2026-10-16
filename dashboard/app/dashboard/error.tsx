"use client";

import { useEffect } from "react";
import MessagePanel, { proseStyle } from "@/app/_components/MessagePanel";
import { PuffButton } from "@/app/onboarding/ui";

export default function DashboardError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error("Dashboard error:", error);
  }, [error]);

  return (
    <div className="flex min-h-[60vh] items-center justify-center p-6">
      <MessagePanel
        eyebrow="Error"
        title="Something went wrong"
        className="w-full max-w-md"
      >
        <p style={proseStyle}>{error.message || "An unexpected error occurred."}</p>
        <div style={{ marginTop: 16 }}>
          <PuffButton label="Try again" kind="primary" onClick={reset} />
        </div>
      </MessagePanel>
    </div>
  );
}
