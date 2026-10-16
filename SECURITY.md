# Security Policy

PuffPAI (BuildBot) is public alpha software. This policy describes what we
support, how to report a problem, and what we promise you can go verify
yourself rather than take on faith.

## Supported versions

Only the current `main` branch (public alpha) is supported. There are no
released version tags yet; run against the latest commit and report issues
against it.

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting for this repository
(the repo's **Security** tab → **Report a vulnerability**) rather than
opening a public issue. This channel is not enabled by default: **the repo
owner needs to turn it on in Settings → Security → "Private vulnerability
reporting"**; if you don't see the option, open a regular issue asking that
it be enabled, without details of the vulnerability itself.

We do not list a report-by-email address. If private reporting is not yet
available and the issue is sensitive, hold details until it is.

## Scope

In scope:
- The **Vault** (Zone 1): PIN/recovery-code handling, key derivation, and
  what's stored encrypted at rest (`src/puffpai/zone_vault.py`,
  `src/puffpai/security/vault.py`).
- **Egress and posture enforcement**: the build/run posture gates (`sealed`
  / `island` / `enumerated`), the sourcing SSRF hardening, and anything that
  lets a build reach a host it shouldn't (`src/puffpai/build_posture.py`,
  `src/puffpai/source_fetch.py`).
- **Pairing and consent**: device-approval and consent gates on
  state-changing actions (Drawbridge's veto path, `src/puffpai/api_server.py`
  pairing/consent handling).
- Anything that would let one profile/household read another's data, or let
  an unpaired party act on the system.

Out of scope: denial-of-service against your own local machine, and issues
that require an attacker to already have full local access to the box the
software runs on (local privilege on an already-compromised host is a
platform problem, not a PuffPAI one).

## Trust boundaries

Stated plainly so you can judge the design rather than infer it.

- **The trust boundary is "can reach the backend on loopback, with the API
  key".** Inside that boundary there is no further authentication of the
  caller. Any local process that can open a socket to the backend can assert
  `X-Puff-Device-Id` and be believed, because the backend believes that header
  from a trusted peer (loopback by default). That is how the dashboard proxy
  identifies a browser. What such a process cannot do is *land in a particular
  device's session*: the id is an HMAC-SHA256 of the device id under the pairing
  secret, truncated to 128 bits, so hitting a real vault-chat bucket means
  guessing 128 bits. An unrecognised id simply gets its own empty bucket.
- **No host packet filter ships with this software.** We do not install or
  manage nftables/iptables rules, and you should not assume one is present.
  Every gate described here is enforced in application code. If you want the
  backend port unreachable from the LAN, that is your firewall's job, not
  ours.
- **`PUFFPAI_TRUSTED_PROXY_IPS` must stay unset** (or loopback-only) unless you
  genuinely run the dashboard on a different host from the backend. Every
  address named in it may assert any device identity. There is deliberately no
  wildcard, and a garbage value trusts nobody rather than everybody.
- **`X-Forwarded-For` is never parsed.** This build ships no `ProxyFix` wrapper
  and no `trusted_proxy_hops` setting: the peer address the backend uses is the
  real socket peer, always. Keep it that way unless a reverse proxy you control
  is actually in front of the backend. A forwarded-header parser would let a LAN
  caller present itself as loopback, which crosses the trust boundary above and
  lets it assert device identities; if one is ever added, the hop count must
  default to `0`.
- **A caller the proxy cannot identify gets nothing, not someone else's
  session.** With `PUFF_PAIRING_ENFORCE=off` an unpaired browser reaches the
  keyed proxy, and a device revoked mid-request derives no identity either.
  Those requests are marked `X-Puff-Anonymous` by the proxy, keyed to a
  single-use session id, and refused vault answers with a "pair this device"
  message. Sharing one bucket keyed on the proxy's own loopback address would
  let one caller's 300-second vault unlock be inherited by the next
  unidentified caller.

## Response expectations

This is a small team building in the open. We aim to acknowledge a report
within a reasonable time and will say so plainly if a fix will take longer
than that. We are not promising an SLA we can't back.

## Standing security contracts (test these yourself)

These are promises about the software's behavior, stated so you can check
them rather than trust them:

