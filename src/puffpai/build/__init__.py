"""Build: the one foundation every bot is made with.

One pipeline (:mod:`.pipeline`) for any goal: interpret the goal into jobs,
find candidates, classify each by how it runs, decide one route per job
(A uses an app as it is, B connects a tool, C puts together a view from data
sources, D nothing found yet), check, collect ONE setup list, ask ONE yes,
then make and prove each job in the background.

Everything lives in ``<state>/build/build.json`` (schema ``build/v1``, see
:mod:`.schema`). Goal-agnostic: nothing here knows any subject.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

__all__: list[str] = []
