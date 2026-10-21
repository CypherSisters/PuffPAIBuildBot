"""The console renderer: draws any card and the one summary, in plain words.

*CLI and dashboard are renderers of the same card object, neither
owns a question*. So there is nothing skill-shaped in this file. It knows the
field kinds in :mod:`puffpai.wizard.cards` and the summary shape in
:mod:`puffpai.wizard.proposal`, and nothing about Discover, Brain or the vault.

How it reads (so it is never hard to read or to see what is being chosen):

* Text is wrapped to the terminal at word boundaries, never cut mid-word.
* A choice is a numbered list of **labels**; the value behind a label is never
  printed. The default is said in words: "Press Enter for yes".
* No status lines, no bracketed ids, no capital-letter shouting, a blank line
  between blocks, and ASCII only, because a Windows console garbles anything
  else.

Two properties kept from before:

* **A secret is never echoed and never returned to the log.** ``secret_entry``
  reads with :func:`getpass.getpass` on a terminal.
* **Non-interactive accepts the proposal.** ``--yes`` returns no answers at
  all, which is "what you proposed is what I want".

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import getpass
import os
import re
import shutil
import sys
import textwrap
from typing import Any, Callable

from puffpai.wizard import cards, fixes, wording

#: Kept for callers that still import them. Nothing is fenced on screen any
#: more: a line of dashes around every question was one more thing to read.
BEGIN_MARK = "--- card ---"
END_MARK = "--- end card ---"

#: What the owner types after. On its own short line, under the question.
ANSWER_MARK = "  > "

#: Punctuation a Windows console cannot print, and the ASCII that means the
#: same thing. A card may carry any printable text -- it is also rendered in a
#: browser -- so the folding belongs here, at the console, and not in the card.
_ASCII_FOLD = str.maketrans({
    "—": "-", "–": "-", "‒": "-", "−": "-",
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"',
    "…": "...", "·": "-", "•": "-", " ": " ",
    "«": '"', "»": '"', "→": "->", "≥": ">=",
    "≤": "<=", "×": "x",
})

MIN_WIDTH = 40
MAX_WIDTH = 100
DEFAULT_WIDTH = 80

#: How many options a console prints at once in the standard wording.
MAX_SHOWN_OPTIONS = 12


def ascii_only(text: str) -> str:
    """Fold to ASCII. A non-ASCII byte garbles a Windows console, every time."""
    folded = str(text or "").translate(_ASCII_FOLD)
    if folded.isascii():
        return folded
    return folded.encode("ascii", "replace").decode("ascii")


def width() -> int:
    """How wide to wrap. ``COLUMNS`` if set, the terminal if there is one,
    else 80 -- so a transcript written to a file or a test is the same every
    time."""
    raw = os.environ.get("COLUMNS")
    value = 0
    if raw and raw.isdigit():
        value = int(raw)
    else:
        try:
            if sys.stdout is not None and sys.stdout.isatty():
                value = shutil.get_terminal_size((DEFAULT_WIDTH, 24)).columns
        except (AttributeError, ValueError, OSError):
            value = 0
    value = value or DEFAULT_WIDTH
    return max(MIN_WIDTH, min(MAX_WIDTH, value - 1))


def wrap(text: Any, indent: int = 0, hang: int | None = None,
         limit: int | None = None) -> list[str]:
    """Wrap at word boundaries only. A word longer than a line stays whole.

    *indent* is the first line's indent and *hang* the rest (defaults to the
    same). Explicit line breaks in *text* are kept as separate lines.
    """
    limit = limit or width()
    hang = indent if hang is None else hang
    out: list[str] = []
    for paragraph in ascii_only(str(text or "")).split("\n"):
        paragraph = " ".join(paragraph.split())
        if not paragraph:
            out.append("")
            continue
        out.extend(textwrap.wrap(
            paragraph, width=limit, initial_indent=" " * indent,
            subsequent_indent=" " * hang, break_long_words=False,
            break_on_hyphens=False) or [" " * indent])
        indent = hang
    return out


def say_line(text: str, *, out: Callable[[str], None] = print) -> None:
    """One plain line (wrapped) with a blank line before it."""
    out("")
    for line in wrap(text):
        out(line)


def strip_block(lines: list[str]) -> list[str]:
    """Every line outside the old card fences. Kept for older callers."""
    out: list[str] = []
    inside = False
    for line in lines:
        if line.strip() == BEGIN_MARK:
            inside = True
            continue
        if line.strip() == END_MARK:
            inside = False
            continue
        if not inside:
            out.append(line)
    return out


# -- a card -------------------------------------------------------------------

def _gb(size: float) -> str:
    """One unit convention for the whole wizard (``cards.format_bytes``)."""
    return cards.format_bytes(size) or f"{int(size)} bytes"


def _shown(field: dict) -> str:
    kind = field["kind"]
    value = field.get("proposed")
    if kind == "toggle":
        return "yes" if value else "no"
    if kind == "multi_choice":
        picked = [_option_label(field, v) for v in (value or ())]
        return ", ".join(picked) or "none"
    if kind == "choice" and value not in (None, ""):
        return _option_label(field, value)
    if kind == "secret_entry":
        return "not set"
    if value in (None, ""):
        return "not set"
    return str(value)


def _option_label(field: dict, value) -> str:
    """A picked value in the words its option shows, never its internal id."""
    for option in field.get("options") or ():
        if isinstance(option, dict) and option.get("value") == value:
            # "Offline while building -- what that means" -> its name only;
            # the full line is in the menu under it.
            label = str(option.get("label") or "").split(" -- ")[0].strip()
            return label or str(value)
    return str(value)


def _heading(title: str) -> list[str]:
    title = ascii_only(title)
    return [title, "-" * min(len(title), width())]


def card_lines(card: dict, *, interactive: bool = False,
               mode: str = wording.STANDARD) -> list[str]:
    """What the card says, before anything is asked. Pure, so it is testable.

    A card that asks something shows its questions at the prompt, so here it
    shows only its heading and what it states. A card nobody is answering
    (``--yes``) lists each question with what was taken, so a transcript still
    says what was decided.
    """
    lines: list[str] = [""]
    lines.extend(_heading(str(card["title"])))
    askable = [f for f in card.get("fields") or ()
               if f["kind"] not in cards.READ_ONLY_KINDS]
    if card.get("decided") and not askable:
        lines.extend(wrap(card["decided"]))
    estimate = card.get("estimate") or {}
    if estimate.get("bytes"):
        lines.extend(wrap(f"Download: {_gb(estimate['bytes'])}."))
    # A run of info fields is one quiet block: a label with nothing beside it
    # heads the lines under it, and a label repeated over a list is printed
    # once ("Suggested" over every line was a label nobody could answer).
    last_label, headed = "", False
    for field in card.get("fields") or ():
        if field["kind"] == "info":
            label = str(field.get("label") or "")
            current = str(field.get("current") or "")
            if label and not current:
                lines.extend(wrap(label))
                last_label, headed = label, True
                continue
            if headed or (label and label == last_label):
                lines.extend(wrap(current, 2))
            elif label:
                lines.extend(wrap(f"{label}: {current}", 0, 2))
            else:
                lines.extend(wrap(current))
            last_label = label or last_label
            continue
        last_label, headed = "", False
        if not interactive and _asked(card, field, {}):
            label = str(field["label"])
            join = " " if label[-1:] in "?.:!)" else ": "
            lines.extend(wrap(f"{label}{join}{_shown(field)}", 0, 2))
    for gap in card.get("gaps") or ():
        lines.extend(wrap(f"Still to do: {gap['what']} {gap['why']}".rstrip(),
                          0, 2))
        command = fixes.fix_line(gap)
        if command:
            lines.extend(wrap(f"To fix it, run: {command}", 2, 4))
        elif (gap.get("fix") or {}).get("kind") == "none":
            lines.extend(wrap("There is nothing to run for this yet.", 2, 4))
    return lines


def _choice_lines(options: list[dict], start: int = 0,
                  marks: dict | None = None) -> list[str]:
    lines = []
    for number, option in enumerate(options, start + 1):
        mark = (marks or {}).get(option["value"], "")
        lines.extend(wrap(f"{number}) {option['label']}{mark}", 2, 5))
    return lines


def _question_lines(field: dict, mode: str) -> list[str]:
    """The question, its help, its numbered choices and what Enter does."""
    lines = [""]
    lines.extend(wrap(field["label"]))
    if field.get("help"):
        lines.extend(wrap(field["help"], 2))
    kind = field["kind"]
    options = field.get("options") or []
    if kind == "choice":
        shown = _page(options, field.get("proposed") or field.get("current"),
                      mode)
        lines.extend(_choice_lines(shown))
        default = _label_of(options, field.get("proposed")
                            or field.get("current"))
        extra = " Type more to see other choices." \
            if len(shown) < len(options) else ""
        if field.get("free_text"):
            extra += " Or type your own."
        lines.extend(wrap((f"Type a number. Press Enter for {default}."
                           if default else "Type a number.") + extra, 2))
    elif kind == "multi_choice":
        chosen = set(field.get("proposed") or [])
        lines.extend(_choice_lines(
            options[:MAX_SHOWN_OPTIONS],
            marks={v: " (chosen)" for v in chosen}))
        lines.extend(wrap("Type the numbers you want, like 1, 3. Press Enter "
                          "to keep what is chosen.", 2))
    elif kind == "toggle":
        default = "yes" if field.get("proposed") else "no"
        lines.extend(wrap(f"Type yes or no. Press Enter for {default}.", 2))
    elif kind == "number":
        lines.extend(wrap(f"Type a number from {field['min']} to "
                          f"{field['max']}. Press Enter to keep "
                          f"{field.get('proposed')}.", 2))
    elif kind == "secret_entry":
        lines.extend(wrap("What you type stays hidden. Press Enter to skip.", 2))
    else:
        current = field.get("proposed") or field.get("current")
        if current and field["id"] != "name":
            lines.extend(wrap(f"Press Enter to keep: {current}", 2))
        elif not current and field["id"] not in ("age",):
            lines.extend(wrap("Press Enter to skip.", 2))
    return lines


def _label_of(options: list[dict], value: Any) -> str:
    for number, option in enumerate(options, 1):
        if option["value"] == value:
            return f"{number}) {option['label']}"
    return ""


def _page(options: list[dict], current: Any, mode: str,
          offset: int = 0) -> list[dict]:
    """The options shown at once: all of them (capped), or three for the
    simple wording."""
    if mode == wording.SIMPLE:
        return options[offset:offset + wording.SIMPLE_MAX_CHOICES]
    return options[:MAX_SHOWN_OPTIONS]


#: Cursor home (H), erase the screen (2J), then erase the scroll history (3J),
#: the order ``clear(1)`` uses. 3J goes LAST on purpose: some terminals (VTE,
#: and tmux with scroll-on-clear) push the visible screen into the history on
#: a 2J, so a 3J sent first would leave the code there.
CLEAR_SEQUENCE = "\x1b[H\x1b[2J\x1b[3J"

#: What a tmux pane id looks like ("%12"). Anything else in $TMUX_PANE is not
#: handed to tmux as a target.
_TMUX_PANE = re.compile(r"%\d{1,9}")

#: Where tmux is looked for: fixed places only, never PATH and never a bare
#: name (see :mod:`puffpai.security.host_programs`). None on Windows.
_TMUX_PLACES = ("/usr/bin/tmux", "/usr/local/bin/tmux", "/opt/homebrew/bin/tmux")


def _tmux_program() -> str | None:
    if os.name == "nt":
        return None
    for place in _TMUX_PLACES:
        if os.path.isfile(place) and os.access(place, os.X_OK):
            return place
    return None


def _windows_vt_on(stream: Any) -> bool:
    """Turn on escape-sequence handling for this console. False when it cannot.

    An old Windows console prints ``ESC[3J`` as junk unless asked to read it,
    so off Windows this is always True and on Windows the sequence is only
    written once the console has agreed to understand it.
    """
    if os.name != "nt":
        return True
    try:
        import ctypes
        import msvcrt
        handle = msvcrt.get_osfhandle(stream.fileno())
        kernel32 = ctypes.windll.kernel32
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        wanted = mode.value | 0x0004  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
        return wanted == mode.value or bool(kernel32.SetConsoleMode(handle, wanted))
    except Exception:
        return False


def clear_terminal(stream: Any = None, *, env: Any = None,
                   run: Callable[..., Any] | None = None,
                   tmux: Callable[[], str | None] | None = None) -> bool:
    """Clear the screen and its scroll history, when this is a terminal.

    Best effort, and it never raises: a terminal that ignores the request is
    left as it was, and the caller goes on. Nothing is written unless
    ``stream`` (stdout by default) is a terminal, so a pipe or a log file never
    gets the escape bytes. Under tmux the pane's own history is cleared too,
    with ``tmux clear-history``, because tmux keeps its scroll history itself.
    Returns True when the sequence was written.
    """
    stream = sys.stdout if stream is None else stream
    try:
        if stream is None or not stream.isatty():
            return False
    except Exception:
        return False
    wrote = False
    try:
        if _windows_vt_on(stream):
            stream.write(CLEAR_SEQUENCE)
            stream.flush()
            wrote = True
    except Exception:
        wrote = False
    env = os.environ if env is None else env
    try:
        if env.get("TMUX"):
            program = (tmux or _tmux_program)()
            if program:
                import subprocess
                from puffpai import procflags
                argv = [program, "clear-history"]
                pane = str(env.get("TMUX_PANE") or "")
                if _TMUX_PANE.fullmatch(pane):
                    argv += ["-t", pane]
                (run or subprocess.run)(
                    argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, timeout=3, check=False,
                    **procflags.hidden())
    except Exception:
        pass
    return wrote


class ConsoleShowOnce:
    """The console as a place a value can be shown once and never stored.

    No state. It is handed lines and prints them; it is handed a prompt and
    reads one line back, in the clear (``ask``) or without echo
    (``ask_secret``, for the recovery code typed back); and it can clear the
    screen once the code has done its job. It keeps no copy of anything, which
    is what makes it usable for a recovery code.
    """

    def __init__(self, *, out: Callable[[str], None] = print,
                 ask: Callable[[str], str | None] | None = None,
                 ask_secret: Callable[[str], str | None] | None = None,
                 clear: Callable[[], Any] | None = None) -> None:
        self._out = out
        self._ask = ask or _read_line
        self._ask_secret = ask_secret or _read_secret
        self._clear = clear or clear_terminal

    @staticmethod
    def _safe(text: Any) -> str:
        """Printable, capped, and still indented."""
        raw = ascii_only(text if isinstance(text, str) else str(text))
        clean = "".join(ch for ch in raw
                        if ch == " " or (ch.isprintable() and ch != "\x7f"))
        return clean[:cards.MAX_TEXT_CAP]

    def show(self, lines) -> None:
        self._out("")
        for line in lines or ():
            text = self._safe(line)
            indent = len(text) - len(text.lstrip(" "))
            if len(text) <= width():
                self._out(text)
                continue
            for part in wrap(text, indent):
                self._out(part)

    def ask(self, prompt: str) -> str | None:
        return self._ask(self._safe(prompt)[:cards.MAX_LABEL])

    def ask_secret(self, prompt: str) -> str | None:
        """One line read the way the PIN is read: no echo on a terminal."""
        return self._ask_secret(self._safe(prompt)[:cards.MAX_LABEL])

    def clear(self) -> None:
        """Clear the screen and scroll history, best effort. Never raises."""
        try:
            self._clear()
        except Exception:
            pass


# -- resume -------------------------------------------------------------------

#: The two answers the resume screen takes, and what a blank line means.
RESUME_CHOICES = ("continue", "restart")


def how_long_ago(seconds: float) -> str:
    """"12 minutes ago". Plain words, no library, no locale to get wrong."""
    seconds = max(0.0, float(seconds or 0))
    for size, unit in ((86400, "day"), (3600, "hour"), (60, "minute")):
        if seconds >= size:
            count = int(seconds // size)
            return f"{count} {unit}{'s' if count != 1 else ''} ago"
    return "a moment ago"


def resume_lines(summary: dict) -> list[str]:
    """The stopped-run screen, before anything is asked. Pure, so it is tested."""
    when = how_long_ago(summary.get("age_s"))
    stopped = cards.plain(summary.get("stopped_title"), cards.MAX_TITLE) \
        or "an unnamed step"
    lines = [""]
    lines.extend(wrap(f"A setup from {when} stopped at: {stopped}."))
    lines.extend(wrap("Pick up where it stopped, or start over?"))
    lines.append("")
    decided = [row for row in (summary.get("decided") or ())
               if isinstance(row, dict)]
    if decided:
        lines.append("Already answered:")
        for row in decided:
            title = cards.plain(row.get("title"), cards.MAX_TITLE) or "a step"
            lines.extend(wrap(f"- {title}", 2, 4))
    else:
        lines.extend(wrap("Nothing was answered before it stopped."))
    lines.append("")
    lines.extend(wrap("Picking up checks the vault and the brain on this "
                      "computer again, and asks again if they are not there."))
    lines.extend(wrap("Starting over keeps a copy of the old record and asks "
                      "everything from the top."))
    return [ascii_only(line) for line in lines]


def resume_screen(summary: dict, *, ask: Callable[[str], str | None] | None = None,
                  out: Callable[[str], None] = print) -> str:
    """Show the screen and return ``"continue"`` or ``"restart"``.

    **Fail closed on silence.** End-of-file, Ctrl-C and an answer this screen
    does not understand all mean *restart*: continuing skips questions.
    """
    reader = ask or _read_line
    for line in resume_lines(summary):
        out(line)
    raw = reader("  Type continue to pick up where it stopped, or press Enter "
                 "to start over.")
    answer = (raw or "").strip().lower()
    if answer in ("continue", "c", "resume", "yes", "y"):
        return "continue"
    return "restart"


# -- asking -------------------------------------------------------------------

def render(card: dict, *, non_interactive: bool = False,
           ask: Callable[[str], str] | None = None,
           ask_secret: Callable[[str], str] | None = None,
           out: Callable[[str], None] = print,
           mode: str = wording.STANDARD) -> dict[str, Any]:
    """Show *card*, collect answers, return only what the owner changed.

    Answering is opt-in per field: an empty line keeps what was proposed. A
    refused answer is said and asked once more; a second refusal keeps the
    proposal rather than looping at somebody who wants to move on.
    """
    card = cards.validate_card(card)
    for line in card_lines(card, interactive=not non_interactive, mode=mode):
        out(line)
    answers: dict[str, Any] = {}
    if not non_interactive:
        reader = ask or _read_line
        secret_reader = ask_secret or _read_secret
        for field in card["fields"]:
            if field["kind"] in cards.READ_ONLY_KINDS:
                continue
            if not _asked(card, field, answers):
                continue
            value = _ask_field(card, field, reader, secret_reader, out, mode)
            if value is not None:
                answers[field["id"]] = value
    return answers


def _asked(card: dict, field: dict, answers: dict) -> bool:
    """False when the field's ``ask_if`` is not met by the answer so far (or,
    unanswered, by what the card proposes)."""
    cond = field.get("ask_if")
    if not cond:
        return True
    if cond["field"] in answers:
        value = answers[cond["field"]]
    else:
        other = cards.field_map(card).get(cond["field"]) or {}
        value = other.get("proposed") or other.get("current")
    return value in cond["values"]


def _ask_field(card: dict, field: dict, reader, secret_reader, out,
               mode: str) -> Any:
    kind = field["kind"]
    lines = _question_lines(field, mode)
    # The whole question is the prompt: it prints exactly as separate lines
    # would, and whatever reads the answer (a person, a script, a test) sees
    # the question it is answering. A second ask repeats only the last line.
    prompt = "\n".join(lines)
    offset = 0
    for attempt in (1, 2, 3):
        raw = (secret_reader if kind == "secret_entry" else reader)(prompt)
        prompt = lines[-1]
        if raw is None:
            return None
        raw = raw if kind == "secret_entry" else raw.strip()
        if not raw:
            return None
        if kind == "choice" and raw.lower() == "more":
            offset += wording.SIMPLE_MAX_CHOICES
            options = field.get("options") or []
            page = options[offset:offset + wording.SIMPLE_MAX_CHOICES]
            if not page:
                offset, page = 0, options[:wording.SIMPLE_MAX_CHOICES]
            for line in _choice_lines(page, start=offset):
                out(line)
            continue
        try:
            return cards.validate_answers(
                card, {field["id"]: _parse(field, raw)})[field["id"]]
        except cards.CardError as exc:
            if attempt >= 2:
                out(ascii_only(f"  Keeping what was there: {_plain_error(exc)}"))
                return None
            out(ascii_only(f"  {_plain_error(exc)}"))
    return None


def _plain_error(exc: Exception) -> str:
    """A refusal without the field id in quotes at the front of it."""
    text = str(exc)
    if text.startswith("'") and "' " in text:
        text = text.split("' ", 1)[1]
        text = text[:1].upper() + text[1:]
    return text


def _parse(field: dict, raw: str) -> Any:
    """Text off a terminal into the type the card asked for. Never raises."""
    kind = field["kind"]
    if kind == "toggle":
        lowered = raw.lower()
        if lowered in ("y", "yes", "true", "1", "on"):
            return True
        if lowered in ("n", "no", "false", "0", "off"):
            return False
        return raw
    if kind == "number":
        try:
            return float(raw) if "." in raw else int(raw)
        except ValueError:
            return raw
    if kind == "choice":
        return _pick(field.get("options") or [], raw)
    if kind == "multi_choice":
        options = field.get("options") or []
        return [_pick(options, part.strip()) for part in raw.split(",")
                if part.strip()]
    return raw


def _pick(options: list[dict], raw: str) -> str:
    """A number, a label or a value, into the option's value."""
    text = raw.strip()
    if text.isdigit():
        index = int(text) - 1
        if 0 <= index < len(options):
            return options[index]["value"]
    lowered = text.lower()
    for option in options:
        if lowered in (option["value"].lower(), option["label"].lower()):
            return option["value"]
    return text


