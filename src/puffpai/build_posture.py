"""Build posture - two axes, not one switch.

Step 0 of the wizard, asked before the hardware census, because posture gates
what every later step is even allowed to offer. "Offline or online" is really
two independent questions, and conflating them is what makes air-gapped builds
impossible:

* ``build_network`` - can the WIZARD reach the internet *while building*?
  (fetching models, packs, sources.) ``online`` or ``offline``.
* ``run_egress`` - what can the FINISHED BOT reach when it runs?
  ``sealed``, ``island``, or ``enumerated``.

The two combine freely and all six combinations are legal. The interesting one
is ``online`` + ``sealed``: the sneakernet build, where a connected box
assembles everything, writes a provisioning bundle to removable media, and a
machine that never touches a network imports it. That is a first-class path
(see :mod:`puffpai.build_bundle`), not an edge case.

Three rules hold this module honest:

* **Defaults are recorded as defaults.** Someone who mashes Enter gets
  ``online`` + ``island`` - useful out of the box, only what the plan named goes out -
  and the manifest says the value was defaulted, not chosen. Same detected-vs-
  stated discipline the hardware census uses.
* **Every enumerated switch is off.** The enumerated tier is a build-time list
  of named doors - weather, music, web search - each closed until the owner
  opens it by name. It decides which doors exist, not whether using one asks
  first; that second question is the Bridge posture, and the two are easy to
  confuse, so the menu says so out loud.
* **Posture is a policy choice, not a discovery feature.** Nothing here scans,
  probes, or classifies anything on the network.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

from typing import Any, Optional

SCHEMA = "build_posture/v1"

DEFAULT_BUILD_NETWORK = "online"
DEFAULT_RUN_EGRESS = "island"

# --- axis 1: can the wizard reach the internet while building? -------------

_BUILD_NETWORKS: dict[str, str] = {
    "online": (
        "This machine can reach the internet while you build, so the wizard may "
        "download models, knowledge packs, and vetted sources."
    ),
    "offline": (
        "This machine has no internet while you build, so the wizard offers only "
        "what is already on the box or carried in on a provisioning bundle."
    ),
}

_BUILD_NETWORK_LABEL: dict[str, str] = {
    "online": "Online while building",
    "offline": "Offline while building",
}

# --- axis 2: what can the finished bot reach when it runs? -----------------

_RUN_EGRESS: dict[str, str] = {
    "sealed": (
        "Every outside service is switched off, and none can be switched on. "
        "Your bot says plainly what it cannot do instead of failing quietly."
    ),
    "island": (
        "Your home network is allowed. Your bot's own features do not use "
        "the internet. BuildBot's tools for finding and adding things do, "
        "while your bot is made; afterwards only the ones its plan named stay "
        "on. Each one has an off switch on \"What leaves your computer\"."
    ),
    "enumerated": (
        "You pick, from a fixed list, which outside services your bot can use "
        "at all - weather, music, web search, media details. Every one starts "
        "off; the ones you leave off are not in the build. Each one you switch "
        "on says in a line what leaves the box."
    ),
}

_RUN_EGRESS_LABEL: dict[str, str] = {
    "sealed": "Nothing outside this computer",
    # Not "home network only": BuildBot's own finding tools reach the
    # internet at this level (the description says so).
    "island": "Your home network, plus BuildBot's own tools",
    "enumerated": "Only the outside services you say yes to",
}

# Printed under the enumerated option while the owner is choosing. The two
# questions read the same to a stranger and are not the same: this one decides
# which outside services exist at all, once, now. How the bot checks actions
# that reach further is the Bridge posture, chosen later.
ENUMERATED_VS_MOAT_NOTE = (
    "This list decides which outside services your bot has at all. Each one "
    "starts off, and only the ones you say yes to are switched on."
)

# The enumerated table. Every switch is off by default; there is no on-by-default
# door anywhere in a build. Descriptions say what leaves the box, in one line.
_EGRESS_SWITCHES: tuple[tuple[str, str, str], ...] = (
    ("weather", "Weather", "Your rough location goes to a weather service."),
    ("web_search", "Web search", "Your search words go to the search service you choose."),
    ("web_fetch", "Fetch a page", "The address of a page you ask for goes to that site."),
    ("radio", "Internet radio", "A station request goes to the streaming service."),
    ("music", "Music streaming", "Playback requests go to the music service you sign in to."),
    ("model_pull", "Model downloads", "A model name goes to the model registry."),
    ("pack_pull", "Knowledge pack downloads", "A pack name goes to the pack library."),
)


# ---------------------------------------------------------------------------
# menus
# ---------------------------------------------------------------------------

def build_network_options() -> list[dict[str, Any]]:
    """The build_network axis, as an owner-facing menu."""
    return [
        {
            "id": key,
            "label": _BUILD_NETWORK_LABEL[key],
            "description": text,
            "default": key == DEFAULT_BUILD_NETWORK,
        }
        for key, text in _BUILD_NETWORKS.items()
    ]


def run_egress_options() -> list[dict[str, Any]]:
    """The run_egress axis, as an owner-facing menu.

    ``note`` carries the enumerated-vs-moat distinction to whatever draws the
    menu, so the clarification lives next to the option that needs it rather
    than in a glossary nobody opens.
    """
    return [
        {
            "id": key,
            "label": _RUN_EGRESS_LABEL[key],
            "description": text,
            "default": key == DEFAULT_RUN_EGRESS,
            "note": ENUMERATED_VS_MOAT_NOTE if key == "enumerated" else "",
        }
        for key, text in _RUN_EGRESS.items()
    ]


def egress_switches() -> list[dict[str, Any]]:
    """The enumerated egress table. Every switch is off; that is the contract."""
    return [
        {"id": sid, "label": label, "description": text, "enabled": False}
        for sid, label, text in _EGRESS_SWITCHES
    ]


def switch_ids() -> frozenset[str]:
    """The ids the enumerated table knows about. Anything else is not a door."""
    return frozenset(sid for sid, _label, _text in _EGRESS_SWITCHES)


# ---------------------------------------------------------------------------
# the combination table
# ---------------------------------------------------------------------------

_COMBINATIONS: dict[tuple[str, str], str] = {
    ("online", "sealed"): (
        "Built here with the internet, then copied to a USB stick and carried "
        "to a computer that never touches a network."
    ),
    ("online", "island"): (
        "The home-lab build. Download what you need now; the finished bot talks "
        "to your own devices and servers and nothing beyond them."
    ),
    ("online", "enumerated"): (
        "The normal path. Download what you need now, and the finished bot uses "
        "only the outside services you said yes to."
    ),
    ("offline", "sealed"): (
        "Built on a computer with no network, from what came with BuildBot, "
        "and the finished bot reaches nothing at all."
    ),
    ("offline", "island"): (
        "No internet during the build, but your home network is fair game "
        "afterwards - Home Assistant, a media server, local devices."
    ),
    ("offline", "enumerated"): (
        "Built from what is already on this computer, then it uses only the "
        "outside services you said yes to. Bring the brain and the books over "
        "some other way, such as a USB stick."
    ),
}


def describe_combination(build_network: str, run_egress: str) -> str:
    """Plain-words consequence of one (build_network, run_egress) pair."""
    key = (_norm_build_network(build_network), _norm_run_egress(run_egress))
    return _COMBINATIONS[key]


# ---------------------------------------------------------------------------
# assembling a posture
# ---------------------------------------------------------------------------

def _norm_build_network(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if text in _BUILD_NETWORKS else DEFAULT_BUILD_NETWORK


def _norm_run_egress(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if text in _RUN_EGRESS else DEFAULT_RUN_EGRESS


def assess_posture(
    build_network: Optional[str] = None,
    run_egress: Optional[str] = None,
    enabled_switches: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Build a posture record from what the owner stated, or the defaults.

    Never raises. ``None`` means "the owner skipped this", and the posture says
    so: ``source`` is ``"defaulted"`` rather than ``"stated"``. A value we do not
    recognise is treated the same way as a skip, with a note, because a
    misspelled posture must never become a permissive one.

    ``enabled_switches`` only means anything under ``run_egress: enumerated``;
    under any other tier the table is empty and every door stays shut.
    """
    notes: list[str] = []

    if build_network is None:
        net, net_source = DEFAULT_BUILD_NETWORK, "defaulted"
    else:
        net = _norm_build_network(build_network)
        net_source = "stated"
        if net != str(build_network).strip().lower():
            net_source = "defaulted"
            notes.append(
                f"Did not recognise build network '{build_network}'; using the "
                f"default '{DEFAULT_BUILD_NETWORK}'."
            )

    if run_egress is None:
        egress, egress_source = DEFAULT_RUN_EGRESS, "defaulted"
    else:
        egress = _norm_run_egress(run_egress)
        egress_source = "stated"
        if egress != str(run_egress).strip().lower():
            egress_source = "defaulted"
            notes.append(
                f"Did not recognise run egress '{run_egress}'; using the default "
                f"'{DEFAULT_RUN_EGRESS}'."
            )

    known = switch_ids()
    table: dict[str, bool] = {sid: False for sid in known}
    if egress == "enumerated":
        for sid in enabled_switches or []:
            key = str(sid).strip().lower()
            if key in known:
                table[key] = True
            else:
                notes.append(f"Ignoring unknown egress door '{sid}'.")
    elif enabled_switches:
        notes.append(
            "Individual egress doors only apply to the enumerated tier; none are "
            "open at this posture."
        )

    if net_source == "defaulted" and egress_source == "defaulted":
        notes.append(
            "You skipped this step, so the build uses the safe default: the "
            "wizard may download, and the finished bot stays on your home network."
        )

    return {
        "schema": SCHEMA,
        "build_network": net,
        "build_network_source": net_source,
        "run_egress": egress,
        "run_egress_source": egress_source,
        "egress_switches": table,
        "enabled_switches": sorted(sid for sid, on in table.items() if on),
        "combination": describe_combination(net, egress),
        "notes": notes,
    }


