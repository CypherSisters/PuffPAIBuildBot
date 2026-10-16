# Getting started

From a clean computer to a bot you can talk to. The README is the short
version; this page shows every step, with a real wizard run.

Two things are required: **Python 3.11+** and **Ollama**, which runs the AI
model. The installer checks for both, and offers to get what is missing. Everything else (the web page, Home
Assistant, voice) is optional and can be added later.

---

## 1. Install

**Windows:** download the ZIP, extract it, and double-click `install.cmd`. It
checks for Python, Ollama and Node.js, offers to install any that are missing
(asking first each time), builds the web page, and ends by asking where you
want to set up your bot.

**Linux:** `bash install.sh` in the folder you cloned. Same checks, same
question at the end.

The per-OS pages carry the options and the snags we actually hit:

- **[INSTALL_WINDOWS.md](INSTALL_WINDOWS.md)**: Windows (`install.cmd`, which
  runs `install.ps1`)
- **[INSTALL_LINUX.md](INSTALL_LINUX.md)**: Linux (`bash install.sh`)
- **[INSTALL_MAC.md](INSTALL_MAC.md)**: a Mac (the same `bash install.sh`, then
  double-click files to start and stop)

The README's "What computer you need" box says how much memory to have. You do
not need to download an AI model first: the wizard shows which one fits this
computer and downloads it when you say yes. If you want to pull one yourself,
`ollama pull qwen2.5:7b-instruct` is a good fit for 16 GB. Ollama listens on
`http://localhost:11434` unless you told it otherwise, and the wizard also
looks at port 11435 and at `OLLAMA_HOST`.

At the end the installer asks:

```
Where do you want to set up your bot?
  1) In your web browser (opens now)
  2) Right here in this window
```

Both ask the same questions. If you skip it, set up later with
**Set up BuildBot.cmd** (Windows), `./run-setup.sh` (Linux) or
`puffpai setup`, or open the web page with **Start BuildBot.cmd**,
`./start-bot.sh` or `puffpai start` and it takes you to the wizard.

By hand, without the installer:

```bash
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\Activate.ps1
pip install -e .
```

`pip install -e .` installs everything a bot needs to run and chat in a
terminal. Optional extras:

| Install | Adds |
|---|---|
| `pip install -e ".[voice]"` | offline voice: mic I/O, Vosk STT, Piper TTS |
| `pip install -e ".[dev]"` | pytest, black, flake8, mypy (contributors); needed before `py -m pytest` will run the suite |
| `pip install -e ".[desktop]"` | PyInstaller, to build a standalone binary |

---

## 2. Make your bot

The wizard starts by showing what it found on this computer, then asks three
questions, and shows one plan.

**Let's see what we're working with here.** The first screen, before any
question: the memory, the processor, the graphics card and its own memory,
the free disk space, whether the internet answers, and whether Ollama (the
free program that runs the thinking model) answers and which models it
already has. Anything it could not measure says "not measured". Then the
thinking model it recommends for this computer, its download size, and one
sentence on why (tied to the memory it found), what it is good for, and the
other models that fit, smaller and faster or bigger and slower. Models that
are too big for this computer are named and never offered. Press Enter to
use the recommended one, or type 2 (**Change**) to pick another. The plan's
"How it thinks" line uses the one you pick.

Then the three questions:

1. **What kind of bot do you want?** Say it in your own words.
2. **What should we call it?** It offers a name made from your words.
3. **How old are you?** This only changes the wizard's wording: under 13 gets
   shorter sentences. Only a rough age group is kept, never the number, and
   the finished bot never reads it. Press Enter to skip.

Then it shows the plan. Nothing has been downloaded or turned on yet.
Its parts, in order:

- **How it thinks.** The AI model, and whether it is already on this computer
  or has to be downloaded (and how big it is). The team of seven helpers.
- **Updates it can send you.** A daily update, for example.
- **What it will know.** Books from the Kiwix library, a Wikipedia book made
  from your words, Project Gutenberg, OpenStax. A line that starts "Not
  included (you can add it)" was found but is not downloaded unless you
  switch it in; it says its size and why it is not in (a big download, free
  for personal use only, a general encyclopedia, a whole book you pick
  yourself, or it matched your words more loosely). A book that only shares a
  word with your goal in another sense ("public space" for a space bot) is not
  listed at all. Books that matched less well are not on the plan; you can
  look through them later on the Knowledge page's Find more knowledge panel.