# -- the one summary ----------------------------------------------------------

def summary_lines(summary: dict) -> list[str]:
    """The whole plan, numbered where "Change something" can open it. Pure."""
    from puffpai.wizard import proposal

    mode = summary.get("mode") or wording.STANDARD
    lines = [""] if summary.get("checked") else []
    for text in summary.get("checked") or ():
        lines.extend(wrap(text))
    lines.extend(["", ""])
    lines.extend(_heading(str(summary.get("title") or "")))
    lines.extend(wrap(summary.get("intro") or ""))
    numbers = {item["id"]: number for number, item
               in enumerate(proposal.numbered(summary), 1)}
    blocks: list[tuple[str, list[dict], list[str], str]] = []
    groups = summary.get("jobs") or []
    if groups:
        # "What it will do", one group per ticked job, first.
        lines.append("")
        lines.append(ascii_only(str(summary.get("jobs_heading") or "")))
        for group in groups:
            tail = list(group.get("notes") or [])
            if group.get("not_reached"):
                tail.append(group["not_reached"])
            blocks.append(("  " + str(group.get("heading") or ""),
                           group.get("items") or [], tail, ""))
    similar = summary.get("similar") or {}
    if similar.get("rows"):
        # "Similar projects also have (tick to add):", at the end of "What it
        # will do"; "Change something" ticks one.
        blocks.append(("  " + str(similar.get("heading") or ""), [],
                       ["[ ] " + str(row.get("line") or "")
                        for row in similar["rows"]], ""))
    for section in summary.get("sections") or ():
        blocks.append((section["heading"], section.get("items") or [],
                       list(section.get("notes") or []),
                       section.get("empty") or ""))
    for heading, rows, notes, empty in blocks:
        lines.append("")
        lines.append(ascii_only(heading))
        if not rows and not notes and empty:
            lines.extend(wrap(empty, 2))
        # In their own order, so the numbers read 1, 2, 3 down the page
        # whatever is switched on.
        for item in rows:
            number = numbers.get(item["id"])
            head = f"{number}. " if number else ""
            pad = 2 + len(head)
            if item.get("on"):
                lines.extend(wrap(f"{head}{item['label']}", 2, pad))
                if item.get("detail"):
                    lines.extend(wrap(item["detail"], pad))
                continue
            lines.extend(wrap(head + (item.get("off_line")
                                      or proposal.off_line(item, mode)),
                              2, pad))
            if proposal.detail_when_off(item):
                # A find stays readable while unticked: what it is, its
                # stars and licence, how it would open.
                lines.extend(wrap(item["detail"], pad))
            if item["section"] == "thinking" and item["apply"]["kind"] in (
                    "model", "model_pull"):
                # The brain is the one thing a bot cannot do without, so what
                # was left out is said in full, size and all.
                if item.get("detail"):
                    lines.extend(wrap(item["detail"], pad))
                lines.extend(wrap(" ".join(wording.say(
                    "brain_off", mode,
                    name=summary.get("name") or "it").split()), pad))
        for note in notes:
            lines.extend(wrap(note, 2, 4))
    lines.append("")
    reach = summary.get("reach") or {}
    lines.extend(wrap(f"{len(numbers) + 1}. {reach.get('label') or ''}", 0, 3))
    lines.append("")
    for key in ("total", "leaves"):
        text = (summary.get("lines") or {}).get(key)
        if text:
            lines.extend(wrap(text))
    return lines


