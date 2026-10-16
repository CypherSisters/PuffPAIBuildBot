"""The locked box: where BuildBot runs outside code.

On Windows the box is one Alpine distro under Windows' Linux feature (WSL),
used ONLY for outside code, with bubblewrap around every run. On Linux it is
bubblewrap on the host. The owner reads it as "the locked box".

Modules:

* :mod:`puffpai.box.driver` -- the one place that talks to ``wsl.exe``: the
  box's name, status, setup, runs, projects and removal through the guard.
* :mod:`puffpai.box.wording` -- every sentence the owner reads about the box.
* :mod:`puffpai.box.jail` -- the one bubblewrap builder (copied into the box).
* :mod:`puffpai.box.locks` -- recorded dependency locks (nothing updates by itself).
* :mod:`puffpai.box.cli_tools` -- command-line tools from npm or PyPI as actions.
* :mod:`puffpai.box.plan` -- the box's plan line and its applier.
* :mod:`puffpai.box.api`, :mod:`puffpai.box.wiring` -- routes and start-up wiring.
* ``assets/`` -- the files setup streams into the box (launcher, proxy, setup script).

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""
