"""The assembled app server: its own small process, BuildBot's gateway, its plan.

* :mod:`.app` is the server program (standard library only, 127.0.0.1).
* :mod:`.gateway` is BuildBot's listener the app server and glue runs ask.
* :mod:`.host` is the backend side: folder, plan, start, actions, streams.

The assemble API (``puffpai.assemble.api``) calls :func:`start_assembled` and
:func:`call_action` here.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

from typing import Any


def start_assembled(app_id: str) -> dict:
    """Start the app's own server on 127.0.0.1: ``{"ok", "port", "reason"?}``."""
    from puffpai.assemble.server import host
    return host.start_assembled(app_id)


def call_action(app_id: str, action: str, input: Any = None) -> dict:  # noqa: A002
    """Run one spec action: ``{"ok", "rows", "reason"?, "fix"?}``."""
    from puffpai.assemble.server import host
    return host.call_action(app_id, action, input)


__all__ = ["call_action", "start_assembled"]
