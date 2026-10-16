# Build a persona

A **persona** is a complete subject-matter-expert bot built on BuildBot: a
**local LLM** pointed at a domain, **knowledge packs** plus apps as tools, and
**agentic capabilities** matched to the domain. HomeBot is the first.

**Be clear about what a JSON file buys, because it is not everything.** A
validated `app.json` gets you five things: your words registered with the router;
a **data store** of your declared shape, over the API the dashboard already uses;
a **page** at the route you declare, rendered generically from that store; a place
in the nav, the apps index and any pack listing you; and the four list verbs in
§3.4. It does not buy behaviour: no field may name code to run, so computing needs
Python and a screen of your own design needs a page written for it. `actions[]` is
a **phrasebook**: `says` is what a person says and `id` names it. It is never a call.

The four pieces: an **app** (`apps/<id>/app.json`, `validate_manifest`), an **app
pack** (`packs/<id>/pack.json`, `validate_pack`), **knowledge packs** (`.zim`,
see `KNOWLEDGE_PACKS.md`), and the **brain + agents** the wizard fits.

## 0. Start here: `puffpai persona new`

```bash
puffpai persona new gardenbot --name "GardenBot" \
  --goal "a garden and greenhouse assistant for a small family farm" \
  --lists "plantings,harvest log"
puffpai persona list     # the personas you have made
puffpai apps list        # every installed app, and the folders they came from
puffpai apps rescan      # re-read those folders; rescanning IS installing

# change your mind later - nothing you wrote down is lost
puffpai persona edit gardenbot --name "Greenhouse"   --add-list "seed orders" --rename-list "plantings=seedlings"
```

It validates the manifest with the real `validate_manifest` **before** writing,
writes `apps/gardenbot/app.json` + `packs/gardenbot/pack.json`, rescans, and
prints what it made. `vocabulary` comes from your list names and the meaningful
words of the goal (filler such as *family*, *small* and *help* is dropped, so it never
learns to answer for the word "help"); `data.default` holds the lists; `ui.route`
is the generic app page; the surface is `daily.notes`. The wizard decides whether to
make the same thing on **every** run ("Want a place to keep lists for this?"), not only
when nothing matched, because a goal that hits a knowledge pack still has nowhere to
write a tomato down. It does not stop to ask: it decides while it builds, after
"Looks good", and records it in the manifest's `persona` block. The answer is
yes by default; what matched only changes the reason given (for example, that a
shipped app already keeps lists).

**The id** (the folder name and the end of the URL) is made from the bot's name
when you leave it out ("GardenBot" → `gardenbot`), then from the first usable word
of the goal. Words this build already answers to are refused (`apps`, `chat`,
`knowledge`, `settings`, `vault`, `dashboard`, and more), so pick another; the
display name is separate. To change an id later, stop the backend and run `puffpai
persona edit <id> --rename-id <new>`: folder, pack and saved lists move together.

**Editing it: `puffpai persona edit`, or `PATCH /persona/<id>`** with
`{name?, add_lists?[], rename_lists?{old: new}}`, owner-made personas only. Display
name, regenerated vocabulary and `data.default` change in `app.json`, and **the live
store migrates in the same breath**: a new list arrives empty, a renamed list keeps
every item, a dropped key is left in the document, and your hand edits survive. **No
`remove_lists`**, because that deletes rows you wrote down and a PATCH carries no typed
confirmation, so it is `DELETE /persona/<id>`, which makes you type the id back.

**Where your apps live: not in the checkout.** `$PUFFPAI_STATE_DIR/apps` and
`/packs`, else `%LOCALAPPDATA%\PuffPAI\apps` (Windows), `~/Library/Application
Support/PuffPAI/apps` (macOS), `$XDG_STATE_HOME/puffpai/apps` (Linux). A
pip-installed build's `apps/` is inside site-packages, and editing that is
editing the package. Both roots are scanned, shipped first, and two folders
claiming one id is a collision: neither installs, both are named by path.
`puffpai apps list` prints the folders it read.

