# Avatars: Puff, or bring your own

**The default avatar is Puff, the PuffPAI cloud logo.** No 3D model ships with
this build. There is nothing to download or configure.

Everything below is the **bring-your-own** path: point the renderer at a hosted
`.glb` or upload one, and your 3D face replaces Puff. Clear the setting and Puff
comes back. While a 3D engine has no model, the dashboard draws Puff and says
so; chat, voice and Piper speech work the same either way.

## Three honest ways to get your own model

1. **Ready Player Me or Avaturn.** Make an avatar in your own account and
   download the `.glb`. Both produce exactly the rig and blendshapes this
   renderer wants, so this is the path that works first try. Whether you point
   it at your own face is your call; their licence applies to *you*, not to us,
   so read it before you use the result anywhere but your own screen.
2. **Blender.** Build or retarget a humanoid, name the bones as below, add shape
   keys, then *File → Export → glTF 2.0*, format **glTF Binary (.glb)**, with
   *Include → Shape Keys* and *Materials → Export* on. Mixamo-named armatures
   work: the `mixamorig` prefix is stripped on load.
3. **A CC0 / CC-BY model.** Plenty exist on Sketchfab. Most are *not* rigged the
   way this renderer needs, so check before you spend an evening. If the licence
   asks for attribution, keep it: the model's name, author and licence in a text
   file. That obligation is yours once you download it.

## What the file must be

Checked by the server before anything is written (`src/puffpai/custom_avatar.py`):

- **One `.glb` file.** glTF 2.0 binary, version 2, with its mandatory JSON
  chunk. The first four bytes must really be `glTF`. A renamed `.zip`, `.png`
  or `.exe` is refused on its content, not its name.
- **Textures embedded.** `.gltf` is deliberately not accepted: it is a manifest
  that points at sibling `.bin` and image files, and a single-file upload cannot
  carry them. A `.gltf` avatar would render as an untextured wreck.
- **32 MB or less.** A Ready Player Me or Avaturn export is 2 to 15 MB.

Checked by the renderer when it loads (the `talkinghead` engine; its checks are
at `node_modules/talkinghead/modules/talkinghead.mjs:1258` and `:1320`):

- **A full-body humanoid skeleton, with these exact bone names.** Any one
  missing and the avatar refuses to load with `Avatar object <name> not found`:

  `Hips`, `Spine`, `Spine1`, `Spine2`, `Neck`, `Head`, and for both `Left` and
  `Right`: `Shoulder`, `Arm`, `ForeArm`, `Hand`, `UpLeg`, `Leg`, `Foot`,
  `ToeBase`, plus every finger joint: `HandThumb1/2/3`, `HandIndex1/2/3`,
  `HandMiddle1/2/3`, `HandRing1/2/3`, `HandPinky1/2/3`.

- **At least one mesh with morph targets** (shape keys). None at all and the
  load fails with `Blend shapes not found`.
- Optional: a root node named `Armature` (otherwise the whole scene is used, with
  a console warning), and `LeftEye` / `RightEye` bones, without which the eyes do
  not track.

### Blendshape names, if you want the face to move

Two families, both standard, both what Ready Player Me exports:

- **ARKit 52:** `eyeBlinkLeft`, `eyeBlinkRight`, `jawOpen`, `mouthSmileLeft`,
  `mouthSmileRight`, `browInnerUp`, `noseSneerLeft`, and the rest. These drive
  moods, blinking and idle micro-expressions.
- **Oculus visemes:** `viseme_aa`, `viseme_E`, `viseme_I`, `viseme_O`,
  `viseme_U`, `viseme_PP`, `viseme_SS`, `viseme_TH`, `viseme_DD`, `viseme_FF`,
  `viseme_kk`, `viseme_nn`, `viseme_RR`, `viseme_CH`, `viseme_sil`. These are
  the mouth shapes lip-sync drives.

**What degrades without them.** Missing visemes are skipped silently: the model
loads, moods and idle motion still play, speech still comes out of the speaker,
and only the mouth stops moving with it. Missing ARKit shapes cost you the
expression that used them. A plain static mesh with no rig and no shape keys does
**not** render: you get "Avatar failed to load" and a line telling you why. Retry
will not fix that; a different export will.

## Uploading, storing, removing

Dashboard → the workbench → **Upload your own**, and pick your `.glb`. A
rejection leaves whatever was rendering exactly as it was. There is **one slot**:
uploading again replaces the previous model atomically.

The file is stored on your own machine at
`<PUFFPAI_DATA_DIR>/data/avatars/custom.glb`, with a small `custom.json` beside
it holding the original filename and upload time. It is never in the source checkout
and never in the web root. It survives restarts and upgrades. Serving it requires the
API key (`GET /avatar/custom/model.glb`); the dashboard reaches it same-origin
through its own `/api/pai` proxy, so the model is not public on your LAN.

To remove it: **Remove yours** in the workbench, or `DELETE /avatar/custom`, or
delete `custom.glb` yourself. The drawn stand-in comes back.

**Pasting a model URL is not supported.** The renderer would have to fetch from
somewhere off this machine, which is exactly the outbound request this build does
not make on its own. Download the `.glb` and upload it.
