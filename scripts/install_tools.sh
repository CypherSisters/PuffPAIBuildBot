# shellcheck shell=bash
# Pinned downloads for install.sh (Linux and macOS): Python, Node.js and, on a
# Mac, Ollama, for a computer that has none of them ("green").
#
# Sourced by install.sh; tests/test_install_tools_sh.py sources it on its own.
#
# The rules every download here follows:
#   * one fixed version, from the project's own release address, over https
#     only (curl --proto =https, also for redirects);
#   * a SHA-256 written in THIS file, checked before anything is unpacked. A
#     file that does not match is deleted and never run. The Ollama program on
#     a Mac must also carry Ollama's own Apple signature (team below);
#   * unpacked into a new, empty folder inside the tools folder, never with
#     sudo, never into a system folder;
#   * the tools folder is OUTSIDE BuildBot's install, private-files and apps
#     folders, because security.host_programs refuses a node program inside
#     those (a folder BuildBot itself writes to could swap it);
#   * on a Mac, the "downloaded from the internet" mark (com.apple.quarantine)
#     is removed only from the folders unpacked here, after the checks pass.
#
# Where the pins came from: python-build-standalone release
# 20261003 (the SHA256SUMS file in that release, which matches GitHub's own
# digest for each file); Node.js v24.21.0 SHASUMS256.txt (its signature checked
# against the Node.js release keys); Ollama v0.40.2 sha256sum.txt (matches
# GitHub's digest; the program inside is signed by team 3MU9H2V9Y9).
# To move a pin: change the version and all of its hashes together.
#
# ASCII only, plain words in everything printed (tests check both).

TOOLS_PY_VERSION="3.12.15"
TOOLS_PY_BUILD="20261003"
TOOLS_NODE_VERSION="24.21.0"
TOOLS_OLLAMA_VERSION="0.40.2"
TOOLS_OLLAMA_TEAM="3MU9H2V9Y9"

# install.sh defines these; the tests do not. Only a shell FUNCTION named
# say counts: every Mac has a /usr/bin/say program that reads text ALOUD,
# so "is there a say?" would answer yes and nothing would be printed.
if [ "$(type -t say 2>/dev/null)" != "function" ]; then
    say() { printf '%s\n' "$*"; }
fi
: "${DRY_RUN:=0}"
: "${PY_MIN_MAJOR:=3}"
: "${PY_MIN_MINOR:=11}"

# tools_platform: darwin-arm64, darwin-x64, linux-x64, linux-arm64 or
# unsupported. PUFFPAI_TOOLS_PLATFORM overrides it (tests only).
tools_platform() {
    if [ -n "${PUFFPAI_TOOLS_PLATFORM:-}" ]; then
        printf '%s\n' "$PUFFPAI_TOOLS_PLATFORM"
        return 0
    fi
    _tp_os="$(uname -s 2>/dev/null || echo unknown)"
    _tp_m="$(uname -m 2>/dev/null || echo unknown)"
    case "$_tp_os" in
        Darwin)
            # A Terminal running under Rosetta on Apple silicon says x86_64;
            # the arm64 build is still the right one for that Mac.
            if [ "$(sysctl -n hw.optional.arm64 2>/dev/null)" = "1" ]; then
                _tp_m="arm64"
            fi
            case "$_tp_m" in
                arm64|aarch64) echo "darwin-arm64" ;;
                x86_64)        echo "darwin-x64" ;;
                *)             echo "unsupported" ;;
            esac ;;
        Linux)
            # The pinned builds need glibc; Alpine and other musl systems
            # get the links instead.
            if ls /lib/ld-musl-* >/dev/null 2>&1; then
                echo "unsupported"
                return 0
            fi
            case "$_tp_m" in
                x86_64|amd64)  echo "linux-x64" ;;
                aarch64|arm64) echo "linux-arm64" ;;
                *)             echo "unsupported" ;;
            esac ;;
        *) echo "unsupported" ;;
    esac
}

