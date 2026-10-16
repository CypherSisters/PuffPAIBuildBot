# Troubleshooting

Short answers to the things that actually go wrong. If your problem isn't here,
open an issue with your OS, Python version, and the error text. `puffpai where`
shows which folders, ports and Ollama address the copy you are in uses; paste
that too (it never prints the key).

---

## The web page

**It asks for a pairing code and I don't have one.** The window that started
your bot prints it. Codes last an hour, and `puffpai start` (or the Start file)
prints a fresh one each time it starts. `puffpai pair` prints the current one
on the computer running the bot, and until when it works. See
[PAIRING.md](PAIRING.md).

**"Something else on this computer is already using port 8000"** (or 3003).
Another program, maybe another bot, has that port. Stop it, or pick other
numbers: `puffpai start --port 8100 --dashboard-port 3100`, or re-run the
installer with `-Port 8100 -DashboardPort 3100` (`--port`/`--dashboard-port` on
Linux) and use the new Start file.

**The web page was not built.** Node.js 20.19+ was missing or the build failed;
the installer shows the last lines of its log. Install what is missing and run
the installer again. `puffpai start` also builds the web page when it has to
(that takes a few minutes). You can still set up and chat without it:
`puffpai setup`, then `puffpai chat`.

**401s, or a web page that shows nothing.** The bot and the web page hold
different keys. Start them together with `puffpai start` or the Start file,
which hands the key over. If you run the two halves by hand, see
[GETTING_STARTED.md](GETTING_STARTED.md) section 10.

---

## Ollama not reachable, or the model is missing

**Symptom:** chat says there is no brain to answer with.

If the model was never downloaded, the chat answer is:

```
That needs the language model, which is not reachable yet: This build is set up to use the model 'qwen2.5:7b-instruct', which is not installed yet, so there is no brain to answer with. Install it with:
    ollama pull qwen2.5:7b-instruct
then ask again.
```

Run the `ollama pull` line exactly as printed, then check with `ollama list`.
The name must match exactly, tag included. On an Ollama that is not at the
usual address, the line carries the address, so you do not download into an
Ollama your bot never reads from. This happens when you said no to the brain
on the plan, or ran `puffpai setup --yes` without `--download-model`.

If nothing answers at the Ollama address at all, chat says so and names it.
Start the Ollama app or service (`ollama list` should work), or point your bot
at the right address: `puffpai setup --ollama-url http://<host>:<port>`, or set
`ollama.url` in `config/puffpai_config.json`. `PUFFPAI_OLLAMA_URL` overrides
it for one run. Ollama listens on `http://localhost:11434` unless you told it
otherwise; the wizard also looks at `OLLAMA_HOST`, `127.0.0.1` and port 11435
before it asks you.

**Over HTTP:** `POST /chat/send` answers HTTP **503** with the same sentence
in `message`, and `error.code` is `model_not_installed`, `unreachable`, or
`ollama_error`. The failed turn is not written into your conversation history.
`GET /system/status` has an `inference` block whose `status` is `connected`,
`model_missing` or `unreachable`; `connected` means that exact model
answered.

---

## A book is slow, stuck, or failed

- **Slow is expected for big books.** Books learn in the background: fast word
  search first, then meaning search at half the CPU, pausing while someone
  talks. A small book can answer within a minute or two; a full Wikipedia
  takes hours. Chat works the whole time. Watch progress on the Knowledge page
  or with `puffpai knowledge status`.
- **"Waiting to be fetched."** Books you said yes to are fetched the first time
  your bot runs. Start it with `puffpai start` (or the Start file) and leave it
  running.
