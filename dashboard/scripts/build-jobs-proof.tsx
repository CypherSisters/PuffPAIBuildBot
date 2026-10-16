// The jobs screens (home rows, job parts, wizard How lines, the one setup
// list), proven without a browser: two bots' GET /build/status answers in
// the backend's shape (scripts/fixtures: a StargazerBot-like and a
// WeddingBot-like; their `parts` map stands in for build.json parts and is
// what GET /build/job/<id> sends) are read and rendered to markup; the
// status/route/kind lists are checked against src/puffpai/build/schema.py
// (or BUILD_SCHEMA_PY) when it is there; the old feed pages' redirect and
// rail rules are checked on the same answers.
//
// Run from the dashboard directory (part of `npm run test:gates`):
//   npm run test:jobs
//
// Copyright (c) Cypher Sisters. Apache-2.0 License.
import * as fs from "fs";
import * as path from "path";
import { renderToStaticMarkup } from "react-dom/server";
import {
  FALLBACK_WORDS,
  JOB_ROUTES,
  JOB_STATUSES,
  SETUP_KINDS,
  feedThingId,
  handoffTitle,
  hasBuildable,
  howLine,
  jobForThing,
  openRows,
  placedThings,
  readBuildStatus,
  readJobReply,
  refThingIds,
  refWords,
  statusWords,
  thingIdOf,
  withoutPlacedFeeds,
  type BuildStatus,
  type Job,
} from "../app/_lib/buildJobs";
import { HowRows, IngredientRows, JobRows, NotUsed, SetupList, TeamLines } from "../app/_components/Jobs/BuildViews";
import { partViews, screenCandidates, screenFor, thingForRef } from "../app/_components/Jobs/jobParts";
import type { Thing } from "../app/_lib/services/myThingsService";
import { isApiAddress, itemLink } from "../app/_lib/safeLink";

let failed = 0;
function check(name: string, ok: boolean): void {
  console.log(`${ok ? "PASS" : "FAIL"} ${name}`);
  if (!ok) failed += 1;
}

const DASH = path.resolve(__dirname, "..", "..");
type Raw = {
  jobs: { id: string; ingredients?: string[] }[];
  parts: Record<string, { name: string; class: string; check?: unknown }>;
  words: { class: Record<string, string> };
};
const raw = (name: string): Raw => JSON.parse(fs.readFileSync(path.join(DASH, "scripts", "fixtures", name), "utf-8"));
const spaceRaw = raw("build_status_stargazerbot.json");
const weddingRaw = raw("build_status_weddingbot.json");
const space: BuildStatus = readBuildStatus(spaceRaw);
const wedding: BuildStatus = readBuildStatus(weddingRaw);

/** GET /build/job/<id> as build/api.py job_view answers it. */
function jobReply(r: Raw, id: string): Job | null {
  const job = r.jobs.find((j) => j.id === id);
  const parts = (job?.ingredients || []).map((ref) => ({
    ref,
    name: r.parts[ref]?.name || ref,
    class: r.parts[ref]?.class || "",
    kind: r.words.class[r.parts[ref]?.class] || "",
    check: r.parts[ref]?.check ?? null,
  }));
  return readJobReply({ ok: true, job, parts, setup: [], words: r.words });
}
const noop = () => undefined;
const nameFrom = (r: Raw) => (ref: string) => r.parts[ref]?.name || refWords(ref);

/** Owner-facing words rules: no em dash, never door(s), no beta. */
function plain(html: string): boolean {
  const words = html.replace(/<[^>]+>/g, " ");
  return !words.includes("—") && !/\bdoors?\b/i.test(words) && !/\bbeta\b/i.test(words);
}

// ── reading the answer ──────────────────────────────────────────────────────
check("StargazerBot: every job is read", space.jobs.length === 6);
check("a status the dashboard does not know reads as planned", space.jobs[5].status === "planned");
check("a route the dashboard does not know reads as D", space.jobs[5].route === "D");
check("an empty pick (route D) reads as no pick", space.jobs[4].pick === null);
check("ingredients are refs", space.jobs[0].ingredients.map((i) => i.ref).join(",") ===
  "app:nasa_epic_earth_photos,nasa-images-api,assembled:pictures-of-earth");
check("agent ids become the names shown (sentinel is Drawbridge)",
  space.lines.map((l) => l.agent).join(",") === "Majordomo,Toolsmith,Drawbridge");
