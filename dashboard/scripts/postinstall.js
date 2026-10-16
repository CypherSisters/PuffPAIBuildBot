/**
 * Postinstall script for PuffPAI dashboard.
 * 1. Copies talkinghead playback-worklet.js to public/
 * 2. Patches talkinghead's dynamic import() to use a static import map
 *    so bundlers (Turbopack, webpack) don't choke on the non-literal import.
 *
 * Copyright (c) 2025 Cypher Sisters. MIT License.
 */
const fs = require("fs");
const path = require("path");

const TALKINGHEAD_MJS = path.join(
  __dirname,
  "..",
  "node_modules",
  "talkinghead",
  "modules",
  "talkinghead.mjs"
);

// Step 1: Reconcile the playback worklet between node_modules and public/.
// The pinned talkinghead git dependency does not reliably ship
// modules/playback-worklet.js into node_modules on a fresh install, but the
// bundler still needs to resolve `new URL('./playback-worklet.js', ...)` at
// build time. A copy is committed in public/, so:
//   - if node_modules has it, refresh the public/ copy (keeps them in sync);
//   - if node_modules is missing it, restore it from the committed public/ copy
//     so the build can resolve the import.
const workletModule = path.join(
  __dirname,
  "..",
  "node_modules",
  "talkinghead",
  "modules",
  "playback-worklet.js"
);
const workletPublic = path.join(__dirname, "..", "public", "playback-worklet.js");
if (fs.existsSync(workletModule)) {
  fs.copyFileSync(workletModule, workletPublic);
  console.log("[postinstall] Copied playback-worklet.js to public/");
} else if (fs.existsSync(workletPublic)) {
  fs.copyFileSync(workletPublic, workletModule);
  console.log("[postinstall] Restored playback-worklet.js into node_modules/talkinghead/modules/");
}

// Step 2: Patch talkinghead dynamic import
if (fs.existsSync(TALKINGHEAD_MJS)) {
  let src = fs.readFileSync(TALKINGHEAD_MJS, "utf8");

  // Check if already patched
  if (src.includes("// PATCHED: static lipsync import map")) {
    console.log("[postinstall] talkinghead already patched, skipping");
    process.exit(0);
  }

  // Replace the dynamic import() with a static import map
  // Original:
  //   const moduleName = path + 'lipsync-' + lang.toLowerCase() + '.mjs';
  //   const className = 'Lipsync' + lang.charAt(0).toUpperCase() + lang.slice(1);
  //   import(moduleName).then( module => {
  //     this.lipsync[lang] = new module[className];
  //   });
  const oldCode = `const moduleName = path + 'lipsync-' + lang.toLowerCase() + '.mjs';
      const className = 'Lipsync' + lang.charAt(0).toUpperCase() + lang.slice(1);
      import(moduleName).then( module => {
        this.lipsync[lang] = new module[className];
      });`;

  const newCode = `// PATCHED: static lipsync import map (avoids non-literal dynamic import)
      const className = 'Lipsync' + lang.charAt(0).toUpperCase() + lang.slice(1);
      const loaders = {
        'en': () => import('./lipsync-en.mjs'),
        'fi': () => import('./lipsync-fi.mjs'),
        'de': () => import('./lipsync-de.mjs'),
        'fr': () => import('./lipsync-fr.mjs'),
        'lt': () => import('./lipsync-lt.mjs'),
      };
      const loader = loaders[lang.toLowerCase()];
      if (loader) {
        loader().then( module => {
          this.lipsync[lang] = new module[className];
        });
      }`;

  if (src.includes(oldCode)) {
    src = src.replace(oldCode, newCode);
    fs.writeFileSync(TALKINGHEAD_MJS, src, "utf8");
    console.log("[postinstall] Patched talkinghead dynamic import -> static import map");
  } else {
    console.log("[postinstall] WARNING: Could not find expected code to patch in talkinghead.mjs");
    console.log("[postinstall] The library may have been updated. Manual patch may be needed.");
  }
} else {
  console.log("[postinstall] talkinghead.mjs not found, skipping patch");
}
