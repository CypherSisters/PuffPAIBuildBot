#!/usr/bin/env node
/**
 * Print (or rotate) the pairing code, on the machine running PuffPAI.
 *
 *   npm run pair:code                 print the current code
 *   npm run pair:code -- --rotate     issue a new one and print it
 *
 * BOOTSTRAP PROBLEM THIS SOLVES
 * -----------------------------
 * GET /api/pair/code answers only an already-paired device, so the FIRST device
 * has no way to learn the code over HTTP. Whoever installed PuffPAI has a shell
 * on the box; this is the out-of-band channel that shell provides.
 *
 * Deliberately a manual, operator-run command rather than something printed to
 * the service log on startup -- the code is a bearer credential and does not
 * belong in a log file that gets tailed, rotated, or pasted into a chat.
 *
 * Only prints the CODE. The HMAC secret is never displayed.
 *
 * OWNER-ONLY, ENFORCED
 * --------------------
 * The state file is 0600, so the operating system already refuses anyone but
 * its owner and root. This script checks the same thing explicitly and refuses
 * early with a sentence a person can act on, rather than an EACCES stack trace;
 * and it REFUSES OUTRIGHT if the file is readable by the group or by the world,
 * because at that point the code is not a secret and printing it would imply
 * that it still is. On Windows those bits are not meaningful and the checks are
 * skipped -- POSIX file modes are the mechanism, and they do not exist there.
 *
 * CODES EXPIRE one hour after they are issued. Like GET /api/pair/code, this
 * command lazily rotates an expired code before printing, so what lands in the
 * terminal is always redeemable -- being handed a dead code by the one tool
 * that exists to hand you a live one would be worse than useless. The rewrite is
 * ADDITIVE and preserves every other field verbatim: the file stays version 1
 * and the secret is never touched, so already-paired devices are unaffected.
 *
 * It also CREATES the state on first run if it does not exist yet, so the code
 * can be read before the dashboard has ever been started. The format is the one
 * app/_lib/server/pairing.ts writes; pairing-cookie.test.mjs asserts that a
 * state minted here is accepted there, so the two cannot drift silently.
 *
 * WHICH BOT'S CODE: each build keeps its pairing state in its own state folder,
 * <state>/pairing/pairing.json, found the way the web page finds it
 * (app/_lib/server/pairing.ts, pairingStatePath): PUFFPAI_STATE_DIR, else
 * PUFFPAI_DATA_DIR/state, else the per-user folder -- reading the first two
 * from the installer's pointer file when this shell was not given them. The
 * one build that owns this copy's old data/pairing.json keeps using it (see
 * data/pairing-owner.json). The rules are duplicated from pairing.ts on
 * purpose (a plain Node script cannot import it);
 * tests/test_per_build_secrets.py runs both, and the Python side, over the
 * same folders.
 *
 * Env: PUFF_PAIRING_STATE overrides all of that.
 * `--path` prints which file this build uses and stops (no code printed).
 *
 * puff puff pass
 */
const crypto = require("crypto");
const fs = require("fs");
const os = require("os");
const path = require("path");

/** Keep in sync with PAIR_CODE_TTL_SEC in app/_lib/server/pairing.ts. */
const CODE_TTL_MS = 60 * 60 * 1000;
/** Keep in sync with CODE_ALPHABET in app/_lib/server/pairing.ts. */
const CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ";
const CODE_LENGTH = 8;
const SECRET_BYTES = 32;

const forceRotate = process.argv.slice(2).includes("--rotate");
const onlyPath = process.argv.slice(2).includes("--path");

/* ── which build (keep in step with pairingStatePath in pairing.ts) ── */

function checkoutRoot() {
  return path.resolve(process.cwd(), "..");
}

function pointerSettings() {
  const out = {};
  try {
    const ptr = JSON.parse(
      fs.readFileSync(path.join(checkoutRoot(), ".puffpai-install.json"), "utf8"),
    );
    const envFile = ptr && typeof ptr.env_file === "string" ? ptr.env_file.trim() : "";
    if (envFile) {
      // Only these two names. The file also holds the API key; not read here.
      for (const line of fs.readFileSync(envFile, "utf8").split(/\r?\n/)) {
        const m = /^\s*(PUFFPAI_STATE_DIR|PUFFPAI_DATA_DIR)\s*=\s*(.*)$/.exec(line);
        if (m && m[2].trim()) out[m[1]] = m[2].trim();
      }
    }
  } catch {
    /* not an installed copy */
  }
  return out;
}

function buildStateDir() {
  const ptr = pointerSettings();
  const setting = (name) => (process.env[name] || "").trim() || (ptr[name] || "").trim();
  const root = checkoutRoot();
  const state = setting("PUFFPAI_STATE_DIR");
  if (state) return path.resolve(root, state);
  const data = setting("PUFFPAI_DATA_DIR");
  if (data) return path.join(path.resolve(root, data), "state");
  if (process.platform === "win32") {
    return path.resolve(path.join(process.env.LOCALAPPDATA || os.homedir(), "PuffPAI"));
  }
  if (process.platform === "darwin") {
    return path.join(os.homedir(), "Library", "Application Support", "PuffPAI");
  }
  return path.resolve(path.join(
    process.env.XDG_STATE_HOME || path.join(os.homedir(), ".local", "state"),
    "puffpai",
  ));
}

