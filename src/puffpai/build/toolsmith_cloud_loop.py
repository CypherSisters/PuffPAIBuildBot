"""Toolsmith's tool loop on its cloud model (Anthropic Messages API).

Used ONLY by :meth:`puffpai.build.toolsmith_run.Run.go` when the owner chose
Anthropic for Toolsmith (``security/toolsmith_cloud.py``). The local loop
(:func:`puffpai.brain_loop.run`) trims, rebuilds and re-orders turns to fit a
small local model; the cloud API forbids all of that, so this loop is its own:

* **Append-only.** One :class:`Conversation` per app try. The assistant's
  ``content`` block list (thinking blocks with their signatures, text,
  tool_use) is appended exactly as returned and sent back unchanged. No turn
  is ever edited, trimmed or re-ordered. A new app try starts a new
  Conversation. A try that would grow past the context ends with a summary.
* **Tool results in one message.** After a ``tool_use`` stop every tool_use
  block runs and ALL ``tool_result`` blocks go back in ONE user message
  (``is_error`` on failure).
* **stop_reason.** ``tool_use``: run the tools and go on. ``end_turn``: done
  talking (as the local loop's no-tool turn). ``max_tokens``: go on once.
  ``refusal``: the try ends with a plain line.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

from typing import Any, Optional

from puffpai.logging_config import get_logger

logger = get_logger(__name__)

CONTINUE_LINE = "Go on from where you stopped."
FULL_LINE = ("Toolsmith's notes for this app grew too long for one try, so it "
             "stopped this try here.")


class Conversation:
    """One app try's messages, append-only."""

    def __init__(self, system: str, task: str):
        self.system = str(system)
        self.messages: list[dict] = [{"role": "user",
                                      "content": [{"type": "text", "text": str(task)}]}]
        self.last_usage: dict = {}

    def add_user_text(self, text: str) -> None:
        """Her next line (KEEP_GOING). After an unanswered user turn the line
        joins it, as the API wants roles to alternate."""
        block = {"type": "text", "text": str(text)}
        if self.messages and self.messages[-1]["role"] == "user":
            self.messages[-1]["content"].append(block)
        else:
            self.messages.append({"role": "user", "content": [block]})


def anthropic_tools(tools: list) -> list[dict]:
    """The loop's tool list in the Messages shape (sorted by name)."""
    out = []
    for t in tools or []:
        spec = t.spec() if hasattr(t, "spec") else t
        fn = spec.get("function") if isinstance(spec, dict) else None
        if not isinstance(fn, dict) or not fn.get("name"):
            continue
        out.append({"name": str(fn["name"]),
                    "description": str(fn.get("description") or "")[:4000],
                    "input_schema": fn.get("parameters") or {"type": "object",
                                                             "properties": {}}})
    return sorted(out, key=lambda r: r["name"])


def _text_of(content: list) -> str:
    return "\n".join(str(b.get("text") or "") for b in content
                     if isinstance(b, dict) and b.get("type") == "text").strip()


def _tool_uses(content: list) -> list[dict]:
    return [b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"]


def run(conv: Conversation, *, scope: Any, ctx: dict, tools: list, max_steps: int,
        app_name: str = "") -> dict:
    """Run the loop on *conv* until the model is done talking, the steps run
    out, the try is cancelled, or the context is full. Returns ``{"text",
    "steps", "used", "stop"}`` with stop in ``end_turn, steps, cancel,
    refusal, full``. Raises ``toolsmith_cloud.CloudError`` (``CloudOffline``
    when Anthropic cannot be reached)."""
    from puffpai import brain_loop, brain_tools
    from puffpai.security import toolsmith_cloud as tc
    specs = anthropic_tools(tools)
    cancelled = ctx.get("cancelled")
    used: list[str] = []
    steps = 0
    continued = False
    text = ""
    while steps < max_steps:
        if cancelled is not None and cancelled.is_set():
            return {"text": text, "steps": steps, "used": used, "stop": "cancel"}
        if tc.context_tokens(conv.last_usage) > tc.CONTEXT_TOKENS:
            return {"text": FULL_LINE, "steps": steps, "used": used, "stop": "full"}
        steps += 1
        try:
            reply = tc.send(scope, system=conv.system, tools=specs,
                            messages=conv.messages, app_name=app_name)
        except tc.CloudError as exc:
            if exc.code == "too_big":
                return {"text": FULL_LINE, "steps": steps, "used": used, "stop": "full"}
            raise
        content = reply["content"]
        conv.last_usage = reply.get("usage") or {}
        # Exactly as returned: never rebuilt, stripped or re-ordered.
        conv.messages.append({"role": "assistant", "content": content})
        text = _text_of(content) or text
        stop = reply.get("stop_reason")
        calls = _tool_uses(content)
        if stop == "refusal":
            details = reply.get("stop_details")
            category = details.get("category") if isinstance(details, dict) else None
            line = "Anthropic's model would not help with this app"
            line += f" ({str(category)[:60]})." if category else "."
            return {"text": line, "steps": steps, "used": used, "stop": "refusal"}
        if calls:
            results = []
            for block in calls:
                name = str(block.get("name") or "")
                args = block.get("input") if isinstance(block.get("input"), dict) else {}
                got = brain_tools.call(name, args, ctx)
                used.append(name)
                if got.get("ok"):
                    out = brain_tools.result_text(got.get("result"))
                else:
                    out = brain_tools.result_text({"error": got.get("error")})
                row: dict[str, Any] = {"type": "tool_result",
                                       "tool_use_id": str(block.get("id") or ""),
                                       "content": out or "(empty)"}
                if brain_loop.tool_failed(got):
                    row["is_error"] = True
                results.append(row)
            conv.messages.append({"role": "user", "content": results})
            continue
        if stop == "max_tokens" and not continued:
            continued = True
            conv.add_user_text(CONTINUE_LINE)
            continue
        return {"text": text, "steps": steps, "used": used, "stop": "end_turn"}
    return {"text": text, "steps": steps, "used": used, "stop": "steps"}


__all__ = ["CONTINUE_LINE", "Conversation", "FULL_LINE", "anthropic_tools", "run"]