check("the setup list and the Yes are read", space.setup.length === 3 && space.yes.given && !wedding.yes.given);
const earth = jobReply(spaceRaw, "pictures-of-earth")!;
check("a job's own answer gives each part its name and kind",
  earth.ingredients[0].name === "NASA EPIC Earth photos" && earth.ingredients[0].kind === "a source of data");
check("Drawbridge's {ok, reason} reads as a check",
  earth.ingredients[0].check?.result === "pass" && earth.ingredients[0].check?.why === "Reads one public site.");
check("a part with no check has none", earth.ingredients[1].check === undefined);
check("no answer at all still reads", readBuildStatus(null).jobs.length === 0 && readBuildStatus("x").setup.length === 0);

// ── one source of words ─────────────────────────────────────────────────────
check("status words come from the answer", statusWords(space.words, "building") === "Being built");
check("other words in the answer are used as sent",
  statusWords(readBuildStatus({ words: { status: { not_yet: "Not working yet" } } }).words, "not_yet") === "Not working yet");
check("an answer without words falls back to the same words",
  statusWords(readBuildStatus({}).words, "needs_setup") === "Needs setup" &&
  JSON.stringify(readBuildStatus({}).words.route) === JSON.stringify(space.words.route));
check("the fallback table has a word for every status and route",
  JOB_STATUSES.every((s) => FALLBACK_WORDS.status[s]) && JOB_ROUTES.every((r) => FALLBACK_WORDS.route[r]));
check("the fallback words are plain", plain(JSON.stringify(FALLBACK_WORDS)));

const schemaPath = process.env.BUILD_SCHEMA_PY || path.join(DASH, "..", "src", "puffpai", "build", "schema.py");
if (fs.existsSync(schemaPath)) {
  const src = fs.readFileSync(schemaPath, "utf-8");
  const has = (v: string) => src.includes(`"${v}"`) || src.includes(`'${v}'`);
  check("every job status the dashboard draws is in build/schema.py", JOB_STATUSES.every(has));
  check("every route is in build/schema.py", JOB_ROUTES.every(has));
  check("every setup kind is in build/schema.py", SETUP_KINDS.every(has));
  const kinds = /SETUP_KINDS\s*=\s*\(([^)]*)\)/.exec(src);
  check("build/schema.py has no setup kind the dashboard does not know",
    !!kinds && (kinds[1].match(/"([a-z_]+)"/g) || ([] as string[])).every((s) => (SETUP_KINDS as readonly string[]).includes(s.slice(1, -1))));
  const statuses = /STATUSES\s*=\s*\(([^)]*)\)/.exec(src);
  check("build/schema.py has as many statuses as the dashboard",
    !!statuses && statuses[1].split(",").filter((s) => s.trim()).length === JOB_STATUSES.length);
} else {
  console.log("SKIP build/schema.py is not in this tree yet (lane F1); set BUILD_SCHEMA_PY to check it");
}

// ── home: one line per job ──────────────────────────────────────────────────
const home = renderToStaticMarkup(<JobRows jobs={space.jobs} words={space.words} />);
check("home: every job has a line", space.jobs.every((j) => home.includes(j.name)));
check("home: every job opens its own page", space.jobs.every((j) => home.includes(`href="/dashboard/jobs/${j.id}"`)));
check("home: status words as the answer says them",
  ["Working", "Not yet", "Needs setup", "Nothing found yet"].every((w) => home.includes(w)));
check("home: why not yet is said", home.includes("Its outside service is off.") && home.includes("It needs your home town."));
check("home: nothing found offers Find more", home.includes("/dashboard/add?find=Name%20that%20star"));
check("home: words are plain", plain(home));
const homeW = renderToStaticMarkup(<JobRows jobs={wedding.jobs} words={wedding.words} />);
check("home (WeddingBot): waiting for the yes is said", homeW.includes("Waiting for your yes"));

const notUsed = renderToStaticMarkup(
  <NotUsed dropped={space.dropped} removable={(r) => r === "skill-horoscope"} onRemove={noop} />,
);
check("Not used: folded, with a count", notUsed.startsWith("<details") && notUsed.includes("Not used (2)"));
check("Not used: says why", notUsed.includes("Not about any of this bot&#x27;s jobs."));
check("Not used: Remove only for what is on this computer", (notUsed.match(/>Remove</g) || []).length === 1);
check("Not used: nothing when nothing was dropped",
  renderToStaticMarkup(<NotUsed dropped={[]} removable={() => true} onRemove={noop} />) === "");

