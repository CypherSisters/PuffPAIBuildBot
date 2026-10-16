"""Discover: what this machine is. The Phase 2 reference conversion.

One skill, one section, one card. It merges what were two inline-prompting
steps -- the hardware census and the Ollama address survey -- because they
answer one question: what is this machine, and where does the thinking happen
on it (spec C.1).

Four calls, in order, and the split is the whole point:

* :func:`propose` measures. Read-only: a census and an address survey, this
  machine only, no LAN scan, nothing written and nothing asked.
* the card it returns is what the CLI and the dashboard both draw.
* :func:`apply` writes the ``machine`` section, using the owner's corrections
  if there were any and the proposal if there were not.
* :func:`verify` observes: does Ollama answer at the address that was recorded,
  and does the disk that was recorded exist. Offline is a gap, never a failure
  -- an air-gapped build is a build.

**Whether the internet answers is measured here, not asked**:
:mod:`puffpai.wizard.netcheck` opens one TCP connection to the
host a model download would come from and sends nothing; if that fails, or the
owner said ``--build-network offline``, nothing is downloaded while building.
Switched off (``PUFFPAI_WIZARD_NET_PROBE=off``, as the test suite runs), it is
recorded as *not checked* -- never as online.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import os

#: Where the reachability check connects (kept here for anything that reads
#: it off this package; the check itself lives in :mod:`puffpai.wizard.netcheck`).
PROBE_HOST = "registry.ollama.ai"


def internet_reachable() -> bool | None:
    """The one quiet check, :func:`puffpai.wizard.netcheck.internet_reachable`."""
    from puffpai.wizard import netcheck
    return netcheck.internet_reachable()


def _internet(ctx) -> bool | None:
    """The owner's word first (``--build-network``), then the measurement."""
    stated = str(getattr(ctx.args, "build_network", "") or "")
    if stated == "offline":
        return False
    applied = ctx.applied("internet", default=None)
    if isinstance(applied, dict) and "reachable" in applied:
        return applied["reachable"]
    return internet_reachable()


def _internet_words(reachable: bool | None) -> str:
    if reachable is True:
        return "reachable, so downloads can happen while building"
    if reachable is False:
        return "not reachable, so nothing is downloaded while building"
    return "not checked, so nothing is downloaded unless you turn it on"


#: An address the owner may type. Deliberately narrow: host or host:port, with
#: an optional scheme. ``brain_health.valid_url`` does the real parsing; this is
#: the card's own guard so nothing unparseable reaches it.
URL_PATTERN = r"^(?:https?://)?[A-Za-z0-9._-]{1,120}(?::\d{1,5})?/?$"


def _census(ctx) -> dict:
    """Today's census, with a stated RAM figure applied if one was given."""
    from puffpai import cli, hardware_census as hc

    census = hc.run_census()
    stated, reason = cli._valid_ram_gb(getattr(ctx.args, "stated_ram", None))
    if stated is not None:
        census = hc.apply_stated(census, total_ram_gb=stated)
    elif reason:
        census.setdefault("notes", []).append(f"Ignoring --stated-ram: {reason}")
    return census


def _survey(ctx) -> dict:
    """Where Ollama answers, and how sure we are. No prompting, no writing.

    The rules are the ones the wizard already followed, lifted out of the step
    that printed them: a CONFIGURED address is never guessed past (that is how
    a scripted install ends up talking to a different Ollama than the one it
    was told about), and only an unconfigured build is probed.
    """
    from puffpai import brain_health, cli, setup_ops

    start, source = cli._ollama_address_source(ctx.args, ctx.cfg)
    looked: list[dict] = []
    models = setup_ops.list_models(start)
    if models or source:
        return {"url": start, "source": source, "models": models,
                "looked": looked, "probed": False}

    looked = setup_ops.ollama_survey(start)
    found = brain_health.best_address(looked)
    if found:
        installed = next((row["models"] for row in looked
                          if row["url"] == found), None) or []
        return {"url": found, "source": "probing", "models": installed,
                "looked": looked, "probed": True}
    return {"url": start, "source": "", "models": None, "looked": looked,
            "probed": True}