# ---------------------------------------------------------------------------
# gates - what later steps may offer
# ---------------------------------------------------------------------------

def gates(posture: dict[str, Any]) -> dict[str, bool]:
    """What the posture permits downstream steps to offer.

    A gate is a permission to *offer*, not a decision to do. The wizard still
    asks; the gate only decides whether the question is allowed to appear. This
    is what stops someone who wants an air-gapped bot from being shown an online
    catalog and then told "just kidding".
    """
    net = _norm_build_network((posture or {}).get("build_network"))
    egress = _norm_run_egress((posture or {}).get("run_egress"))
    online = net == "online"
    return {
        "may_offer_online_sourcing": online,
        "may_download_models": online,
        "may_download_packs": online,
        "may_offer_sneakernet_bundle": online,
        "needs_sneakernet_bundle": (not online) or egress == "sealed",
        "may_offer_lan_integrations": egress in ("island", "enumerated"),
        "may_offer_wan_at_runtime": egress == "enumerated",
        "may_offer_egress_table": egress == "enumerated",
        "must_state_limits_aloud": egress == "sealed",
    }


def posture_manifest_block(posture: dict[str, Any]) -> dict[str, Any]:
    """The posture, shaped for the manifest's ``posture`` block.

    Records the values, whether each was stated or defaulted, and the whole
    enumerated table including the doors that are shut - a build should be able
    to say what it did *not* open, not only what it did.
    """
    settled = posture if posture.get("schema") == SCHEMA else assess_posture(
        posture.get("build_network"),
        posture.get("run_egress"),
        posture.get("enabled_switches"),
    )
    return {
        "build_network": settled["build_network"],
        "build_network_source": settled["build_network_source"],
        "run_egress": settled["run_egress"],
        "run_egress_source": settled["run_egress_source"],
        "egress_switches": dict(settled["egress_switches"]),
        "enabled_switches": list(settled["enabled_switches"]),
        "combination": settled["combination"],
    }


