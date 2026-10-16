"""A key split over lines is caught like one on a single line.

A key wrapped onto new lines (``\\n``,
``\\r\\n``, or a mix of spaces, tabs and line breaks) was not caught. The
grouped-key rule read only spaces and tabs between the groups, and nothing
joined a key a text box had wrapped. Such a key was saved in the chat history
and printed in the "[Chat] Route: rag for:" log line.

Now any whitespace between groups counts, a key wrapped at one width is
joined up and judged, and every chat and agent log line that prints what a
person wrote goes through one key-safe preview (``web_search.log_preview``).
"""
from __future__ import annotations

import json
import os
import random
import re
import string

import pytest

from puffpai import api_server, web_search

AUTH = {"Authorization": f"Bearer {api_server._API_KEY}"}

HEX64 = "8f3a9c1e7b2d4f6a0c5e9b1d3f7a2c4e6b8d0f1a3c5e7b9d2f4a6c8e0b1d3f5a"
B62 = "Qz8vN3xT1pL6wR9mK2cY5hB7dF4gJ0sAe2Rt"


def _wrap(key: str, width: int, sep: str) -> str:
    return sep.join(key[i:i + width] for i in range(0, len(key), width))


#: The wrapped shape and its neighbours: one key, many ways of splitting it.
WRAPPED = {
    "groups on new lines": "AB12\nCD34\nEF56\nGH78\nIJ90\nKL12\nMN34",
    "groups on crlf lines": "AB12\r\nCD34\r\nEF56\r\nGH78\r\nIJ90\r\nKL12\r\nMN34",
    "groups, mixed whitespace": "AB12 \nCD34\t\r\nEF56  GH78\n\n IJ90\tKL12 \r\n MN34",
    "groups then a word": "here:\nAB12\nCD34\nEF56\nGH78\nIJ90\nKL12\nMN34\nthanks",
    "hex wrapped at 32": _wrap(HEX64, 32, "\n"),
    "hex wrapped at 16, crlf": _wrap(HEX64, 16, "\r\n"),
    "hex wrapped at 20, in a sentence": "my key is\n" + _wrap(HEX64, 20, "\n") + "\nthanks",
    "base62 wrapped at 12": _wrap(B62, 12, "\n"),
    "base62 wrapped at 18, indented": _wrap(B62, 18, "\n    "),
    "base62 wrapped, mixed breaks": B62[:12] + " \r\n" + B62[12:24] + "\n\t" + B62[24:],
    "begins mid-line": "the key: " + B62[:10] + "\n" + B62[10:],
}

#: Text with line breaks that must not read as a wrapped key.
LEFT_ALONE = [
    "Roses are red\nviolets are blue\nsugar is sweet\nand so are you",
    "eggs\nmilk\nbread\nbutter\ncheese\nspinach\ntomatoes",
    "2026-09-24\n2026-09-25\n2026-09-26\n2026-09-27",
    "### JSON5.stringify()\nConverts a JavaScript value to a JSON5 string",
    "def test_x(client, scratch,\n           monkeypatch):\n    from puffpai import api_server",
    "        tile = tk.Canvas(parent, bg=self.T.C_GLASS,\n"
    "                         highlightthickness=0, bd=0)",
    "        cards_frame = tk.Frame(content, bg=self.T.C_BG)\n        cards_frame.pack()",
    "Flight BA2490\nSeat 14C\nGate B32\nTerminal 5",
    "Call Dr. O'Neil\nat (555) 123-4567\next. 89",
    "Windows 11 Pro\n23H2 build 22631",
]


@pytest.mark.parametrize("name", sorted(WRAPPED))
def test_a_key_split_over_lines_is_caught(name):
    assert web_search.looks_like_pasted_key(WRAPPED[name]), name


@pytest.mark.parametrize("text", LEFT_ALONE, ids=lambda t: t[:24])
def test_text_with_line_breaks_is_still_left_alone(text):
    assert not web_search.looks_like_pasted_key(text)


def test_nearly_every_wrapped_random_key_is_caught():
    """Hex and letters-and-digits keys, wrapped at every width from 8 to one
    short of their length, with every kind of line break."""
    rng = random.Random(20260924)
    alphabets = {"hex": "0123456789abcdef",
                 "b62": string.ascii_letters + string.digits}
    seps = ["\n", "\r\n", " \n", "\n\t", "\r\n  "]
    missed, total = [], 0
    for name, alphabet in alphabets.items():
        for length in (32, 40, 64):
            for width in range(8, length):
                key = "".join(rng.choice(alphabet) for _ in range(length))
                for sep in seps:
                    total += 1
                    if not web_search.looks_like_pasted_key(_wrap(key, width, sep)):
                        missed.append((name, length, width, sep))
    # A random draw can read like words now and then; all but a few percent.
    assert len(missed) <= 0.03 * total, missed[:10]


