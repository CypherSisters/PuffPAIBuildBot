/**
 * Device pairing -- server-side only.
 *
 * WHY THIS EXISTS
 * ---------------
 * The dashboard's API routes are server-side proxies that attach the appliance
 * credential (PUFFPAI_API_KEY, or the Home Assistant token on /api/camera) to
 * whatever they forward. Before this module, ANY device that could reach port
 * 3003 could POST /api/pai/ha/service/... and actuate the house with the
 * owner's own key, or read the vault listing, with no credential of its own.
 * The allowlist in proxyAllowlist.ts narrowed WHAT that caller could reach; it
 * never asked WHO the caller was, and the cross-site check in crossSite.ts only
 * ever spoke about browsers. Neither is authentication and neither claimed to be.
 *
 * This adds one: a pairing code. A device proves it belongs to the owner once
 * (by typing an 8-character code printed on the box), and then carries a
 * signed, HttpOnly cookie. Unpaired API clients get 401; unpaired page loads
 * are redirected to /pair.
 *
 * Ported from the HomeBot dashboard's app/_lib/server/pairing.ts, which has
 * been carrying a real household since 2026-08. Divergences are marked
 * DIVERGENCE and each one is in the restrictive direction; see docs/PAIRING.md.
 *
 * THREAT MODEL (be honest about it)
 * ---------------------------------
 *  - Protects against: any other device or person who can reach port 3003 --
 *    a guest on the wifi, an IoT gadget, a housemate's laptop, a neighbour --
 *    driving the appliance or reading its data through the keyed proxies.
 *  - Does NOT protect against: an on-path attacker. The dashboard serves plain
 *    HTTP, so the cookie and the code are visible to anyone who can sniff or
 *    ARP-spoof the local network. TLS is the fix and is not deployed.
 *  - Does NOT protect the backend itself (:8000). Anything that can reach the
 *    backend with the API key directly is out of scope here.
 *  - Does NOT authorise. A paired device reaches the whole allowlisted surface.
 *    Pairing bounds WHO, the allowlist bounds WHAT; neither is per-user scope.
 *
 * DIVERGENCE 1 -- v2 COOKIES ONLY, NO v1
 * --------------------------------------
 * HomeBot still accepts a legacy `v1.<expiry>.<hmac>` cookie that carries no
 * device identity, because a live wall kiosk and a household of phones were
 * already holding one when identity was introduced and a cutoff would have
 * dropped them all. BuildBot has never issued a v1 cookie and never will, so
 * porting the format would add an UNREVOCABLE credential shape for zero
 * benefit. Only `v2.<deviceId>.<issued>.<expiry>.<hmac>` verifies here. A
 * three-field cookie is rejected like any other malformed value.
 *
 * DIVERGENCE 2 -- ENFORCEMENT IS "all" BY DEFAULT
 * -----------------------------------------------
 * HomeBot defaults to a staged "knowledge" mode because it was retrofitted onto
 * a running appliance whose kiosk had not paired yet. BuildBot ships the gate
 * from the first boot, so the default is "all" and there is no partial stage to
 * be stuck in. "off" survives as an explicit, documented escape route for an
 * owner who has locked themselves out; anything unrecognised means "all".
 *
 * Node runtime only -- uses node:crypto and node:fs.
 *
 * puff puff pass
 */
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

/* ── constants ─────────────────────────────────────────────────────── */

/** Cookie name the UI can look for (it cannot read the value -- HttpOnly). */
export const PAIRING_COOKIE = "puff_device";

const COOKIE_VERSION_V2 = "v2";
/** 365 days. */
export const COOKIE_MAX_AGE_SEC = 365 * 24 * 60 * 60;

/**
 * Device id: 12 base64url characters (9 random bytes -> 72 bits).
 *
 * The charset is exactly [A-Za-z0-9_-] -- note the absence of ".". The cookie
 * is dot-delimited, so a "." inside an id would let one field masquerade as
 * several and change how the value re-parses. Every place an id is accepted
 * from outside (cookie, request body) tests it against this regex BEFORE the id
 * is used for anything, and issueDeviceCookieValueV2 refuses to sign one that
 * fails. Anchored ^...$ with a fixed length: no prefix, suffix, or newline
 * games (JS "$" would otherwise match before a trailing \n -- the {12} length
 * bound plus the anchors close that).
 */
export const DEVICE_ID_RE = /^[A-Za-z0-9_-]{12}$/;
const DEVICE_ID_BYTES = 9;

/**
 * Re-issue a v2 cookie once it is older than this. 30 days.
 *
 * WHY SLIDING RENEWAL: the cookie's 365-day life is a compromise between "the
 * screen on the wall must not log itself out" and "a captured cookie should not
 * be good forever". Renewal lets a device that is actually in daily use keep a
 * fresh cookie without anyone retyping a code, while a cookie sniffed off the
 * network and squirreled away still dies on its original 365-day clock -- the
 * attacker cannot renew it without also being registered, because renewal only
 * happens for cookies that pass full verification, registry membership included.
 */
export const RENEW_AFTER_SEC = 30 * 24 * 60 * 60;

/**
 * At most one lastSeenAt write per device per hour.
 *
 * WHY THROTTLED: this stamp is taken on the hot path -- every gated request AND
 * every page load runs a cookie verification. An unthrottled stamp would mean a
 * read-modify-write of devices.json per request; a single dashboard page load
 * fires dozens in parallel, so the registry would be rewritten continuously,
 * burning flash on a small appliance and widening the window for a torn read.
 * "Last seen" is a human-facing nicety with hour-level usefulness, so it gets
 * hour-level precision.
 */
const LAST_SEEN_THROTTLE_MS = 60 * 60 * 1000;

/**
 * Human-typeable alphabet: digits + uppercase, minus confusables 0/O/1/I.
 * 8 digits (2-9) + 24 letters = 32 symbols -> 32^8 = 2^40 codes.
 */
const CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ";
const CODE_LENGTH = 8;
const CODE_GROUP = 4;

const SECRET_BYTES = 32;

/* ── state file ────────────────────────────────────────────────────── */

export interface PairingState {
  version: 1;
  /** 32-byte hex; HMAC-SHA256 key for device cookies. Never leaves the server. */
  secret: string;
  /** Current pairing code, unformatted (no dashes), uppercase. */
  code: string;
  createdAt: string;
  codeRotatedAt: string;
}

// Cached on globalThis so Next's dev HMR / multiple route bundles share one
// copy instead of racing on the file.
const G = globalThis as unknown as {
  __puffPairingState?: PairingState;
  __puffPairingStatePath?: string;
  __puffPointerSettings?: Record<string, string>;
  __puffPairFails?: Map<string, { count: number; windowStart: number }>;
  /** deviceId -> epoch ms of the last lastSeenAt write. Throttles that write. */
  __puffDeviceSeen?: Map<string, number>;
};

/* ── which build this web page belongs to ──────────────────────────── */

/**
 * PER-BUILD PAIRING
 * -----------------
 * Each build keeps its pairing state in its OWN state folder:
 * `<state>/pairing/pairing.json`, with devices.json beside it. It used to be
 * `<this folder>/data/pairing.json`, shared by every build run from one code
 * copy, so a device paired to one bot was paired to all of them.
 *
 * The state folder is found exactly as the Python side finds it
 * (puffpai.config.get_state_dir): PUFFPAI_STATE_DIR, else
 * PUFFPAI_DATA_DIR/state, else the per-user folder. The installer's launchers
 * and `puffpai start` set PUFFPAI_STATE_DIR; a web page started by hand in an
 * installed copy reads it from the installer's pointer file, as `puffpai`
 * does. src/puffpai/pairing_code.py resolves the same path with the same
 * rules, and tests/test_per_build_secrets.py runs both over the same folders.
 *
 * EXISTING INSTALLS: the old shared file keeps working for exactly ONE build,
 * the first one to ask after this change. It records itself in
 * data/pairing-owner.json (written whole, then linked into place) and keeps
 * using the old file in place, so its paired devices stay paired. Every
 * other build gets its own.
 */