// ── job page: ingredients and screen ────────────────────────────────────────
const things: Thing[] = [
  { id: "feed-nasa_epic_earth_photos", kind: "feed", name: "NASA EPIC Earth photos", icon: "", blurb: "", on_home: true, page: "/dashboard/things/feed-nasa_epic_earth_photos" },
  { id: "assembled-pictures-of-earth", kind: "app", name: "Earth pictures", icon: "", blurb: "", on_home: false, page: "/dashboard/things/assembled-pictures-of-earth" },
  { id: "book-a-field-book", kind: "book", name: "A Field Book of the Stars", icon: "", blurb: "", on_home: false, page: "/dashboard/things/book-a-field-book" },
];
check("a live card ref finds its thing", thingForRef("app:nasa_epic_earth_photos", undefined, things)?.id === "feed-nasa_epic_earth_photos");
check("an assembled app ref finds its thing", thingForRef("assembled:pictures-of-earth", undefined, things)?.id === "assembled-pictures-of-earth");
check("a find's own id finds its thing by name", thingForRef("book-gutenberg-12345", "A Field Book of the Stars", things)?.id === "book-a-field-book");
check("nothing found is nothing", thingForRef("nasa-images-api", "NASA Image and Video Library", things) === undefined);

const parts = {
  "app:nasa_epic_earth_photos": { thing: things[0], feedOn: true },
  "assembled:pictures-of-earth": { thing: things[1] },
};
const views = partViews(earth, parts);
check("parts: pick first, each once", views.map((v) => v.ref).join(",") ===
  "app:nasa_epic_earth_photos,nasa-images-api,assembled:pictures-of-earth");
check("parts: a live card has its online switch, its thing id and no old page link",
  views[0].toggle?.on === true && views[0].thingId === "feed-nasa_epic_earth_photos" && views[0].href === undefined && views[0].removable);
check("parts: the kind is the backend's words", views[0].kind === "a source of data");
check("parts: Drawbridge's check is shown", views[0].check === "Drawbridge: OK. Reads one public site.");
check("parts: something not on this computer says so and has no Remove",
  views[1].kind === "a source of data, not added yet" && !views[1].removable && views[1].name === "NASA Image and Video Library");
check("parts: an app BuildBot put together says so", views[2].kind === "app BuildBot put together");
const routine = partViews(jobReply(weddingRaw, "reminders")!, {});
check("parts: a built-in has no Remove and is not 'not added yet'",
  !routine[0].removable && routine[0].name === "Your bot's reminders" && !routine[0].kind.includes("not added"));
const ingHtml = renderToStaticMarkup(<IngredientRows items={views} onToggle={noop} onRemove={noop} confirming="assembled:pictures-of-earth" />);
check("ingredients render name, kind, switch and Remove",
  ingHtml.includes("NASA EPIC Earth photos") && ingHtml.includes("a source of data") &&
  ingHtml.includes('role="switch"') && ingHtml.includes("Online: on") && ingHtml.includes(">Remove<"));
check("Remove asks once more before it removes", ingHtml.includes("Yes, remove it"));
check("ingredients: words are plain", plain(ingHtml));

check("screen: an app BuildBot put together comes before the pick",
  screenCandidates(earth).join(",") === "assembled-pictures-of-earth,app-nasa_epic_earth_photos");
check("screen: refs are matched to the things found for them",
  screenCandidates(jobReply(weddingRaw, "guest-list")!, (r) => (r === "gh-guest-list-manager" ? "app-glm" : thingIdOf(r)))[0] === "app-glm");
const pageBase = { ok: true, kind: "app" as const, kind_words: "app", name: "Guest List Manager", icon: "", from: "", on_home: false,
  recipe: { title: "Guest List Manager", icon: "", line: "", try: [], can: [], made_by: "words" as const }, improving: false };
check("screen: a running app on 127.0.0.1 opens in her own browser, not our frame",
  screenFor({ ...pageBase, id: "app-glm", host: { state: "running", url: "http://127.0.0.1:5173/", can_run: true } })?.kind === "window");
check("screen: an app BuildBot put together itself is framed",
  screenFor({ ...pageBase, id: "assembled-news", host: { state: "running", url: "http://127.0.0.1:5174/", can_run: true } })?.kind === "frame");
check("screen: an address that is not this computer is never framed",
  screenFor({ ...pageBase, id: "app-glm", host: { state: "running", url: "http://evil.example:80/", can_run: false } }) === null);