The rest of this page is that by hand, and the reference for editing what the
scaffold wrote.

## 1. An app

`apps/<id>/app.json`, schema `puffpai_app/v1`. What `app_registry` enforces:

- **No field may name code to run**: `handler`, `module`, `exec`, `command`,
  `script`, `plugin`, `run` and friends are refused.
- `id`: 2-32 chars, lowercase, letter first, letters/digits/underscores. `name`
  required, ASCII, short; `blurb`/`version`/`author`/`license`/`order` optional.
- `surfaces`: 1 to 8, each already in this build's taxonomy; an app **joins** a
  surface, it cannot invent one. The list: `comms.calendar` `comms.timers`
  `daily.fitness` `daily.games` `daily.kitchen` `daily.notes` `daily.weather`
  `house.cameras` `house.climate` `house.lights` `house.sensors`
  `instruments.telescope` `knowledge.library` `knowledge.maps`
  `knowledge.medical` `media.audio` `media.books` `media.video`.
- `vocabulary` (`keywords`, `examples`), `actions` (`id` + `says`), `data` (a
  `default` shape) and `ui` (`route`, `icon`) route and render the app.
- **Privacy is five fields, and half an answer is refused.** Every entry in
  `doors` (one per outside service the app reaches) names `hosts` (exact
  hostnames, empty allowed), `purpose` and `data_sent_plain`; plus
  `credential.kind` and `children.may_be_used_by_minor`.
- `requires` (`home_assistant`, `network`, `ollama`) is evaluated, not decoration;
  an unmet requirement never blocks an install, it is shown.

`apps/_examples/greenhouse/app.json`, quoted here and validated as **that file**
by `tests/test_persona_path.py`, so this page cannot drift from what works:

```json
{
  "schema": "puffpai_app/v1", "id": "greenhouse", "name": "Greenhouse",
  "blurb": "Soil moisture and bench temperature, written down and read back.",
  "version": "1.0.0", "author": "You", "license": "Apache-2.0", "order": 60,
  "surfaces": ["house.sensors", "house.climate"], "credential": { "kind": "none" },
  "data": { "default": { "readings": [] } }, "children": { "may_be_used_by_minor": true },
  "ui": { "route": "/dashboard/apps/greenhouse", "icon": "mdiSprout" },
  "vocabulary": { "keywords": ["greenhouse", "seedlings", "soil moisture"],
    "examples": ["how is the greenhouse doing", "log a greenhouse reading"] },
  "actions": [{ "id": "greenhouse_list_readings", "says": "how is the greenhouse doing" },
    { "id": "greenhouse_add_reading", "says": "log a greenhouse reading" }],
  "doors": [{ "hosts": [], "purpose": "None. Readings are typed in or read from sensors this build already sees.", "data_sent_plain": "nothing" }],
  "requires": { "home_assistant": false, "network": false, "ollama": false }
}
```

```bash
python -c "import json; from puffpai import app_registry as a; \
print(a.validate_manifest(json.load(open('apps/greenhouse/app.json'))) or 'OK')"
```

A folder starting with `_` or `.` is **skipped by both loaders**. That is why
`_examples/` works, and where you park a half-finished app.

## 2. A pack

`packs/<id>/pack.json`, schema `puffpai_pack/v1`. A pack is a **list**: it names
apps that must already be installed and carries no data, vocabulary, ui or code;
one naming an app this build lacks is refused, missing ids named. Here is
`packs/_examples/growbot/pack.json` (`packs/homebot/` is the shipped one):

```json
{
  "schema": "puffpai_pack/v1", "id": "growbot", "version": "1.0.0",
  "display_name": "GrowBot app pack", "blurb": "What a growing build ships with.",
  "author": "You", "license": "Apache-2.0", "posture_min": "offline",
  "apps": [{ "id": "greenhouse", "version": "1.0.0" }]
}
```

## 3. Walk it end to end

No model needed, no code written; `tests/test_persona_path.py` is this as a test.

