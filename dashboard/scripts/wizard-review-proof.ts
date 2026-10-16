// The wizard's screen rules, proven without a browser.
//
// The parts that could lie are the parts worth proving:
//
//  1. `basis` is about the TIME. A row's bytes are a size the registry
//     declares, so they are summed even when nobody has timed the download;
//     what is refused is a confident MINUTE count.
//  2. ONE summary, ONE "Looks good". There is no longer a gate
//     that makes the owner open every heavy row first: the summary spells out
//     every size and every outside service, and one click approves it.
//     "Change something" opens any item, never mutating what was shown.
//  3. Plain words: no banned wizard lingo on the summary, and a
//     young builder sees at most three choices at once.
//  4. The browser is never looser than the terminal about the vault PIN.
//  5. Staleness is ONE collapsed banner with a count, never a badge per row.
//
// Exits non-zero on failure so it can sit in `npm run test:gates`.
//
// Run from the dashboard directory:
//   npm run test:gates
//
// Copyright (c) Cypher Sisters. Apache-2.0 License.
import { readFileSync } from "node:fs";
import { join } from "node:path";

import type { Summary, SummaryItem, WizardCard } from "../app/_lib/services/wizardService";
import {
  cardId,
  decidedLine,
  fieldForError,
  formatBytes,
  groupFields,
  inSpecOrder,
  infoBlock,
  notConvertedRows,
  reasonText,
  rowCost,
  staleBanner,
  totalLine,
} from "../app/onboarding/review/reviewModel";
import {
  BRAIN_OFF,
  bannedIn,
  choicePage,
  emptyChanges,
  numbered,
  pinRefusal,
  summaryTexts,
  townOnLine,
  withBrain,
  withReach,
  withToggle,
  withTown,
} from "../app/onboarding/summaryModel";

// A GB is 1024 MB everywhere in this wizard (cards.format_bytes and its mirror
// in reviewModel.formatBytes). The fixtures below are stated in those units, so
// a size an owner reads on a row is the same number the backend put in the
// card's own words.
const GB = 1024 ** 3;

let failures = 0;

function check(name: string, ok: boolean, detail = "") {
  console.log(`  ${ok ? "ok  " : "FAIL"}  ${name}${detail ? ` — ${detail}` : ""}`);
  if (!ok) failures += 1;
}

function card(
  skill: string,
  extra: Partial<WizardCard> = {},
): WizardCard {
  return {
    schema: "wizard_card/v1",
    skill,
    section: skill,
    part: "",
    title: skill,
    decided: "something",
    status: "ready",
    consequential: false,
    consequential_reasons: [],
    estimate: { bytes: 0, seconds: 0, basis: "measured" },
    fields: [],
    gaps: [],
    ...extra,
  };
}

// The owner's real build: a brain that knows its own size, packs that do not
// know how long they will take, and a vault with no PIN on it yet.
const BRAIN = card("brain", {
  title: "What does the thinking",
  estimate: { bytes: 4.7 * GB, seconds: 600, basis: "calibrated" },
  consequential: true,
  consequential_reasons: ["heavy_bytes"],
});
const INTEGRATE = card("integrate", {
  title: "Getting it in",
  estimate: { bytes: 96 * GB, seconds: null, basis: "unknown" },
  consequential: true,
  consequential_reasons: ["heavy_bytes"],
});
const SECURE = card("secure", {
  title: "What is locked, what gets out",
  status: "needs_you",
  consequential: true,
  consequential_reasons: ["vault_pin_unset", "runtime_egress"],
});
const DISCOVER = card("discover", {
  title: "What this machine is",
  estimate: { bytes: 0, seconds: 3, basis: "measured" },
});
const PURPOSE = card("purpose", { title: "What it is for" });

const BUILD = [DISCOVER, PURPOSE, BRAIN, INTEGRATE, SECURE];