- **A download stopped part-way.** Stop and start your bot; the book is
  fetched again. For a `.zim` file you downloaded yourself, download it again
  from [library.kiwix.org](https://library.kiwix.org), replace the file, and
  run `puffpai knowledge sync`.
- **A book that is off topic.** Matching is weakest for crafts words. Take it
  out with `puffpai knowledge remove <book>`, or `puffpai add --take-out <n>`
  if you added it after the build.

---

## "Not connected yet"

Your bot says this when you ask for something it has no way to do. Two common
reasons:

- **A service is switched off.** Open **What leaves your computer** and switch
  it on (from a paired device), or `puffpai outside on <n>` in a terminal. The
  closing screen's "Still to do" line names these too.
- **A gadget.** Home Assistant is the one hub BuildBot can connect today. Other
  gadgets (a smart telescope, a record player) are listed as not connected
  yet. They are not a fault on your side.

---

## Web search does nothing

Web search starts switched off. Switch **Web search** on under What leaves
your computer, then ask in your own words: "search the web for ...". Your
bot never searches on its own. With a Brave or Marginalia key added on the
Sign-up keys page, those are tried first. A key given with a payment card is
paused while the vault is locked, and your bot says so under the answer and
uses the next one.

---

## Home Assistant token invalid or hub unreachable

**Symptom:** device commands fail, or `puffpai devices scan` finds nothing or
reports an auth error.

- Make a new long-lived access token in Home Assistant (your profile, then
  Security) and run `puffpai setup` again to save it. Changing your Home
  Assistant password revokes old tokens.
- Check the address. The wizard's default is `http://homeassistant.local:8123`;
  if `.local` names don't work on your network, use the hub's IP address and
  port. Check you can open that address in a browser on the same computer.

---

## Voice components unavailable

**Symptom:** `puffpai voice test` reports missing pieces, or voice falls back
to text only.

Voice is an opt-in extra. Install it and its models:

- Dependencies: `pip install -e ".[voice]"` (adds sounddevice, Vosk, Piper).
- **Vosk model:** from
  [alphacephei.com/vosk/models](https://alphacephei.com/vosk/models), unzipped
  into the folder named by `models.vosk_model_dir` in your settings.
- **Piper voice:** a `.onnx` voice, with `models.piper_model_path` pointed at
  it.
- No microphone detected? `puffpai voice test --mic-test` records a short
  sample so you can check audio in and out. On Linux you may need PortAudio:
  `sudo apt install libportaudio2`.

`puffpai voice test` names exactly what is missing. Install those pieces and
run it again.

---

## Vault PIN rejected

Digits-only PINs need at least 10 digits; common sequences are refused with:
"That one shows up near the top of every published list of common choices,
which is the first place a guessing program looks. Almost anything else is a
better pick." Add a letter to a short digits-only PIN, or pick a less common
one.

---

## Vault PIN forgotten

**Use your recovery code.** The wizard showed it once, right after you chose
your PIN (the "Your private stuff" screen after **Looks good**), and asked you
to type it back. That code opens the vault on its own. The vault's key is
locked twice, once under the PIN and once under the recovery code, so either
one opens it. **The code is not shown again and is not stored anywhere.**

On the web page: open the Vault page and press **Forgot your PIN? Use your
recovery code**. Type the code and a new PIN (twice). In a terminal on the
computer running the bot: `puffpai vault recover`. It asks for the code and the
new PIN without showing them.

Using the code is a recovery, not a login: you set a new PIN at the same time,
the vault's contents are locked again under new keys, and you get a NEW
recovery code, shown once, to write down and type back. The code you used and
your old PIN stop working. What is in the vault stays, and personal details
the bot noticed while the vault was locked are put into it. The new PIN
follows the same rules as the one you chose in setup. After too many wrong
codes the page asks you to wait (a few seconds at first, never more than five
minutes).

If you recover in a terminal while the bot is running with the vault open, the
bot closes the vault; open it again on the Vault page with the new PIN. (For a
program: `POST /vault/recover` with `{"recovery_code": "...", "new_pin": "..."}`
and the API key.)

If your vault is open and you want a new code (you lost the paper, say), open
the Vault page and press **New recovery code**.

If you have lost both the PIN and the code, there is no way to open the vault.
That is by design: neither we, you, nor anyone who steals the disk can open it
without one of the two. To start over, delete `vault_data` in your data folder
(`puffpai where` shows it) and run `puffpai setup` to make a new vault. You lose
what was stored in it; everything else keeps working. `puffpai vault status`
shows the vault's state at any time.

---

## Desktop window won't open

**Symptom:** `puffpai ui` errors about `tkinter`, `_tkinter`, or
`customtkinter`, or nothing appears.

- It needs a display, so it won't run on a headless server. Use `puffpai chat`
  or the web page there.
- Tk must be installed. Linux: `sudo apt install python3-tk`. macOS: use
  Homebrew's Python (Tk included) or install `tcl-tk`. Windows: the python.org
  installer includes Tk; reinstall with the default options if it's missing.
- CustomTkinter is optional; without it the window falls back to plain Tk. To
  get the styled window: `pip install customtkinter`.
- If Tk imports but no window shows, check you have a graphical session (not
  SSH without X forwarding) and that `DISPLAY` is set on Linux.