const PAIRING_DIR = "pairing";
const OWNER_FILE = "pairing-owner.json";

function checkoutRoot(): string {
  return path.resolve(process.cwd(), "..");
}

/** PUFFPAI_STATE_DIR / PUFFPAI_DATA_DIR from the installer's pointer, if any. */
function pointerSettings(): Record<string, string> {
  if (G.__puffPointerSettings) return G.__puffPointerSettings;
  const out: Record<string, string> = {};
  try {
    const ptr = JSON.parse(
      fs.readFileSync(path.join(checkoutRoot(), ".puffpai-install.json"), "utf8"),
    ) as { env_file?: unknown };
    const envFile = typeof ptr?.env_file === "string" ? ptr.env_file.trim() : "";
    if (envFile) {
      // Only these two names are read. The file also holds the API key, which
      // this module has no business touching.
      for (const line of fs.readFileSync(envFile, "utf8").split(/\r?\n/)) {
        const m = /^\s*(PUFFPAI_STATE_DIR|PUFFPAI_DATA_DIR)\s*=\s*(.*)$/.exec(line);
        if (m && m[2].trim()) out[m[1]] = m[2].trim();
      }
    }
  } catch {
    /* no pointer: a checkout that was not installed. Nothing to read. */
  }
  G.__puffPointerSettings = out;
  return out;
}

function setting(name: "PUFFPAI_STATE_DIR" | "PUFFPAI_DATA_DIR"): string {
  const own = (process.env[name] || "").trim();
  return own || (pointerSettings()[name] || "").trim();
}

function userStateRoot(): string {
  if (process.platform === "win32") {
    return path.join(process.env.LOCALAPPDATA || os.homedir(), "PuffPAI");
  }
  if (process.platform === "darwin") {
    return path.join(os.homedir(), "Library", "Application Support", "PuffPAI");
  }
  return path.join(
    process.env.XDG_STATE_HOME || path.join(os.homedir(), ".local", "state"),
    "puffpai",
  );
}

/** This build's state folder, absolute. Mirrors puffpai.config.get_state_dir. */
export function buildStateDir(): string {
  const root = checkoutRoot();
  const state = setting("PUFFPAI_STATE_DIR");
  if (state) return path.resolve(root, state);
  const data = setting("PUFFPAI_DATA_DIR");
  if (data) return path.join(path.resolve(root, data), "state");
  return path.resolve(userStateRoot());
}

/*
 * WHICH BUILD: the build id is worked out by the Python side ONLY
 * (puffpai.security.build_identity), which writes it with the canonical state
 * folder to <state>/build_id.json. This page reads that file and never
 * computes an id itself: Node's lower-casing and Python's disagree on some
 * folder names, and long-path prefixes, short names and junctions gave one
 * folder several ids. The id is only needed to decide
 * who owns this copy's OLD shared pairing file; without a trustworthy id that
 * decision is refused (fail closed) with a plain message, and nothing is
 * created, so no build's state is split in two.
 */
const ID_FILE = "build_id.json";
/** An empty owner record younger than this is still being written. */
const CLAIM_STALE_MS = 30_000;
const IN_PROGRESS = "in-progress";

/** Thrown when this page cannot yet tell which pairing file is its own. */
export class PairingNotReady extends Error {}

const NOT_RECORDED =
  "This bot has not written down which build it is yet, so the web page " +
  "cannot tell which pairing code is its own. Start the bot once (not only " +
  "the web page), then try again.";
const CLAIM_BUSY =
  "Another copy of this bot is setting up its pairing right now. Try again " +
  "in a moment.";

/**
 * The build id Python recorded in <stateDir>/build_id.json, or null.
 * Null too when the file names a DIFFERENT folder (a state folder copied from
 * another build): the recorded folder and this one must be the same folder on
 * disk (same device and file id), however each is spelt.
 */