console.log("\nthe total sums declared bytes and refuses to invent a time");
{
  const line = totalLine([BRAIN, INTEGRATE]);
  check("bytes are still summed", line.startsWith("about 101 GB declared"), line);
  check("and the total says where the number came from",
    line.includes("declared"), line);
  check("and the time says unknown", line.includes("time unknown"), line);
  check(
    "no confident minute count survives the unknown row",
    !/\d+ (min|sec|hr)/.test(line),
    line,
  );
  const known = totalLine([BRAIN, DISCOVER]);
  check("all-known rows do get a time", /\d+ min/.test(known), known);
  check("and their bytes", known.startsWith("about 4.7 GB declared"), known);
  const nothing = totalLine([PURPOSE]);
  check("a build with no download says so", nothing === "nothing to download · no wait", nothing);

  // The row's own line carries the same honesty ("96 GB · time unknown").
  check("an unknown row states both", rowCost(INTEGRATE.estimate) === "96 GB · time unknown",
    rowCost(INTEGRATE.estimate));
  check("a measured row with no bytes states its time", rowCost(DISCOVER.estimate) === "3 sec",
    rowCost(DISCOVER.estimate));
  check("bytes read as a person reads them", formatBytes(4.7 * GB) === "4.7 GB",
    formatBytes(4.7 * GB));
  check("and a GB is 1024 MB, as the backend says it", formatBytes(1024 ** 2) === "1 MB",
    formatBytes(1024 ** 2));
  check("and nothing is not 0 GB", formatBytes(0) === "", `"${formatBytes(0)}"`);
}

console.log("\nstaleness is one banner with a count");
{
  check("nothing stale: no banner", staleBanner([]) === null);
  check("undefined: no banner", staleBanner(undefined) === null);
  check("three stale", staleBanner(["prove", "context", "handoff"]) === "3 things need rechecking",
    String(staleBanner(["prove", "context", "handoff"])));
  check("one stale is singular", staleBanner(["prove"]) === "1 thing needs rechecking",
    String(staleBanner(["prove"])));
  check(
    "the same skill twice is one thing",
    staleBanner(["prove", "prove"]) === "1 thing needs rechecking",
    String(staleBanner(["prove", "prove"])),
  );
}

console.log("\na skill with no card is listed, not hidden");
{
  // The backend names them; this file keeps no list of the twelve, which is
  // the whole point - one source of truth for what the wizard is made of.
  const plain = notConvertedRows([{ skill: "prove", title: "Proof it works" }]);
  check("the backend's rows are the rows", plain.length === 1, String(plain.length));
  check("in the owner's words", plain[0].title === "Proof it works", plain[0].title);
  check("with the skill id kept", plain[0].skill === "prove", plain[0].skill);
  check(
    "and an honest note",
    plain.every((row) => row.note === "set in the step-by-step setup"),
  );
  check("nothing to list is no rows", notConvertedRows([]).length === 0);
  check("a backend that says nothing lists nothing",
    notConvertedRows(undefined).length === 0);
}

console.log("\na skill that asks more than one question gets one row each");
{
  const vault = card("secure", { part: "vault", consequential: true,
    consequential_reasons: ["vault_pin_unset"] });
  const bridge = card("secure", { part: "bridge" });
  check("the id carries the part", cardId(vault) === "secure:vault", cardId(vault));
  check("a single-question skill keeps its plain id",
    cardId(DISCOVER) === "discover", cardId(DISCOVER));
  check("the two rows are never the same row", cardId(vault) !== cardId(bridge));
  check("a heavy row still says why in words",
    reasonText(vault.consequential_reasons) === "the vault PIN is not set",
    reasonText(vault.consequential_reasons));
}

