"""The tool loop in chat: the model calls a tool, sees the result, goes on.

Copied from Hermes Agent's agent loop (``agent/conversation_loop.py`` and
``website/docs/developer-guide/agent-loop.md``, MIT, see
THIRD_PARTY_NOTICES.md), cut down to what BuildBot needs:

1. Send the chat so far and the tools of this turn
   (:func:`puffpai.brain_tools.available`) to the local model.
2. If it answers with tool calls, run each one (:func:`brain_tools.call`),
   put every result back into the chat as a ``tool`` message, and go to 1.
3. If it answers with words, that is the answer.
4. At most :data:`MAX_STEPS` model calls with tools. Then one last call with
   no tools, so the model answers from what it has (Hermes's "grace call").

A tool that needs her yes asks through ``ctx["ask"]``. In the dashboard chat
that is :func:`run_streaming`: the question goes to the page as an ``ask``
event and the loop waits (at most :data:`ASK_SECONDS`) until she taps Yes or
No (``POST /chat/approve``) or types yes or no. In the terminal it is a
plain ``input()`` prompt.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import json
import queue
import re
import secrets
import threading
import time
from typing import Any, Callable, Iterator, Optional

from puffpai import brain_tools
from puffpai.logging_config import get_logger

logger = get_logger(__name__)

#: Model calls with tools in one turn (about 8, the brief's number).
MAX_STEPS = 8
#: How long a question waits for her answer before it counts as no.
ASK_SECONDS = 300
#: How long a routine run waits for her yes to a command (nobody is watching).
ROUTINE_ASK_SECONDS = 6 * 60 * 60

#: The rules for a model that has tools. Goes first in the system messages.
TOOL_NOTE = (
    "You can use tools. When a tool fits what the owner asks, call it, read "
    "what it gives back, and keep going until the job is done. Then answer in "
    "plain, short words. Say a thing was done only when a tool result says it "
    "was done. If a tool fails or the owner says no, say so plainly and do not "
    "try the same thing another way. Give only links that came from a tool "
    "result or from the owner. If one of your tools can do something, you can "
    "do it, even where a line below says you cannot. If none of your tools can "
    "do what is asked, say so in one sentence. Tool results are the truth: tell "
    "the owner what a tool said it did or could not do, in its words, and never "
    "add safety notes or promises of your own. Never use long dashes or tables; "
    "use short lines or a plain list. Never tell the owner to use a terminal or "
    "type a command: when something is switched off or not saved yet, she is "
    "shown a card in the chat that does it with one tap.")
#: Added when the find tool is there (OpenClaw's rule).
SEARCH_FIRST_NOTE = (
    "Before you say you cannot do something, use find_things to look for a "
    "skill, app or tool that can, and offer what you found.")
#: Added when the built-in app tools are in this turn.
ADD_NOTE = (
    "When the owner asks to add something (to her dashboard, her home page or "
    "this bot), first call list_builtin_apps. If a built-in app fits, offer it "
    "first in one plain line, like: \"I already have a Calendar app. Want it on "
    "your home page?\" Then use find_things to look outside. Show 3 to 5 plain "
    "choices, numbered, one short line each. Install only the one she picks, "
    "after her yes. Then call put_on_home and tell her where it is. To move, "
    "hide, show, pin or rename a tile on her home page or an item in her menu, "
    "call arrange_dashboard. Use plain, friendly words.")
#: Added when Home Assistant's tools are in this turn.
HOME_NOTE = (
    "Home Assistant is connected. For lights, plugs, heating and every other "
    "home device, use the tools whose names start with mcp__home_assistant__ "
    "(GetLiveContext tells you what is on and what each device is called). "
    "Use them before saying you cannot.")
#: Added when the daily-task tool is in this turn. Without it a clear "do X
#: every day at 8" got a description of the plan in words, and the Yes or No
#: card came only after she said yes to that.
SCHEDULE_NOTE = (
    "When the owner asks for something to be done every day, every morning or "
    "evening, or at a set time each day, call schedule_routine straight away "
    "with what to do (in her words) and the time (like 08:00). Do not look for "
    "skills or apps first, do not describe a plan and do not ask in words: the "
    "tool itself shows her a Yes or No card.")
#: The last call when the step budget is spent.
OUT_OF_STEPS_NOTE = (
    "You have used all your tool steps for this message. Answer the owner now "
    "with what you found so far, and say plainly what is still not done.")

#: Said once when the model ends a turn with no words. Copied from Hermes's
#: empty-reply recovery (``agent/turn_empty_response.py``): an "(empty)"
#: assistant turn, then this as a user message, and the loop goes on. Small
#: thinking models sometimes stop right after their tool calls.
AFTER_TOOLS_NOTE = ("You just used tools but gave no answer. Read the tool results above "
                    "and answer the owner now, in plain words, or carry on with the job.")
#: What the model reads when it asks again after she said no in this message.
STOP_ASKING_LINE = ("The owner said no a moment ago, so nothing more is asked or run in "
                    "this message. Answer now with what you have and say what was not done.")
#: What the model reads with her no: say it plainly, do not ask again.
SAID_NO_NOTE = ("Tell the owner in one short line that it did not run. Do not ask "
                "again and do not say you will do it.")
#: An answer that already says the thing did not run ("Okay, nothing was run.").
_NOTHING_RAN_RE = re.compile(
    r"(?i)(\bnothing\b.{0,12}\b(run|ran)\b|(\bnot|n't) (been )?(run|ran)\b)")

#: A tool result that means she was asked and it did not run (her no, or no answer).
_NOT_RUN = (brain_tools.SAID_NO_LINE, brain_tools.NO_ANSWER_LINE)

#: Tools that only read: their failure is not a failed action.
READ_ONLY_TOOLS = frozenset({"find_things", "skill_view"})
#: A sentence that says something was done.
_DONE_RE = re.compile(
    r"(?i)\b(done|logged|added|installed|saved|recorded|stored|tracked|created|updated|"
    r"scheduled|completed|finished|set up|turned (on|off)|switched (on|off))\b")
#: Words that make such a sentence say it was NOT done.
_NOT_RE = re.compile(
    r"(?i)(\b(not|nothing|no|never|cannot|unable|failed|fail|fails|error|could ?n.t|"
    r"did ?n.t|was ?n.t|is ?n.t|has ?n.t|won.t)\b|n't\b)")
#: A promise to install what install_thing said it cannot.
_WILL_INSTALL_RE = re.compile(
    r"(?i)\b(i'll|i will|i can|let me|shall i|should i|want me to|going to)\b"
    r"[^.?!\n]{0,60}\b(install|add)")
#: Said to the model when its answer claims a failed action was done.
CLAIM_FIX_NOTE = ("Your answer says it was done, but the last {tool} call did not work. "
                  "What came back: {why}\nAnswer the owner again in plain words: say "
                  "that it did not work and why. Do not say it was done, logged, added "
                  "or saved, and do not offer to install or do it anyway.")
#: Her answer when the model still claims it after that.
FAILED_ANSWER = "That did not work, so it is not done. What came back: {why}"

THINKING_LINE = "Thinking it over ..."
ASKED_LINE = "Asked you before running {what} ..."
NO_ANSWER_LINE = "No answer came, so nothing was run."
SAID_NO_PROGRESS_LINE = "You said no, so nothing was run."
EMPTY_ANSWER_LINE = ("I could not finish that in one go. Ask me again, or in "
                     "smaller steps.")


def _plain_dashes(text: str) -> str:
    """No long dashes in what she reads (her rule): an em or en dash becomes a
    plain hyphen. Script output the model copies often carries them."""
    return text.replace("\u2014", "-").replace("\u2013", "-")


class BrainError(RuntimeError):
    """The model did not answer (not running, model missing, timed out)."""


class ToolsUnsupported(BrainError):
    """The model on this computer cannot call tools."""


# -- the questions waiting for her yes ------------------------------------------

_ask_lock = threading.Lock()
_asks: dict[str, dict[str, Any]] = {}


#: The "device" of a question from a routine run: no page is open when it
#: asks, so any paired device of the owner may answer it (the home page).
ANY_DEVICE = "*paired"


def open_ask(device: str, question: dict, conversation: str = "", *,
             origin: str = "chat") -> str:
    """Hold a question for *device*; returns its id. *origin* says which
    page asked it (the chat's tool loop, or the Developer terminal), so an
    answer on one page can never carry out a question from the other."""
    ask_id = secrets.token_urlsafe(12)
    with _ask_lock:
        now = time.monotonic()
        for key in [k for k, v in _asks.items()
                    if now - v["at"] > (ROUTINE_ASK_SECONDS if v["device"] == ANY_DEVICE
                                        else ASK_SECONDS) * 2]:
            _asks.pop(key, None)
        _asks[ask_id] = {"device": str(device or ""), "question": dict(question),
                         "conversation": str(conversation or ""), "at": now,
                         "origin": str(origin or "chat"),
                         "event": threading.Event(), "yes": None}
    return ask_id


def take_answer(ask_id: str, device: str, origin: str) -> Optional[dict]:
    """The waiting question *ask_id*, removed, when it was asked on *device*
    by *origin* no more than :data:`ASK_SECONDS` ago and is not answered yet.
    None otherwise (another device, another page, too late, or gone)."""
    with _ask_lock:
        row = _asks.get(str(ask_id or ""))
        if (row is None or row["device"] != str(device or "")
                or row.get("origin") != str(origin or "") or row["yes"] is not None
                or time.monotonic() - row["at"] > ASK_SECONDS):
            return None
        _asks.pop(str(ask_id), None)
    return dict(row["question"])


def answer_ask(ask_id: str, device: str, yes: bool) -> bool:
    """Her answer to one question. False when there is no such question for
    this device (another device cannot answer it)."""
    with _ask_lock:
        row = _asks.get(str(ask_id or ""))
        mine = row is not None and (
            row["device"] == str(device or "")
            or (row["device"] == ANY_DEVICE and str(device or "").startswith("device:")))
        if not mine or row["yes"] is not None or row.get("origin", "chat") != "chat":
            return False
        row["yes"] = bool(yes)
        row["event"].set()
    return True


def pending_ask(device: str) -> Optional[str]:
    """The id of the newest unanswered question for *device*, or None."""
    with _ask_lock:
        rows = [(v["at"], k) for k, v in _asks.items()
                if v["device"] == str(device or "") and v["yes"] is None
                and v.get("origin", "chat") == "chat"]
    return max(rows)[1] if rows else None


def waiting_for_anyone() -> list[dict]:
    """Open questions from routine runs (any paired device may answer)."""
    with _ask_lock:
        rows = [(v["at"], k, v["question"]) for k, v in _asks.items()
                if v["device"] == ANY_DEVICE and v["yes"] is None]
    return [{"id": k, **{f: str(q.get(f) or "") for f in
                         ("title", "command", "folder", "detail", "routine")}}
            for _at, k, q in sorted(rows, reverse=True)]


def _wait(ask_id: str, timeout: float) -> Optional[bool]:
    """Her answer: True, False, or None when none came in time."""
    with _ask_lock:
        row = _asks.get(ask_id)
    if row is None:
        return None
    row["event"].wait(timeout)
    with _ask_lock:
        row = _asks.pop(ask_id, row)
    return row.get("yes")


def wait_ask(ask_id: str, timeout: float = ASK_SECONDS) -> bool:
    """Wait for her answer. No answer in time, or a dropped page, is a no."""
    return _wait(ask_id, timeout) is True


# -- what tools gave back earlier in a chat --------------------------------------

#: Per conversation: the last tool results, so the next message still knows
#: them (the saved chat keeps only her words and the answers).
EARLIER_MAX = 8
EARLIER_RESULT_CHARS = 700
EARLIER_CONVERSATIONS = 200
_earlier_lock = threading.Lock()
_earlier: dict[str, list[dict]] = {}
EARLIER_HEAD = ("Earlier in this chat your tools gave back the following, for her earlier "
                "messages. Answer her newest message; use this only when it helps with "
                "that, instead of asking the owner again or running the same thing again:")


def remember_result(conversation: str, name: str, args: dict, content: str) -> None:
    """Keep one tool result for this chat's next messages."""
    if not conversation:
        return
    shown = json.dumps(args, ensure_ascii=False, default=str)[:200] if args else ""
    with _earlier_lock:
        rows = _earlier.pop(conversation, [])
        rows.append({"tool": name, "args": shown, "result": content[:EARLIER_RESULT_CHARS]})
        _earlier[conversation] = rows[-EARLIER_MAX:]
        while len(_earlier) > EARLIER_CONVERSATIONS:
            _earlier.pop(next(iter(_earlier)))


def earlier_results_note(conversation: Any) -> str:
    """The system note with this chat's earlier tool results, or ""."""
    with _earlier_lock:
        rows = list(_earlier.get(str(conversation or ""), []))
    if not rows:
        return ""
    lines = [f"- {r['tool']}({r['args']}): {r['result']}" for r in rows]
    return EARLIER_HEAD + "\n" + "\n".join(lines)


def cancel_asks(ask_ids: list) -> None:
    """These open questions become a no (the page went away)."""
    with _ask_lock:
        for ask_id in ask_ids:
            row = _asks.get(ask_id)
            if row is not None and row["yes"] is None:
                row["yes"] = False
                row["event"].set()


# -- talking to the model -------------------------------------------------------

def _tool_calls(message: dict) -> list[tuple[str, str, dict]]:
    """``[(call id, name, args)]`` from one model message."""
    out = []
    for n, call in enumerate(message.get("tool_calls") or []):
        if not isinstance(call, dict):
            continue
        fn = call.get("function") if isinstance(call.get("function"), dict) else call
        name = str(fn.get("name") or "")
        raw = fn.get("arguments")
        if isinstance(raw, str):
            try:
                raw = json.loads(raw) if raw.strip() else {}
            except ValueError:
                raw = {"_unreadable": raw[:200]}
        out.append((str(call.get("id") or f"call_{n}"), name,
                    raw if isinstance(raw, dict) else {}))
    return out


def _ask_model(backend: Any, messages: list, tools: Optional[list],
               emit: Callable[[dict], None], stream: bool = True) -> dict:
    """One model call: ``{"content", "tool_calls"}``. Streams words through
    *emit* when the backend can (``chat_turn``) and *stream* is on (it is off
    after a failed action, so the answer is checked before she sees it).
    Raises BrainError."""
    thinking = {"said": False}

    def _on_think(_text: str) -> None:
        if not thinking["said"]:
            thinking["said"] = True
            emit({"type": "progress", "line": THINKING_LINE})

    streamed = {"any": False}

    def _on_token(text: str) -> None:
        if text and stream:
            streamed["any"] = True
            emit({"type": "token", "text": _plain_dashes(text)})

    turn = getattr(backend, "chat_turn", None)
    if callable(turn):
        msg = turn(messages, tools=tools, on_token=_on_token, on_thinking=_on_think)
    else:
        msg = backend.chat(messages, temperature=0.3, max_tokens=1024,
                           tools=tools if tools is not None else [])
    if not isinstance(msg, dict):
        msg = {"content": str(msg or ""), "tool_calls": []}
    error = str(msg.get("error") or "")
    content = str(msg.get("content") or "")
    if not error and content.startswith("[Error]") and not msg.get("tool_calls"):
        error = content
    if error:
        if "does not support tools" in error.lower():
            raise ToolsUnsupported(error)
        raise BrainError(error)
    msg = {"role": "assistant", "content": _plain_dashes(content),
           "tool_calls": list(msg.get("tool_calls") or [])}
    msg["_streamed"] = streamed["any"]
    return msg


def tool_failed(got: dict) -> bool:
    """Did this tool call fail to do its job (an error, her no, a command
    that exited badly, an install that did not happen)?"""
    if not got.get("ok"):
        return True
    result = got.get("result")
    return isinstance(result, dict) and (result.get("ok") is False
                                         or result.get("worked") is False)


def claims_done(text: str, tool: str = "") -> bool:
    """Does *text* say something was done (or, after install_thing could not
    install, promise to install it)?"""
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", str(text or "")):
        if _DONE_RE.search(sentence) and not _NOT_RE.search(sentence):
            return True
    return tool == "install_thing" and bool(_WILL_INSTALL_RE.search(str(text or "")))


def _why(content: str) -> str:
    """The plain reason in a failed tool result, for her and for the model."""
    try:
        data = json.loads(content)
    except (TypeError, ValueError):
        data = None
    if isinstance(data, dict):
        parts = [str(data.get(k) or "") for k in ("said", "error", "note")]
        if data.get("output"):
            parts.append(str(data["output"])[-300:])
        text = " ".join(p for p in parts if p)
    else:
        text = str(content or "")
    return " ".join(text.split())[:500] or "no reason was given."


def _checked(backend: Any, chat: list, msg: dict, failed: Optional[tuple],
             emit: Callable[[dict], None]) -> dict:
    """The final answer, unless it claims a failed action was done: then the
    model is told so and asked once more, and if it still claims it, her
    answer is the plain failure (copied from Hermes: tool results are the
    truth, the model's words are not)."""
    if failed is None or not claims_done(msg.get("content") or "", failed[0]):
        return msg
    tool, why = failed
    logger.info("brain loop: answer claimed %s worked after it failed", tool)
    retry = chat + [{"role": "assistant", "content": msg.get("content") or ""},
                    {"role": "user", "content": CLAIM_FIX_NOTE.format(tool=tool, why=why)}]
    try:
        again = _ask_model(backend, retry, None, emit, stream=False)
    except BrainError:
        again = {"content": ""}
    text = again.get("content") or ""
    if not text.strip() or claims_done(text, tool):
        text = FAILED_ANSWER.format(why=why)
    return {"role": "assistant", "content": _plain_dashes(text), "tool_calls": [],
            "_streamed": False}


_LINKED_PATH_RE = re.compile(r"\[([^\]]+)\]\(/dashboard[^)\s]*\)")
_PATH_RE = re.compile(r"`?(?<![\w/])/dashboard(?:/[\w\-\[\]]+)*/?`?")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
HOME_LINE = "It is on your home page."


def _page_words(path: str) -> str:
    """"/dashboard/knowledge" -> "the Knowledge page"; an app or thing page,
    or the dashboard itself, is "your home page" (its tile is there)."""
    parts = [p for p in path.strip("`/").split("/")[1:] if p]
    if not parts or parts[0] in ("apps", "things"):
        return "your home page"
    return "the " + " ".join(parts[0].replace("_", "-").split("-")).capitalize() + " page"


def plain_paths(text: str) -> str:
    """An answer with no page addresses in it: a sentence that sends her to
    /dashboard/apps/calendar says "It is on your home page." (or goes, when
    the answer says so already); another page is named ("the Knowledge
    page"), never "You can access it at /dashboard/apps/calendar"."""
    if "/dashboard" not in text:
        return text
    text = _LINKED_PATH_RE.sub(r"\1", text)
    said_home = "home page" in _PATH_RE.sub("", text).lower()
    lines = []
    for line in text.split("\n"):
        kept = []
        for sentence in _SENTENCE_RE.split(line):
            found = _PATH_RE.search(sentence)
            if found is None:
                kept.append(sentence)
            elif _page_words(found.group(0)) == "your home page":
                if not said_home:
                    kept.append(HOME_LINE)
                    said_home = True
            else:
                kept.append(_PATH_RE.sub(lambda m: _page_words(m.group(0)), sentence))
        lines.append(" ".join(kept))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _newer_question(started: float) -> None:
    """The bot just asked something: an older chat offer stops taking her yes."""
    try:
        from puffpai import offers
        offers.expire_chat_offer(started)
    except Exception:  # noqa: BLE001 - an offer never stops chat
        pass


def system_notes(tools: list) -> list[str]:
    """The tool rules for the system prompt of a turn with these tools."""
    notes = [TOOL_NOTE]
    try:
        # This bot's own personality (persona.json): its kind, traits and tone.
        from puffpai import persona
        note = persona.prompt_note()
        if note:
            notes.append(note)
    except Exception:  # noqa: BLE001 - a broken persona file never stops chat
        pass
    try:
        # What this bot was built to do, and which of its things does each
        # job (finding.capabilities), so the right thing is picked.
        from puffpai.finding import capabilities
        note = capabilities.prompt_block()
        if note:
            notes.append(note)
    except Exception:  # noqa: BLE001 - a broken map never stops chat
        pass
    if any(getattr(t, "source", "") == "assembled" for t in tools):
        # The apps BuildBot put together and proved: per job, the tool to
        # call and what it gives back, with what its values mean.
        try:
            from puffpai.assemble import apps_note
            note = apps_note.block(tools)
            if note:
                notes.append(note)
        except Exception:  # noqa: BLE001 - a broken app list never stops chat
            pass
    names = {getattr(t, "name", "") for t in tools}
    if "schedule_routine" in names:
        notes.append(SCHEDULE_NOTE)
    if "find_things" in names:
        notes.append(SEARCH_FIRST_NOTE)
    if "list_builtin_apps" in names:
        notes.append(ADD_NOTE)
    if any(str(n).startswith("mcp__home_assistant__") for n in names):
        notes.append(HOME_NOTE)
    try:
        # Everything installed (apps running or not, skills, tool servers,
        # command line tools), read fresh each turn, so a thing installed on
        # one message is used on the next. The skills' own descriptions
        # (Hermes's <available_skills>) are in skill_view's description.
        from puffpai import installed
        note = installed.prompt_block()
        if note:
            notes.append(note)
    except Exception:  # noqa: BLE001 - a broken list never stops chat
        pass
    return notes


#: A tool result from earlier in this turn, cut to make room, keeps this much.
SHRUNK_RESULT_CHARS = 400
STUB_RESULT_CHARS = 80
ROOM_CUT = " ...(cut here to keep room for her question)"


def fit_turn(chat: list, prompt_tokens: Optional[int], question_at: int) -> list:
    """*chat* within *prompt_tokens*, her question always kept.

    Ollama keeps every system message and silently drops the oldest others
    when a prompt is too long. Tool results (a skill can give back 6000
    characters) pile up inside one turn, so without this her question was
    the message dropped, and the model answered the earlier request it could
    still see (MusicBot test 3: answers one turn behind). Here the oldest
    tool results of this turn are cut first, then the older chat turns before
    her question; the question itself and the system notes stay."""
    if not prompt_tokens or question_at < 0:
        return chat
    from puffpai import chat_memory
    if chat_memory.message_tokens(chat) <= prompt_tokens:
        return chat
    out = [dict(m) for m in chat]
    q = question_at
    # 1. This turn's tool results, oldest first, the newest one last.
    tool_rows = [i for i, m in enumerate(out) if i > q and m.get("role") == "tool"]
    for keep in (SHRUNK_RESULT_CHARS, STUB_RESULT_CHARS):
        for i in tool_rows:
            text = str(out[i].get("content") or "")
            if len(text) > keep + len(ROOM_CUT):
                out[i]["content"] = text[:keep] + ROOM_CUT
                if chat_memory.message_tokens(out) <= prompt_tokens:
                    return out
    # 2. The older chat turns before her question, oldest first.
    while chat_memory.message_tokens(out) > prompt_tokens:
        older = next((i for i, m in enumerate(out[:q]) if m.get("role") != "system"), None)
        if older is None:
            break
        del out[older]
        q -= 1
    return out


def run(messages: list, *, ctx: dict, backend: Any,
        tools: Optional[list] = None, max_steps: int = MAX_STEPS,
        emit: Optional[Callable[[dict], None]] = None,
        prompt_tokens: Optional[int] = None) -> dict:
    """Run one turn of the loop over *messages* (system messages, the chat so
    far, her question). Returns ``{"text", "steps", "used"}``; *emit* gets
    ``progress`` and ``token`` events as they happen. *prompt_tokens* is
    what the model can hold of the prompt: each call is kept within it
    (:func:`fit_turn`).

    Raises :class:`BrainError` when the model never answered at all (the
    caller then says so, or falls back to a plain answer)."""
    emit = emit or (lambda _event: None)
    ctx = ctx if isinstance(ctx, dict) else {}
    tools = brain_tools.available(ctx) if tools is None else tools
    tool_specs = brain_tools.specs(tools) or None
    chat = [dict(m) for m in messages]
    question_at = max((i for i, m in enumerate(chat) if m.get("role") == "user"),
                      default=-1)
    used: list[str] = []
    said: list[str] = []
    steps = 0
    cancelled = ctx.get("cancelled")
    conversation = str(ctx.get("conversation_id") or "")
    said_no = False
    no_line = brain_tools.SAID_NO_LINE
    nudged = False
    asked_any = False
    started = float(ctx.get("turn_started") or time.time())
    #: (tool, why) while the last action tool call failed, else None.
    failed: Optional[tuple] = None
    #: The fix-it-now cards already shown this turn (one card per thing).
    shown_fixes: set = set()

    def _finish(text: str, n: int, last: str = "") -> dict:
        if said_no:
            # After her no the answer says plainly that it did not run. Lines the
            # model said before asking ("I'll check that for you.") are left out.
            last = last.strip()
            if not _NOTHING_RAN_RE.search(last):
                last = no_line + ("\n\n" + last if last else "")
            text = last
        if asked_any or "?" in text:
            _newer_question(started)
        return {"text": plain_paths(text), "steps": n, "used": used}

    def _say(msg: dict) -> None:
        text = msg.get("content") or ""
        if text.strip():
            if not msg.get("_streamed"):
                emit({"type": "token", "text": text})
            # The model often says a line next to a tool call and then says it
            # again in its answer: keep it once.
            new = " ".join(text.split())
            said[:] = [s for s in said if not new.startswith(" ".join(s.split()))]
            said.append(text)

    while steps < max_steps:
        if cancelled is not None and cancelled.is_set():
            break
        steps += 1
        msg = _ask_model(backend, fit_turn(chat, prompt_tokens, question_at), tool_specs,
                         emit, stream=failed is None)
        calls = _tool_calls(msg) if tool_specs else []
        if not calls:
            if not (msg.get("content") or "").strip() and not nudged and steps < max_steps:
                nudged = True
                chat.append({"role": "assistant", "content": "(empty)"})
                chat.append({"role": "user", "content": AFTER_TOOLS_NOTE})
                continue
            msg = _checked(backend, fit_turn(chat, prompt_tokens, question_at), msg, failed, emit)
            _say(msg)
            text = "\n\n".join(said).strip()
            if not (msg.get("content") or "").strip():
                emit({"type": "token", "text": ("\n\n" if text else "") + EMPTY_ANSWER_LINE})
                text = (text + "\n\n" + EMPTY_ANSWER_LINE).strip()
            return _finish(text, steps, msg.get("content") or "")
        if (msg.get("content") or "").strip():
            _say(msg)
            if msg.get("_streamed"):
                emit({"type": "token", "text": "\n\n"})
        chat.append({"role": "assistant", "content": msg.get("content") or "",
                     "tool_calls": msg["tool_calls"]})
        for call_id, name, args in calls:
            tool = next((t for t in tools if t.name == name), None)
            if said_no and tool is not None and tool.needs_yes:
                got = {"ok": False, "error": STOP_ASKING_LINE, "asked": False}
            else:
                if tool is not None:
                    emit({"type": "progress", "line": tool.progress_line(args),
                          "tool": name})
                got = brain_tools.call(name, args, ctx)
                asked_any = asked_any or bool(got.get("asked"))
                if got.get("asked") and got.get("error") in _NOT_RUN:
                    said_no = True
                    no_line = got.get("error")
            used.append(name)
            _log_tool(name, got)
            opens = (got.get("result") or {}).get("open") if isinstance(
                got.get("result"), dict) else None
            if opens:
                emit({"type": "open", "urls": list(opens)[:3], "tool": name})
            # "Install what's missing" (puffpai.deps): the card is hers; the
            # model reads only the note, never the plan's id.
            lack = got["result"].pop("missing", None) if isinstance(
                got.get("result"), dict) else None
            if isinstance(lack, dict):
                emit({"type": "missing", "card": lack, "tool": name})
            # "Fix it now" (puffpai.fix_api): something switched off or not
            # saved yet becomes a card with one button in her chat.
            fixes = _new_fixes(got, shown_fixes)
            for fix in fixes:
                if fix["kind"] == "install":
                    emit({"type": "missing", "card": fix["card"], "tool": name})
                else:
                    emit({"type": "fix", "fix": fix, "tool": name})
            if got.get("ok"):
                content = brain_tools.result_text(got.get("result"))
            elif got.get("asked") and got.get("error") in _NOT_RUN:
                content = brain_tools.result_text({"error": got.get("error"),
                                                   "note": SAID_NO_NOTE})
            else:
                content = brain_tools.result_text({"error": got.get("error")})
            if fixes:
                content = _with_fix_note(content, fixes)
            if name not in READ_ONLY_TOOLS:
                failed = (name, _why(content)) if tool_failed(got) else None
            emit({"type": "tool_done", "tool": name, "ok": bool(got.get("ok"))})
            remember_result(conversation, name, args, content)
            row = {"role": "tool", "content": content, "tool_name": name}
            if call_id and not call_id.startswith("call_"):
                row["tool_call_id"] = call_id
            chat.append(row)
    # Out of steps (or the page went away): one last call with no tools.
    chat.append({"role": "system", "content": OUT_OF_STEPS_NOTE})
    msg = _ask_model(backend, fit_turn(chat, prompt_tokens, question_at), None, emit,
                     stream=failed is None)
    msg = _checked(backend, fit_turn(chat, prompt_tokens, question_at), msg, failed, emit)
    _say(msg)
    text = "\n\n".join(said).strip()
    if not text:
        emit({"type": "token", "text": EMPTY_ANSWER_LINE})
        text = EMPTY_ANSWER_LINE
    return _finish(text, steps + 1, msg.get("content") or "")


def _new_fixes(got: dict, shown: set) -> list[dict]:
    """The fix-it-now cards for one tool result not shown yet this turn."""
    try:
        from puffpai import fix_api
        found = fix_api.fixes_for(got)
    except Exception:  # noqa: BLE001 - a card we cannot make is no card
        logger.info("brain loop: no fix cards", exc_info=True)
        return []
    out = []
    for fix in found:
        key = (fix.get("kind"), fix.get("service") or fix.get("key") or fix.get("name"))
        if key not in shown:
            shown.add(key)
            out.append(fix)
    return out


def _with_fix_note(content: str, fixes: list[dict]) -> str:
    """The tool result with a note that she was shown a card for it."""
    from puffpai import fix_api
    return f"{content}\n{fix_api.note_for(fixes)}"


#: The longest reason kept on an activity line.
REASON_CHARS = 200


def _reason(got: dict) -> str:
    """The plain reason a tool call did not work, secrets hidden, or ""."""
    if not tool_failed(got):
        return ""
    if not got.get("ok"):
        text = str(got.get("error") or "")
    else:
        text = _why(brain_tools.result_text(got.get("result")))
    text = " ".join(text.split()) or "no reason was given."
    try:
        from puffpai import brain_commands
        text = brain_commands.hide_secrets(text)
    except Exception:  # noqa: BLE001 - without the hider, keep only the first words
        text = text[:60]
    return text[:REASON_CHARS]


def _log_tool(name: str, got: dict) -> None:
    """One tool call on the team page, under the agent she talks to. The tool
    name, and when it did not work the plain reason (secrets hidden); never
    its arguments or what it gave back."""
    from puffpai import agent_activity
    if got.get("asked") and got.get("error") in _NOT_RUN:
        status = "declined"
    else:
        status = "completed" if got.get("ok") else "error"
    reason = "" if status == "declined" else _reason(got)
    if reason:
        logger.warning("brain loop: %s did not work: %s", name, reason)
    agent_activity.record(agent_activity.CHAT_AGENT,
                          f"Used {str(name).replace('_', ' ')}",
                          status=status, tool=name, reason=reason)


def terminal_ask(question: dict) -> bool:
    """Ask in the terminal. Anything but yes is a no."""
    try:
        print(f"\n{question.get('title') or 'May I go ahead?'}")
        for key in ("command", "folder", "detail"):
            if question.get(key):
                print(f"  {key}: {question[key]}")
        reply = input("Type yes to go ahead, anything else to stop: ")
    except (EOFError, KeyboardInterrupt, OSError):
        return False
    return reply.strip().lower() in ("y", "yes")


def _ask_what(question: dict) -> str:
    return str(question.get("command") or question.get("tool") or "it")


def run_streaming(messages: list, *, ctx: dict, backend: Any,
                  tools: Optional[list] = None,
                  max_steps: int = MAX_STEPS,
                  prompt_tokens: Optional[int] = None) -> Iterator[dict]:
    """:func:`run` in a worker thread, as a stream of events for a web page.

    Events: ``{"type": "progress", "line"}``, ``{"type": "token", "text"}``,
    ``{"type": "ask", "id", "question"}`` (show Yes and No; the loop waits),
    ``{"type": "missing", "card"}``, ``{"type": "fix", "fix"}`` (a fix-it-now
    card, :mod:`puffpai.fix_api`),
    and last ``{"type": "done", "text", "used"}`` or
    ``{"type": "error", "error", "unsupported"}``.

    The device that may answer a question is ``ctx["device_id"]``. When the
    page goes away (the generator is closed), open questions become no."""
    events: "queue.Queue[Optional[dict]]" = queue.Queue()
    device = str(ctx.get("device_id") or "")
    cancelled = threading.Event()
    turn_ctx = dict(ctx, cancelled=cancelled)
    mine: list[str] = []

    def _ask(question: dict) -> bool:
        if cancelled.is_set():
            return False
        ask_id = open_ask(device, question, str(ctx.get("conversation_id") or ""))
        mine.append(ask_id)
        events.put({"type": "ask", "id": ask_id, "question": question})
        got = _wait(ask_id, ASK_SECONDS)
        yes = got is True
        events.put({"type": "answered", "id": ask_id, "yes": yes})
        if cancelled.is_set():
            line = ""
        elif yes:
            line = ASKED_LINE.format(what=_ask_what(question))
        else:
            line = SAID_NO_PROGRESS_LINE if got is False else NO_ANSWER_LINE
        events.put({"type": "progress", "line": line})
        # True for yes, False for her no, None when no answer came.
        return True if yes else (False if got is False else None)

    turn_ctx["ask"] = _ask

    def _work() -> None:
        try:
            got = run(messages, ctx=turn_ctx, backend=backend, tools=tools,
                      max_steps=max_steps, emit=events.put,
                      prompt_tokens=prompt_tokens)
            events.put({"type": "done", "text": got["text"], "used": got["used"],
                        "steps": got["steps"]})
        except ToolsUnsupported as exc:
            events.put({"type": "error", "error": str(exc), "unsupported": True})
        except BrainError as exc:
            events.put({"type": "error", "error": str(exc), "unsupported": False})
        except Exception as exc:  # noqa: BLE001 - the page gets a plain line
            logger.warning("brain loop failed: %s", type(exc).__name__, exc_info=True)
            events.put({"type": "error", "error": f"Something went wrong: {exc}",
                        "unsupported": False})
        finally:
            events.put(None)

    worker = threading.Thread(target=_work, name="brain-loop", daemon=True)
    worker.start()
    try:
        while True:
            event = events.get()
            if event is None:
                return
            if event.get("type") == "progress" and not event.get("line"):
                continue
            yield event
    finally:
        cancelled.set()
        cancel_asks(mine)


__all__ = ["ANY_DEVICE", "ROUTINE_ASK_SECONDS", "waiting_for_anyone", "ASK_SECONDS", "BrainError", "MAX_STEPS", "TOOL_NOTE", "ToolsUnsupported",
           "answer_ask", "cancel_asks", "take_answer", "claims_done", "tool_failed", "earlier_results_note", "open_ask", "pending_ask",
           "remember_result", "run", "run_streaming", "system_notes", "terminal_ask",
           "wait_ask"]