export function recordedBuildId(stateDir: string): string | null {
  let rec: { build?: unknown; state_dir?: unknown };
  try {
    rec = JSON.parse(fs.readFileSync(path.join(stateDir, ID_FILE), "utf8"));
  } catch {
    return null;
  }
  const build = rec?.build;
  const recorded = rec?.state_dir;
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

function readOwner(file: string): string | null {
  let raw: string;
  try {
    raw = fs.readFileSync(file, "utf8");
  } catch (err) {
    if ((err as NodeJS.ErrnoException)?.code === "ENOENT") return null;
    return "";
  }
  if (!raw.trim()) {
    // Created and not yet written (an older writer): wait for it, never guess.
    try {
      return Date.now() - fs.statSync(file).mtimeMs < CLAIM_STALE_MS ? IN_PROGRESS : "";
    } catch {
      return "";
    }
  }
  try {
    const build = (JSON.parse(raw) as { build?: unknown })?.build;
    return typeof build === "string" && build.length === 16 ? build : "";
  } catch {
    return "";
  }
}

/**
 * Create the owner record WHOLE, only if it does not exist: written to a
 * temporary file, then linked into place (the link fails if the record is
 * already there), so nobody ever reads a half-written one. Where links are
 * not possible, an exclusive create and one write. True when this call made it.
 */
function claimOwner(ownerFile: string, record: string): boolean {
  const tmp = path.join(
    path.dirname(ownerFile),
    `.${OWNER_FILE}.${process.pid}.${crypto.randomBytes(4).toString("hex")}.tmp`,
  );
  let linked: boolean | null = null;
  try {
    fs.writeFileSync(tmp, record, { flag: "wx", mode: 0o600 });
    try {
      fs.linkSync(tmp, ownerFile);
      linked = true;
    } catch (err) {
      if ((err as NodeJS.ErrnoException)?.code === "EEXIST") linked = false;
    }
  } finally {
    try {
      fs.unlinkSync(tmp);
    } catch {
      /* already gone */
    }
  }
  if (linked !== null) return linked;
  try {
    fs.writeFileSync(ownerFile, record, { flag: "wx", mode: 0o600 });
    return true;
  } catch (err) {
    if ((err as NodeJS.ErrnoException)?.code === "EEXIST") return false;
    throw err;
  }
}

/**
 * May this build keep using the code copy's old pairing file? Yes when it
 * owns it, or when nobody does and it becomes the owner now (exactly one of
 * two builds starting together wins). A damaged owner record says no to
 * everybody -- fail closed, the build gets its own state. Throws
 * PairingNotReady when this build's id is not recorded yet, or while another
 * process is still writing the owner record.
 */
function keepsLegacy(legacy: string, stateDir: string): boolean {
  const me = recordedBuildId(stateDir);
  if (!me) throw new PairingNotReady(NOT_RECORDED);
  const ownerFile = path.join(path.dirname(legacy), OWNER_FILE);
  let owner = readOwner(ownerFile);
  if (owner === null) {
    const record = JSON.stringify(
      { version: 1, build: me, claimedAt: new Date().toISOString() },
      null,
      2,
    );
    try {
      if (claimOwner(ownerFile, record)) return true;
    } catch {
      return false;
    }
    owner = readOwner(ownerFile);
  }
  if (owner === IN_PROGRESS) throw new PairingNotReady(CLAIM_BUSY);
  return owner === me;
}

/** Where THIS build's pairing state lives. See the note above. */
export function pairingStatePath(): string {
  if (process.env.PUFF_PAIRING_STATE) return process.env.PUFF_PAIRING_STATE;
  const state = buildStateDir();
  const own = path.join(state, PAIRING_DIR, "pairing.json");
  if (fs.existsSync(own)) return own;
  const legacy = path.join(process.cwd(), "data", "pairing.json");
  // Already serving the old file and it vanished (deleted, or a blip): keep
  // the old path so getPairingState's last-known-good rule applies, instead
  // of minting a new secret elsewhere and un-pairing everyone for good.
  if (G.__puffPairingStatePath === legacy) return legacy;
  if (fs.existsSync(legacy) && keepsLegacy(legacy, state)) return legacy;
  return own;
}

function statePath(): string {
  return pairingStatePath();
}

function randomCode(): string {
  // Rejection-free because 256 % 32 === 0 -- every byte maps uniformly.
  const bytes = crypto.randomBytes(CODE_LENGTH);
  let out = "";
  for (let i = 0; i < CODE_LENGTH; i++) {
    out += CODE_ALPHABET[bytes[i] % CODE_ALPHABET.length];
  }
  return out;
}

function freshState(): PairingState {
  const now = new Date().toISOString();
  return {
    version: 1,
    secret: crypto.randomBytes(SECRET_BYTES).toString("hex"),
    code: randomCode(),
    createdAt: now,
    codeRotatedAt: now,
  };
}

function isValidState(v: unknown): v is PairingState {
  if (!v || typeof v !== "object") return false;
  const s = v as Partial<PairingState>;
  return (
    typeof s.secret === "string" &&
    /^[0-9a-f]{64}$/.test(s.secret) &&
    typeof s.code === "string" &&
    s.code.length === CODE_LENGTH
  );
}

function writeState(file: string, state: PairingState): void {
  const dir = path.dirname(file);
  fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
  // Write to a temp file then rename so a crash cannot leave a truncated state.
  // mode 0600: the secret is the only thing standing between a LAN device and
  // the appliance's API key. (No-op on Windows; real on Linux and macOS.)
  const tmp = `${file}.${process.pid}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(state, null, 2), { mode: 0o600 });
  try {
    fs.chmodSync(tmp, 0o600);
  } catch {
    /* Windows: chmod is a no-op / may throw. Not fatal. */
  }
  fs.renameSync(tmp, file);
}

/**
 * Read the state file.
 *
 * Returns null ONLY when the file genuinely does not exist (ENOENT) -- the
 * first-boot case, where the caller may mint a fresh secret. Every other
 * problem (EACCES, EIO, a half-written file, JSON that parses but fails
 * isValidState) THROWS.
 *
 * WHY THROWING BEATS SELF-HEALING HERE
 * ------------------------------------
 * The obvious-looking alternative -- "unreadable? generate a new state" -- is a
 * silent mass-revocation: a new secret invalidates every paired device at once,
 * with no log line saying so. A permission blip or a data volume that came up
 * late would spend the owner's entire pairing state. Throwing instead surfaces
 * as 401s (isPaired catches and returns false) and a 500 on the pairing routes:
 * loud, recoverable, and non-destructive. The secret is only ever created when
 * there is provably nothing to destroy.
 */
function loadState(file: string): PairingState | null {
  let raw: string;
  try {
    raw = fs.readFileSync(file, "utf8");
  } catch (err) {
    if ((err as NodeJS.ErrnoException)?.code === "ENOENT") return null;
    throw err;
  }
  const parsed: unknown = JSON.parse(raw);
  if (!isValidState(parsed)) {
    throw new Error(`pairing state at ${file} is present but malformed`);
  }
  return parsed;
}

/**
 * Load pairing state, generating and persisting it on first use.
 *
 * Read FRESH from disk on every call (the file is a few hundred bytes and the
 * write path is atomic). An in-memory copy that outlives a rotation is a
 * correctness bug: `npm run pair:code -- --rotate` rewrites this file from the
 * owner's terminal, and if this function served a cached copy the running
 * server would then reject the very code it just printed.
 *
 * The globalThis copy is kept only as a DISASTER FALLBACK: if the file becomes
 * unreadable while the process is running (unmounted volume, permission blip),
 * serve the last known-good state rather than throwing and 401ing everything.
 * It is never used to shadow a readable file.
 *
 * Fails closed: if the state cannot be read or persisted the error propagates,
 * and callers turn that into a 401/500 rather than silently running
 * unauthenticated -- or, worse, minting a fresh secret (see loadState).
 */
export function getPairingState(): PairingState {
  const file = statePath();
  const cached = G.__puffPairingStatePath === file ? G.__puffPairingState : undefined;

  let state: PairingState | null;
  try {
    state = loadState(file);
  } catch (err) {
    if (cached) return cached;
    throw err;
  }

  if (!state) {
    // ENOENT. If we have served this path before, the file was deleted out from
    // under us -- that is the documented nuclear revoke, and it takes effect on
    // the next restart. Do not fight it by rewriting the file, and do not mint a
    // new secret mid-flight either; keep serving what we had.
    if (cached) return cached;
    state = freshState();
    writeState(file, state);
  }

  G.__puffPairingState = state;
  G.__puffPairingStatePath = file;
  return state;
}

/* ── paired-device registry ────────────────────────────────────────── */

/**
 * One row per device that may present a cookie. This file is the revocation
 * list: nothing here grants authority (the HMAC does that), it only withholds
 * it. Remove a row and that device's cookie stops verifying on the next
 * request -- no restart, no secret change, no other device affected.
 *
 * `id` is public: it is shown to paired callers by GET /api/pair/devices so the
 * pairing page can revoke by it. It is NOT a credential; knowing an id gets you
 * nothing without a cookie signed over it.
 *
 * FAIL-CLOSED ASYMMETRY: a missing or corrupt devices.json makes every cookie
 * fail membership -- no registry, no entry. The registry can only ever REMOVE
 * authority, never grant it. devices.json therefore joins pairing.json on the
 * never-delete list.
 */
export interface PairedDevice {
  id: string;
  /** Human label. Sanitised, may be empty. Never trusted. */
  name: string;
  createdAt: string;
  /** Best-effort, hour-granular. */
  lastSeenAt: string | null;
}

interface PairedDeviceFile {
  version: 1;
  devices: PairedDevice[];
}

/**
 * Where the registry lives. A SIBLING of the pairing state: one 0700 directory,
 * one backup story, one thing to remember not to delete.
 *   - PUFF_PAIRED_DEVICES  explicit override, or
 *   - <dir of this build's pairing.json>/devices.json -- the build's own
 *     <state>/pairing/ folder, or (for the one build that kept it) the old
 *     shared data/ folder, so its paired devices stay paired.
 */
function devicesPath(): string {
  if (process.env.PUFF_PAIRED_DEVICES) return process.env.PUFF_PAIRED_DEVICES;
  return path.join(path.dirname(statePath()), "devices.json");
}

function isPairedDeviceRecord(v: unknown): v is PairedDevice {
  if (!v || typeof v !== "object") return false;
  const r = v as Partial<PairedDevice>;
  return (
    typeof r.id === "string" &&
    DEVICE_ID_RE.test(r.id) &&
    typeof r.name === "string" &&
    typeof r.createdAt === "string"
  );
}

/**
 * Read the registry for an AUTHORISATION decision. Missing, unreadable or
 * corrupt -> an EMPTY list, which makes every cookie fail membership. That is
 * the fail-closed direction: a registry we cannot read authorises nobody.
 *
 * ONLY for read-only authorisation (findPairedDevice -> cookie verification).
 * Anything that writes must use readPairedDevicesForWrite() instead -- see the
 * long note there for why collapsing a read error to [] is safe on this path
 * and destroys the device list on that one.
 *
 * Records that fail validation are dropped individually rather than poisoning
 * the whole file. Read fresh every time (the file is tiny, the write path is
 * atomic) so a revocation takes effect on the very next request instead of
 * whenever a cache happened to expire.
 */
function readPairedDevices(): PairedDeviceFile {
  try {
    return readPairedDevicesForWrite();
  } catch {
    /* missing or unreadable -> empty, which authorises nobody */
  }
  return { version: 1, devices: [] };
}

/**
 * Read the registry for a READ-MODIFY-WRITE. Same parse, opposite error policy:
 * this one THROWS rather than pretending the registry is empty.
 *
 * WHY THE TWO POLICIES MUST DIFFER (this is a real bug that shipped once
 * upstream, and the reason it is copied here with its comment intact)
 * -------------------------------------------------------------------------
 * readPairedDevices() collapsing every failure to [] is exactly right when the
 * question is "may this cookie in?" -- an unreadable registry authorises nobody.
 * It is catastrophic when the question is "what should I write back?", because
 * the answer becomes "just the one row I am adding" and the write ATOMICALLY
 * REPLACES every other device with it. One transient read failure (EMFILE under
 * load, a slow mount, an EIO blip) during a pair would silently un-pair every
 * other device on the appliance.
 *
 * So: ENOENT means the registry genuinely does not exist yet (first device ever)
 * and starting from an empty list is correct and lossless. EVERY other outcome
 * -- EACCES, EMFILE, EIO, EISDIR, JSON that will not parse, a shape without a
 * devices array -- means there may be rows we cannot see, and the only safe move
 * is to abort the mutation and write nothing. Callers turn the throw into "no
 * renewal this request" (renewalCookieFor, already wrapped) or a 500
 * (POST /api/pair, revoke), never into data loss.
 *
 * This is the same rule loadState() applies to pairing.json's secret, for the
 * same reason: never destroy credentials to recover from a read error.
 */
function readPairedDevicesForWrite(): PairedDeviceFile {
  const file = devicesPath();
  let raw: string;
  try {
    raw = fs.readFileSync(file, "utf8");
  } catch (err) {
    if ((err as NodeJS.ErrnoException)?.code === "ENOENT") {
      return { version: 1, devices: [] };
    }
    throw err;
  }
  const parsed: unknown = JSON.parse(raw);
  if (
    !parsed ||
    typeof parsed !== "object" ||
    !Array.isArray((parsed as PairedDeviceFile).devices)
  ) {
    throw new Error(`paired-device registry at ${file} is present but malformed`);
  }
  return {
    version: 1,
    devices: (parsed as PairedDeviceFile).devices.filter(isPairedDeviceRecord),
  };
}

/** Atomic 0600 write (temp + rename), same discipline as pairing.json. */
function writePairedDevices(state: PairedDeviceFile): void {
  const file = devicesPath();
  const dir = path.dirname(file);
  fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
  const tmp = `${file}.${process.pid}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(state, null, 2), { mode: 0o600 });
  try {
    fs.chmodSync(tmp, 0o600);
  } catch {
    /* Windows: chmod is a no-op / may throw. Not fatal. */
  }
  fs.renameSync(tmp, file);
}