- **Live info it can show you.** Live cards from free services that answered a
  test request just now, each with where the data comes from, what leaves your
  computer, and its license.
- **Websites and services it may use.** Weather, web search, and the search
  services that need a free key.
- **Your private stuff.** The vault, and what happens to personal details.
- **Your gadgets.** Home Assistant, if you have one. Other gadgets are listed as
  "not connected yet".
- **How it looks.** Colours picked from your words. You can change them later.

Under the plan: the total download size, and every website your computer will
talk to. Press Enter for **Looks good**, or type 2 for **Change something**.
Change something numbers every line: type a number to switch that line in or
out, choose a different brain, or change how your bot uses the internet (only
the services on the plan, or not at all). Press Enter when you are done and
the plan is shown again.

After **Looks good**:

- **The vault PIN.** Use at least 10 digits, or a mix of letters and numbers.
  Easy ones like 1234567890 are turned down.
- **The recovery code.** Shown once. It is the only way back into your vault if
  you forget the PIN. Write it on paper and type it back. If you cannot write
  it down right now, type `skip`; the code still works.
- **The build.** Downloads what you said yes to and sets up the live cards and
  updates.
- **How to start it.** The closing screen: what was added, what is still
  learning, what is still missing, and your first pairing code.

Books you said yes to are fetched and learned in the background the first time
your bot runs. You can chat while they learn.

If the wizard stops part-way (the window closed, the power went), the next run
offers to pick up where it stopped or start over. Picking up checks the vault
and the brain again before it skips them.

### A real run

This is `puffpai setup` on a Windows PC with 64 GB of memory and
`qwen2.5:7b-instruct` already installed, for a knitting bot. The answers were
Enter to use the recommended thinking model, the goal, Enter for the name,
Enter for the age, Enter for Looks good, and a PIN. The PIN does not show on
screen. The recovery code and the pairing code are replaced with X's.

