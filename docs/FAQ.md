# BuildBot FAQ

Short answers to what people ask when they pull BuildBot and try to build their
own bot. Each answer says what BuildBot does **today**. When something is not
built yet, the answer says so, and the
[issue tracker](https://github.com/CypherSisters/PuffPAIBuildBot/issues) is
where to ask for it (how: [ISSUES.md](ISSUES.md)).

Pictures of how it fits together: [ARCHITECTURE.md, section 11](ARCHITECTURE.md#11-diagrams).
For coding agents: [AGENTS.md](../AGENTS.md).

**Jump to:**
[Getting started](#getting-started) ·
[Using your bot](#using-your-bot) ·
[Adding things](#adding-things-apps-skills-tools-data-your-own-github-projects) ·
[Phones and other devices](#phones-and-other-devices) ·
[Smart home](#smart-home) ·
[Networks and firewalls](#networks-and-firewalls) ·
[Privacy and the three zones](#privacy-and-the-three-zones) ·
[Models and brains](#models-and-brains) ·
[For developers and AI agents](#for-developers-and-ai-agents) ·
[Working on the Developer page](#working-on-the-developer-page) ·
[Troubleshooting](#troubleshooting) ·
[Contributing and reporting issues](#contributing-and-reporting-issues)

---

## Getting started

### What is BuildBot?

An AI operating system for one person's own computer. You give it one goal in
plain words ("a bot that shows me pictures of Earth and tells me what is in the
sky tonight"). It finds the apps, tools and code that already do each part,
runs them the way they were built, connects them, and turns the result into
your own AI bot. The brain is a local model on your computer. One agent,
Toolsmith, may use a cloud model instead, but only if you choose it (see
[Can I use a cloud model?](#can-i-use-chatgpt-claude-or-another-cloud-model)).
Build it once, own it forever.

### Who is it for?

Anyone with a computer and a goal: people who never code, people who build by
chatting with an AI, startups, and engineers. You can do everything from the
web page. If you like a terminal, every step also has a `puffpai` command.

### What does it cost?

Nothing. It is open source under the Apache-2.0 license
([LICENSE](../LICENSE)). It is the free early version you install yourself, so
expect rough edges. Some outside services it can use ask for their own free
sign-up key (for example Brave Search).

### What computer do I need?

Windows 10 or 11, a Linux PC, or a Mac with macOS 14 or newer. At least 2 GB
of memory (RAM) runs the smallest model; more memory runs a smarter one. The
README's [What computer you need](../README.md) box and
[Minimum requirements](../README.md#minimum-requirements) table list exactly
which model fits which memory.

### How do I install it?

- **Windows:** download the ZIP from GitHub, extract it, double-click
  `install.cmd`.
- **Mac:** download the ZIP, then in Terminal run `bash install.sh`.
- **Linux:** `git clone https://github.com/CypherSisters/PuffPAIBuildBot.git`,
  then `bash install.sh`.

Step by step: [GETTING_STARTED.md](GETTING_STARTED.md), and
[INSTALL_WINDOWS.md](INSTALL_WINDOWS.md), [INSTALL_MAC.md](INSTALL_MAC.md),
[INSTALL_LINUX.md](INSTALL_LINUX.md).

### Do I need Python, Node.js, Ollama, git or Docker first?

No. The installer checks for Python, Node.js and Ollama and asks before
installing any that are missing (Windows uses winget; Mac and Linux get a
pinned, checked download of Python and Node.js, and Ollama too on a Mac; on
Linux, Ollama comes from Ollama's own install script). git is not needed to
install BuildBot, but Toolsmith needs git to download the apps it finds.
Without git, each download stops with "It cannot be downloaded here (no git,
or not a project address)", so install Git if you want BuildBot to run apps
from GitHub. When an app's README commands name a program this computer does
not have yet (Go, Docker, git and so on), it goes on that app's row of the
setup list and your Yes installs it. Docker is not needed to install BuildBot; only some apps it finds
need Docker, and you are asked about it then
(see [Do I need Docker?](#do-i-need-docker)).

### How do I start and stop my bot?

Double-click **Start BuildBot.cmd** (Windows), **Start BuildBot.command**
(Mac) or run `./start-bot.sh` (Linux). Or type `puffpai start`. Stop it with
the Stop file or `puffpai stop`. Start opens your web browser at your bot's
page.

### What is the wizard?

The wizard is the build. It asks what you want your bot to do, then works out
the jobs, finds what already exists, shows you **one setup list** and asks for
**one Yes**. After your Yes it installs and runs everything, and each job gets
its own page that says **Working** or **Not yet** with a plain reason. It runs
in your web browser or in a terminal (`puffpai setup`). Along the way it asks
"Who should do Toolsmith's installs?": this computer (the default) or
Anthropic (see [cloud model](#can-i-use-chatgpt-claude-or-another-cloud-model)).
How it decides: the four questions in
[ARCHITECTURE.md](ARCHITECTURE.md#113-the-build-four-questions).

### What are the four questions the wizard asks itself?

For each goal:

1. What tools do I need to do these tasks?
2. Are they already available, on this computer or ready-made online (GitHub
   apps, skill lists, tool servers, data services)?
3. If so, what do I need to do to use them (install, dependencies including
   Docker, keys, switches)? That becomes one setup list and one Yes.
4. If not, what code exists that I can use to make the tool? Then Toolsmith
   makes one: a small app around a library or program it found (or one of
   yours), or an app assembled from data services.

### Can I stop the wizard and come back later?

Yes. Run the wizard again (`puffpai setup`, or open the web page). It asks
whether to pick up where you stopped or start over, and checks your vault and
model again before it goes on.

### How do I uninstall it?

`puffpai remove` (or **Remove BuildBot.cmd** on Windows, or
`bash install.sh remove`). It lists everything it will delete with sizes and
asks once. Add `--keep-vault` to keep your vault.

---

## Using your bot

### How do I talk to my bot?

Open **Chat** on its web page and type. In a terminal, `puffpai chat` works
too. Ask "what can you do?" and it lists what it really has.

### What does "not connected yet" mean?

You asked for something your bot has no way to do yet, so it says so instead
of making something up. Usually an outside service is switched off (turn it on
under **What leaves your computer**) or it is a gadget BuildBot cannot reach
(see [Smart home](#smart-home)).

### Where do I see what each job is doing?

Each job from your goal has its own page: its main screen, what it is made of
(**Ingredients**), and **Working** or **Not yet** with the reason. "Working"
means it showed real content or answered a real question, never just "it was
installed".

### Where does an app BuildBot found open?

In your own web browser. The job page has a big **Open** button. The app runs
on this computer (at an address starting `http://127.0.0.1`). Apps Toolsmith
got working start again in the background when your bot starts.

### A job says "Not yet". What can I do?

The job page and your bot's page say why, and which app was tried. While
Toolsmith works on a job they say which app it is trying now and since when
(and, with the cloud model, about what it has cost so far). Then:

- **Try next app** runs the job again with the next app in its list.
- **Check again** runs the job again from the top, apps tried before
  included; the old reasons stay under "Tried before".
- **Use a project you know**: paste a GitHub address, then **Run it first**
  (it is tried before any other app on that job) or **Add to the list**.

Each app gets a limited number of steps and minutes; when one cannot start,
the next one follows.

### Can my bot see what is on an app's page?

Yes. The brain has a tool that opens a running app's page in a hidden browser
and reads what a person would see: the title, headings, lists, tables, numbers
and links. So you can ask "what is on the news app right now?". If the app is
stopped, it asks you before starting it. If the app has its own API, the
brain can call it, and anything other than reading waits for your yes. If the app
has its own tool server (MCP), its tools run without asking each time.

### Can my bot run commands on my computer?

Only if you say yes, every time. Each command shows you exactly what will run
and waits for Yes or No. There is no "yes to all". The one exception is a
program you added yourself and set to **Always allow** (see
[your own programs](#can-buildbot-use-programs-i-already-have)): then one plain
call of that program runs without asking, and is logged; anything with shell
features (`;`, `|`, `>` and the like) still asks. (The **Developer** page has
a real terminal for you, which is full access by design and logs every
command.)

### Can it do things on a schedule?

Yes: daily updates, reminders, and watching a live number (for example "tell
me when it goes above 30"). Ask in chat, or use `puffpai routines`. A routine
never goes online by itself.

### Can it talk out loud?

Yes, optionally, and offline: wake word, then Vosk (speech to text), then your
model, then Piper (text to speech). You install the voice extra and the voice
models yourself. See [GETTING_STARTED.md section 8](GETTING_STARTED.md#8-voice-optional).
`puffpai voice test` says what is missing.

### Can I change how my bot looks?

Yes. The **Looks** page or `puffpai looks find "outer space"` then
`puffpai looks use 1` (colours; `puffpai looks undo` goes back). The talking
avatar can be your own 3D model: [AVATARS.md](AVATARS.md). You can also
arrange the home page and the menu (the menu's Customize panel moves, pins,
hides and renames items), or ask in chat ("pin the weather to the top").

### Is there a tour of the pages?

Yes. **Settings**, **Show me around: what each page is for** walks you
through each page, one short step at a time, with Back, Next and Skip.

### Can I have more than one bot on one computer?

Yes, with a second copy of BuildBot in its own folder. Install it with its own
private-files folder and ports (`install.cmd -StateDir <folder> -Port 8330
-DashboardPort 3330`, or `bash install.sh --state-dir <folder> --port 8330
--dashboard-port 3330`). Each copy then has its own folders, API key and
pairing code. Only changing the ports of the same copy is not enough: it would
share the same folders and vault.

The installer never switches you away from a bot you already have without
asking. Run setup again where a bot already lives and it asks "Keep it, or
start a new bot?" (with no one at the keyboard it keeps it; `-NewBot` or
`--new-bot` starts a new one). On Windows a new bot gets a new folder inside
BuildBot's own folder (`state`, then `state-2` and so on) and the old bot's
folder is left as it is. On Mac and Linux the old state folder is moved aside
(nothing is deleted) and its new name is printed; if the old bot kept other
files outside that folder, they are moved into its `other-files` folder. If a second copy of BuildBot on Windows finds another
copy's bot in the shared folder, it asks whether to share it or give this
copy its own folder.

---

## Adding things: apps, skills, tools, data, your own GitHub projects

### How do I add more after the first build?

Three ways, one loop:

- **Chat:** "add tomato diseases to you".
- **The Find more page:** type what you want, press **Find**, tick what you
  want, press **Add these**.
- **Terminal:** `puffpai add "tomato diseases"`. `puffpai add --list` shows
  what you added; `puffpai add --take-out 2` takes one back out.

### How do I add a specific app from GitHub?

On a job page, paste its address (`https://github.com/owner/project`) into
**Use a project you know**, then press **Run it first** (it is tried before
any other app on that job) or **Add to the list**. On the **Find more** page,
a GitHub code project's card has **Add**: it goes on the job it clearly fits,
or on one new job named after it. A project with no licence is not refused;
the card says so in one line and the button reads **Add anyway**.

Either way it waits for your Yes. Then Toolsmith downloads it, Drawbridge
checks the code before any command runs, and Toolsmith runs it the way its
README says. If the README does not say how to run it, Toolsmith treats it as
code to build with and makes a small app around it. If GitHub downloads are
switched off, the job page offers that switch.

### Can it use my own GitHub projects, including private ones?

Yes, once you connect GitHub. Your own projects and the ones you starred are
offered first, marked "From your GitHub", when they share words with a job.
Private projects are offered only if your token can read them, and are
downloaded only after your Yes covers them.

### How do I connect GitHub?

On the **Developer** page (the GitHub tile) or during the wizard. Paste a
token; a read-only token is enough, because BuildBot only reads. The token goes
into your vault, never a file or a log. Disconnecting deletes it from the
vault. Connecting also lifts GitHub's lower search limit for strangers.

### What is a skill, and how do I add one?

A skill is a folder with a `SKILL.md` file (the agentskills.io format) that
teaches your bot how to do something. Skills come from **Find more** (it searches the public skill
lists), or from **Add my own tool**, **Draft a skill**: give a docs page or
paste text, read and change the draft, and say yes. They land in your state
folder under `skills/`. There is no button yet to add a whole skill folder you
wrote; asking for one on the tracker helps.

### How do I add a tool server (MCP)?

Use **Add my own tool** (on the Smart Home page, and on the Developer page
under **Brain**; the Developer page's **Add your own** has a card that leads
there), or say in chat "add my tool server at <address>". Give a web address
or a command that starts one. BuildBot connects to it first and keeps it only if it answers. Any
password goes into your vault. Its tools then show up for the brain.

### How do I give my bot my own documents?

**Knowledge** page, **Add to library**. It reads text, Markdown, PDF, Word,
PowerPoint, spreadsheets (CSV, Excel) and pictures (it reads text in
pictures). BuildBot can read EPUB books too, but the web page does not let an
EPUB through yet. The file is read and stored on this computer. **My Documents** lists
what you added. From a phone, use **Send to Puff** (see
[Phones](#how-do-i-send-a-photo-or-a-document-from-my-phone)).

For many files at once, use **Your files** on the Knowledge page:

- **Add files** or **Add a whole folder**: they go into "My files" in your
  data folder. You can also drop files into that folder yourself.
- **Link a folder** you already have: your bot reads it where it is. It is
  never copied, moved or changed, and **Unlink** takes its files back out.
- You choose where the data folder is. Changing it never moves or deletes
  anything; the old folder is still read.

Your bot looks for new and changed files in these folders by itself (text,
Markdown, PDF, Word, spreadsheets and similar; it leaves pictures in a folder
alone). Keys, passwords and personal details in your files go to your vault,
and what your bot searches says "[held in vault]" in their place.

### How do I add books or offline libraries?

`puffpai sources search <words>`, then `puffpai sources add <id>` (it shows
the size and license first). Or the **Find more knowledge** panel on the
Knowledge page. `puffpai knowledge status` shows how far each book has
learned. Offline library files (`.zim`): see
[KNOWLEDGE_PACKS.md](KNOWLEDGE_PACKS.md).

### How do I add an API key for a service?

The **Sign-up keys** page, or `puffpai keys add <name>`. The key goes straight
into your vault. Never paste a key into chat: your bot refuses it and points
you to the Sign-up keys page.

### Can I build my own bot type by hand?

Yes. A persona is a bot for one subject, made with `puffpai persona new`.
See [BUILD_A_PERSONA.md](BUILD_A_PERSONA.md).

### What if BuildBot finds nothing for a job?

When no ready-made app runs but BuildBot found code about the job (a library,
a command line program, or one of your own projects), Toolsmith makes a small
app from it and the job page says what it was made from. When there is
nothing at all, the job page says **Nothing found yet** and offers
**Find more** (try other words) and **Use a project you know** (paste a GitHub
address). If you think it should have found something, open a "Help me build
a bot for..." issue ([ISSUES.md](ISSUES.md)).

### Can BuildBot run a folder of my own code?

Yes. On the **Developer** page, **Add your own**, **A folder of my code**:
give its full path (like `C:\Users\you\code\my-app` or `/home/you/my-app`).
Your bot reads its README, `package.json`, compose file or Dockerfile the
same way it reads a download, and it joins a job like a GitHub project
(**Run it first** or **Add to the list**), waiting for your Yes. Toolsmith
then runs it right there in your folder: nothing is downloaded or copied, and
BuildBot's own notes and logs go in a `.buildbot` folder inside it. It is never
deleted. BuildBot refuses folders it must not touch: the vault, its own
settings and code, system folders, sign-in keys, a whole drive, and the data
folder itself (a folder you made inside My files is fine). If you chose the
cloud model for Toolsmith, it reads your folder the same way as any app, and
sends Anthropic its README and docs, its file list, the first lines of its
setup files (`package.json`, `pyproject.toml`, the compose file), the
commands it runs there with their output, and what its pages show. A command
that prints a file sends that file's text too.

### Can BuildBot use programs I already have?

Yes, from the **Developer** page, **Add your own**:

- **A program on this computer** (a command line program): your bot finds it,
  reads its help once, and lists what it can do. Your bot can then use it in
  chat, and asks before each run unless you switch on
  **Always allow this program**. A program like git or npm gets one entry per
  command it has. A shell or interpreter (cmd, PowerShell, bash, Python,
  Node) is never run just to read its help.
- **A desktop app** (picked from the apps installed here, like Word or
  Excel): your bot can open it, and open your files in it. It asks you the
  first time it opens a file in that program, and it only opens files in your
  data folder or a folder you linked.
- **A tool server**: leads to **Add my own tool**.

### Can my bot make Word, Excel or PowerPoint files?

Yes. Ask in chat ("make a packing list spreadsheet"). It makes the file in
"My files", in a folder named "Made by" and your bot's name, and the answer
has a one-click way to open it in Office. It can also change a Word, Excel or
PowerPoint file (add paragraphs, rows or slides, or replace text). Files it
made itself change at once. Any other file of yours in your data folder waits
for your yes, one file per question. A file in a folder you linked waits for
your yes too, the question says BuildBot normally never changes those, and it
keeps the original beside it as
"<name> (before BuildBot)". The Knowledge page lists the newest files in My
files with an **Open** button. This needs a model that can use tools.

### Why are the first apps off?

BuildBot ships with five apps of its own (AlarmFlow, Calendar, FitFam,
FreshTable and Trivia). A new bot starts with all of them off, so your bot
only has what you asked for. On the **Apps** page, under **Your first apps**,
each one has a **Turn on** or **Turn off** button. When one fits your goal,
the setup list (on the wizard's last screen, the home page's Build jobs and
each job's page) has the line "Turn on: <app> (one of your first apps)", and
your **Yes, build all of this** turns it on. Turning one on downloads nothing
and reaches nothing outside. Turning one off hides it (its menu entry, home
tiles and widgets, and its tools in chat) and keeps its saved data for when
you turn it on again. A bot made before this change keeps on the first apps it
already used.

---

## Phones and other devices

### Can I use my bot from my phone?

Yes, through your phone's web browser. By default the web page only listens on
this computer. To open it from a phone on your home network, start the web page
for your network: `npm run start:lan` in the `dashboard` folder. Then open
`http://<your computer's address>:3003` on the phone and type the pairing
code. See [GETTING_STARTED.md section 3](GETTING_STARTED.md#3-open-it-and-pair-a-device).

### How does pairing work?

Your bot's web page only answers devices you let in. Type the pairing code
(like `7XK4-P2M9`) on the device. Codes last one hour; a paired device stays
paired for a year. `puffpai pair` prints the current code. From any paired
device, `/pair` lists every device and lets you revoke one.
More: [PAIRING.md](PAIRING.md).

### How do I send a photo or a document from my phone?

Open `/send` (**Send to Puff**) on a paired phone, pick a photo or a PDF, and
it goes into your bot's knowledge. Your phone's browser may also let you add
that page to your home screen. There is no "share to Puff" entry in the
phone's share menu yet.

### Is there a phone app?

Not yet. The phone uses the web page. A native app is not built.

### Can I text my bot, or message it from Telegram, Signal or email?

Not yet. None of those are built. Requests are welcome on the tracker.

### Can I reach my bot from outside my home (away on mobile data)?

Not built in. BuildBot has no remote access, VPN or tunnel feature, and
Drawbridge stops code that opens a hidden tunnel. Apps it runs are started on
this computer only (see
[Does the Bridge cover the apps BuildBot runs?](#does-the-bridge-cover-the-apps-buildbot-runs)).
There is also no HTTPS on the web page yet, so do not expose it to the
internet.

### Is the connection between my phone and my bot encrypted?

Not yet. There is no HTTPS on your home network yet. Pairing keeps out other
devices on your wifi; it does not protect against someone who can watch your
network traffic. [PAIRING.md](PAIRING.md) says the same.

### Can scripts or other programs talk to my bot?

Yes, through its HTTP API on this computer, with the bot's API key
(`X-API-Key` or `Authorization: Bearer`). `puffpai apikey` prints it. Keep it
private.

---

## Smart home

### Can BuildBot run my smart home?

Through **Home Assistant**, yes. Home Assistant is the one smart home hub
BuildBot connects to today. Other gadgets are listed as "not connected yet".

### How do I connect Home Assistant?

Two ways, and they give you different things:

- **Chat or the Smart Home page's connect button:** say "connect my Home
  Assistant", give its address (like `http://homeassistant.local:8123`), and
  paste a long-lived access token where BuildBot shows you (never in chat).
  The token goes into your vault, and Home Assistant's own tool server (MCP)
  becomes tools for the chat brain.
- **The wizard:** switch Home Assistant on under **Your gadgets** and give the
  address and token. This is what the Smart Home page's own device, light and
  climate panels use. The token is kept in BuildBot's settings file. See
  [GETTING_STARTED.md section 7](GETTING_STARTED.md#7-home-assistant-optional).

Make the token in Home Assistant: your profile, then Security.

### What can it do with Home Assistant?

Through Home Assistant's tool server, whatever Home Assistant offers it: for
example check what is on and turn lights and switches on or off. **You choose
which devices it can see, in Home Assistant's own settings** for what is
exposed to assistants. The **Smart Home** page shows devices, lights, climate,
media and automations once Home Assistant is set up in the wizard.

### Does it ask before it turns things on or off?

No. Through chat, the tools Home Assistant gives it run when you ask. The
Smart Home page's buttons act as soon as you press them, from a paired device;
there is no extra confirmation step, even for locks or alarms. That is why you
choose in Home Assistant what it can see. `puffpai devices scan` records new
devices as pending, and `puffpai devices approve` or `deny` decides them.
Only an approved device can be used in a Home Assistant automation your bot
makes, or added as a camera on your home page. Approving or denying does not
change the chat tools or the Smart Home buttons.

### Does Home Assistant traffic go through the Bridge?

Partly. Home Assistant connected with "connect my Home Assistant" gets its own
row and switch on **What leaves your computer**, and every use is logged. When
the Bridge is up (it allows only what you switched on), BuildBot reaches Home
Assistant only if it is switched on. The address set in the wizard is checked
to be on your home network; the address given to "connect my Home Assistant"
is not.

### What about Google Home, Alexa, SmartThings, Matter or Zigbee?

Not directly. If Home Assistant can talk to them, your bot can reach them
through Home Assistant. Direct support is not built; ask on the tracker.

### What media can it control?

Jellyfin (playback and volume) and Calibre-Web (books), on your home network.
Plex, Kodi, Sonos and Chromecast are not built.

---

## Networks and firewalls

### Does BuildBot manage my firewall or router?

No. BuildBot does not configure, manage or connect to your firewall or router.
The wizard asks whether you run one, and suggests a setting for what your bot
itself may do. It works alongside the security gear you already run, and never
touches it.

### Then what does the Bridge control?

BuildBot's own calls to outside services. Each outside service (weather, web
search, a data source an app needs, the code sites and skill lists it searches)
has its own switch on the **What leaves your computer** page. Services like
weather and web search start switched off until you switch them on, or say
Yes to a setup list that shows them (your Yes switches on what the list
shows, and nothing else). Toolsmith's cloud model has its own switch there
too, off unless you choose it. The ones
BuildBot uses to find and download what you pick (code sites, skill lists,
book sources) start switched on, are used only when you look for or add
something, and switch off with one tap. A switched-off service is refused
before a connection opens. With the Bridge up, only what you switched on can
be reached, on the internet or your home network. Every request, allowed or
refused, gets one line in the outside log.

### Does the Bridge cover the apps BuildBot runs?

No. An app BuildBot runs for you can reach the internet by itself; the Bridge
only covers what BuildBot itself sends. The Yes card says so before you say
yes. What BuildBot does: it starts each app on this computer only
(127.0.0.1), and rewrites Docker port settings so they publish on 127.0.0.1.
A Docker app that still publishes itself to your whole network is stopped at
once. An app whose own settings still answer other devices on your home
network keeps running, and its job page says so in one plain line ("This app
can also be reached from other devices on your home network."); stop the app
to close it.

### How do I see and change what goes out?

The **What leaves your computer** page, or in a terminal:

```bash
puffpai outside                # the list, with a number for each
puffpai outside on 6           # shows what will leave, then asks
puffpai outside off 12
puffpai outside log            # every request in the last day
```

Switching off is one tap. Switching on shows one sentence about what will leave
and asks for your Yes on a paired device.

### Can I get a list of sites to allow in my own firewall?

Not yet. The outside page and the outside log show every host your bot reaches,
which you can copy by hand. An export for firewalls is not built; requests are
welcome.

### Does BuildBot scan my network?

No. The wizard looks at this computer, not your network. There is no network
scan.

---

## Privacy and the three zones

### Does anything leave my computer?

Nothing leaves your computer without your consent. The AI model runs here.
Outside services like weather and web search are off until you switch them on
(or say Yes to a setup list that shows them). Looking for apps, skills and
books sends only the short search words shown on screen, when you press to
look, and those services switch off with one tap. If you choose Anthropic for
Toolsmith, Toolsmith sends Anthropic what it needs to find and install apps,
after you agree to a consent sentence (the full list:
[What does Toolsmith send to Anthropic?](#what-does-toolsmith-send-to-anthropic)).
The outside log shows every request.

### What are the three zones?

- **Zone 1, your private stuff:** the vault, an encrypted store for keys,
  personal details and private files, opened with your PIN, which locks itself
  when idle.
- **Zone 2, the brain:** the local model, five of the seven agents
  (Concierge, Steward, Archivist, Majordomo and Caretaker), what your bot
  knows, and the apps running on this computer.
- **Zone 3, the Bridge:** the only way out for BuildBot's own calls. Every
  outside service has its own switch on the **What leaves your computer**
  page. Most start off; the ones used to find and download what you pick
  start on and switch off with one tap. Two agents work here: Drawbridge, the
  Bridge's agent, which checks everything that comes in, and Toolsmith, which
  downloads and runs strangers' code and may talk to a cloud model, so it is
  kept away from your private things (see
  [What can Toolsmith touch?](#what-can-toolsmith-touch-on-my-computer)).

Picture: [ARCHITECTURE.md 11.1](ARCHITECTURE.md#111-the-three-zones-and-what-may-cross).

### What is the vault, and what if I forget my PIN?

The vault is an encrypted file on your computer, opened with your PIN. Keys
never leave it except to their own service, added on the way out. If you
forget the PIN: [TROUBLESHOOTING.md, Vault PIN forgotten](TROUBLESHOOTING.md#vault-pin-forgotten).

### What happens to personal details I type in chat?

Phone numbers, home addresses, birthdays, ID and card numbers are taken out of
a chat before it is saved and put into the vault (or its sealed inbox while
the vault is locked). Saved chats themselves are not encrypted. Files you add
are checked too: keys, passwords and personal details found in them go into
the vault, and your bot's search sees "[held in vault]" instead. A file you
upload while the vault is locked that holds such things is sealed whole and
added once you unlock.

### What is a "Yes", and what does it cover?

One Yes covers exactly the setup list you were shown. If the list changes
(you add a project, find more, a new job), the old Yes no longer covers it and
only the changed jobs wait for a new Yes. Your Yes also switches on the
outside services and turns on the first apps the list shows. Turning on an
outside service later, adding a key, choosing Anthropic for Toolsmith, and
running a chat command each ask for their own Yes.

### Who checks the code BuildBot downloads?

Drawbridge, the security agent. It checks every addition before your Yes
counts, and every app Toolsmith downloads before any of its commands runs:
glue code, packages, and code projects (for example a hidden tunnel, or a
page that hides encoded code and runs it). In a project's README, install
lines are fine; lines aimed at an AI are hidden from Toolsmith's model (the
job says so), and clearly hostile text ("ignore previous instructions") stops
it. Drawbridge stops real code-safety problems; it is not a promise that an
outside app is safe, so the Yes card tells you what an app can do once it
runs. A link inside a download (a symbolic link or a Windows junction) is
never followed when BuildBot checks, copies or deletes it, on every Python
BuildBot supports (3.11 included), so a download cannot lead those steps into
your other folders.

### What can Toolsmith touch on my computer?

Toolsmith works in the app's own folder (or the folder of your code you
chose), its logs and its scratch folder. It may read the folders of the usual
build tools (Node, Python, Go, Docker, git and their caches). On Windows, any
other folder waits for your Yes. On Mac and Linux, only other folders inside
your home folder wait for your Yes; a command that names a folder outside it
(like `/etc`, `/opt`, `/usr` or `/tmp`) is neither refused nor held. Some
places it never touches, even with your Yes: BuildBot's private files (vault,
keys, settings, chats, logs), other apps' folders, your data folder and linked
folders, BuildBot's own code, sign-in folders like `.ssh`, browser profiles,
and your Documents, Desktop, Pictures, Downloads, Videos and Music. One
exception: a folder of your code that you chose may sit inside Documents,
your data folder or a linked folder, and then Toolsmith works in that one
folder only. Steps beyond the app's folder (an admin step, a
program for the whole computer) run only when they are on your setup list
exactly as written. Your account name is replaced with "you" in what its
model sees.

The honest limit: this bounds what Toolsmith's model can ask for. An install
script or server it runs still runs as you, so a hostile script could read
your files. Docker apps are held by Docker. Running Toolsmith's installs under
a separate, limited Windows account is not built yet.

---

## Models and brains

### Which AI model does my bot use?

A local model through Ollama. The wizard checks your memory and offers the
models that fit, from small Llama 3.2 and Qwen 2.5 models up to Qwen 3. It
downloads the model after it asks.

### Can I use ChatGPT, Claude or another cloud model?

For chat, no: the brain and every agent but one always run on a local
model. The one exception is **Toolsmith**, the agent that finds, installs and
starts apps. You may put it on a frontier model, which is faster and gets
far more apps working. Today that means Anthropic's Claude (Opus 5.5 by
default, or Sonnet 5.5). The model on this computer stays the
default ("private, slower"). Choose it in **Settings**, on the
**Toolsmith's model** card, or when the wizard asks "Who should do
Toolsmith's installs?". You read one consent sentence, paste your own
Anthropic key, and say Yes; the key goes into your vault. Going back to this
computer is one click, and **Remove the key** deletes it from the vault.
No other provider (ChatGPT and the rest) is offered; ask on the tracker if you
want one. Why a frontier model helps Toolsmith:
[README](../README.md#toolsmith-works-best-on-a-frontier-model).

Anthropic bills your account. Settings shows about how much Toolsmith has
used so far, the job page shows what the job has cost so far (every app tried
for it), and your
bot stops at 3000 requests a month. If Anthropic cannot be reached, Toolsmith
uses the model on this computer for that app. If your key is refused, the job
says "Toolsmith's Anthropic key was not accepted. Check it in Settings."
After you switch, the bot page and Build jobs offer **Check all again** for
jobs that were built on this computer. If you picked Anthropic in the wizard
but your key is not saved yet, the browser wizard's build waits before
Toolsmith's first install, with **Open Settings** and **Use this computer** on
the setup list, so it does not quietly build on this computer instead. The
terminal wizard (`puffpai setup`) works differently: with no key, a key it
cannot use, or no vault yet, it says so and Toolsmith uses this computer until
you add the key in Settings.

### What does Toolsmith send to Anthropic?

Nothing, unless you chose Anthropic. When you choose it, you agree to this
consent sentence, word for word:

> "When Toolsmith finds and installs apps, it sends Anthropic your goal and
> your bot's jobs, the names and descriptions of the apps it found, each app's
> README, docs and file list, the start of its setup files, the commands it
> runs with their output, and what the app's pages show. If you point it at a
> folder of your own, it reads that folder the same way. Keys and personal
> details it spots are taken out first. It never sends your chats or your
> vault. Everything else in BuildBot stays on this computer."

In more detail:

- **Finding and picking apps** (before any install): your goal, each job's
  name and what it does, and the public names, descriptions, stars, topics
  and language of the projects it found.
- **Installing an app:** the job's name and what it does, the app's README,
  its file list, the first lines of its setup files (`package.json`,
  `pyproject.toml`, the compose file), pages of its own docs it reads, the
  commands it runs with their output, and the text of its pages once it runs.
- **A folder of your own code:** read the same way as any app, and these
  are your files: its README and docs, file list and setup files, the
  commands run there with their output, and what its pages show. A command
  that prints a file (`cat`, `type`) sends that file's text.
- **Never sent:** your chats, your vault, and files Toolsmith does not read.

If this sentence changes in an update, Toolsmith goes back to this computer
until you read the new one and pick Anthropic again. Settings says "What
Toolsmith sends to Anthropic now reads differently. Read it and pick
Anthropic again to keep using it." The **Toolsmith's model** card opens on
Anthropic with the new sentence, and your key stays in your vault, so
agreeing again is one tap on **Use Anthropic for Toolsmith**, with no key to
paste. Until you pick, a build waits instead of quietly using this computer,
and says "What Toolsmith sends to Anthropic now reads differently. Read it in
Settings and pick Anthropic again to keep using it, or choose This computer."
Pressing **Skip** on the wizard's Toolsmith question does not end that wait;
only picking Anthropic again or this computer does.

Before anything leaves, every piece of text passes a key and password
detector and a personal details screen (a health statement loses the person
and the amounts). If that check cannot run, nothing is sent. Your account
name becomes "you". Each call writes one line on the outside log (sizes only)
and a cost row (tokens and cost, never content). Honest limits: the screens
catch known key shapes and kinds of personal detail, not every private fact.

### My vault is locked and Toolsmith is waiting. What do I do?

The vault locks itself after 5 minutes idle, and Toolsmith needs your
Anthropic key from it. So when a cloud build starts, or when you unlock the
vault or save the key, Toolsmith keeps the key ready in memory (never in a
file or a log) while you build: 1 hour after its last work by default, or
4 hours, or until you lock the vault (the
"Keep Toolsmith's key ready while I'm building" choice in Settings), and
never longer than 24 hours. Locking the vault, switching Toolsmith back to
this computer, or changing the key lets go of it at once. If the key is not
ready, the build pauses in place and the wizard's Building screen, the
Build jobs tile, the bot page and the job page show a PIN box with
**Unlock**. Unlock, and it goes on with the same app.

### Can I use LM Studio, llama.cpp or another local model server?

Ollama is the supported way. There is code for running a GGUF model file
directly through llama.cpp inside BuildBot, but no setting for another model
server's address. Requests are welcome.

### Can I point it at Ollama on another computer?

Yes, while the Bridge is not up (and the build is not sealed). Set `PUFFPAI_OLLAMA_URL` (for example
`http://192.168.1.50:11434`). With the Bridge up (only what you switched on),
an Ollama on another computer is refused. `puffpai where` shows which Ollama
address your bot uses.

### How do I change the model after setup?

On the **Developer** page, open **Brain** and use **Pick another model**. It
lists models already on this computer and uses the one you pick after your bot
restarts. It also shows models that fit but are not on this computer yet; to
download one of those, run the wizard again (`puffpai setup`).
You can also change `model` under `ollama` in `config/puffpai_config.json`.
Toolsmith's model (this computer or Anthropic) is a separate choice, in
**Settings**.

### Why do some answers feel limited on a small computer?

Small models follow set phrases better than free conversation. The README's
[What computer you need](../README.md) box says what each memory size
can do. More memory lets the wizard offer a model that can use tools.

---

## For developers and AI agents

### Where do I start reading the code?

[AGENTS.md](../AGENTS.md) (one page: zones, agents, the build, consent, how to
extend, how to test), then [ARCHITECTURE.md](ARCHITECTURE.md) (module map,
data flow, schemas, and the diagrams in section 11). [llms.txt](../llms.txt)
lists the key files for AI tools.

### What are the seven agents?

Concierge (front desk), Steward (Home Assistant and media), Drawbridge
(security, the Bridge's agent and the veto), Archivist (vault and knowledge),
Majordomo (routines, background work, narrates the build), Caretaker (machine
health) and Toolsmith (finds, installs and starts apps, makes apps from code
and parts, and keeps its own workspace; the only agent that may use a cloud
model, and only if you choose it). Five work in the Brain; Drawbridge and
Toolsmith work in the Bridge, the zone that touches the outside. They talk
only through one message bus, and every message that changes something is
reviewed by Drawbridge first. The
chat brain is the local model with its tools, not an eighth agent. See the
**Agent Team** page, `puffpai agents status`, `src/puffpai/agents/registry.py`, and the
[agents diagram](ARCHITECTURE.md#112-the-seven-agents-and-who-calls-what).

### Can I add my own agent?

Not as a plug-in. The roster is fixed in `src/puffpai/agents/registry.py`, and
each agent's secrets are fixed in `src/puffpai/security/secrets_provider.py`.
Adding one means changing both, with tests. For most needs, a brain tool, a
tool server or a skill is the better fit.

### How do I add a tool for the chat brain?

Register it with `brain_tools.register(name, description, schema, fn,
needs_yes=...)` in `src/puffpai/brain_tools.py`, or add a provider for tools
that come and go. Anything that changes something should set `needs_yes`.
See [ARCHITECTURE.md section 8](ARCHITECTURE.md#8-the-chat-brain-tool-loop).

### Where is the cloud model code, and how is it fenced in?

`src/puffpai/security/toolsmith_cloud.py` (the setting, the consent, the key,
what is cleaned before it leaves, costs) and
`src/puffpai/build/toolsmith_cloud_loop.py` (the install loop). Only
Toolsmith's install loop and its two finding steps may call it, and
`tests/test_toolsmith_cloud.py` fails if any other code does. Toolsmith's
allowed and refused places are in `src/puffpai/security/toolsmith_zone.py`.
Details: [AGENTS.md sections 3b and 3c](../AGENTS.md#3b-toolsmiths-model).
Never add another caller.

### How do I add a new place to search (a catalog source)?

Add a row to `config/where_to_look.json`, write a searcher in
`src/puffpai/catalogs/searchers/`, and add it to `SEARCHER_MODULES` in
`catalogs/where_to_look.py`. Tests: `tests/test_catalogs_adapters.py`,
`tests/test_catalogs_live.py`. Details: [AGENTS.md section 7](../AGENTS.md#7-how-to-extend).

### How do I add a new view for assembled apps?

Add it to `assemble/spec.py` (`VIEWS`, `expect_for`) and
`assemble/scaffold/views.js` (`VIEWS`, `TAB_WORDS`), and if a route may pick
it, to `build/schema.py`. Tests are listed in
[AGENTS.md section 7](../AGENTS.md#7-how-to-extend).

### Why is there no code that knows about my subject?

On purpose. BuildBot is goal-agnostic: the same pipeline builds a music bot or
a garden bot. Never add code that names a topic or one bot; fix the general
rule instead.

### How do I run the tests?

```bash
pip install -e ".[dev]"
python -m pytest -q
```

On Windows use `py -3 -m pytest -q`. Tests never go online; opt-in live tests
are in `tests/live/`. While you work, run the tests for what you changed, not
the whole suite. See [CONTRIBUTING.md](../CONTRIBUTING.md).

On GitHub, CI (`.github/workflows/ci.yml`) runs on pushes to `main` and on
pull requests. Each run tests Python 3.11 on Linux; Python 3.12 is tested only
when someone starts the run by hand and ticks it. The same run also does a
type check that reports problems but does not fail yet, installs BuildBot
from nothing on a clean Linux computer, and type-checks and builds the
dashboard. A separate Mac check (`.github/workflows/mac.yml`, macOS 14 on
Apple silicon) installs and starts BuildBot when the installer or the files
it covers change, or when started by hand.

### When is a change "done"?

For anything that changes the build: a live run. A real goal end to end, on
spare ports (`puffpai start --port <n> --dashboard-port <m>`), with a fresh
state folder and the local model, one Yes from a paired device, judged by what
each job page shows. Passing unit tests is not done.
[AGENTS.md section 8](../AGENTS.md#8-how-to-run-and-test).

### Where does BuildBot keep its data?

Logs, chats, builds and keys for outside services go in the state folder.
Windows: `%LOCALAPPDATA%\PuffPAI`. Mac: `~/Library/Application Support/PuffPAI`.
Linux: `~/.local/state/puffpai`. Set `PUFFPAI_STATE_DIR` to use another folder
(useful for tests). Your settings (`config/`) and your vault (`vault_data`)
stay in BuildBot's own folder unless you move them (the installer's state
folder option moves both); git leaves them out. Apps Toolsmith downloads go in
`host_apps` in the state folder. Your own files and downloaded books go in
your data folder (its "My files" folder holds your uploads and the files your
bot makes); choose it on the Knowledge page under **Your files**.
`puffpai where` prints the state, data and config folders and the vault's
folder, and `puffpai vault location` shows the vault. The file list is in
[AGENTS.md section 6](../AGENTS.md#6-data-files).

### How should an AI coding agent work in this repo?

Read [AGENTS.md](../AGENTS.md) first: it is the instructions for coding
agents (`CLAUDE.md` points to it), with the hard rules in its section 9.
[llms.txt](../llms.txt) lists the key files. The rules that catch agents most
often: owner-facing words are plain (no em dashes, none of the machinery
words listed in `src/puffpai/plain_words.py`, and the privacy line is exactly "Nothing leaves your computer without your
consent."), code never names a subject or one bot, outbound calls go only
through `security/egress_doors.py`, keys live only in the vault, new source
files carry the copyright header, and the "puff puff pass" signature is never
removed, moved or reworded. Add a test with every change, and run the tests
for what you changed. When a change makes an answer in this FAQ untrue,
update the FAQ in the same change. When someone asks about something not
built, say so plainly and point to the
[issue tracker](https://github.com/CypherSisters/PuffPAIBuildBot/issues).

---

## Working on the Developer page

### What is the Developer page, and who is it for?

One page in your bot's web page for people who like to see the parts and use
commands: your own code, programs and tool servers, the three zones (Vault,
Brain, Bridge), a real command line, and GitHub. You do not need to code to
open it, but the command line is for people who know commands. Open
**Developer** in the menu (in the lower group, between **Looks** and
**Settings**), or press the "YOUR PRIVATE STUFF" card at the bottom of the
menu. It is always there, on every bot.

### What is on the Developer page?

From top to bottom:

- **Add your own**: four cards, each with a one-line "What happens".
  **A folder of my code**, **A program on this computer**, **A desktop app**
  and **A tool server** (see the questions under
  [Adding things](#can-buildbot-run-a-folder-of-my-own-code)).
- **Three cards**, one per zone: **Vault** (Zone 1), **Brain** (Zone 2) and
  **Bridge** (Zone 3). Each shows a status line (for example "Made, locked",
  or the model name and whether it is answering) and opens that zone's page.
- **Add a tool**: three ways to give your bot something new: **Find one**
  (goes to Find more), **Paste a link** (a GitHub, Codeberg, npm, PyPI,
  ClawHub or skills.sh link goes to Find more; any other link, like a tool
  server address or a docs page, goes to **Add my own tool**), and **For
  developers: the command line**.
- **Command line** and **GitHub**, side by side in one row. The command line
  is a real shell on this computer, with **Bigger** and **Own window**.
  GitHub connects your account (a read-only token is enough) so searches go
  further and your own projects are offered.

Near the top the page also shows the privacy line, "Nothing leaves your
computer without your consent.", from the same place the menu's card and
**What leaves your computer** read it.

### What is on the Vault, Brain and Bridge pages?

Each has **Use CLI** at the top: a side panel with the terminal commands for
that page, for **Windows PowerShell** or **Mac or Linux**, ready to copy. No
command there ever carries your PIN, a password or a key; the ones that need
one ask when they run. Things with no command yet are said plainly there.

- **Zone 1: Vault**: locked or open, the folder it lives in, how many things
  wait to go in at the next unlock, and how it is locked. **Change where the
  vault lives** moves it to another folder (it checks the copy opens with
  your PIN and keeps the old copy), or uses a BuildBot vault already in
  another folder. Locking, unlocking and a new recovery code stay on the
  **Vault page**; keys on the **Sign-up keys page**.
- **Zone 2: Brain**: the model in use, where it runs and where that address
  came from, whether it is answering, and this computer's memory and
  processor. **Pick another model** lists the models that fit; **Use this
  one** then **Yes, use it** picks one already on this computer. Below:
  **Home Assistant** (connect it with its address and token) and **Add my own
  tool** (a tool server, or **Draft a skill** from a docs page), with links to
  the **Smart Home page** and the **Device page**.
- **Zone 3: Bridge**: the internet setting (for example "Only the services
  you switched on"), your Bridge choice, how many outside services are on and
  which ones, what BuildBot does and does not do with your own firewall,
  router and home network, your paired
  devices (with Revoke), and **Add a device** (the pairing page). Switches
  for outside services stay on **What leaves your computer**, linked from
  here.

### How does the command line work?

It is a real shell on the computer that runs your bot: PowerShell on Windows,
bash on Mac and Linux. It starts in BuildBot's own folder with the `puffpai`
command ready, and the folder and anything you set carry over from one
command to the next. Ctrl+C or **Stop** stops a running command; `clear` or
`cls` empties the screen; the Up and Down keys bring back earlier commands
(kept in memory only). Leaving the page ends the shell, and your bot ends one
left alone for 30 minutes. One device can have two open at once (a third
ends the oldest).

It is full access by design: there is no question before each command, and
what you run here is not checked by the Bridge. It only opens on a paired
device, it is off when commands are switched off for your bot (the same
switch as the chat's), and each command you type is one line in the security
log (secrets in it hidden, never what it printed). Commands that ask for a
PIN or a password need a terminal window of their own, and so do
`puffpai setup`, `puffpai chat`, `puffpai start` and `puffpai stop`
(stopping your bot ends this command line too); **Use CLI** says how to open
one. This is not the chat's way of running commands, which still waits for
your yes on every command, except one plain call of a program you added and
set to **Always allow this program**.

### What is a good way to work there?

A workflow that keeps each step easy to check:

1. **Pair this device** if you have not. Every change on the Developer page
   needs a paired device.
2. **Connect GitHub** on the **GitHub** tile, so searches go further and your
   own projects are offered.
3. **Bring in your own code.** On **A folder of my code**, give the folder's
   full path and press **Add this folder**. If it fits a job, pick **Run it
   first**, **Add to the list** or **Its own job**. Then
   **Open the job to give your Yes**. The job page shows what Toolsmith is
   trying, and its notes and logs go in a `.buildbot` folder inside yours.
4. **Register programs you use.** On **A program on this computer**, type a
   name (like `ffmpeg`) or a full path, pick **Command line** or
   **Has a window**, and press **Add**. Check the commands it listed. Leave
   **Always allow this program** off until you trust how your bot uses it in
   chat. Desktop apps go on **A desktop app**: search, **Add**, then
   **Open**.
5. **Connect a tool server.** **A tool server** leads to **Add my own tool**
   on the Brain page: a short name, its address or the command that starts
   it, a password if it needs one, then **Add** and **Yes, add it**. BuildBot
   connects to it first and keeps it only if it answers.
6. **Switch the model if you need to.** Brain page, **Pick another model**,
   then restart your bot.
7. **Try it, then check what happened.** Ask in chat. Then open **What leaves
   your computer** (linked from the Bridge page) to see every outside request
   from the last day, or type `puffpai outside log` in the command line.
   `bot.log` is in the `logs` folder of your state folder (`puffpai where`
   shows the state folder).

### What takes effect at once, and what needs a restart?

- **A new model** is used after your bot restarts. Stop and start it from a
  terminal window of your own (`puffpai stop`, then `puffpai start`), not from
  the Developer page's command line, which ends with your bot.
- **A tool server** and **Home Assistant** give your bot their tools as soon
  as they are added.
- **A program** you add, and **Always allow this program**, count from your
  next chat message.
- **A folder of your code** joins a job and waits for your Yes on the job
  page; nothing runs before that.
- **GitHub** is connected (or disconnected) at once.
- **Changing where the vault lives** happens when you press the button
  (moving it, or using another vault, takes your PIN).
- **The internet setting and the Bridge choice** are not changed here: run
  Setup again. **Use CLI** on the Bridge page shows the command.

### What on the Developer page asks for a Yes, a PIN or a paired device?

The whole web page only opens on a paired device, and the backend checks
again for the command line, your programs, a folder of your code, tool
servers, Home Assistant, GitHub and where the vault lives. On top of that:

- **A Yes:** **Yes, use it** for a new model; **Yes, add it** for a tool
  server; **Yes, connect it** for Home Assistant; **Yes, add this skill** for
  a drafted skill; the job page's Yes for
  a folder of your code.
- **Your PIN:** moving the vault or using another vault. A tool server's
  password goes into your vault, so the vault must be open.
- **No question each time:** the command line (full access, every command
  logged), and a program you set to **Always allow** (one plain call of it,
  logged; anything with shell features still waits for your yes).

### What is not built on the Developer page yet?

The pages, or their **Use CLI** panel, say these plainly:

- Downloading a new model from the Brain page (run `puffpai setup` again).
- Pointing your bot at a model on another computer from the Brain page (a
  setup question).
- A command for changing your PIN without the recovery code.
- A list of websites to copy into your own firewall, and a command that
  saves the outside log to a file.

Also not built, though no page says so:

- A log viewer. Read the files in the `logs` folder of your state folder
  (`puffpai where` shows the state folder).
- Adding a whole skill folder you wrote.

If **A program on this computer** and **A desktop app** say "Coming soon",
your bot is older than the web page: update both. Ask for anything else on
the [issue tracker](https://github.com/CypherSisters/PuffPAIBuildBot/issues).

### How do I run BuildBot from source to work on it?

```bash
git clone https://github.com/CypherSisters/PuffPAIBuildBot.git
cd PuffPAIBuildBot
pip install -e ".[dev]"
puffpai setup
puffpai start
```

Use a virtual environment if you like (CONTRIBUTING.md shows one). You need
Python 3.11 or newer, Ollama, and Node.js 20.19 or newer in the 20 line, or
22.12 or newer, for the web page (`^20.19.0 || >=22.12.0` in
`dashboard/package.json`). `puffpai start` runs the bot on port 8000 and the
web page on port 3003, on this computer only, and prints the pairing code.

To keep a test bot apart from your real one, set `PUFFPAI_DATA_DIR` to a
spare folder and pick spare ports
(`puffpai start --port 8330 --dashboard-port 3330`). `PUFFPAI_DATA_DIR`
moves your settings (`config/`), the vault (`vault_data`) and knowledge data
out of BuildBot's folder, and the state folder (logs, chats, builds, keys for
outside services) becomes its `state` folder. `PUFFPAI_STATE_DIR` moves only
the state folder: set on its own, the settings and the vault stay in
BuildBot's folder and are shared with your real bot. `puffpai where` shows
which folders a copy uses.

To work on the web page itself, run the two halves yourself:
`puffpai serve --port 8000` in one terminal, and in the `dashboard` folder
`npm install` then `npm run dev` (port 3003) in another. The web page needs
the bot's API key in `dashboard/.env.local` (`puffpai apikey` prints it);
the steps are in
[GETTING_STARTED.md section 10](GETTING_STARTED.md#10-for-developers-the-two-halves-by-hand).

### Which checks should I run for a Developer page change?

- **Python:** `python -m pytest -q` with the test files for what you touched
  (Windows: `py -3 -m pytest -q`). For the Developer page:
  `tests/test_developer_pages.py`, `tests/test_developer_api.py`,
  `tests/test_developer_terminal.py`, `tests/test_own_programs.py`,
  `tests/test_build_local_folder.py`, `tests/test_mcp_api.py` and
  `tests/test_dashboard_plain_words.py`.
- **The web page:** in `dashboard`, `npx tsc --noEmit` and `npm run build`
  (what CI runs). `npm run lint` runs ESLint, and the `npm run test:...`
  scripts in `dashboard/package.json` run the pairing, proxy and page proofs.
- **The docs:** `tests/test_docs_truth.py` (every `puffpai` command the docs
  name must exist, no em dashes, the README's model tables),
  `tests/test_no_ask_first_and_telemetry.py`, `tests/test_plain_words.py` and
  `tests/test_repo_hygiene_docs.py`.

### Where is the code for each part of the Developer page?

Web page files are under `dashboard/app/`; Python under `src/puffpai/`.

| Part | Web page | Backend |
|------|----------|---------|
| The page itself and its three cards | `dashboard/developer/page.tsx`, `overviewWords.ts` | `GET /build/self`, `GET /system/privacy` |
| Add your own | `dashboard/developer/_components/AddYourOwn.tsx`, `dashboard/developer/addYourOwnService.ts` | `build/api.py` (`POST /build/project`), `build/apps.py` (`from_local_folder`), `programs.py`, `programs_api.py` |
| Add a tool | `_components/AddATool.tsx` | (links only) |
| Add my own tool, Home Assistant | `_components/ToolServers/AddOwnTool.tsx`, `ConnectHomeAssistant.tsx` | `mcp_api.py`, `mcp_client.py` |
| Command line | `dashboard/developer/_components/CliTile.tsx`, `CommandLine.tsx`, `dashboard/developer/terminal/page.tsx` | `developer_terminal.py` |
| Use CLI | `dashboard/developer/_components/UseCliPanel.tsx` | `developer_api.py` (`COMMANDS`, `NO_COMMAND_YET`) |
| Vault page | `dashboard/developer/vault/page.tsx`, `ChangePlace.tsx` | `developer_api.py` (`GET /developer/vault`), `vault_place_api.py` |
| Brain page | `dashboard/developer/brain/page.tsx` | `developer_api.py` (`GET /developer/brain`), `POST /setup/ollama` in `api_server.py` |
| Bridge page | `dashboard/developer/bridge/page.tsx`, `dashboard/settings/PairedDevicesCard.tsx` | `GET /build/self`, `GET /system/privacy` |
| GitHub | `dashboard/developer/_components/GitHubTile.tsx` | `github_account_api.py` |
| The menu entry | `dashboard/_lib/menuAside.ts` | |

### How do I add a new card or route to the Developer page?

1. **The backend route.** Put it in a blueprint registered in `api_server.py`
   with one line, like `developer_api.py` (read only) or `programs_api.py`.
   Anything that changes something needs a paired device. Copy the
   `_paired_only` check in `mcp_api.py`: it checks for a paired device AND
   that the caller holds the bot's own key (`_app_caller_class() == "owner"`),
   which the one in `programs_api.py` does not. If it is a real change, show
   a Yes in one plain sentence first.
2. **The web page's proxy list.** The browser reaches the backend only
   through `/api/pai`, which carries only the paths in the
   `PROXY_ALLOWED_PREFIXES` list in `dashboard/app/_lib/server/`. A new first
   path segment goes there, in order, with a comment naming its caller.
3. **The card.** Put it in `dashboard/app/dashboard/developer/` (a component
   under `_components/`, its calls in a service file). The zone names
   ("Zone 1: Vault" and the like) are allowed only inside that folder.
4. **Its words.** Every file in the developer folder must be listed in
   `FILES` in `tests/test_dashboard_plain_words.py`, and its text must pass
   `src/puffpai/plain_words.py` (no machinery words; a file name like
   `.exe` or `.json` is fine). A test fails if a developer file is missing
   from that list.
5. **Commands.** A command shown in **Use CLI** goes in `COMMANDS` in
   `developer_api.py`; `tests/test_developer_api.py` checks every one is a
   real `puffpai` command, and none may carry a PIN, password or key.
6. **Tests and docs.** Add a test (tests never go online), keep the code free
   of any subject or one bot, and update this FAQ in the same change.

---

## Troubleshooting

### "Something else on this computer is already using port 8000" (or 3003)

Another program, maybe another bot, has that port. Stop it, or pick other
numbers: `puffpai start --port 8330 --dashboard-port 3330`. If it was your own
bot, it says "Your bot is already running" instead.

### An app needs Docker and Docker is not installed

The setup list shows a Docker row. On computers where BuildBot can install
it, your Yes installs Docker and starts it:

- **Windows:** with Windows' own installer (winget). Windows asks you for an
  admin yes once, and Docker Desktop may ask you to restart the computer;
  restart, then press **Check again**.
- **Mac:** BuildBot downloads Docker Desktop from Docker and copies it to
  your Applications folder. The first start asks for your Mac password.
- **Linux:** Docker Engine (not Docker Desktop), with Docker's own install
  script, which needs admin (root or sudo).

Where it cannot (Windows without winget, a Mac or Linux computer without
`curl`, or Linux without admin), the row says to install Docker Desktop
yourself and press **Check again**. If no app for that job runs without
Docker, the job waits.

### Docker is installed but not running

BuildBot tries to start it and waits a few minutes. If it cannot, it asks you
to open Docker Desktop yourself (it may ask you to accept its terms or to
restart), then press **Check again**.

### Do I need Docker?

Only for apps that are built to run in Docker. BuildBot runs each app the way
its README says: Node apps natively, Python apps in their own folder, Docker
apps in Docker. Apps that do not need Docker never ask for it.

### It says GitHub asked it to slow down

GitHub limits how often anyone can search without signing in. BuildBot waits
and says how long, and it remembers searches for a day. Connect GitHub on the
**Developer** page (a read-only token is enough) for a higher limit.

### "Ollama not reachable" or the model is missing

See [TROUBLESHOOTING.md](TROUBLESHOOTING.md#ollama-not-reachable-or-the-model-is-missing).
Check Ollama is running and `puffpai where` shows the right address.

### The web page asks for a pairing code and I do not have one

The window that started your bot shows it. `puffpai pair` prints the current
one. Codes last an hour.

### The web page shows nothing, or errors with 401

The bot and the web page hold different keys. Start them together with
`puffpai start` or the Start file.

### Windows notes

If Windows says "Windows protected your PC", click **More info**, then
**Run anyway**. The installer uses winget to offer missing programs. Apps run
by BuildBot open no console windows.
[INSTALL_WINDOWS.md](INSTALL_WINDOWS.md).

### Mac notes

macOS 14 or newer (Ollama needs it). The first time, if your Mac says it
cannot check who made a file, right-click it and pick **Open** (on macOS 15:
System Settings, Privacy & Security, Open Anyway). The Mac is new: an
automatic check installs BuildBot from nothing on an Apple silicon Mac and
starts it, but no one has used it on their own Mac yet. Reports are welcome.
[INSTALL_MAC.md](INSTALL_MAC.md).

### Linux notes

`bash install.sh`; Ollama comes from Ollama's own script, which asks for your
password. Toolsmith's sandboxed workspace shell (for code it writes itself)
needs Linux with bubblewrap installed. Installing the apps it finds uses a
normal command line on every system (Git Bash on Windows when Git is there).
[INSTALL_LINUX.md](INSTALL_LINUX.md).

### Where are the logs?

In the `logs` folder of your state folder (`puffpai where` shows it):
`bot.log` (every command Toolsmith runs and its last lines are here too),
`web-page.log`, and `outside.jsonl` (every outside request). Each app
Toolsmith runs keeps its own log folder beside it, and a folder of your own
code keeps them in its `.buildbot` folder. On Windows the installer keeps
`install-log.txt` in BuildBot's own folder. Before sharing any, read
[ISSUES.md](ISSUES.md#take-private-things-out-first).

More: [TROUBLESHOOTING.md](TROUBLESHOOTING.md).

---

## Contributing and reporting issues

### How do I report a bug or ask for a feature?

On the issue tracker:
[github.com/CypherSisters/PuffPAIBuildBot/issues](https://github.com/CypherSisters/PuffPAIBuildBot/issues).
Pick **Bug report**, **Feature request**, **Help me build a bot for...** or
**Wizard gap**. [ISSUES.md](ISSUES.md) says what to include and how to take
private things out of logs first.

### What should I never put in an issue?

Your vault, any key or token (your Anthropic key included), your home
address, or whole log files. Copy only
the lines around the problem. [ISSUES.md](ISSUES.md#take-private-things-out-first).

### I found a security problem

Do not open a public issue. Use GitHub's private vulnerability reporting; see
[SECURITY.md](../SECURITY.md).

### How do I contribute code?

Fork [CypherSisters/PuffPAIBuildBot](https://github.com/CypherSisters/PuffPAIBuildBot),
make your change with a test, run the tests, and open a
[pull request](https://github.com/CypherSisters/PuffPAIBuildBot/pulls) there.
Good places to start are the rough edges under the README's
[Status](../README.md#status) and [How this was built](../README.md#how-this-was-built).
If you work on the Developer page, see
[Working on the Developer page](#working-on-the-developer-page).
[CONTRIBUTING.md](../CONTRIBUTING.md) has setup and style;
[AGENTS.md](../AGENTS.md) has the hard rules (plain words, goal-agnostic code,
outbound only through the Bridge, keys only in the vault).

### My question is not here

Open an issue and ask. Good questions end up in this FAQ.
