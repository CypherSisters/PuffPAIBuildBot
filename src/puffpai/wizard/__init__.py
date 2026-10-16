"""The wizard as a pipeline of packages: validator, runner, built-in packages.

The runner walks a
fixed schedule; the twelve built-in packages each decide one thing through
``propose``/``apply``/``verify`` (``CONVERTING.md``), and the console and the
dashboard are two renderers of the one card. One writer per manifest section,
an honest per-skill run log, resume with its own screen, and gap records whose
fix is a registered command id rather than a string a package made up.

Nothing here executes code that did not ship inside this installed package.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

__all__ = ["fixes", "loader", "manifest", "runner"]