```
BuildBot, from PuffPAI version 0.1.0-alpha

Welcome to BuildBot. Let's make your bot together.
First I will show you what this computer has and which thinking model fits it.
Then three short questions, and one plan to say yes to.

Checking this computer...


Let's see what we're working with here
--------------------------------------
Before any questions, here is what BuildBot found on this computer. Nothing has
been downloaded or changed.

  Memory: 63.7 GB
  Processor: 13th Gen Intel(R) Core(TM) i7-13850HX, 28 cores
  Graphics card: NVIDIA RTX 2000 Ada Generation Laptop GPU, with 8 GB of its
    own memory
  Free disk space: 34 GB free of 473.9 GB
  The internet: reachable, so downloads can happen while we build
  Ollama: answers at http://127.0.0.1:11435, with 4 models already:
    llama3.2:1b, qwen2.5:7b-instruct, dolphin-llama3:8b, gemma3:4b

The thinking model I recommend for this computer: Qwen2.5 7B
(qwen2.5:7b-instruct), already on this computer.
  It is already on this computer, and 63.7 GB of memory is plenty for it, so
  nothing needs downloading.
  Good for: a good everyday helper, best with one request at a time.

Other thinking models that fit:
  - Qwen3 30B (qwen3:30b-a3b): 19 GB download, fits well, bigger and slower;
    handles requests with several steps, asked in your own words
  - Qwen2.5 3B (qwen2.5:3b-instruct): 2 GB download, fits well, smaller and
    faster; quick answers, one simple request at a time
  - Llama 3.2 3B (llama3.2:3b): 2 GB download, fits well, smaller and faster;
    quick answers, one simple request at a time
  - Qwen2.5 1.5B (qwen2.5:1.5b-instruct): 1 GB download, fits well, smaller and
    faster; short, simple answers only
  - Llama 3.2 1B (llama3.2:1b): already on this computer, fits well, smaller
    and faster; short, simple answers only

  1) Use this one
  2) Change
  Press Enter to use Qwen2.5 7B (qwen2.5:7b-instruct), or type 2 to pick another.
  >

Your bot
--------

What kind of bot do you want? Say it in your own words.
  For example: a garden helper for my vegetable beds, or a bot that knows every
  dinosaur.
  Press Enter to skip.
  > A knitting and crochet buddy with patterns and tips

Its name
--------

What should we call it?
  Press Enter to use KnittingBot, or type a name you like.
  >

How old are you? (This only changes how I word my questions. Press Enter to
skip.)
  Only a rough age group is kept, never the number.
  >

This computer has 63.7 GB of memory and 34 GB of free space.
The internet is reachable, so downloads can happen while we build.


Here is the plan for KnittingBot
--------------------------------
Nothing has been downloaded or turned on yet. Read it, then say "Looks good" or
change something.

How it thinks
  1. The brain: qwen2.5:7b-instruct
     Already on this computer, so nothing is downloaded. This brain does one
     job at a time. Ask for one thing per message and it will do it well; a
     chain of tasks in one breath will lose a step.
  A team of 7 helpers works inside KnittingBot
  Meet them later on the Agent Team page.

Updates it can send you
  2. Not included (you can add it): A daily knitting update at 8 am - it starts
     off; switch it on if you want it

What it will know
  3. Arts & Crafts Q&A (about 55 MB)
     Stack Exchange Q&A for artists and crafters. About 4,950 pages, read
     offline. Download: 55 MB, from lb.download.kiwix.org. License
     CC-BY-SA-4.0, free to use, even for business.
  4. Not included (you can add it): Wikipedia (short version) - about 13.4 GB,
     a big download, so it waits for your yes
  5. Not included (you can add it): Vikidia (text only) - about 10 MB, a
     general encyclopedia, not about your subject
  6. Not included (you can add it): Handbook of Wool Knitting and Crochet - up
     to 25 MB, whole books like this are added only when you pick them
  7. Not included (you can add it): The New Guide to Knitting & Crochet - up to
     25 MB, whole books like this are added only when you pick them
  8. Wikipedia articles about knitting, crochet and pattern (up to 25 MB)
     Up to 300 Wikipedia articles about knitting, crochet and pattern, picked
     by their titles and first paragraphs and saved on this computer as one
     book. Fetched once from en.wikipedia.org (up to 25 MB of text); it sends
     only the words knitting, crochet, pattern and the article names.

Live info it can show you
  9. Live: Internet Archive search
     Search old books, magazines, recordings and videos kept by the Internet
     Archive. Comes from archive.org. What leaves your computer: a request for
     the data and your internet address. License: set per item; many are public
     domain or Creative Commons. Right now it shows: Knitting Magazines...

Websites and services it may use
  10. Not included (you can add it): Weather forecast - it reaches an outside
      service, so it is off until you say yes
  11. Not included (you can add it): Web search - it reaches an outside
      service, so it is off until you say yes
  12. Not included (you can add it): Search the web with Brave Search: needs a
      free key
  13. Not included (you can add it): Search the web with Marginalia Search:
      needs a free key (personal use only)

Your private stuff
  Kept in a locked vault on this computer
  Only your PIN opens it, and the PIN never leaves this computer. You choose it
  right after you say yes.
  Keep personal details out of saved chats
  Phone numbers, home addresses, birthdays, ID and card numbers are taken out
  before a chat is saved and put in the vault, even while it is locked. Saved
  chats are not encrypted.
  The AI model runs on this computer
  What you type stays on this computer.

Your gadgets
  Nothing here yet.

14. How KnittingBot uses the internet: Only the services listed above

Total to download: 80 MB.
What leaves your computer: only what you say yes to. When you press Looks
good: requests to archive.org, en.wikipedia.org, lb.download.kiwix.org. After
KnittingBot is made, these stay on: Book library (archive.org, Wikipedia, Kiwix).
Every other outside service is off until you switch it on.

  1) Looks good
  2) Change something
  Press Enter for "Looks good", or type 2 to change something.
  >

Great. Building KnittingBot now.

Your private stuff
------------------
What this is: KnittingBot keeps your private things in a locked vault on this
  computer. You choose the PIN. It never leaves this computer and is never
  saved as plain text.

Choose a vault PIN
  Use at least 10 digits, or a mix of letters and numbers. Easy ones like
  1234567890 are turned down.
  What you type stays hidden. Press Enter to skip.
  >

Type the same PIN again
  Both have to match, or no vault is made.
  What you type stays hidden. Press Enter to skip.
  >

  Write this code down before you go any further.
  
  This code is the only way back into your vault if you forget your PIN.
  We cannot recover it for you. It is not sent anywhere and no copy of it is
  kept.
  There is no reset email, because there is no us. Nobody is holding a spare.
  Write it on paper and keep the paper where you would keep a spare house key.
  You will be asked to type it back on the next screen. That is on purpose.
  This code is shown once. Once you leave this screen it is gone from the
  screen and from memory, and we cannot show it to you again.

      XXXXX-XXXXX-XXXXX-XXXXX-XXXXX

  If you cannot write it down right now, press Enter or type 'skip'.
  The code keeps working either way.
  Type the recovery code back in. Capitals and dashes do not matter.
  > skip

  Okay. That code still works, so keep it somewhere safe.
  Nobody typed it back, so the build record says it was not checked.
  If you lose it and forget the PIN, what is inside is gone. To make a
  new code later, open your vault on the dashboard's Vault page and
  press New recovery code.

Now building KnittingBot. Big downloads can take a while; leave this window
open.

How to start it
---------------
Start it with puffpai start; 2 things are still missing.
Where this build got to: Setup finished. Your bot can start, but 2 things are
  still missing - see below.
Open your bot: puffpai start
It opens: http://127.0.0.1:3003/ in your web browser
Let your first device in: the first time, the page asks for a pairing code;
  puffpai start shows a fresh one each time it starts.
Added for you: Wikipedia articles about knitting, crochet and pattern (up to 25
  MB): fetched and learned in the background the first time your bot runs. It
  can answer questions a few seconds after it arrives.
  Internet Archive search: a live card on your dashboard. Right now it shows:
  Knitting Magazines; Knitting Reference Library; Victorian knitting Manuals.
Learning in the background: Wikipedia articles about knitting, crochet and
  pattern (up to 25 MB): waiting to be fetched. It starts in the background
  while your bot is running.
  Books you said yes to are fetched the first time you open your bot with
  puffpai start.
Your bot says: Hi! I'm KnittingBot. Ask me anything about knitting, or ask
  "what can you do?" to see everything I have for you.
Proof it works: Of 5 checks: 3 passed, 2 found something missing. Once it is
  running, ask it to prove itself: `puffpai prove`
Still to do: Arts & Crafts Q&A is downloaded and not searchable yet. The bytes
  are on disk and the knowledge store has no chunks from them.
  To fix it, run: puffpai knowledge sync
Still to do: Arts & Crafts Q&A is downloaded but not searchable yet. Downloaded
  and waiting. It is indexed on the next start, or now with the command.
  To fix it, run: puffpai knowledge sync


Your pairing code for the first device: XXXX-XXXX. It works until 6:23 pm;
after that, puffpai start shows a fresh one.
```