/*
 * The build id is worked out by the Python side only and read here from
 * <state>/build_id.json (see recordedBuildId in app/_lib/server/pairing.ts):
 * this script never computes one. Without it, the one decision it is needed
 * for (who owns the old shared file) is refused with a plain message.
 */
const OWNER_FILE = "pairing-owner.json";
const CLAIM_STALE_MS = 30000;
const IN_PROGRESS = "in-progress";

class NotReady extends Error {}

function recordedBuildId(stateDir) {
  let rec;
  try {
    rec = JSON.parse(fs.readFileSync(path.join(stateDir, "build_id.json"), "utf8"));
  } catch {
    return null;
  }
  const build = rec && rec.build;
  const recorded = rec && rec.state_dir;
  if (typeof build !== "string" || !/^[0-9a-f]{16}$/.test(build)) return null;
  if (typeof recorded !== "string" || !recorded) return null;
  try {
    const here = fs.statSync(stateDir, { bigint: true });
    const there = fs.statSync(recorded, { bigint: true });
    if (here.dev !== there.dev || here.ino !== there.ino) return null;
  } catch {
    return null;
  }
  return build;
}

function readOwner(ownerFile) {
  let raw;
  try {
    raw = fs.readFileSync(ownerFile, "utf8");
  } catch (err) {
    return err && err.code === "ENOENT" ? null : "";
  }
  if (!raw.trim()) {
    try {
      return Date.now() - fs.statSync(ownerFile).mtimeMs < CLAIM_STALE_MS ? IN_PROGRESS : "";
    } catch {
      return "";
    }
  }
  try {
    const build = JSON.parse(raw).build;
    return typeof build === "string" && build.length === 16 ? build : "";
  } catch {
    return "";
  }
}

/** Create the owner record whole (temp file, then link), only if absent. */
function claimOwner(ownerFile, record) {
  const tmp = path.join(path.dirname(ownerFile),
    `.${OWNER_FILE}.${process.pid}.${crypto.randomBytes(4).toString("hex")}.tmp`);
  let linked = null;
  try {
    fs.writeFileSync(tmp, record, { flag: "wx", mode: 0o600 });
    try {
      fs.linkSync(tmp, ownerFile);
      linked = true;
    } catch (err) {
      if (err && err.code === "EEXIST") linked = false;
    }
  } finally {
    try { fs.unlinkSync(tmp); } catch { /* already gone */ }
  }
  if (linked !== null) return linked;
  try {
    fs.writeFileSync(ownerFile, record, { flag: "wx", mode: 0o600 });
    return true;
  } catch (err) {
    if (err && err.code === "EEXIST") return false;
    throw err;
  }
}

function keepsLegacy(legacy, stateDir) {
  const me = recordedBuildId(stateDir);
  if (!me) {
    throw new NotReady(
      "This bot has not written down which build it is yet, so this cannot " +
      "tell which pairing code is its own. Start the bot once (not only the " +
      "web page), then run this again.");
  }
  const ownerFile = path.join(path.dirname(legacy), OWNER_FILE);
  let owner = readOwner(ownerFile);
  if (owner === null) {
    const record = JSON.stringify(
      { version: 1, build: me, claimedAt: new Date().toISOString() }, null, 2);
    try {
      if (claimOwner(ownerFile, record)) return true;
    } catch {
      return false;
    }
    owner = readOwner(ownerFile);
  }
  if (owner === IN_PROGRESS) {
    throw new NotReady(
      "Another copy of this bot is setting up its pairing right now. Run this " +
      "again in a moment.");
  }
  return owner === me;
}

function pairingStatePath() {
  if (process.env.PUFF_PAIRING_STATE) return process.env.PUFF_PAIRING_STATE;
  const state = buildStateDir();
  const own = path.join(state, "pairing", "pairing.json");
  if (fs.existsSync(own)) return own;
  const legacy = path.join(process.cwd(), "data", "pairing.json");
  if (fs.existsSync(legacy) && keepsLegacy(legacy, state)) return legacy;
  return own;
}

let file;
try {
  file = pairingStatePath();
} catch (err) {
  if (!(err instanceof NotReady)) throw err;
  console.error(err.message);
  process.exit(2);
}
if (onlyPath) {
  console.log(file);
  process.exit(0);
}

/** Rejection-free because 256 % 32 === 0 -- every byte maps uniformly. */
function randomCode() {
  const bytes = crypto.randomBytes(CODE_LENGTH);
  let out = "";
  for (let i = 0; i < CODE_LENGTH; i++) {
    out += CODE_ALPHABET[bytes[i] % CODE_ALPHABET.length];
  }
  return out;
}