def summary_screen(summary: dict, *, rebuild: Callable[[dict], dict],
                   non_interactive: bool = False,
                   ask: Callable[[str], str | None] | None = None,
                   out: Callable[[str], None] = print) -> dict:
    """Show the plan; return the owner's changes once they say "Looks good".

    ``rebuild(changes)`` returns the summary with those changes applied, so the
    plan shown after a change is the plan that will be built. End of input at
    the main prompt is "Looks good" -- the prompt says Enter is -- and an answer
    it does not understand is asked again.
    """
    reader = ask or _read_line
    mode = summary.get("mode") or wording.STANDARD
    changes: dict = {"on": {}}
    while True:
        for line in summary_lines(summary):
            out(line)
        if non_interactive:
            say_line(wording.say("approved_by_flag", mode), out=out)
            return changes
        out("")
        for number, action in enumerate(summary.get("actions") or (), 1):
            out(ascii_only(f"  {number}) {action['label']}"))
        prompt_lines = wrap(wording.say("choose_prompt", mode), 2)
        for line in prompt_lines[:-1]:
            out(line)
        raw = reader(prompt_lines[-1])
        answer = (raw or "").strip().lower()
        if answer in ("", "1", "looks good", "yes", "y", "ok", "good"):
            return changes
        if answer not in ("2", "change", "change something", "c", "no", "n"):
            continue
        changed = _change_menu(summary, changes, reader, out, mode)
        if changed:
            summary = rebuild(changes)
            say_line(wording.say("changed", mode).replace("\n", " "), out=out)


