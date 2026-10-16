# A skill's card

How a wizard skill decides and shows its decision. Every built-in skill except
Prove decides through `propose` / `apply` / `verify`, and both renderers (the
console and the dashboard) draw the card that comes out.
Discover (`packages/discover/skill.py`) is the worked example; Secure
(`packages/secure/skill.py`) is the example of a skill that asks more than one
question.

## The contract

Three functions in `packages/<id>/skill.py`:

```python
def propose(ctx) -> dict:   # {"card": {...}, ...anything your apply needs}
def apply(ctx, answers: dict) -> dict   # the runner's result dict
def verify(ctx) -> dict     # {"status": "pass"|"gap", "evidence": str, "gap": {...}}
```

* `propose` is **read-only**: measure, survey, read config. No writes, no
  downloads, no outside calls. It returns the card plus whatever it worked out; the
  runner puts the return value on `ctx.proposal` for `apply` and `verify`.
* **`propose` starts from its own applied section.** `ctx.applied()` (and
  `ctx.applied("choice", "model")` for a nested value) is that section as it
  stands, read-only; unlike `ctx.own()` it never creates it. The order is:
  what the owner already applied, then flags, then detection. `current` is the
  applied value and `proposed` stays your fresh recommendation, so a card drawn
  again after an apply shows the answer and still says what this build would
  have picked. Getting this wrong is not cosmetic: without it, `POST
  /wizard/apply` with a name and a goal returns a card that still reads
  "unnamed - no goal given yet", `GET /wizard/cards` agrees with it, and the
  dashboard tells the owner their answer was not taken. Re-applying with **no** answers has
  the same rule: keep what was settled, do not re-decide it from the defaults.