# tools_pin <python|node|ollama> <platform>: prints "URL SHA256", or fails
# when there is no pinned build for that pair.
tools_pin() {
    _pn_sha=""; _pn_triple=""
    case "$1:$2" in
        python:darwin-arm64) _pn_triple="aarch64-apple-darwin"
            _pn_sha="316a463172740e71d8dca1f2730784e325f3f720941137b5d674d5801a632213" ;;
        python:darwin-x64)   _pn_triple="x86_64-apple-darwin"
            _pn_sha="a8fd7a91852f19b6d959793ef41fad048631ccb2a334a9ecdf573255298f7978" ;;
        python:linux-x64)    _pn_triple="x86_64-unknown-linux-gnu"
            _pn_sha="f937814031eab4698ca6d07ec606ede1825768f3f3e99af76d9db3900bee03c5" ;;
        python:linux-arm64)  _pn_triple="aarch64-unknown-linux-gnu"
            _pn_sha="95c01982c9fcb9d95b0acfdb5eb8a6e0099dd11edf062314a474228d93f2b765" ;;
        node:darwin-arm64)
            _pn_sha="bed7eea5325e1108f32ce5228ddd6a5f0f08a499ee42aa7442aea583702f6057" ;;
        node:darwin-x64)
            _pn_sha="1462cb3b3046b815cf8ea436d3da450ec1a9f11dac7e5a46b0ada5305d7e8097" ;;
        node:linux-x64)
            _pn_sha="6e1db87ef58b8819e5d5402eff1536491b18edd8eb7bee5ef7897876e88dc5ff" ;;
        node:linux-arm64)
            _pn_sha="724282c3b43aec998aa9527380465b45d229e021b58035f5f4f63095eabfe5d5" ;;
        ollama:darwin-arm64|ollama:darwin-x64)
            # One file for both kinds of Mac.
            _pn_sha="e888b7637291ceb80b622c00ea067f62c86d9c50d419b3eb903032e4f1f8a4f6" ;;
        *) return 1 ;;
    esac
    case "$1" in
        python) printf '%s %s\n' "https://github.com/astral-sh/python-build-standalone/releases/download/${TOOLS_PY_BUILD}/cpython-${TOOLS_PY_VERSION}%2B${TOOLS_PY_BUILD}-${_pn_triple}-install_only.tar.gz" "$_pn_sha" ;;
        node)   printf '%s %s\n' "https://nodejs.org/dist/v${TOOLS_NODE_VERSION}/node-v${TOOLS_NODE_VERSION}-$2.tar.gz" "$_pn_sha" ;;
        ollama) printf '%s %s\n' "https://github.com/ollama/ollama/releases/download/v${TOOLS_OLLAMA_VERSION}/ollama-darwin.tgz" "$_pn_sha" ;;
    esac
}

# Where the downloaded programs live: PUFFPAI_TOOLS_DIR, else a folder beside
# (never inside) the per-user private-files and apps folders.
tools_default_dir() {
    if [ -n "${PUFFPAI_TOOLS_DIR:-}" ]; then
        printf '%s\n' "$PUFFPAI_TOOLS_DIR"
    elif [ "$(uname -s 2>/dev/null)" = "Darwin" ]; then
        printf '%s\n' "$HOME/Library/Application Support/PuffPAI-tools"
    else
        printf '%s\n' "${XDG_DATA_HOME:-$HOME/.local/share}/puffpai-tools"
    fi
}

# tools_inside <path> <root>: true when path is root or under it.
tools_inside() {
    case "${1%/}/" in
        "${2%/}/"*) return 0 ;;
    esac
    return 1
}