# -- "Finding out what a <kind> bot needs" -------------------------------------

def needs_lines(words: dict) -> list[str]:
    """The job list, numbered, with the words that would be sent. Pure."""
    lines: list[str] = [""]
    if words.get("title"):
        lines.extend(_heading(str(words["title"])))
    lines.extend(wrap(words.get("intro") or ""))
    lines.append("")
    for number, job in enumerate(words.get("jobs") or (), 1):
        mark = "[x]" if job.get("ticked") else "[ ]"
        chip = f" ({job['chip']})" if job.get("chip") else ""
        head = f"{mark} {number}. "
        lines.extend(wrap(f"{head}{job.get('name') or ''}{chip}", 0,
                          len(head)))
    lines.append(ascii_only(f"[+] {words.get('add') or ''}"))
    held = words.get("held") or []
    if held:
        lines.append("")
        marks = ", ".join(f"[{'x' if row.get('allowed') else ' '}] "
                          f"{row.get('word')}" for row in held)
        lines.extend(wrap(f"{words.get('held_line') or ''} {marks}"))
    lines.append("")
    for text in words.get("before_button") or ():
        lines.extend(wrap(text))
    return lines


def needs_screen(words: dict, *, ask: Callable[[str], str | None] | None = None,
                 out: Callable[[str], None] = print) -> tuple:
    """Draw the job list once and read one answer.

    Returns ``("look", "online"|"here")``, ``("toggle", [job ids])``,
    ``("add", text)``, ``("allow", word)`` or ``("none",)`` for an answer it
    did not understand. End of input is "look only on this computer":
    nothing leaves unless a person pressed Enter under the words shown."""
    reader = ask or _read_line
    for line in needs_lines(words):
        out(line)
    prompt = wrap(str(words.get("prompt") or ""), 2)
    for line in prompt[:-1]:
        out(line)
    raw = reader(prompt[-1] if prompt else "")
    if raw is None:
        return ("look", "here")
    answer = raw.strip().lower()
    online = bool(words.get("online"))
    if answer == "":
        return ("look", "online" if online else "here")
    if answer in ("here", "only here"):
        return ("look", "here")
    if answer == "add":
        typed = reader(ascii_only("  " + str(words.get("add_prompt") or "")))
        typed = " ".join(str(typed or "").split())
        return ("add", typed) if typed else ("none",)
    if answer in ("words", "word"):
        held = [row for row in words.get("held") or () if not row.get("allowed")]
        if not held:
            return ("none",)
        for number, row in enumerate(held, 1):
            out(ascii_only(f"  {number}) {row.get('word')}"))
        picked = (reader(ascii_only("  " + str(words.get("words_prompt") or "")))
                  or "").strip()
        if picked.isdigit() and 1 <= int(picked) <= len(held):
            return ("allow", str(held[int(picked) - 1]["word"]))
        return ("none",)
    tokens = answer.replace(",", " ").split()
    jobs = words.get("jobs") or []
    if tokens and all(t.isdigit() and 1 <= int(t) <= len(jobs) for t in tokens):
        picked = list(dict.fromkeys(jobs[int(t) - 1]["id"] for t in tokens))
        return ("toggle", picked)
    return ("none",)


