"""Brain: the model this machine runs, and the check that it answers.

Phase 2 (spec C.3). One card where there were two steps, and the split falls
where the lifecycle already puts it:

* :func:`propose` fits the catalog to this machine -- the recommendation, the
  alternatives, the routing tier and what that tier costs, and an honest size
  for anything that would have to be downloaded. Read-only.
* :func:`apply` records the choice and, only if the owner said yes on the card
  and the posture allows a download, pulls the model **through the path that
  already exists** (``brain_health.pull_model``, gated by
  ``gates.may_download_models``). No new outbound call site: the egress
  call-site guard in ``tests/test_dashboard_weather_egress_doors.py`` stays green
  because nothing here calls ``requests``.
* :func:`verify` asks the Ollama this build is configured for whether the model
  is there.

**What blocks.** Brain is one of the two skills that may (spec B.1), and today's
wizard uses that allowance for nothing: a missing model ends with
``ready: false``, a pull command on the closing screen and exit code 0. So
verify returns a **gap**, never an exception, and this conversion neither adds
a block nor removes one.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

#: A model name the owner may type instead of picking. Ollama's own shape, and
#: the **tag is required**: ``qwen3:30b-a3b``, ``library/llama3.2:3b``. The tag
#: is what makes this a model name rather than a word -- the previous pattern
#: accepted any bare word, so a bot called Infinity was accepted as a model and
#: nothing said so until verify could not find it on the Ollama.
MODEL_PATTERN = (r"^(?:[A-Za-z0-9][A-Za-z0-9._-]{0,60}/){0,2}"
                 r"[A-Za-z0-9][A-Za-z0-9._-]{0,80}:[A-Za-z0-9._-]{1,40}$")

_GB = 1024 ** 3


# -- what the run knows about this machine ------------------------------------

def _machine(ctx) -> dict:
    """Census, address and installed models, from the run or from the section."""
    shared = getattr(ctx, "shared", None) or {}
    census = shared.get("census")
    url = shared.get("ollama_url")
    installed = shared.get("installed_models")
    if census is None or url is None:
        section = {}
        try:
            section = ctx.read("machine")
        except Exception:  # pragma: no cover - a package always declares it
            section = {}
        census = census if census is not None else section.get("census")
        url = url if url is not None else section.get("ollama_url")
        if installed is None:
            installed = section.get("installed_models")
    return {"census": census or {}, "url": str(url or ""),
            "installed": installed}


def _menu(ctx, machine: dict, gates: dict) -> dict:
    from puffpai import model_catalog, setup_ops

    installed = machine["installed"]
    if installed is None:
        installed = setup_ops.list_models(machine["url"]) if machine["url"] else None
    menu = model_catalog.fit_menu(
        machine["census"], installed_models=list(installed or []),
        prefer_hosted_tier=bool(ctx.non_interactive
                                and not getattr(ctx.args, "model", None)))
    options = list(menu.get("options") or [])
    if not gates.get("may_download_models", True):
        # An offline build is never offered a model it would have to download,
        # and the card says why rather than quietly showing a shorter list.
        options = [opt for opt in options if opt.get("installed")]
    menu = dict(menu)
    menu["options"] = options
    menu["installed_models"] = installed
    return menu


def _default_model(ctx, menu: dict) -> tuple[str, bool]:
    """The model the card proposes, and whether the owner named it.

    What was applied first (the propose rule, CONVERTING.md), then the flag,
    then the fit. A card re-drawn after an apply shows the model that was
    recorded, not the recommendation it was recorded instead of.
    """
    applied = ctx.applied("choice", default={})
    applied_model = str((applied or {}).get("model") or "")
    if applied_model:
        return applied_model, str((applied or {}).get("chosen_by")) == "owner"
    # A brain picked on the summary under "Change something".
    summary = (getattr(ctx, "shared", None) or {}).get("summary")
    if isinstance(summary, dict) and summary.get("approved") \
            and summary.get("model"):
        return str(summary["model"]), True
    # The model picked on the first screen ("Let's see what we're working with
    # here"), so the plan's "How it thinks" line is the one chosen there.
    first = (getattr(ctx, "shared", None) or {}).get("first_look")
    if isinstance(first, dict) and first.get("model"):
        return (str(first["model"]),
                str(first["model"]) != str(first.get("recommended") or ""))
    stated = getattr(ctx.args, "model", None)
    if stated:
        return str(stated), True
    rec = menu.get("recommended") or {}
    on_menu = {str(opt.get("id")) for opt in menu.get("options") or ()}
    if rec.get("id") and str(rec["id"]) in on_menu:
        return str(rec["id"]), False
    from puffpai import model_catalog
    configured = str(getattr(getattr(ctx.cfg, "ollama", None), "model", "") or "")
    return model_catalog.fitting_fallback(menu, configured), False


# -- propose ------------------------------------------------------------------

def propose(ctx) -> dict:
    """Fit the catalog to this machine. Reads, measures, downloads nothing."""
    from puffpai import brain_health, cli

    cli.enter_step("The brain")
    gates = (getattr(ctx, "shared", None) or {}).get("gates") or {}
    machine = _machine(ctx)
    menu = _menu(ctx, machine, gates)
    options = menu.get("options") or []
    model, by_owner = _default_model(ctx, menu)
    chosen_opt = next((opt for opt in options if str(opt.get("id")) == model),
                      None)
    # Installed means on this Ollama now, whether or not it is on the fit
    # menu: a model named on a smaller computer than the list asks for is
    # still there, and it is never offered as a download.
    installed = bool((chosen_opt or {}).get("installed")) or bool(
        model and brain_health.is_installed(model, list(machine["installed"] or [])))
    settled = None
    if model and not installed:
        # Another known address may already hold it (a relay on 11434 while
        # the brain is on 11435). Apply moves there (_settle_address), so the
        # card and the plan must say it is already here, not "Download".
        moved = _settle_address(ctx, machine, model)
        if moved:
            settled = dict(moved, model=model)
            machine = dict(machine, url=moved["url"], installed=moved["installed"])
            options = [dict(opt, installed=brain_health.is_installed(
                           str(opt.get("id")), moved["installed"]))
                       for opt in options]
            menu = dict(menu, options=options, installed_models=moved["installed"])
            chosen_opt = next((opt for opt in options
                               if str(opt.get("id")) == model), None)
            installed = True
    may_download = bool(gates.get("may_download_models", True))
    disk_gb = (chosen_opt or {}).get("disk_gb")

    fields = [{"id": "found", "kind": "info", "label": "Ollama",
               "current": _ollama_line(machine)}]
    choices = [_model_option(opt, menu) for opt in options][:40]
    # Every model this Ollama already has is a real answer, whether or not the
    # catalog knows it. Offering them is what makes "type your own" the rare
    # case rather than the only way to name what is installed.
    offered = {item["value"] for item in choices}
    for name in (machine["installed"] or ()):
        name = str(name)
        if name and name not in offered and len(choices) < 60:
            offered.add(name)
            choices.append({"value": name,
                            "label": f"{name} (already on this computer)"})
    if choices:
        fields.append({
            "id": "model", "kind": "choice", "label": "Which brain it uses",
            "options": choices,
            "current": model if (chosen_opt or model in offered) else "",
            "free_text": True, "pattern": MODEL_PATTERN,
            "help": "Pick one of these, or type the full name of one you "
                    "already have, like llama3.2:3b."})
        if model and model not in offered:
            fields.append({
                "id": "unknown_model", "kind": "info",
                "label": "This build does not know that brain",
                "current": f"'{model}' is not on Ollama at "
                           f"{machine['url'] or 'the address it was given'} "
                           f"and it is not on this build's list. It is kept "
                           f"as you asked, and your bot cannot answer until "
                           f"a brain by that exact name is there."})
    else:
        fields.append({
            "id": "none", "kind": "info", "label": "Nothing to pick",
            "current": ("This build is offline and no brain is on this "
                        "computer yet. Bring one over with puffpai build "
                        "import, or run setup again with the internet on.")
            if not may_download else
            ("No brain on this build's list fits this computer. "
             f"{menu.get('message') or ''}").strip()})

    if model:
        fields.append({"id": "tier", "kind": "info", "label": "What it can do",
                       "current": _tier_line(model, machine["census"])})
    if not installed and may_download and model:
        fields.append({
            "id": "download_now", "kind": "toggle",
            "label": "Download it now?", "current": False,
            "help": (brain_health.download_estimate(disk_gb)
                     or "This build cannot tell its size in advance") +
                    ". Leave this off and the last screen shows the command "
                    "to run yourself."})

    pulling = bool(not installed and may_download and model)
    card = {
        "skill": "brain", "section": "brain",
        "title": "Its brain",
        "decided": _decided(model, installed, may_download, disk_gb),
        "status": "ready" if (model and (installed or may_download))
                  else "needs_you",
        "consequential": pulling,
        "consequential_reasons": ["heavy_bytes"] if pulling else [],
        "estimate": _estimate(installed, disk_gb, pulling),
        "fields": fields,
    }
    return {"card": card, "menu": menu, "machine": machine,
            "model": model, "by_owner": by_owner, "gates": gates,
            # The address propose moved to, so apply still writes it to the
            # config (its own check sees nothing left to move).
            "settled": settled,
            # What the apply would share (CONVERTING.md). The model is what the
            # Ollama config entry below this card needs to run at all.
            "shared_preview": {"chosen_model": model}}


def _ollama_line(machine: dict) -> str:
    installed = machine["installed"]
    where = machine["url"] or "no address recorded"
    if installed is None:
        return (f"Nothing answered at {where}. Ollama, the free program that "
                f"runs the brain, comes from https://ollama.com; the brain "
                f"below is still the one to get.")
    if not installed:
        return f"Ollama answered at {where}, with no brains downloaded yet."
    return (f"Ollama answered at {where}, with {len(installed)} brain(s) "
            f"downloaded.")


def _model_option(opt: dict, menu: dict) -> dict:
    rec = str((menu.get("recommended") or {}).get("id") or "")
    if opt.get("installed"):
        avail = "already on this computer"
    elif opt.get("disk_gb"):
        avail = f"about {opt.get('disk_gb')} GB to download"
    else:
        avail = "a download of unknown size"
    fit = "fits well" if opt.get("fit") == "comfortable" else "a tight fit"
    mark = " (best fit)" if rec and str(opt.get("id")) == rec else ""
    return {"value": str(opt.get("id")),
            "label": f"{opt.get('label')}: {avail}, {fit}{mark}"}


def _tier_line(model: str, census: dict) -> str:
    from puffpai import model_profile

    line = model_profile.limits_sentence(model)
    if not model_profile.supports(model, "planning"):
        line += (" That is a real limit of this brain, not a bug.")
    return line


def _decided(model: str, installed: bool, may_download: bool,
             disk_gb) -> str:
    if not model:
        return "No brain picked yet, so this build cannot answer anything."
    if installed:
        return f"{model}, already on this computer."
    if not may_download:
        return f"{model} fits this computer and is not on it yet."
    size = f" (about {disk_gb:g} GB)" if disk_gb else ""
    return f"{model}{size}, not on this computer yet."


def _estimate(installed: bool, disk_gb, pulling: bool) -> dict:
    """Honest, or it says unknown. Never a number nobody measured.

    Size comes from the catalog, which is calibrated against real pulls, so a
    download says ``calibrated`` and carries the bytes. Time does not: how long
    a download takes is a property of the owner's connection, and this build
    has not measured it, so no seconds are offered at all.
    """
    if installed or not pulling:
        return {"bytes": 0, "seconds": 0, "basis": "measured"}
    try:
        size = float(disk_gb or 0)
    except (TypeError, ValueError):
        size = 0.0
    if size <= 0:
        return {"bytes": 0, "seconds": None, "basis": "unknown"}
    return {"bytes": int(size * _GB), "seconds": None, "basis": "calibrated"}


# -- apply --------------------------------------------------------------------

def apply(ctx, answers: dict) -> dict:
    """Record the choice, pull only if asked and allowed, report readiness."""
    from puffpai import model_profile

    answers = answers or {}
    proposal = ctx.proposal or {}
    gates = proposal.get("gates") or (
        (getattr(ctx, "shared", None) or {}).get("gates") or {})
    machine = proposal.get("machine") or _machine(ctx)
    menu = proposal.get("menu") or _menu(ctx, machine, gates)
    options = menu.get("options") or []

    model = str(answers.get("model") or proposal.get("model")
                or _default_model(ctx, menu)[0] or "")
    by_owner = bool(answers.get("model")) or bool(proposal.get("by_owner"))
    if answers.get("model"):
        by_owner = str(answers["model"]) != str(
            (menu.get("recommended") or {}).get("id") or "")

    chosen_opt = next((opt for opt in options if str(opt.get("id")) == model),
                      None)
    brain_choice = {
        "model": model,
        "disk_gb": (chosen_opt or {}).get("disk_gb"),
        "recommended": (menu.get("recommended") or {}).get("id"),
        "chosen_by": "owner" if by_owner else "recommendation",
        "routing_tier": model_profile.routing_tier(model),
        "supports_planning": model_profile.supports(model, "planning"),
        "profile_basis": model_profile.basis(model),
    }

    moved = _settle_address(ctx, machine, model) if model else None
    settled = proposal.get("settled") or {}
    if (not moved and model and settled.get("model") == model
            and settled.get("url") == machine.get("url")):
        moved = settled
    if moved:
        machine = dict(machine, url=moved["url"], installed=moved["installed"])
    if model:
        ready, result = _ready(ctx, machine["url"], model, brain_choice, gates,
                               bool(answers.get("download_now")))
    else:
        # Nothing in the catalog fits this machine, so nothing was picked. Say
        # that, with the menu's own sentence, rather than asking Ollama about a
        # model name that is not there.
        from puffpai import brain_health
        reason = str(menu.get("message") or
                     "No brain on this build's list fits this computer.")
        ready = {"ready": False, "status": brain_health.MODEL_MISSING,
                 "pull_command": "", "reason": reason}
        # The same "what" verify uses for an empty model, so the closing
        # screen lists this once (the runner merges gaps on skill + what).
        result = {"gap": _gap("No brain is recorded for this build.", reason,
                              "Your bot cannot answer anything until it runs "
                              "on a computer with more memory.")}
    brain_choice["ready"] = bool(ready.get("ready"))
    brain_choice["brain_status"] = ready.get("status")

    shared = {"chosen_model": model, "brain_choice": brain_choice,
              "brain_ready": ready}
    data = {"choice": brain_choice, "model": model,
            "ready": brain_choice["ready"]}
    if moved:
        # The address that has the brain is the one written to the config
        # (persist_config reads ``ollama_url`` from the notebook).
        shared.update({"ollama_url": moved["url"],
                       "installed_models": moved["installed"]})
        data["ollama_url"] = moved["url"]
        note = moved["note"]
        result["note"] = (f"{result['note']} {note}".strip()
                          if result.get("note") else note)
    result.update({"section": "brain", "data": data, "shared": shared})
    return result


def _settle_address(ctx, machine: dict, model: str) -> dict | None:
    """Move to the Ollama address that actually has *model*, or None to stay.

    The discover step picks an address before the brain is chosen, so it can
    land on an Ollama that answers with other models (a relay on 11434 with one
    small model, while the chosen brain is on 11435). Every chat then fails.
    So once the brain is known, the usual addresses are asked again and the
    one holding it wins. An address the owner typed with --ollama-url is kept
    as stated, and an address the build's posture refuses is never chosen.
    """
    from puffpai import brain_health, cli, setup_ops

    url = machine.get("url") or ""
    installed = machine.get("installed")
    if url and installed is not None and brain_health.is_installed(model, installed):
        return None
    if getattr(getattr(ctx, "args", None), "ollama_url", None):
        return None
    try:
        looked = setup_ops.ollama_survey(url or None)
    except Exception:  # noqa: BLE001 - a survey fault leaves the address as it was
        return None
    found = brain_health.best_address(looked, model=model)
    if not found or brain_health._same_server(found, url):
        return None
    models = next((row.get("models") for row in looked
                   if row.get("url") == found), None)
    if not brain_health.is_installed(model, models):
        return None
    posture = (getattr(ctx, "shared", None) or {}).get("posture")
    if cli._posture_violation("ollama", found, posture) is not None:
        return None
    return {"url": found, "installed": list(models or []),
            "note": (f"{model} is on the Ollama at {found}, not at "
                     f"{url or 'the first address found'}, so this build "
                     f"uses {found}.")}


def _ready(ctx, url: str, model: str, brain_choice: dict, gates: dict,
           download_now: bool) -> tuple[dict, dict]:
    """The Brain-ready block, and whatever gap goes with it.

    The same four outcomes ``cli._setup_brain_ready_step`` has always had, with
    the yes/no it used to ask arriving as a card answer instead.
    """
    from puffpai import brain_health, setup_ops

    state = setup_ops.brain_status(url, model)
    status = str(state.get("status"))
    pull = brain_health.pull_command(model, url)
    estimate = brain_health.download_estimate(brain_choice.get("disk_gb"))

    if status == brain_health.CONNECTED:
        return ({"ready": True, "status": status, "pull_command": "",
                 "reason": ""}, {})

    if status == brain_health.UNREACHABLE:
        reason = (f"Nothing answered at {url}, so this build has no brain "
                  f"to talk to.")
        return ({"ready": False, "status": status, "pull_command": pull,
                 "reason": reason, "download_estimate": estimate,
                 "remedy": brain_health.unreachable_remedy(url)},
                {"gap": _gap("Your bot has no brain to talk to.", reason,
                             "It cannot answer anything until Ollama is "
                             "running at that address.")})

    installed = state.get("installed") or []
    reason = (f"This build is set to use '{model}', which is not on Ollama at "
              f"{url} yet.")
    block = {"ready": False, "status": status, "pull_command": pull,
             "reason": reason, "download_estimate": estimate}

    if not gates.get("may_download_models", True):
        return (block, {"gap": _gap(
            "The brain this build needs is not on this computer.", reason,
            "Nothing was downloaded because this build is offline. Bring it "
            "over with puffpai build import, or download it yourself when "
            "this computer is online.", fix=pull_fix(model, url))})

    if not download_now:
        return (block, {"gap": _gap(
            "The brain this build needs is not downloaded.", reason,
            "Your bot cannot answer until the brain is downloaded.",
            fix=pull_fix(model, url))})

    # The one download this skill starts, through the path that already exists.
    outcome = brain_health.pull_model(model, url)
    if outcome.get("ok"):
        return ({"ready": True, "status": brain_health.CONNECTED,
                 "pull_command": "", "reason": ""},
                {"note": str(outcome.get("message") or "")})
    return (block, {"gap": _gap(
        "The brain download did not finish.",
        str(outcome.get("message") or reason),
        "Your bot cannot answer until the brain is on Ollama.",
        fix=pull_fix(model, url))})


def pull_fix(model: str, url: str = "") -> dict:
    """The fix for a missing brain: the real download command, not "run the
    wizard again" -- an offline re-run cannot download anything."""
    from puffpai import brain_health

    if not model:
        return {"kind": "command", "id": "setup.rerun", "args": {}}
    if url and not brain_health._same_server(url, brain_health.DEFAULT_URL):
        return {"kind": "command", "id": "model.pull_at",
                "args": {"model": model, "url": url}}
    return {"kind": "command", "id": "model.pull", "args": {"model": model}}