check("screen: a stopped app offers Start",
  screenFor({ ...pageBase, id: "app-glm", host: { state: "stopped", url: "http://127.0.0.1:5173/", can_run: true } })?.kind === "stopped");
check("screen: a tool server links to its own page (its MCP screens stay there)",
  screenFor({ ...pageBase, id: "tool-sky", kind: "tool_server" })?.kind === "link");

// ── wizard handoff: How lines, the one setup list ───────────────────────────
const wJobs = wedding.jobs.map((j) => jobReply(weddingRaw, j.id)!);
const how = renderToStaticMarkup(<HowRows jobs={wJobs} words={wedding.words} nameOf={nameFrom(weddingRaw)} showStatus={false} />);
check("How: route A uses the backend's words and names the app", how.includes("Uses it as it is: Guest List Manager"));
check("How: route C fills <view> from the answer's view words and names its sources",
  how.includes("Puts together a map from Places directory"));
check("How: route B names what it connects", how.includes("Connects a tool: Your bot&#x27;s reminders"));
check("How: no status before the Yes", !how.includes("Waiting for your yes"));
const howS = renderToStaticMarkup(<HowRows jobs={space.jobs} words={space.words} nameOf={nameFrom(spaceRaw)} showStatus />);
check("How: route D says nothing found yet", howS.includes("Nothing found yet"));
check("How: after the Yes each job shows its status", howS.includes("Working") && howS.includes("Needs setup"));
check("How: a C job names its view in words and every source but the app itself",
  howLine(earth, space.words, nameFrom(spaceRaw)) ===
  "Puts together a picture gallery from NASA EPIC Earth photos and NASA Image and Video Library");
check("How: older {slot} words still work",
  howLine(wJobs[0], { ...wedding.words, route: { ...wedding.words.route, A: "Uses {pick} as it is" } }, nameFrom(weddingRaw)) ===
  "Uses Guest List Manager as it is");

const setup = renderToStaticMarkup(<SetupList rows={wedding.setup} jobs={wedding.jobs} busy="" said={{ "home-town": "Saved." }} onDo={noop} />);
check("setup: one list with every row (the sites in ONE websites row, each named under Show which)",
  wedding.setup.every((r) => setup.includes(r.label) || setup.includes(r.label.replace(/^(Turn on|Connect to|Allow) /, ""))) &&
    (setup.match(/Show which/g) || []).length === 1);
check("setup: a key is a hidden field that goes to the vault",
  setup.includes('type="password"') && setup.includes("Keep in my vault"));
check("setup: before the Yes, programs, packages and the outside address come with it",
  (setup.match(/Comes with your Yes/g) || []).length === 3 && !setup.includes(">Allow<"));
const setupAfter = renderToStaticMarkup(<SetupList rows={wedding.setup} jobs={wedding.jobs} busy="" said={{}} yesGiven onDo={noop} />);
check("setup: after the Yes, a site it could not switch on has its own button", setupAfter.includes(">Allow<"));
check("setup: a done row says Done and has no field", setup.includes(">Done<") && (setup.match(/<input/g) || []).length === 1);
check("setup: each row names the jobs it is for", setup.includes("For: Venues near us") && setup.includes("For: Guest list"));
check("setup: the backend's answer to a row is shown", setup.includes("Saved."));
const setupS = renderToStaticMarkup(<SetupList rows={space.setup} jobs={space.jobs} busy="" said={{}} yesGiven={space.yes.given} onDo={noop} />);
check("setup: a home town is a text field with Save", setupS.includes('type="text"') && setupS.includes(">Save<"));
check("setup: after the Yes, a switch still off turns on", setupS.includes(">Turn on<"));
check("setup: words are plain", plain(setup) && plain(setupS) && plain(setupAfter));
check("the Yes is allowed with rows not done: only what needs her own input is counted",
  openRows(wedding.setup).map((r) => r.id).join(",") === "key-places");
check("after the Yes, a site it could not switch on is hers again",
  openRows(wedding.setup, true).map((r) => r.id).join(",") === "key-places,address-places");
check("programs and packages are not rows she has to do", !openRows(wedding.setup, true).some((r) => r.kind === "program" || r.kind === "package"));

