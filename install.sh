#!/usr/bin/env bash
# PuffPAI BuildBot installer (Linux / macOS).
#
# Makes a venv in ./venv, installs this checkout with pip, builds the web page
# (the dashboard), and writes launchers: ./start-bot.sh starts the bot and its
# web page together and opens it; ./stop-bot.sh stops them (on a Mac also
# "Start BuildBot.command" and "Stop BuildBot.command" to double-click). It
# never installs anything (system packages, Python, Node.js, Ollama) without a
# plain yes from the person at the keyboard or a flag, and never downloads a
# model unless --pull asks for one (the wizard downloads the one it picks,
# after asking).
#
# A computer with nothing on it ("green"): Python, Node.js and, on a Mac,
# Ollama are downloaded after ONE yes, pinned and checked by SHA-256
# (scripts/install_tools.sh), into a tools folder of their own. No sudo, no
# Homebrew. On Linux, Ollama still comes from its own official install script
# (curl ... | sh, which asks for the password), after its own yes.
#
# Test hook (not for owners): PUFFPAI_INSTALL_PRETEND_MISSING=python,node,ollama
# makes those tools look missing, so the missing-tool path can be run with
# --dry-run on a machine that already has them (install.ps1 has the same hook).
# PUFFPAI_TOOLS_PLATFORM=darwin-arm64 (or linux-x64, unsupported, ...) picks
# which pinned downloads a dry run shows, and darwin-* also takes the Mac path
# (tests/test_install_tools_sh.py runs both on any computer).
#
# ASCII only in every string printed here: this file is read by people whose
# terminals are not all UTF-8. Everything a person reads is plain words;
# internal names stay in comments.

set -euo pipefail

PY_MIN_MAJOR=3
PY_MIN_MINOR=11          # setup.py: python_requires=">=3.11"
# Node.js: 22.4 or newer, because the part that lets your bot use a browser app
# needs Node's built-in WebSocket. The web page itself
# (dashboard/package.json engines: ^20.19 || >=22.12) needs 22.12 on the 22
# line, so node_ok asks for both: 22.12 or newer.
NODE_MIN_MAJOR=22
NODE_MIN_MINOR=4
NODE_WEB_PAGE_MINOR=12

DRY_RUN=0
BUILD_DASHBOARD=1
WITH_VOICE=0
WITH_DEV=0
NO_APT=0
NO_QUESTIONS=0
NEW_BOT=0
YES_OLLAMA=0
YES_TOOLS=0
DESKTOP=0
TOOLS_ARG=""
PULL_TAG=""
PORT=8000
DASH_PORT=3003
STATE_ARG=""

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$REPO_DIR/venv"

# bash install.sh remove [--yes] [--keep-vault]: take BuildBot off this
# computer. The plan, the question and the deleting are puffpai.remove_cli's
# (the same as "puffpai remove"): it stops what start-bot.sh started (by the
# numbers in running.env), says what will be deleted, asks once, and never
# follows a link or leaves this install's own folders. This installer sets
# up no systemd units, so there are none to stop.
if [ "${1:-}" = "remove" ]; then
    shift
    RM_PY="$VENV_DIR/bin/python"
    if [ ! -x "$RM_PY" ]; then
        printf '%s\n' "BuildBot's own parts are missing from $REPO_DIR, so it cannot remove itself. Delete that folder by hand."
        exit 1
    fi
    RM_ENV="$("$RM_PY" -c 'import json,sys; print(json.load(open(sys.argv[1]))["env_file"])' "$REPO_DIR/.puffpai-install.json" 2>/dev/null || true)"
    if [ -n "$RM_ENV" ] && [ -r "$RM_ENV" ]; then
        while IFS= read -r line; do
            case "$line" in ''|'#'*) continue ;; esac
            export "${line%%=*}=${line#*=}"
        done < "$RM_ENV"
    fi
    cd "${TMPDIR:-/tmp}"
    exec "$RM_PY" -m puffpai.remove_cli "$@"
fi

usage() {
    cat <<'EOF'
BuildBot setup for Linux and macOS.

Usage: bash install.sh [options]
       bash install.sh remove [--yes] [--keep-vault]   (takes BuildBot off this computer; it asks first)

  --no-dashboard        do not build the web page (the terminal still works)
  --with-dashboard      accepted for older instructions; the web page is built
                        anyway unless --no-dashboard is given
  --with-voice          also install the parts for talking out loud
  --with-dev            also install the tools for running the tests
  --no-apt              never offer to run the system package line; print it
  --yes-install-tools   download what is missing of Python, Node.js and (on a
                        Mac) Ollama without asking; checked, no password
  --yes-install-ollama  install Ollama without asking (Linux: its official
                        script, which asks for your password)
  --tools-dir PATH      where downloaded programs go (default: beside your
                        bot's private files, never inside them)
  --desktop-shortcuts   on a Mac, also put Start and Stop on your Desktop
  --pull NAME           download this AI model with Ollama at the end
  --port N              port for your bot (default 8000)
  --dashboard-port N    port for your bot's web page (default 3003)
  --state-dir PATH      where this copy keeps its private files
                        (default: your user folder)
  --no-questions        ask nothing; print what to do instead (a bot you
                        already have is kept)
  --new-bot             start a new bot even if that folder already has one
                        (the old one is moved aside, never deleted)
  --dry-run             show every step and every file it would write, and
                        change nothing at all
  -h, --help            this text

Nothing (system packages, Python, Node.js, Ollama, an AI model) is installed
or downloaded unless you say yes or pass the flag for it.

This copy gets its own private key, saved in <private files>/buildbot.env and
never printed, so its bot and its web page always recognise each other. A file
.puffpai-install.json beside this script tells every puffpai command which copy
this is; "puffpai where" prints it.
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --with-dashboard) BUILD_DASHBOARD=1 ;;
        --no-dashboard)   BUILD_DASHBOARD=0 ;;
        --with-voice)     WITH_VOICE=1 ;;
        --with-dev)       WITH_DEV=1 ;;
        --no-apt)         NO_APT=1 ;;
        --no-questions)   NO_QUESTIONS=1 ;;
        --new-bot)        NEW_BOT=1 ;;
        --yes-install-ollama) YES_OLLAMA=1 ;;
        --yes-install-tools)  YES_TOOLS=1 ;;
        --tools-dir)      shift; TOOLS_ARG="${1:-}" ;;
        --desktop-shortcuts) DESKTOP=1 ;;
        --pull)           shift; PULL_TAG="${1:-}" ;;
        --port)           shift; PORT="${1:-8000}" ;;
        --dashboard-port) shift; DASH_PORT="${1:-3003}" ;;
        --state-dir)      shift; STATE_ARG="${1:-}" ;;
        --dry-run)        DRY_RUN=1 ;;
        -h|--help)        usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

# Ask only when a person can answer: not under --no-questions or --dry-run,
# and not when the input is a pipe or a file.
CAN_ASK=0
if [ "$NO_QUESTIONS" = "0" ] && [ "$DRY_RUN" = "0" ] && [ -t 0 ]; then
    CAN_ASK=1
fi

PRETEND=",$(printf '%s' "${PUFFPAI_INSTALL_PRETEND_MISSING:-}" | tr 'A-Z' 'a-z' | tr -d ' '),"
pretends() { case "$PRETEND" in *",$1,"*) return 0 ;; esac; return 1; }
have() { command -v "$1" >/dev/null 2>&1 && ! pretends "$1"; }

# The web page's build tool (Next.js) sends usage reports to its maker unless
# this is set. BuildBot promises zero telemetry, so it is off for everything
# this installer runs, and every launcher and env file below sets it again.
export NEXT_TELEMETRY_DISABLED=1

say()  { printf '%s\n' "$*"; }
step() { printf '\nStep %s of 6: %s\n' "$1" "$2"; }
fail() { printf '\nSetup stopped: %s\n' "$*" >&2; exit 1; }