- **Named crypto, not "military-grade".** The vault encrypts with Fernet
  (AES-128-CBC + HMAC-SHA256, authenticated) and derives each wrapping key
  with PBKDF2-HMAC-SHA256 at 600,000 iterations over its own random salt;
  the wording and the iteration count are the module's own
  (`src/puffpai/security/vault.py`, header and `_PBKDF2_ITERATIONS`). If you
  think those are the wrong primitives, that is a reviewable claim about a
  named algorithm, which is the point of naming it.
- **Zero telemetry.** Nothing phones home. Run the app with a local network
  monitor or a deny-by-default firewall and confirm no outbound traffic
  occurs beyond what you explicitly configured (Ollama, your Home Assistant
  instance, or a source you chose to fetch from).
- **Enumerated egress.** Every destination on the public internet a running
  build can reach is a named outside service in
  `src/puffpai/security/egress_doors.py`, and every one is switched off by
  default. One can only open on a build whose run posture is `enumerated` and
  whose switch you turned on by name; under `sealed` or `island` every one
  stays off whatever the config says, and a switched-off service refuses
  before a socket is opened. `GET /system/status` and the **What leaves your
  computer** page list every one with its state, including the ones that are
  off. They include the dashboard weather card (approximate coordinates to
  `api.open-meteo.com` and `air-quality-api.open-meteo.com`), the news widget
  (the feed URL you name), the book sources (Wikipedia, Project Gutenberg,
  OpenStax, Wikibooks, the Internet Archive), web search (Wikipedia, Brave,
  Marginalia), the Toolsmith's one-page read, finding your town's rough map
  position, GitHub when you connect it, and pictures a data source's own
  results point to. The wizard's online-sourcing step is gated separately and
  more strictly by `config/source_registry.json`.