const siteRows = [
  { id: "switch-a", kind: "switch" as const, label: "Turn on A", detail: "Your bot reaches a.example for it.", job_ids: [], done: false, by_yes: true, hosts: ["a.example"] },
  { id: "switch-b", kind: "switch" as const, label: "Turn on B", detail: "b.example, and the picture sites its results point to.", job_ids: [], done: false, by_yes: true },
  { id: "switch-look", kind: "switch" as const, label: "Take one look at each website first", detail: "BuildBot takes one look at each website before adding it.", job_ids: [], done: false, by_yes: true },
  { id: "setting-go-online", kind: "setting" as const, label: "Let StargazerBot go online", detail: "", job_ids: [], done: false, by_yes: true },
];
const grouped = renderToStaticMarkup(<SetupList rows={siteRows} jobs={[]} busy="" said={{}} bot="StargazerBot" onDo={noop} />);
check("setup: the websites row counts the sites it lists (not the look, not going online)",
  grouped.includes("Let StargazerBot go online and reach 2 websites"));

const queuedJob = readBuildStatus({ ready: true, jobs: [{ id: "launches", route: "C", status: "queued" }],
  yes: { given: true, sha: "a".repeat(64) } });
check("after the Yes, a job not started yet waits its turn (never 'waiting for your yes')",
  queuedJob.jobs[0].status === "queued" && statusWords(queuedJob.words, "queued") === "Waiting its turn");

// ── seams (f-merge): part names, book picks, the title, getting ready ───────
const named = readBuildStatus({
  ready: true,
  bot: "StargazerBot",
  jobs: [{ id: "facts", route: "B", pick: { ref: "book:stars", class: "TEXT" }, ingredients: ["book:stars"], status: "waiting_yes" },
         { id: "photos", route: "C", view: "gallery", pick: { ref: "seed:epic", class: "DATA" }, ingredients: ["seed:epic"], status: "waiting_yes" }],
  parts: [{ ref: "book:stars", name: "A Field Book of the Stars", class: "TEXT", kind: "a book" },
          { ref: "seed:epic", name: "NASA EPIC Earth photos", class: "DATA", kind: "a source of data" }],
  yes: { given: false, sha: "a".repeat(64) },
  words: space.words,
  setup: [],
});
check("status: part names come with the status (no call per job)",
  named.jobs[0].ingredients[0].name === "A Field Book of the Stars" && named.jobs[1].ingredients[0].kind === "a source of data");
check("How: a book pick answers from it, it does not connect a tool",
  howLine(named.jobs[0], named.words, refWords) === "Answers from A Field Book of the Stars");
check("How: the StargazerBot facts job answers from its book",
  howLine(jobReply(spaceRaw, "space-facts")!, space.words, nameFrom(spaceRaw)) === "Answers from A Field Book of the Stars");
check("How: a guide with no name known still answers from it",
  howLine({ ...named.jobs[0], pick: { ref: "g", class: "GUIDE", score: 0, why: "" }, ingredients: [] }, named.words, () => "") ===
  "Answers from a guide");
check("How: a data job still puts its view together",
  howLine(named.jobs[1], named.words, refWords) === "Puts together a picture gallery from NASA EPIC Earth photos");
const howBook = renderToStaticMarkup(<HowRows jobs={named.jobs} words={named.words} nameOf={refWords} showStatus={false} />);
check("How: never says a book is a tool it connects", !howBook.includes("Connects a tool: A Field Book"));
check("title: not built before her Yes", handoffTitle(named) === "StargazerBot is ready to build");
check("title: building after the Yes",
  handoffTitle({ ...named, yes: { ...named.yes, given: true }, running: true }) === "Building...");
check("title: built when nothing is left to make",
  handoffTitle({ ...named, yes: { ...named.yes, given: true }, jobs: named.jobs.map((j) => ({ ...j, status: "working" as const })) }) === "Built");
check("title: a bot with no name yet", handoffTitle({ ...named, bot: "" }) === "Your bot is ready to build");
check("title: plain words", plain(handoffTitle(named)) && !/\bbuilt\b/i.test(handoffTitle(named)));
check("the Yes sends the list's sha; a bad one is not kept",
  named.yes.sha === "a".repeat(64) && readBuildStatus({ yes: { sha: "abc" } }).yes.sha === "");
const notReady = readBuildStatus({ ready: false, line: "Your bot is getting ready." });
check("getting ready is read, an older answer is ready", !notReady.ready && notReady.line.includes("getting ready") &&
  readBuildStatus({}).ready);

