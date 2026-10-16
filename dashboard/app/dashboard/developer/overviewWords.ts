// The Developer overview's card lines that are worked out, not just read.
// Kept out of page.tsx (a Next page may export only the page) and free of
// path aliases, so the proof script runs it under plain node.
import type { VaultZone } from "../../_lib/services/buildService";

/** The Vault card's line: what is true now, never "opened" while it is
 *  locked. An older backend that does not say whether
 *  it is open gets the plain "Made". */
export function vaultStatus(vault: VaultZone | null | undefined): string {
  if (!vault) return "Could not be read";
  // An unplugged vault reads initialized:false (its files are not there to
  // see), so "missing" is asked first: "Not made yet" would send the owner to
  // make a second, empty vault, which the backend refuses.
  if (vault.missing) return "Made, its drive is not plugged in";
  if (!vault.initialized) return "Not made yet";
  if (vault.unlocked === true) return "Made, open now";
  if (vault.unlocked === false) return "Made, locked";
  return "Made";
}