# Run a command, or print it under --dry-run.
run() {
    if [ "$DRY_RUN" = "1" ]; then
        printf '  [dry-run] %s\n' "$*"
        return 0
    fi
    "$@"
}

# Run a noisy command with its output in a log file; on failure show the end.
run_logged() {
    _log="$1"; shift
    if [ "$DRY_RUN" = "1" ]; then
        printf '  [dry-run] %s   (output to %s)\n' "$*" "$_log"
        return 0
    fi
    mkdir -p "$(dirname "$_log")"
    if "$@" >"$_log" 2>&1; then
        return 0
    fi
    say "  That did not work. The last lines of $_log:"
    tail -n 25 "$_log" | sed 's/^/    /'
    return 1
}

ask_yes_no() {
    _i=0
    while [ $_i -lt 3 ]; do
        printf '%s Type y for yes or n for no: ' "$1"
        read -r _a || _a=""
        case "$_a" in
            [yY]|[yY][eE][sS]) return 0 ;;
            [nN]|[nN][oO]) return 1 ;;
        esac
        _i=$((_i + 1))
    done
    return 1
}

# Pinned downloads (Python, Node.js, and Ollama on a Mac): versions, hashes
# and every check, in one file of their own.
TOOLS_LIB="$REPO_DIR/scripts/install_tools.sh"
[ -r "$TOOLS_LIB" ] || fail "a part of this setup is missing ($TOOLS_LIB). Download BuildBot again, then run this setup again."
# shellcheck source=scripts/install_tools.sh
. "$TOOLS_LIB"

abs_path() {
    case "$1" in
        /*) printf '%s\n' "$1" ;;
        *)  printf '%s\n' "$REPO_DIR/$1" ;;
    esac | sed -e 's|/\./|/|g' -e 's|/\.$||'
}

# One value out of a KEY=value file, or nothing.
env_value() {
    [ -r "$1" ] || return 0
    sed -n "s/^[[:space:]]*$2[[:space:]]*=[[:space:]]*//p" "$1" | head -1
}

# Mirrors puffpai.config.get_state_dir so the launcher points where the backend
# already looks: an explicit flag, then PUFFPAI_STATE_DIR, then
# PUFFPAI_DATA_DIR/state, then the per-user location.
if [ -n "$STATE_ARG" ]; then
    STATE="$(abs_path "$STATE_ARG")"
elif [ -n "${PUFFPAI_STATE_DIR:-}" ]; then
    STATE="$(abs_path "$PUFFPAI_STATE_DIR")"
elif [ -n "${PUFFPAI_DATA_DIR:-}" ]; then
    STATE="$(abs_path "$PUFFPAI_DATA_DIR")/state"
elif [ "$(uname -s)" = "Darwin" ]; then
    STATE="$HOME/Library/Application Support/PuffPAI"
else
    STATE="${XDG_STATE_HOME:-$HOME/.local/state}/puffpai"
fi

# DATA_DIR defaults to the checkout (that is the project's default and the
# manifest lives under it), but --state-dir means "keep this install's whole
# footprint here", so it moves both.
if [ -n "${PUFFPAI_DATA_DIR:-}" ]; then
    DATA_DIR="$(abs_path "$PUFFPAI_DATA_DIR")"
elif [ -n "$STATE_ARG" ]; then
    DATA_DIR="$STATE"
else
    DATA_DIR="$REPO_DIR"
fi

node_ok() {
    # Both floors: the runner's 22.4 (NODE_MIN_MAJOR.NODE_MIN_MINOR) and the web
    # page's engines (^20.19 || >=22.12). Node 20 has no built-in WebSocket.
    have node || return 1
    _v="$(node --version 2>/dev/null | sed 's/^v//')"
    _maj="${_v%%.*}"; _rest="${_v#*.}"; _min="${_rest%%.*}"
    case "$_maj" in ''|*[!0-9]*) return 1 ;; esac
    case "$_min" in ''|*[!0-9]*) return 1 ;; esac
    [ "$_maj" -lt "$NODE_MIN_MAJOR" ] && return 1
    [ "$_maj" -eq "$NODE_MIN_MAJOR" ] && [ "$_min" -lt "$NODE_MIN_MINOR" ] && return 1
    [ "$_maj" -eq 22 ] && [ "$_min" -ge "$NODE_WEB_PAGE_MINOR" ] && return 0
    [ "$_maj" -ge 23 ] && return 0
    return 1
}

PLATFORM="$(tools_platform)"
IS_MAC=0
if [ -n "${PUFFPAI_TOOLS_PLATFORM:-}" ]; then
    # Test hook: the named platform decides, so a Mac can practise Linux.
    case "$PLATFORM" in darwin-*) IS_MAC=1 ;; esac
elif [ "$(uname -s)" = "Darwin" ]; then
    IS_MAC=1
fi
TOOLS_DIR="$(abs_path "${TOOLS_ARG:-$(tools_default_dir)}")"
APPS_ROOT="$(abs_path "${PUFFPAI_APPS_ROOT:-${XDG_DATA_HOME:-$HOME/.local/share}/puffpai/apps}")"

OLLAMA_URL="${OLLAMA_HOST:-http://127.0.0.1:11434}"
case "$OLLAMA_URL" in http*) : ;; *) OLLAMA_URL="http://$OLLAMA_URL" ;; esac
ollama_answers() {
    command -v curl >/dev/null 2>&1 && curl -fsS --max-time 3 "$OLLAMA_URL/api/tags" >/dev/null 2>&1
}

say ""
say "BuildBot setup"
say "This gets BuildBot ready on this computer. Nothing is installed without asking you."
[ "$DRY_RUN" = "1" ] && say "(Practice run: it shows each step and changes nothing.)"

# --- 1. Python, and what else is missing -------------------------------------
step 1 "Python (BuildBot is written in it)"

PY_PROBE="import sys; raise SystemExit(0 if sys.version_info[:2] >= ($PY_MIN_MAJOR, $PY_MIN_MINOR) else 1)"
PYTHON=""
PY_FROM_TOOLS=0
if ! pretends python; then
    for cand in python3 python3.13 python3.12 python3.11 python; do
        have "$cand" || continue
        # A Mac's stand-in python3 opens an "install the tools" box instead.
        if tools_skip_python "$cand"; then continue; fi
        if "$cand" -c "$PY_PROBE" 2>/dev/null; then
            PYTHON="$cand"
            break
        fi
    done
fi
# The copy an earlier run of this setup downloaded (BuildBot's own, so the
# test hook above does not hide it).
OWN_PY="$TOOLS_DIR/python-${TOOLS_PY_VERSION}-${TOOLS_PY_BUILD}/bin/python3"
if [ -z "$PYTHON" ] && [ -x "$OWN_PY" ] && tools_python_ok "$OWN_PY"; then
    PYTHON="$OWN_PY"
    PY_FROM_TOOLS=1
fi

# Node.js, only when the web page is wanted (step 5 builds it).
NODE_FROM_TOOLS=""
use_own_node() {
    NODE_FROM_TOOLS="$1"
    PATH="$1/bin:$PATH"
    export PATH
    PRETEND="$(printf '%s' "$PRETEND" | sed 's/,node,/,/g')"
}
NEED_NODE=0
if [ "$BUILD_DASHBOARD" = "1" ] && ! node_ok; then
    OWN_NODE="$TOOLS_DIR/node-v${TOOLS_NODE_VERSION}-${PLATFORM}"
    if [ -x "$OWN_NODE/bin/node" ]; then
        use_own_node "$OWN_NODE"
    fi
    if ! node_ok; then NEED_NODE=1; fi
fi

