# PuffPAI Avatar System

Pluggable, user-switchable avatars for the PuffPAI dashboard.

_Copyright (c) 2026 Cypher Sisters. Apache-2.0 License._

---

## 1. What this is

The dashboard shows a talking avatar in three places (home tile, chat overlay,
full avatar page). Users can **choose** their avatar: the cloud mascot, a 3D
digital human, or their **own** 3D model. Every choice degrades gracefully
when an engine can't load.

This document describes the code layout, the pluggable **engine**
architecture, and the one library patch the 3D engine needs.

User-facing instructions, licences and the per-engine bone/morph requirements
live in [`docs/AVATARS.md`](../../docs/AVATARS.md).

---

## 2. Layout

All avatar code lives in `app/_components/Avatar/`.

| File | Role |
| --- | --- |
| `PuffCloudAvatar.tsx` | **2D SVG engine.** Inline SVG of the mascot; the mouth opens off the audio waveform (Web Audio `AnalyserNode`). No WebGL, no downloads. The reliable default. |
| `TalkingHeadAvatar.tsx` | **3D engine.** Wraps the `talkinghead` library (Three.js): full-body human, viseme lip-sync, moods, idle motion. Loaded via `dynamic(ssr:false)`. |
| `AvaturnAvatar.tsx` | **3D GLB engine** built on React Three Fiber (`@react-three/fiber` + `drei`). Loads any GLB, portrait framing, personality-reactive lighting. |
| `AvatarStage.tsx` | Renders whichever engine is chosen, with a fallback to Puff (see 3.3). |
| `AvatarTile.tsx` | Dashboard home tile: voice-first (mic, speech-to-text, the brain, Piper, lip-sync). |
| `AvatarOverlay.tsx` | Draggable floating avatar on the chat page. |
| `useAvatarCharacter.ts` | Resolves the chosen avatar. localStorage answers instantly; `/profile/ai` reconciles in the background. |
| `poses.ts`, `usePose.ts`, `usePersonalityEffects.ts`, `useSpeechAnimation.ts` | R3F helpers used by `AvaturnAvatar`. |
| `engines/types.ts`, `engines/registry.tsx` | The engine contract and the registry (see 3.1, 3.2). |

The full avatar page is `app/dashboard/avatar/page.tsx`.

Supporting state and persistence:

- `app/_stores/avatarSlice.ts`: Redux slice holding `character`
  (an `AvatarEngineId`), plus `avatarUrl`, `mood`, `speaking`, `personality`.
- `app/_lib/services/profileService.ts`: `getAISettings()` / `updateAISettings()`
  against the backend `/profile/ai` endpoint.
- `app/_interfaces/api.ts`: `AISettings` (carries `avatar_character` and
  `avatar_3d_url`).
- `app/dashboard/profile/_components/AICustomizationForm.tsx`: the picker UI.

---

## 3. Pluggable engine architecture

### 3.1 The engine contract: `engines/types.ts`

Every avatar renderer implements one **handle** and accepts one **prop shape**:

```ts
interface AvatarEngineHandle {
  speakText(text): Promise<void>;
  speakPcm(pcmBase64, sampleRate, text): Promise<void>;   // server-TTS + lipsync
  stopSpeaking(): void;
  setMood(mood): void;
  setView(view): void;
  isReady(): boolean;
}
```

Engines that can't honour a capability implement it as a **safe no-op** (the SVG
engine's `setView` does nothing) so callers never branch on engine type.

Each engine also declares **capabilities** (`lipsync`, `mood`, `view`,
`personality`, `customModel: 'glb' | 'image' | 'none'`), which drive the picker
UI and let callers feature-detect instead of hard-coding ids.

### 3.2 The registry: `engines/registry.tsx`

The single source of truth. Each entry is a **descriptor**: `id`, `label`,
`description`, `capabilities`, a lazily-loaded (`next/dynamic`, `ssr:false`)
`Component`, and an optional `defaultModelUrl`.

| id | label | engine | lipsync | custom model |
| --- | --- | --- | --- | --- |
| `puff` | Puff | `PuffCloudAvatar` (SVG) | audio-driven | none |
| `human` | Digital Human | `TalkingHeadAvatar` (talkinghead) | viseme | GLB |
| `glb` | Custom 3D Model | `AvaturnAvatar` (R3F) | motion only | GLB |

Helpers: `resolveEngine(id)` (**always** returns a usable engine; unknown ids
fall back to Puff), `isAvatarEngineId(x)` (type guard for persistence),
`listEngines()` (for building pickers). **Adding an engine = one descriptor + a
component that implements the handle.** No placement code changes.

The heavy Three.js engines are `dynamic(ssr:false)`, so they never enter the SSR
bundle and only download when actually selected; Puff (cheap, safe) is the
default and the universal fallback.

### 3.3 The stage: `AvatarStage.tsx`