/** Strip control chars, collapse whitespace, cap length. Never trusted input. */
export function sanitizeDeviceName(input: unknown): string {
  if (typeof input !== "string") return "";
  return input
    .replace(/[\x00-\x1f\x7f]/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 64);
}

/**
 * Every registered device. Safe to hand to a paired caller as-is.
 *
 * Uses the STRICT read deliberately, even though this only feeds a display.
 * A missing registry (ENOENT) still yields an honest empty list, but a registry
 * we cannot read or parse raises, and the route turns that into a 500 rather
 * than rendering "no devices" -- which would tell the owner their devices had
 * been wiped when in fact the file was merely unreadable.
 */
export function listPairedDevices(): PairedDevice[] {
  return readPairedDevicesForWrite().devices;
}

/** The record for `id`, or null. Shape-checks the id before looking. */
export function findPairedDevice(id: string): PairedDevice | null {
  if (typeof id !== "string" || !DEVICE_ID_RE.test(id)) return null;
  for (const d of readPairedDevices().devices) {
    if (d.id === id) return d;
  }
  return null;
}

/**
 * Register a new device and return its record. Throws if the registry cannot be
 * written -- the caller must NOT hand out a cookie it failed to register,
 * because that cookie would fail membership on its very next use and the user
 * would be left "paired" but 401ing.
 */
export function addPairedDevice(name: unknown, now = Date.now()): PairedDevice {
  const record: PairedDevice = {
    id: crypto.randomBytes(DEVICE_ID_BYTES).toString("base64url"),
    name: sanitizeDeviceName(name),
    createdAt: new Date(now).toISOString(),
    lastSeenAt: new Date(now).toISOString(),
  };
  // STRICT read: if the existing rows cannot be read, abort rather than write a
  // one-row file over them. See readPairedDevicesForWrite.
  const state = readPairedDevicesForWrite();
  state.devices.push(record);
  writePairedDevices(state);
  // Seed the throttle so the request that just paired does not immediately
  // rewrite the file it created.
  (G.__puffDeviceSeen ??= new Map()).set(record.id, now);
  return record;
}

/**
 * Revoke by id -- removes the row, which kills that device's cookie on its next
 * request. Returns false if the id is unknown or malformed.
 *
 * Errors are NOT swallowed: a revoke that silently failed to persist would be
 * the worst possible lie to tell the owner, so the caller turns a throw into a
 * 500 rather than reporting success. That includes an unreadable registry --
 * answering a confident "not_found" for a device that is really still there and
 * still authorised is the same lie wearing a different status code.
 */
export function revokePairedDevice(id: string): boolean {
  if (typeof id !== "string" || !DEVICE_ID_RE.test(id)) return false;
  const state = readPairedDevicesForWrite();
  const before = state.devices.length;
  state.devices = state.devices.filter((d) => d.id !== id);
  if (state.devices.length === before) return false;
  writePairedDevices(state);
  G.__puffDeviceSeen?.delete(id);
  return true;
}

/**
 * Best-effort "last seen" stamp, throttled to one write per device per hour
 * (see LAST_SEEN_THROTTLE_MS). Every failure is swallowed: an unwritable
 * registry must never turn a valid cookie into a 401. The throttle map lives in
 * memory and resets on restart, which costs at most one extra write per device
 * per boot.
 */
function touchPairedDevice(id: string, now: number): void {
  try {
    const seen = (G.__puffDeviceSeen ??= new Map());
    const last = seen.get(id);
    if (last !== undefined && now - last < LAST_SEEN_THROTTLE_MS) return;
    // Claim the slot BEFORE writing. If the write throws we still hold off for
    // an hour rather than retrying the failing write on every single request.
    seen.set(id, now);

    // STRICT read: a stamp must never be the thing that rewrites the registry
    // from a failed read. A throw here is swallowed by the catch below, which
    // is exactly right -- losing an hour of "last seen" precision is free,
    // losing the device list is not.
    const state = readPairedDevicesForWrite();
    const target = state.devices.find((d) => d.id === id);
    if (!target) return;
    target.lastSeenAt = new Date(now).toISOString();
    writePairedDevices(state);
  } catch {
    /* a stamp is a nicety; never let it affect authorisation */
  }
}

/* ── codes ─────────────────────────────────────────────────────────── */

/** "7XK4P2M9" -> "7XK4-P2M9" for display. */
export function formatCode(code: string): string {
  const groups: string[] = [];
  for (let i = 0; i < code.length; i += CODE_GROUP) {
    groups.push(code.slice(i, i + CODE_GROUP));
  }
  return groups.join("-");
}

/**
 * Strip dashes/spaces/case. Characters outside the alphabet (including the
 * excluded confusables 0/O/1/I) are dropped rather than guessed at -- dropping
 * them changes the length, so the code simply fails to verify. Guessing which
 * one the user meant would widen the accepted-code space.
 */
export function normalizeCode(input: string): string {
  const upper = String(input || "").toUpperCase();
  let out = "";
  for (let i = 0; i < upper.length && out.length < 64; i++) {
    if (CODE_ALPHABET.indexOf(upper[i]) >= 0) out += upper[i];
  }
  return out;
}

/* ── code time-to-live ─────────────────────────────────────────────── */

/**
 * How long a pairing code stays redeemable after it is rotated. One hour.
 *
 * A code is a bearer credential that gets printed on a terminal, read aloud,
 * and photographed. It should behave like one: without a TTL, "valid until
 * somebody rotates it" means "valid forever", and the code from the day the box
 * was installed still pairs a stranger's phone a year later.
 *
 * Deliberately NOT env-tunable -- a knob here is a knob an operator can set to a
 * year, which is exactly the state we are avoiding.
 *
 * Expiry is derived from the existing `codeRotatedAt`, so pairing.json keeps
 * exactly the fields it has and a rollback still passes isValidState(). A file
 * with a missing or unparseable codeRotatedAt is treated as already expired,
 * which self-heals on the next display fetch.
 */