# Ollama. On a Mac it can be downloaded with the rest (no password); Linux
# keeps its own question in step 4 (the official script asks for one).
OLLAMA_BIN=""
OLLAMA_APP=""
NEED_OLLAMA=0
if [ "$IS_MAC" = "1" ] && ! have ollama && ! ollama_answers; then
    OWN_OLLAMA="$TOOLS_DIR/ollama-${TOOLS_OLLAMA_VERSION}/ollama"
    if [ -x "$OWN_OLLAMA" ]; then
        OLLAMA_BIN="$OWN_OLLAMA"
    elif pretends ollama; then
        NEED_OLLAMA=1
    elif [ -d "/Applications/Ollama.app" ]; then
        OLLAMA_APP="/Applications/Ollama.app"
    elif [ -d "$HOME/Applications/Ollama.app" ]; then
        OLLAMA_APP="$HOME/Applications/Ollama.app"
    else
        NEED_OLLAMA=1
    fi
fi

# Ollama needs macOS 14 or newer; an older Mac gets the link instead.
if [ "$NEED_OLLAMA" = "1" ] && [ "$(uname -s)" = "Darwin" ]; then
    _macver="$(sw_vers -productVersion 2>/dev/null || echo 0)"
    if [ "${_macver%%.*}" -lt 14 ] 2>/dev/null; then
        say "  Ollama needs macOS 14 or newer, and this Mac has $_macver, so setup cannot get it for you."
        NEED_OLLAMA=0
    fi
fi

# One question for everything that can be downloaded without a password.
OFFER_PY=0; OFFER_NODE=0; OFFER_OLLAMA=0
if [ "$PLATFORM" != "unsupported" ]; then
    if [ -z "$PYTHON" ]; then OFFER_PY=1; fi
    if [ "$NEED_NODE" = "1" ]; then OFFER_NODE=1; fi
    if [ "$NEED_OLLAMA" = "1" ]; then OFFER_OLLAMA=1; fi
fi
GO_PY=0; GO_NODE=0; GO_OLLAMA=0
if [ "$OFFER_PY$OFFER_NODE$OFFER_OLLAMA" != "000" ]; then
    if ! tools_dir_ok "$TOOLS_DIR" "$REPO_DIR" "$STATE" "$DATA_DIR" "$APPS_ROOT"; then
        say "  Setup would download what is missing into $TOOLS_DIR, but that is"
        say "  inside one of BuildBot's own folders, which is not safe. To pick another"
        say "  folder, run setup again with --tools-dir and a folder of your own."
        OFFER_PY=0; OFFER_NODE=0; OFFER_OLLAMA=0
    fi
fi
if [ "$OFFER_PY$OFFER_NODE$OFFER_OLLAMA" != "000" ]; then
    say "  BuildBot needs these free programs, and this computer does not have them yet:"
    [ "$OFFER_PY" = "1" ]     && say "    - Python $TOOLS_PY_VERSION (about 25 MB)"
    [ "$OFFER_NODE" = "1" ]   && say "    - Node.js $TOOLS_NODE_VERSION (about 50 MB), for your bot's web page"
    [ "$OFFER_OLLAMA" = "1" ] && say "    - Ollama $TOOLS_OLLAMA_VERSION (about 170 MB), which runs the AI model"
    say "  Setup can download them from their makers' own websites, check that each"
    say "  one is exactly the copy BuildBot expects, and keep them in this folder:"
    say "    $TOOLS_DIR"
    say "  It does not need your password, and it changes nothing else on this computer."
    GO_ALL=0
    if [ "$YES_TOOLS" = "1" ]; then
        GO_ALL=1
    elif [ "$DRY_RUN" = "1" ]; then
        say "  [dry-run] would ask: Download them now? (y/n)"
        GO_ALL=1
    elif [ "$CAN_ASK" = "1" ] && ask_yes_no "  Download them now?"; then
        GO_ALL=1
    fi
    if [ "$GO_ALL" = "1" ]; then
        GO_PY="$OFFER_PY"; GO_NODE="$OFFER_NODE"; GO_OLLAMA="$OFFER_OLLAMA"
    elif [ "$YES_OLLAMA" = "1" ]; then
        GO_OLLAMA="$OFFER_OLLAMA"
    fi
    if [ "$GO_PY$GO_NODE$GO_OLLAMA" = "000" ]; then
        say "  Nothing was downloaded."
    fi
fi

if [ "$GO_PY" = "1" ]; then
    if tools_install_python "$TOOLS_DIR" "$PLATFORM"; then
        PYTHON="$TOOLS_PYTHON"
        PY_FROM_TOOLS=1
    else
        say "  Python could not be set up."
    fi
fi
if [ "$GO_NODE" = "1" ]; then
    if tools_install_node "$TOOLS_DIR" "$PLATFORM"; then
        [ "$DRY_RUN" = "1" ] || use_own_node "$TOOLS_NODE_HOME"
    else
        say "  Node.js could not be set up. Your bot still works in a terminal without its web page."
    fi
fi
if [ "$GO_OLLAMA" = "1" ]; then
    if tools_install_ollama "$TOOLS_DIR" "$PLATFORM"; then
        [ "$DRY_RUN" = "1" ] || OLLAMA_BIN="$TOOLS_OLLAMA"
    else
        say "  Ollama could not be set up. Setup goes on; your bot needs Ollama to answer."
    fi
fi

if [ -z "$PYTHON" ]; then
    if [ "$OFFER_PY" = "1" ]; then
        fail "BuildBot needs Python ${PY_MIN_MAJOR}.${PY_MIN_MINOR} or newer. To let setup download it, run this setup again and type y (or add --yes-install-tools)."
    fi
    fail "BuildBot needs Python ${PY_MIN_MAJOR}.${PY_MIN_MINOR} or newer. Install it from https://www.python.org/downloads/ or your system's software centre, then run this setup again."
fi
if [ "$PY_FROM_TOOLS" = "1" ]; then
    if [ "$DRY_RUN" = "1" ] && [ ! -x "$PYTHON" ]; then
        say "  Would use Python $TOOLS_PY_VERSION from $TOOLS_DIR."
    else
        say "  Using Python $TOOLS_PY_VERSION from $TOOLS_DIR."
    fi
else
    say "  Found $("$PYTHON" --version 2>&1)."
fi

# System packages: the venv module and a compiler, which some distros ship
# separately from Python itself. Not needed for the downloaded Python, which
# has both.
DISTRO="unknown"
if [ -r /etc/os-release ]; then
    # shellcheck disable=SC1091
    DISTRO="$(. /etc/os-release && printf '%s' "${ID_LIKE:-$ID}")"
fi
case "$(uname -s)" in
    Darwin) DISTRO="macos" ;;
esac
[ "$PY_FROM_TOOLS" = "1" ] && DISTRO="own-python"

APT_LINE="sudo apt-get install -y python${PY_MIN_MAJOR}-venv python${PY_MIN_MAJOR}-dev build-essential"
case "$DISTRO" in
    *debian*|*ubuntu*)
        if [ "$CAN_ASK" = "1" ] && [ "$NO_APT" = "0" ]; then
            say "  On Ubuntu and Debian, Python needs two more system parts. The command is:"
            say "    $APT_LINE"
            if ask_yes_no "  Run it now? It asks for your password."; then
                run sh -c "$APT_LINE" || say "  That did not finish. If the next step fails, run the line above yourself."
            else
                say "  Skipped. If the next step fails, run the line above yourself."
            fi
        else
            say "  If the next step fails on Ubuntu or Debian, run this first:"
            say "    $APT_LINE"
        fi
        ;;
    macos)
        say "  If the next step fails on a Mac, run this, then run setup again: xcode-select --install"
        ;;
    *fedora*|*rhel*)
        say "  If the next step fails, run: sudo dnf install -y python3-devel gcc gcc-c++ make"
        ;;
    *arch*)
        say "  If the next step fails, run: sudo pacman -S --needed base-devel python"
        ;;
    *suse*)
        say "  If the next step fails, run: sudo zypper install -y python3-devel gcc gcc-c++ make"
        ;;
esac

