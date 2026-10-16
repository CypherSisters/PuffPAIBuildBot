"""The first screen: what this computer has, and the thinking model that fits.

The hardware scan and the model size it recommends are made explicit: they
are the first screen in both wizards, before the goal question, rather than a
quiet check between the questions and the plan that leaves two lines behind:

* what was found -- memory, processor, graphics card, free disk space, whether
  the internet answers, whether Ollama answers and which models it has --
  each one measured, stated by the owner, or plainly "not measured";
* the thinking model recommended for this computer, its size, and one plain
  sentence tying the pick to those numbers;
* the other models that fit, what each is good for, and the ones that are too
  big (never offered);
* "Change" to pick another.

**One source of truth.** The recommendation is what the Brain card itself
proposes for this machine (its menu, its gates, its default), so nothing here
can recommend a model the Brain card would not run, and nothing is ever
offered that does not fit (``model_catalog.fit_menu`` is RAM-first). The pick
is kept in the run's notebook as ``first_look`` and the Brain card starts from
it, so the plan's "How it thinks" line is the model chosen here.

Nothing here downloads or writes anything but that one choice. The age is not
known yet, so the wording is always the standard one.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

from typing import Any, Callable

from puffpai.wizard import cards, wording

SCHEMA = "wizard_first_look/v1"

#: The runner's entry for this screen, right after the quiet check.
ENTRY = "runner.first_look"

#: Where the choice is kept in the run's notebook.
KEY = "first_look"

_GB = 1024 ** 3


# -- what was found -----------------------------------------------------------

def _num(value: Any) -> str:
    try:
        return f"{float(value):g}"
    except (TypeError, ValueError):
        return str(value)


def _measured(section: dict) -> str:
    return " (you said this)" if section.get("source") == "stated" else ""


def found_rows(shared: dict) -> list[dict]:
    """``[{"label", "text"}]`` in plain words. Unknown says unknown."""
    census = shared.get("census") or {}
    mem = census.get("memory") or {}
    cpu = census.get("cpu") or {}
    accel = census.get("accelerator") or {}
    disk = census.get("disk") or {}
    rows: list[dict] = []

    ram = mem.get("total_gb")
    rows.append({"label": "Memory",
                 "text": (f"{_num(ram)} GB{_measured(mem)}" if ram
                          else "not measured")})

    if cpu.get("model"):
        cores = cpu.get("logical_cores")
        text = str(cpu["model"]) + (f", {cores} cores" if cores else "")
        rows.append({"label": "Processor", "text": text + _measured(cpu)})
    else:
        rows.append({"label": "Processor", "text": "not measured"})

    kind = accel.get("kind")
    if kind == "nvidia_dgpu":
        vram = accel.get("vram_gb")
        text = str(accel.get("name") or "an NVIDIA graphics card")
        if vram:
            text += f", with {_num(vram)} GB of its own memory"
    elif kind in ("jetson_integrated", "apple_silicon"):
        text = (f"{accel.get('name') or 'built in'}, sharing the main memory")
    elif kind == "none":
        text = "none, so the processor does the thinking"
    else:
        text = ("not detected (BuildBot can only check NVIDIA cards so far); "
                "the processor can do the thinking")
    rows.append({"label": "Graphics card", "text": text + _measured(accel)})

    free, total = disk.get("free_gb"), disk.get("total_gb")
    rows.append({"label": "Free disk space",
                 "text": (f"{_num(free)} GB free" + (f" of {_num(total)} GB"
                                                     if total else "")
                          if free is not None else "not measured")})

    internet = shared.get("internet")
    rows.append({"label": "The internet",
                 "text": ("reachable, so downloads can happen while we build"
                          if internet is True else
                          "not reachable, so nothing is downloaded while we "
                          "build" if internet is False else "not checked")})

    url = str(shared.get("ollama_url") or "")
    installed = shared.get("installed_models")
    if installed is None:
        text = (f"not answering at {url or 'its usual address'} yet. It is "
                f"the free program that runs the thinking model, and your bot "
                f"needs it running to think.")
    elif not installed:
        text = "is running, with no thinking models yet"
    else:
        names = [str(m) for m in installed]
        shown = ", ".join(names[:4]) + (f" and {len(names) - 4} more"
                                        if len(names) > 4 else "")
        text = (f"is running, with {len(names)} model"
                f"{'s' if len(names) != 1 else ''} already: {shown}")
    rows.append({"label": "Ollama", "text": text})
    return rows


# -- the recommendation -------------------------------------------------------

def _catalog_row(model: str) -> dict:
    from puffpai import model_catalog
    for row in model_catalog.load_catalog().get("models") or ():
        if str(row.get("id")) == model:
            return row
    return {}


def good_for(model: str) -> str:
    """What a model is good for, from its profile. Plain, one phrase."""
    from puffpai import model_profile
    tier = model_profile.routing_tier(model)
    if tier == "light":
        return "handles requests with several steps, asked in your own words"
    if tier == "rails":
        return "short, simple answers only"
    if model_profile.supports(model, "planning"):
        return "a good everyday helper, best with one request at a time"
    return "quick answers, one simple request at a time"


def _size_words(opt: dict) -> str:
    if opt.get("installed"):
        return "already on this computer"
    disk = opt.get("disk_gb")
    return (f"{cards.format_bytes(int(float(disk) * _GB))} download" if disk
            else "download size not known")


def why_sentence(model: str, opt: dict | None, census: dict, *,
                 installed: bool, may_download: bool) -> str:
    """One plain sentence tying the pick to the numbers found."""
    ram = (census.get("memory") or {}).get("total_gb")
    row = _catalog_row(model)
    if ram is None:
        return ("I could not measure this computer's memory, so I cannot tell "
                "which thinking model fits; this is the usual one.")
    fit = (opt or {}).get("fit")
    if fit is None and row:
        # Not on the menu shown (an offline build lists only what is here):
        # fitted against the catalog the same way, never guessed.
        from puffpai import model_catalog
        fit = model_catalog._fit(row, float(ram))
    head = ""
    if installed:
        if fit == "tight":
            text = (f"It is already on this computer, so nothing needs "
                    f"downloading; with {_num(ram)} GB of memory it runs, with "
                    f"little room to spare.")
        elif fit == "comfortable":
            text = (f"It is already on this computer, and {_num(ram)} GB of "
                    f"memory is plenty for it, so nothing needs downloading.")
        else:
            text = "It is already on this computer, so nothing needs downloading."
    else:
        if not may_download:
            head = ("The internet is not reachable, so it cannot be "
                    "downloaded now. ")
        if row and fit == "comfortable":
            text = (f"This computer has {_num(ram)} GB of memory and this "
                    f"model runs well from {_num(row.get('rec_ram_gb'))} GB, so "
                    f"it fits with room to spare.")
        elif row:
            text = (f"This computer has {_num(ram)} GB of memory and this "
                    f"model needs at least {_num(row.get('min_ram_gb'))} GB, so "
                    f"it runs, with little room to spare.")
        else:
            text = "This build cannot tell how much memory it needs."
    accel = census.get("accelerator") or {}
    try:
        vram = float(accel.get("vram_gb") or 0)
        size = float(row.get("disk_gb") or 0)
    except (TypeError, ValueError):
        vram = size = 0.0
    if accel.get("kind") == "nvidia_dgpu" and 0 < vram < size:
        text += (f" Your graphics card has {_num(vram)} GB of its own memory, "
                 f"so part of it runs on the processor and answers come "
                 f"slower.")
    # Chat uses tools: say whether this one can.
    tools = (opt or {}).get("tools", (row or {}).get("tools"))
    if tools is not None:
        try:
            size_b = float((opt or {}).get("params_b") or (row or {}).get("params_b") or 0)
        except (TypeError, ValueError):
            size_b = 0.0
        if not tools:
            text += " It cannot use tools, so chat answers without looking things up."
        elif size_b >= 9:
            text += (" It is big enough to use tools well: it looks things up, "
                     "adds what you pick and uses it.")
        else:
            text += " It still uses tools, more simply than a bigger model."
    return head + text


def _relative(opt: dict, pick: dict | None) -> str:
    try:
        mine, theirs = float(opt.get("disk_gb") or 0), float(
            (pick or {}).get("disk_gb") or 0)
    except (TypeError, ValueError):
        return ""
    if not mine or not theirs or mine == theirs:
        return ""
    return "smaller and faster" if mine < theirs else "bigger and slower"


def _brain_proposal(*, args: Any, cfg: Any, shared: dict, sections: dict,
                    non_interactive: bool) -> dict:
    """What the Brain card proposes for this computer, before any choice.

    The Brain card's own ``propose`` with the posture's gates, so this screen
    and the card that later runs cannot disagree. *non_interactive* is whether
    anybody is choosing: an unattended run is not committed to the biggest
    download (``fit_menu(prefer_hosted_tier=...)``), a person choosing is shown
    the most capable model that fits.
    """
    from puffpai.wizard import loader, proposal, runner as wizard_runner

    notebook = {k: v for k, v in (shared or {}).items()
                if k not in (KEY, "summary")}
    found, _notes = proposal.previews(args=args, cfg=cfg, shared=notebook,
                                      sections=sections,
                                      only=("secure:posture",))
    preview = (found.get("secure:posture") or {}).get("shared_preview")
    if isinstance(preview, dict):
        notebook.update({k: v for k, v in preview.items() if isinstance(k, str)})
    packages, _refusals = loader.load_builtin_packages()
    package = packages.get("brain")
    if package is None:
        return {}
    ctx = wizard_runner.RunContext(
        args=proposal._PreviewArgs(args), cfg=cfg,
        non_interactive=non_interactive, shared=notebook,
        sections={k: v for k, v in (sections or {}).items() if k != "brain"},
        package=package, part="", show_once=None, remote=True)
    return loader.load_module(package).propose(ctx) or {}


def _label(opt: dict | None, model: str) -> str:
    label = str((opt or {}).get("label") or "")
    return f"{label} ({model})" if label and label != model else model


def screen(*, args: Any, cfg: Any, shared: dict, sections: dict,
           non_interactive: bool = False) -> dict:
    """The first screen both faces draw. Reads; writes nothing."""
    mode = wording.STANDARD
    shared = shared or {}
    brain = _brain_proposal(args=args, cfg=cfg, shared=shared,
                            sections=sections, non_interactive=non_interactive)
    menu = brain.get("menu") or {}
    gates = brain.get("gates") or {}
    may_download = bool(gates.get("may_download_models", True))
    options = [opt for opt in menu.get("options") or () if opt.get("id")]
    by_id = {str(opt["id"]): opt for opt in options}
    census = shared.get("census") or {}
    installed_list = [str(m) for m in (shared.get("installed_models") or ())]
    recommended = str(brain.get("model") or "")
    chosen = str((shared.get(KEY) or {}).get("model") or "")
    current = chosen if chosen and (chosen in by_id or chosen == recommended) \
        else recommended

    recommend: dict = {"value": "", "line": "", "why": "", "good_for": ""}
    if recommended:
        opt = by_id.get(recommended)
        installed = bool((opt or {}).get("installed")) \
            or recommended in installed_list
        size = _size_words(opt or {"installed": installed,
                                    "disk_gb": _catalog_row(recommended).get(
                                        "disk_gb")})
        recommend = {
            "value": recommended,
            "line": wording.say("first_look_recommend", mode,
                                model=_label(opt or _catalog_row(recommended),
                                             recommended)) + f", {size}.",
            "why": why_sentence(recommended, opt, census, installed=installed,
                                may_download=may_download),
            "good_for": "Good for: " + good_for(recommended) + ".",
        }
    none_line = ""
    if not recommended:
        none_line = str(menu.get("message") or
                        "No thinking model on BuildBot's list fits this "
                        "computer.")

    pick = by_id.get(recommended)
    choices: list[dict] = []
    for opt in options:
        model = str(opt["id"])
        fit = "fits well" if opt.get("fit") == "comfortable" else "a tight fit"
        parts = [_size_words(opt), fit]
        relative = _relative(opt, pick) if model != recommended else ""
        if relative:
            parts.append(relative)
        mark = " (recommended)" if model == recommended else ""
        choices.append({
            "value": model,
            "name": _label(opt, model),
            "label": cards.plain(f"{_label(opt, model)}{mark}: "
                                 f"{', '.join(parts)}", 160),
            "good_for": good_for(model),
            "size_bytes": (0 if opt.get("installed") else
                           int(float(opt["disk_gb"]) * _GB)
                           if opt.get("disk_gb") else None),
            "recommended": model == recommended,
        })
    too_big = []
    ram = (census.get("memory") or {}).get("total_gb")
    if ram is not None:
        for row in menu.get("too_big") or ():
            too_big.append(f"{row.get('label') or row.get('id')} (needs "
                           f"{_num(row.get('min_ram_gb'))} GB of memory)")
    return {
        "schema": SCHEMA,
        "title": wording.say("first_look_title", mode),
        "intro": wording.say("first_look_intro", mode),
        "found": found_rows(shared),
        "recommend": recommend,
        "none_line": none_line,
        "others_heading": wording.say("first_look_others", mode),
        "choices": choices,
        "too_big": ("Too big for this computer, so never offered: "
                    + "; ".join(too_big) + ".") if too_big else "",
        "current": current,
        "chosen": bool(chosen),
        "actions": [{"value": "use", "label": wording.say("first_look_use", mode)},
                    {"value": "change",
                     "label": wording.say("first_look_change", mode)}],
    }


def choose(shared: dict, shown: dict, model: Any) -> dict:
    """Keep the owner's pick in the notebook. Only a model the screen offered.

    Returns the notebook entry. ``None`` or ``""`` takes the recommendation.
    Raises :class:`ValueError` for anything the screen did not list.
    """
    offered = {c["value"] for c in shown.get("choices") or ()}
    recommended = str((shown.get("recommend") or {}).get("value") or "")
    value = str(model or "") or recommended
    if value and value not in offered and value != recommended:
        raise ValueError(f"{value!r} is not one of the thinking models this "
                         f"computer can run.")
    entry = {"model": value or None, "recommended": recommended or None,
             "seen": True}
    shared[KEY] = entry
    return entry


# -- the console --------------------------------------------------------------

def lines(shown: dict) -> list[str]:
    """The screen as console lines. Pure, so it is tested."""
    from puffpai.wizard import render_cli

    wrap = render_cli.wrap
    out: list[str] = ["", ""]
    title = render_cli.ascii_only(shown["title"])
    out += [title, "-" * min(len(title), render_cli.width())]
    out += wrap(shown["intro"])
    out.append("")
    for row in shown["found"]:
        out += wrap(f"{row['label']}: {row['text']}", 2, 4)
    rec = shown.get("recommend") or {}
    out.append("")
    if rec.get("value"):
        out += wrap(rec["line"])
        out += wrap(rec["why"], 2)
        out += wrap(rec["good_for"], 2)
    if shown.get("none_line"):
        out += wrap(shown["none_line"])
    others = [c for c in shown.get("choices") or () if not c["recommended"]]
    if others:
        out.append("")
        out += wrap(shown["others_heading"])
        for choice in others:
            out += wrap(f"- {choice['label']}; {choice['good_for']}", 2, 4)
    if shown.get("too_big"):
        out.append("")
        out += wrap(shown["too_big"])
    return out


def console(shown: dict, *, non_interactive: bool,
            ask: Callable[[str], str | None] | None = None,
            out: Callable[[str], None] = print) -> str | None:
    """Show the screen; return the model picked, or ``None`` when nobody is
    choosing (``--yes``) or there is nothing to pick."""
    from puffpai.wizard import render_cli

    for line in lines(shown):
        out(line)
    choices = list(shown.get("choices") or ())
    rec = str((shown.get("recommend") or {}).get("value") or "")
    if non_interactive or not rec:
        return None
    reader = ask or render_cli._read_line
    out("")
    if len(choices) < 2:
        # Nothing else fits (or nothing else can be had offline): no choice to
        # offer, so no "Change".
        reader(render_cli.ascii_only("  " + wording.say(
            "first_look_go_on", wording.STANDARD)))
        return rec
    for number, action in enumerate(shown["actions"], 1):
        out(render_cli.ascii_only(f"  {number}) {action['label']}"))
    name = next((c["name"] for c in choices if c["value"] == rec), rec)
    raw = (reader(render_cli.ascii_only("  " + wording.say(
        "first_look_prompt", wording.STANDARD, model=name))) or "").strip().lower()
    picked = rec
    if raw in ("2", "change", "c"):
        for line in render_cli._choice_lines(
                [{"value": c["value"], "label": c["label"]} for c in choices]):
            out(line)
        answer = (reader(render_cli.ascii_only("  " + wording.say(
            "first_look_which", wording.STANDARD, model=name))) or "").strip()
        if answer.isdigit() and 1 <= int(answer) <= len(choices):
            picked = choices[int(answer) - 1]["value"]
        else:
            picked = next((c["value"] for c in choices
                           if answer and answer.lower() == c["value"].lower()),
                          rec)
        label = next(c["name"] for c in choices if c["value"] == picked)
        render_cli.say_line(wording.say("first_look_picked", wording.STANDARD,
                                        model=label), out=out)
    return picked


def run_console(ctx: Any) -> dict | None:
    """The runner's ``first_look`` entry. Only a console draws it; the browser
    shows it through :mod:`puffpai.wizard.service` and records the choice
    there."""
    if not getattr(ctx, "console", False):
        return None
    shown = screen(args=ctx.args, cfg=ctx.cfg, shared=ctx.shared,
                   sections=ctx._sections,
                   non_interactive=bool(ctx.non_interactive))
    picked = console(shown, non_interactive=bool(ctx.non_interactive))
    if picked is None:
        return {"note": "first screen shown; nothing picked"}
    entry = choose(dict(ctx.shared), shown, picked)
    return {"shared": {KEY: entry}, "note": f"thinking model picked: {picked}"}


__all__ = ["ENTRY", "KEY", "SCHEMA", "choose", "console", "found_rows",
           "good_for", "lines", "run_console", "screen", "why_sentence"]