export const PAIR_CODE_TTL_SEC = 60 * 60;

/** Epoch ms at which the current code stops being redeemable. */
function codeExpiresAtMs(state: PairingState): number {
  const rotated = Date.parse(state.codeRotatedAt || "");
  // Unparseable/absent -> already expired. Fail closed: an unreadable rotation
  // timestamp must never be read as "valid forever".
  if (!Number.isFinite(rotated)) return 0;
  return rotated + PAIR_CODE_TTL_SEC * 1000;
}

function isCodeExpired(state: PairingState, now: number): boolean {
  return now >= codeExpiresAtMs(state);
}

export interface PairingCodeInfo {
  /** Formatted for display, e.g. "7XK4-P2M9". */
  code: string;
  rotatedAt: string;
  /** ISO 8601. After this instant the code no longer redeems. */
  expiresAt: string;
}

/**
 * The code to put on a screen, plus when it dies.
 *
 * LAZILY ROTATES an expired code before returning it, so whatever is on screen
 * is redeemable. Nothing expires "at" the user.
 *
 * Note the asymmetry with verifyCode(), which does NOT rotate: only a paired
 * caller can reach a display path, so only a paired caller can cause a
 * rotation. A stranger guessing codes cannot spin the wheel.
 */
export function getPairingCodeInfo(now = Date.now()): PairingCodeInfo {
  let state = getPairingState();
  if (isCodeExpired(state, now)) {
    rotatePairingCode(now);
    state = getPairingState();
  }
  return {
    code: formatCode(state.code),
    rotatedAt: state.codeRotatedAt,
    expiresAt: new Date(codeExpiresAtMs(state)).toISOString(),
  };
}

/**
 * Rotate the pairing code only. The HMAC secret is deliberately NOT rotated, so
 * devices that are already paired keep working -- rotation revokes the ability
 * to pair *new* devices with the old code, nothing more.
 * (To revoke every device, delete the state file; a fresh secret invalidates
 * all cookies.)
 */
export function rotatePairingCode(now = Date.now()): string {
  const state = getPairingState();
  // Spread first so every existing field survives verbatim. pairing.json is
  // append-only by policy: a rollback to a previous build must still parse it.
  const next: PairingState = {
    ...state,
    code: randomCode(),
    codeRotatedAt: new Date(now).toISOString(),
  };
  writeState(statePath(), next);
  G.__puffPairingState = next;
  return formatCode(next.code);
}

/* ── constant-time helpers ─────────────────────────────────────────── */

function timingSafeEqualStr(a: string, b: string): boolean {
  const ab = Buffer.from(a, "utf8");
  const bb = Buffer.from(b, "utf8");
  if (ab.length !== bb.length) {
    // Compare against self so the length-mismatch path costs roughly the same
    // as a real comparison; the length itself is still observable, which is
    // fine -- the code length is public.
    crypto.timingSafeEqual(ab, ab);
    return false;
  }
  return crypto.timingSafeEqual(ab, bb);
}

/**
 * Constant-time check of a user-supplied pairing code.
 *
 * An EXPIRED code is rejected exactly like a wrong one -- and, critically, on
 * the same code path: the comparison runs unconditionally and the expiry check
 * is applied afterwards as a boolean veto, never as an early return. If expiry
 * short-circuited first, an attacker could time the difference and learn "the
 * code you are guessing is stale, come back after the next rotation", which is
 * a small but free hint about the appliance's state. The caller (POST /api/pair)
 * returns the same `invalid_code` for both, so nothing downstream distinguishes
 * them either.
 *
 * Deliberately does NOT rotate on expiry -- see getPairingCodeInfo(). Letting an
 * unauthenticated guess trigger rotation would hand a stranger a lever on the
 * appliance's credential.
 */
export function verifyCode(input: string, now = Date.now()): boolean {
  const state = getPairingState();
  const candidate = normalizeCode(input);
  const expired = isCodeExpired(state, now);
  if (candidate.length !== CODE_LENGTH) {
    // Still do a comparison so a wrong-length guess is not distinguishable by
    // a fast return path.
    timingSafeEqualStr(state.code, state.code.slice(0, -1) + "X");
    return false;
  }
  const matches = timingSafeEqualStr(candidate, state.code);
  return matches && !expired;
}

/* ── device cookie ─────────────────────────────────────────────────── */

function sign(payload: string, secret: string): string {
  return crypto.createHmac("sha256", Buffer.from(secret, "hex")).update(payload).digest("hex");
}

/**
 * Cookie value: `v2.<deviceId>.<issued>.<expiry>.<hmac-sha256-hex>`, where the
 * HMAC covers the literal string `v2.<deviceId>.<issued>.<expiry>`.
 *
 * `issued` is signed, not just carried, because sliding renewal keys off it: an
 * unsigned issue time would let a device claim any age it liked, which is
 * harmless today (renewal only re-issues an already-valid cookie for a
 * still-registered device) but is exactly the kind of unsigned input that grows
 * teeth later.
 *
 * The version prefix is inside the signed payload, so a future format can never
 * be confused with this one by an attacker who reuses a mac.
 */
export function issueDeviceCookieValueV2(deviceId: string, now = Date.now()): string {
  // Refuse to sign anything that is not a well-formed id. A deviceId with a "."
  // in it would split the cookie into extra fields and could be parsed back out
  // as a different (deviceId, issued, expiry) triple, so the shape check is
  // load-bearing, not cosmetic.
  if (!DEVICE_ID_RE.test(deviceId)) {
    throw new Error("refusing to sign a malformed device id");
  }
  const state = getPairingState();
  const issued = Math.floor(now / 1000);
  const exp = issued + COOKIE_MAX_AGE_SEC;
  const payload = `${COOKIE_VERSION_V2}.${deviceId}.${issued}.${exp}`;
  return `${payload}.${sign(payload, state.secret)}`;
}

/** What a verified cookie tells us about the device carrying it. */
export interface DeviceCookieIdentity {
  version: "v2";
  deviceId: string;
  /** Epoch seconds. */
  issued: number;
  /** Epoch seconds. */
  expiry: number;
}

/**
 * Verify a device cookie and return what it identifies, or null if it does not
 * verify.
 *
 * Order: structure -> deviceId shape -> expiry -> constant-time HMAC -> registry
 * membership. Membership LAST, so an unregistered id still costs a full HMAC
 * verification and revocation is not detectable by timing.
 *
 * A three-field (legacy v1) cookie is rejected here, unlike HomeBot -- see
 * DIVERGENCE 1 at the top of the file. It falls out of the parts.length check
 * with no special case, so there is no v1 code path to accidentally re-enable.
 *
 * Side effect on a successful verify: a throttled, best-effort lastSeenAt stamp
 * (see touchPairedDevice).
 */
export function verifyDeviceCookieIdentity(
  value: string | undefined | null,
  now = Date.now(),
): DeviceCookieIdentity | null {
  if (!value) return null;
  const parts = value.split(".");
  if (parts.length !== 5) return null;

  const [version, deviceId, issuedStr, expStr, mac] = parts;
  if (version !== COOKIE_VERSION_V2) return null;
  if (!DEVICE_ID_RE.test(deviceId)) return null;
  if (!/^\d{1,12}$/.test(issuedStr)) return null;
  if (!/^\d{1,12}$/.test(expStr)) return null;
  if (!/^[0-9a-f]{64}$/.test(mac)) return null;

  const issued = parseInt(issuedStr, 10);
  const exp = parseInt(expStr, 10);
  if (!Number.isFinite(issued) || !Number.isFinite(exp)) return null;
  if (exp * 1000 <= now) return null;

  const state = getPairingState();
  const expected = sign(`${version}.${deviceId}.${issuedStr}.${expStr}`, state.secret);
  if (!timingSafeEqualStr(mac, expected)) return null;

  // Registry membership. A plain === is correct here: the id is not a secret
  // (GET /api/pair/devices shows it to any paired caller) and the HMAC above has
  // already proved the cookie is ours. Nothing to leak by comparing fast.
  if (!findPairedDevice(deviceId)) return null;

  touchPairedDevice(deviceId, now);
  return { version: "v2", deviceId, issued, expiry: exp };
}