# --- 2. venv + pip ---------------------------------------------------------
step 2 "BuildBot's own parts"
say "  Getting BuildBot's parts from the Python package library. This takes a few minutes."
if [ -x "$VENV_DIR/bin/python" ] && tools_python_ok "$VENV_DIR/bin/python"; then
    say "  Reusing the venv folder from last time."
else
    if [ -e "$VENV_DIR" ]; then
        # Made by an older Python (or half made): BuildBot's own folder, so
        # it is replaced rather than reused.
        say "  The venv folder from last time does not work with this Python, so setup makes a new one."
        run rm -rf "$VENV_DIR"
    fi
    run "$PYTHON" -m venv "$VENV_DIR"
fi

EXTRAS=""
[ "$WITH_VOICE" = "1" ] && EXTRAS="${EXTRAS}voice,"
[ "$WITH_DEV" = "1" ]   && EXTRAS="${EXTRAS}dev,"
TARGET="."
if [ -n "$EXTRAS" ]; then
    TARGET=".[${EXTRAS%,}]"
fi

run "$VENV_DIR/bin/python" -m pip install -q --disable-pip-version-check --upgrade pip
( cd "$REPO_DIR" && run "$VENV_DIR/bin/python" -m pip install -q --disable-pip-version-check -e "$TARGET" )
[ "$DRY_RUN" = "1" ] || say "  Done."

# --- 3. This install's own key and state dir -------------------------------
#
# Why this section exists: the dashboard proxy sends
# Authorization: Bearer $PUFFPAI_API_KEY and nothing else, so if the two sides
# do not hold the SAME key every dashboard call is a 401. Writing a key into the
# state dir is not enough on a machine that has ever run PuffPAI: the backend
# prefers a stored secret (vault, then OS keyring) over that file, so a stale
# entry from another install would win. The one thing that beats every store is
# an explicitly set PUFFPAI_API_KEY, so this install mints a key into
# <state>/buildbot.env and every launcher exports it before starting anything.

step 3 "Your bot's private files"

ENV_FILE="$STATE/buildbot.env"
BACKEND_URL="http://127.0.0.1:$PORT"
LOG_DIR="$STATE/logs"
# The pointer a bare "puffpai" command reads (puffpai.install_pointer): without
# it, "puffpai setup" in a fresh shell resolves the DEFAULT per-user state dir
# and writes the config, vault and manifest somewhere run-backend.sh never looks.
# One machine's absolute paths, so it is gitignored.
POINTER="$REPO_DIR/.puffpai-install.json"

say "  Your bot keeps its private files in: $STATE"
[ "$DATA_DIR" = "$STATE" ] || say "  Its other files go in: $DATA_DIR"
say "  Your bot will use port $PORT, and its web page port $DASH_PORT."

# Keep the bot already here, or start a new one. A state folder that already
# holds a built bot (a build manifest, a finished setup or a personality) used
# to be reused without a word, so an owner who ran setup again to start over
# never saw the setup questions. Now it asks. A new bot moves the old folder
# aside as state-<name>-<date>; nothing is ever deleted. --no-questions (or
# nobody at the keyboard) keeps the old bot; --new-bot starts a new one.
OLD_NAME="$("$PYTHON" - "$STATE/persona.json" "$DATA_DIR/config/build_manifest.json" "$DATA_DIR/data/setup_state.json" <<'PY' 2>/dev/null || true
import json, re, sys
def load(p):
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return None
persona, manifest, flag = (load(p) for p in sys.argv[1:4])
if persona is None and manifest is None and not (flag or {}).get("complete"):
    sys.exit(0)
name = (persona or {}).get("name") or ((manifest or {}).get("build") or {}).get("name") or ""
print("bot:" + re.sub(r"[^A-Za-z0-9 _.-]", "", str(name)).strip())
PY
)"
if [ -n "$OLD_NAME" ]; then
    OLD_NAME="${OLD_NAME#bot:}"
    OLD_LABEL="${OLD_NAME:-a bot}"
    START_NEW="$NEW_BOT"
    if [ "$START_NEW" = "0" ] && [ "$CAN_ASK" = "1" ]; then
        say ""
        say "  You already have $OLD_LABEL. Keep it, or start a new bot?"
        _i=0
        while [ $_i -lt 3 ]; do
            printf '  Type k to keep it, or n to start a new bot: '
            read -r _a || _a=""
            case "$_a" in
                [kK]|[kK][eE][eE][pP]) break ;;
                [nN]|[nN][eE][wW]) START_NEW=1; break ;;
            esac
            _i=$((_i + 1))
        done
    fi
    if [ "$START_NEW" = "0" ]; then
        say "  Keeping it."
        [ "$CAN_ASK" = "1" ] || say "  (To start a new bot instead, run setup again with --new-bot.)"
    else
        SLUG="$(printf '%s' "$OLD_NAME" | tr 'A-Z' 'a-z' | sed -e 's/[^a-z0-9]\{1,\}/-/g' -e 's/^-//' -e 's/-$//')"
        ASIDE_BASE="$(dirname "$STATE")/state-${SLUG:-bot}-$(date +%Y%m%d-%H%M)"
        ASIDE="$ASIDE_BASE"; _n=2
        while [ -e "$ASIDE" ]; do ASIDE="$ASIDE_BASE-$_n"; _n=$((_n + 1)); done
        # The bot's own files that live outside the state folder (the default
        # layout keeps them beside the code) go with it, into one subfolder.
        OUTSIDE=""
        case "$DATA_DIR/" in
            "$STATE/"*) ;;
            *) for rel in data config/build_manifest.json config/puffpai_config.json \
                          config/home_assistant_config.json config/media_server_config.json \
                          config/profiles.json; do
                   [ -e "$DATA_DIR/$rel" ] && OUTSIDE="$OUTSIDE $rel"
               done ;;
        esac
        if [ "$DRY_RUN" = "1" ]; then
            say "  [dry-run] would move $STATE aside to $ASIDE (nothing deleted)"
            for rel in $OUTSIDE; do say "  [dry-run] would move $DATA_DIR/$rel there too"; done
        else
            if [ -e "$STATE" ]; then
                mv "$STATE" "$ASIDE" || fail "could not move the old bot aside. If it is running, stop it first, then run setup again."
            else
                mkdir -p "$ASIDE"
            fi
            for rel in $OUTSIDE; do
                mkdir -p "$(dirname "$ASIDE/other-files/$rel")"
                mv "$DATA_DIR/$rel" "$ASIDE/other-files/$rel" || fail "could not move the old bot aside. If it is running, stop it first, then run setup again."
            done
            # The reading helper is not the bot's: put it back so it is not
            # downloaded again.
            if [ "$DATA_DIR" = "$STATE" ] && [ -d "$ASIDE/models/reading_helper" ]; then
                mkdir -p "$STATE/models" && cp -R "$ASIDE/models/reading_helper" "$STATE/models/" || true
            fi
            say "  Starting a new bot. The old one is kept, untouched, in: $ASIDE"
        fi
    fi
fi

# Is somebody else already living there? A manifest or a key file that this
# install did not write means two builds would share one identity.
SQUATTERS=""
for rel in api_key.txt config/build_manifest.json vault; do
    [ -e "$STATE/$rel" ] && SQUATTERS="$SQUATTERS $rel"
done
if [ -n "$SQUATTERS" ] && [ "$(env_value "$ENV_FILE" PUFFPAI_CHECKOUT)" != "$REPO_DIR" ]; then
    say ""
    say "  Careful: another copy of BuildBot already keeps its private files in that folder."
    say "  Sharing it would mix up the two bots. To keep this one separate, run setup"
    say "  again with:  bash install.sh --state-dir ./state"
    say ""
fi

# A key is minted ONCE per state dir. Re-running the installer must not rotate
# it: the dashboard, the pairing registry and the vault were all set up against
# the existing one.
KEY="$(env_value "$ENV_FILE" PUFFPAI_API_KEY)"
if [ "${#KEY}" -ge 32 ]; then
    say "  Reusing this copy's private key (kept in buildbot.env, not printed)."
