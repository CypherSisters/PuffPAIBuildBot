# Goal measurement harness

`scripts/measure_goals.py` measures the build engine on many unrelated goals.
It is run by hand after a merge; the unit suite collects nothing from this folder.

Run from the repository root with `PYTHONPATH=src`, `PYTHONDONTWRITEBYTECODE=1`,
`PUFFPAI_OLLAMA_URL=http://127.0.0.1:11435` and your own `PUFFPAI_STATE_DIR`.

| Mode | What it does |
|---|---|
| `--dry` | needs step with the local brain, then find_parts on this computer only |
| `--live` | the same, then online after the owner's yes (only screened phrases leave) |
| `--discovery` | the discovery bar: keyless live run on `tests/fixtures/r3_discovery_goals.json`, writes `F_discovery_<run>.json` and `.md` |
| `--integrate` | walks the wizard over the HTTP API at `--base-url` (key from `--api-key-file`), ticks the best addable line of each job, presses "Looks good" and follows the build |

Other flags: `--goals <file>`, `--keyless` (refuses to start when `GITHUB_TOKEN`,
`GH_TOKEN` or a github key in the vault is present), `--pace 65` (seconds between
goals; GitHub's keyless search allowance is 10 a minute for the whole home
network), `--out <dir>`, `--only a,b`, `--show-hits`, `--reuse-needs <earlier json>`
(uses the jobs of an earlier run again, so its searches answer from the 24-hour
cache and cost no allowance), `--model`.

Every outgoing request is captured (URL, method, header names only, body) by
wrapping `source_fetch`'s transport and `urllib`'s opener. The report holds, per
goal: the jobs and their search phrases with their state, recall against the
hand-written gold list (reading helper, cosine 0.45), what was sent, every plan
line with its scores, notes, the captured requests, timings, and for a goal that
fails the bar, every raw find with the reason it was left out.

Nothing in the harness is tuned to a goal: the goals file is only what is measured.