/** Verify a device cookie value. */
export function verifyDeviceCookieValue(
  value: string | undefined | null,
  now = Date.now(),
): boolean {
  return verifyDeviceCookieIdentity(value, now) !== null;
}

/** Minimal shape shared by NextRequest and Request-with-cookies. */
export interface CookieCarrier {
  cookies?: { get(name: string): { value: string } | undefined };
  headers: Headers;
}

function readPairingCookie(req: CookieCarrier): string | undefined {
  const viaApi = req.cookies?.get(PAIRING_COOKIE)?.value;
  if (viaApi) return viaApi;
  // Fallback for plain Request objects (no NextRequest cookie jar).
  const raw = req.headers.get("cookie");
  if (!raw) return undefined;
  const parts = raw.split(";");
  for (let i = 0; i < parts.length; i++) {
    const eq = parts[i].indexOf("=");
    if (eq < 0) continue;
    if (parts[i].slice(0, eq).trim() === PAIRING_COOKIE) {
      return decodeURIComponent(parts[i].slice(eq + 1).trim());
    }
  }
  return undefined;
}

/** True if this request carries a valid, unexpired, registered device cookie. */
export function isPaired(req: CookieCarrier): boolean {
  try {
    return verifyDeviceCookieValue(readPairingCookie(req));
  } catch {
    // Unreadable state file, bad secret, anything: fail closed.
    return false;
  }
}

/** The registered device id this request is carrying, or null. */
export function pairedDeviceIdFor(req: CookieCarrier, now = Date.now()): string | null {
  try {
    return verifyDeviceCookieIdentity(readPairingCookie(req), now)?.deviceId ?? null;
  } catch {
    return null;
  }
}

/* ── backend session identity ──────────────────────────────────────── */

/**
 * Domain separator. Mixed into the HMAC so a value derived here can never
 * collide with a cookie MAC, which is signed over `v2.<id>.<issued>.<expiry>`
 * with the same key. Bump the version suffix if the derivation ever changes.
 */
const DEVICE_SESSION_LABEL = "puff-chat-session:v1:";

/**
 * The value the proxy asserts to the backend as `X-Puff-Device-Id`.
 *
 * WHY NOT THE deviceId ITSELF: a paired caller can read every registered
 * deviceId out of `GET /api/pair/devices`. The backend keys its 300-second
 * vault-unlocked chat session on this value, so if the raw id ever leaked past
 * the proxy's header filter -- a future route that relays client headers, a
 * misconfigured reverse proxy in front of the dashboard -- a household member
 * would be able to name someone else's session. An HMAC over the pairing
 * secret is not guessable from the id, so knowing the id buys nothing.
 *
 * WHY NOT THE COOKIE: the cookie is the credential. It must not be copied into
 * an upstream header where it can land in a backend log.
 *
 * Stable for the life of the device row: same deviceId -> same value across
 * requests, restarts and cookie renewals (the issued/expiry fields are
 * deliberately NOT mixed in, or renewal would silently reset the unlock
 * window). Revoking the device kills the cookie, so the value becomes
 * unreachable rather than needing rotation.
 *
 * 32 hex chars = 128 bits, which is collision-free for any household and fits
 * the backend's `^[A-Za-z0-9_-]{8,64}$` shape check.
 */
export function deviceSessionId(deviceId: string): string {
  if (!DEVICE_ID_RE.test(deviceId)) {
    throw new Error("deviceSessionId: malformed deviceId");
  }
  const state = getPairingState();
  return sign(`${DEVICE_SESSION_LABEL}${deviceId}`, state.secret).slice(0, 32);
}

/**
 * The derived session id for the VERIFIED pairing cookie on this request, or
 * null when the request carries no valid cookie. Null means "assert nothing":
 * the proxy sends `X-Puff-Anonymous` in place of the identity header (see
 * proxyHeaders.ts) and the backend gives the request a single-use session key
 * with no vault access, rather than the shared per-socket key every other
 * unidentified caller would land on.
 *
 * NO EAGER COOKIE UPGRADE HERE, unlike HomeBot's proxyDeviceSessionFor(): this
 * repo has never issued a v1 cookie (see DIVERGENCE 1 at the top of the file),
 * so there is no identity-less-but-paired population to upgrade before the first
 * proxied chat call. A cookie either verifies as v2 — which requires registry
 * membership, and therefore names a device — or it does not verify at all. The
 * remaining ways to reach null are genuinely unidentified: no cookie (reachable
 * with `PUFF_PAIRING_ENFORCE=off`), a bad MAC, an expiry, or a device revoked
 * between the pairing gate and this call. If a legacy format is ever added here,
 * port HomeBot's eager upgrade with it.
 */
export function deviceSessionIdFor(req: CookieCarrier, now = Date.now()): string | null {
  try {
    const id = pairedDeviceIdFor(req, now);
    return id ? deviceSessionId(id) : null;
  } catch {
    return null;
  }
}

/* ── sliding renewal ───────────────────────────────────────────────── */

/**
 * Pure decision: is a cookie issued at `issued` (epoch seconds) old enough to
 * re-issue? Extracted so the rule is testable without a request, a cookie, or a
 * state file.
 *
 * A cookie issued in the FUTURE (clock skew, or a device handed one before an
 * NTP correction) yields a negative age and is not renewed -- renewing on a
 * nonsense timestamp would just mint another nonsense timestamp.
 */
export function shouldRenewDeviceCookie(issued: number, now = Date.now()): boolean {
  if (!Number.isFinite(issued)) return false;
  const ageSec = Math.floor(now / 1000) - issued;
  return ageSec >= RENEW_AFTER_SEC;
}

/**
 * Decide whether this request's cookie should be replaced, and with what.
 * Returns cookie options to Set-Cookie, or null for "leave it alone".
 *
 * A valid cookie older than RENEW_AFTER_SEC is re-issued with the same deviceId
 * and a fresh 365-day window. Only reachable for a device that is still in the
 * registry, because that membership check is part of verification -- a revoked
 * device cannot renew its way back in.
 *
 * NEVER THROWS. Every path is wrapped, and any failure degrades to "no renewal
 * on this request" -- the request has already passed the gate and must not be
 * turned into a 500 by an optional housekeeping step.
 */
export function renewalCookieFor(req: CookieCarrier, now = Date.now()): DeviceCookieOptions | null {
  try {
    const raw = readPairingCookie(req);
    if (!raw) return null;
    const identity = verifyDeviceCookieIdentity(raw, now);
    // Not a valid cookie -> this request is unpaired or was let through on an
    // exempt path. Never Set-Cookie on that path.
    if (!identity) return null;
    if (!shouldRenewDeviceCookie(identity.issued, now)) return null;
    return deviceCookieOptions(issueDeviceCookieValueV2(identity.deviceId, now));
  } catch {
    // Unwritable registry, unreadable secret, anything at all: skip renewal.
    // The cookie the request already carries stays valid, so nothing breaks.
    return null;
  }
}

/* ── enforcement ───────────────────────────────────────────────────── */

export type EnforceMode = "off" | "all";

