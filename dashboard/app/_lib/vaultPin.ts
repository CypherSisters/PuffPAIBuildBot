// The vault PIN rules, for every page in the browser that sets a PIN: the
// onboarding wizard's vault step and the Vault page's "Forgot your PIN?".
//
// The browser may be stricter than the backend, never looser. The backend's
// rule for a PIN set in setup or in a recovery is setup_ops.validate_vault_pin,
// which runs two checks: zone_vault.validate_pin (the wizard's rules) and
// security/vault.py validate_vault_secret (the vault's own floor, with a
// common-password list of its own). This file holds BOTH, so a PIN the
// browser accepts is never one the backend then refuses.
// tests/test_vault_pin_crosslang.py runs this real function against the
// Python rules over the same list of PINs.
//
// Plain JavaScript inside a .ts file (Node runs it with type stripping), no
// imports, so the test can load it without a build.

const MIN_PIN = 8;
const MIN_NUMERIC_PIN = 10;
const MAX_PIN = 128;

/** zone_vault._COMMON_PINS and security/vault.py _COMMON_SECRETS, together. */
const COMMON_PINS = new Set([
  // zone_vault._COMMON_PINS
  "000000", "00000000", "111111", "11111111", "123456", "1234567", "12345678",
  "123456789", "654321", "87654321", "112233", "121212", "123123", "123321",
  "696969", "abc123", "abcd1234", "password", "passw0rd", "letmein", "qwerty",
  "qwertyui", "iloveyou", "trustno1", "welcome", "monkey", "dragon", "sunshine",
  "puffpai", "buildbot", "homebot",
  "3141592653", "31415926535", "2718281828", "27182818284", "1618033988",
  "16180339887",
  // security/vault.py _COMMON_SECRETS
  "password1", "1234567890", "letmein1", "welcome1", "aaaaaaaa", "changeme",
  "adminadmin", "1qaz2wsx", "1q2w3e4r", "1q2w3e4r5t", "zaq12wsx", "qazwsxedc",
  "qwertyuiop", "asdfghjkl", "zxcvbnm1",
]);

// security/vault.py weak_pattern: runs on the digit and letter rings (they
// wrap: 7890123, xyzab) and along the keyboard's rows, two or more runs back
// to back (each three or more long, or at most two characters left over when
// one run is four or more long), a block of one to three
// characters repeated (a part-block at the end included), and digits counting
// by a fixed step.
const RINGS = ["0123456789", "abcdefghijklmnopqrstuvwxyz"];
const ROWS = ["1234567890-=", "qwertyuiop[]\\", "asdfghjkl;'", "zxcvbnm,./",
  "qwertyuiopasdfghjklzxcvbnm"];

function steps(a: string, b: string): Set<string> {
  const found = new Set<string>();
  RINGS.forEach((ring, r) => {
    const i = ring.indexOf(a);
    const j = ring.indexOf(b);
    if (i >= 0 && j >= 0) {
      if ((i + 1) % ring.length === j) found.add(`r${r}:1`);
      if ((i - 1 + ring.length) % ring.length === j) found.add(`r${r}:-1`);
    }
  });
  ROWS.forEach((row, w) => {
    const i = row.indexOf(a);
    const j = row.indexOf(b);
    if (i >= 0 && j >= 0 && Math.abs(i - j) === 1) found.add(`w${w}:${j - i}`);
  });
  return found;
}

function runSegments(text: string): number[] {
  const out: number[] = [];
  let i = 0;
  while (i < text.length) {
    let best = 1;
    if (i + 1 < text.length) {
      steps(text[i], text[i + 1]).forEach((kind) => {
        let j = i + 1;
        while (j + 1 < text.length && steps(text[j], text[j + 1]).has(kind)) j += 1;
        best = Math.max(best, j - i + 1);
      });
    }
    out.push(best);
    i += best;
  }
  return out;
}

// security/vault.py _PADS, _cover_steps and _run_cover: the whole secret cut
// into runs, three or more long counting by one on a ring, along a row or down
// a phone / number pad column (147, 2580), four or more long counting by any
// other fixed step (24680, 97531, acegi, 7777). Exact, not greedy.
// security/vault.py (_KEYPAD_LINES, _LOOPS, _keypad_steps,
// _keypad_shape): the two keypad diagonals, the two keypad loops (the diamond
// 2-6-8-4 and the corners 1-3-9-7, four or more long) and paths of seven or
// more neighbouring keys count only when the WHOLE secret is cut into them.
const PADS = ["147", "2580", "369", "741", "8520", "963"];
const KEYPAD_LINES = ["159", "357"];
const LOOPS = ["2684", "1397"];
const UNIT_RUN = 3;
const STEPPED_RUN = 4;
const KING_RUN = 7;

