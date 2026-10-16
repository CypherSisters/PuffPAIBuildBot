"""Connecting code ("glue") for assembled apps: the rules, the runner, the helper.

* :mod:`.check` is Drawbridge's ``glue`` checklist (it reads the code, never
  runs it).
* :mod:`.runner` is the program that runs one glue function in its own
  process (started by :func:`puffpai.assemble.toolsmith_loop.run_glue`).
* :mod:`.puff_glue` is the one helper glue may import: it asks BuildBot to
  fetch a page through the Bridge.
* :mod:`.skeletons` are the short starting points Toolsmith is shown.

This is not a sandbox. See :mod:`.runner` for what limits a glue run.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
