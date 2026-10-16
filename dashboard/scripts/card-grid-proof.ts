// The home page's live-card grid: order and tile size, proven without a browser.
//
// Run from the dashboard directory (part of `npm run test:gates`):
//   npx tsc scripts/card-grid-proof.ts --outDir .proofout --module commonjs --target es2020 --moduleResolution node --skipLibCheck && node .proofout/scripts/card-grid-proof.js
//
// Copyright (c) Cypher Sisters. Apache-2.0 License.
import {
  FEED_POLL_MS, FEED_SOON_MS, FEED_SOON_TRIES, feedPollMs, orderCards, tileClasses, tileSize,
} from "../app/_lib/cardGrid";

let failed = 0;
function check(name: string, ok: boolean): void {
  console.log(`${ok ? "PASS" : "FAIL"} ${name}`);
  if (!ok) failed += 1;
}

const cards = [
  { app_id: "epic", kind: "gallery" },
  { app_id: "weather", kind: "value" },
  { app_id: "fossils", kind: "list" },
  { app_id: "music", kind: "gallery" },
];
const ids = (xs: { app_id: string }[]) => xs.map((c) => c.app_id).join(",");

check("no placement list: the order they were added",
  ids(orderCards(cards)) === "epic,weather,fossils,music");
check("placement list first, then the rest in added order",
  ids(orderCards(cards, ["music", "weather"])) === "music,weather,epic,fossils");
check("placement entries may be objects with app_id or id",
  ids(orderCards(cards, [{ app_id: "fossils" }, { id: "music" }])) === "fossils,music,epic,weather");
check("unknown, repeated and junk placements are skipped",
  ids(orderCards(cards, ["gone", "music", "music", null, 7 as unknown as string, {}]))
    === "music,epic,weather,fossils");
check("a placement list that is not a list is ignored",
  ids(orderCards(cards, "music" as unknown as string[])) === "epic,weather,fossils,music");
check("the backend's placed rows: only kind card orders the grid",
  ids(orderCards(cards, [{ kind: "update", id: "epic" }, { kind: "card", id: "fossils" },
    { kind: "look", id: "music" }])) === "fossils,epic,weather,music");
check("a card left off the home page is not on the grid",
  ids(orderCards(cards, [], ["weather"])) === "epic,fossils,music");
check("a placed card shows even if it is also listed as off",
  ids(orderCards(cards, ["weather"], ["weather"])) === "weather,epic,fossils,music");
check("junk in the off list is ignored",
  ids(orderCards(cards, null, [7, null, "gone"])) === "epic,weather,fossils,music");
check("every card shows exactly once",
  orderCards(cards, ["weather", "weather"]).length === cards.length);

check("a gallery gets a big tile", tileSize("gallery") === "wide");
check("a picture gets a big tile", tileSize("image") === "wide");
check("a list is tall enough to read", tileSize("list") === "tall");
check("a number is compact", tileSize("value") === "small");
check("an unknown kind is compact", tileSize(undefined) === "small");
check("big tiles span two columns and rows from small screens up",
  tileClasses("wide") === "sm:col-span-2 sm:row-span-2");
check("on a phone every tile is one column", !tileClasses("tall").includes(" col-span"));

check("a card just switched on with no data yet is read again within seconds",
  feedPollMs({ allowed: true, card: null }, 0) === FEED_SOON_MS && FEED_SOON_MS <= 5_000);
check("a tile that has not loaded yet is read again soon", feedPollMs(null, 0) === FEED_SOON_MS);
check("a card with data waits the slow beat", feedPollMs({ allowed: true, card: { kind: "value" } }, 0) === FEED_POLL_MS);
check("a card that is not switched on waits the slow beat",
  feedPollMs({ allowed: false, card: null }, 0) === FEED_POLL_MS);
check("quick reads stop after the limit",
  feedPollMs({ allowed: true, card: null }, FEED_SOON_TRIES) === FEED_POLL_MS
  && FEED_SOON_TRIES * FEED_SOON_MS <= 2 * 60_000);

if (failed) {
  console.log(`${failed} check(s) failed`);
  process.exit(1);
}
console.log("card grid: all checks passed");