def posture_summary_lines(posture: dict[str, Any]) -> list[str]:
    """Plain-language lines describing a posture, for the CLI/API."""
    settled = posture or {}
    net = _norm_build_network(settled.get("build_network"))
    egress = _norm_run_egress(settled.get("run_egress"))

    def suffix(source: Any) -> str:
        return " (default, not chosen)" if source == "defaulted" else ""

    lines = [
        f"While building: {_BUILD_NETWORK_LABEL[net]}"
        f"{suffix(settled.get('build_network_source'))}",
        f"When it runs: {_RUN_EGRESS_LABEL[egress]}"
        f"{suffix(settled.get('run_egress_source'))}",
        describe_combination(net, egress),
    ]
    if egress == "enumerated":
        on = list(settled.get("enabled_switches") or [])
        lines.append(
            f"Outside services switched on: {', '.join(on)}" if on
            else "Outside services switched on: none. Every one starts off."
        )
    for note in settled.get("notes") or []:
        lines.append(str(note))
    return lines


__all__ = [
    "SCHEMA",
    "DEFAULT_BUILD_NETWORK",
    "DEFAULT_RUN_EGRESS",
    "ENUMERATED_VS_MOAT_NOTE",
    "build_network_options",
    "run_egress_options",
    "egress_switches",
    "switch_ids",
    "describe_combination",
    "assess_posture",
    "gates",
    "posture_manifest_block",
    "posture_summary_lines",
]