function coverSteps(a: string, b: string): Set<string> {
  const found = steps(a, b);
  RINGS.forEach((ring, r) => {
    const i = ring.indexOf(a);
    const j = ring.indexOf(b);
    if (i >= 0 && j >= 0) {
      const k = (j - i + ring.length) % ring.length;
      if (k !== 1 && k !== ring.length - 1) found.add(`s${r}:${k}`);
    }
  });
  PADS.forEach((pad, p) => {
    const i = pad.indexOf(a);
    const j = pad.indexOf(b);
    if (i >= 0 && j >= 0 && Math.abs(i - j) === 1) found.add(`p${p}:${j - i}`);
  });
  return found;
}

function keypadSteps(a: string, b: string): Set<string> {
  const found = kingSteps(a, b);
  KEYPAD_LINES.forEach((line, d) => {
    const i = line.indexOf(a);
    const j = line.indexOf(b);
    if (i >= 0 && j >= 0 && Math.abs(i - j) === 1) found.add(`d${d}:${j - i}`);
  });
  LOOPS.forEach((loop, l) => {
    const i = loop.indexOf(a);
    const j = loop.indexOf(b);
    const k = (j - i + 4) % 4;
    if (i >= 0 && j >= 0 && (k === 1 || k === 3)) found.add(`l${l}:${k}`);
  });
  return found;
}

// security/vault.py _king_steps: every keypad on which b is right next to a.
function kingSteps(a: string, b: string): Set<string> {
  const found = new Set<string>();
  KEYPADS.forEach((pad, n) => {
    if (!(a in pad) || !(b in pad)) return;
    const [r1, c1] = pad[a];
    const [r2, c2] = pad[b];
    if ((r1 !== r2 || c1 !== c2) && Math.max(Math.abs(r1 - r2), Math.abs(c1 - c2)) === 1) {
      found.add(`k${n}`);
    }
  });
  return found;
}

function coverAndKing(a: string, b: string, king: boolean): Set<string> {
  const found = coverSteps(a, b);
  if (king) keypadSteps(a, b).forEach((kind) => found.add(kind));
  return found;
}

function keypadShape(text: string): boolean {
  if (text.length < 8) return false;
  return keypadPath(text) || runCover(text, true)[0] === 0;
}

// security/vault.py _keypad_path: eight digits or more, every move to a key
// right next to the last one on one keypad.
function keypadPath(text: string): boolean {
  if (text.length < 8 || !/^\d+$/.test(text)) return false;
  let kinds: Set<string> | null = null;
  for (let k = 0; k + 1 < text.length; k += 1) {
    const here = kingSteps(text[k], text[k + 1]);
    if (kinds === null) {
      kinds = here;
    } else {
      const both = new Set<string>();
      kinds.forEach((kind) => { if (here.has(kind)) both.add(kind); });
      kinds = both;
    }
    if (kinds.size === 0) return false;
  }
  return true;
}

function runCover(text: string, king = false): [number, number] {
  const n = text.length;
  const never = n + 1000;
  const left = new Array<number>(n + 1).fill(never);
  const leftLong = new Array<number>(n + 1).fill(never);
  left[0] = 0;
  for (let i = 0; i < n; i += 1) {
    if (left[i] >= never) continue;
    left[i + 1] = Math.min(left[i + 1], left[i] + 1);
    leftLong[i + 1] = Math.min(leftLong[i + 1], leftLong[i] + 1);
    if (i + 1 >= n) continue;
    coverAndKing(text[i], text[i + 1], king).forEach((kind) => {
      const shortest = kind[0] === "k" ? KING_RUN
        : (kind[0] === "s" || kind[0] === "l") ? STEPPED_RUN : UNIT_RUN;
      let j = i + 1;
      while (j + 1 < n && coverAndKing(text[j], text[j + 1], king).has(kind)) {
        j += 1;
        const end = j + 1;
        if (end - i < shortest) continue;
        left[end] = Math.min(left[end], left[i]);
        leftLong[end] = Math.min(leftLong[end], leftLong[i]);
        if (end - i > shortest && kind[0] !== "k") leftLong[end] = Math.min(leftLong[end], left[i]);
      }
    });
  }
  return [left[n], leftLong[n]];
}

