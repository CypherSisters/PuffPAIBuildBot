# Install on Linux

Verified on Debian, WSL2 Ubuntu and CI's Ubuntu. Mac: [INSTALL_MAC.md](INSTALL_MAC.md).

## The one command

```bash
git clone https://github.com/CypherSisters/PuffPAIBuildBot.git
cd PuffPAIBuildBot
bash install.sh
```

It checks Python (3.11+) and Node.js (22.12+); a missing one is offered, ONE yes,
no password, as a pinned copy checked by SHA-256 (Python 3.12, python-build-standalone;
Node.js 24, nodejs.org) in `~/.local/share/puffpai-tools`. With the system Python it
offers Debian/Ubuntu's one `sudo apt-get` line (asking). It makes `venv/`, runs `pip
install -e .`, mints the API key, offers Ollama's official script (asking; the one
unpinned download), builds the web page, writes the launchers. No Docker, no git.
At the end it asks: set up in the web browser or in the terminal (same questions).
Flags: `--dry-run` (print everything, change nothing), `--no-questions`,
`--no-dashboard` (`--with-dashboard` is still accepted), `--with-voice`,
`--with-dev`, `--port N`, `--dashboard-port N`, `--state-dir PATH`, `--no-apt`,
`--yes-install-tools`, `--yes-install-ollama`, `--tools-dir PATH`, `--pull TAG`. Then:

```bash
./start-bot.sh         # starts the bot and its web page, waits, opens the browser
./stop-bot.sh          # stops exactly what start-bot.sh started
./run-setup.sh         # the setup questions in this terminal
./puffpai where        # what a command here touches (no secrets printed)
```

## The launchers, and why they exist

The installer mints a 64-hex API key into `<state>/buildbot.env` (mode 600, never
printed) and generates `start-bot.sh`, `stop-bot.sh`, `run-setup.sh`,
`run-backend.sh`, `run-dashboard.sh` and `./puffpai`, which all load it first.
The dashboard authenticates with `PUFFPAI_API_KEY`, and an explicitly set one
beats every stored key, so a stale OS keyring entry from this machine's last
PuffPAI cannot get between the two halves. `dashboard/.env.local` gets the same
key and `PUFFPAI_BACKEND_URL`. Re-running the installer reuses the key; deleting
`buildbot.env` mints a new one. `start-bot.sh` records both PIDs in
`<state>/running.env`; `stop-bot.sh` stops those and nothing else.

It also writes `.puffpai-install.json` at the checkout root, naming that env file,
so a bare `puffpai setup` in a fresh shell uses this install's state dir instead
of the default per-user one; anything already set in your environment still wins.
`puffpai where` prints the state dir, config dir, env file in use, ports, Ollama
address and where the API key comes from. It never prints the key itself.

Config, vault, manifest and key go to the per-user location; nothing secret lands
in the checkout. `--state-dir ./state` keeps one install's footprint beside it. Do
that when the installer warns the default holds another install's; the pointer
makes `puffpai` follow it too.

## The manual path

`sudo apt-get install -y python3-venv python3-dev build-essential`, then
`python3 -m venv venv && source venv/bin/activate`, `python -m pip install -e .`
(add `".[dev]"` for the tests), `puffpai setup`, and supply `PUFFPAI_API_KEY`
yourself. Toolchain elsewhere (the script detects these and prints it): Fedora/RHEL
`sudo dnf install -y python3-devel gcc gcc-c++ make`; Arch `sudo pacman -S
--needed base-devel python`; openSUSE `sudo zypper install -y python3-devel gcc
gcc-c++ make`.

## Ollama

BuildBot needs a local model runtime and will not install one behind your back.
The official install is `curl -fsSL https://ollama.com/install.sh | sh` (see
https://ollama.com/download), then `ollama pull llama3.2:1b`, the 2 GB-RAM floor
model. Ollama in a container or on another machine is fine: `puffpai setup
--ollama-url http://HOST:11434`. The address lives in
`config/puffpai_config.json`; `PUFFPAI_OLLAMA_URL=...` overrides it for one run.

## Troubleshooting

**`ensurepip is not available` / venv creation fails.** Debian splits the venv
module out: `sudo apt-get install -y python3-venv`, then re-run.

**`pip install -e .` fails building a wheel.** You are missing a C toolchain:
`build-essential` (Debian), `gcc gcc-c++ make` (Fedora). That is the only reason
the installer mentions apt at all. **`puffpai: command not found`:** the venv is
not active. Run `source venv/bin/activate`.

**401s, or a dashboard that shows nothing.** The two sides hold different keys:
re-run `bash install.sh` and start both halves with `./start-bot.sh`.

**"Nothing is answering at http://127.0.0.1:11434".** Not a failure: setup writes
the config and manifest and exits 0; no brain until Ollama is up. **Headless box:**
`puffpai ui` needs a display, so use `puffpai chat`, or `./run-backend.sh` plus the
dashboard from another machine. **Setup wrote to the wrong place?** `puffpai where`.
