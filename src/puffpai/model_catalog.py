"""The brain menu — fit local models to the hardware, RAM-first.

Step 3 of the wizard. Given a hardware census (:mod:`puffpai.hardware_census`)
and the shipped model ladder (``config/model_catalog.json``), decide which local
models this machine can actually run and mark exactly one recommendation with a
one-line reason.

The discipline is the hardware ladder, productized:

* **RAM-first fitting.** A model whose floor exceeds the machine's RAM is not
  offered at any setting. RAM is the binding constraint; the accelerator only
  colours how we talk about speed.
* **Honest tiers, no extrapolation.** Measured numbers are shown only for real
  silicon of the machine's class, always labelled with the board they were
  measured on. A model or a class we have not benched says "untested, reports
  welcome" — never a guessed number.
* **All local.** There is no cloud-model slot and no bring-an-API-key step. Every
  option runs on the box.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

from puffpai import brain_health, config_health

# Baked-in fallback so the wizard still fits a brain if the shipped catalog file
# is missing or unreadable. Deliberately tiny; the real ladder lives in
# config/model_catalog.json and is the thing maintainers edit.
_FALLBACK_CATALOG: dict[str, Any] = {
    "schema": "model_catalog/v1",
    "silicon": {},
    "models": [
        {
            "id": "qwen2.5:7b-instruct",
            "label": "Qwen2.5 7B",
            "family": "qwen2.5",
            "params": "7B (dense)",
            "min_ram_gb": 8,
            "rec_ram_gb": 16,
            "disk_gb": 5,
            "strength": "A solid all-rounder.",
            "benchmarks": {},
            "params_b": 7.6,
            "tools": True,
        },
        {
            "id": "qwen2.5:3b-instruct",
            "label": "Qwen2.5 3B",
            "family": "qwen2.5",
            "params": "3B (dense)",
            "min_ram_gb": 5,
            "rec_ram_gb": 8,
            "disk_gb": 2,
            "strength": "Runs on modest hardware.",
            "benchmarks": {},
            "params_b": 3.1,
            "tools": True,
        },
    ],
}


def _catalog_path() -> str:
    """Absolute path of the shipped model catalog."""
    from puffpai.config import get_config
    return os.path.join(get_config().REPO_ROOT, "config", "model_catalog.json")


def validate_catalog(data: Any) -> Optional[str]:
    """Structural check of a parsed model ladder; None means healthy.

    The ladder is RAM-first, so the RAM fields are the ones that must be there
    and must be numbers: a model with a missing or non-numeric floor cannot be
    fitted, and a ladder we cannot fit is not a ladder.
    """
    if not isinstance(data, dict):
        return "The model catalog is not a JSON object."
    models = data.get("models")
    if not isinstance(models, list) or not models:
        return "The model catalog lists no models."
    for index, model in enumerate(models):
        if not isinstance(model, dict) or not model.get("id"):
            return f"Model {index} has no id."
        for field in ("min_ram_gb", "rec_ram_gb"):
            value = model.get(field)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                return f"Model '{model.get('id')}' has no numeric {field}."
    return None


def load_catalog(path: Optional[str] = None) -> dict[str, Any]:
    """Load the model ladder, degrading to the baked-in fallback.

    An absent catalog falls back silently; a present-but-corrupt one falls back
    loudly, recording the degradation in :mod:`puffpai.config_health` so the
    owner is told the brain menu they are choosing from is a stub.
    """
    target = path or _catalog_path()
    try:
        with open(target, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, OSError) as exc:
        config_health.note_load_failure("model_catalog", target, exc, reduced=True)
        return _FALLBACK_CATALOG
    problem = validate_catalog(data)
    if problem is None:
        return data  # type: ignore[no-any-return]
    config_health.record(
        "model_catalog", target, config_health.INVALID, problem, reduced=True,
    )
    return _FALLBACK_CATALOG


# ---------------------------------------------------------------------------
# fitting
# ---------------------------------------------------------------------------

def _fit(model: dict[str, Any], total_ram_gb: float) -> Optional[str]:
    """Return 'comfortable', 'tight', or None (does not fit) for a model."""
    try:
        min_ram = float(model.get("min_ram_gb", 0) or 0)
        rec_ram = float(model.get("rec_ram_gb", min_ram) or min_ram)
    except (TypeError, ValueError):
        return None
    if total_ram_gb < min_ram:
        return None
    return "comfortable" if total_ram_gb >= rec_ram else "tight"


def _benchmark_for(
    model: dict[str, Any], hardware_class: str, silicon: dict[str, Any],
    census: Optional[dict[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    """Measured numbers for this model on silicon of the machine's class, or None.

    Honest-tiers gate, and there are two of them. A benchmark surfaces only when

    1. it was measured on a board whose declared CLASS matches the machine's, and
    2. the machine really IS that board.

    The second gate is the one the review found missing here. Every Jetson Orin
    and every desktop with a discrete GPU is class ``workstation``, so class alone
    attached the authors' Jetson measurements to a stranger's Windows laptop -
    which reads as "this tool is hard-coded to the authors' hardware", the exact
    opposite of what a census is for. The CLI's label already applies the identity
    check (:func:`puffpai.model_profile.basis_label`); applying it here means the
    dashboard, which reads ``fit_menu``, gets it too.

    ``census`` is optional so a caller with only a class still gets the old,
    weaker behaviour rather than an exception; :func:`fit_menu` always passes it.
    """
    from puffpai import model_profile

    benchmarks = model.get("benchmarks") or {}
    if not isinstance(benchmarks, dict):
        return None
    for silicon_id, numbers in benchmarks.items():
        board = silicon.get(silicon_id)
        if not (board and board.get("hardware_class") == hardware_class
                and isinstance(numbers, dict)):
            continue
        if census is not None and not model_profile._census_is_this_board(
                census, str(silicon_id), board):
            continue
        out = {"silicon_label": board.get("label", silicon_id)}
        out.update({k: v for k, v in numbers.items() if v is not None})
        return out
    return None


def _vram_clause(model: dict[str, Any], census: dict[str, Any],
                 installed_label: str = "") -> str:
    """The sentence a discrete GPU smaller than the model earns, or "".

    RAM fitting says a 19 GB model "fits your 64 GB". True, and useless on a box
    whose 8 GB card can hold a third of it: the rest runs on the CPU and the owner
    experiences a recommendation as a machine that has gone quiet. Say the number.
    """
    accel = census.get("accelerator") if isinstance(census, dict) else None
    accel = accel if isinstance(accel, dict) else {}
    if accel.get("kind") != "nvidia_dgpu":
        # Unified memory (Jetson, Apple Silicon) has no separate VRAM to fall
        # short, and a machine with no GPU at all is already all-CPU and knows it.
        return ""
    try:
        vram = float(accel.get("vram_gb") or 0)
        size = float(model.get("disk_gb") or 0)
    except (TypeError, ValueError):
        return ""
    if vram <= 0 or size <= 0 or vram >= size:
        return ""
    faster = (f" - the {installed_label} you already have will feel faster"
              if installed_label else "")
    return (f" It fits your RAM; with {vram:g} GB of VRAM most of a {size:g} GB "
            f"model runs on the CPU, so expect it to be slow{faster}.")


def _recommendation_reason(fit: str, ram: Optional[float],
                           installed: bool = False,
                           bigger_available: bool = False,
                           tier_capped: str = "") -> str:
    """One honest line for why this is the pick. Not a spec sheet."""
    ram_str = f"your {ram} GB" if ram is not None else "this machine"
    if installed:
        # An already-downloaded brain wins over a bigger one that is not here.
        # The wizard that recommends a 19 GB download to somebody who already
        # has a fitting model on the box is recommending a wait, then a 404.
        tail = ""
        if bigger_available:
            tail = (" A more capable one also fits this machine, if you would "
                    "rather download it.")
        if fit == "comfortable":
            return (
                f"Already on this machine and fits {ram_str} with room for the "
                f"knowledge index, so your bot can answer as soon as setup "
                f"finishes - nothing to download.{tail}"
            )
        return (
            f"Already on this machine and runs on {ram_str}, though with little "
            f"headroom, so your bot can answer as soon as setup finishes - "
            f"nothing to download.{tail}"
        )
    if tier_capped:
        # Nothing is installed and nobody is watching the download. The most
        # capable row is the right pick for a person who chose it and the wrong
        # one for a machine that chose it for them - so an unattended run stops
        # at the least demanding routing tier this hardware still hosts well,
        # and takes the best brain in THAT tier. Capability floor first, size
        # second: a 1 GB rails brain on a 64 GB box is not a kindness.
        return (
            f"Nothing is installed here yet, so this is the best brain in the "
            f"{tier_capped} tier, which {ram_str} hosts comfortably - capable "
            f"enough to be worth running, without a download nobody chose. "
            f"Pass --model, or re-run without --yes, for the most capable "
            f"brain this machine runs."
        )
    if fit == "comfortable":
        return (
            f"Fits {ram_str} with room for the knowledge index, and it is the "
            f"most capable brain this machine runs well."
        )
    return (
        f"Runs on {ram_str}, but with little headroom. It is the most capable "
        f"brain that fits; a lighter one will feel snappier."
    )


# Routing tiers, most capable first. An unattended run never reaches for the
# top one: `light` is where the 19 GB mixture-of-experts models live, and
# committing an absent owner to that download is the defect this list exists to
# stop. `guided` is the floor worth having - it follows a plain request in the
# owner's own words - so `rails` is chosen only when the hardware genuinely
# cannot host guided.
_TIER_ORDER: tuple[str, ...] = ("light", "guided", "rails")
_UNATTENDED_TIER_CAP = "guided"


def _tier_of(model_id: str) -> str:
    """The routing tier a model declares in config/model_profiles.json.

    Imported lazily: model_profile reads this module's catalog for silicon
    labels, and a module-level import in both directions is a cycle.
    """
    from puffpai import model_profile
    return model_profile.routing_tier(str(model_id or ""))


def _unattended_tier(options: list[dict[str, Any]]) -> Optional[str]:
    """The tier an unattended run should pick from, given what fits.

    ``guided`` whenever any fitting model is guided; otherwise the most capable
    tier actually available, which on a small board is ``rails``. ``light``
    only if light is the one thing this machine can run at all.
    """
    present = {str(o.get("routing_tier") or "") for o in options}
    if _UNATTENDED_TIER_CAP in present:
        return _UNATTENDED_TIER_CAP
    below = _TIER_ORDER[_TIER_ORDER.index(_UNATTENDED_TIER_CAP) + 1:]
    for tier in list(below) + list(_TIER_ORDER):
        if tier in present:
            return tier
    return None


# ---------------------------------------------------------------------------
# the tool model
# ---------------------------------------------------------------------------
#
# Chat runs a tool loop now (puffpai.brain_loop), so the brain must call tools.
# Her rule: recommend a model of 9B or larger where this computer can run it,
# and otherwise the smallest model that still calls tools. A catalog row says
# ``"tools": true`` when it calls tools well enough for the loop (the 1B and
# 1.5B rows do not: they were the parked "rails" tier).

#: Billions of parameters from which a model uses tools well (Hermes Agent:
#: "models 9B+ handle this well"; Qwen3.5 9B is the one this build tests with).
TOOL_MODEL_MIN_B = 9.0

NO_TOOLS_NOTE = ("None of the models that fit this computer can use tools, so "
                 "chat will answer without looking things up or using what you add.")


def _params_b(model: dict[str, Any]) -> Optional[float]:
    """The model's size in billions of parameters, or None when unknown."""
    value = model.get("params_b")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _tool_pick(options: list[dict[str, Any]]) -> Optional[tuple[dict[str, Any], str]]:
    """``(option, kind)`` by her rule, or None when no fitting model calls tools.

    * ``big_installed``: a 9B-or-larger tool model already here (the strongest);
    * ``big``: else the smallest 9B-or-larger tool model that fits;
    * ``small_installed``: no 9B fits, but a smaller tool model is here (no
      download), the strongest of those;
    * ``small``: else the smallest tool model that fits.
    """
    tools = [o for o in options if o.get("tools")]
    if not tools:
        return None

    def size(o: dict[str, Any]) -> float:
        b = o.get("params_b")
        return float(b) if isinstance(b, (int, float)) else 0.0

    big = [o for o in tools if size(o) >= TOOL_MODEL_MIN_B]
    if big:
        here = [o for o in big if o.get("installed")]
        if here:
            return max(here, key=size), "big_installed"
        return min(big, key=size), "big"
    here = [o for o in tools if o.get("installed")]
    if here:
        return max(here, key=size), "small_installed"
    return min(tools, key=size), "small"