The "Still to do" lines are honest: the book was downloaded, and it becomes
searchable the next time the bot starts (or right away with
`puffpai knowledge sync`).

**Scripted runs.** `puffpai setup --yes` asks nothing. Nobody is there to say
yes, so only what the command line names is downloaded or turned on:
`--goal`, `--name`, `--age`, `--model`, `--download-model` (download the brain
it picks), `--fetch-source ID --accept-source-size` (a book), `--vault-pin`
(test use only; it can end up in your shell history), `--skip-ha`. With no
model installed, a `--yes` run picks a model that does one job at a time well
rather than the biggest one, so an unattended run does not start a 19 GB
download. `puffpai setup --help` lists every option.

---

## 3. Open it and pair a device

```bash
puffpai start
```

or double-click **Start BuildBot.cmd** (or the Start file with your bot's
name), or `./start-bot.sh` on Linux. It starts the bot and its web page on
this computer only, waits until both answer, and opens your browser:

```
Opening your bot on this computer.
  Starting your bot...
  Your bot is ready.
  Checking the web page is up to date (if it rebuilds, that takes a few minutes).
  Starting your bot's web page...
  Your bot's web page is ready.

Your bot is running on this computer.
Its web page: http://127.0.0.1:3330/
The first time, the page asks for a pairing code. Type XXXX-XXXX (it works until 6:12 pm).
Open that address in your web browser.
To stop it, press Ctrl+C here, or run puffpai stop in another window.
```

