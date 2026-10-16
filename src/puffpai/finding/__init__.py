"""Finding things other people built, and installing what she picks.

* :mod:`.find`: ``find_things(words, kinds)`` and ``find_for_goal(goal)``,
  one list grouped by her jobs and labelled by type, from the skill shelves
  (ClawHub, skills.sh, the Hermes list), the MCP Registry, and GitHub,
  Codeberg, npm, PyPI and awesome-selfhosted, each ranked the way the site
  ranks it.
* :mod:`.install`: ``preview(id)`` then ``install_thing(id)`` and ``undo``.
* :mod:`.tools`: the chat loop's ``find_things``, ``install_thing`` and
  ``skill_view`` tools.
* :mod:`.api`: the routes the wizard's "Finding things for your bot" screen
  and the Add page use.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
