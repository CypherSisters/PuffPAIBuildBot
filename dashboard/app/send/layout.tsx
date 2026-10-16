import type { Metadata } from "next";

/**
 * /send lives OUTSIDE the dashboard shell — no sidebar, no topbar, no widget
 * board. It is the one screen a phone opens after scanning the pairing QR, so
 * it inherits only the root layout (fonts, theme script, scene background).
 */
export const metadata: Metadata = {
  title: "Send to Puff",
  description: "Send a photo or document to PuffPAI from any household device.",
};

export default function SendLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return <main className="min-h-dvh px-4 py-6 sm:px-6 sm:py-10">{children}</main>;
}