(That run used `--port 8330 --dashboard-port 3330` and `--no-browser`; the
default web page port is 3003. The code is replaced with X's.)

**Pairing.** Your bot's web page only answers devices you have let in. Type the
code on the pairing screen and that device stays paired for a year. Codes last
an hour; `puffpai start` shows a fresh one each time it starts, and
`puffpai pair`
prints the current one on the computer running the bot. A paired device can
see and revoke every other one at `/pair`. More in [PAIRING.md](PAIRING.md).

The web page listens on this computer only (127.0.0.1). To open it from a
phone, the web page has to listen on your home network too:
`npm run start:lan` in `dashboard` starts it that way. Pairing is what keeps
other devices on your network out.

Stop it with Ctrl+C in that window, **Stop BuildBot.cmd**, `./stop-bot.sh`, or
`puffpai stop`. Stop only stops what Start started, by the process numbers it
wrote down.

---

## 4. Your first chat

The home page lists things to try, built from what your bot really has. Open
**Chat** and type. Good first questions:

- "What can you do?" It lists what it has now, and says it only uses the
  outside websites you said yes to.
- A question about its subject.
- "Are you done learning?"

This is the garden bot above answering "what can you do?":

```
I run on this computer, and I learned most of what I know before I was built, so I can be out of date.
Things I can do now:
- send you messages on a schedule, like a daily update or a reminder, on your dashboard while your bot runs.
- use the GardenBot app: its own page.
- show live iNaturalist on a card.
- show live Open-Meteo weather on a card.
- say a reminder you wrote, at a time you pick.
- and 5 more small things.
I only use the outside websites you said yes to. The What leaves your computer page lists every request I make.

Would you like a daily garden update at 8 am? It would have the date and the latest from iNaturalist and Open-Meteo weather. It stays on this computer; nothing new goes online to make it. Just say yes or no.
```

**Not connected yet.** If you ask for something your bot has no way to do (a
gadget it cannot reach, a service that is switched off), it says it is not
connected yet, and the home page lists those under **Not connected yet**. It
does not make something up.

**Daily updates.** Your bot may offer one in chat, as above, or you may have
said yes on the plan. Updates show on a dashboard card, as "While you were
away", and out loud if you use voice and switched that on. In a terminal:

```bash
puffpai routines                    # what runs on its own
puffpai routines on <name>          # or off, or remove
puffpai routines add reminder --at 21:00 --say "Water the plants"
puffpai routines speak on           # say updates out loud
puffpai home set "Dayton, Ohio"     # your town, for weather in updates
```

`puffpai home set` looks the town up once (it asks first) and keeps only a
rough position.

**Prove it.** `puffpai prove` asks the running bot to show the knowledge it
really has and give up to three real answers, then asks whether you can see
them.

`puffpai chat` talks to your bot in the terminal, with no web page.
`puffpai chat --once "your question"` asks one question and exits.

---

## 5. Add more later

One loop does this everywhere: it looks in the lists saved on this computer,
shows numbered lines (books, live cards, updates), and adds only the ones you
pick.

- **Chat:** "add tomato diseases to you", "can you learn about bird songs",
  "add me some cross stitch tips too". Say "yes", a number like "just 1", or
  "no". Nothing you type in chat goes online by itself: if it could look
  online too, it first asks "Look online too? It sends only these words: ..."
  and waits for you to say "look online". When nothing is found on this
  computer it can offer a Wikipedia book made from your own words.
- **The Add more page:** type what you want and press **Find**. Tick what you
  want and press **Add these**. **Look online too** does the online look, and
  **Take it out** removes something you added.
- **Terminal:**

  ```bash
  puffpai add "tomato diseases"        # asks which lines to add
  puffpai add "tomato diseases" --online
  puffpai add --list                   # what was added since the build
  puffpai add --take-out 2             # take one back out
  ```

If something you add needs an outside website that is switched off, you are
asked whether to switch it on. From a paired device that needs no PIN.

**Knowledge.** The Knowledge page shows every book and how far it has learned.
Its **Find more knowledge** panel searches the book libraries, and **Add to
library** takes a file of your own. My Documents lists the files you added.

```bash
puffpai sources search tomato diseases     # look for books (--offline: only the saved list)
puffpai sources add <id>                   # shows the size and license, then asks
puffpai knowledge status                   # how far each book has learned
puffpai knowledge deeper <book>            # learn one book fully (--stop to stop)
puffpai knowledge remove <book>
```

**Looks.** The Looks page, or:

```bash
puffpai looks find "outer space"    # nothing changes until you pick one
puffpai looks use 1
puffpai looks undo                  # go back one step
puffpai looks reset                 # back to your bot's own look
```

Looks are colours only. Code checks that every one is easy to read, and
picking one sends nothing outside.

**Sign-up keys and web search.** Web search is off until you switch it on,
on the plan or on What leaves your computer. Then your bot searches Wikipedia
when you ask it to ("search the web for ..."), with no key. For the whole web,
add a free key on the **Sign-up keys** page, or:

```bash
puffpai keys                          # Brave Search and Marginalia Search
puffpai keys add brave_search         # asks for the key, then your vault PIN
puffpai keys test brave_search
```

Brave is tried first; Brave asks for a payment card when you sign up.
Marginalia needs no card and is free for personal use only. Wikipedia is the
backup. A key goes straight into your vault; your bot keeps only a note of
which key to use. A key given with a payment card never works while the vault
is locked. Before a search goes out, private details are taken out of the
words.

---

## 6. Privacy and what leaves your computer

- **What you type stays on this computer.** The AI model runs here.
- **Personal details.** Phone numbers, home addresses, birthdays, ID and card
  numbers are taken out of a chat before it is saved and put in the vault. The
  vault is locked most of the time, so they go into a sealed inbox that only
  the vault can open, and are filed when you next unlock it on the Vault page.
  Saved chats themselves are not encrypted.
- **Keys pasted in the wrong place** (chat, any text box) are refused before
  they are saved, logged or sent, and your bot tells you to use the Sign-up
  keys page.
- **What leaves your computer.** The page lists every outside service your
  build has: what it is for, what leaves, where it goes, and when it was last
  used. Switching one off is one tap, and it stays off. Switching one on shows
  you one sentence about what will leave, and only the paired device that saw
  it can say yes, once, within ten minutes. There is no PIN for this: being
  paired is the gate. The log of every request is kept for 30 days on this
  computer and never records what you searched for or typed.

  ```bash
  puffpai outside                # the list, with a number for each
  puffpai outside off 12
  puffpai outside on 6           # prints the sentence, then asks
  puffpai outside log            # every request in the last day (--hours 72)
  ```

  Things on your home network, like Home Assistant, are not listed: that
  traffic stays in your home. Ollama's own model downloads are listed, but
  BuildBot cannot switch them; Ollama makes them itself.
- **Each bot has its own keys.** Every bot on one computer has its own API key
  and pairing code. A device let into one bot is not let into another.

---

## 7. Home Assistant (optional)

If you run [Home Assistant](https://www.home-assistant.io), switch it in under
**Your gadgets** on the plan (Change something, then its number). The wizard
then asks for the hub's address (default `http://homeassistant.local:8123`) and
a long-lived access token: in Home Assistant open your profile, then Security,
and make one. It is kept in this computer's settings, and your bot talks to the
hub on your home network only. Adding it later? Run `puffpai setup` again.

New Home Assistant devices never act on their own. They wait as **pending**
until you approve them:

```bash
puffpai devices scan               # look for new devices
puffpai devices list               # every device and its status
puffpai devices approve <entity_id>
puffpai devices deny <entity_id>
```

---

## 8. Voice (optional)

The voice pipeline is entirely offline: mic → wake word → Vosk (speech to
text) → the AI model → Piper (text to speech) → speaker. It does **not** need
Home Assistant.

1. Install the voice extra: `pip install -e ".[voice]"` (or the installer's
   `-WithVoice` / `--with-voice`).
2. Download the models and put them where the settings expect them (the paths
   are under `models` in `config/puffpai_config.json`):
   - **Vosk:** from [alphacephei.com/vosk/models](https://alphacephei.com/vosk/models)
     (for example `vosk-model-small-en-us`), unzipped into the `vosk_model_dir`
     folder.
   - **Piper:** a `.onnx` voice from
     [the Piper voices](https://github.com/rhasspy/piper/blob/master/VOICES.md),
     pointed to by `piper_model_path`.
3. Check what is wired up:
   ```bash
   puffpai voice test              # reports what is installed, the models, the mic
   puffpai voice test --mic-test   # also records a short mic sample
   ```

Anything missing falls back to stand-in engines, so your bot still works by
text, and `puffpai voice test` says what to install. The **Talk** page is the web
page's voice screen.

---

## 9. The desktop window (optional)

`puffpai ui` opens a desktop window instead of the web page. It needs a display
and Tk (`sudo apt install -y python3-tk` on Debian/Ubuntu; the python.org
Windows build and Homebrew's Python include it). It uses CustomTkinter if it is
installed and plain Tk otherwise.

---

## 10. For developers: the two halves by hand

`puffpai start` is the way to run a bot. To run the two halves yourself:

```bash
# Terminal 1: the bot (from the repo root)
puffpai serve --port 8000

# Terminal 2: the web page
cd dashboard && npm install && npm run build && npm run start
```

On Windows PowerShell 5.1, `&&` does not work; use
`cd dashboard; npm install; npm run build; npm run start`.

The web page needs the bot's API key. `puffpai start` hands it over for you. By
hand, copy `dashboard/.env.example` to `dashboard/.env.local` and set
`PUFFPAI_API_KEY` to what `puffpai apikey` prints (and `PUFFPAI_BACKEND_URL`
if the bot is not on port 8000). An explicitly set `PUFFPAI_API_KEY` wins;
otherwise the bot uses its own entry in your OS keyring (one entry per bot,
named after its state folder), or `api_key.txt` in the state folder. Reading a
key file directly can give you a stale key; `puffpai apikey` always prints the
right one.

The browser never sees the key: it calls a same-origin proxy (`/api/pai/...`)
and the Next.js server attaches it. Keep `NEXT_PUBLIC_API_URL=/api/pai` (the
default).

`npm run dev` (port 3003) is fine for working on the web page itself.
`npm run build` then `npm run start` is the production path.

`puffpai serve` also speaks the OpenAI/Ollama-compatible chat protocol, for
the Home Assistant Assist pipeline, and requires the API key on every
non-public request.

---

## Where things live

`puffpai where` prints all of it for the copy you are in (never the key itself).

- **Settings:** `config/puffpai_config.json` in the data folder. Your bot's
  name is `identity.name` there; the Profile page, the wizard, chat and the
  web page all read that one field. Home Assistant's address and token are in
  `config/home_assistant_config.json` beside it. `config/build_manifest.json`
  and `config/wizard_run.json` are the record of the build.
- **The vault:** `vault_data` in the data folder.
- **Books:** `knowledge_packs` in the data folder.
- **The data folder** is the checkout unless `PUFFPAI_DATA_DIR` says otherwise.
- **State** (pairing, the API key file, logs, saved chats, live cards, looks,
  the outside log): `PUFFPAI_STATE_DIR`, else `state` inside
  `PUFFPAI_DATA_DIR` when that is set, else `%LOCALAPPDATA%\PuffPAI` on
  Windows or `~/.local/state/puffpai` on Linux. Logs are in `logs` there.

---

## "My bot does not seem to know its own name or my lists"

Your bot's name, what you said it was for, and the current contents of your
lists are put into the model's instructions on every question. To read exactly
what the model was told:

```bash
# Windows PowerShell
$env:PUFFPAI_DEBUG_PROMPT = "1"; puffpai serve
# macOS / Linux
PUFFPAI_DEBUG_PROMPT=1 puffpai serve
```

Ask one question, then open `logs/last_system_prompt.txt` in your state
folder (`%LOCALAPPDATA%\PuffPAI\logs\` on Windows, or
`$PUFFPAI_STATE_DIR/logs/` if you set one). It is overwritten on every question.

> **That file contains your own data:** your bot's name, the goal you typed,
> and every row of every list it keeps, in plain text. The switch is off by
> default for that reason. Turn it off and delete the file when you are done,
> and do not paste the contents into a bug report without reading it first.

Stuck? See [TROUBLESHOOTING.md](TROUBLESHOOTING.md).
