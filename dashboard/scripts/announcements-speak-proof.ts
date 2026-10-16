// The "From your bot" card's read-aloud rules, proven without a browser.
//
// Proves the decision of WHAT is read, not that sound comes out: nothing here
// plays audio. Exits non-zero on failure.
//
// Run from the dashboard directory:
//   npx tsc scripts/announcements-speak-proof.ts --outDir .proofout --module commonjs --target es2020 --moduleResolution node --skipLibCheck && node .proofout/scripts/announcements-speak-proof.js
//
// Copyright (c) Cypher Sisters. Apache-2.0 License.
import { pickToSpeak, type SpeakableMessage } from "../app/_lib/services/announcementsSpeak";

let failed = 0;
function check(name: string, ok: boolean): void {
  console.log(`${ok ? "PASS" : "FAIL"} ${name}`);
  if (!ok) failed += 1;
}

const a1 = { id: "a1", text: "Old update", spoken: false };
const a2 = { id: "a2", text: "Reminder: Water the beans", spoken: false };
const a3 = { id: "a3", text: "Good morning! Tonight's low: 3 °C.", spoken: false };
const a4 = { id: "a4", text: "Said by the computer already", spoken: true };

// 1. Page opens with a backlog: nothing is read, even with speaking on.
let r: { toSpeak: SpeakableMessage[]; seen: Set<string> } = pickToSpeak(null, [a1], true);
check("a backlog is never read when the page opens", r.toSpeak.length === 0);
let seen = r.seen;

// 2. Two new messages arrive (feed is newest first): read oldest first, once.
r = pickToSpeak(seen, [a3, a2, a1], true);
check("new messages are read oldest first",
  r.toSpeak.map((a) => a.id).join(",") === "a2,a3");
seen = r.seen;
r = pickToSpeak(seen, [a3, a2, a1], true);
check("a message is read only once", r.toSpeak.length === 0);

// 3. Speaking switched off: new messages are remembered but not read, and
//    switching it back on later does not read them late.
r = pickToSpeak(seen, [{ id: "a5", text: "Quiet one" }, a3], false);
check("nothing is read while speaking is off", r.toSpeak.length === 0);
seen = r.seen;
r = pickToSpeak(seen, [{ id: "a5", text: "Quiet one" }, a3], true);
check("switching speaking on does not read old messages late", r.toSpeak.length === 0);
seen = r.seen;

// 4. The computer already said it: the page does not say it again.
r = pickToSpeak(seen, [a4], true);
check("a message the computer already said is not read again", r.toSpeak.length === 0);

// 5. The caller's set is not changed behind its back.
const before = new Set(["x"]);
pickToSpeak(before, [a2], true);
check("the seen set passed in is left alone", before.size === 1);

if (failed) {
  console.log(`${failed} failed`);
  process.exit(1);
}
console.log("announcements speak: PASS");