elif [ "$DRY_RUN" = "1" ]; then
    KEY="<64 hex characters, minted when you run this for real>"
    say "  Made a private key so your bot and its web page know each other (saved in buildbot.env, not printed)."
else
    KEY="$("$PYTHON" -c 'import secrets; print(secrets.token_hex(32))')"
    say "  Made a private key so your bot and its web page know each other (saved in buildbot.env, not printed)."
fi

# write_secret <path> <<'body'
# Owner-only (0600): the file is created EMPTY, its mode set and CHECKED, and
# only then does the secret go in. A file that cannot be made owner-only stops
# the install. Under --dry-run it prints, with the key redacted.
write_secret() {
    _path="$1"; _body="$(cat)"
    if [ "$DRY_RUN" = "1" ]; then
        say "  [dry-run] would create $_path empty, set it to 0600 (owner-only)"
        say "  [dry-run]   and check that worked (stopping if not), then write:"
        printf '%s\n' "$_body" | sed -e 's/^\(PUFFPAI_API_KEY=\).*/\1<not printed>/' -e 's/^/      /'
        return 0
    fi
    mkdir -p "$(dirname "$_path")"
    ( umask 077; : > "$_path" ) || fail "could not create $_path."
    chmod 600 "$_path" || fail "could not make $_path private to your account. Nothing secret was written to it."
    _mode="$(stat -c '%a' "$_path" 2>/dev/null || stat -f '%Lp' "$_path" 2>/dev/null || echo unknown)"
    if [ "$_mode" != "600" ]; then
        fail "could not make $_path private to your account (it is $_mode, not 600). Nothing secret was written to it."
    fi
    printf '%s\n' "$_body" > "$_path"
}

write_launcher() {
    _path="$1"; _body="$(cat)"
    if [ "$DRY_RUN" = "1" ]; then
        say "  [dry-run] would write $_path:"
        printf '%s\n' "$_body" | sed 's/^/      /'
        return 0
    fi
    printf '%s\n' "$_body" > "$_path"
    chmod +x "$_path"
}

# --- The reading helper (83 MB) ---------------------------------------------
# Lets the bot match questions worded differently from its books. Fetched once
# from one fixed address, checked against its pinned SHA-256 while it downloads
# and again by puffpai.knowledge.reading_helper before anything is unpacked. If
# it cannot be fetched, setup carries on: books and uploads still answer
# questions that use their own words.
HELPER_URL="https://chroma-onnx-models.s3.amazonaws.com/all-MiniLM-L6-v2/onnx.tar.gz"
HELPER_SHA="913d7300ceae3b2dbc2c50d1de4baacab4be7b9380491c27fab7418616a16ec3"
say "  Getting the reading helper, 83 MB. It lets your bot match questions worded differently from its books."
if [ "$DRY_RUN" = "1" ]; then
    say "  [dry-run] would fetch $HELPER_URL and check it before using it"
elif PUFFPAI_DATA_DIR="$DATA_DIR" PUFFPAI_STATE_DIR="$STATE" \
        "$VENV_DIR/bin/python" -m puffpai knowledge helper >/dev/null 2>&1; then
    say "  It is already on this computer."
else
    HELPER_TMP="$(mktemp "${TMPDIR:-/tmp}/puffpai-helper.XXXXXX")"
    if "$VENV_DIR/bin/python" - "$HELPER_URL" "$HELPER_TMP" "$HELPER_SHA" <<'PY'
import hashlib, sys, urllib.request
url, out, want = sys.argv[1:4]
h = hashlib.sha256()
with urllib.request.urlopen(url, timeout=60) as r, open(out, "wb") as f:
    for block in iter(lambda: r.read(1 << 20), b""):
        h.update(block)
        f.write(block)
sys.exit(0 if h.hexdigest() == want else 3)
PY
    then
        PUFFPAI_DATA_DIR="$DATA_DIR" PUFFPAI_STATE_DIR="$STATE" \
            "$VENV_DIR/bin/python" -m puffpai knowledge helper from-file "$HELPER_TMP" || true
    else
        say "  Could not get it now. Your bot still answers from its books; run setup again later to add it."
    fi
    rm -f "$HELPER_TMP"
fi

# --- 4. Ollama -------------------------------------------------------------
step 4 "Ollama (it runs the AI model on this computer)"

OLLAMA_OK=0
if [ -n "$OLLAMA_BIN" ]; then
    say "  Found Ollama in BuildBot's tools folder. Your bot starts it when it starts,"
    say "  on this computer only."
    OLLAMA_OK=1
elif have ollama; then
    say "  Found Ollama."
    OLLAMA_OK=1
elif ollama_answers; then
    say "  Ollama is answering at $OLLAMA_URL."
    OLLAMA_OK=1
elif [ -n "$OLLAMA_APP" ]; then
    say "  Found the Ollama app. Open it once before you start your bot, so it is running."
    OLLAMA_OK=1
fi

if [ "$OLLAMA_OK" = "0" ]; then
    OLLAMA_LINE="curl -fsSL https://ollama.com/install.sh | sh"
    if [ "$IS_MAC" = "1" ] && [ "$DRY_RUN" = "1" ] && [ "$GO_OLLAMA" = "1" ]; then
        say "  [dry-run] Ollama would come from the download in step 1."
    elif [ "$IS_MAC" = "1" ]; then
        say "  Ollama is not on this computer. Your bot needs it to think."
        say "  Run this setup again and type y when it offers to download it,"
        say "  or get it free from https://ollama.com/download"
    elif [ "$DRY_RUN" = "1" ]; then
        say "  Ollama is not on this computer. Your bot needs it to think."
        say "  [dry-run] would ask: Install Ollama now? (y/n)"
        say "  [dry-run] on yes it would run: $OLLAMA_LINE"
    else
        say "  Ollama is not on this computer. Your bot needs it to think."
        # Linux: Ollama's own official script (it sets up the service and
        # the graphics card parts, so it asks for the password). The one
        # place this setup runs a downloaded script without a pinned hash.
        _go=0
        if [ "$YES_OLLAMA" = "1" ]; then
            _go=1
        elif [ "$CAN_ASK" = "1" ] && ask_yes_no "  Install Ollama now with its official script? It asks for your password."; then
            _go=1
        fi
        if [ "$_go" = "1" ] && command -v curl >/dev/null 2>&1; then
            say "  Installing Ollama."
            if sh -c "$OLLAMA_LINE"; then OLLAMA_OK=1; else say "  That did not finish."; fi
        else
            say "  To install it, type:  $OLLAMA_LINE"
            say "  Or get it from https://ollama.com/download"
        fi
    fi
fi

# --pull with BuildBot's own Ollama waits until the launchers exist (it has
# to be started first); see the end of step 6.
if [ -n "$PULL_TAG" ] && [ -z "$OLLAMA_BIN" ]; then
    say "  Downloading the AI model $PULL_TAG (you asked for it with --pull)."
    run ollama pull "$PULL_TAG"
fi

# --- 5. The web page (dashboard) --------------------------------------------
step 5 "Your bot's web page"

DASHBOARD_BUILT=0
DASH_DIR="$REPO_DIR/dashboard"
DASH_ENV="$REPO_DIR/dashboard/.env.local"
if [ "$BUILD_DASHBOARD" = "0" ]; then
    say "  Skipped (you asked for no web page). You can still set up and chat in a terminal."
