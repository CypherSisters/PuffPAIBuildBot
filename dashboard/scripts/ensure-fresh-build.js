/**
 * Prestart script for PuffPAI dashboard (runs automatically via the npm
 * `prestart` hook before `npm run start`).
 *
 * Rebuilds .next when any source file is newer than the last build, so a
 * restart of the PuffPAI-Dashboard scheduled task can never silently serve
 * a stale UI (.next can be hours older than source after a restart).
 *
 * Staleness = newest mtime under app/, css/, public/ or the root config
 * files is newer than .next/BUILD_ID (written at build time). A failed
 * rebuild logs a warning but still lets the previous build start.
 *
 * Copyright (c) 2026 Cypher Sisters. MIT License.
 */
const fs = require("fs");
const path = require("path");
const { spawnSync } = require("child_process");

const ROOT = path.join(__dirname, "..");
const BUILD_ID = path.join(ROOT, ".next", "BUILD_ID");
const SOURCES = [
  "app",
  "css",
  "public",
  "middleware.ts",
  "instrumentation.ts",
  "next.config.ts",
  "package.json",
];

function newestFile(p) {
  const stat = fs.statSync(p);
  if (!stat.isDirectory()) {
    return { file: p, mtimeMs: stat.mtimeMs };
  }
  let newest = { file: null, mtimeMs: 0 };
  for (const entry of fs.readdirSync(p)) {
    const candidate = newestFile(path.join(p, entry));
    if (candidate.mtimeMs > newest.mtimeMs) newest = candidate;
  }
  return newest;
}

let staleReason = null;
if (!fs.existsSync(BUILD_ID)) {
  staleReason = "no .next/BUILD_ID found";
} else {
  const buildMs = fs.statSync(BUILD_ID).mtimeMs;
  let newest = { file: null, mtimeMs: 0 };
  for (const src of SOURCES) {
    const full = path.join(ROOT, src);
    if (!fs.existsSync(full)) continue;
    const candidate = newestFile(full);
    if (candidate.mtimeMs > newest.mtimeMs) newest = candidate;
  }
  if (newest.mtimeMs > buildMs) {
    staleReason =
      `${path.relative(ROOT, newest.file)} modified ` +
      `${new Date(newest.mtimeMs).toISOString()} is newer than BUILD_ID ` +
      `${new Date(buildMs).toISOString()}`;
  }
}

if (!staleReason) {
  console.log("[ensure-fresh-build] .next build is current");
  process.exit(0);
}

console.log(`[ensure-fresh-build] stale build: ${staleReason}`);
console.log("[ensure-fresh-build] rebuilding before start...");
const result = spawnSync("npm", ["run", "build"], {
  cwd: ROOT,
  stdio: "inherit",
  shell: true,
});
if (result.status !== 0) {
  console.log(
    "[ensure-fresh-build] WARNING: build FAILED - starting previous build anyway"
  );
} else {
  console.log("[ensure-fresh-build] rebuild complete");
}
process.exit(0);