def _address_options(survey: dict) -> list[dict]:
    """Every address that was looked at, with what was found there."""
    options = [{"value": survey["url"],
                "label": f"{survey['url']} -- {_found(survey['models'])}"}]
    for row in survey.get("looked") or ():
        url = row.get("url")
        if not isinstance(url, str) or url == survey["url"]:
            continue
        options.append({"value": url, "label": f"{url} -- {_found(row.get('models'))}"})
    return options[:20]


def _found(models) -> str:
    if models is None:
        return "nothing answered"
    if not models:
        return "answers, no models installed yet"
    return f"answers, {len(models)} model(s)"


def propose(ctx) -> dict:
    """Measure the machine and survey the address. Read-only (spec A.3)."""
    from puffpai import cli, hardware_census as hc

    cli.enter_step("Hardware census")
    census = _census(ctx)
    survey = _survey(ctx)
    memory = (census.get("memory") or {}).get("total_gb")
    # Start from what was applied, fall back to what was detected (the propose
    # rule, CONVERTING.md): a card re-drawn after an apply shows the address the
    # owner settled on, with this run's survey still offered beside it.
    applied_url = str(ctx.applied("ollama_url", default="") or "")

    options = _address_options(survey)
    if applied_url and applied_url not in {opt["value"] for opt in options}:
        options.insert(0, {"value": applied_url,
                           "label": f"{applied_url} -- what you chose here"})
    internet = _internet(ctx)
    fields = [
        {"id": "machine", "kind": "info", "label": "What this computer has",
         "current": " - ".join(hc.census_summary_lines(census))},
        # Measured, not asked. Retyping a figure this machine just reported is
        # not a question, and the one place a person can genuinely know better
        # than the census has its own flag (`puffpai setup --stated-ram`).
        {"id": "memory_gb", "kind": "info", "label": "Memory",
         "current": (f"{memory:g} GB" if memory else "not found")
                    + (" (you said this)"
                       if (census.get("memory") or {}).get("source") == "stated"
                       else " (measured on this computer)")},
        {"id": "internet", "kind": "info", "label": "The internet",
         "current": _internet_words(internet)},
        {"id": "ollama_url", "kind": "choice",
         "label": "Where Ollama, the program that runs the brain, answers",
         "options": options[:20], "current": applied_url or survey["url"],
         "proposed": survey["url"],
         "free_text": True, "pattern": URL_PATTERN,
         "help": "Pick one of these, or type its web address if Ollama runs "
                 "on another computer."},
    ]
    for note in census.get("notes") or ():
        fields.append({"id": f"note_{len(fields)}", "kind": "info",
                       "label": "Note", "current": str(note)})

    card = {
        "skill": "discover", "section": "machine",
        "title": "This computer",
        "decided": _decided(census, survey),
        "status": "ready" if memory else "needs_you",
        "consequential": False,
        "estimate": {"bytes": 0, "seconds": 0, "basis": "measured"},
        "fields": fields,
    }
    # What this card's apply would share if the proposal were accepted, so the
    # cards below it (Brain, Crew) size themselves against the address and the
    # machine this one just found rather than against the shipped defaults.
    # Read-only, like the rest of propose: nothing here is written anywhere.
    url = applied_url or survey["url"]
    preview = {"census": census, "ollama_url": url,
               "installed_models": (list(survey["models"] or [])
                                    if survey["models"] is not None
                                       and url == survey["url"] else None),
               "internet": internet}
    return {"card": card, "census": census, "survey": survey,
            "internet": internet, "shared_preview": preview}


def _decided(census: dict, survey: dict) -> str:
    memory = (census.get("memory") or {}).get("total_gb")
    disk = census.get("disk") or {}
    parts = [str((census.get("os") or {}).get("name") or "this computer")]
    if memory:
        parts.append(f"{memory:g} GB")
    if disk.get("free_gb"):
        parts.append(f"{disk['free_gb']:g} GB free on {disk.get('path') or 'disk'}")
    parts.append(f"Ollama at {survey['url']}")
    return " - ".join(parts)