else
    NODE_READY=1
    if node_ok; then
        say "  Found Node.js $(node --version)."
        # The absolute path of the node program, for security.host_programs:
        # outside parts are never started through a bare name.
        if [ "$DRY_RUN" = "1" ]; then
            say "  [dry-run] would write the node program's place to $REPO_DIR/node_path.txt"
        else
            "$PYTHON" -c 'import os,shutil,sys; p=shutil.which("node"); open(sys.argv[1],"w").write(os.path.realpath(p) if p else "")' "$REPO_DIR/node_path.txt"
        fi
    elif [ "$DRY_RUN" = "1" ] && [ "$GO_NODE" = "1" ]; then
        say "  [dry-run] would use the Node.js downloaded in step 1"
        say "  [dry-run] would write the node program's place to $REPO_DIR/node_path.txt"
    else
        NODE_READY=0
        say "  Node.js 22.12 or newer is not on this computer. BuildBot needs it."
        say "  Get it free from https://nodejs.org (or your package manager), then run this setup again."
        if [ "$PLATFORM" != "unsupported" ]; then
            say "  Or run this setup again and type y when it offers to download it."
        fi
    fi
    # No git needed: package-lock.json pins talkinghead to a GitHub commit, and
    # npm fetches that as a public https download (codeload.github.com), with
    # no git and no GitHub SSH key (checked with npm 11.9).

    # The two settings the proxy reads (app/api/pai/[...path]/route.ts). Written
    # before the build, and merged rather than overwritten: an existing
    # .env.local may hold MEDIA_SERVER_URL or a pairing setting that is none of
    # this script's business.
    KEEP=""
    if [ -r "$DASH_ENV" ] && [ "$DRY_RUN" != "1" ]; then
        KEEP="$(grep -v -E '^[[:space:]]*(PUFFPAI_API_KEY|PUFFPAI_BACKEND_URL|NEXT_TELEMETRY_DISABLED)[[:space:]]*=' "$DASH_ENV" \
                | grep -v -E '^# Written by install\.' || true)"
    fi
    write_secret "$DASH_ENV" <<EOF
# Written by install.sh - the dashboard's half of this install's pair.
# PUFFPAI_API_KEY here must equal the one run-backend.sh exports.
PUFFPAI_BACKEND_URL=$BACKEND_URL
PUFFPAI_API_KEY=$KEY
NEXT_TELEMETRY_DISABLED=1
$KEEP
EOF

    if [ "$NODE_READY" = "1" ]; then
        say "  Building the web page. This takes a few minutes."
        if ( cd "$DASH_DIR" && run_logged "$LOG_DIR/install-web-page.log" npm ci --no-audit --no-fund \
             && run_logged "$LOG_DIR/install-web-page-build.log" npm run build ); then
            if [ "$DRY_RUN" = "0" ]; then DASHBOARD_BUILT=1; say "  Done."; fi
        else
            say "  The web page was not built. You can still set up and chat in a terminal."
        fi
    else
        say "  The web page was not built. You can still set up and chat in a terminal,"
        say "  or install what is missing and run this setup again."
    fi
fi

# This install's environment. Written after the web-page step so that
# PUFFPAI_WITH_DASHBOARD records what actually happened: 1 only when the web
# page was built (the wizard's closing screen reads it). Under --dry-run it
# records what was asked for.
WITH_DASH_VALUE="$DASHBOARD_BUILT"
[ "$DRY_RUN" = "1" ] && WITH_DASH_VALUE="$BUILD_DASHBOARD"
write_secret "$ENV_FILE" <<EOF
# PuffPAI BuildBot - the environment THIS install runs with.
# Written by install.sh. Holds a secret: keep it mode 600.
# Delete this file to mint a new key; re-running the installer reuses it.
PUFFPAI_CHECKOUT=$REPO_DIR
PUFFPAI_API_KEY=$KEY
PUFFPAI_DATA_DIR=$DATA_DIR
PUFFPAI_STATE_DIR=$STATE
PUFFPAI_PORT=$PORT
PUFFPAI_DASHBOARD_PORT=$DASH_PORT
PUFFPAI_WITH_DASHBOARD=$WITH_DASH_VALUE
PUFFPAI_BACKEND_URL=$BACKEND_URL
NEXT_TELEMETRY_DISABLED=1
PUFFPAI_NODE_HOME=$NODE_FROM_TOOLS
PUFFPAI_OLLAMA_BIN=$OLLAMA_BIN
EOF

# --- 6. Launchers -----------------------------------------------------------
step 6 "Launchers"