console.log("\nthe rest of the screen's arithmetic");
{
  const shuffled = inSpecOrder([SECURE, DISCOVER, BRAIN],
    ["discover", "brain", "secure"]);
  check("rows are drawn in the backend's order",
    shuffled.map((c) => c.skill).join(",") === "discover,brain,secure",
    shuffled.map((c) => c.skill).join(","));
  check("a row the order does not name is drawn last, never dropped",
    inSpecOrder([card("mystery"), DISCOVER], ["discover"])
      .map((c) => c.skill).join(",") === "discover,mystery");

  // A refusal that names a field is shown beside that field, and one that
  // names nothing this card has stays a card-level error.
  check("a refusal finds its field",
    fieldForError("'vault_pin' takes at most 128 characters.", ["vault_pin", "doors"]) === "vault_pin");
  check("an unrelated refusal stays card-level",
    fieldForError("This card has no field called 'nope'.", ["vault_pin"]) === null);
  check("a refusal with no field name stays card-level",
    fieldForError("Nothing was changed.", ["vault_pin"]) === null);

  // Product rules that reach the screen as strings.
  const words = [
    ...Object.values(BUILD.map((c) => c.title)),
    totalLine(BUILD),
    String(staleBanner(["prove"])),
    reasonText(SECURE.consequential_reasons),
  ];
  check("no banned word", !words.some((w) => w.toLowerCase().includes("hatch")));
  check("no row says 'configured'", !words.some((w) => w.toLowerCase().includes("configured")));
}

console.log("\nthe screen says how much of the build has been decided");
{
  check("a fresh build says nothing has been applied",
    decidedLine(14, []) === "Nothing has been applied. Open a row to change what it decided.",
    decidedLine(14, []));
  check("and after three applies it counts them",
    decidedLine(14, ["discover", "brain", "secure:posture"])
      === "3 of 14 decided so far. Open a row to change what it decided.",
    decidedLine(14, ["discover", "brain", "secure:posture"]));
  check("the same row twice is still one row",
    decidedLine(14, ["discover", "discover"]).startsWith("1 of 14"),
    decidedLine(14, ["discover", "discover"]));
}

console.log("\ninfo lines are a quiet block, never a label per sentence");
{
  // The starter catalog read "SUGGESTED / Knowledge packs: / SUGGESTED /
  // - Science…": a label over every line of a list nobody can answer.
  const block = infoBlock([
    { id: "matched", label: "From that goal, the starter catalog suggests", current: "" },
    { id: "matched_1", label: "Suggested", current: "Knowledge packs:" },
    { id: "matched_2", label: "Suggested", current: "- Science" },
  ]);
  check("one small label for the block",
    block.heading === "From that goal, the starter catalog suggests", block.heading);
  check("and none on the lines under it",
    block.lines.every((line) => line.label === ""),
    block.lines.map((line) => line.label).join("|"));
  check("every line still has its words",
    block.lines.map((line) => line.text).join("|") === "Knowledge packs:|- Science",
    block.lines.map((line) => line.text).join("|"));

  // Genuinely different info rows keep their own labels.
  const mixed = infoBlock([
    { id: "machine", label: "What was found", current: "Windows - 64 GB" },
    { id: "memory_gb", label: "Memory", current: "64 GB (measured on this machine)" },
  ]);
  check("distinct rows keep their labels",
    mixed.heading === "" && mixed.lines.map((line) => line.label).join("|")
      === "What was found|Memory",
    `${mixed.heading}::${mixed.lines.map((line) => line.label).join("|")}`);

  // And a run of info fields is one block, with answerable fields left alone.
  const groups = groupFields([
    { id: "a", kind: "info" },
    { id: "b", kind: "info" },
    { id: "model", kind: "choice" },
    { id: "c", kind: "info" },
  ]);
  check("consecutive info fields group, answerable ones do not",
    groups.map((g) => (g.kind === "info" ? `info:${g.fields.length}` : g.field.id)).join(",")
      === "info:2,model,info:1",
    groups.map((g) => (g.kind === "info" ? `info:${g.fields.length}` : g.field.id)).join(","));
}