// security/vault.py _KEYPADS, _keypad_strokes and _keypad_walk: digits
// drawing a shape on a phone keypad (123 on top, 0 under 8) or a number pad
// (789 on top, the wide 0 under 2 and under 1). Each move goes to a key in
// the same row, column or diagonal, at most two keys away; a stroke is moves
// in one direction, the corner key shared.
const KEYPADS: Array<Record<string, [number, number]>> = [
  { "1": [0, 0], "2": [0, 1], "3": [0, 2], "4": [1, 0], "5": [1, 1],
    "6": [1, 2], "7": [2, 0], "8": [2, 1], "9": [2, 2], "0": [3, 1] },
  { "7": [0, 0], "8": [0, 1], "9": [0, 2], "4": [1, 0], "5": [1, 1],
    "6": [1, 2], "1": [2, 0], "2": [2, 1], "3": [2, 2], "0": [3, 1] },
  { "7": [0, 0], "8": [0, 1], "9": [0, 2], "4": [1, 0], "5": [1, 1],
    "6": [1, 2], "1": [2, 0], "2": [2, 1], "3": [2, 2], "0": [3, 0] },
];

function keypadStrokes(text: string, pad: Record<string, [number, number]>): number {
  const directions: string[] = [];
  for (let k = 0; k + 1 < text.length; k += 1) {
    const [r1, c1] = pad[text[k]];
    const [r2, c2] = pad[text[k + 1]];
    const dr = r2 - r1;
    const dc = c2 - c1;
    if ((dr === 0 && dc === 0) || Math.max(Math.abs(dr), Math.abs(dc)) > 2) return 0;
    if (dr !== 0 && dc !== 0 && Math.abs(dr) !== Math.abs(dc)) return 0;
    directions.push(`${Math.sign(dr)},${Math.sign(dc)}`);
  }
  let strokes = 1;
  for (let k = 0; k + 1 < directions.length; k += 1) {
    if (directions[k] !== directions[k + 1]) strokes += 1;
  }
  return strokes;
}

function keypadWalk(text: string, part = false): boolean {
  const [shortest, slack, most] = part ? [8, 4, 6] : [6, 3, 7];
  if (text.length < shortest || !/^\d+$/.test(text)) return false;
  const limit = Math.min(text.length - slack, most);
  return KEYPADS.some((pad) => {
    const strokes = keypadStrokes(text, pad);
    return strokes > 0 && strokes <= limit;
  });
}

// strict: for a part of a PIN (security/vault.py weak_pattern(strict=True)).
export function weakPattern(secret: string, strict = false): string {
  const text = String(secret || "").toLowerCase();
  const length = text.length;
  if (length < 3) return "";
  for (let block = 1; block <= Math.min(3, Math.floor(length / 2)); block += 1) {
    let same = true;
    for (let k = 0; k < length; k += 1) {
      if (text[k] !== text[k % block]) { same = false; break; }
    }
    if (same) return "repeat";
  }
  if (/^\d+$/.test(text) && length >= 6) {
    const step = (Number(text[1]) - Number(text[0]) + 10) % 10;
    let same = true;
    for (let k = 0; k < length - 1; k += 1) {
      if ((Number(text[k + 1]) - Number(text[k]) + 10) % 10 !== step) { same = false; break; }
    }
    if (same) return "digit_step";
  }
  const segments = runSegments(text);
  if (segments.length === 1) return "run";
  const [left, leftLong] = runCover(text);
  if (left === 0 || (!strict && leftLong <= 2)) return "runs";
  if (keypadWalk(text, strict)) return "keypad";
  if (!strict) {
    if (keypadShape(text) || keypadWithMarks(text)) return "keypad";
    const block = saidTwiceBlock(text);
    if (block) {
      const kind = weakPattern(block);
      if (FLOOR_PATTERNS.has(kind)) return kind;
    }
  }
  return "";
}

// security/vault.py FLOOR_PATTERNS, _keypad_with_marks and _said_twice_block:
// a keypad shape with one or two other characters put in
// anywhere (1478963k250), and a floor pattern said twice.
const FLOOR_PATTERNS = new Set(["run", "runs", "digit_step", "keypad"]);

function keypadWithMarks(text: string): boolean {
  const digits = text.replace(/[^0-9]/g, "");
  const others = text.length - digits.length;
  if (!(others > 0 && others <= 2) || digits.length < 6) return false;
  // Any keypad shape, drawn in any order (9517538426q)
  return keypadWalk(digits) || keypadShape(digits);
}