_YES = ("y", "yes")


def helper_screen(words: dict, *, ask: Callable[[str], str | None] | None = None,
                  out: Callable[[str], None] = print,
                  mode: str = wording.STANDARD) -> bool | None:
    """The reading helper offer, once, when the needs step says it is
    missing. True for "Get it", False for "Not now", ``None`` when there is
    nothing to offer. End of input and Enter are "Not now"."""
    offer = str(words.get("helper_offer") or "")
    if not offer:
        return None
    reader = ask or _read_line
    out("")
    for line in wrap(offer):
        out(line)
    prompt = wrap(" ".join(wording.say("needs_helper_prompt", mode).split()), 2)
    for line in prompt[:-1]:
        out(line)
    return (reader(prompt[-1]) or "").strip().lower() in _YES


def waiting_screen(waiting: list[dict], *,
                   decide: Callable[[str, bool], list[str]],
                   turn_on: Callable[[], str],
                   ask: Callable[[str], str | None] | None = None,
                   out: Callable[[str], None] = print,
                   mode: str = wording.STANDARD) -> None:
    """After the build: each thing Drawbridge held asks yes or no, and the
    Windows' Linux feature line asks to turn it on. Anything but yes or no
    (or end of input) leaves it waiting, as the browser does."""
    reader = ask or _read_line
    for row in waiting or ():
        out("")
        for text in row.get("lines") or ():
            for line in wrap(str(text), 2, 4):
                out(line)
        if row.get("status") == "held":
            question = wording.say("building_held_prompt", mode,
                                   name=row.get("name") or "it")
        else:
            question = wording.say("building_wsl_prompt", mode)
        answer = (reader(ascii_only("  " + question)) or "").strip().lower()
        if row.get("status") == "held" and answer in (*_YES, "n", "no"):
            said = decide(str(row.get("id") or ""), answer in _YES)
        elif row.get("status") != "held" and answer in _YES:
            said = [turn_on()]
        else:
            continue
        for text in said:
            if text:
                for line in wrap(str(text), 2, 4):
                    out(line)