console.log("\none summary, one Looks good");
{
  const item = (id: string, section: string, kind: string, extra: Partial<SummaryItem> = {}): SummaryItem => ({
    id,
    section,
    label: id,
    detail: "",
    size_bytes: null,
    outside_host: null,
    default_on: true,
    needs_yes: kind !== "none",
    apply: { kind, ref: id.split(":").pop() || id },
    on: true,
    ...extra,
  });
  const brain = item("thinking:brain", "thinking", "model_pull", {
    label: "The brain: qwen2.5:7b-instruct",
    size_bytes: 4.4 * GB,
    outside_host: "registry.ollama.ai",
  });
  const team = item("thinking:team", "thinking", "none", { label: "A team of 7 helpers works inside StargazerBot" });
  const pack = item("knowledge:registry:kiwix_space", "knowledge", "source_fetch", {
    label: "Space Exploration Stack Exchange",
    size_bytes: 700 * 1024 ** 2,
    outside_host: "download.kiwix.org",
  });
  const weather = item("outside_services:door:weather", "outside_services", "door", {
    label: "Weather",
    on: false,
    default_on: false,
  });
  const summary: Summary = {
    name: "StargazerBot",
    goal: "space",
    mode: "simple",
    internet: true,
    title: "Here is the plan for StargazerBot",
    intro: "Nothing has happened yet.",
    checked: ["The internet works. We can download things."],
    sections: [
      { id: "thinking", heading: "How it thinks", items: [brain, team], empty: "Nothing here yet." },
      { id: "knowledge", heading: "What it will know", items: [pack], empty: "Nothing here yet." },
      { id: "outside_services", heading: "Websites and services it may use", items: [weather], empty: "Nothing here yet." },
    ],
    reach: {
      current: "only_listed",
      label: "Internet: Only the services listed above",
      choices: [
        { value: "offline", label: "Keep StargazerBot off the internet" },
        { value: "only_listed", label: "Only the services listed above" },
        { value: "ask_each_time", label: "Ask me every time" },
      ],
    },
    model_choices: [
      { value: "qwen2.5:7b-instruct", label: "Qwen2.5 7B: 4.4 GB to download, fits well", size_bytes: 4.4 * GB },
      { value: "qwen2.5:3b-instruct", label: "Qwen2.5 3B: 2 GB to download, fits well", size_bytes: 2 * GB },
      { value: "llama3.2:3b", label: "Llama 3.2 3B: 2 GB to download, fits well", size_bytes: 2 * GB },
      { value: "llama3.2:1b", label: "Llama 3.2 1B: 1 GB to download, fits well", size_bytes: 1 * GB },
    ],
    total_bytes: 5.1 * GB,
    hosts: ["download.kiwix.org", "registry.ollama.ai"],
    lines: { total: "Total to download: 5.1 GB.", leaves: "Your computer will talk to: download.kiwix.org, registry.ollama.ai." },
    actions: [{ value: "looks_good", label: "Looks good" }, { value: "change", label: "Change something" }],
    approved: false,
  };
  const order = numbered(summary).map((row) => row.id);
  check("statements are not switches: the team line has no number",
    !order.includes("thinking:team"), order.join(","));
  check("numbers run in summary order",
    order.join(",") === [brain.id, pack.id, weather.id].join(","), order.join(","));
  check("there is exactly one approve action, and it says Looks good",
    summary.actions.filter((a) => a.value === "looks_good").length === 1
      && summary.actions[0].label === "Looks good");

  const start = emptyChanges();
  const off = withToggle(start, pack.id, false);
  check("a change never mutates what was shown", Object.keys(start.on).length === 0);
  check("switching an item off is recorded by its id", off.on[pack.id] === false);
  const other = withBrain(off, brain, "llama3.2:1b");
  check("another brain is picked by value and stays on", other.model === "llama3.2:1b" && other.on[brain.id] === true);
  const none = withBrain(other, brain, BRAIN_OFF);
  check("no brain now drops the model and switches it off", none.model === undefined && none.on[brain.id] === false);
  check("the internet answer is carried as its value", withReach(start, "ask_each_time").reach === "ask_each_time");

  const page0 = choicePage(summary.model_choices, "simple", 0);
  const page1 = choicePage(summary.model_choices, "simple", 1);
  check("a young builder sees at most three choices at once", page0.shown.length === 3 && page0.more);
  check("and the rest on the next page", page1.shown.length === 1 && !page1.more);
  check("the standard wording shows the list", choicePage(summary.model_choices, "standard", 0).shown.length === 4);

  const texts = summaryTexts(summary);
  const bad = texts.flatMap((text) => bannedIn(text).map((word) => `${word} in "${text}"`));
  check("no banned word anywhere on the summary", bad.length === 0, bad.join("; "));
  check("the banned list does catch the old lingo",
    bannedIn("What may the FINISHED BOT reach? sealed, island, enumerated").length === 3);
  check("and catches a bracketed id", bannedIn("Where you meet it   [crew:roster]").includes("[bracketed id]"));
  check("and a status line", bannedIn("  status: ready").includes("status:"));
  check("but not a word that merely contains one", bannedIn("outdoors, tiered cake, zoned out").length === 0);

  // The home town: asked once, as a field on the weather line, only when the
  // backend says the plan needs it.
  check("no town field when the plan does not ask", !townOnLine(summary, weather));
  check("and no town is sent", withTown(start, summary, "Boulder").town === undefined);
  const weatherOn = { ...weather, on: true };
  const asking: Summary = {
    ...summary,
    town: {
      ask: true, item: weather.id, question: "Which town do you live in? (just the town, for the weather)",
      help: "Only the town name is sent, once.", skip: "Press Enter to skip.",
      placeholder: "Springfield, Missouri", value: "",
    },
  };
  check("the town field sits on the line the backend named", townOnLine(asking, weatherOn));
  check("and only that line", !townOnLine(asking, pack));
  check("not while that line is off", !townOnLine(asking, weather));
  check("the typed town rides along with Looks good",
    withTown(start, asking, "  Boulder,   Colorado ").town === "Boulder, Colorado");
  check("an empty town is a skip, sent as empty", withTown(start, asking, "   ").town === "");
  const townBad = summaryTexts(asking).flatMap((t) => bannedIn(t));
  check("the town words are plain too", townBad.length === 0, townBad.join(","));
}