function saidTwiceBlock(text: string): string {
  const n = text.length;
  for (let block = 4; block <= Math.floor(n / 2); block += 1) {
    let same = true;
    for (let k = 0; k < n; k += 1) {
      if (text[k] !== text[k % block]) { same = false; break; }
    }
    if (same) return text.slice(0, block);
  }
  return "";
}

function sequential(text: string): boolean {
  if (text.length < 3) return false;
  const step = text.charCodeAt(1) - text.charCodeAt(0);
  if (step !== 1 && step !== -1) return false;
  for (let i = 0; i < text.length - 1; i += 1) {
    if (text.charCodeAt(i + 1) - text.charCodeAt(i) !== step) return false;
  }
  return true;
}

function repeatedBlock(text: string): boolean {
  for (let block = 1; block < 4; block += 1) {
    if (block < text.length && text.length % block === 0
        && text === text.slice(0, block).repeat(text.length / block)) return true;
  }
  return false;
}

// zone_vault._guessable_shape: the usual ways a pattern is dressed up (marks
// between the characters, two runs woven together, a few characters
// repeated, a pattern with one or two added, keyboard columns, a common
// password with digits or look-alike characters). Kept step for step with
// the Python, so the two agree (tests/test_vault_pin_crosslang.py).
const COLUMNS = ["1qaz", "2wsx", "3edc", "4rfv", "5tgb", "6yhn", "7ujm", "8ik,",
  "9ol.", "0p;/"];
const COMMON_WORDS = new Set([
  "password", "passwort", "letmein", "iloveyou", "trustno", "welcome",
  "monkey", "dragon", "sunshine", "princess", "football", "baseball",
  "soccer", "hockey", "master", "shadow", "superman", "batman", "admin",
  "login", "hello", "freedom", "whatever", "starwars", "secret", "qwerty",
  "changeme", "puffpai", "buildbot", "homebot", "michael", "jennifer",
  "charlie", "computer", "internet", "summer", "winter", "flower", "cookie",
  "pokemon", "matrix", "mustang", "access", "family", "loveme", "abc",
  // Months, seasons and weekdays the same way
  "january", "february", "march", "april", "may", "june", "july", "august",
  "september", "october", "november", "december", "jan", "feb", "mar",
  "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
  "spring", "summer", "autumn", "fall", "winter",
  "monday", "tuesday", "wednesday", "thursday", "friday", "saturday",
  "sunday",
  "vault", "mypin", "mysecret", "mypassword",
  // Holidays and the days people mark, the same way
  "christmas", "xmas", "easter", "halloween", "thanksgiving", "hanukkah",
  "chanukah", "diwali", "ramadan", "kwanzaa", "passover", "valentine",
  "valentines", "newyear", "newyears", "birthday", "anniversary",
  "wedding", "holiday", "holidays", "vacation", "juneteenth",
  "mothersday", "fathersday", "stpatricks",
  // words near the top of every published list of choices
  "jesus", "christ", "blessed", "angel", "purple", "orange", "yellow",
  "silver", "golden", "roblox", "minecraft", "fortnite", "unicorn",
  "rainbow", "butterfly", "sunflower", "chocolate", "banana", "cheese",
  "ninja", "spiderman", "babygirl",
  // well-known passphrases, word for word
  "correcthorsebatterystaple", "correcthorse", "batterystaple",
  "horsebatterystaple", "troubador", "troubadour", "pinnumber",
  "mypinnumber", "passcode", "mypasscode", "pincode", "mypincode",
]);
// zone_vault._NAMES, _PHRASE_STARTS, _MONTH_NAMES, _FAMOUS_NUMBERS
const NAMES = new Set((
  "liam noah oliver james elijah william henry lucas benjamin theodore "
  + "mateo levi sebastian daniel jack alexander owen asher samuel ethan leo "
  + "jacob logan jackson david joseph john luke ezra matthew olivia emma "
  + "charlotte amelia sophia mia isabella ava evelyn luna harper sofia "
  + "camila eleanor elizabeth violet scarlett emily hazel lily chloe grace "
  + "ella aria nora zoey riley madison abigail sarah jessica ashley anna "
  + "maria jordan taylor morgan").split(" "));
const PHRASE_STARTS = ["ilove", "iluv", "mypin", "mypass", "mysecret", "letmein",
  "opensesame"];
