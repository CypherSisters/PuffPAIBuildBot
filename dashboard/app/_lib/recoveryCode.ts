// Vault recovery-code helpers for the browser face.
//
// The code itself never lives here — these functions only compare. Nothing in
// this module writes to localStorage, sessionStorage, a store, a URL, or the
// console: a recovery code exists in React state for exactly as long as the
// screen showing it is mounted.
//
// The normalization mirrors `_normalize_recovery_code` in
// src/puffpai/zone_vault.py: uppercase, letters and digits only. Someone
// copying twenty-five characters off a card should not be failed for where
// they put the dashes.

/** Reduce a code to comparable form: uppercase, alphanumeric only.
 *
 * This strips ASCII punctuation, whitespace and control characters, and
 * DELIBERATELY KEEPS any non-ASCII character. That asymmetry is the whole
 * point: Python's `str.isalnum()` is Unicode-aware, so `"...37É"` keeps the
 * `É` there and fails to derive the key. A naive `[^A-Z0-9]` here would strip
 * it, the browser would say "confirmed", and the owner would discover months
 * later that the code they carefully wrote down does not open their vault.
 *
 * The rule to preserve: this function may be STRICTER than the Python side,
 * never more permissive. A false rejection costs a retype; a false
 * confirmation costs the vault.
 *
 * `\p{L}`/`\p{N}` would express this directly but need an es2018 target, and
 * this project compiles to es5 - hence the explicit ASCII ranges.
 */
export function normalizeRecoveryCode(text: string): string {
  return (
    (text || "")
      .toUpperCase()
      // ASCII punctuation, whitespace and control characters.
      .replace(/[\x00-\x2F\x3A-\x40\x5B-\x60\x7B-\x7F]/g, "")
      // Invisible formatting characters. Python drops these too - they are
      // not alphanumeric - so stripping them keeps the two sides in step,
      // and a code pasted out of a document does not fail for something the
      // owner cannot even see. FORGIVING direction only: a visible lookalike
      // (Greek omega, an accented letter, an Arabic-Indic digit) is still
      // kept and still fails, exactly as it does in the vault.
      // Written as escapes on purpose - a literal invisible character in
      // source is a character no reviewer can see.
      .replace(/[\u00ad\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff]/g, "")
  );
}

/** True when `typed` is the code we `shown`, allowing for handwriting.
 *  Fails closed: an empty or unusable value on either side is never a match. */
export function recoveryCodeMatches(shown: string, typed: string): boolean {
  const left = normalizeRecoveryCode(shown);
  const right = normalizeRecoveryCode(typed);
  if (!left || !right) return false;
  return left === right;
}

/** The one line the CLI wizard uses when a typed-back code is wrong. */
export const RECOVERY_CONFIRM_FAILED =
  "That does not match the code on the screen. Check what you wrote down and " +
  "try it again. This is exactly the mistake it is better to catch now.";

/** Said plainly when an owner takes the escape route. */
export const RECOVERY_NOT_CONFIRMED =
  "No recovery code was confirmed. The code that was on that screen is gone " +
  "and cannot be shown again. Unlock the vault and issue a new one from the " +
  "vault page before you need it.";
