/**
 * A job's parts as the job page draws them: each ingredient's name, its kind
 * in plain words, Drawbridge's check, its switch and whether it can be
 * removed, filled in from GET /build/job/<id> parts and from the bot's things
 * (GET /things, /things/<id>, and a live card's own feed state). Also which
 * thing's screen the page frames.
 *
 * A ref is matched to a thing by id ("assembled:x" is assembled-x, "app:x"
 * is the live card feed-x or the app app-x) and, for a find's own id, by
 * its name.
 *
 * Relative imports only (the proof script runs partViews in plain node).
 *
 * puff puff pass
 */

import { getThing, type Thing, type ThingPage } from "../../_lib/services/myThingsService";
import { getFeed } from "../../_lib/services/feedService";
import { localScreen } from "../../_lib/localScreen";
import { refThingIds, refWords, thingIdOf, type Job } from "../../_lib/buildJobs";
import { CHECK_WORDS, type IngredientView } from "./BuildViews";

const KIND_WORDS: Record<string, string> = {
  skill: "skill",
  tool_server: "tool server",
  app: "app",
  data: "data",
  book: "book",
  feed: "live card",
};

/** The finder's class for something not on this computer yet (the
 *  backend's words["class"] when the answer has none). */
const CLASS_WORDS: Record<string, string> = {
  app: "an app",
  tool: "a tool",
  data: "a source of data",
  text: "a book",
  guide: "a guide your bot reads",
};

export interface PartInfo {
  thing?: Thing;
  page?: ThingPage;
  feedOn?: boolean;
}

/** The refs a job uses: its pick first, then its parts, each once. */
export function jobRefs(job: Job): string[] {
  const out: string[] = [];
  const add = (r: string) => {
    if (r && !out.includes(r)) out.push(r);
  };
  if (job.pick) add(job.pick.ref);
  job.ingredients.forEach((i) => add(i.ref));
  return out;
}

export function feedAppId(thingId: string): string {
  return thingId.startsWith("feed-") ? thingId.slice(5) : "";
}

/** The thing a ref names, by id first, then by the part's name. */
export function thingForRef(ref: string, name: string | undefined, things: Thing[]): Thing | undefined {
  for (const id of refThingIds(ref)) {
    const hit = things.find((t) => t.id === id);
    if (hit) return hit;
  }
  const n = (name || "").trim().toLowerCase();
  return n ? things.find((t) => t.name.trim().toLowerCase() === n) : undefined;
}

/** What is known about each part. Apps are read for their screen and
 *  Start/Stop; live cards for their outside service switch. */
export async function loadParts(job: Job, things: Thing[]): Promise<Record<string, PartInfo>> {
  const out: Record<string, PartInfo> = {};
  await Promise.all(
    jobRefs(job).map(async (ref) => {
      const thing = thingForRef(ref, job.ingredients.find((i) => i.ref === ref)?.name, things);
      const info: PartInfo = { thing };
      if (thing?.kind === "feed") {
        const feed = await getFeed(feedAppId(thing.id)).catch(() => null);
        if (feed) info.feedOn = feed.allowed;
      } else if (thing?.kind === "app") {
        const r = await getThing(thing.id);
        if (r.ok) info.page = r.data;
      }
      out[ref] = info;
    }),
  );
  return out;
}

export function partViews(job: Job, parts: Record<string, PartInfo>): IngredientView[] {
  return jobRefs(job).map((ref) => {
    const ing = job.ingredients.find((i) => i.ref === ref);
    const info = parts[ref] || {};
    const thing = info.thing;
    const id = thing?.id || thingIdOf(ref);
    let kind = ing?.kind || (thing ? KIND_WORDS[thing.kind] || thing.kind : "");
    if (id.startsWith("assembled-")) kind = "app BuildBot put together";
    if (!kind && job.pick?.ref === ref) kind = CLASS_WORDS[job.pick.class.toLowerCase()] || "";
    const builtin = ref.startsWith("builtin:") || id.startsWith("builtin");
    if (!thing && !builtin) kind = kind ? `${kind}, not added yet` : "not added yet";
    const host = info.page?.host;
    const toggle =
      thing?.kind === "feed" && info.feedOn !== undefined
        ? { on: info.feedOn, onLabel: "Online: on", offLabel: "Online: off" }
        : host?.can_run
          ? { on: host.state === "running" || host.state === "starting", onLabel: "Running", offLabel: "Stopped" }
          : undefined;
    return {
      ref,
      thingId: thing?.id,
      name: ing?.name || thing?.name || refWords(ref),
      kind,
      check: ing?.check
        ? `Drawbridge: ${CHECK_WORDS[ing.check.result]}${ing.check.why ? `. ${ing.check.why}` : ""}`
        : "",
      checkTone: ing?.check?.result,
      toggle,
      // A live card's old page sends you back here, so it has no link.
      href: thing && thing.kind !== "feed" ? thing.page || `/dashboard/things/${thing.id}` : undefined,
      removable: Boolean(thing) && !builtin,
      mine: ing?.mine || undefined,
    };
  });
}

/** The things whose screen could fill the job page, best first: the job's
 *  own view thing, an app BuildBot put together for it, then its pick.
 *  `idOf` turns a ref into the thing id it was matched to. */
export function screenCandidates(job: Job, idOf: (ref: string) => string = thingIdOf): string[] {
  const ids: string[] = [];
  const add = (r?: string) => {
    const t = r ? idOf(r) : "";
    if (t && !ids.includes(t)) ids.push(t);
  };
  if (/^[a-z]+[-:]/.test(job.view)) add(job.view);
  job.ingredients.forEach((i) => {
    if (idOf(i.ref).startsWith("assembled-")) add(i.ref);
  });
  if (job.pick) add(job.pick.ref);
  return ids;
}

/** What the main area shows for one thing. */
export type Screen =
  | { kind: "frame"; thing: string; name: string; url: string }
  | { kind: "stopped"; thing: string; name: string }
  | { kind: "link"; thing: string; name: string; href: string }
  /** An app with its own web page: it opens in her own browser (127.0.0.1). */
  | { kind: "window"; thing: string; name: string; url: string };

export function screenFor(page: ThingPage): Screen | null {
  const name = page.recipe?.title || page.name;
  const host = page.host;
  const url = localScreen(host?.url) || localScreen(page.screen);
  const running = host ? host.state === "running" || host.state === "starting" : Boolean(url);
  // An app that runs in a web browser opens in her own browser, the way it is
  // built; only an app BuildBot put together itself shows inside the page.
  if (url && running)
    return page.id.startsWith("assembled-")
      ? { kind: "frame", thing: page.id, name, url }
      : { kind: "window", thing: page.id, name, url };
  if (host?.can_run) return { kind: "stopped", thing: page.id, name };
  if (page.open && page.open.startsWith("/dashboard/")) return { kind: "link", thing: page.id, name, href: page.open };
  if (page.kind === "tool_server" || page.kind === "skill")
    return { kind: "link", thing: page.id, name, href: `/dashboard/things/${page.id}` };
  return null;
}