def town_screen(summary: dict, *, ask: Callable[[str], str | None] | None = None,
                out: Callable[[str], None] = print) -> str | None:
    """The one town question, right after "Looks good", only when the plan
    needs it. Returns what was typed (``""`` is a skip), or ``None`` when the
    plan did not ask."""
    town = summary.get("town") or {}
    if not town.get("ask"):
        return None
    reader = ask or _read_line
    mode = summary.get("mode") or wording.STANDARD
    out("")
    for text in (town.get("help"), town.get("skip")):
        for line in wrap(str(text or "")):
            out(line)
    typed = " ".join(str(reader(ascii_only(
        str(town.get("question") or ""))) or "").split())
    if not typed:
        say_line(wording.say("town_skipped", mode).replace("\n", " "), out=out)
    return typed


def _change_menu(summary: dict, changes: dict, reader, out, mode: str) -> bool:
    """One pass of "Change something". True when anything changed.

    The items are a choice like any other: labels, numbered, three at a time
    for a young builder.
    """
    from puffpai.wizard import proposal

    items = proposal.numbered(summary)
    reach = summary.get("reach") or {}
    options = [{"value": item["id"],
                "label": item["label"] + ("" if item.get("on")
                                          else " (not included)")}
               for item in items]
    similar = [row for row in (summary.get("similar") or {}).get("rows") or ()
               if row.get("name")]
    for number, row in enumerate(similar):
        options.append({"value": f"{SIMILAR_PREFIX}{number}",
                        "label": str(row["name"]) + " (not included)"})
    options.append({"value": proposal.REACH_ITEM_ID,
                    "label": str(reach.get("label") or "")})
    many: list[str] = []
    picked = _pick_from(options, "", reader, out, mode,
                        wording.say("change_which", mode),
                        hint=wording.say("change_many", mode), many=many)
    if many:
        return _flip_many(items, many, changes)
    if picked is None:
        return False
    if picked.startswith(SIMILAR_PREFIX):
        row = similar[int(picked[len(SIMILAR_PREFIX):])]
        answer = (reader(ascii_only("  " + wording.say(
            "change_include", mode, label=row["name"], current="no")))
            or "").strip().lower()
        if answer in ("y", "yes", "on", "1"):
            changes.setdefault("similar", [])
            if row["name"] not in changes["similar"]:
                changes["similar"].append(row["name"])
            return True
        return False
    if picked == proposal.REACH_ITEM_ID:
        return _change_reach(summary, changes, reader, out, mode)
    item = next(item for item in items if item["id"] == picked)
    if item["section"] == "thinking" and item["apply"]["kind"] in (
            "model", "model_pull"):
        return _change_brain(summary, item, changes, reader, out, mode)
    current = "yes" if item.get("on") else "no"
    answer = (reader(ascii_only("  " + wording.say(
        "change_include", mode, label=item["label"], current=current)))
        or "").strip().lower()
    if answer in ("y", "yes", "on", "1"):
        changes["on"][item["id"]] = True
        # A line that needs the locked box brings the box with it.
        for other in items:
            if (other.get("apply") or {}).get("kind") in (
                    item.get("requires") or ()) and not other.get("on"):
                changes["on"][other["id"]] = True
        return True
    if answer in ("n", "no", "off", "0"):
        changes["on"][item["id"]] = False
        return True
    return False


