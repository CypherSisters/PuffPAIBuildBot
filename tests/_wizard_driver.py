"""A prompt-following driver for the console wizard.

Two ways to drive ``puffpai setup`` the way a person would -- read what is on
the screen, answer the question that was just asked:

* :func:`drive_in_process` swaps the console's line readers for a function
  that answers from a script keyed on words in the question. Fast; what most
  tests use.
* :func:`drive_subprocess` runs the real command in a child process with its
  stdin and stdout on pipes, waits for the answer mark (``  > ``), and answers
  from the same kind of script. Slower; it proves the wizard works over a pipe
  exactly as a person at a keyboard would see it.

A script is a list of ``(words, answer)``: the first entry whose words appear
(case-insensitive) in the text since the last answer wins. Anything nobody
scripted gets Enter, which is what a person accepting the plan would press.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from typing import Iterable

ANSWER_MARK = "  > "

#: A wizard that asks more than this is looping on an answer it will not take.
MAX_QUESTIONS = 80


def _answer_for(screen: str, script: Iterable[tuple[str, object]],
                used: list[str]) -> str:
    """The first script entry whose words are on screen. An answer that is a
    list is a sequence: each match takes the next one, and once it is used up
    the entry stops matching."""
    lowered = screen.lower()
    for words, answer in script:
        if words.lower() not in lowered:
            continue
        if isinstance(answer, list):
            taken = used.count(words)
            if taken >= len(answer):
                continue
            used.append(words)
            return str(answer[taken])
        used.append(words)
        return str(answer)
    return ""


def drive_in_process(argv: list[str], script: list[tuple[str, str]], *,
                     monkeypatch, capsys) -> tuple[int, str, list[str]]:
    """Run ``cli.main(argv)`` answering from *script*. Returns
    ``(exit code, transcript, prompts answered)``.

    The transcript is everything printed plus each question and the answer
    given, the way it would look on a screen.
    """
    from puffpai import cli
    from puffpai.wizard import render_cli

    transcript: list[str] = []
    asked: list[str] = []
    used: list[str] = []

    def flush() -> str:
        out = capsys.readouterr().out
        transcript.append(out)
        return out

    def reader(prompt: str) -> str:
        if len(asked) > MAX_QUESTIONS:
            raise RuntimeError("the wizard kept asking; a script answer does "
                               "not fit the question it was matched to")
        screen = flush() + prompt
        answer = _answer_for(screen[-600:], script, used)
        asked.append(prompt)
        transcript.append(f"{prompt}\n{ANSWER_MARK}{answer}\n")
        return answer

    monkeypatch.setattr(render_cli, "_read_line", reader)
    monkeypatch.setattr(render_cli, "_read_secret", reader)
    capsys.readouterr()
    code = cli.main(list(argv))
    flush()
    return code, "".join(transcript), asked


def drive_subprocess(argv: list[str], script: list[tuple[str, str]], *,
                     env: dict | None = None, cwd: str | None = None,
                     timeout: float = 600.0) -> tuple[int, str, list[str]]:
    """Run ``python -m puffpai.cli <argv>`` over pipes, answering prompts.

    Returns ``(exit code, transcript, questions answered)``. The transcript is
    the child's own output with each answer written after its ``  > `` mark,
    so it reads like the screen did.
    """
    child_env = dict(os.environ)
    child_env.update(env or {})
    child_env.setdefault("PYTHONIOENCODING", "utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "puffpai.cli", *argv],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, cwd=cwd, env=child_env)
    buffer: list[str] = []
    lock = threading.Lock()
    done = threading.Event()

    def pump() -> None:
        while True:
            chunk = proc.stdout.read1(4096) if hasattr(
                proc.stdout, "read1") else proc.stdout.read(1)
            if not chunk:
                break
            with lock:
                buffer.append(chunk.decode("utf-8", "replace"))
        done.set()

    threading.Thread(target=pump, daemon=True).start()
    transcript = ""
    seen = 0
    asked: list[str] = []
    used: list[str] = []
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(0.05)
        with lock:
            text = "".join(buffer)
        if len(text) > seen and text.endswith(ANSWER_MARK):
            if len(asked) > MAX_QUESTIONS:
                proc.kill()
                break
            screen = text[seen:]
            answer = _answer_for(screen[-600:], script, used)
            asked.append(screen.strip().splitlines()[-1] if screen.strip()
                         else "")
            with lock:
                buffer.append(answer + "\n")
            seen = len(text) + len(answer) + 1
            try:
                proc.stdin.write((answer + "\n").encode("utf-8"))
                proc.stdin.flush()
            except OSError:
                break
            continue
        if done.is_set() and proc.poll() is not None:
            break
    else:
        proc.kill()
    try:
        proc.stdin.close()
    except OSError:
        pass
    code = proc.wait(timeout=30)
    done.wait(timeout=10)
    with lock:
        transcript = "".join(buffer).replace("\r\n", "\n")
    return code, transcript, asked


def questions_before(transcript: str, marker: str) -> int:
    """How many answer marks come before *marker* in a transcript."""
    head = transcript.split(marker, 1)[0]
    return head.count(ANSWER_MARK)