/**
 * PUFF_PAIRING_ENFORCE: "all" (default) or "off".
 *
 * DEFAULT IS "all" -- see DIVERGENCE 2 at the top of the file. An unset variable
 * on a fresh install means the gate is ON, which is the only defensible default
 * for a build whose first user is a stranger following a tutorial.
 *
 * "off" exists as a documented escape route: an owner who cannot reach the code
 * (no shell, lost the terminal output) can start the dashboard with the gate
 * disabled, pair, and take it out again. It is a deliberate, visible act in a
 * unit file or a shell -- not something a typo can produce, because an
 * UNRECOGNISED value falls back to "all", the restrictive choice.
 *
 * There is deliberately NO localhost/Host-header bypass: the Host header is
 * attacker-controlled, and under `next start` the peer address is not reliably
 * available to route handlers (see the note on rlKey below). A browser running
 * on the appliance itself pairs like every other device.
 */
export function enforceMode(): EnforceMode {
  const raw = (process.env.PUFF_PAIRING_ENFORCE || "").trim().toLowerCase();
  if (raw === "off") return "off";
  return "all";
}

/**
 * The ONLY /api paths that are not gated. Everything else under /api is gated,
 * including a path that routes nowhere.
 *
 * WHY THIS IS AN EXEMPT LIST AND NOT A GATED LIST
 * -----------------------------------------------
 * It used to be the other way round: four gated prefixes, and any other /api
 * path fell through to "none". That is default-ALLOW for the API surface, and
 * it was refuted exactly the way it deserved to be -- a scratch route dropped at
 * app/api/vaultfiles/route.ts proxied the backend and answered an unpaired
 * caller, with the gate test still green. The list of things that must be open
 * is short, fixed, and reviewable; the list of things that must be closed is
 * every route anyone will ever add. Enumerate the short one.
 *
 * Exact match, not a prefix, for the health endpoint: /api/health/x is a
 * route nobody has written, so it should be refused rather than inheriting an
 * exemption from its parent. /api/pair is the exception that genuinely needs a
 * subtree, because a fresh device redeems its code through /api/pair/* and has
 * no cookie to present.
 *
 * `/api/media/status` is not on this list: the route no longer exists, so the
 * exemption would name a 404. A live exemption for a path with no
 * handler is a trap -- whoever adds `app/api/media/status/route.ts` next inherits
 * an unauthenticated endpoint without ever deciding to. If that route comes
 * back, it gets a fresh argument for exemption or it gets gated.
 */
const EXEMPT_API_EXACT = ["/api/health"];

/** Never gated, at any stage. The only way a fresh device can ever pair. */
const PAIR_API_PREFIX = "/api/pair";

/** The unpaired page. Must render for a device that has no cookie. */
export const PAIR_PAGE = "/pair";

/**
 * Collapse a request path to the *effective* path the proxy will actually act
 * on, so the gate cannot be fooled by percent-encoding.
 *
 * WHY THIS EXISTS (the bug it closes)
 * -----------------------------------
 * `nextUrl.pathname` preserves percent-encoding for an encoded separator:
 * `/api/pai/knowledge%2fupload` stays literally `...knowledge%2fupload`, so a
 * raw prefix match against `/api/pai/` would still hit here, but a gate written
 * against a deeper prefix would miss -- and the catch-all proxy splits and
 * DECODES `[...path]`, forwarding the decoded path upstream with the appliance
 * key. Gate on the same bytes the proxy acts on.
 *
 * So: fully decode (looping to defeat double-encoding like `%252f`), then
 * lowercase for a case-insensitive decision. Decoding only ever REVEALS more
 * path structure, so it can only gate MORE, never less.
 *
 * A "." or ".." SEGMENT also returns null, which is the one place this file
 * improves on the module it was ported from. Without it, a path that spells a
 * dot segment inside the exempt pairing prefix -- "/api/pair", then "..", then
 * a segment naming the keyed proxy -- reads as exempt here, and the gate waves
 * it through, while the router resolves it back onto the keyed proxy.
 * Gate and router disagreeing about what a path means is exactly the bug the
 * decode loop above exists to prevent, and traversal is the other half of it.
 * Collapsing the segments instead of refusing them would also work; refusing is
 * the smaller thing to get right, because no legitimate route path on this
 * dashboard contains a dot segment in any spelling, so there is nothing to lose.
 *
 * Fails closed: malformed percent-encoding (throws), a control character, or a
 * dot segment returns null, and the caller treats null as "must be gated".
 */
function effectiveGatePath(pathname: string): string | null {
  try {
    let prev = pathname;
    // Bounded loop: undo double/triple encoding (%252f -> %2f -> /). Three
    // passes is far more than any legitimate path needs.
    for (let i = 0; i < 3; i++) {
      const dec = decodeURIComponent(prev);
      if (dec === prev) break;
      prev = dec;
    }
    // NUL / control chars never appear in a legitimate path; a decoded one is a
    // smuggling attempt. Fail closed.
    if (/[\x00-\x1f\x7f]/.test(prev)) return null;
    // Traversal. Checked AFTER the decode loop so %2e%2e and %252e%252e are
    // caught in their revealed form, and split on "\" as well as "/" because a
    // backslash is a separator to some upstream parsers even where it is not
    // one to this router.
    for (const seg of prev.split(/[/\\]/)) {
      if (seg === "." || seg === "..") return null;
    }
    return prev.toLowerCase();
  } catch {
    // Malformed escape (e.g. "%zz", lone "%") -> fail closed.
    return null;
  }
}

/**
 * Does this path look like a static asset rather than a page?
 *
 * A page is "no extension" or ".html". Everything else -- .png, .svg, .js,
 * .woff2, .json -- is a build or public asset that carries no household data
 * and no credential, and redirecting one to /pair would break the pair page's
 * own styling and fonts before the owner could type anything.
 *
 * Note what is NOT on this list: the app pages in public/ are .html and ARE
 * page-gated, because they read household data through the keyed proxy.
 * Anything the check gets wrong in the permissive direction is an asset served
 * to an unpaired client; anything it gets wrong in the restrictive direction is
 * a redirect. Neither reaches the backend, which is what the API gate is for.
 */
function isStaticAsset(eff: string): boolean {
  const lastSlash = eff.lastIndexOf("/");
  const leaf = lastSlash >= 0 ? eff.slice(lastSlash + 1) : eff;
  const dot = leaf.lastIndexOf(".");
  if (dot <= 0) return false; // no extension, or a dotfile -- treat as a page
  const ext = leaf.slice(dot + 1);
  return ext !== "html" && ext !== "htm";
}

/**
 * What kind of gate applies to this request.
 *
 *   "none"  let it through
 *   "api"   401 {"error":"pairing_required"} if unpaired
 *   "page"  302 to /pair if unpaired
 *
 * Everything that is not an exempt API route, a static asset, or the pair page
 * itself is gated. Default-deny for pages is the point: a new page added later
 * is protected by existing, not by somebody remembering to add it to a list.
 */
export type GateKind = "none" | "api" | "page";

export function gateKindFor(pathname: string): GateKind {
  if (enforceMode() === "off") return "none";

  const eff = effectiveGatePath(pathname);
  // Undecodable / malformed path while the gate is active -> fail closed. It is
  // not a page (a redirect would be nonsense), so refuse it as an API call.
  if (eff === null) return "api";

  // The pairing endpoints must always be reachable, or a fresh device could
  // never pair. Checked on the decoded+casefolded path so an encoded spelling
  // cannot dodge the exemption either. A request that routes to a keyed proxy
  // always decodes to an /api/pai|knowledge|ha|camera prefix, never /api/pair/,
  // so this exemption cannot be abused to reach the key.
  if (eff === PAIR_API_PREFIX || eff.indexOf(PAIR_API_PREFIX + "/") === 0) {
    return "none";
  }

  // DEFAULT-DENY FOR THE WHOLE API SURFACE. Two endpoints are open by name --
  // /api/health so a supervisor probe with no cookie can answer, and
  // /api/media/status because it pings a loopback liveness endpoint and carries
  // nothing. Every other /api path is gated, whether or not a route file exists
  // for it and whether or not anyone remembered to list it. A route added next
  // month is closed on the day it is written.
  if (eff === "/api" || eff.indexOf("/api/") === 0) {
    for (let i = 0; i < EXEMPT_API_EXACT.length; i++) {
      if (eff === EXEMPT_API_EXACT[i]) return "none";
    }
    return "api";
  }

  if (eff === PAIR_PAGE || eff.indexOf(PAIR_PAGE + "/") === 0) return "none";
  if (isStaticAsset(eff)) return "none";
  return "page";
}