def test_a_long_message_of_short_lines_stays_quick():
    import time
    text = "\n".join(f"line{i:04d}" for i in range(20000))
    start = time.perf_counter()
    web_search.looks_like_pasted_key(text)
    assert time.perf_counter() - start < 10


# ── the one key-safe preview ────────────────────────────────────────────────

@pytest.mark.parametrize("name", sorted(WRAPPED))
def test_the_log_preview_never_shows_a_wrapped_key(name):
    for preview in (web_search.log_preview(WRAPPED[name], 500),
                    api_server._pii_log_preview(WRAPPED[name], 500)):
        assert preview == web_search.KEY_LOG_LINE


def test_the_log_preview_shows_ordinary_words():
    assert web_search.log_preview("what is a comet?") == "what is a comet?"


_CHAT_LOG_MARKERS = re.compile(
    r"Route: |Error in processing|Refusal-fix|Verify-fix|Intent-fix|"
    r"Auto-verified fixes|Conversation error|Chat (?:history|send|stream) error|"
    r"\[RAG\]|add-more: chat turn|\[Voice API\]")


def _log_calls(source: str) -> list[str]:
    """Every print(...) / logger.x(...) call, whole (brackets balanced)."""
    calls = []
    for match in re.finditer(r"\b(?:print|logger\.\w+)\(", source):
        depth, i = 0, match.end() - 1
        while i < len(source):
            depth += {"(": 1, ")": -1}.get(source[i], 0)
            if depth == 0:
                break
            i += 1
        calls.append(source[match.start():i + 1])
    return calls


def test_every_chat_log_line_that_prints_words_uses_the_preview():
    """A chat log line that prints a variable prints it through the preview."""
    path = os.path.join(os.path.dirname(api_server.__file__), "api_server.py")
    with open(path, encoding="utf-8") as fh:
        source = fh.read()
    lines = [c for c in _log_calls(source) if _CHAT_LOG_MARKERS.search(c)]
    assert len(lines) >= 20
    bad = []
    for line in lines:
        # What is left once every preview call is taken out must not print
        # the person's words, or an error or a fix result that may quote
        # them. A fixed label ({route}, label) is not their words.
        rest = line
        while "_pii_log_preview(" in rest:
            start = rest.index("_pii_log_preview(")
            end = start + len("_pii_log_preview(") - 1
            depth = 0
            while end < len(rest):
                depth += {"(": 1, ")": -1}.get(rest[end], 0)
                if depth == 0:
                    break
                end += 1
            rest = rest[:start] + "PREVIEW" + rest[end + 1:]
        words = r"(?:user_message|message|text|result|fixes|e|exc)\b"
        if re.search(r"\{" + words + r"|,\s*" + words + r"|join\(fixes", rest):
            bad.append(line.split("\n")[0][:100])
    assert not bad, bad


def test_the_agents_log_errors_through_the_preview():
    from puffpai.agents import concierge, toolsmith
    for module in (concierge, toolsmith):
        with open(module.__file__, encoding="utf-8") as fh:
            source = fh.read()
        assert re.search(r"failed: %s\",\s*(?:intent,\s*)?\n?\s*log_preview\(",
                         source), module.__name__


# ── through the real chat routes ────────────────

@pytest.fixture
def chat(monkeypatch):
    seen: list[str] = []

    def _model(*a, **k):
        seen.append(json.dumps([a, k], default=str))
        return "a model answer"
    for name in ("_ollama_chat", "_query_ollama_direct"):
        monkeypatch.setattr(api_server, name, _model)
    monkeypatch.setattr(api_server, "_stream_ollama_tokens",
                        lambda *a, **k: iter([_model(*a, **k)]))
    api_server.app.config.update(TESTING=True)
    return api_server.app.test_client(), seen


def _pieces(text: str) -> list[str]:
    return [p for p in re.split(r"\s+", text) if len(p) >= 4]


@pytest.mark.parametrize("route", ["/chat/send", "/chat/stream"])
@pytest.mark.parametrize("name", ["groups on new lines", "groups on crlf lines",
                                  "groups, mixed whitespace", "hex wrapped at 32"])
def test_a_wrapped_key_is_not_saved_logged_or_sent(chat, capsys, route, name):
    client, seen = chat
    key = WRAPPED[name]
    cid = "wrapped" + re.sub(r"\W", "", route + name)
    r = client.post(route, headers=AUTH,
                    json={"message": key, "conversation_id": cid})
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Sign-up keys page" in body
    out = capsys.readouterr()
    printed = out.out + out.err
    path = os.path.join(api_server._get_chat_history_dir(), f"{cid}.json")
    with open(path, encoding="utf-8") as fh:
        saved = fh.read()
    with api_server._chat_history_lock:
        memory = json.dumps(list(api_server._chat_history))
    joined = re.sub(r"\s+", "", key)
    for piece in _pieces(key) + [joined]:
        if piece in web_search.KEY_PASTE_REPLY:
            continue
        assert piece not in printed, piece
        assert piece not in saved and piece not in memory, piece
        assert piece not in body, piece
    assert seen == []