# tools_dir_ok <tools> <root>...: the tools folder must be a full path and must
# not sit inside any of the given folders (install, private files, apps).
tools_dir_ok() {
    _td="$1"; shift
    case "$_td" in /*) ;; *) return 1 ;; esac
    case "$_td" in *'/../'*|*'/..'|*'/./'*) return 1 ;; esac
    for _root in "$@"; do
        [ -n "$_root" ] || continue
        if tools_inside "$_td" "$_root"; then return 1; fi
    done
    return 0
}

# tools_sha256 <file>: the file's SHA-256 in lowercase hex.
tools_sha256() {
    if command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$1" | awk '{print tolower($1)}'
    elif command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | awk '{print tolower($1)}' | sed 's/^\\//'
    else
        return 1
    fi
}

# tools_verify <file> <expected sha256>: true only on an exact match with a
# well-formed expected value. No checker on the computer means no match.
tools_verify() {
    case "$2" in ''|*[!0-9a-f]*) return 1 ;; esac
    [ "${#2}" -eq 64 ] || return 1
    [ -f "$1" ] || return 1
    _tv_got="$(tools_sha256 "$1" 2>/dev/null)" || return 1
    [ "$_tv_got" = "$2" ]
}

# tools_fetch <url> <out>: https only, and only from the three release sites.
tools_fetch() {
    case "$1" in
        https://github.com/astral-sh/python-build-standalone/releases/download/*) ;;
        https://github.com/ollama/ollama/releases/download/*) ;;
        https://nodejs.org/dist/*) ;;
        *) return 1 ;;
    esac
    if command -v curl >/dev/null 2>&1; then
        curl --proto '=https' --proto-redir '=https' --tlsv1.2 -fsSL --retry 3 \
             --connect-timeout 30 --max-filesize 400000000 -o "$2" "$1"
    elif command -v wget >/dev/null 2>&1; then
        wget --https-only -q -O "$2" "$1"
    else
        return 2
    fi
}

# tools_fetch_verified <url> <sha256> <out>: download, check, keep. On any
# failure the partial file is deleted and it says why.
tools_fetch_verified() {
    rm -f "$3.part"
    if ! tools_fetch "$1" "$3.part"; then
        rm -f "$3.part"
        say "  The download did not work. Check this computer is online, then run setup again."
        return 1
    fi
    if ! tools_verify "$3.part" "$2"; then
        rm -f "$3.part"
        say "  The download did not match the copy BuildBot expects, so it was thrown away and nothing was run."
        return 1
    fi
    mv "$3.part" "$3"
}

# tools_names_safe <archive>: every name inside is relative and never climbs
# out with "..". The hash check is what makes the file trusted; this is a
# second guard so even a bad pin cannot write outside its folder.
tools_names_safe() {
    _tn_list="$(tar -tzf "$1" 2>/dev/null)" || return 1
    [ -n "$_tn_list" ] || return 1
    if printf '%s\n' "$_tn_list" | grep -E '^/|(^|/)\.\.(/|$)' >/dev/null; then
        return 1
    fi
    return 0
}

# tools_unpack <archive> <new folder>: into a folder that must not exist yet.
tools_unpack() {
    [ ! -e "$2" ] || return 1
    tools_names_safe "$1" || return 1
    mkdir -p "$2" || return 1
    tar -xzf "$1" -C "$2" 2>/dev/null
}

# On a Mac only, and only on a folder this file just unpacked and checked.
tools_clear_quarantine() {
    [ "$(uname -s 2>/dev/null)" = "Darwin" ] || return 0
    xattr -dr com.apple.quarantine "$1" 2>/dev/null || true
}

# tools_remove_own <tools> <path>: delete a folder this file made, and refuse
# anything that is not strictly inside the tools folder.
tools_remove_own() {
    [ -n "$1" ] && [ -n "$2" ] || return 1
    case "$2" in "${1%/}/"?*) ;; *) return 1 ;; esac
    case "$2" in *'/../'*|*'/..') return 1 ;; esac
    rm -rf "$2"
}

# tools_python_ok <program>: Python new enough, with venv and ssl (pip needs both).
tools_python_ok() {
    "$1" -c "import sys, venv, ssl; raise SystemExit(0 if sys.version_info[:2] >= ($PY_MIN_MAJOR, $PY_MIN_MINOR) else 1)" >/dev/null 2>&1
}

# tools_skip_python <name>: true when trying this Python would pop up a window
# instead of answering. A Mac without Apple's command line tools has a
# stand-in /usr/bin/python3 that opens an "install the tools" box when run
# (and the Python it installs is 3.9, too old anyway).
tools_skip_python() {
    [ "$(uname -s 2>/dev/null)" = "Darwin" ] || return 1
    _sp="$(command -v "$1" 2>/dev/null)"
    case "$_sp" in /usr/bin/python3|/usr/bin/python) ;; *) return 1 ;; esac
    if xcode-select -p >/dev/null 2>&1; then return 1; fi
    return 0
}

# tools_new_stage <tools>: a fresh, empty working folder inside the tools folder.
tools_new_stage() {
    mkdir -p "$1" || return 1
    _ts="$1/.download-$$-$RANDOM"
    tools_remove_own "$1" "$_ts" || return 1
    mkdir -p "$_ts" || return 1
    printf '%s\n' "$_ts"
}

# tools_install_python <tools> <platform>: sets TOOLS_PYTHON.
tools_install_python() {
    _ip_home="$1/python-${TOOLS_PY_VERSION}-${TOOLS_PY_BUILD}"
    TOOLS_PYTHON="$_ip_home/bin/python3"
    if [ -x "$TOOLS_PYTHON" ] && tools_python_ok "$TOOLS_PYTHON"; then
        say "  Python $TOOLS_PY_VERSION is already in $1."
        return 0
    fi
    _ip_pin="$(tools_pin python "$2")" || return 1
    _ip_url="${_ip_pin%% *}"; _ip_sha="${_ip_pin##* }"
    if [ "$DRY_RUN" = "1" ]; then
        say "  [dry-run] would download Python $TOOLS_PY_VERSION from $_ip_url"
        say "  [dry-run] check it is exactly $_ip_sha, and unpack it to $_ip_home"
        return 0
    fi
    say "  Downloading Python $TOOLS_PY_VERSION (about 25 MB)."
    _ip_stage="$(tools_new_stage "$1")" || return 1
    if tools_fetch_verified "$_ip_url" "$_ip_sha" "$_ip_stage/python.tar.gz" \
       && tools_unpack "$_ip_stage/python.tar.gz" "$_ip_stage/x" \
       && [ -x "$_ip_stage/x/python/bin/python3" ]; then
        if [ -e "$_ip_home" ]; then tools_remove_own "$1" "$_ip_home"; fi
        mv "$_ip_stage/x/python" "$_ip_home"
    fi
    tools_remove_own "$1" "$_ip_stage"
    [ -x "$TOOLS_PYTHON" ] || return 1
    tools_clear_quarantine "$_ip_home"
    tools_python_ok "$TOOLS_PYTHON"
}

# tools_install_node <tools> <platform>: sets TOOLS_NODE_HOME.
tools_install_node() {
    _in_name="node-v${TOOLS_NODE_VERSION}-$2"
    TOOLS_NODE_HOME="$1/$_in_name"
    if [ -x "$TOOLS_NODE_HOME/bin/node" ]; then
        say "  Node.js $TOOLS_NODE_VERSION is already in $1."
        return 0
    fi
    _in_pin="$(tools_pin node "$2")" || return 1
    _in_url="${_in_pin%% *}"; _in_sha="${_in_pin##* }"
    if [ "$DRY_RUN" = "1" ]; then
        say "  [dry-run] would download Node.js $TOOLS_NODE_VERSION from $_in_url"
        say "  [dry-run] check it is exactly $_in_sha, and unpack it to $TOOLS_NODE_HOME"
        return 0
    fi
    say "  Downloading Node.js $TOOLS_NODE_VERSION (about 50 MB)."
    _in_stage="$(tools_new_stage "$1")" || return 1
    if tools_fetch_verified "$_in_url" "$_in_sha" "$_in_stage/node.tar.gz" \
       && tools_unpack "$_in_stage/node.tar.gz" "$_in_stage/x" \
       && [ -x "$_in_stage/x/$_in_name/bin/node" ]; then
        if [ -e "$TOOLS_NODE_HOME" ]; then tools_remove_own "$1" "$TOOLS_NODE_HOME"; fi
        mv "$_in_stage/x/$_in_name" "$TOOLS_NODE_HOME"
    fi
    tools_remove_own "$1" "$_in_stage"
    [ -x "$TOOLS_NODE_HOME/bin/node" ] || return 1
    tools_clear_quarantine "$TOOLS_NODE_HOME"
    "$TOOLS_NODE_HOME/bin/node" --version >/dev/null 2>&1
}

# tools_signed_by_ollama <program>: Ollama's own Apple signature, by team.
# Macs only (only a Mac can check it); elsewhere the hash check stands alone.
tools_signed_by_ollama() {
    [ "$(uname -s 2>/dev/null)" = "Darwin" ] || return 0
    [ -x /usr/bin/codesign ] || return 1
    /usr/bin/codesign --verify --strict \
        -R="anchor apple generic and certificate leaf[subject.OU] = \"$TOOLS_OLLAMA_TEAM\"" \
        "$1" >/dev/null 2>&1
}

# tools_install_ollama <tools> <platform>: Macs only. Sets TOOLS_OLLAMA. The
# plain program (no app, no menu bar icon, no password); the launchers start
# it on this computer only (127.0.0.1) when nothing else answers there.
tools_install_ollama() {
    case "$2" in darwin-*) ;; *) return 1 ;; esac
    _io_home="$1/ollama-${TOOLS_OLLAMA_VERSION}"
    TOOLS_OLLAMA="$_io_home/ollama"
    if [ -x "$TOOLS_OLLAMA" ]; then
        say "  Ollama $TOOLS_OLLAMA_VERSION is already in $1."
        return 0
    fi
    _io_pin="$(tools_pin ollama "$2")" || return 1
    _io_url="${_io_pin%% *}"; _io_sha="${_io_pin##* }"
    if [ "$DRY_RUN" = "1" ]; then
        say "  [dry-run] would download Ollama $TOOLS_OLLAMA_VERSION from $_io_url"
        say "  [dry-run] check it is exactly $_io_sha and signed by Ollama, and unpack it to $_io_home"
        return 0
    fi
    say "  Downloading Ollama $TOOLS_OLLAMA_VERSION (about 170 MB)."
    _io_stage="$(tools_new_stage "$1")" || return 1
    _io_ok=0
    if tools_fetch_verified "$_io_url" "$_io_sha" "$_io_stage/ollama.tgz" \
       && tools_unpack "$_io_stage/ollama.tgz" "$_io_stage/x" \
       && [ -x "$_io_stage/x/ollama" ]; then
        if tools_signed_by_ollama "$_io_stage/x/ollama"; then
            if [ -e "$_io_home" ]; then tools_remove_own "$1" "$_io_home"; fi
            mv "$_io_stage/x" "$_io_home" && _io_ok=1
        else
            say "  The Ollama program is not signed by Ollama, so it was thrown away and nothing was run."
        fi
    fi
    tools_remove_own "$1" "$_io_stage"
    [ "$_io_ok" = "1" ] || return 1
    tools_clear_quarantine "$_io_home"
    return 0
}

# tools_write_mac_launchers <install folder> <into folder>: the two files an
# owner double-clicks in Finder. They hold only a "cd" to the install folder
# and the bash launcher, so there is nothing in them to keep up to date.
tools_write_mac_launchers() {
    printf -v _wl_repo '%q' "$1"
    for _wl_pair in "Start BuildBot.command:start-bot.sh:start" \
                    "Stop BuildBot.command:stop-bot.sh:stop"; do
        _wl_file="$2/${_wl_pair%%:*}"
        _wl_rest="${_wl_pair#*:}"
        _wl_target="${_wl_rest%%:*}"
        _wl_verb="${_wl_rest#*:}"
        if [ "$DRY_RUN" = "1" ]; then
            say "  [dry-run] would write $_wl_file (double-click it to $_wl_verb your bot)"
            continue
        fi
        cat > "$_wl_file" <<EOF
#!/bin/bash
# Made by install.sh: double-click this in Finder to $_wl_verb your bot.
# Run the setup again to make it again.
cd $_wl_repo || { echo "BuildBot's folder has moved. Run its setup again."; exit 1; }
./$_wl_target
echo ""
echo "You can close this window."
EOF
        chmod +x "$_wl_file" || return 1
    done
    return 0
}