const MONTH_NAMES = ["january", "february", "march", "april", "may", "june",
  "july", "august", "september", "october", "november", "december", "sept",
  "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "oct", "nov", "dec"];
const FAMOUS_NUMBERS = ["8675309"];
const LOOKALIKES: Record<string, string> = { "@": "a", "4": "a", "3": "e",
  "1": "il", "!": "i", "0": "o", "$": "s", "5": "s", "7": "t", "+": "t",
  "8": "b", "9": "g" };
const TRIM = "0123456789!@#$%^&*()_-+=.,?~";

function allSame(text: string): boolean {
  return new Set(text.split("")).size === 1;
}

// part: a piece of the PIN (zone_vault._is_weak_core(part=True)).
function weakCore(text: string, part = false): boolean {
  if (text.length < 3) return false;
  return allSame(text) || sequential(text) || repeatedBlock(text)
    || weakPattern(text, part) !== "" || COMMON_PINS.has(text);
}

function keyboardSteps(a: string, b: string): Set<string> {
  const found = steps(a, b);
  COLUMNS.forEach((col, c) => {
    const i = col.indexOf(a);
    const j = col.indexOf(b);
    if (i >= 0 && j >= 0 && Math.abs(i - j) === 1) found.add(`c${c}:${j - i}`);
  });
  return found;
}

function keyboardSegments(text: string): number[] {
  const out: number[] = [];
  let i = 0;
  while (i < text.length) {
    let best = 1;
    if (i + 1 < text.length) {
      keyboardSteps(text[i], text[i + 1]).forEach((kind) => {
        let j = i + 1;
        while (j + 1 < text.length && keyboardSteps(text[j], text[j + 1]).has(kind)) j += 1;
        best = Math.max(best, j - i + 1);
      });
    }
    out.push(best);
    i += best;
  }
  return out;
}

function trimMarks(text: string): string {
  let a = 0;
  let b = text.length;
  while (a < b && TRIM.includes(text[a])) a += 1;
  while (b > a && TRIM.includes(text[b - 1])) b -= 1;
  return text.slice(a, b);
}

function spellings(text: string): string[] {
  let out = [""];
  for (const ch of text) {
    const options = LOOKALIKES[ch] ?? ch;
    const next: string[] = [];
    for (const s of out) for (const o of options) next.push(s + o);
    out = next.slice(0, 16);
  }
  return out;
}

// zone_vault: a shape said twice, a pattern with one or two
// characters of the other kind put in anywhere, dates, phone numbers,
// well-known runs of numbers, and a word or a name with a number on the end.
const SEQUENCES = [
  "0112358132134558914423337761098715972584418167651094617711",
  "31415926535897932384626433832795028841971",
  "27182818284590452353602874713526624977572",
  "16180339887498948482045868343656381177203",
  "14142135623730950488016887242096980785696",
  "0149162536496481100121144169196225256289324361400",
  "0182764125216343512729100013311728219727443375",
  "235711131719232931374143475359616771737983899710110310710911",
  "1248163264128256512102420484096819216384",
];
const EDGE_MARKS = "!@#$%^&*()_-+=.,?~";
const PHONE_WRITTEN = /^\+?1?[ .\-]?\(?\d{3}\)?[ .\-]?\d{3}[ .\-]\d{4}$/;
const PHONE_LOOSE = /^\+?1?[ .\-]?\(?\d{3}\)?[ .\-]?\d{3}[ .\-]?\d{4}$/;
const WORD_THEN_NUMBER = /^([a-z]{3,})[!@#$%^&*()_\-+=.,?~]?([0-9]{1,6})$/;
const NUMBER_THEN_WORD = /^([0-9]{1,6})[!@#$%^&*()_\-+=.,?~]?([a-z]{3,})$/;
const UNSAYABLE = /q(?!u)|[jqxzvwk]{2}|(.)\1\1/;
const COMMON_PAIRS = new Set((
  "th he in er an re on at en nd ti es or te of ed is it al ar st to nt ng "
  + "se ha as ou io le ve co me de hi ri ro ic ne ea ra ce li ch ll be ma si "
  + "om ur ca el ta la ns di fo ho pe ec pr no ct us ac ot il tr ly nc et ut "
  + "ss so rs un lo wa ge ie wh ee wi em ad ol rt po we na ul ni ts mo ow pa "
  + "im mi ai sh ir su id os iv ia am fi ci vi pl ig tu ev ld ry mp fe bl ab "
  + "gh ty op wo sa ay ex ke fr oo av ag if ap gr od bo sp rd do uc bu ei ov "
  + "by rm ep tt oc fa ef cu rn sc gi da yo cr cl du ga qu ue ff ba ey ls va "
  + "um pp ua up lu go ht ru ug ds lt pi rc rr eg au ck ew mu br bi pt ak pu "
  + "ui rg ib tl ny ki rk ys ob mm fu ph og ms ye ud mb ip ub oi rl gu dr hr "
  + "cc tw ft wn nu af hu nn eo vo rv nf xp gn sm fl iz ok nl my gl aw ju oa "
  + "eq sy sl ps jo lf nv je nk kn gs dy hy ze ks xt bs ik dd cy rp sk").split(" "));

function stripEdges(text: string, chars: string): string {
  let a = 0;
  let b = text.length;
  while (a < b && chars.includes(text[a])) a += 1;
  while (b > a && chars.includes(text[b - 1])) b -= 1;
  return text.slice(a, b);
}

function isDigits(text: string): boolean {
  return /^[0-9]+$/.test(text);
}

function dateAt(digits: string): boolean {
  const md = (m: number, d: number) => m >= 1 && m <= 12 && d >= 1 && d <= 31;
  const num = (s: string) => Number(s);
  if (digits.length === 8) {
    const y = num(digits.slice(0, 4));
    if (y >= 1920 && y <= 2039 && md(num(digits.slice(4, 6)), num(digits.slice(6)))) return true;
    const a = num(digits.slice(0, 2));
    const b = num(digits.slice(2, 4));
    const tail = num(digits.slice(4));
    return tail >= 1920 && tail <= 2039 && (md(a, b) || md(b, a));
  }
  if (digits.length === 6) {
    const a = num(digits.slice(0, 2));
    const b = num(digits.slice(2, 4));
    const c = num(digits.slice(4));
    return md(a, b) || md(b, a) || md(b, c);
  }
  if (digits.length === 7 && num(digits.slice(3)) >= 1920 && num(digits.slice(3)) <= 2039) {
    const one = num(digits.slice(0, 1));
    const two = num(digits.slice(1, 3));
    const first = num(digits.slice(0, 2));
    const last = num(digits.slice(2, 3));
    return md(one, two) || md(two, one) || md(first, last) || md(last, first);
  }
  return false;
}

function dateShape(core: string): boolean {
  for (const width of [8, 7, 6]) {
    if (core.length < width || core.length - width > 2) continue;
    for (const window of [core.slice(0, width), core.slice(core.length - width)]) {
      if (isDigits(window) && dateAt(window)) return true;
    }
  }
  return false;
}

function wordDateShape(core: string): boolean {
  const got = /^([a-z]*)([0-9]+)([a-z]*)$/.exec(core);
  if (!got) return false;
  const head = got[1];
  const digits = got[2];
  const tail = got[3];
  const letters = head || tail;
  if (letters && !(head && tail) && digits.length >= 6 && digits.length <= 8 && dateAt(digits)
      && (digits.length === 8 || letters.length <= 2 || wordsOrNames(letters))) return true;
  for (const [word, other] of [[head, tail], [tail, head]]) {
    if (other.length > 3) continue;
    if (MONTH_NAMES.indexOf(word) < 0) continue;
    const n = Number(digits);
    if (digits.length <= 2 && n >= 1 && n <= 31) return true;
    if (digits.length === 4 && ((n >= 1900 && n <= 2039)
        || (Number(digits.slice(0, 2)) >= 1 && Number(digits.slice(0, 2)) <= 31))) return true;
    if ((digits.length === 5 || digits.length === 6)) {
      const day = Number(digits.slice(0, digits.length - 4));
      const year = Number(digits.slice(-4));
      if (day >= 1 && day <= 31 && year >= 1900 && year <= 2039) return true;
    }
  }
  return false;
}

function phoneShape(text: string, core: string): boolean {
  if (FAMOUS_NUMBERS.some((n) => core.includes(n))) return true;
  const digits = core.length === 11 && core.startsWith("1") ? core.slice(1) : core;
  if (digits.length !== 10 || !isDigits(digits)) return false;
  if (core !== text && (PHONE_WRITTEN.test(text)
      || (PHONE_LOOSE.test(text) && /[()+.\-]/.test(text)))) return true;
  if (digits.slice(0, 3) === "555" || digits.slice(3, 8) === "55501") return true;
  return weakCore(digits.slice(3), true);
}

function sequenceShape(core: string): boolean {
  if (!isDigits(core)) return false;
  for (let head = 0; head < 3; head += 1) {
    for (let tail = 0; tail < 3; tail += 1) {
      const middle = core.slice(head, core.length - tail);
      if (middle.length >= 8 && SEQUENCES.some((s) => s.includes(middle))) return true;
    }
  }
  return false;
}

function readsAsAWord(letters: string): boolean {
  const isVowel = (ch: string) => "aeiouy".includes(ch);
  let vowels = 0;
  for (const ch of letters) if (isVowel(ch)) vowels += 1;
  if (!(4 * vowels >= letters.length && 10 * vowels <= 7 * letters.length)) return false;
  let consonants = 0;
  let inARow = 0;
  for (const ch of letters) {
    if (isVowel(ch)) { inARow += 1; consonants = 0; } else { inARow = 0; consonants += 1; }
    if (consonants > 3 || inARow > 2) return false;
  }
  if (UNSAYABLE.test(letters)) return false;
  let rare = 0;
  for (let k = 0; k + 1 < letters.length; k += 1) {
    if (!COMMON_PAIRS.has(letters.slice(k, k + 2))) rare += 1;
  }
  return rare <= (letters.length >= 7 ? 1 : 0);
}

function aWordOrName(letters: string): boolean {
  return COMMON_WORDS.has(letters) || NAMES.has(letters) || readsAsAWord(letters);
}

function known(letters: string): boolean {
  return NAMES.has(letters) || COMMON_WORDS.has(letters);
}

function wordsOrNames(letters: string): boolean {
  if (aWordOrName(letters)) return true;
  for (let k = 3; k < letters.length - 2; k += 1) {
    const a = letters.slice(0, k);
    const b = letters.slice(k);
    if ((known(a) && (known(b) || (b.length >= 4 && readsAsAWord(b))))
        || (known(b) && a.length >= 4 && readsAsAWord(a))) return true;
  }
  return false;
}

function aNumberPeopleAdd(digits: string): boolean {
  if (digits.length <= 2 || (digits.length === 4 && Number(digits) >= 1900 && Number(digits) <= 2039)) return true;
  return allSame(digits) || sequential(digits) || repeatedBlock(digits);
}

function wordNumberShape(text: string): boolean {
  const bare = stripEdges(text, EDGE_MARKS);
  if (bare !== text && /^[a-z]+$/.test(bare) && bare.length >= 4 && readsAsAWord(bare)) return true;
  let letters = "";
  let digits = "";
  let got = WORD_THEN_NUMBER.exec(bare);
  if (got) {
    letters = got[1];
    digits = got[2];
  } else {
    got = NUMBER_THEN_WORD.exec(bare);
    if (!got) return false;
    digits = got[1];
    letters = got[2];
  }
  return aNumberPeopleAdd(digits) && wordsOrNames(letters);
}

function commonInside(text: string): boolean {
  const bare = stripEdges(text, EDGE_MARKS);
  if (!/^[a-z]+$/.test(bare)) return false;
  if (PHRASE_STARTS.some((start) => bare.startsWith(start))) return true;
  for (const w of Array.from(COMMON_WORDS)) {   // the build targets ES5: no Set iteration
    if (w.length >= 5 && bare.includes(w) && 2 * w.length >= bare.length) return true;
  }
  return false;
}

function saidTwice(core: string): boolean {
  return saidTwiceBlock(core) !== "";
}

function patternWithInsert(core: string): boolean {
  const digits = core.replace(/[^0-9]/g, "");
  const letters = core.replace(/[0-9]/g, "");
  for (const [rest, putIn] of [[digits, letters], [letters, digits]]) {
    if (putIn.length > 0 && putIn.length <= 2 && rest.length >= 6 && weakCore(rest, true)) return true;
  }
  return false;
}

export function guessableShape(pin: string): string {
  const text = pin.toLowerCase();
  const core = text.replace(/[^a-z0-9]/g, "");
  if (core !== text && weakCore(core)) return "marks";
  let evens = "";
  let odds = "";
  for (let k = 0; k < core.length; k += 1) {
    if (k % 2 === 0) evens += core[k]; else odds += core[k];
  }
  if (evens.length >= 3 && odds.length >= 3 && weakCore(evens, true) && weakCore(odds, true)) return "woven";
  if (new Set(core.split("")).size <= 3) return "few";
  if (saidTwice(core)) return "twice";
  for (let head = 0; head < 3; head += 1) {
    for (let tail = 0; tail < 3; tail += 1) {
      const middle = core.slice(head, core.length - tail);
      if ((head || tail) && middle.length >= 6 && weakCore(middle, true)) return "padded";
    }
  }
  let collapsed = "";
  for (let k = 0; k < core.length; k += 1) {
    if (k === 0 || core[k - 1] !== core[k]) collapsed += core[k];
  }
  if (collapsed !== core && collapsed.length >= 3 && weakCore(collapsed, true)) return "padded";
  if (patternWithInsert(core)) return "padded";
  if (dateShape(core)) return "date";
  if (phoneShape(text, core)) return "phone";
  if (sequenceShape(core)) return "sequence";
  const segments = keyboardSegments(text);
  const longRuns = segments.filter((n) => n >= 3);
  const leftOver = segments.filter((n) => n < 3).reduce((a, n) => a + n, 0);
  if (longRuns.length && leftOver <= 2
      && (segments.length === 1 || longRuns.reduce((a, n) => a + n, 0) >= 6)) return "keyboard";
  for (const body of [trimMarks(text), text]) {
    if (body.length < 3) continue;
    for (const spelled of spellings(body)) {
      const word = trimMarks(spelled);
      if (COMMON_WORDS.has(word) || COMMON_PINS.has(word)) return "common";
    }
  }
  if (commonInside(text)) return "common";
  if (wordNumberShape(text)) return "word_number";
  if (wordDateShape(core)) return "date";
  return "";
}

const SHAPE_WORDS: Record<string, string> = {
  marks: "Dashes, dots and other marks do not hide a pattern. Pick something with no pattern in it.",
  woven: "That weaves two patterns together, like 1a2b3c. Pick something with no pattern in it.",
  few: "That uses only a few different characters, mostly repeated. Mix it up more.",
  padded: "That is a pattern with a character or two added. Pick something with no pattern in it.",
  keyboard: "That runs along the keyboard, down the columns or across the rows. Pick something with no pattern in it.",
  common: "That is a common password with a few characters changed or added. Pick something that is not a common word.",
  twice: "That is the same thing typed twice. Pick something with no pattern in it.",
  date: "That looks like a date. Pick something that is not a date.",
  phone: "That looks like a phone number. Pick something else.",
  sequence: "That is a well-known run of numbers, like the start of pi. Pick something with no pattern in it.",
  word_number: "That is a word or a name with a number or a year on the end. Mix letters, numbers and marks all through it.",
};

/** Why this PIN will not do, in one plain sentence, or "" when it will. */
export function pinRefusal(pin: string): string {
  if (!pin) return "Your vault needs a PIN.";
  if (/\s/.test(pin)) return "Leave spaces out of the PIN. They are easy to lose track of.";
  if (pin.split("").some((ch) => ch.charCodeAt(0) <= 32 || ch.charCodeAt(0) >= 127)) {
    return "Use letters, digits or punctuation you can type on any keyboard.";
  }
  if (pin.length > MAX_PIN) return `That is longer than ${MAX_PIN} characters.`;
  if (/^\d+$/.test(pin) && pin.length < MIN_NUMERIC_PIN) {
    return `A PIN of only numbers needs at least ${MIN_NUMERIC_PIN} of them. Adding a letter lets you use ${MIN_PIN}.`;
  }
  if (pin.length < MIN_PIN) return `Use at least ${MIN_PIN} characters so it is not quick to guess.`;
  if (new Set(pin.split("")).size === 1) return "That is the same character repeated. Pick something with some variety.";
  if (sequential(pin)) return "That runs straight up or down the keyboard. Pick something less easy to guess.";
  if (repeatedBlock(pin)) return "That is a short pattern repeated. Mix it up a bit.";
  if (COMMON_PINS.has(pin.toLowerCase())) return "That one is on every list of common choices. Almost anything else is better.";
  const kind = weakPattern(pin);
  if (kind === "repeat") return "That is a short pattern repeated. Mix it up a bit.";
  if (kind === "keypad") return "That draws a shape on the number pad, like a snake, a spiral or a square. Pick something with no pattern in it.";
  if (kind) return "That counts up or down, by ones or by bigger steps, or runs along the keyboard or keypad, even if it wraps around. Pick something with no pattern in it.";
  const shape = guessableShape(pin);
  if (shape) return SHAPE_WORDS[shape];
  return "";
}

/** The longest PIN either side accepts, for an input's maxLength. */
export const PIN_MAX_LENGTH = MAX_PIN;