def _tool_reason(kind: str, pick: dict[str, Any], ram: Optional[float]) -> str:
    """One plain line for why this brain, under the tool rule."""
    ram_str = f"your {ram} GB" if ram is not None else "this computer"
    room = ("" if pick.get("fit") == "comfortable"
            else " It is a tight fit, so it may be slow.")
    disk = pick.get("disk_gb")
    download = f" About {disk} GB to download." if disk else ""
    if kind == "big_installed":
        return ("Already on this computer, and big enough (9B or more) to use "
                "tools well: it can look things up, add what you pick and use "
                "it. Nothing to download." + room)
    if kind == "big":
        return (f"Fits {ram_str} and is big enough (9B or more) to use tools "
                f"well: it can look things up, add what you pick and use it."
                + download + room)
    if kind == "small_installed":
        return ("Already on this computer and it still uses tools. A 9B model "
                "would use them better, but none fits here." + room)
    return ("A 9B model does not fit this computer, so this is the smallest "
            "one that still uses tools. Expect simpler answers." + download + room)


def fit_menu(
    census: dict[str, Any],
    installed_models: Optional[list[str]] = None,
    catalog: Optional[dict[str, Any]] = None,
    *,
    prefer_hosted_tier: bool = False,
) -> dict[str, Any]:
    """Build the brain menu for a machine, RAM-first.

    Returns a dict with: the fitting ``options`` (strongest first, each carrying
    its fit, install state, and either a measured benchmark or a
    reports-welcome note), the single ``recommended`` model id + reason, a
    ``too_big`` list explaining what the RAM ceiling excludes, and a ``message``
    when nothing fits. Never raises; an unknown-RAM census returns a message that
    tells the wizard to ask.

    ``prefer_hosted_tier`` flips the no-installed-model recommendation from the
    most capable row that fits to the best row in the least demanding ROUTING
    tier this hardware still hosts comfortably - ``guided`` wherever a guided
    model fits, ``rails`` only where none does. It is what the wizard passes on
    a ``--yes`` run: committing an absent owner to a 19 GB ``light`` download is
    a defensible choice for a person who made it and a bad one for a machine
    that made it while nobody was watching, and dropping a 64 GB box to a 1 GB
    ``rails`` brain that cannot follow a plain request is the opposite mistake.
    An installed model still wins over both, and ``--model`` skips it entirely.
    """
    cat = catalog or load_catalog()
    models: list[dict[str, Any]] = list(cat.get("models") or [])
    silicon: dict[str, Any] = dict(cat.get("silicon") or {})
    installed = set(installed_models or [])

    memory = census.get("memory") or {}
    ram = memory.get("total_gb")
    hardware_class = str(census.get("hardware_class", "unknown"))
    budget = census.get("model_budget_gb")

    result: dict[str, Any] = {
        "hardware_class": hardware_class,
        "total_ram_gb": ram,
        "model_budget_gb": budget,
        "options": [],
        "too_big": [],
        "recommended": None,
        "message": None,
    }

    if ram is None:
        result["message"] = (
            "We could not read this machine's memory, so we cannot fit a brain "
            "yet. Tell the wizard how much RAM it has and we will fit one."
        )
        return result

    ram_f = float(ram)
    options: list[dict[str, Any]] = []
    too_big: list[dict[str, Any]] = []

    for model in models:
        fit = _fit(model, ram_f)
        if fit is None:
            too_big.append({
                "id": model.get("id"),
                "label": model.get("label"),
                "min_ram_gb": model.get("min_ram_gb"),
            })
            continue
        benchmark = _benchmark_for(model, hardware_class, silicon, census)
        options.append({
            "id": model.get("id"),
            "label": model.get("label"),
            "params": model.get("params"),
            "disk_gb": model.get("disk_gb"),
            "strength": model.get("strength"),
            "fit": fit,
            "installed": brain_health.is_installed(str(model.get("id") or ""), list(installed)),
            "benchmark": benchmark,
            "benchmark_note": None if benchmark else "Untested on hardware like yours, reports welcome.",
            "routing_tier": _tier_of(str(model.get("id") or "")),
            "tools": bool(model.get("tools")),
            "params_b": _params_b(model),
            # Per row as well as on the recommendation: the menu is where somebody
            # overrides the pick, and that is exactly where the number matters.
            "vram_note": _vram_clause(model, census).strip() or None,
        })

    result["options"] = options
    result["too_big"] = too_big

    if not options:
        result["message"] = (
            "This machine is below the floor of our smallest local model. Add "
            "memory, or point the wizard at a board with more RAM."
        )
        return result

    # Recommendation, in order: the strongest INSTALLED model that fits, then
    # the strongest that fits at all. Models are ordered strongest-first in the
    # catalog, so the first match in each pass wins.
    #
    # Install state is part of the fit, not a cosmetic label on the menu. A
    # machine that already holds a fitting brain is a machine whose bot can
    # answer the moment setup ends; recommending a 19 GB download over it (and
    # then never fetching it) is what turned a finished wizard into a 404.
    # Explicitly picking a bigger model is still one keystroke away.
    def _best(pool: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
        if not pool:
            return None
        return next((o for o in pool if o["fit"] == "comfortable"), pool[0])

    tool_pick = _tool_pick(options)
    if tool_pick is not None:
        pick, kind = tool_pick

        def _disk(o: dict[str, Any]) -> float:
            try:
                return float(o.get("disk_gb") or 0)
            except (TypeError, ValueError):
                return 0.0
        faster = next((str(o["label"]) for o in sorted(options, key=_disk, reverse=True)
                       if o.get("installed") and o["id"] != pick["id"]
                       and _disk(o) < _disk(pick)), "")
        result["recommended"] = {
            "id": pick["id"],
            "label": pick["label"],
            "reason": _tool_reason(kind, pick, ram)
            + _vram_clause(next((m for m in models if str(m.get("id")) == str(pick["id"])),
                                {}), census, faster),
            "fit": pick["fit"],
            "installed": pick["installed"],
            "disk_gb": pick["disk_gb"],
            "routing_tier": pick.get("routing_tier"),
            "tools": True,
        }
        return result

    # PARKED: the rule below picked a brain for a chat with
    # no tools. It now only runs when no model that fits can use tools.
    installed_pick = _best([o for o in options if o["installed"]])
    fallback = _best(options) or options[0]
    tier_capped = ""
    if installed_pick is None and prefer_hosted_tier:
        tier = _unattended_tier(options)
        in_tier = _best([o for o in options if o.get("routing_tier") == tier])
        if in_tier is not None:
            fallback = in_tier
            tier_capped = str(tier)
    pick = installed_pick or fallback
    bigger_available = bool(
        installed_pick is not None
        and options and options[0]["id"] != pick["id"]
        and not options[0]["installed"]
    )
    # The GPU clause goes on whichever model the sentence is pointing the owner
    # AT. Usually that is the pick; when the pick is an already-installed model
    # and the reason also says "a more capable one fits", it is that bigger one -
    # which is the case the review actually caught, a 19 GB download offered to an
    # 8 GB card. It names the installed model as the faster option, because
    # "expect it to be slow" with no alternative is a warning with no move in it.
    if bigger_available:
        subject_id = str(options[0]["id"])
        faster_label = str(pick["label"])
    else:
        subject_id = str(pick["id"])
        other = _best([o for o in options
                       if o["installed"] and o["id"] != pick["id"]])
        faster_label = str(other["label"]) if other else ""
    subject = next((m for m in models if str(m.get("id")) == subject_id), {})
    result["recommended"] = {
        "id": pick["id"],
        "label": pick["label"],
        "reason": _recommendation_reason(pick["fit"], ram,
                                         installed=bool(pick["installed"]),
                                         bigger_available=bigger_available,
                                         tier_capped=tier_capped)
        + _vram_clause(subject, census, faster_label)
        + " " + NO_TOOLS_NOTE,
        "fit": pick["fit"],
        "installed": pick["installed"],
        "disk_gb": pick["disk_gb"],
        "routing_tier": pick.get("routing_tier"),
    }
    return result


def fitting_fallback(menu: dict[str, Any], configured: str) -> str:
    """The model to fall back on when the recommendation cannot be used.

    Both wizard faces used to fall back on the configured default
    (``qwen2.5:7b-instruct``, 8 GB) without asking whether this machine can run
    it, so a 1.5 GB board below the catalog's floor was told to use a 7B brain.
    The rule now: the configured model only if it is not a catalog row this
    machine's RAM excludes; otherwise the menu's recommendation, which always
    fits; and when the RAM is known and nothing in the catalog fits, no model at
    all (an empty string), so the wizard says plainly that nothing fits instead
    of proposing something that will not run. An unknown-RAM machine keeps the
    configured model, because there is nothing to fit it against.
    """
    rec = str((menu.get("recommended") or {}).get("id") or "")
    if menu.get("total_ram_gb") is not None and not rec:
        return ""
    configured = str(configured or "")
    too_big = {str(m.get("id")) for m in menu.get("too_big") or ()}
    if configured and configured not in too_big:
        return configured
    return rec


# ---------------------------------------------------------------------------
# display
# ---------------------------------------------------------------------------

def menu_summary_lines(menu: dict[str, Any]) -> list[str]:
    """Plain-language lines describing the brain menu, for the CLI/API to show."""
    lines: list[str] = []
    if menu.get("message"):
        lines.append(str(menu["message"]))
        return lines

    rec = menu.get("recommended") or {}
    if rec:
        disk = rec.get("disk_gb")
        disk_str = f" (about {disk} GB to download)" if disk and not rec.get("installed") else ""
        installed_str = " [already installed]" if rec.get("installed") else ""
        lines.append(f"Recommended: {rec.get('label')}{disk_str}{installed_str}")
        # The VRAM clause is already part of the recommendation's reason, so it is
        # not repeated here; the rows below carry their own.
        lines.append(f"  {rec.get('reason')}")

    others = [o for o in menu.get("options", []) if o.get("id") != rec.get("id")]
    if others:
        lines.append("Also fits this machine:")
        for opt in others:
            fit_str = "comfortable" if opt.get("fit") == "comfortable" else "tight"
            inst = " [installed]" if opt.get("installed") else ""
            lines.append(f"  - {opt.get('label')} ({opt.get('params')}, {fit_str}){inst}")
            if opt.get("vram_note"):
                lines.append(f"      {opt['vram_note']}")

    return lines


__all__ = [
    "load_catalog",
    "fit_menu",
    "fitting_fallback",
    "menu_summary_lines",
]
