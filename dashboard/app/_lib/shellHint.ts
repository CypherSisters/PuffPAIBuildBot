// Shell-safe command hints.
//
// WHY: `cd dashboard && npm run pair:code` is a POSIX one-liner. PowerShell —
// the default shell on the Windows boxes this ships to — does not accept `&&`
// there, so the one command the pairing screen tells you to run fails with
// "The token '&&' is not a valid statement separator". Every user-facing command
// with more than one step is rendered in both forms, the local one first.
//
// Pure functions only; the caller decides how to detect the platform.

export interface CommandForms {
  /** bash / zsh: steps joined with && */
  posix: string;
  /** PowerShell: steps joined with ; */
  powershell: string;
}

export const POSIX_LABEL = "macOS / Linux";
export const POWERSHELL_LABEL = "Windows PowerShell";

/** Join sequential steps into the two shell dialects we document. */
export function commandForms(steps: string[]): CommandForms {
  const parts = steps.map((s) => s.trim()).filter(Boolean);
  return { posix: parts.join(" && "), powershell: parts.join("; ") };
}

/** Best-effort Windows detection from navigator bits. Unknown → false, so the
 *  POSIX form leads and the PowerShell form is still shown underneath. */
export function isWindowsClient(nav?: { platform?: string; userAgent?: string }): boolean {
  const hay = `${nav?.platform ?? ""} ${nav?.userAgent ?? ""}`.toLowerCase();
  return hay.includes("win");
}

/** Which shell the BOX runs, as far as we can tell. */
export type BoxPlatform = "windows" | "posix" | "unknown";

/**
 * Read the box's platform out of the hardware census (`/setup/hardware`), whose
 * `os` block is `platform.system()` / `release` / `machine` from
 * hardware_census.py `_detect_os`.
 *
 * WHY: the commands these hints render run on the BOX, not in the browser. A
 * phone or a Mac on the sofa paired to a Windows box was being told to use `&&`
 * — a line that fails on the machine it is meant for. The census is the box's
 * own answer about itself, so it wins whenever it is there.
 *
 * Takes `unknown` on purpose: the browser's Census type does not declare `os`,
 * and this must not fall over on a census from an older backend that omits it.
 */
export function boxPlatform(source: unknown): BoxPlatform {
  if (!source || typeof source !== "object") return "unknown";
  const root = source as Record<string, unknown>;
  const os = root.os;
  const bag: Record<string, unknown> =
    os && typeof os === "object" ? (os as Record<string, unknown>) : root;
  const raw = [bag.system, bag.platform, bag.os, bag.name]
    .filter((v): v is string => typeof v === "string")
    .join(" ")
    .toLowerCase();
  if (!raw.trim()) return "unknown";
  // POSIX first: "darwin" contains "win", and a Mac reported as Windows is the
  // very bug this function exists to prevent.
  if (/linux|darwin|mac|bsd|android|sunos|aix/.test(raw)) return "posix";
  if (raw.includes("win")) return "windows";
  return "unknown";
}

/**
 * Should the PowerShell form lead? The box's own report decides it; the
 * browser's OS is only the fallback for a box that did not say.
 */
export function preferPowerShell(box: BoxPlatform, browserIsWindows: boolean): boolean {
  if (box === "windows") return true;
  if (box === "posix") return false;
  return browserIsWindows;
}

export interface ShellHint {
  primary: string;
  primaryLabel: string;
  secondary: string;
  secondaryLabel: string;
}

/** The pair the UI renders: the likely-matching form first, the other below. */
export function shellHint(steps: string[], windows: boolean): ShellHint {
  const { posix, powershell } = commandForms(steps);
  return windows
    ? {
        primary: powershell,
        primaryLabel: POWERSHELL_LABEL,
        secondary: posix,
        secondaryLabel: POSIX_LABEL,
      }
    : {
        primary: posix,
        primaryLabel: POSIX_LABEL,
        secondary: powershell,
        secondaryLabel: POWERSHELL_LABEL,
      };
}

// The pairing screen used to show PAIR_CODE_STEPS (cd dashboard, npm run
// pair:code). It now names the Start window and "puffpai pair", one word
// the same in every shell (app/pair/pairWords.ts), so the constant is gone.