/**
 * Atomic 0600 temp+rename, matching writeState() in pairing.ts. The secret sits
 * in this file; a truncated or world-readable intermediate is not acceptable
 * even for the moment a rename would take.
 */
function writeState(next) {
  fs.mkdirSync(path.dirname(file), { recursive: true, mode: 0o700 });
  const tmp = `${file}.${process.pid}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(next, null, 2), { mode: 0o600 });
  try {
    fs.chmodSync(tmp, 0o600);
  } catch {
    /* Windows: chmod is a no-op / may throw. Not fatal. */
  }
  fs.renameSync(tmp, file);
}

function die(...lines) {
  for (const l of lines) console.error(l);
  process.exit(1);
}

/**
 * Refuse to run for anyone but the file's owner or root, and refuse to print a
 * code out of a file the rest of the machine can already read.
 *
 * POSIX only. `process.getuid` is undefined on Windows, where these bits carry
 * no meaning; there the ACL on the data directory is the control and this
 * function correctly does nothing rather than pretending to check something.
 */
function assertOwnerOnly(st) {
  if (typeof process.getuid !== "function") return;
  const uid = process.getuid();
  if (uid !== 0 && st.uid !== uid) {
    die(
      `${file} belongs to uid ${st.uid}; you are uid ${uid}.`,
      "The pairing code is readable only by the account PuffPAI runs as, or by root.",
      "Re-run this as that account (for example: sudo -u puffpai npm run pair:code).",
    );
  }
  const mode = st.mode & 0o777;
  if (mode & 0o077) {
    die(
      `${file} is mode ${mode.toString(8)} -- readable beyond its owner.`,
      "Refusing to print a pairing code out of a file the rest of the machine can read.",
      `Fix it and run this again:   chmod 600 ${file}`,
    );
  }
}

let state = null;
let created = false;
try {
  const st = fs.statSync(file);
  assertOwnerOnly(st);
  state = JSON.parse(fs.readFileSync(file, "utf8"));
} catch (err) {
  if (err && err.code === "ENOENT") {
    // First run, before the dashboard has ever started. Minting the state here
    // is the whole point of being able to read the code during install.
    try {
      state = {
        version: 1,
        secret: crypto.randomBytes(SECRET_BYTES).toString("hex"),
        code: randomCode(),
        createdAt: new Date().toISOString(),
        codeRotatedAt: new Date().toISOString(),
      };
      writeState(state);
      created = true;
    } catch (writeErr) {
      die(
        `Could not create the pairing state at ${file}.`,
        String(writeErr && writeErr.message ? writeErr.message : writeErr),
      );
    }
  } else {
    die(
      `Could not read the pairing state at ${file}.`,
      String(err && err.message ? err.message : err),
    );
  }
}

if (
  !state ||
  typeof state.secret !== "string" ||
  !/^[0-9a-f]{64}$/.test(state.secret) ||
  typeof state.code !== "string" ||
  state.code.length !== CODE_LENGTH
) {
  die(
    `Pairing state at ${file} is malformed.`,
    "Refusing to overwrite it -- doing so would mint a new secret and un-pair",
    "every device at once. Inspect the file, or move it aside deliberately if",
    "you do intend to drop every paired device.",
  );
}

const rotatedAtMs = Date.parse(state.codeRotatedAt || "");
// Unparseable/absent rotation time counts as expired -- fail closed, and it
// self-heals right here.
const expired =
  !Number.isFinite(rotatedAtMs) || Date.now() >= rotatedAtMs + CODE_TTL_MS;

let rotatedNow = false;
if (!created && (expired || forceRotate)) {
  try {
    // Spread first: every existing field survives, including the secret. This
    // rotates the CODE only -- already-paired devices are untouched.
    const next = Object.assign({}, state, {
      code: randomCode(),
      codeRotatedAt: new Date().toISOString(),
    });
    writeState(next);
    state = next;
    rotatedNow = true;
  } catch (err) {
    die(
      "The pairing code needed rotating and the state file is not writable.",
      "Not printing a code that will be rejected.",
      String(err && err.message ? err.message : err),
    );
  }
}

const pretty = `${state.code.slice(0, 4)}-${state.code.slice(4)}`;
const expiresAt = new Date(Date.parse(state.codeRotatedAt) + CODE_TTL_MS);
console.log("");
console.log("  PuffPAI pairing code");
console.log("");
console.log(`      ${pretty}`);
console.log("");
if (created) {
  console.log("  (first run -- this is a brand new code)");
} else if (rotatedNow && forceRotate) {
  console.log("  (rotated on request -- the previous code no longer works)");
} else if (rotatedNow) {
  console.log("  (the previous code had expired -- this is a fresh one)");
}
console.log(`  expires: ${expiresAt.toISOString()}  (one hour)`);
console.log("");
console.log("  Open the dashboard on the phone or laptop you want to use,");
console.log("  and type this code on the pairing screen. That device then");
console.log("  stays paired for a year, and you can revoke it from that");
console.log("  same screen at any time.");
console.log("");