Every placement renders one `AvatarStage`. It:

1. Resolves the engine by id via the registry.
2. Renders it, forwarding the caller's `ref` (React 19 passes `ref` through
   `dynamic()`) and props, defaulting `avatarUrl` to the engine's model.
3. Wraps heavy engines in an **error boundary**. If a WebGL/GLB/chunk failure
   throws during load, the stage **degrades to Puff**. The caller's ref stays
   valid because Puff implements the same handle, so speech/sink wiring keeps
   working. Puff itself is never wrapped (nowhere left to degrade to).

```tsx
<AvatarStage engineId={character} ref={avatarRef} avatarUrl={avatarUrl} mood={mood} … />
```

### 3.4 Per-user persistence

Two layers:

- **localStorage**: `puffpai_avatar_character` (engine id) and
  `puffpai_avatar_model_url` (custom GLB). Answers on first paint, no flicker.
- **Profile / backend**: `AISettings.avatar_character` and
  `AISettings.avatar_3d_url`, via `/profile/ai`. Reconciled in the background by
  `useAvatarCharacter`; the server value wins and is mirrored back to
  localStorage. Everything is best-effort: offline, localStorage decides, then
  the store default (`puff`).

`useAvatarCharacter` validates the stored/remote id against the registry
(`isAvatarEngineId`), so a stale or mistyped id can never blank the avatar.

### 3.5 The picker: `AICustomizationForm.tsx`

The Profile, AI Customization card is **registry-driven**: it maps
`listEngines()` into selectable cards, so new engines appear automatically.
Saving persists `avatar_character` and `avatar_3d_url` to the backend and
mirrors both into the live store and localStorage.

---

## 4. The default avatar, and a user's own

**No GLB ships with this repository.** The default avatar is Puff, the cloud
logo, drawn by the `puff` engine. `DEFAULT_AVATAR_MODEL_URL` is empty and the
`human` and `glb` engines have no `defaultModelUrl`, so until the user supplies
a model those engines render Puff plus one line of text pointing at the upload.
If a supplied model fails to load, `AvatarStage`'s error boundary degrades to
the always-safe Puff engine.

How a model is uploaded, where it is stored, how it is served and removed, and
why a public model URL is not accepted: see
[`docs/AVATARS.md`](../../docs/AVATARS.md). Nothing is ever written into
`dashboard/public/avatars/` at runtime: that path is inside the checkout and is
served with no pairing gate, so an uploaded face must not land there.

The contract reserves `customModel: 'image'` for a 2D "upload your own
portrait" engine; it would be one more registry entry.

---

## 5. The talkinghead worklet patch

`talkinghead.mjs` loads its AudioWorklet processor with:

```js
const workletUrl = new URL('./playback-worklet.js', import.meta.url);
```

Bundlers special-case the `new URL('./x', import.meta.url)` idiom and try to
resolve `./playback-worklet.js` as a graph asset. **Turbopack fails that
resolution** and aborts the whole dashboard build.

`scripts/postinstall.js` handles this in three steps:

1. **Copies** `playback-worklet.js` to `public/playback-worklet.js`.
2. **Patches** talkinghead's dynamic lipsync `import()` (a separate non-literal
   import) into a static import map.
3. **Rewrites** the worklet line to load the public copy instead of the module
   graph:

```js
// before
const workletUrl = new URL('./playback-worklet.js', import.meta.url);
// after (postinstall-patched)
const workletUrl = new URL('/playback-worklet.js',
  (typeof self !== 'undefined' && self.location) ? self.location.origin : 'http://localhost');
```

Because the first argument is an **absolute** path, Turbopack no longer treats
it as a bundled asset; at runtime `workletUrl.href` resolves to
`<origin>/playback-worklet.js`, served from `public/`, which is what step 1
copied. Both patches (lipsync and worklet) are **idempotent** and guarded by
independent markers, so re-running `npm install` never double-applies or skips
one.

### Keeping talkinghead

- `talkinghead` is a git dependency (`github:met4citizen/TalkingHead`), not
  versioned on npm, and needs **patching library internals** at postinstall.
  That is fragile: a library update can silently break the patch, and the
  script warns when its target text isn't found.
- It is the only engine here with real **viseme** lip-sync, and it is wired to
  the offline voice loop (`speakPcm` drives the "en" lipsync module from Piper
  PCM).
- The alternative, **React Three Fiber + GLB**, is already a dependency
  (`@react-three/fiber`, `@react-three/drei`, `three`) with a working engine
  (`AvaturnAvatar`). It has no viseme model, but it accepts **any user GLB**.

Because of the registry, replacing talkinghead is a configuration change, not a
rewrite: delete its descriptor and nothing else changes. A path to retiring it:
add viseme-to-morph-target lip-sync to the R3F engine (Ready Player Me models
expose ARKit blendshapes), then remove the `human` engine.