/**
 * Should this request require pairing at all? Kept as a boolean helper for the
 * per-route guards, which do not care which shape the refusal takes.
 */
export function pathRequiresPairing(pathname: string): boolean {
  return gateKindFor(pathname) !== "none";
}

const UNPAIRED_BODY = JSON.stringify({ error: "pairing_required" });

export function unpairedResponse(): Response {
  return new Response(UNPAIRED_BODY, {
    status: 401,
    headers: {
      "Content-Type": "application/json",
      "Cache-Control": "no-store",
    },
  });
}

/**
 * Route-handler guard. Returns a 401 Response to return immediately, or null to
 * continue. A second layer behind the middleware, so a route stays protected
 * even if the matcher is ever narrowed by mistake.
 *
 *   const denied = requirePairing(req);
 *   if (denied) return denied;
 */
export function requirePairing(
  req: CookieCarrier & {
    nextUrl?: { pathname: string };
    url?: string;
    method?: string;
  },
): Response | null {
  let pathname = req.nextUrl?.pathname;
  if (!pathname && req.url) {
    try {
      pathname = new URL(req.url).pathname;
    } catch {
      pathname = undefined;
    }
  }
  // Unknown path -> treat as protected (fail closed).
  if (pathname && !pathRequiresPairing(pathname)) return null;
  if (isPaired(req)) return null;
  return unpairedResponse();
}

/* ── failed-attempt rate limiting ──────────────────────────────────── */

const RL_WINDOW_MS = 60_000;
const RL_MAX_FAILS = 5;

/**
 * In-memory failed-pairing counter.
 *
 * Single-process `next start`, so a plain Map is enough; it resets on restart,
 * which is an accepted weakness (an attacker who can restart the service has
 * already won). Not shared across workers if the deployment ever scales out.
 *
 * PER-IP BUCKETING IS OFF BY DEFAULT, ON PURPOSE. Under bare `next start` there
 * is no trusted reverse proxy, so x-forwarded-for / x-real-ip are just
 * attacker-supplied strings: bucketing on them would let a guesser mint a fresh
 * 5-attempt budget per request simply by rotating the header, i.e. no rate limit
 * at all. Next itself fills x-forwarded-for from the socket ONLY when the client
 * did not send one (`??=`), and the result carries no marker distinguishing
 * "synthesised" from "typed by the attacker". So unless PUFF_TRUST_PROXY=1 is
 * explicitly set, every request shares one global bucket -- guessing is capped
 * at 5/minute for the whole appliance.
 *
 * Trade-off, stated plainly: any LAN device can then keep pairing locked out a
 * minute at a time (a nuisance-level DoS on *pairing only*; paired devices are
 * unaffected). Against a 32^8 (~1.1e12) code space with a one-hour TTL, 5/min
 * gives an attacker ~300 guesses per code, about one in four billion, after
 * which the code they were grinding no longer exists. The global cap costs
 * nothing in security terms.
 *
 * Set PUFF_TRUST_PROXY=1 ONLY if the dashboard is fronted by a reverse proxy
 * that overwrites x-forwarded-for.
 */
function rlKey(req: CookieCarrier): string {
  if (process.env.PUFF_TRUST_PROXY !== "1") return "global";
  const xff = req.headers.get("x-forwarded-for");
  if (xff) return `xff:${xff.split(",")[0].trim()}`;
  const real = req.headers.get("x-real-ip");
  if (real) return `ip:${real.trim()}`;
  return "unknown";
}

/** True if this client is currently rate-limited (too many recent failures). */
export function isRateLimited(req: CookieCarrier, now = Date.now()): boolean {
  const map = (G.__puffPairFails ??= new Map());
  const entry = map.get(rlKey(req));
  if (!entry) return false;
  if (now - entry.windowStart >= RL_WINDOW_MS) return false;
  return entry.count >= RL_MAX_FAILS;
}

export function recordPairFailure(req: CookieCarrier, now = Date.now()): void {
  const map = (G.__puffPairFails ??= new Map());
  const key = rlKey(req);
  const entry = map.get(key);
  if (!entry || now - entry.windowStart >= RL_WINDOW_MS) {
    map.set(key, { count: 1, windowStart: now });
    return;
  }
  entry.count += 1;
}

export function clearPairFailures(req: CookieCarrier): void {
  G.__puffPairFails?.delete(rlKey(req));
}

/* ── cookie attributes ─────────────────────────────────────────────── */

export interface DeviceCookieOptions {
  name: string;
  value: string;
  httpOnly: true;
  sameSite: "lax";
  path: "/";
  maxAge: number;
  secure: boolean;
}

/**
 * Cookie attributes for the paired-device cookie.
 *
 * `secure` is FALSE by default, deliberately. The appliance serves plain HTTP
 * on the LAN -- there is no TLS story yet. Setting Secure would make the browser
 * drop the cookie outright and pairing would silently never work. The
 * consequence is that the cookie is sniffable by anyone on the local network;
 * that is a known, accepted residual risk documented at the top of this file.
 *
 * PUFF_COOKIE_SECURE=1 flips it on for a deployment that HAS put the dashboard
 * behind TLS. It is opt-in rather than auto-detected because "did this request
 * arrive over https" is answered by client-controlled headers under bare
 * `next start`, and guessing wrong in the ON direction silently breaks pairing
 * with no error anyone can see.
 *
 * SameSite=Lax means a malicious site cannot forge cross-site POST/PUT/DELETE
 * against the proxies with this cookie attached (top-level GET navigations do
 * still carry it, which is what makes the redirect-to-/pair flow work).
 */
function cookieSecure(): boolean {
  return process.env.PUFF_COOKIE_SECURE === "1";
}

export function deviceCookieOptions(value: string): DeviceCookieOptions {
  return {
    name: PAIRING_COOKIE,
    value,
    httpOnly: true,
    sameSite: "lax",
    path: "/",
    maxAge: COOKIE_MAX_AGE_SEC,
    secure: cookieSecure(),
  };
}

/**
 * Attributes that DELETE the device cookie. Used when a device revokes itself,
 * so the browser stops sending a cookie that is about to stop verifying anyway.
 *
 * Identical name/path/attributes to deviceCookieOptions -- a Set-Cookie only
 * overwrites a cookie it matches on, so a mismatched path here would leave the
 * old cookie in place and the screen would keep sending a dead credential.
 * Clearing is a courtesy, not the control: the authority is gone the moment the
 * registry row is, whether or not the browser cooperates.
 */
export function clearedDeviceCookieOptions(): DeviceCookieOptions {
  return {
    name: PAIRING_COOKIE,
    value: "",
    httpOnly: true,
    sameSite: "lax",
    path: "/",
    maxAge: 0,
    secure: cookieSecure(),
  };
}

/* ── CSRF: same-origin check for state-changing pairing calls ──────── */

/**
 * Reject cross-site state-changing requests. The Origin header is set by the
 * browser and cannot be forged by page JavaScript, so comparing it against the
 * request's own Host is a cheap, reliable CSRF check that works no matter which
 * hostname or IP the appliance is reached by.
 *
 * Requests with no Origin at all (curl, a native app) are allowed -- they are
 * not browser-driven and therefore not CSRF. That is the same contract
 * crossSite.ts states for the proxy, and the same one HomeBot ships.
 */
export function isSameOrigin(req: CookieCarrier): boolean {
  const origin = req.headers.get("origin");
  if (!origin) return true;
  const host = req.headers.get("host");
  if (!host) return false;
  try {
    return new URL(origin).host === host;
  } catch {
    return false;
  }
}
