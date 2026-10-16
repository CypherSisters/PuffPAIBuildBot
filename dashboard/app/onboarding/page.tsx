import { Suspense } from "react";
import Conversation from "./Conversation";

/**
 * /onboarding: the build wizard, as the same conversation `puffpai setup`
 * has on the terminal. The old 8-step onboarding and the
 * card review screen are retired; there is one face in the browser.
 */
export default function OnboardingPage() {
  return (
    <Suspense
      fallback={
        <div style={{ color: "var(--text3)", padding: 32, fontSize: 14 }}>Loading...</div>
      }
    >
      <Conversation />
    </Suspense>
  );
}