console.log("\nthe browser is never looser than the terminal about the PIN");
{
  check("empty is refused", pinRefusal("") !== "");
  check("nine digits are refused", pinRefusal("739402816") !== "");
  check("ten varied digits are fine", pinRefusal("7394028165") === "", pinRefusal("7394028165"));
  check("a run up the keyboard is refused", pinRefusal("0123456789") !== "");
  check("a repeated pattern is refused", pinRefusal("1212121212") !== "");
  check("a common word is refused", pinRefusal("password") !== "");
  check("spaces are refused", pinRefusal("my vault pin 42") !== "");
  check("a mixed eight is fine", pinRefusal("tr4in-yard") === "", pinRefusal("tr4in-yard"));
}

console.log("\nthere is one face: the old screens are gone");
{
  const conversation = readFileSync(join(process.cwd(), "app", "onboarding", "Conversation.tsx"), "utf8");
  const page = readFileSync(join(process.cwd(), "app", "onboarding", "page.tsx"), "utf8");
  const review = readFileSync(join(process.cwd(), "app", "onboarding", "review", "page.tsx"), "utf8");
  check("/onboarding draws the conversation", page.includes("<Conversation />"));
  check("the old review route sends people to it", review.includes('redirect("/onboarding")'));
  check("no row-opening gate survives in the page",
    !/canBuild|unopenedConsequential|gateLabel/.test(conversation));
  check("the page approves through the backend, once",
    (conversation.match(/approveSummary\(/g) || []).length === 1);
  check("the page keeps no question list of its own",
    !/What kind of bot do you want/.test(conversation));
}

console.log(failures === 0 ? "\nwizard review: PASS" : `\nwizard review: ${failures} FAILURE(S)`);
if (failures > 0) process.exit(1);
