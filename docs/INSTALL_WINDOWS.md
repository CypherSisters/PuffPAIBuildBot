# Install on Windows

Tested on Windows 11 x64. Windows 10 should work the same way.

## Without typing anything

1. On the BuildBot GitHub page, click the green **Code** button, then
   **Download ZIP**. Open your Downloads folder, right-click the ZIP and pick
   **Extract All**.
2. Open the folder you extracted and double-click **install.cmd**. If Windows
   says "Windows protected your PC", click **More info**, then **Run anyway**.
3. A window opens and checks for three free tools: Python, Ollama (it runs the
   AI model) and Node.js (needed for the web page). If one is missing, it
   asks before installing it, using Windows' own app installer (winget). Say no
   and it tells you where to get it instead.
4. It builds your bot's web page. That takes a few minutes.
5. At the end it asks: "Where do you want to set up your bot? 1) In your web
   browser (opens now) 2) Right here in this window". Both ask the same questions.

The web page asks for a pairing code the first time you open it. The window that
started your bot shows it (eight letters and numbers, good for one hour).

## Every day after that

In the BuildBot folder:

| Double-click | What it does |
|---|---|
| **Start BuildBot.cmd** | starts your bot and its web page, waits until both answer, opens your browser |
| **Stop BuildBot.cmd** | stops both |
| **Set up BuildBot.cmd** | asks the setup questions in a window instead of the web page |

Once your bot has a name, Start also makes **Start <name>.cmd** and
**Stop <name>.cmd**, which do the same thing. Your bot keeps running after you
close the Start window. What it wrote while running is in `logs\` inside its
private files folder (`%LOCALAPPDATA%\PuffPAI` unless you chose another).

## From a Command Prompt

```bat
install.cmd -Help
install.cmd -Port 8100 -DashboardPort 3100 -StateDir .\state -NoQuestions
puffpai.cmd where
```

`install.cmd` passes everything to `install.ps1` with `-ExecutionPolicy Bypass`
for that one process; your PC's script setting is not changed. Options: `-DryRun`
(show every step and file, change nothing), `-NoQuestions`, `-NoDashboard`,
`-WithVoice`, `-WithDev`, `-Port N` and `-DashboardPort N` (default 8000 and
3003), `-StateDir PATH`, `-YesInstallOllama`, `-Pull NAME`. `-WithDashboard` is
still accepted; the web page is built unless `-NoDashboard` is given.

The installer mints this install's own 64-hex key into `<state>\buildbot.env`
(owner-only, never printed) and copies it into `dashboard\.env.local`, so the bot
and its web page always hold the same key; an explicitly set `PUFFPAI_API_KEY`
beats a stale Credential Manager entry from another install. Every launcher
loads that file first. What an owner double-clicks is always a `.cmd` file: a
stock Windows client blocks `.ps1` files, so Start and Stop hand over to
`run-start.ps1` / `run-stop.ps1` with Bypass, and `run-setup.ps1`,
`run-backend.ps1`, `run-dashboard.ps1` stay for people who run them by hand.
Start records the two PIDs in `<state>\running.json`; Stop stops those and
nothing else. `.puffpai-install.json` beside the scripts makes a bare `puffpai`
command use this install's state dir; `puffpai.cmd` is `venv\Scripts\puffpai.exe`
with the env file loaded.

## Ollama

If you said no to Ollama: `winget install Ollama.Ollama`, or
https://ollama.com/download/windows. Ollama in WSL or on another computer is
fine: `puffpai setup --ollama-url http://HOST:11434`.

## Troubleshooting

**The web page was not built.** Something the build needs was missing (Node.js
20.19+) or the build failed; the window shows the last lines of the log in
`<state>\logs\`. Install what is missing and double-click install.cmd again. You
can still set up and chat with **Set up BuildBot.cmd**.

**"Something else on this computer is already using port 8000".** Another program
(maybe another BuildBot) has that port. Run `install.cmd -Port 8100
-DashboardPort 3100` and use the new Start file.

**401s, or a web page that shows nothing.** The two halves hold different keys:
run install.cmd again and start with **Start BuildBot.cmd**.

**A slow first install** is `chromadb` and the web page build; ten minutes is
normal. **Setup wrote to the wrong place?** `puffpai.cmd where` names the folder
it uses, and why.
