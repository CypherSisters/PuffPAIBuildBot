"""No console window, ever: the keyword arguments for every program BuildBot starts.

The owner saw console windows flash on her screen while BuildBot worked. On
Windows a console program started from a process without a console of its
own (the backend, the dashboard, a glue run) gets a NEW console window unless
it is told not to. :func:`hidden` gives the arguments that prevent that, for
``subprocess.Popen`` / ``run`` / ``check_output``::

    subprocess.run(argv, ..., **procflags.hidden())
    subprocess.Popen(argv, ..., **procflags.hidden(CREATE_SUSPENDED))

* ``creationflags`` always includes ``CREATE_NO_WINDOW``. Any flag the
  caller passes is kept, except ``CREATE_NEW_CONSOLE`` and
  ``DETACHED_PROCESS``: Windows ignores ``CREATE_NO_WINDOW`` next to either,
  and a detached program's own children open windows.
* ``startupinfo`` asks for the window to be hidden (``SW_HIDE``). That covers
  a program that starts another one itself and passes its start-up settings
  on, like a venv's ``python.exe`` (a small launcher that starts the real
  Python).

Elsewhere (Linux, macOS) it gives back nothing: there are no console windows
to hide, and ``creationflags`` must stay 0 there.

``tests/test_procflags.py`` reads every file under ``src/puffpai`` and fails
on a program start that does not use this (a short list of exceptions says
why each one is left out).

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import os
import subprocess
from typing import Any, Optional

CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_CONSOLE = 0x00000010
DETACHED_PROCESS = 0x00000008
STARTF_USESHOWWINDOW = 0x00000001
SW_HIDE = 0

_IS_WINDOWS = os.name == "nt"


def flags(extra: int = 0) -> int:
    """*extra* with ``CREATE_NO_WINDOW`` added and the window-making flags
    taken out (0 off Windows)."""
    if not _IS_WINDOWS:
        return 0
    return (int(extra) | CREATE_NO_WINDOW) & ~(CREATE_NEW_CONSOLE | DETACHED_PROCESS)


def hidden(extra: int = 0, startupinfo: Optional[Any] = None) -> dict:
    """``{"creationflags", "startupinfo"}`` that keep any window from showing
    (``{}`` off Windows). *extra*: the caller's own flags, kept. *startupinfo*:
    the caller's own, changed to hide the window."""
    if not _IS_WINDOWS:
        return {}
    info = startupinfo if startupinfo is not None else subprocess.STARTUPINFO()
    info.dwFlags |= STARTF_USESHOWWINDOW
    info.wShowWindow = SW_HIDE
    return {"creationflags": flags(extra), "startupinfo": info}


def own_group(pid: Any) -> Optional[int]:
    """The process group to signal to stop *pid* with its children, or
    ``None`` when only *pid* itself may be signalled (Linux, macOS).

    A group is only answered when *pid* leads it (it was started in a new
    session, as BuildBot starts its children) and it is not this process's
    own group. Signalling the group of a process that does not lead one
    would reach whatever started it too: on CI, stopping a test's child
    that way stopped the test runner. Always ``None`` on Windows.
    """
    if _IS_WINDOWS:
        return None
    try:
        pid = int(pid)
        group = os.getpgid(pid)
    except (OSError, TypeError, ValueError):
        return None
    try:
        mine = os.getpgrp()
    except OSError:
        return None
    if group != pid or group == mine or group <= 1:
        return None
    return group


__all__ = ["CREATE_NEW_CONSOLE", "CREATE_NO_WINDOW", "DETACHED_PROCESS", "SW_HIDE", "flags",
           "hidden", "own_group"]