// ── a website's live card is an ingredient of its job only ──────────────────
const seeded = readBuildStatus({
  jobs: [{ id: "venues", route: "C", pick: { ref: "seed:venues", class: "DATA" }, ingredients: ["seed:venues"] }],
  parts: [{ ref: "seed:venues", name: "Venue Finder", class: "DATA", kind: "a source of data", things: ["feed-venue_finder", "../x"] }],
});
check("the live card her Yes made is read as its part (bad ids dropped)",
  seeded.parts[0].things.join(",") === "feed-venue_finder");
check("its old pages go to the job's page", jobForThing(seeded, "feed-venue_finder")?.id === "venues");
check("it has no sidebar entry of its own",
  (withoutPlacedFeeds([{ id: "venue_finder" }, { id: "my_recipes" }], seeded) || []).map((a) => a.id).join(",") === "my_recipes");
check("it counts as placed (no tile of its own)", placedThings(seeded).has("feed-venue_finder"));

// ── an item opens its human page or nothing ─────────────────────────────────
check("an item's raw API record is never a link",
  itemLink("https://ll.thespacedevs.com/2.3.0/launches/e3df2ecd/") === undefined &&
  itemLink("https://api.example.org/x") === undefined && itemLink("https://x.example/a.json") === undefined &&
  itemLink("https://x.example/a?format=json") === undefined);
check("an item's own web page is a link",
  itemLink("https://www.spacelaunchnow.me/launch/falcon-9") === "https://www.spacelaunchnow.me/launch/falcon-9");
check("an address that is not https is nothing", itemLink("javascript:alert(1)") === undefined && itemLink("http://x.example/") === undefined);
check("isApiAddress reads the backend's rule", isApiAddress("https://x.example/v1/items") && !isApiAddress("https://x.example/about"));

const team = renderToStaticMarkup(<TeamLines lines={space.lines} />);
check("team lines name Toolsmith and Drawbridge", team.includes("Toolsmith") && team.includes("Drawbridge"));

// ── old pages go to the job page ────────────────────────────────────────────
check("a ref names one thing", thingIdOf("assembled:pictures-of-earth") === "assembled-pictures-of-earth");
check("an app ref may be a live card or the app", refThingIds("app:x").join(",") === "feed-x,app-x");
check("a ref that is not a thing id is nothing", thingIdOf("../etc") === "" && thingIdOf("A B") === "");
check("an app page's live card id", feedThingId("nasa_epic_earth_photos") === "feed-nasa_epic_earth_photos");
check("/dashboard/apps/<feed id> goes to the job that uses it",
  jobForThing(space, feedThingId("nasa_epic_earth_photos"))?.id === "pictures-of-earth");
check("/dashboard/things/feed-<id> goes to the job that uses it",
  jobForThing(space, "feed-launch_library_2_the_space")?.id === "rocket-launches");
check("a card no job uses keeps its page", jobForThing(space, "feed-something_else") === null);
check("no answer, no redirect", jobForThing(null, "feed-x") === null);
check("placed things include Not used", placedThings(space).has("skill-horoscope"));
const apps = [{ id: "stargazerbot", own: true }, { id: "nasa_epic_earth_photos" }, { id: "launch_library_2_the_space" }, { id: "my_recipes" }];
check("the rail drops live cards the build placed, keeps the rest",
  (withoutPlacedFeeds(apps, space) || []).map((a) => a.id).join(",") === "stargazerbot,my_recipes");
check("the bot's own page keeps its rail entry even if a job names it",
  (withoutPlacedFeeds([{ id: "stargazerbot", own: true }], readBuildStatus({ jobs: [{ id: "a", ingredients: ["app:stargazerbot"] }] })) || []).length === 1);
check("the rail is unchanged without an answer", withoutPlacedFeeds(apps, null) === apps);

// Her first apps on the plan's one Yes (build/api.py first_apps).
{
  const got = readBuildStatus({
    first_apps: [
      { id: "fitfam", name: "FitFam", line: "Turn on: FitFam (one of your first apps)" },
      { id: "", name: "x", line: "dropped" },
    ],
  });
  check("the Yes lists her first app to turn on",
    (got.firstApps ?? []).map((a) => a.line).join("|") === "Turn on: FitFam (one of your first apps)");
  check("a plan that only turns on a first app is still something to say yes to", hasBuildable(got));
  check("no first apps: nothing listed", (readBuildStatus({}).firstApps ?? []).length === 0);
}

console.log(failed ? `${failed} failed` : "all passed");
process.exit(failed ? 1 : 0);