- **The LAN half of that promise is checked too.** The addresses you configure
  for Ollama, Home Assistant, n8n, Kiwix, the media servers and the dashboard
  are not outside services, but they are checked against the same posture,
  because an `ollama.url` pointing at someone else's server is unrestricted
  egress no switch would see, and every prompt in the house goes to it. Under `island` and
  `enumerated` those URLs must be on your home network: a private address
  (RFC 1918), a link-local or unique-local address, or a `.local` / `.lan` /
  `.home.arpa` name **that actually resolves there**: a name is resolved and
  every address it answers with is checked, so a LAN-looking name pointed
  off-site is refused. Under `sealed` only this machine is allowed
  (`127.0.0.1` / `localhost`); **running Home Assistant, Ollama or a media
  server on another box, even one in your own house, requires the `island`
  posture, not `sealed`.** A URL that breaks the rule is refused when the
  wizard tries to write it, and if one reaches the config file anyway the
  service is switched off (and Ollama's address reset to loopback) at startup
  rather than being called. `GET /system/status` reports it under
  `posture_check.lan_services`, which is `"ok"` or the list of violations.
- **License-gated sourcing.** Nothing is fetched over the network by the
  online-sourcing step without a license shown first: you see the license,
  size, and URL before any byte moves (`src/puffpai/source_fetch.py`).

If any of the above fails to hold on your machine, that's a security bug.
Please report it through the channel above.

## Known risks

These are risks we chose to accept, written down so they are re-checked
before every release rather than forgotten.

- **A connected GitHub token can read whatever its own permissions allow**
  It is stored only in the vault (never a copy for while it is
  locked) and sent only in a header to api.github.com, but BuildBot cannot
  narrow a token GitHub issued; a token made with write permissions could
  also change your projects, and the status says so when GitHub reports it.

- **A data source's Yes also lets through pictures its own results point to,
  on sites not named before the Yes.** Picture GETs only (image
  types, never SVG, size-capped, no query of yours, no cookie or key), only to
  addresses that source's own answer named, only while its switch is on, one
  audit line per site; a site the source controls could still point at a
  tracking pixel, which then learns this computer's internet address.

- **The boundary: other programs already running as you.** BuildBot
  cannot fully protect a key from other programs already running with the
  your permissions on your computer. An approved command, or the shell on
  the Developer page, runs as you and could watch a program while it runs
  (its command line, its memory, its files). The key protections below stop
  accidental leaks and casual tricks; they do not stop a program that
  already has your full permissions. That is the agreed boundary.

- **A skill's own command can borrow its vault key for one checked request,
  sent only to the skill's own service.** Keys
  always live in the vault. Some skills say to run a command that names their
  key, for example `curl ... -H "Authorization: Bearer $BLUECOLUMN_API_KEY"`.
  A key reaches a command ONLY through such a checked request; a plain command
  in a skill's folder gets no key, not even the skill's own scripts. After the
  one Yes, the
  request runs only when it is one curl, wget or httpie command whose every
  address is the skill's own service: the homepage or API base the skill
  declares in its frontmatter, or a host in one of its own code examples next
  to the key. A prose link elsewhere does not count, and the SKILL.md is
  checked byte for byte against the copy the install pinned. On shared hosting
  such as `*.supabase.co` only the exact project host counts.

  The request is run directly, never through a shell: the program is
  `curl`/`wget`/`httpie` resolved to a fixed system path (never `PATH`, never
  the "Install what's missing" tools folder, never the skill folder, so a
  planted copy cannot run), and the key value is placed by us into the one
  argument it belongs in. The value never goes into a shell string, the chat,
  the Yes card or a log; its base64 (standard and url-safe, at any byte
  offset such as `Basic` auth, and wrapped at any width), url-encoded (once
  and twice), hex and JSON (`\/`, `\uXXXX`) forms are replaced by
  `[hidden key]` in the output, scrubbed before any trimming. One security log
  line per use names the skill, the key name and the host. The checker is
  `src/puffpai/security/skill_key_use.py`. What this does not protect against:
  - a malicious or compromised skill, or its service, gets the key by design;
  - the address or command checker can be fooled by forms we did not
    anticipate;
  - the service could send the key back in a form the scrubber does not know
    (split differently than we cover, reversed, encrypted or compressed);
  - an address on the same registrable domain that someone else controls (a
    shared host we do not list) could receive it;
  - while the request runs, the key is in `curl`'s own command line, so
    another process running as the same user could read it (such a process
    could already read the unlocked vault in this process's memory); passing
    it on standard input instead is a possible later hardening;
  - the install lock that pins the SKILL.md is a plain file, so an approved
    command could rewrite it together with the SKILL.md to add an address.

- **A skill's own command line program, installed by BuildBot, can get the
  skill's key for one run.** (For programs such as `runcomfy` that read their
  key from the environment themselves.) When
  "Install what's missing" installs the npm or pip package that provides a
  program the skill declares, BuildBot installs a second copy into a folder of
  that skill's own (`<state>/tools/keycli/<skill>/...`) and records, in the
  vault (encrypted and authenticated, so a rewritten record does not open), a
  manifest of EVERY file in that folder (relative path and sha256) plus which
  program maps to which entry, the package name and version. The folder holds
  the package and its dependencies, so the manifest covers the whole import
  root: the package's own files, its dependencies, any `__pycache__`, any
  top-level module. The record is keyed by skill and program together, so one
  skill's program can never take over another's. With the vault locked at
  install time nothing is recorded and the program never gets a key. At run
  time the command must be one plain call of a program the skill declares,
  with no shell features at all; anything else runs without a key. The whole
  folder is re-walked and must match the manifest exactly, checked again right
  before it starts, so a planted `__init__.py`, a `.pyc`, a top-level module
  or any changed dependency means no key and nothing runs. The program never
  runs from that folder: each recorded file is read once into memory, its
  hash checked on those bytes, and those same bytes written into a fresh run
  copy with a random name, which is what runs and is deleted afterwards. pip
  runs through BuildBot's own Python with `-I -S -B` (no `site` import, so no
  site-packages and no `.pth` file ever load) and `sys.path` set to the
  stdlib plus the run copy, appended last so a module in the copy cannot
  shadow the stdlib; a program that needs anything outside its own copy fails
  with a plain line. npm runs through the trusted `node`
  (`host_programs.path`) with the copied entry and no `NODE_PATH` or
  `NODE_OPTIONS`. A same-name program planted on `PATH`
  or in a BuildBot folder is never used. The environment is the small fixed
  set plus that skill's own declared keys, nothing else. The Yes card names
  the program, the package, the version and the skill. One security log line
  per use. Code: `src/puffpai/security/skill_programs.py` (records in
  `src/puffpai/security/keys.py`). What this does not protect against:
  - the program itself gets the key by design, and can send it anywhere;
  - a program installed while the vault was locked has no record and runs
    without the key until it is installed again with the vault open.
