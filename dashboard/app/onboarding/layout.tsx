import type { Metadata } from "next";

/**
 * /onboarding lives OUTSIDE the dashboard shell — no rail, no topbar, no widget
 * board. On a fresh box there is nothing behind it yet, so it inherits only the
 * root layout (fonts, theme script, scene background), exactly like /send.
 */
export const metadata: Metadata = {
  title: "Build your bot",
  description: "Say what bot you want, check one plan, and build it.",
};

export default function OnboardingLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return <main className="min-h-dvh px-4 py-6 sm:px-6 sm:py-10">{children}</main>;
}
