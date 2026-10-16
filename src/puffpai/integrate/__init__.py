"""Toolsmith integration: outside code the owner ticked, fetched, checked, added.

When the owner says "Looks good", every ticked find is

1. fetched at exactly the version pinned when the plan was made
   (:mod:`puffpai.integrate.fetch`),
2. sorted into what kind of project it is (:mod:`puffpai.integrate.classify`),
3. checked by Drawbridge (:func:`puffpai.drawbridge.check_addition`, kind
   ``code_project``), and
4. added in its mode (:mod:`puffpai.integrate.apply`), unless the check found
   something the plan did not show, in which case it waits for its own yes.

Every integrated thing becomes actions the bot's AI can call from chat
(:mod:`puffpai.integrate.actions`, :mod:`puffpai.integrate.chat`), only for the
owner's paired devices or the terminal.

Other packages' modules are reached only through :func:`optional`: a module that
is absent means "not in this build yet" and the caller fails closed; a module
that exists but cannot be imported raises.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import importlib


def optional(modname: str):
    """The module *modname*, or None when it is not in this build.

    Pinned helper (design I-10). Never ``except ImportError``: a module that
    exists but fails to import (a missing dependency of its own) raises.
    """
    try:
        return importlib.import_module(modname)
    except ModuleNotFoundError as e:
        if e.name is not None and (e.name == modname or modname.startswith(e.name + ".")):
            return None          # not in this build yet
        raise                    # exists but broken: fail loudly


__all__ = ["optional"]
