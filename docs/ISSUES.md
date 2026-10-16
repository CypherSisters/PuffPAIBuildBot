# Reporting a problem or asking for something

BuildBot's issue tracker is on GitHub:
**[github.com/CypherSisters/PuffPAIBuildBot/issues](https://github.com/CypherSisters/PuffPAIBuildBot/issues)**.

Use it to report a bug, ask for a feature, or ask for help building a bot for
something. You need a free GitHub account to open an issue. Before you open
one, search the open and closed issues; someone may have asked already. Many
common questions are answered in the [FAQ](FAQ.md).

**Security problem?** Do not open a public issue. Use GitHub's private
vulnerability reporting
([report one here](https://github.com/CypherSisters/PuffPAIBuildBot/security/advisories/new)).
See [SECURITY.md](../SECURITY.md).

## Pick the right form

When you press **New issue** GitHub offers these forms (they live in
`.github/ISSUE_TEMPLATE/`):

| Form | Use it when |
|------|-------------|
| **Bug report** | Something broke, or did something you did not expect. |
| **Feature request** | You want BuildBot to do something it does not do yet. |
| **Help me build a bot for...** | You have a goal and the wizard did not get you there, or you are not sure how to start. |
| **Wizard gap** | The setup wizard asked the wrong thing, or skipped a step it should have asked. |

A question that fits none of these can be a blank issue.

## What makes a good report

1. **What you did**, step by step, in plain words.
2. **What you expected**, and **what happened instead**. Copy the exact words
   on the screen if you can.
3. **Your computer**: Windows, Mac or Linux and its version, how much memory,
   which BuildBot you have (`puffpai version`, or the commit you installed
   from: `git rev-parse HEAD`), and whether Toolsmith uses this computer or
   Anthropic (Settings, Toolsmith's model).
4. **Your goal**, if it was a build: the words you typed into the wizard.
   Leave out anything private.
5. **The log lines around the problem** (see below), with private things
   taken out.

## Where the logs are

Run `puffpai where`. It prints every folder this copy of BuildBot uses
(and never prints a key). The logs are in the **state folder**, in `logs`:

| Computer | Logs folder (unless you moved the state folder) |
|----------|----------------------------------------------|
| Windows | `%LOCALAPPDATA%\PuffPAI\logs` (for example `C:\Users\you\AppData\Local\PuffPAI\logs`) |
| Mac | `~/Library/Application Support/PuffPAI/logs` |
| Linux | `~/.local/state/puffpai/logs` (or `$XDG_STATE_HOME/puffpai/logs`) |

If you set `PUFFPAI_STATE_DIR`, the logs are in that folder's `logs`
instead. The files that help most:

- `bot.log`: the bot itself (written when you start it with `puffpai start`
  or the Start file). Every command Toolsmith runs while it installs an app,
  and the last lines of its output, are here too.
- `web-page.log`: the web page.
- `outside.jsonl`: one line per request to an outside service, allowed or
  refused. It never holds query strings or keys. Attach it when the problem
  is about something online.

Two more places help when an app would not install or start:

- **An app Toolsmith ran:** its own log folder sits next to the app's folder
  in `host_apps` in the state folder (named like the app's folder, ending in
  `-logs`). For a folder of your own code, the logs are in the `.buildbot`
  folder inside it.
- **The installer (Windows):** `install-log.txt` in BuildBot's own folder
  keeps everything the setup window showed.

Copy only the lines around the problem, not the whole file.

## Take private things out first

Logs can hold words from your chats, file names, your bot's name, your town,
and addresses on your home network. Before you paste anything:

- **Never attach the vault** (`vault.enc`, in the `vault_data` folder or
  wherever you moved it) or anything from inside it. Nobody needs it to fix a
  bug, and nobody should ask for it.
- **Never paste a key or token**: API keys, the bot's own key
  (`puffpai apikey` prints it, so do not run that for a report), a Home
  Assistant token, a GitHub token, your Anthropic key if you chose it for
  Toolsmith. If one slipped in, replace it with `REDACTED`.
- **Command output is not cleaned for you.** `bot.log` and an app's own logs
  hold what Toolsmith's commands printed, which can include settings from the
  app's `.env` file and the paths of your folders. Read the lines before you
  paste them.
- **Never attach `last_system_prompt.txt`** if you turned on
  `PUFFPAI_DEBUG_PROMPT`: it is your bot's whole prompt, with what it knows
  about you.
- **Never attach `security_logs/audit.jsonl` whole** (or the other files in
  `security_logs`). If it matters, copy the few lines you need.
- **Replace** your name, your town, email addresses, and home network
  addresses (like `192.168.1.20`) with something like `<my-town>` or
  `<home-ip>`. Your account name shows up in folder paths (in
  `install-log.txt` too): replace it with `you`. `127.0.0.1` and `localhost`
  are fine to leave in.
- **Pairing codes** stop working after an hour, but take them out anyway.
- **Screenshots**: crop out chats, names and anything private.

When in doubt, leave it out. We can ask for more.

## Asking for a feature

Say what you want to do, not only how. "I want my phone to send a photo to my
bot from the share menu" helps more than "add a share target". If you know a
ready-made app or project on GitHub that already does it, link it: BuildBot's
whole idea is to use what people already made.

Things people often ask for that are not built yet are listed as "not yet" in
the [FAQ](FAQ.md). Asking for them on the tracker is how they get built.

## Want to fix it yourself?

Read [CONTRIBUTING.md](../CONTRIBUTING.md) for setup and tests, and
[AGENTS.md](../AGENTS.md) and [ARCHITECTURE.md](ARCHITECTURE.md) for how the
code fits together. Open a pull request against `main`.