def _gap(what: str, why: str, impact: str, fix: dict | None = None) -> dict:
    return {"skill": "brain", "what": what, "why": why, "impact": impact,
            "fix": fix or {"kind": "command", "id": "setup.rerun", "args": {}}}


# -- verify -------------------------------------------------------------------

def verify(ctx) -> dict:
    """Ask the Ollama this build is configured for whether the model is there.

    Not the proposal, and not the pull's own report: the question is whether a
    model is installed on the server the finished bot reads from, which is the
    single fact that decides whether it answers anything.
    """
    from puffpai import brain_health, setup_ops

    section = ctx.own()
    model = str(section.get("model") or
                (section.get("choice") or {}).get("model") or "")
    # The address the brain step settled on (it may have moved to the Ollama
    # that holds the model), then the one the machine step found.
    url = str(section.get("ollama_url") or "")
    try:
        url = url or str((ctx.read("machine") or {}).get("ollama_url") or "")
    except Exception:  # pragma: no cover - machine is declared in the manifest
        url = ""
    url = url or str((getattr(ctx, "shared", None) or {}).get("ollama_url") or "")

    if not model:
        # Usually because nothing in the catalog fits this machine; when that
        # is the reason, the gap says so in the menu's own sentence.
        why = "the brain step did not settle on a model"
        try:
            from puffpai import model_catalog
            census = (ctx.read("machine") or {}).get("census") or {}
            why = str(model_catalog.fit_menu(census).get("message") or why)
        except Exception:  # pragma: no cover - machine is declared in the manifest
            pass
        return {"status": "gap", "evidence": "no model is recorded",
                "gap": _gap("No brain is recorded for this build.", why,
                            "Your bot cannot answer anything.")}

    state = setup_ops.brain_status(url, model)
    status = str(state.get("status"))
    if status == brain_health.CONNECTED:
        return {"status": "pass",
                "evidence": f"{model} is installed on the Ollama at {url}"}
    if status == brain_health.UNREACHABLE:
        return {"status": "gap",
                "evidence": f"nothing answered at {url}",
                "gap": _gap("Your bot has no brain to talk to.",
                            f"nothing answered at {url} when the build checked",
                            "It cannot answer anything until Ollama is running "
                            "there.")}
    return {"status": "gap",
            "evidence": f"{url} answered, and {model} is not installed there",
            "gap": _gap("The brain this build needs is not downloaded.",
                        f"'{model}' is not on Ollama at {url} yet.",
                        "Your bot cannot answer until it is downloaded.",
                        fix=pull_fix(model, url))}