* **`propose` may say what its apply would share: `shared_preview`.** A console
  run applies as it goes, so Brain reads the address Discover *wrote*. The
  dashboard applies nothing before it draws, so `service.propose_cards` threads
  a *provisional* notebook instead: an optional `shared_preview` dict in your
  propose's return value is what your `apply` would put in `shared` if this
  proposal were accepted, and the cards below yours read it. It writes nothing
  (not the run's notebook, not the state, not the config), so `GET
  /wizard/cards` stays side-effect free, and a card the owner has already
  applied contributes nothing because the real values are in the run state.
  Without it the Brain row sizes a download against the configured address
  while the Discover row above it proposes a different one, and Crew offers
  "up to 0 specialists" with a census on the screen above. Discover previews
  `census`/`ollama_url`/`installed_models`, `secure:posture` previews
  `posture`/`gates`/`build_network`, Brain previews `chosen_model` and
  `purpose:identity` previews `bot_name`/`interview`; between them, every key
  the schedule's `requires` names. Preview what a later card needs; anything
  expensive or unknowable stays out of it.
* `apply` gets the *changed* fields only; an empty dict means "the proposal
  stands". Return `{"section": <yours>, "data": {...}, "shared": {...}}`, plus
  `gap`/`gaps`/`status`/`note` if needed. The runner refuses a `section` your
  `manifest.json` does not declare in `writes`.
* `verify` **observes the world**: a file on disk, a route answering, a model
  replying. Re-reading your own proposal is not verification, and the review
  before your package ships rejects it. Unreachable is a `gap`, not a failure.

`ctx` is `runner.RunContext`: `args`, `cfg`, `non_interactive`, `remote`,
`shared` (the run's cross-step values), `part`, `proposal`, `read(section)`
(refuses a section you did not declare in `reads`), `own()` (your own section,
live), `applied(...)` (the same section, read-only, never created) and `face()`.

`non_interactive` and `remote` are two different facts, and conflating them
loses the Tools card its question. `non_interactive` means **nobody typed an answer
for this call**: true of `--yes` and true of every HTTP request, because a
browser's answers arrive in the *next* request. `remote` means **a person is
there, in a browser**. So: `--yes` is `non_interactive and not remote` (decide
nothing on the owner's behalf), and the dashboard is `non_interactive and
remote` (ask, and expect the answer in the next apply). Neither is permission
to show a secret once: that is `face()`, and an HTTP run has none, ever.

## The card

Built as a plain dict and validated by `cards.validate_card`, strict, so a
typo is a test failure and not an owner's screen. Keys:

`skill`, `section` (must match your manifest), `part` (empty unless your
package asks more than one question, see below), `title` (the owner's words,
"What this machine is", with the id as small type in the renderer), `decided`
(one line), `status` (`ready`/`needs_you`/`gap`/`blocked`/`not_built`),
`consequential` + `consequential_reasons` (from `cards.CONSEQUENTIAL_REASONS`;
true needs at least one), `estimate` `{bytes, seconds, basis}` with basis in
`measured`/`calibrated`/`unknown` (an unknown basis carries no seconds, so no
total can sum it), `fields`, `gaps`.

Field kinds: `choice` (add `free_text: true` + `pattern` to allow a typed
answer), `multi_choice`, `text` (`max_length`, optional `pattern`), `toggle`,
`number` (`min`, `max`), `secret_entry`, `info`. Every field needs `id` and
`label`; `help` is a sentence.
**`secret_entry` carries no value, ever.** No `current`, no `proposed`: a card
holding one is refused. Its answer reaches your `apply` and nowhere else: the
run log and the API response get `cards.redact_answers`.

A card is named by `cards.card_id`: `discover`, or `secure:vault` for a
package with parts. That id is what the schedule, the API's `order`, the
dashboard's rows and `/wizard/apply` all use.

## Asking more than one question

Declare `PARTS = ("posture", "vault", "bridge")` at module level (Purpose:
`("identity", "place")`), take `ctx.part` (fall back to inferring it), and set
`card["part"]` to the part you proposed; the runner refuses a card that answers a different question from the
one it was asked. The schedule names the part as `card:<part>`; the API accepts
`skill:<part>`, and **refuses a bare skill id** for such a package, because
"whichever question the run was up to" is not something a browser should be
able to apply by accident.

**Split a package into parts when one question's answer decides another's
default.** Purpose is the example: the id a place is scaffolded under is
`suggest_id(name, goal)`, and on a single card that suggestion would be
computed before the owner had typed either, so a bot named Infinity would be
offered the fallback id and accepting the default would give it. `purpose:identity` takes the
name and the goal; `purpose:place` proposes the id *from what identity
applied*. A sibling part may go stale: `stale.stale_for_answers` drops the
skill's own id from the stale list only for a package with **one** card.

## What a browser may run: `POST /wizard/run`

"Accept the rest" applies every card the owner has not answered, and it also
runs the schedule's non-card entries at their scheduled positions (the Ollama
config, the build manifest, Prove's static checks), because they ask the owner
nothing and skipping them would leave a build made in a browser with no
`config/build_manifest.json` at all. Three rules hold there and they are in
`service._run_support_step` / `_run_rest_locked`:

* an entry runs only when the `requires` it names are in the run's shared
  notebook, otherwise it is reported as waiting;
* its output is captured and dropped: the caller is an HTTP request, and
  stdout is a server log;
* the run has a renderer, so `show_once()` is `None`: no face, so no secret is
  created from a request.

First-run setup is **not** marked complete while a card that blocks (a package
whose manifest says `blocking`) is still `needs_you`. The manifest is still
written (a record of what was decided is the honest artefact), and the reply
carries `complete: false` with `outstanding: ["secure:vault"]`.

## Showing a value exactly once

A recovery code or a pairing code may not go on the card, in the run log or in
an API response. It goes to the run's show-once face: `ctx.face()`, which is
`None` unless something is drawing this wizard on a console.

**Fail closed.** No face means no secret is created; the vault card says where
the PIN can be set instead. The face is per run (`Runner.show_once`), never a
module global: otherwise a server that had once rendered a card on a terminal
would answer "yes, somebody can see this" to every later HTTP request.

**Scripted runs must budget lines for the recovery ceremony.** On a console
run, the show-once face does not just print: after the vault card it shows the
recovery code and then *reads from stdin*: it asks the owner to type the code
back, and on a mismatch it burns that code, shows a fresh one and asks again.
That is **0 to 3 extra lines consumed from stdin** (none when there is no face
or no code, up to `_RECOVERY_MAX_SHOWS` when codes are re-issued), taken from
the same stream the card answers come from. A file of scripted answers that
does not account for them has every later card answered one line out of step,
which, because end-of-file is sticky (`cli._read_line`), shows up as the rest
of the wizard silently taking defaults rather than as an error.

## Wiring it in

1. Write `propose`/`apply`/`verify` in `packages/<id>/skill.py`.
2. Put your entry in `runner.SCHEDULE`:
   `Step("<id>.card", "<id>", V2_PART, (<requires>,))`, or
   `Step("<id>.<part>", "<id>", f"{V2_PART}:<part>")` for one part of several.
   `requires` names keys in the run's shared notebook, and every one of them
   must be written by an entry **above** yours.
3. Add rows to `stale.FIELD_STALENESS` for every answerable field of your card,
   keyed `"<section>.<field id>"`, or put the field on
   `stale.MAKES_NOTHING_STALE` with the reason. A field on neither is a test
   failure (`tests/test_wizard_stale_and_hosts.py`). Prove and Handoff are
   added for you.
4. If your `apply` reaches the network, declare the hosts in
   `manifest.json`'s `permissions.network_hosts`. A skill that downloads and
   declares nothing fails a test.
5. Nothing to do for the CLI or the API: `render_cli` and `/wizard/*` render
   whatever validates, and the dashboard takes its order and its row ids from
   `GET /wizard/cards`.

## Testing it

Copy `tests/test_wizard_discover.py`: it pins the machine (a fixed census, a
stubbed `setup_ops.list_models`) so nothing depends on the box. Cover propose
returning a card `validate_card` accepts (detection failing as well as
working); `apply` writing the section contents the card promises
(`tests/test_wizard_golden.py` holds a checked-in manifest per scripted run and
fails when any section's contents change, so a change to your `apply` is a
diff you either explain or revert); and `verify` returning a gap, not an
exception, when what it checks is absent.

## What not to touch

`build_manifest/v2` and its section names; `RESERVED_SECTIONS`; another
package's section; the runner's order or blocking rules (only Brain and Secure
may block, from their manifests). Do not print from a skill: the renderer
prints and the face shows. Do not put a command string in a gap: name a
registered id in `fixes.COMMANDS` and let the runner render the line.

The `_setup_*_step` functions in `cli` are not called by `src/` (except
`_setup_manifest_step`, which the runner's own manifest entry uses). They stay
because roughly a hundred assertions across fifteen test files run them as the
per-skill equivalence control.

**Your verify is also the resume rule.** If your package is `blocking`
(Brain, Secure), a resumed run does not skip your card because a file says it
finished: `Runner.reverify_blocking` calls your `verify` again against the
machine as it is now, and anything that is not `pass` is asked again. Write
`verify` so that it is true of the machine and not of the record. The vault
is checked on disk on top of verify, because Secure's verify only looks at the
vault when the *recorded* block claims one.
