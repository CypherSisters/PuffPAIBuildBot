"""Assemble: turn what was found for each capability into a working app.

For every capability the bot must have, BuildBot decides one of three things
(:mod:`.plan`): use a whole app or skill that already does the job, put one
together from parts (live data from an outside service, an app's own calls, a
library, a command line tool, a stream), or say plainly that nothing was found.
A put-together app is described by one ``assembled_app/v1`` spec (:mod:`.spec`)
built from one real sample of each part (:mod:`.shape`); each capability is
then proven with a real ask (:mod:`.accept`) and the result kept for the
dashboard and the closing screen (:mod:`.report`).

Files, all under ``<state>/assemble/``::

    plan.json          what was decided per capability
    status.json        what is happening now ("Putting together: 3 of 5")
    report.json        per capability: working, or not yet and why
    <app id>/spec.json the assembled app's spec

Goal-agnostic: nothing in this package knows any subject.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import os
import re

SPEC_SCHEMA = "assembled_app/v1"
PLAN_SCHEMA = "assemble_plan/v1"
REPORT_SCHEMA = "assemble_report/v1"

#: An assembled app's id: the same rule as an installed app's id, so the app
#: host, the Bridge and the dashboard all accept it.
APP_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")


def state_dir(*parts: str) -> str:
    """``<state>/assemble/<parts...>``."""
    from puffpai.config import get_state_dir
    return os.path.join(get_state_dir(), "assemble", *parts)


def app_dir(app_id: str) -> str:
    if not APP_ID_RE.match(str(app_id or "")):
        raise ValueError("not an assembled app id")
    return state_dir(app_id)


__all__ = ["APP_ID_RE", "PLAN_SCHEMA", "REPORT_SCHEMA", "SPEC_SCHEMA", "app_dir",
           "state_dir"]
