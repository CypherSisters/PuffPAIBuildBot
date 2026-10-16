# Install on a Mac

New: checked on every change by our automatic tests (a macOS 14 Apple silicon
machine runs the installer and the tests), not yet by many people. macOS 14
(Sonoma) or newer, which Ollama needs. Apple silicon is best; an Intel Mac works,
but the AI model runs on its processor only, so answers are slower. Reports are
welcome.

## Starting from nothing

You do not need Python, Node.js, Ollama, Homebrew, git, Docker or Apple's
developer tools first.

1. Download BuildBot's ZIP and double-click it. You get a folder in Downloads.
2. Open **Terminal** (Applications, then Utilities). Type `bash ` (with a
   space), drag **install.sh** from the folder into the window, press Return.
   If your Mac asks whether Terminal may use files in Downloads, click **OK**.
3. Setup lists what is missing and asks once:

   ```
   BuildBot needs these free programs, and this computer does not have them yet:
     - Python 3.12.15 (about 25 MB)
     - Node.js 24.21.0 (about 50 MB), for your bot's web page
     - Ollama 0.40.2 (about 170 MB), which runs the AI model
   ...
   Download them now? Type y for yes or n for no:
   ```

   Type `y`. No password is needed. Each download comes from its maker's own
   release page, and is checked against the exact copy BuildBot expects (its
   SHA-256, written in `scripts/install_tools.sh`) before anything is unpacked;
   Ollama must also carry Ollama's own Apple signature. They are kept in
   `~/Library/Application Support/PuffPAI-tools`, outside BuildBot's own folders.
4. Then it installs BuildBot's parts and builds its web page (a few minutes),
   and asks where to set up your bot: the web browser or the Terminal window.

## Starting and stopping

Setup writes **Start BuildBot.command** and **Stop BuildBot.command** into the
folder (and on your Desktop if you say yes). Double-click them in Finder. Start
opens a Terminal window, starts your bot, its web page and Ollama, and opens
your browser; you can close that window and your bot keeps running. Stop stops
all three. From Terminal: `./start-bot.sh` and `./stop-bot.sh`.

**The first time:** if your Mac says it cannot check who made the file, or that
it comes from an unidentified developer, right-click the file, pick **Open**,
then **Open**. On macOS 15 or newer: open **System Settings**, **Privacy &
Security**, scroll down and click **Open Anyway**. You only do this once per
file. (Setup makes these files on your Mac, so most Macs do not ask at all.)

If your Mac asks whether Python or Ollama may accept incoming connections, you
can click **Deny**: everything listens on this computer only (127.0.0.1).

## What happens to the AI model

Setup does not download a model. The wizard picks one that fits your Mac's
memory, says how big it is, and downloads it through Ollama when you say yes.
Ollama keeps models in `~/.ollama`.

## Where things are

- Your bot's private files and logs:
  `~/Library/Application Support/PuffPAI` (logs in its `logs` folder:
  `bot.log`, `web-page.log`, `ollama.log`, and the setup logs).
- The downloaded programs: `~/Library/Application Support/PuffPAI-tools`.
- `./puffpai where` prints every folder and port this copy uses.

## Removing it

`bash install.sh remove` takes BuildBot off (it asks first). The downloaded
programs are shared by every copy of BuildBot on this Mac and are not removed
with it: delete the `PuffPAI-tools` folder yourself when no copy needs it, and
`~/.ollama` for the models.

## Already have Python, Node.js or Ollama?

Setup uses your own when it is new enough (Python 3.11+, Node.js 22.12+, an
Ollama that is installed or answering), and downloads only what is missing.
The Ollama app counts: open it once before you start your bot. Apple's own
`/usr/bin/python3` is too old and is skipped without opening Apple's
"install developer tools" box.

## Options

The same as Linux ([INSTALL_LINUX.md](INSTALL_LINUX.md)), plus
`--yes-install-tools` (download what is missing without asking),
`--tools-dir PATH` and `--desktop-shortcuts`.
