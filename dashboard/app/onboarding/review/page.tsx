import { redirect } from "next/navigation";

/**
 * The card review screen is retired: the wizard ends on
 * ONE summary with one "Looks good", on /onboarding. An old link lands there.
 */
export default function ReviewPage() {
  redirect("/onboarding");
}