#: A "Similar projects also have" line in the change menu.
SIMILAR_PREFIX = "similar:"
_MANY_RE = re.compile(r"^\d+(?:[\s,]+\d+)+$")


def _flip_many(items: list[dict], ids: list[str], changes: dict) -> bool:
    """Tick what was off and untick what was on, for every line picked at
    once ("2 7"). The brain and the internet choice keep their own menus;
    a line that needs the locked box brings the box with it."""
    by_id = {item["id"]: item for item in items}
    changed = False
    for ident in ids:
        item = by_id.get(ident)
        if item is None or (item["section"] == "thinking" and item["apply"][
                "kind"] in ("model", "model_pull")):
            continue
        now = changes["on"].get(ident, bool(item.get("on")))
        changes["on"][ident] = not now
        changed = True
        if not now:
            for other in items:
                if (other.get("apply") or {}).get("kind") in (
                        item.get("requires") or ()) and not other.get("on"):
                    changes["on"][other["id"]] = True
    return changed


def _pick_from(options: list[dict], current: str, reader, out, mode: str,
               question: str, *, hint: str = "",
               many: list | None = None) -> str | None:
    """Numbered labels, three at a time in the simple wording. The value
    picked, or ``None`` for "keep". With *many* (a list), several numbers
    typed at once ("2 7") are put in it and ``None`` is returned."""
    offset = 0
    step = (wording.SIMPLE_MAX_CHOICES if mode == wording.SIMPLE
            else MAX_SHOWN_OPTIONS)
    while True:
        page = options[offset:offset + step]
        out("")
        for line in _choice_lines(page, start=offset,
                                  marks={current: " (now)"} if current
                                  else None):
            out(line)
        more = offset + step < len(options)
        if hint and many is not None and len(options) > 2:
            for line in wrap(hint.replace("\n", " "), 2):
                out(line)
        text = question + (" Type more to see other choices." if more else "")
        prompt = wrap(text.replace("\n", " "), 2)
        for line in prompt[:-1]:
            out(line)
        raw = (reader(prompt[-1]) or "").strip().lower()
        if raw == "more" and more:
            offset += step
            continue
        if many is not None and _MANY_RE.match(raw):
            numbers = [int(n) for n in re.split(r"[\s,]+", raw)]
            if all(1 <= n <= len(options) for n in numbers):
                for n in numbers:
                    value = options[n - 1]["value"]
                    if value not in many:
                        many.append(value)
                return None
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1]["value"]
        # A person may type the words instead of the number.
        for option in options:
            if raw and raw in (option["value"].lower(),
                               option["label"].lower()):
                return option["value"]
        return None


