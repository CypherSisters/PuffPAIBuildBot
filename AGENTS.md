# AGENTS.md: how BuildBot works, for coding agents and for the bot's own brain

**Common tasks**

- Add another cloud model for Toolsmith: [the recipe](#add-another-cloud-model-for-toolsmith)
- Add your own app, skill, tool or data source: [How to extend](#7-how-to-extend) and [Add your own](#add-your-own)
- Add an outside service with its switch: [the Bridge](#2-the-three-zones) (`security/egress_doors.py`), step 3 of [the recipe](#add-another-cloud-model-for-toolsmith) as a worked example
- Change the wizard: [the build logic](#4-the-build-logic) (the wizard is that pipeline)
- Run the tests: [How to run and test](#8-how-to-run-and-test)
- The hard rules: [Hard rules](#9-hard-rules)

Read this first. Deeper detail (module map, data flow, schemas, contracts) is
in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), and its
[section 11](docs/ARCHITECTURE.md#11-diagrams) has the diagrams (three zones,
seven agents, the four questions, how a found app runs, the Yes and the
Bridge). Security promises are in [SECURITY.md](SECURITY.md). Owner-facing docs
are [README.md](README.md) and `docs/`; [docs/FAQ.md](docs/FAQ.md) answers what
owners and builders ask, and says plainly what is not built yet. Bugs and
requests go to the issue tracker,
[github.com/CypherSisters/PuffPAIBuildBot/issues](https://github.com/CypherSisters/PuffPAIBuildBot/issues);
[docs/ISSUES.md](docs/ISSUES.md) says what to attach and how to take private
things out of logs. Every path below is relative to the repo root; Python lives under
`src/puffpai/`.

## 1. What BuildBot is

BuildBot is an AI operating system for one person's own computer: PuffPAI's
three zones, seven agents, and almost everything customizable. Give it one goal
in plain words ("a bot that shows me pictures of Earth and tells me what is in
the sky tonight") and it goes out and finds the apps, tools and code it needs
to do each part of that goal, runs them as they were built, connects what needs
connecting, and turns the result into a personal AI bot. The setup wizard is
that whole process. The brain is a local model (Ollama). Toolsmith is the ONE
agent the owner may put on a cloud model (Anthropic), in Settings, with a
plain consent line (section 3b); every other agent always runs locally.

Think of it as a factory that builds a new kind of OS around each owner's goal.

- **Goal-agnostic.** No module knows any subject. The same pipeline builds a
  music bot, a garden bot or a wedding bot. Never add code that names a topic,
  a goal or a specific bot. If one bot needs something, make it a general rule
  every bot gets.
- **Never hand-build one bot.** Fix the factory (the rule, the classifier, the
  catalog, the view), not the output.
- **Find before writing.** BuildBot looks for things people already made (apps,
  tools, skills, data services, books) and uses them as built. It writes code
  only to connect data sources when nothing ready-made does the job.

## 2. The three zones

These are contracts enforced in code, not a diagram.

| Zone | What it is | Code |
|------|------------|------|
| **Zone 1, your private stuff** | The vault: an encrypted store (Fernet, PBKDF2-HMAC-SHA256) for keys, personal details and private files, opened with the owner's PIN, locks itself when idle. | `security/vault.py`, `zone_vault.py`, `security/keys.py`, `security/chat_privacy.py`, `security/vault_inbox.py` |
| **Zone 2, the brain** | The local model, the agents, what the bot knows (books, files, skills), apps running on this computer. | `brain_loop.py`, `brain_tools.py`, `agents/`, `knowledge/`, `skills.py`, `integrate/run_here.py` |
| **Zone 3, the Bridge** | The only way out for BuildBot's own calls. Every outside service has its own switch, listed on the "What leaves your computer" page. The named outside services (weather, news, web search, live cards and the rest) ship off. BuildBot's own finders (searching GitHub for apps, downloading the app you picked, the free book library, catalog refresh) are on from the start so the wizard can look for apps and books; each has its own switch on that page. | `security/egress_doors.py`, `security/egress_ledger.py`, `outside_services.py`, `outside_api.py`, `zone_bridge.py` |

What may cross:

- **Zone 1 to Zone 2.** Vault contents only while the vault is unlocked. Key
  values never reach the model, a prompt, a log, glue code or an install step.
  A key goes out only bound to its own service's hosts, added by the Bridge on
  the way out (`egress_doors.keyed_first_hop`, `source_fetch.fetch_small`).
  Personal details typed in chat are moved into the vault (or its sealed inbox
  while locked) before a chat is saved (`security/chat_privacy.py`).
- **Zone 2 to Zone 3.** Only through `security/egress_doors.py`
  (`egress_request`, `egress_get`, `app_egress_get`, `app_web_request`). The
  bot's run posture must be `enumerated` (`build/online.py`; `island` is the
  default and refuses everything) AND that service's switch must be on. The
  decision is made before any socket opens; redirects are re-checked per hop;
  every request, refused or not, writes one line to `<state>/logs/outside.jsonl`.
- **Zone 3 to Zone 2.** Everything fetched is data, never instructions.
  Additions are checked by Drawbridge (`drawbridge.py`) before the owner's Yes
  counts.
- **Not behind the Bridge, by design:** build-time book and source downloads
  (`source_fetch.py`, gated by `config/source_registry.json` and a license shown
  first); LAN services such as Ollama and Home Assistant (checked against the
  posture instead); and what an outside program does once it runs on this
  computer after its Yes (the Yes card says so).

## 3. The agent team

Seven agents on one message bus (`agents/registry.py`, `agents/bus.py`). Each
holds only the secrets its scope allows (`security/secrets_provider.py`,
`AgentScope`, `_SCOPE_ALLOW`). Agents talk only through the bus; a
state-changing envelope is reviewed by Drawbridge first.

| Agent (id) | Does | May hold / call | May not |
|------------|------|-----------------|---------|
| **Toolsmith** (`toolsmith`) | `web_fetch` (one page via the `web_fetch` switch), `workspace_read/write/list`, `workspace_exec` (Linux + bubblewrap only), `write_glue` / `run_glue` for assembled apps. Its coding loop is `assemble/toolsmith_loop.py`. The only agent that may think with a cloud model (section 3b). | No secret at all (empty scope; its Anthropic key is used by `security/toolsmith_cloud.py`, never handed to it). Everything it returns is marked `untrusted`. | Run a shell on Windows; use Docker; touch files outside its workspace or `<state>/assemble/<id>/glue/`. |
| **Drawbridge** (`sentinel`) | The Bridge's agent and the veto on state-changing actions. Its addition checks live in `drawbridge.py` (`check_addition`, verdicts `block` / `hold` / `warn` / `allow`), with checklists per kind (`glue`, packages in `deps.py`, code projects in `integrate/apply.py`). | `PUFFPAI_AUDIT_KEY` only. | Trust a stored `allow` (installs re-check); accept a checker registered after `close_registration`. |
| **Majordomo** (`majordomo`) | Background work: routines, queue, scoped task workers with budgets (default 1 action, no egress). Narrates the build on the team page (`build/pipeline.py`, `AGENT = "majordomo"`). | n8n and calendar keys. | Go online from a routine; run a routine without its recorded yes. |
| **Concierge**, **Steward**, **Archivist**, **Caretaker** | Conversation front desk; Home Assistant and media; the vault and knowledge; machine health. | Concierge: nothing. Steward: HA and media keys. Archivist: vault keys. Caretaker: service control. | Widen their own scope. |

**The chat brain** is the local model in the Hermes-style tool loop
(`brain_loop.py`): send the chat and this turn's tools, run each tool call,
feed results back, at most `MAX_STEPS = 8` tool rounds, then one last call with
no tools. Its tools come from one registry (`brain_tools.py`): built-ins
(`brain_tools_builtin.py`: `search_web`, `get_weather`, `undo_last_change`),
`run_command` (`brain_commands.py`, asks Yes/No for every command),
`schedule_routine` (`routines.py`, asks), `arrange_dashboard` (`home_things.py`:
her home page and menu), `find_things` / `install_thing`
(asks) (`finding/tools.py`), `skill_view` (`skills.py`), MCP server tools
(`mcp_client.py`), actions of added apps (`integrate/actions.py`,
`integrate/host_tools.py`: `start_local_app`, `use_local_app`) and actions of
assembled apps (`assemble/api.py`). A tool with `needs_yes` asks the owner
through `ctx["ask"]`; with no way to ask, it does not run.

Everything installed is one read-only list, `installed.listing()`
(`installed.py`, no storage of its own): Toolsmith apps (running or not),
run_here apps and command line tools, assembled apps, skills, tool servers and
box command line tools, rows `{id, kind, name, what, status, url, source_url,
brain}`. The system prompt gets one line per row every turn (all of them up to
60, then `list_installed`), so a thing installed on one message is used on the
next; the dashboard reads the same rows from `GET /installed`. `read_app` on a
stopped app starts it first (the same yes as `start_local_app`); a running
Toolsmith app's own OpenAPI/Swagger file becomes `use_local_app` calls; installed
command line tools are named in `run_command`'s description.

## 3b. Toolsmith's model

Toolsmith is the ONLY cloud-dependent agent;
everything else runs locally. One setting, `toolsmith.model`
(`<state>/toolsmith_model.json`, `security/toolsmith_cloud.py`):
`{"where": "local"}` (default, today's behavior exactly) or `{"where":
"cloud", "provider": "anthropic", "model": "claude-opus-5-5" |
"claude-sonnet-5-5"}`. Only Anthropic is offered today; to add another
provider, follow [Add another cloud model for Toolsmith](#add-another-cloud-model-for-toolsmith). Cloud without her recorded yes to the current consent
sentence reads as local. A yes to an EARLIER sentence (`consent_outdated`) is
never migrated: Settings says `RECONSENT_LINE` (in `status().line`,
`consent_again` opens the card on the new sentence; the key stays in the
vault, so agreeing again is one tap), and the build HOLDS like the wish below
(`hold_line()` = `RECONSENT_HOLD_LINE`) until she picks Anthropic again or
this computer. `CONSENT_TEXT` must stay true of what is sent ("What is sent"
below); changing it asks everyone on Anthropic to agree again. Settings card
`settings/ToolsmithModelCard.tsx`, one line on the setup list (`YesPanel.tsx`), routes `toolsmith_model_api.py`
(paired device; the key only in the POST body).

* **Scope guard.** `toolsmith_cloud.send` needs a `Scope` that only
  `toolsmith_scope()` mints, and that refuses every caller module but
  `puffpai.build.toolsmith_run` (`Run._cloud_scope`, only in `run_job` with no
  brain handed in), plus Toolsmith's own finding work:
  `build.apps.search_words` and `build.apps.rank` mint a "find" scope
  (`FIND_CALLERS`, checked by module AND function name). A find scope sends
  no tools, `max_tokens` 2048, effort low, through `toolsmith_cloud.ask_json`
  (JSON asked for in the text); its cost row says "Toolsmith search phrases"
  or "Toolsmith ranking for <job>". Sent: job names and what they do, the
  goal (secret and PII screens), public repo names, descriptions, stars,
  topics, language. The cloud answer splits them: apps that do the job
  (ranked), code about the job's subject (libraries, SDKs, CLIs: kept as
  `job["code"]` for MAKE, never tried as an app) and DROPPED (off-subject,
  demos and samples of something else, lists, templates, spam); any failure
  uses the local brain as before.
  `tests/test_toolsmith_cloud.py` fails if any other module or function
  names the scope, or any module the cloud loop or `keys.keyed_post`. Never
  add another caller.
* **Saving her key.** A locked vault answers 423 `vault_locked`; the card
  asks for her PIN inline, opens the vault through `/vault/unlock` and saves
  again. In the wizard, before the plan is approved, the key is saved too
  (row flag `store_before_setup`, only while `github_account.before_setup()`
  says the wizard run is under way): her switch is recorded with
  `record_yes`, the key goes into the vault UNCHECKED (nothing is sent to
  Anthropic before setup; a 401 on Toolsmith's first call says the key was
  not accepted), and the plan's run setting opens the door. No vault yet:
  "Set your vault PIN first". Outside a wizard run, not set up: 409
  `before_setup`. The wizard records "cloud" only once `status()` says cloud;
  otherwise her wish is kept (`<state>/toolsmith_wish.json`, 409
  `not_saved`, step not done) and the build HOLDS before Toolsmith's first
  run and on the Yes (`toolsmith_cloud.held`, `toolsmith_hold` on
  /build/status, YesPanel: Open Settings / Use this computer) until the key
  saves or she picks this computer.
* **Key.** Curated row `anthropic` in `config/catalog_seeds/keys.json`
  (vault only, card backed, door `toolsmith_cloud`, 3000 requests a month);
  sent only by `keys.keyed_post` as `x-api-key` to api.anthropic.com.
* **Session key lease**. The vault idle-locks after 5
  minutes; a building session runs longer. Toolsmith reads the Anthropic key
  ONCE from the open vault into memory (`_Lease`, a bytearray; never a file,
  log or answer) when a cloud run starts, when she unlocks the vault with
  cloud Toolsmith on (`on_vault_opened`), when she saves or replaces the key
  (`keys.add_key` lets go of the old lease BEFORE the new
  key is written, then `key_saved` takes a new one; `choose_cloud` takes it
  once the setting says cloud), when the wizard's plan is approved or its
  build starts with the vault open (`take_for_plan`), and when she gives a
  build Yes with the vault open (`yes.give` -> `take_for_yes`). It is passed only by `send`
  to `keys.keyed_post(lease=)`, checked by `lease_record` (key broker caller,
  Anthropic, caller `toolsmith`, the key's `stored_at` unchanged). The idle
  lock keeps it, and every other vault item keeps the 5-minute lock. It ends
  when Toolsmith has had no work (no run going, no busy queue, no wizard
  build running (`set_wizard_probe`), no wizard step, no call) for
  her choice on the Toolsmith card, "Keep Toolsmith's key ready while I'm
  building": 1 hour (default), 4 hours, or until I lock the vault
  (`<state>/toolsmith_key_ready.json`, `POST /toolsmith/model/keep`). A
  wizard step is any `/wizard/` POST answered OK, or a GET /wizard/build
  poll while its build runs (`api_server._wizard_step_keeps_toolsmith_key`
  -> `wizard.service.note_step` -> `toolsmith_cloud.wizard_touched`), counted
  at the time she took it; page reads and refused requests are not steps. A
  wizard that is only under way (goal saved, plan not approved) is NOT work
  by itself, so a wizard she leaves alone lets go of the key after her keep
  time from her last step, never up to 24 hours ("until I lock the vault"
  has no quiet end, as before), and a step after that does not bring the
  key back. Always
  after 24 hours; and at once when she locks the vault (`/vault/lock` or a
  lock listener), turns cloud off, or adds or removes the key. A timer lets
  it go on time. Start and end go on the Toolsmith security log and
  bot.log; Settings shows `lease_line` ("Toolsmith's key is ready until
  <time>. Lock the vault to stop it."). With no key (locked, no lease) a run PAUSES in
  place (`Run._wait_for_vault`: unlock event or a 15 s look, 2 hours at most),
  marks nothing tried and goes on with the same app; after the cap the job
  stays `waiting_yes` with "Unlock your vault to let Toolsmith go on." and is
  queued again when the vault opens. Tried entries with a vault-pause reason
  are tried again (`make.vault_pause`). While a run waits, or a job needs
  the key and it is not ready, `vault_wait` on GET /build/status and GET
  /wizard/build carries that line, and the wizard's Building screen, its last
  screen, the Build jobs tile, the bot page and the job page show a PIN box
  with Unlock (`VaultWaitUnlock.tsx`, the existing POST /vault/unlock).
* **What is sent.** The goal and the jobs (finding, above), the app's README
  and docs, file listings, the first 60 lines of its `package.json`,
  `pyproject.toml` and compose file, Toolsmith's commands and their output
  (server log and `read_page` text included), and the same for a folder of
  her own code; every user turn passes `secret_detector` and the
  PII screen first (`toolsmith_cloud.clean`, fails closed). Each call writes
  one outside-traffic line (`what: toolsmith_install`, bytes only) and a
  usage row (`<state>/toolsmith_cloud_usage.json`: provider, model, purpose
  "Toolsmith install of <app>", tokens, cost; never content).
* **The loop** (`build/toolsmith_cloud_loop.py`, called from `Run.go`):
  append-only per app try (assistant `content` blocks, thinking signatures
  included, go back exactly as returned; no `fit_turn`; a new app try is a
  new conversation; a too-large try ends instead of trimming); all
  `tool_result` blocks in one user message; `end_turn` = done talking,
  `max_tokens` = go on once, `refusal` = the try ends. Body: `max_tokens`
  16000, stable `system`, tools sorted by name, top-level `cache_control`,
  `output_config.effort` high, `fallbacks: "default"` (dropped per model on a
  400), never `thinking` or forced `tool_choice`. 429/5xx retry honoring
  `retry-after`; 401 says "Toolsmith's Anthropic key was not accepted. Check it
  in Settings."; no network runs that try on the local model.

## 3c. Toolsmith's zone

Toolsmith has no access to the internal parts: it is the agent that touches
the outside. Toolsmith downloads and runs
strangers' code and may talk to a cloud model, so whatever it reads may leave
the computer. It belongs to zone 3. One boundary, `security/toolsmith_zone.py`
(`zone_for`, `Run._zone`):

* **Allowed**: the try's folder (one folder directly under `host_apps`, or the
  folder of her code she chose), its logs and its scratch folder
  (`<folder>-tmp`, or `.buildbot/tmp` in her folder; TEMP, TMP, TMPDIR and Git
  Bash's `/tmp` point there). Read only: the folders of node, npm, pnpm,
  python, pip, go, ruby, docker, git and their caches (npm-cache, pip cache,
  `~/.npm`, the Go module cache ...). Any other folder is "outside" and waits
  for her yes, as before.
* **Never, even with her yes** (`REFUSED`, one plain line to the model:
  "That path is outside what Toolsmith may touch (<category>)."): BuildBot's
  state folder (vault, keys, config, chats, logs, `toolsmith_model.json`,
  `programs.json`, consent files) except the try's own folder, other
  `host_apps` folders, the vault folder and its inbox wherever `vault_place`
  points, the data folder, previous data folders and linked folders (a folder
  of her code inside one: only that folder), BuildBot's own code, and her
  `~/.ssh`, `.gnupg`, `.aws`, `.azure`, `.kube`, `.docker`, `.git-credentials`,
  `.netrc`, `.npmrc`, `.pypirc`, browser profiles, Windows Credentials and
  Protect folders, Start Menu startup items, Documents, Desktop, Pictures,
  Downloads, Videos and Music. A folder that HOLDS one of them is refused too
  (her home folder, a drive, AppData, `..` above the try's folder). So is a
  container that holds the Docker engine (`docker.sock`, the
  `docker_engine` pipe) or runs `--privileged` / `pid: host`: it could mount
  any folder (`ENGINE`; a Portainer-style app needs her change here).
* **Where it is checked**: every terminal command (`command_check`: `~`,
  `$HOME`, `%APPDATA%`, `$env:X`, `${X:-d}`, `..`, quotes, `/c/...`, `cd`,
  redirections, copy targets, `-o`, `-C`, every path-like word, `-v` and
  `--mount` sources), before her list of approved steps is looked at;
  `start_server` (its command and its folder); every compose command (all
  compose files of the app, with their `.env`: volumes, bind sources,
  `env_file`, build context, secrets; `compose_refusal`); `write_file`;
  `server_log`; `install_program`'s arguments; her chosen folder at download
  (`folder_problem`); a restart's start commands. `python -m puffpai` and the
  `puffpai` command line are refused.
* **Its shell** (`toolsmith_env`): the Developer shell's environment without
  `PUFFPAI_*` names, without BuildBot's shim folder (its `puffpai` command
  line) or code on any path list; `python`/`python3` come from its own shim
  folder (`host_apps/.toolsmith-shims`).
* **Nothing internal in its conversation**: its task and tool results carry
  the job, the candidates, the app's own files and its commands' output only.
  Her account name is "you" in everything it is sent (`scrub`), and a path it
  copies back gets her name again (`unscrub`). `tests/test_toolsmith_zone.py`
  builds a Run for a fake job and fails if a state-folder file name, the vault
  path or the data folder path is in its messages.
* **Honest limit.** This bounds what Toolsmith's model can name. Native
  install scripts and servers that Toolsmith runs (npm scripts, pip builds,
  `./install.sh`) still run as the owner's Windows user, so a hostile script
  could read her files; a word the check cannot resolve (a variable set by a
  script, a path built at run time) is not seen. Docker apps are contained by
  Docker (and their mounts are checked). Next step, an owner choice not yet
  built: a separate limited Windows account for Toolsmith's native runs.

## 4. The build logic

One pipeline for every goal: `build/pipeline.py`. Shared words (enums, the Job
record, build.json) are in `build/schema.py`; never invent values outside them.

The app path is one loop, nothing else on it: `build/interpret.py` (jobs) ->
`build/apps.py` (GitHub search by stars plus the app list, one brain rank) ->
`build/setup.py` + `build/yes.py` (one setup list, one Yes) -> `build/make.py`
`_make_app` -> `_make_with_toolsmith` -> `build/toolsmith_run.py` (runs the app
as its README says, installs the programs her Yes covers, tries the next app on
failure; an app already running for another job, same ref or same GitHub
owner/repo, is reused when it is the job's pick or her project; one that is
only an alternate waits its turn in the order and is used only when its page
passes the main-page check) -> `integrate/host_tools.py` `read_app` for the brain.
When no app runs (none found, or every one gave up), the same loop MAKEs one:
`apps.py` keeps the code it found (libraries, CLIs, her own repos) as
`job["code"]`, and Toolsmith writes a small server and page around the first
(then the second), kept with `made_from` and used like any app. A GitHub
address pasted in Find more (`apps.from_url`) is an app candidate when it
says how to run, else it goes first in `job["code"]`, and wins over a
ready-made tool or data for that job (`decide._pasted`: route A, its MAKE row).
A GitHub code project on the Add page (app, program, library or data, with or
without a licence; no licence is one plain line and "Add anyway", never a
refusal) goes the same way: `POST /build/project` (`pipeline.plan_project`)
puts it on the job it clearly fits (`fits_project`) or ONE new job named
"<name>: <its line>", her project first, waiting for her one Yes.
Jobs with no app and no code use routes B (tools, routines, books) and C
(assembled from data), below.
The whole logic is four questions, asked per job, by the agent team
(Majordomo runs the plan per job and narrates it on the team page, Toolsmith
does the finding and running, Drawbridge checks code before it runs):

1. **What tools do I need to do these tasks?** Concrete jobs from the goal,
   1 to 3 for a short goal (one per thing when she lists more).
2. **Are the tools already available?** On this computer (built into
   BuildBot: routines and reminders, knowledge and books, chat; or already
   installed or part of the bot: reused, never reinstalled) OR ready-made
   online: an app on GitHub (searched by the job's words, most stars first,
   whose README says how to run it), awesome-selfhosted, the skill shelves,
   the MCP lists. A ready-made app or tool that exists online counts as
   available.
3. **If so, what do I need to do to use them?** Install it the way it is
   built (its README's own commands: npm natively, compose in Docker, Python
   in its own venv) plus its dependencies, keys, switches and settings: the
   one setup list and ONE Yes. It runs on 127.0.0.1; a web app opens in her
   own browser, and the brain sees it with `read_app`.
4. **If not, what code exists that I can use to make the tool?** Only when
   no ready-made tool exists anywhere: Toolsmith assembles one from parts
   (data services, libraries, glue code) as an app of its own (route C).

### 1. The jobs (`build/interpret.py`)

The brain answers under a JSON schema whose fields are enums: `action` in
`show, watch, listen, ask, make, do, remind, learn`; `output` in `pictures,
video, sound, numbers_over_time, places, text, action`; `needs` from `place,
time, account_key, none`; plus `name`, `does`, `subject` (1 to 3 lowercase
words) and a sample `ask`. Each job is a concrete feature she would recognize,
named with her goal's words ("World news dashboard"), never an invented one.
Answers are validated (`schema.job_problems`), retried twice, then filled from
a deterministic word table (`interpret.from_words`). Jobs the wizard already
kept (`finding/capabilities.json`, from `wizard/needs_llm.py`) are planned as
given, cut to the ones tied to her words (`interpret.focus`). A goal typed as
one word ("WorldNews") reads as words.

### 2a. What is already on this computer (`build/find.py`)

`find.have_for`: installed skills, tool servers and apps
(`finding/installed.json`), used as they are; the bot's own reminders
(`find.ROUTINE`); books and offline libraries join any job about learning; an
app another job of this build already added is reused (`make._added_from`).
Her own GitHub projects come next when she connected GitHub
(`github_account.candidates_for`).

### 2b. What is ready-made online (`build/apps.py`, then the rest of `find.py`)

* **Ready-made apps first** (`build/apps.py`). The brain is asked once for
  the GitHub search phrases a person would type (1 to 2 per job, one for the
  goal); without a brain the job's name and the goal give the words. At most
  6 searches per build, most stars first, in name, description and topics
  (at least 20 stars; cached a day; her account when connected; GitHub's
  rate limit respected); a result that names none of the searched words, or
  whose description is stuffed with words, is left out. Per job the pool is
  its results, the goal's results and the awesome-selfhosted rows that mean
  what the job means (`finding/app_catalog.py`); the brain puts the pool in
  order ONCE (never a veto: what it leaves out follows by stars). READMEs
  are read in that order (`integrate/readme_steps.py`: start/run/serve/dev/
  compose-up commands, never test/lint/typecheck lines; a compose file or a
  Dockerfile also counts) until 4 apps have run steps. No fit score. A job
  with ready-made apps gets nothing else searched.
* **Then the rest**: the shelves and code sites (`finding/find.py`, registry
  `config/where_to_look.json`), the live data lists (`catalogs/live.py`, our
  checked list `config/catalog_seeds/apis.json` first).
* **Classify** (`build/classify.py`) by how a thing runs, never by its name:

  | Class | When |
  |-------|------|
  | APP | a repo with a runnable start (package.json dev/start, README run commands, a compose file) and a screen, even if it also offers a tool server |
  | TOOL | an MCP server; a skill with scripts AND declared programs; a skill naming an MCP or OpenAPI address; a CLI repo; app API calls without a screen; a built-in routine |
  | DATA | a feed, website row, API, stream, library, data template, picture set; a skill whose only action is a request to its own remote service |
  | TEXT | a book, an offline library (ZIM) |
  | GUIDE | a text-only skill |

* **Fits and route** (`build/decide.py`). An app found for a job by stars fits
  it as it is. Other finds fit by meaning (all-MiniLM, `T[class]`), a shared
  subject word, `produces(class, output, action)` and the brain's yes/no.
  One route per job:

  | Route | When | What gets made | Main screen |
  |-------|------|----------------|-------------|
  | **A** | an APP fits | the app, run as built (the next one when it cannot start) | the app's own screen |
  | **B** | a TOOL fits (or remind: a routine; learn/ask: a book or guide) | the tool connected | shared view of tool outputs |
  | **C** | only DATA fits | an app Toolsmith puts together from the data sources | `VIEW[output]`: gallery, chart, map, player, list, form |
  | **D** | nothing fits | nothing | "Nothing found yet" + Find more (words or a pasted GitHub address) |

* **Make** (`build/make.py`), in the background, one job at a time, only for
  jobs the Yes covers. A: **Toolsmith works like a person (or Hermes)**
  (`build/toolsmith_run.py`) in the chat's tool loop with a real command line
  in the app's own folder: `download` (git clone, then Drawbridge's code
  check before any command), `terminal` (one persistent bash, Git Bash on
  Windows, PowerShell only without Git; the prompt names the real one: installs,
  `.env` copies, `docker compose up -d`; a command waits 120 s, up to 10 min
  while it keeps printing; one that never ends is stopped), `start_server` (a dev
  server in the background, its log kept), `read_page`, `server_log`,
  `done(url)` (only when the page shows real content), `give_up(reason)` (the
  next candidate follows). Every app whose code is a public project address
  (found by stars or from the app list) goes this way, the pick then up to two
  backups in order; one try gets 20 steps and 12 minutes (a command still
  running at the end is stopped), and the repeat guard holds back only the same
  command that just failed with nothing changed since; the third hold ends the
  try with that command's own error (what it started is taken away first: on
  Windows each server and shell runs in its own job, so what they started goes
  too). A project she pasted runs before any app still on the job (pasting it again takes it
  off the job's tried list); when it does not start, an app the job had that still answers
  is kept ("Kept <app>: <pasted> did not start here."). An app already kept here with its
  folder is started again, never downloaded again, and a try that gives up leaves no folder. Guardrails only: the plan card lists the apps;
  commands stay inside the app's folder (no sudo or admin, no system
  settings, no global installs, no other folders, no other Docker projects;
  a `docker run` container carries the app's own name; compose projects are
  named for the app; its ports publish on 127.0.0.1 only: compose runs a copy
  `.bb-local.<file>` beside each compose file, `docker run -p` and `--host
  0.0.0.0` style flags are rewritten, and a page still listening on every
  network gets `reachable_from_network` and one plain line on its job); every command and its last lines go to bot.log and the
  team page; no window opens; what it started is stopped by its exact process
  id, container name or compose project. A working app is kept in
  `<state>/toolsmith_apps.json` (one entry per repo, older copies dropped at startup and on listing; its port
  answers on 127.0.0.1 or ::1), opened once in her own browser, and the
  brain sees it with `read_app`. When the backend starts, these apps are
  started again in the background, one at a time (`toolsmith_run.restart_all`;
  config `apps.restart_on_start: false` turns it off). An optional local Toolsmith
  model (`PUFFPAI_TOOLSMITH_MODEL`, used only when it is on this computer) can
  drive this instead of the bot's own model; or Anthropic, when she chose it
  (section 3b). Each try and app used keeps the model (`"model": "local"` or
  the cloud id, `make.model_now`; the job's `built_with`); after she switches
  to Anthropic the bot page and Build jobs offer "Check all again" for jobs
  built locally. B: `mcp_client.add_server`,
  `deps.run_plan`, a routine, or a book. C: `assemble.api.make_job_app`.
* **Prove** (`build/prove.py`). "Working" means its page shows real content
  (A) or it answered a real ask (B, C), never "something was installed".
  Else `not_yet` with a plain reason naming the app.
* **One page per job**: `page = "job-<id>"`, shown at
  `dashboard/app/dashboard/jobs/[id]/page.tsx` (main screen, Ingredients, and
  "Use a project you know" for a pasted GitHub address, `POST /build/more/<id>`).

### 3. Using them: one setup list, ONE Yes, the brain's view

* **Check** (`build/check.py`). Drawbridge on every part; a code project is
  downloaded at its pinned commit and checked (`integrate/apply.py`) before
  anything is added, or after the Yes when downloads are not on yet.
  Drawbridge stops real code-safety problems only: big apps are normal (only
  a disk-space bound, 4 GB, with a plain note above 1 GB); a hidden connection
  means a real tunnel tool in code (ngrok, trycloudflare, cloudflared tunnel).
  A README's install lines (git clone, make, sudo make install, curl | sh)
  never count; lines aimed at an AI are hidden from Toolsmith's model
  (`drawbridge.hide_ai_lines`, on every tool answer) with a note on the job;
  only clearly hostile text stops it (`drawbridge.readme_block`: "ignore
  previous instructions", a new role, keys sent to an address, words hidden in
  invisible characters or an HTML comment).
* **One setup list** (`build/setup.py`). Every row any picked part needs, for
  all jobs, merged: kinds `program, package, key, setting, address, switch`.
  An app's row shows the exact commands its README says to run. Keys and
  settings (the home town) need the owner's input; the rest is done by the Yes.
* **ONE Yes** (`build/yes.py`). Her tap covers exactly the list shown: a
  consent ticket minted and spent at once (`add_on`, `build:<sha16>`), an
  audit line, then `yes: {given, at, sha, by}` in build.json. If the list
  changes (Find more, a pasted project address, a new job), `yes.covers` is
  false and only the changed jobs wait for a new single Yes.
* **Knowledge** (`build/knowledge.py`), once per build and on Find more: the
  knowledge sites (`find_more.search`) with apps.py's search phrases, best 5 in
  `build["knowledge"]`, one tickable line each on the same Yes (over 2 GB starts
  unticked; ticks are in `yes.sha`); after the Yes `find_more.add(via="build")`
  one at a time in the background, status in build.json, shown on the bot page
  and in `/installed` (kind `knowledge`); chat searches them every turn.
* **The brain's view of a running app** (`integrate/host_tools.py`).
  `read_app(app, path)` opens the app's 127.0.0.1 page in a headless browser
  (her default browser when it is Chromium-family, else any Chromium-family
  one; no window, its own empty profile, short timeout, stopped by that
  profile after each read; a plain HTML read when none can be driven) and gives the brain its
  visible text (title, headings, lists, tables, numbers) and links: any web
  app, no app-specific code. An app's own API or MCP server is offered too
  (`use_local_app`, `mcp_client`). The job page opens the app in her own
  browser (a big "Open <app>" button), never inside our frame.

### 4. Build from parts (only when nothing ready-made exists)

Route C: Toolsmith assembles an app from data services (`assemble/`: spec,
glue code, its own small server and a scaffold view). This is the only place
BuildBot makes a screen of its own.

Job statuses: `planned, needs_setup, waiting_yes, queued, building, working,
not_yet, nothing_found` (`schema.STATUSES`, owner words in `schema.WORDS`).

## 5. Consent model

- **One Yes per build** (`build/yes.py`); later additions get their own single
  Yes. An assembled app's Yes covers Toolsmith's rewrites within its budget
  (`assemble/consent.py`).
- **Chat `run_command` keeps Yes/No for every command** (`brain_commands.py`).
  No "yes to all".
- **The Developer page shell is full access by design**
  (`developer_terminal.py`): a real shell as the owner, paired device plus API
  key, one audit line per command, no per-command question.
- **Keys only in the vault** (`security/keys.py`). A key pasted into chat is
  refused (`security/pasted_key_guard.py`). Setup rows of kind `key` write
  straight to the vault (`POST /build/setup/<row>`).
- **Outside code runs on this computer after one Yes** (`integrate/run_here.py`):
  bound to 127.0.0.1, in its own Job Object on Windows or its own process group
  elsewhere, stopped at once if anything listens beyond loopback.
- **Outbound for BuildBot's own calls only through the Bridge.**
- Consent tickets (`security/consent_tickets.py`): single use, 10 minutes,
  bound to `(action, target, device_id)`, sentence built by the server.

## 5b. Data folder, linked folders, vault capture

- **One data folder** (`knowledge/my_data.py`): `knowledge.data_dir` in the
  main config is THE setting (default: the old pack folder, so existing
  installs keep working; `zim_dir` is written with the same value for older
  readers). Downloads land there (`find_more.knowledge_folder`); her own files
  land in `<data_dir>/My files/` (`POST /ingest/upload`, chat attachments,
  `POST /knowledge/my-files/upload` for many files or a whole folder).
  Changing it (`POST /knowledge/data-folder`, the setup plan's question, the
  Knowledge page) never moves or deletes: the old folder goes into
  `knowledge.previous_dirs` and is still read.
- **Linked folders** (`knowledge.linked_folders`, `POST /knowledge/folders`,
  `DELETE /knowledge/folders/<id>`): read in place, never copied, moved or
  changed; unlinking takes their files out of the index. Refused only: the
  vault's folder, the state folder, `.ssh`/`.gnupg`, system folders, a whole
  drive, or a folder holding one of them (`my_data.refusal`). Changing either
  needs a paired device. Routes: `knowledge_folders_api.py`.
- **The watcher**: a polling thread (`my_data.start_watcher`, every 45 s, once
  at start-up, woken after an upload, a link or a vault unlock) compares
  mtime and size with `<state>/knowledge/my_data.json`; new or changed files
  go through `ingestion.ingest_file` (the upload path), deleted ones leave the
  index. Skips hidden/system files, links, pictures, `.zim`, book folders and
  files over `knowledge.max_file_mb` (25). `auto_index=false` stops it. ZIM
  packs stay with `auto_index.py`. Status: `GET /knowledge/my-data`; rows in
  `/installed` (kind `knowledge`).
- **Vault capture, every path**: `ingestion.screen_text_for_pii` runs
  `security/secret_detector.py` (key shapes, PEM blocks, JWTs, `.env` lines,
  password/secret/token assignments) before the PII classifier. Keys and high
  PII: values plus a pointer (file, path, line) go into the vault
  (`held_<id>.json`), the index gets `[held in vault]`. An owner's file
  (`keep_original`) is never touched; an upload's plain copy is removed once
  vaulted, as before. **Vault locked**: with `hold_when_locked` the values are
  sealed into the vault inbox (`vault_inbox.deposit_status`), the file waits
  unindexed (`VaultWaiting`), and the first scan after unlock finishes it.
  An UPLOAD (`/ingest/upload`, `/knowledge/upload`, My files uploads) that
  must wait keeps no plain copy: the whole file is sealed into the inbox
  (`vault_inbox.seal_file`, `<inbox>/files/`), and after unlock it is put
  back into My files and added as usual (`my_data.hold_upload`,
  `_restore_sealed`); if the inbox cannot take it, it is not kept and she is
  told. An upload with no key or high PII is added at once, locked or not.
  Deleting an upload from the Documents page deletes its file in My files
  (`my_data.remove_upload_file`, never a linked folder). The scan state is
  written in batches (200 files or 5 s, and at the end).
  Downloaded book pages lose key shapes (`text_books.set_aside_orders`), with
  nothing kept. Logs name files and counts only.

## 5c. Your own programs

She can add programs she already has (`programs.py`, routes `programs_api.py`,
paired device only; one list `<state>/programs.json`). Row: `{id, name, kind
("cli" | "desktop"), path, command, added_at, help, commands, always_allow}`.

- **Command line** (`POST /programs` `{"kind": "cli", "path" | "command"}`):
  a name is found on `PATH` once (full-path entries only) and kept by its full
  path; refused only when it is not there. Its `--help` (then `-h`, `/?`) runs
  once, 10 s, no shell, hidden; 200 lines kept, read by `cli_tools.parse_help`
  / `derive_specs(host=True)`. A program with subcommands (git, docker, npm:
  `cli_tools.list_subcommands` reads "Commands:" sections, npm's "All
  commands:" list and git's "These are common Git commands" groups) gets one
  `commands` entry per subcommand (`name`, `what`, `argv`; 60 max); the first
  25 get `<program> <sub> --help` read the same way (git: `-h`, its `--help`
  opens a web page; 60 s for all), never for `programs.NO_SUB_HELP` ones
  (rm, push, run, publish ...). A shell or interpreter (cmd, powershell,
  pwsh, bash, sh, python, node ...: `programs.is_shell`) never runs for its
  help; its row says `programs.SHELL_NOTE`. `host_programs.registered(id or full path)` is
  the separate list (`NAMES` stays closed).
- **The brain**: rows of kind `program` / `desktop` in `installed.listing()`;
  `installed.cli_commands()` names them in `run_command`. Every command still
  asks, unless she set **Always allow** (`PATCH /programs/<id>`): then ONE plain
  call of that program (no ; | & $ < > ( ) { }, backtick or newline) runs as argv by its
  full path, never a shell, one audit line per run (`own_program_run`). A call
  with shell features asks as before. A command naming `programs.json` is
  refused. Always allow on a shell or interpreter returns a plain `warning`.
- **Desktop** (`.exe` / `.lnk`, `.app`, `.desktop`): `POST /programs/<id>/launch`
  shows its window (`programs._start_window`, the one procflags exception);
  `GET /programs` also lists `installed_desktop` (Start Menu shortcuts, App
  Paths, `/Applications`, `.desktop` files; 300 max). Brain tool
  `open_in_program(program, file)` asks yes the first time per program
  (`open_yes` in programs.json).
- **Opening files** (`POST /files/open`, `open_in_program`): only files inside
  the data folder (My files included) or a linked folder, never a vault or
  state path; a program file (`.exe`, `.js`, `.bat` ...) never opens in its
  default app.
- **Toolsmith**: `done({"program": <full path>})` for a desktop app with no web
  page; the file must be in the app's folder or an install folder; it becomes
  a `desktop` row.

## 6. Data files

All under the state folder `<state>` (`config.get_state_dir()`:
`$PUFFPAI_STATE_DIR`, else `$PUFFPAI_DATA_DIR/state`, else the per-user OS
state folder). Never inside the checkout.

| File | Holds |
|------|-------|
| `build/build.json` | schema `build/v1`: goal, jobs, setup, yes, dropped, parts |
| `build/made/<job>.json` | what make made for a job |
| `build/outside_asks.json` | "Allow <app> to reach <host>" rows found at run time |
| `build/app_sites.json` | each browser app's site set |
| `assemble/<app>/spec.json`, `glue/`, `checks.json` | an assembled app |
| `assemble/report.json`, `status.json`, `yes.json` | assembled app results and Yeses |
| `host_apps/<id>/` | an outside project run by `run_here` |
| `skills/<name>/SKILL.md` | installed skills |
| `mcp_servers.json` | tool servers (never a header value) |
| `finding/installed.json`, `finding/capabilities.json` | installs; the jobs as capabilities |
| `dashboard_layout.json` | how the owner arranged her home page and sidebar (section 7) |
| `apps_on.json` | `{"on": [ids]}`: the first apps this bot turned on (section 7) |
| `addition_checks.jsonl` | every Drawbridge verdict |
| `logs/outside.jsonl` | every outside request (no query strings) |
| `knowledge/my_data.json` | what the data-folder watcher has seen: per file mtime, size, doc id, status |
| `security_logs/audit.jsonl` | security audit trail |

A bot made before build.json gets one from its old files once
(`build/migrate.py`, never deletes).

## 7. How to extend

Always add a test with the change. Tests never go online.

- **A catalog source.** Add a row to `config/where_to_look.json` and a searcher
  in `catalogs/searchers/<module>.py`; add the module to `SEARCHER_MODULES` in
  `catalogs/where_to_look.py`. Live searchers send only
  `outbound_query.Sendable` phrases. Tests: `tests/test_catalogs_adapters.py`,
  `tests/test_catalogs_live.py`.
- **A classifier rule.** Edit `build/classify.py` (by how it runs, never by
  name). Tests: truth tables in `tests/test_build_core.py`, then
  `tests/test_build_matrix.py` (8 goals, scripted brain, decoys) and
  `tests/test_build_matrix_real.py` (recorded real finds,
  `tests/fixtures/build/real/`).
- **A shared view.** Add it to `assemble/spec.py` (`VIEWS`, `expect_for`),
  `assemble/scaffold/views.js` (`VIEWS`, `TAB_WORDS`), and if a route may pick
  it, `build/schema.py` (`VIEW`, `MADE_VIEWS`, `WORDS["view"]`). Tests:
  `tests/test_assemble_scaffold.py`, `tests/test_assemble_scaffold.js`,
  `tests/test_assemble_core.py`.
- **A skill or MCP server.** Skills are folders in the agentskills.io format
  (`skills.py`); tool servers go through `mcp_client.add_server`. To make them
  findable, use the shelves in `finding/` or `catalogs/searchers/mcp.py`. Tests:
  `tests/test_mcp_client.py`, `tests/test_skill_missing_deps.py` for declared needs.
- **The home page and sidebar layout.** One plain JSON file,
  `<state>/dashboard_layout.json` (`dashboard_widgets.py`, `get_layout`,
  `save_layout`, `arrange`; `GET/PUT /dashboard/layout`). Fields:
  `tiles` {`order` [tile id], `sizes` {id: {w, h}} on a 12-column grid,
  `hidden`, `pinned` (lead the board), `names` {id: her name}}; `sidebar`
  {`order`, `hidden`, `pinned` (moved to the top group), `names`}, keyed by
  href; `seen` {tiles, sidebar}: id to name of what the page last showed,
  written by the page so the brain can find things by name. A missing file
  means `default_layout()`: Chat, Build jobs and Find more, with the household
  tiles in `STARTS_HIDDEN`. PUT replaces only the sections sent. The page
  (`dashboard/app/dashboard/page.tsx`, `_lib/hooks/useDashLayout.ts`) and the
  rail (`_lib/useBuildMenu.ts` `withOwnerLayout`, `AsideMenu/CustomizeRail.tsx`)
  read and write it; the browser keeps a copy only as an offline cache. The
  brain changes it with `arrange_dashboard` (`home_things.py`: pin, unpin,
  hide, show, move, rename). **A tile for a new kind of installed thing**:
  tiles come from `GET /installed` (`layoutService.getInstalled`, falls back
  to `GET /build/apps`), one per item, id `<kind>:<id>`, drawn by
  `InstalledTile` in `dashboard/app/_components/Home/HomeTiles.tsx`: add the
  kind to `KIND_WORDS` and `KIND_ICON` there (and to `KINDS` in
  `layoutService.ts`). It appears by itself when installed; a running app
  opens in a new tab, never a frame. Tests: `tests/test_dashboard_layout.py`.
- **Your first apps.** The five shipped apps (`apps/alarmflow`, `calendar`,
  `fitfam`, `freshtable`, `trivia`) say `"first_app": true` in their
  manifests: off on a new bot, turned on per bot (`first_apps.py`,
  `<state>/apps_on.json`). An app that is off is left out of
  `app_registry.load_manifests()` (sidebar, home tiles, brain tools, chat fast
  paths, router words); `load_manifests(include_off=True)` sees it (data
  layer, wizard matching, id checks), and its `/apps/<id>/data|action|updated`
  routes answer 409 "turned off". A bot with no `apps_on.json` yet keeps on
  each first app it already uses (saved data, its surface on the build's
  trimmed page list, or its tile on her home page); reading never writes, and
  `first_apps.settle` keeps the set once, when the backend starts and before a
  build record is written (`build_manifest.write_manifest`). On and off: the
  Apps page section "Your first apps" (`GET /apps/first`, `POST
  /apps/first/turn {id, on}`, paired device, local only). The build plan lists
  the ones that fit the goal (`build_interview` catalog tags) on its one Yes
  as "Turn on: FitFam (one of your first apps)" (`build["first_apps"]`, part
  of `yes.sha`), and her Yes turns them on. Home tiles that show an app's data
  (`surfaceMap.ts` `APPS_BY_WIDGET`) and the Calendar and Timers widgets
  (`dashboard_widgets.WIDGET_APP`) show only while that app is on. Owner apps
  in `<state>/apps` are never off. Tests: `tests/test_first_apps.py`.
- The five first apps are planned as independent marketplace apps.
- **A data seed.** Add a row to `config/catalog_seeds/apis.json` with a
  keyless https sample on the row's own host (`catalogs/api_seeds.py` drops bad
  rows). Tests: `tests/test_catalogs_seeds_goals.py`.

### Add your own

The owner's own things come in from the top of the Developer page
(`dashboard/app/dashboard/developer/_components/AddYourOwn.tsx`), four ways,
each card with a one-line "What happens":

- **A folder of my code.** `POST /build/project {"path", "order"?, "job"?}`
  (`build/api.py`). `build/apps.py` `from_local_folder` reads its README,
  package.json scripts, compose file or Dockerfile the same way as a
  download (`readme_steps`, `runbook.detect`) and gives an app candidate
  with `local_path` (ref `local:<hash>`, `direct`), or code to make an app
  from (ref `code:local:<hash>`). It joins a job like a GitHub project (Run
  it first / Add to the list, her one Yes). Toolsmith runs it IN PLACE: no
  download, no copy; its own files (logs, admin output) go in `.buildbot/`
  inside her folder (`toolsmith_run.own_files`); `remove_try_folder` never
  deletes it. A full path only (`X:\...` or `/...`; `C:`, `.`, `src` and
  other relative paths get `apps.NOT_FULL_PATH`, never read against
  BuildBot's working folder). Refused for the vault, BuildBot's settings
  folder, system folders, sign-in keys, a whole drive (the data folder's
  rules in `knowledge/my_data.refusal`) and BuildBot's own code: its install
  folder (`apps.install_root()`, the repo root holding `src/puffpai`),
  anything in it or a folder holding it (`apps.local_folder_problem`), and
  the data folder (now or before), the data dir and what BuildBot made in
  them (knowledge packs, the My files folder itself) or a folder holding
  them (`apps._data_folder_refusal`); a folder she made inside My files is
  fine. Tests:
  `tests/test_build_local_folder.py`.
- **A program on this computer** (command line or with a window) and **a
  desktop app** (picked from the apps installed here): `/programs` and
  `/programs/<id>/launch` (registering, the help it reads, "Always allow
  this program", Remove, Open). The cards say "Coming soon" while those
  routes answer 404 (`addYourOwnService.ts`).
- **A tool server**: the existing "Add my own tool" on the Brain page
  (`AddOwnTool`, `mcp_client.add_server`).

**Office files.** The brain tools `make_document(kind, title, content)` and
`edit_document(path, changes)` (`office_docs.py`, registered in
`brain_tools_builtin.py`, owner only, local only) make Word, Excel and
PowerPoint files in `<data folder>/My files/Made by <bot name>/` with
python-docx, openpyxl and python-pptx, and change files only inside her
data folder or a folder she linked (add paragraphs, rows or slides; replace
text). Its own files change at once; any other file in her data folder only
after her yes (`needs_yes`, one file per question); a file in a linked folder
(indexed in place, never changed) only after her yes to "This file is in a
folder you linked. BuildBot normally never changes those. Change it
anyway?", with the original kept beside it as "<name> (before
BuildBot).<ext>". The answer carries "Open it in Office from your files" and the
one-click `POST /files/open`; the Knowledge page lists the newest files in
My files with an Open button (`my_data.recent_my_files`). The data folder's
watcher indexes new files by itself. Tests: `tests/test_office_docs.py`.

### Add another cloud model for Toolsmith

Only Anthropic is offered today (section 3b). To add another provider ("add
OpenAI", "add Google Gemini"), touch these in order. `<p>` is the new
provider id (say `openai`); `tc` is `security/toolsmith_cloud.py`. Every name
below is NEW and per provider; the Anthropic ones stay as they are. Today
much of `tc` says `"anthropic"` or `PROVIDERS["anthropic"]` outright: each of
those reads the CHOSEN provider (`read_setting()["provider"]`) instead.

1. **Provider and prices** (`tc`). A `PROVIDERS["<p>"]` row: `label`, `host`,
   `url` (its chat endpoint), `key_id` `"<p>"`, `models`, `default`. Its
   `models` dict has the shape of `MODELS` (`{"name", "in", "out",
   "cache_read"}`), prices in US dollars per million tokens taken from the
   provider's current price page, never invented; model ids unique across
   providers. Its cache-write factor too (`CACHE_WRITE_FACTOR` is
   Anthropic's 1.25; 1.0 where writes bill as input). `MODELS`,
   `DEFAULT_MODEL` and `CACHE_WRITE_FACTOR` are read directly today by
   `read_setting` (`last_model`), `status` (`models`, `line`),
   `choose_cloud` and `set_model` ("Pick Opus 5.5 or Sonnet 5.5."),
   `cost_usd` and `cli._setup_toolsmith_model_step`: make each read the
   chosen provider's row. The other hard-coded spots: `_key_state`, `_take`,
   `lease_record` (its `service_id` check), `choose_cloud` (`check_shape`,
   `add_key`, the written setting, the audit line), `record_usage` (`"provider"`
   in `last`), and `keys._lease_ends` / `keys._lease_starts`.
2. **Key row** in `config/catalog_seeds/keys.json`, a copy of the `anthropic`
   row with id `<p>`: `name`, `for`, `signup_url`, `door` and `own_page` (both
   the new switch id, step 3), `vault_only` true, `store_before_setup` true,
   `card_required` true, `personal_use_only`, `how_long`, `cost_note`,
   `placement` (header only; `value_format` `"Bearer {key}"` where the
   provider wants it), `hosts` (1 or 2, all in the switch's hosts), `check`
   (`url`, `ok`, `rejected` [401]) and `check_headers`, `shape` (the key's
   regex, used by `keys.check_shape`), `monthly_cap`, `per_minute_cap`,
   `signup_steps`. `keys.row_problem` refuses a row whose `signup_url` is not
   on the same registered site as its `hosts` (Gemini's key page and its API
   host differ: pick hosts and page that agree, or say why in the row). The
   key leaves only through `keys.keyed_post(keys.handle(row["key_id"]),
   lease=...)`, called only from `tc.send`. The lease holds ONE key: `_take`
   reads the chosen provider's key (`keys.read_for_lease`), `lease_record`
   accepts only that `key_id` and caller `toolsmith`; a provider switch drops
   the lease (`drop_lease("off")`) before the new one is taken. Check that
   `security/secret_detector.py` catches the new key's shape (`sk-...`
   and `AIza...` are there) and add the route field to
   `pasted_key_guard.py` if a new route takes the key.
3. **The Bridge switch.** A new `Door` in `security/egress_doors.py`, id
   `toolsmith_cloud_<p>`: `hosts` (the API host only), `purpose`, `data_sent`
   (the same list as the consent sentence, naming the provider and its host),
   `config_flag` = the id, `env_var` `PUFFPAI_EGRESS_TOOLSMITH_CLOUD_<P>`,
   `posture_switch=None`, `settings_hint`, `notes`; and a `_WHAT_BY_DOOR`
   entry mapping it to `"toolsmith_install"`. A `_BUILT_IN` row in
   `src/puffpai/outside_services.py` (`name` "Toolsmith's cloud model
   (<Provider>)", `for`, `what_leaves`). `tc.DOOR` becomes the provider row's
   own switch id (`choose_cloud`, `use_local`).
4. **The adapter.** Two halves, because only `tc` may call `keyed_post`:
   * In `tc`: the provider's body and reply, picked by `PROVIDERS[provider]`
     inside `send` (today `body_for`, the `anthropic-version` and
     `anthropic-beta` headers, `fallbacks`, the `content` list check). Map:
     the system prompt, tools (name, description, JSON schema; sorted by
     name), tool calls and their results, stop reasons (to `end_turn`,
     `tool_use`, `max_tokens`, `refusal`), the max tokens (`MAX_TOKENS`,
     `FIND_MAX_TOKENS` for a find scope, which sends no tools), the effort
     setting if it has one, and its `usage` into the four keys `cost_usd`,
     `record_usage` and `context_tokens` read (`input_tokens`,
     `cache_creation_input_tokens`, `cache_read_input_tokens`,
     `output_tokens`). Errors: 429 and 5xx retry with `retry-after`
     (`_retry_wait`, `RETRIES`); 401/403 raise `CloudError("key", ...)` with
     that provider's own key line ("Toolsmith's <Provider> key was not
     accepted. Check it in Settings."); `KeyUnavailable("unreachable")`
     raises `CloudOffline` with its own offline line (step 9). Never send
     `fallbacks` or the `anthropic-beta` header to another provider. Every user turn
     goes through `clean()` (`secret_detector` plus the PII screen and
     `scrub_health_statements`, fails closed) BEFORE the body is built: write
     a `_clean_turn` for the provider's user and tool-result shapes, as
     today's does for `text` and `tool_result` blocks. `ask_json` reads the
     reply text: give it the provider's text the same way.
   * In `build/`: a new loop module beside `toolsmith_cloud_loop.py` (say
     `build/toolsmith_<p>_loop.py`, same `Conversation` and `run(conv, *,
     scope, ctx, tools, max_steps, app_name)` contract and stop words). It
     keeps the rules of the Anthropic loop: append-only per app try, the
     model's own turns (reasoning items, thought signatures) go back
     exactly as returned, all tool results of a turn together, `max_tokens`
     goes on once, a too-large try ends (`FULL_LINE`). `Run._go` in
     `build/toolsmith_run.py` picks the loop by `read_setting()["provider"]`;
     no other module names it.
   The scope guard does not change: `toolsmith_scope`, `SCOPE_CALLER`,
   `FIND_CALLERS` and `Run._cloud_scope` stay exactly as they are, no new
   callers.
5. **Consent.** `CONSENT_TEXT` names Anthropic. Each provider gets its own
   true sentence naming that company (a `consent_text` in its `PROVIDERS`
   row, or `CONSENT_TEXT_<P>`), minted into her ticket by `choose_cloud`.
   The stored hash (`consent.text`, `_sha(...)[:16]`) is per sentence:
   `read_setting` and `consent_outdated` compare against the CHOSEN
   provider's sentence, and changing a sentence takes the `RECONSENT_LINE` /
   `RECONSENT_HOLD_LINE` path (both name the provider). `status()` returns
   the chosen provider's `consent_text`. Keep README "What is sent", the
   switch's `data_sent`, `what_leaves` and the FAQ quote word for word with
   it.
6. **Settings card and its service.**
   `dashboard/app/dashboard/settings/ToolsmithModelCard.tsx`: a provider
   choice beside "On this computer" and "Anthropic", that provider's model
   list (from `status().models`), its key box and consent sentence; the
   inline PIN box on 423 `vault_locked` (`/vault/unlock`, then the same save)
   works for any provider. `dashboard/app/_lib/services/toolsmithModelService.ts`:
   a `provider` on the POST (beside `chooseAnthropic`). `toolsmith_model_api.py`
   `POST /toolsmith/model` passes `provider` to `choose_cloud` (its words
   "Choose this computer or Anthropic." too); the key stays only in the POST
   body.
7. **The wizard step.** Browser: `dashboard/app/onboarding/ToolsmithStep.tsx`
   (a button per provider; it opens the card). `wizard/service.py`:
   `TOOLSMITH_CHOICES` stays `("local", "cloud", "skipped")` (the provider
   lives in the setting); the wish (`record_wish`, `HOLD_LINE`) and
   `ToolsmithNotSaved`'s words name the chosen provider. Console:
   `cli.py` `TOOLSMITH_QUESTION`, the numbered menu, `check_shape("<p>",
   key)`, the model names (no "Claude " prefix), the key prompt. The key
   saves before setup through the row's `store_before_setup` (unchecked,
   while `github_account.before_setup()`), exactly as Anthropic's.
8. **Cost cap.** `monthly_cap` (enforced in `keys._keyed`, `paused_limit`)
   and `per_minute_cap` (`_per_minute_ok`, `busy`, retried by `send`) on the
   key row; `cost_note` states the same monthly number. Usage rows go to
   `<state>/toolsmith_cloud_usage.json` with the real provider; Settings
   shows `spent_line` and the run says "Toolsmith's cloud model: about $X
   for this job so far." (`Run._say_cost`).
9. **Offline.** No network or the provider down: `CloudOffline` with that
   provider's `OFFLINE_LINE` ("Toolsmith could not reach <Provider>, so it
   uses the model on this computer for this app."), and `Run._go` runs that
   app try on the local model (`local_for`). Finding work in
   `build/apps.py` (`search_words`, `rank`) falls back through
   `_cloud_failed` (its "could not ask Anthropic" names the provider).
10. **Docs.** README "### Toolsmith works best on a frontier model" (the
    provider, its "What is sent" bullet, cost, no connection) and the
    optional-needs row for its key; `docs/FAQ.md` "Can I use ChatGPT, Claude
    or another cloud model?" and a "What does Toolsmith send to
    <Provider>?" answer quoting its sentence; AGENTS.md 3b (stop saying only
    Anthropic is offered); `llms.txt`; the outside list wording (step 3).
    `grep -rn Anthropic src/puffpai dashboard/app` lists every owner line
    that names the provider today.
11. **Tests.** Copy and extend, never go online: provider copies of
    `tests/test_toolsmith_cloud.py` (local by default, consent hash and the
    reconsent hold, `clean` before send, retries, 401 key line, offline
    fallback, cost, lease) and `tests/test_toolsmith_cloud_health_screen.py`;
    `tests/test_wizard_toolsmith_step.py` and the hold tests in
    `tests/test_build_api.py`; the key row in `tests/test_keys_broker.py`.
    `test_only_toolsmith_run_mints_the_scope_and_runs_the_cloud_loop` keeps
    its `minting` and `keyed` lists exactly as they are; extend only its
    loop check so the new loop module is named by `build/toolsmith_run.py`
    alone. Then the live run (section 8): a real build that installs one
    app end to end on the new provider. Unit tests passing is not done.

Hard rules for every provider:

* Toolsmith is the ONLY agent that may use a cloud model; every other agent
  stays local.
* Only the owner's own key, kept in the vault; never in chat, a file or a log.
* Off unless the owner chooses it, with her explicit yes to that provider's
  consent sentence.
* The consent sentence stays true of what is sent.
* Everything sent passes `clean()` first.
* Toolsmith stays in the Bridge (`security/toolsmith_zone.py`, `zone_for`).
* No middleman aggregator without its own consent sentence naming it.
* The local model stays the default.

## 8. How to run and test

Python 3.11+, Ollama, Node 20+ for the dashboard (`dashboard/`).

```bash
pip install -e ".[dev]"
python -m pytest -q                       # Windows: py -3 -m pytest -q
python -m pytest tests/test_build_core.py tests/test_build_matrix.py -q
MYPYPATH=src python -m mypy src
```

Useful environment: `PUFFPAI_STATE_DIR`, `PUFFPAI_DATA_DIR` (keep test state
out of the real bot), `PUFFPAI_OLLAMA_URL`, `PUFFPAI_PORT`,
`PUFFPAI_DASHBOARD_PORT`, `PUFFPAI_TOOL_LOOP=off` (chat tools off).
`puffpai where` prints the folders, ports and Ollama a copy uses.

Run the relevant tests, not the whole suite, while iterating. On Windows,
every program started must use `procflags.hidden()` (no console windows) and
programs come from `security/host_programs.py` by full path.

**Definition of done for build changes is a live run**: a real goal end to end,
on spare ports (`puffpai start --port <n> --dashboard-port <m>`) with a fresh
state folder and the local brain; give the one Yes from a paired device; judge
it by what the owner sees on each job page (working, or not yet with a true
reason). Unit tests passing is not done.

When an owner or a builder asks the bot a question about BuildBot itself,
answer from [docs/FAQ.md](docs/FAQ.md) and the code, and point at the issue
tracker for anything not built yet. A change that makes an FAQ answer untrue
updates the FAQ in the same change.

## 9. Hard rules

1. Never write founders' or household members' names anywhere in the repo.
2. Owner-facing text uses plain words, short sentences, no jargon, no em
   dashes. Never say "door" or "doors" to the owner: say the Bridge, a switch,
   an outside service. Never "nothing leaves your computer"; the sentence is
   "Nothing leaves your computer without your consent."
3. Never weaken a security assertion to make a test pass. Fix the code.
4. The "puff puff pass" signature in code must never be removed, moved or
   reworded.
5. Goal-agnostic code only; fail closed; outbound only through
   `security/egress_doors.py`; keys only in the vault.
6. New source files carry the header `Copyright (c) 2025 Cypher Sisters.
   Apache-2.0 License.`