1. **Install it.** Copy both folders out of `_examples` to `apps/greenhouse` and
   `packs/growbot` (PowerShell: `Copy-Item -Recurse`), or scaffold your own (§0).
2. **Make the box see it.** `puffpai apps rescan`, or `POST /apps/rescan` with
   your API key; `GET /apps/installed` answers the same question without writing.
3. **Look at it.** It is in the sidebar under Apps (the nav is derived from
   installed manifests) and the link lands on the **generic app page**,
   `dashboard/app/dashboard/apps/[id]/page.tsx`: name, blurb, surfaces, then your
   store, each declared array a table whose columns are your rows' keys. **Add
   item** and the per-row delete pull the document, change it and push it back
   (`GET`/`PUT /apps/<id>/data`). A hand-written page always wins.
4. **Say something. With no model at all**, `puffpai.app_fastpath` answers four
   verbs, before any model call, from `vocabulary` + the lists in `data.default`:

   ```
   add tomatoes to the plantings list   |   put beans on the plantings list
   what is on the plantings list | show me the plantings | list the plantings
   remove tomatoes from the plantings list | delete beans from the plantings
   how many things are on the plantings list
   I planted tomatoes today          (only when the verb shares a stem with a
                                      list you declared)
   ```

   One verb, one list, one item. Name no list when you keep two and you get the
   question ("Which list - plantings or harvest log?"), never a guess. It **asks
   back** rather than falling silent on an incomplete sentence that is plainly yours:
   a named list with no verb, a bare `add basil` where you have one persona, or
   `delete the last one`, which names the most recent row and asks you to say the
   sentence that removes it. An ask-back needs evidence (a list name, an app name,
   one of your vocabulary words, or a lone persona plus an unambiguous add/remove),
   so ordinary chat is never taken over. Nothing is
   called saved until the document has been read back off disk with the item in
   it. Rows are `{"item": ..., "added": ...}`. The five shipped apps are excluded:
   they have their own parsers. **With a model**, everything else works too: your words
   route the message to apps, the model answers in the persona's voice with
   retrieval behind it, and those four verbs still run first, so writing a tomato
   down never depends on the brain being up. **The model is told what your build
   is**: `puffpai.build_context` puts your bot's name, your goal, your app and list
   names and the CURRENT contents of those lists in every system prompt, so "what
   have I planted so far?" is answerable. Your text arrives **quoted, as data**
   (goal ≤300 chars; 8 lists x 25 items x 80 chars) under a standing sentence saying
   a quotation is never an instruction, so a row reading "ignore previous
   instructions" is read out, not obeyed. The only instruction there is your own
   `system_prompt` from the Profile page (`/dashboard/profile`), ≤1000 chars and
   last, under the style paragraph its personality dropdown picks. What does *not* happen either way is
   `greenhouse_add_reading` running: `POST /apps/<id>/action` answers only a
   fixed list of `AppData` methods that exist in Python; a manifest cannot add
   to it.
5. **Take it back out:** delete both folders and rescan; `app_data/` keeps your
   data, because nothing deletes an owner's data quietly.

So a manifest covers understanding a subject, keeping its data, showing it, and
the four list verbs. A page that looks like *your* app needs `apps/<id>/page.tsx`;
anything else the bot *does* needs a Python method and its entry in that list;
reaching the internet needs a named outside service in
`src/puffpai/security/egress_doors.py` and a line in `SECURITY.md`.

## 4. Knowledge, the brain, and publishing

Knowledge packs are offline ZIM archives you drop in the knowledge folder
(`puffpai setup --zim-dir` picks it); see [KNOWLEDGE_PACKS.md](KNOWLEDGE_PACKS.md).
The model is the wizard's RAM-first fit against `config/model_catalog.json`;
`--model` overrides it. What the wizard decided lands in the build manifest. That manifest, with your `apps/`, `packs/<id>/pack.json` and the knowledge packs
you name, is the whole handoff to whoever rebuilds it.