# Every launcher starts by loading this install's env file. Install-time values
# expand here; a \$ is the launcher's own.
LOAD_ENV="ENV_FILE=\"$ENV_FILE\"
if [ ! -r \"\$ENV_FILE\" ]; then
    echo \"Your bot's settings file is missing: \$ENV_FILE\" >&2
    echo \"Run bash install.sh to set BuildBot up again.\" >&2
    exit 1
fi
while IFS= read -r line; do
    case \"\$line\" in ''|'#'*) continue ;; esac
    export \"\${line%%=*}=\${line#*=}\"
done < \"\$ENV_FILE\"
# The Node.js this setup downloaded, when it downloaded one.
if [ -n \"\${PUFFPAI_NODE_HOME:-}\" ]; then export PATH=\"\$PUFFPAI_NODE_HOME/bin:\$PATH\"; fi"

write_launcher "$REPO_DIR/run-setup.sh" <<EOF
#!/usr/bin/env bash
# Generated by install.sh. Re-run the installer to regenerate.
#
# Runs the wizard against THIS install's state dir. A bare "puffpai setup" also
# works - the installer leaves .puffpai-install.json beside this file and the CLI
# reads it - but this launcher is the one that cannot be got wrong.
set -euo pipefail
$LOAD_ENV
# BuildBot's own Ollama, when this setup downloaded it (nothing otherwise).
"$REPO_DIR/start-ollama.sh" || true
exec "$VENV_DIR/bin/puffpai" setup "\$@"
EOF

# start-ollama.sh: starts the Ollama program this setup downloaded (only that
# one, named in the env file) on this computer only, when nothing answers on
# 127.0.0.1:11434 yet. Its number goes to <state>/ollama.env for stop-bot.sh.
write_launcher "$REPO_DIR/start-ollama.sh" <<EOF
#!/usr/bin/env bash
# Generated by install.sh. Re-run the installer to regenerate.
#
# Starts the Ollama program BuildBot's setup downloaded, on 127.0.0.1 only,
# when nothing answers there yet. Does nothing when you use your own Ollama.
set -uo pipefail
$LOAD_ENV
BIN="\${PUFFPAI_OLLAMA_BIN:-}"
if [ -z "\$BIN" ] || [ ! -x "\$BIN" ]; then exit 0; fi
PY="$VENV_DIR/bin/python"
URL="http://127.0.0.1:11434/api/version"
answers() { "\$PY" -c 'import sys, urllib.request; urllib.request.urlopen(sys.argv[1], timeout=2)' "\$URL" >/dev/null 2>&1; }
if answers; then exit 0; fi
LOGS="\$PUFFPAI_STATE_DIR/logs"
mkdir -p "\$LOGS"
cd /
# Its own session, so closing this window does not stop it and stop-bot.sh
# can stop it whole. macOS has no setsid program; Python's os.setsid is the same.
if command -v setsid >/dev/null 2>&1; then
    OLLAMA_HOST=127.0.0.1:11434 setsid "\$BIN" serve >"\$LOGS/ollama.log" 2>&1 < /dev/null &
else
    OLLAMA_HOST=127.0.0.1:11434 nohup "\$PY" -I -c 'import os, sys
try:
    os.setsid()
except OSError:
    pass
os.execv(sys.argv[1], sys.argv[1:])' "\$BIN" serve >"\$LOGS/ollama.log" 2>&1 < /dev/null &
fi
PID=\$!
printf 'ollama_pid=%s\n' "\$PID" > "\$PUFFPAI_STATE_DIR/ollama.env"
printf '  Starting the program that runs the AI model (Ollama)...'
_n=0
while [ \$_n -lt 30 ]; do
    if answers; then echo " ready."; exit 0; fi
    kill -0 "\$PID" 2>/dev/null || break
    printf '.'; sleep 1; _n=\$((_n + 1))
done
echo " it did not start."
echo "  The last lines of \$LOGS/ollama.log:"
tail -n 15 "\$LOGS/ollama.log" 2>/dev/null | sed 's/^/    /'
exit 1
EOF

# The pointer itself. Written through Python so the path is JSON-escaped rather
# than pasted, and never chmod +x: it is a record, not a program.
if [ "$DRY_RUN" = "1" ]; then
    say "  [dry-run] would write $POINTER naming $ENV_FILE"
else
    "$PYTHON" -c 'import json,sys
with open(sys.argv[1], "w", encoding="utf-8") as fh:
    json.dump({"env_file": sys.argv[2]}, fh, indent=2)
    fh.write("\n")' "$POINTER" "$ENV_FILE"
fi

write_launcher "$REPO_DIR/run-backend.sh" <<EOF
#!/usr/bin/env bash
# Generated by install.sh. Re-run the installer to regenerate.
#
# Starts THIS checkout's backend with THIS install's key, state dir and port.
# Use it instead of a bare "puffpai serve": the key it exports wins over any
# stored one, which is what keeps the dashboard from getting a 401 after
# another install left an entry in the OS keyring.
set -euo pipefail
$LOAD_ENV
echo "Your bot is starting on http://127.0.0.1:\${PUFFPAI_PORT:-$PORT}"
exec "$VENV_DIR/bin/puffpai" serve --host 127.0.0.1 --port "\${PUFFPAI_PORT:-$PORT}"
EOF

write_launcher "$REPO_DIR/run-dashboard.sh" <<EOF
#!/usr/bin/env bash
# Generated by install.sh. Re-run the installer to regenerate.
#
# Starts the dashboard on this install's port, pointed at this install's
# backend, holding this install's key. package.json's "start" is pinned to
# 3003; this is the way to pick a port.
set -euo pipefail
$LOAD_ENV
# No usage reports to the web page's build tool maker (zero telemetry).
export NEXT_TELEMETRY_DISABLED=1
cd "$DASH_DIR"
# Same staleness guard "npm run start" gets from its prestart hook.
node scripts/ensure-fresh-build.js
echo "Your bot's web page is starting on http://127.0.0.1:\${PUFFPAI_DASHBOARD_PORT:-$DASH_PORT}"
exec npx next start -H 127.0.0.1 -p "\${PUFFPAI_DASHBOARD_PORT:-$DASH_PORT}"
EOF

# start-bot.sh: the backend and the dashboard together, each in its own
# session (setsid) so closing the terminal does not stop them; waits until both
# answer, prints the pairing code, opens the browser. PIDs go to
# <state>/running.env so stop-bot.sh stops exactly what this started.
write_launcher "$REPO_DIR/start-bot.sh" <<EOF
#!/usr/bin/env bash
# Generated by install.sh. Re-run the installer to regenerate.
#
# Starts your bot and its web page, waits until both answer, and opens the web
# page. PUFFPAI_NO_BROWSER=1 prints the address instead of opening it.
set -uo pipefail
$LOAD_ENV
REPO="$REPO_DIR"
DASH_DIR="$DASH_DIR"
PY="$VENV_DIR/bin/python"
PORT="\${PUFFPAI_PORT:-$PORT}"
DASH_PORT="\${PUFFPAI_DASHBOARD_PORT:-$DASH_PORT}"
LOGS="\$PUFFPAI_STATE_DIR/logs"
RUN_FILE="\$PUFFPAI_STATE_DIR/running.env"
URL="http://127.0.0.1:\$DASH_PORT/"
mkdir -p "\$LOGS"

answers() { "\$PY" -c 'import sys, urllib.request; urllib.request.urlopen(sys.argv[1], timeout=2)' "\$1" >/dev/null 2>&1; }
alive() { [ -n "\${1:-}" ] && kill -0 "\$1" 2>/dev/null; }
start_bg() {
    _log="\$1"; shift
    if command -v setsid >/dev/null 2>&1; then
        setsid "\$@" >"\$_log" 2>&1 < /dev/null &
    else
        # macOS has no setsid program: Python's os.setsid gives the same own
        # session, so closing the window does not stop it and stop-bot.sh
        # stops it with everything it started.
        nohup "\$PY" -I -c 'import os, sys
try:
    os.setsid()
except OSError:
    pass
os.execv(sys.argv[1], sys.argv[1:])' "\$(command -v "\$1")" "\${@:2}" >"\$_log" 2>&1 < /dev/null &
    fi
    echo \$!
}
wait_up() {
    _label="\$1"; _url="\$2"; _pid="\$3"; _secs="\$4"; _log="\$5"
    printf '  Starting %s...' "\$_label"
    _end=\$((SECONDS + _secs))
    while [ \$SECONDS -lt \$_end ]; do
        if answers "\$_url"; then echo " ready."; return 0; fi
        alive "\$_pid" || break
        printf '.'; sleep 2
    done
    echo " it did not start."
    echo "  The last lines of \$_log:"
    tail -n 15 "\$_log" 2>/dev/null | sed 's/^/    /'
    return 1
}

# Checked before anything starts, so nothing is left running when the web
# page cannot open.
# No usage reports to the web page's build tool maker (zero telemetry).
export NEXT_TELEMETRY_DISABLED=1
NEXT_BIN="\$DASH_DIR/node_modules/next/dist/bin/next"
if [ ! -f "\$DASH_DIR/.next/BUILD_ID" ] || [ ! -f "\$NEXT_BIN" ] || ! command -v node >/dev/null 2>&1; then
    echo "  Your bot's web page is not built on this computer, so it cannot open."
    echo "  You can set up your bot in this terminal instead: ./run-setup.sh"
    echo "  To get the web page, run bash install.sh again."
    exit 1
fi

PREV_BACKEND=""; PREV_DASH=""
if [ -r "\$RUN_FILE" ]; then
    PREV_BACKEND="\$(sed -n 's/^backend_pid=//p' "\$RUN_FILE")"
    PREV_DASH="\$(sed -n 's/^dashboard_pid=//p' "\$RUN_FILE")"
fi
BACKEND_PID=""; DASH_PID=""

echo ""
# BuildBot's own Ollama, when this setup downloaded it (nothing otherwise).
"\$REPO/start-ollama.sh" || echo "  Your bot starts anyway; it cannot answer until Ollama runs."
if alive "\$PREV_BACKEND" && answers "http://127.0.0.1:\$PORT/health"; then
    echo "  Your bot is already running."
    BACKEND_PID="\$PREV_BACKEND"
elif answers "http://127.0.0.1:\$PORT/health"; then
    echo "  Something else on this computer is already using port \$PORT."
    echo "  Stop that program, or run install.sh again with --port and another number."
    exit 1
else
    cd "\$REPO"
    BACKEND_PID="\$(start_bg "\$LOGS/bot.log" "\$PY" -m puffpai serve --host 127.0.0.1 --port "\$PORT")"
    printf 'backend_pid=%s\ndashboard_pid=\n' "\$BACKEND_PID" > "\$RUN_FILE"
    wait_up "your bot" "http://127.0.0.1:\$PORT/health" "\$BACKEND_PID" 180 "\$LOGS/bot.log" || exit 1
fi

if alive "\$PREV_DASH" && answers "\${URL}api/health"; then
    echo "  Your bot's web page is already running."
    DASH_PID="\$PREV_DASH"
elif answers "\${URL}api/health"; then
    echo "  Something else on this computer is already using port \$DASH_PORT."
    echo "  Stop that program, or run install.sh again with --dashboard-port and another number."
    exit 1
else
    echo "  Checking the web page is up to date (if it rebuilds, that takes a few minutes)."
    ( cd "\$DASH_DIR" && node scripts/ensure-fresh-build.js > "\$LOGS/web-page-check.log" 2>&1 )
    cd "\$DASH_DIR"
    DASH_PID="\$(start_bg "\$LOGS/web-page.log" node "\$NEXT_BIN" start -H 127.0.0.1 -p "\$DASH_PORT")"
    printf 'backend_pid=%s\ndashboard_pid=%s\n' "\$BACKEND_PID" "\$DASH_PID" > "\$RUN_FILE"
    wait_up "your bot's web page" "\${URL}api/health" "\$DASH_PID" 120 "\$LOGS/web-page.log" || exit 1
fi

# The pairing code: the web page only opens for devices that have typed it once.
# THIS bot's code: pairing_code finds its state folder from PUFFPAI_STATE_DIR
# (loaded above), the same way the web page does.
CODE="\$(cd "\$REPO" && "\$PY" -c "from puffpai import pairing_code as p; i = p.ensure_code(); print(p.format_code(i['code']) if i else '')" 2>/dev/null | tail -n 1)"

echo ""
echo "  Your bot is running on this computer."
echo "  Its web page: \$URL"
if [ -n "\$CODE" ]; then
    echo "  The first time, the page asks for a pairing code. Type: \$CODE"
    echo "  (The code works for one hour. After that, start again to get a new one.)"
fi
if [ "\${PUFFPAI_NO_BROWSER:-0}" = "1" ]; then
    echo "  Open this address in your web browser: \$URL"
elif command -v xdg-open >/dev/null 2>&1; then
    echo "  Opening your web browser now."
    xdg-open "\$URL" >/dev/null 2>&1 &
elif command -v open >/dev/null 2>&1; then
    echo "  Opening your web browser now."
    open "\$URL"
else
    echo "  Open this address in your web browser: \$URL"
fi
echo "  It keeps running after you close this terminal. To stop it: ./stop-bot.sh"
EOF

write_launcher "$REPO_DIR/stop-bot.sh" <<EOF
#!/usr/bin/env bash
# Generated by install.sh. Re-run the installer to regenerate.
#
# Stops exactly the processes start-bot.sh recorded in <state>/running.env, by
# PID (with their process group), and only while that PID still runs this
# install's bot or web page - a reused PID is never touched.
set -uo pipefail
$LOAD_ENV
RUN_FILE="\$PUFFPAI_STATE_DIR/running.env"
OLLAMA_FILE="\$PUFFPAI_STATE_DIR/ollama.env"
ANY=0
if [ -r "\$RUN_FILE" ]; then
    for pair in "web page:dashboard_pid:next" "bot:backend_pid:puffpai"; do
        label="\${pair%%:*}"; rest="\${pair#*:}"; key="\${rest%%:*}"; want="\${rest#*:}"
        pid="\$(sed -n "s/^\$key=//p" "\$RUN_FILE")"
        [ -n "\$pid" ] || continue
        if kill -0 "\$pid" 2>/dev/null && ps -o args= -p "\$pid" 2>/dev/null | grep -q "\$want"; then
            kill -- "-\$pid" 2>/dev/null || kill "\$pid" 2>/dev/null
            echo "  Stopped your \$label."
            ANY=1
        fi
    done
    rm -f "\$RUN_FILE"
fi
# The Ollama start-ollama.sh started (BuildBot's own copy), and only that one.
if [ -r "\$OLLAMA_FILE" ]; then
    pid="\$(sed -n 's/^ollama_pid=//p' "\$OLLAMA_FILE")"
    if [ -n "\$pid" ] && kill -0 "\$pid" 2>/dev/null && ps -o args= -p "\$pid" 2>/dev/null | grep -q "ollama serve"; then
        kill -- "-\$pid" 2>/dev/null || kill "\$pid" 2>/dev/null
        echo "  Stopped the program that runs the AI model (Ollama)."
        ANY=1
    fi
    rm -f "\$OLLAMA_FILE"
fi
[ "\$ANY" = "1" ] || echo "  Your bot was not running."
EOF

write_launcher "$REPO_DIR/puffpai" <<EOF
#!/usr/bin/env bash
# Generated by install.sh. The puffpai command for this copy of BuildBot, with
# its own settings loaded. From this folder: ./puffpai where
set -euo pipefail
$LOAD_ENV
exec "$VENV_DIR/bin/puffpai" "\$@"
EOF

say "  In $REPO_DIR:"
say "    ./start-bot.sh   starts your bot and opens its web page"
say "    ./stop-bot.sh    stops it"
say "    ./run-setup.sh   asks the setup questions in this terminal instead of the web page"
say "    ./puffpai        the puffpai command for this copy (try: ./puffpai where)"

# A Mac: the two files to double-click in Finder, beside the bash launchers,
# and on the Desktop when asked for.
if [ "$IS_MAC" = "1" ]; then
    tools_write_mac_launchers "$REPO_DIR" "$REPO_DIR" || say "  Could not write the files to double-click."
    if [ "$DESKTOP" = "0" ] && [ "$CAN_ASK" = "1" ] \
       && ask_yes_no "  Put Start BuildBot and Stop BuildBot on your Desktop too?"; then
        DESKTOP=1
    fi
    if [ "$DESKTOP" = "1" ] && [ -d "$HOME/Desktop" ]; then
        tools_write_mac_launchers "$REPO_DIR" "$HOME/Desktop" || say "  Could not put them on your Desktop."
    fi
    say "    Start BuildBot.command   double-click it in Finder to start your bot"
    say "    Stop BuildBot.command    double-click it to stop your bot"
fi

# --pull with BuildBot's own Ollama: start it, then download.
if [ -n "$PULL_TAG" ] && [ -n "$OLLAMA_BIN" ]; then
    say "  Downloading the AI model $PULL_TAG (you asked for it with --pull)."
    run "$REPO_DIR/start-ollama.sh" && run "$OLLAMA_BIN" pull "$PULL_TAG"
fi

# --- what to do next -------------------------------------------------------
say ""
say "BuildBot can set up a locked box for outside code later, when you pick something that needs it."
say ""
if [ "$OLLAMA_OK" = "0" ]; then
    say "Note: Ollama is not installed yet. Setup still works, but your bot cannot"
    say "answer until Ollama is on this computer (https://ollama.com/download)."
    say ""
fi

if [ "$IS_MAC" = "1" ]; then
    say "The first time you double-click Start BuildBot.command, your Mac may say it"
    say "cannot check who made it. If it does: right-click the file, pick Open, then"
    say "click Open. On macOS 15 or newer: open System Settings, then Privacy & Security,"
    say "and click Open Anyway near the bottom. You only do this once."
    say ""
fi

if [ "$DRY_RUN" = "1" ]; then
    say "Practice run finished. Nothing was changed."
    exit 0
fi

CHOICE=""
if [ "$CAN_ASK" = "1" ]; then
    say "Where do you want to set up your bot?"
    if [ "$DASHBOARD_BUILT" = "1" ]; then
        say "  1) In your web browser (opens now)"
        say "  2) Right here in this window"
        _i=0
        while [ $_i -lt 3 ] && [ "$CHOICE" != "1" ] && [ "$CHOICE" != "2" ]; do
            printf 'Type 1 or 2: '
            read -r CHOICE || CHOICE=""
            _i=$((_i + 1))
        done
    else
        say "  The web page is not built, so setup happens right here in this window."
        if ask_yes_no "Start the setup questions now?"; then CHOICE=2; fi
    fi
fi

case "$CHOICE" in
    1) exec "$REPO_DIR/start-bot.sh" ;;
    2) exec "$REPO_DIR/run-setup.sh" ;;
esac

# No answer (or nobody to ask): say how to do either later. Both lead to the
# same questions.
say "To set up your bot, either:"
[ "$DASHBOARD_BUILT" = "1" ] && say "  - in your web browser: ./start-bot.sh"
say "  - right here in a terminal: ./run-setup.sh"
say "  Both are in $REPO_DIR"
say "  (puffpai where shows which folders and ports this copy uses)"
exit 0