def apply(ctx, answers: dict) -> dict:
    """Write the ``machine`` section: census, address, installed models.

    The same three keys the two Phase 1 steps wrote, for the same inputs. A
    stated memory figure overrides detection and is recorded as stated, which
    is what :func:`hardware_census.apply_stated` already means by it.
    """
    from puffpai import cli, brain_health, hardware_census as hc, setup_ops

    proposal = ctx.proposal or {}
    census = proposal.get("census") or _census(ctx)
    survey = proposal.get("survey") or {"url": "", "models": None}

    memory = answers.get("memory_gb")
    if memory is not None:
        census = hc.apply_stated(census, total_ram_gb=float(memory))

    url = survey.get("url") or ""
    models = survey.get("models")
    typed = answers.get("ollama_url")
    if typed and typed != url:
        parsed = brain_health.valid_url(typed)
        if parsed:
            url, models = parsed, setup_ops.list_models(parsed)

    # The posture is settled after the summary now, so the one thing that can
    # already forbid a home-network address is a flag the owner passed.
    posture = ctx.shared.get("posture") or (
        {"run_egress": str(getattr(ctx.args, "run_egress", "") or "")}
        if getattr(ctx.args, "run_egress", None) else None)
    violation = cli._posture_violation("ollama", url, posture)
    result: dict = {}
    if violation is not None:
        # Non-interactive: the refusal is the answer, and it is raised at the
        # step that captured the address rather than six steps later. With an
        # owner at the keyboard the address is dropped and the gap says so --
        # a wizard that stops dead on a correctable answer is worse than one
        # that records what it would not keep.
        if ctx.non_interactive:
            raise cli._refuse_for_posture(violation)
        result["gap"] = {
            "skill": "discover",
            "what": "The Ollama address you gave was not written down.",
            "why": getattr(violation, "user_message", str(violation)),
            "impact": "Your bot will look for Ollama at "
                      f"{brain_health.DEFAULT_URL} instead.",
            "fix": {"kind": "command", "id": "setup.rerun", "args": {}},
        }
        url, models = brain_health.DEFAULT_URL, None

    # ``None`` when nothing answered, never ``[]``: "no brains downloaded" and
    # "nothing to ask" are two different sentences on the Brain card.
    installed = list(models) if models is not None else None
    internet = proposal.get("internet") if "internet" in proposal \
        else _internet(ctx)
    result.update({
        "section": "machine",
        "data": {"census": census, "ollama_url": url,
                 "installed_models": installed,
                 "internet": {"reachable": internet,
                              "checked": internet is not None,
                              "host": PROBE_HOST}},
        "shared": {"census": census, "ollama_url": url,
                   "installed_models": installed, "internet": internet},
    })
    return result


def _same_disk_there(path: str) -> bool:
    """Is the disk *path* is on still here? The data folder itself may not
    exist yet: this computer is checked first, before anything is written,
    so the nearest folder above it that exists stands for the
    same disk. Only a disk that is gone (a drive letter no longer there) is
    "not on this computer"."""
    probe = os.path.abspath(path)
    while not os.path.exists(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            return False
        probe = parent
    return True


def verify(ctx) -> dict:
    """Observe the world, not the proposal (spec A.3): address, then disk."""
    from puffpai import setup_ops

    section = ctx.own()
    url = section.get("ollama_url") or ""
    census = section.get("census") or {}
    path = (census.get("disk") or {}).get("path")

    if path and not _same_disk_there(path):
        return {"status": "gap",
                "evidence": f"the recorded disk {path} is not there",
                "gap": {"skill": "discover",
                        "what": "The disk this build recorded is not on this "
                                "computer.",
                        "why": f"{path} does not exist now, though the census "
                               f"found it.",
                        "impact": "Size and free-space figures for your packs "
                                  "will be wrong.",
                        "fix": {"kind": "command", "id": "setup.rerun",
                                "args": {}}}}

    models = setup_ops.list_models(url) if url else None
    if models is not None:
        return {"status": "pass",
                "evidence": f"{url} answered with {len(models)} model(s)"}
    return {"status": "gap",
            "evidence": f"nothing answered at {url}",
            "gap": {"skill": "discover",
                    "what": "Nothing answered at the Ollama address.",
                    "why": f"{url} did not respond when the build checked it.",
                    "impact": "Your bot cannot answer anything until Ollama is "
                              "running there.",
                    "fix": {"kind": "command", "id": "setup.rerun", "args": {}}}}