def _change_brain(summary, item, changes, reader, out, mode) -> bool:
    choices = [dict(c) for c in summary.get("model_choices") or ()]
    choices.append({"value": "__off__",
                    "label": wording.say("leave_out_brain", mode)})
    current = item["apply"]["ref"] if item.get("on") else "__off__"
    picked = _pick_from(choices, current, reader, out, mode,
                        wording.say("change_brain", mode))
    if picked is None:
        return False
    if picked == "__off__":
        changes["on"][item["id"]] = False
    else:
        changes["model"] = picked
        changes["on"][item["id"]] = True
    return True


def _change_reach(summary, changes, reader, out, mode) -> bool:
    reach = summary.get("reach") or {}
    picked = _pick_from(list(reach.get("choices") or ()),
                        str(reach.get("current") or ""), reader, out, mode,
                        wording.say("change_reach", mode,
                                    name=summary.get("name") or "it"))
    if picked is None:
        return False
    changes["reach"] = picked
    return True


# -- reading ------------------------------------------------------------------

def _stdin_is_tty() -> bool:
    """Is there a real terminal on the other end of stdin?"""
    try:
        return bool(sys.stdin is not None and sys.stdin.isatty())
    except (AttributeError, ValueError, OSError):
        return False


def _read_line(prompt: str) -> str | None:
    try:
        print(prompt)
        return input(ANSWER_MARK)
    except (EOFError, KeyboardInterrupt):
        return None


def _read_secret(prompt: str) -> str | None:
    """Read one secret. Never echoes on a terminal, and never blocks off one.

    With no tty the line reader is used instead: there is no echo to worry
    about on a pipe, and end-of-file is "no answer". It never hangs.
    """
    try:
        print(prompt)
        if not _stdin_is_tty():
            return input(ANSWER_MARK)
        return getpass.getpass(ANSWER_MARK)
    except (EOFError, KeyboardInterrupt):
        return None
    except Exception:
        # No terminal to turn echo off on, and not a pipe either. Refusing is
        # the honest answer: a secret typed in the clear is in scrollback.
        return None


__all__ = ["ANSWER_MARK", "BEGIN_MARK", "CLEAR_SEQUENCE", "END_MARK",
           "RESUME_CHOICES", "ConsoleShowOnce", "ascii_only", "card_lines",
           "clear_terminal", "how_long_ago",
           "needs_lines", "needs_screen",
           "render", "resume_lines", "resume_screen", "say_line",
           "strip_block", "summary_lines", "summary_screen", "town_screen",
           "width", "wrap"]
