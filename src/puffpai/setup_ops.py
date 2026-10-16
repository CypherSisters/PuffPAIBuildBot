"""Reusable first-run setup operations.

Single source of truth for the setup wizard's side effects, shared by the
``puffpai setup`` CLI command (see :mod:`puffpai.cli`) and the ``/setup/*`` REST
endpoints (see :mod:`puffpai.api_server`) so the terminal and graphical wizards
can never drift apart.

Every function here is small and honest: it checks Ollama, writes the two config
files the app reads, creates the reference vault, and records a "setup complete"
flag. Nothing here prompts, prints, or touches argparse/Flask - the front-ends
own the conversation; this module owns the effects.

Secrets discipline: a vault password and a Home Assistant token flow *in* to
``create_vault`` and ``set_ha_config``. They are written only to their intended
destinations (the encrypted vault; the gitignored HA config file). They are
never logged, never returned, and never placed in ``setup_status``.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Optional

import requests

from puffpai.config import get_config
from puffpai.security.lan_guard import no_proxies as _no_proxies  # marked home-network calls never use a proxy


# ---------------------------------------------------------------------------
# paths
# ---------------------------------------------------------------------------

def _config_path() -> str:
    """Absolute path of the main config file the app reads."""
    cfg = get_config()
    return os.path.join(cfg.CONFIG_DIR, "puffpai_config.json")


def _ha_config_path() -> str:
    """Absolute path of the Home Assistant config file."""
    cfg = get_config()
    return os.path.join(cfg.CONFIG_DIR, "home_assistant_config.json")


def _setup_flag_path() -> str:
    """Absolute path of the setup-complete marker.

    Lives under the data dir (honours ``PUFFPAI_DATA_DIR``) so a scratch install
    tracks its own state independently of any other install on the machine.
    """
    cfg = get_config()
    return os.path.join(cfg.DATA_DIR, "data", "setup_state.json")


def _vault_dir() -> str:
    """Resolve the vault directory from config (absolute)."""
    cfg = get_config()
    vd = cfg.paths.vault_data_dir
    return vd if os.path.isabs(vd) else os.path.join(cfg.DATA_DIR, vd)


def default_pack_dir() -> str:
    """Default folder offered for ZIM knowledge packs."""
    cfg = get_config()
    return os.path.join(cfg.DATA_DIR, "knowledge_packs")


# ---------------------------------------------------------------------------
# json helpers
# ---------------------------------------------------------------------------

def _read_json(path: str) -> dict[str, Any]:
    """Load a JSON object, returning {} on any failure."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _write_json(path: str, data: dict[str, Any]) -> None:
    """Write a JSON object with a trailing newline, creating parent dirs."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def config_template() -> dict[str, Any]:
    """Load the shipped config template, or {} if it is missing."""
    cfg = get_config()
    example = os.path.join(cfg.REPO_ROOT, "config", "puffpai_config.example.json")
    return _read_json(example)


def _base_config() -> dict[str, Any]:
    """Return the config to merge into: the existing file, else the template."""
    existing = _read_json(_config_path())
    return existing or config_template()


# ---------------------------------------------------------------------------
# Ollama
# ---------------------------------------------------------------------------

def list_models(url: Optional[str] = None, timeout: int = 3) -> Optional[list[str]]:
    """Query Ollama's ``/api/tags``.

    Returns the installed model names, or ``None`` when Ollama is unreachable
    (an empty list means Ollama is up but has no models pulled).
    """
    from puffpai import brain_health

    cfg = get_config()
    return brain_health.list_models(url or cfg.ollama.url, timeout=timeout)


def ollama_survey(default_url: Optional[str] = None,
                  timeout: int = 2) -> list[dict[str, Any]]:
    """Every address worth trying, and what each answered. See brain_health.survey.

    Goes through this module's :func:`list_models` on purpose, so a test (or a
    caller) that has substituted the probe substitutes it for the search too.
    """
    from puffpai import brain_health

    cfg = get_config()
    return brain_health.survey(default_url or cfg.ollama.url, timeout=timeout,
                               probe=list_models)


def find_ollama(default_url: Optional[str] = None,
                timeout: int = 2) -> Optional[str]:
    """The best address Ollama answers on, or None.

    Best, not first: an address that lists models beats one that answers with
    none. On the validated Windows box 127.0.0.1:11434 is a WSL relay to an EMPTY
    Ollama while the real one is on 11435, and "first that answers" chose the
    relay - an address where every prompt 404s.
    """
    from puffpai import brain_health

    return brain_health.best_address(
        ollama_survey(default_url, timeout=timeout))


def brain_status(url: Optional[str] = None, model: Optional[str] = None,
                 timeout: int = 3) -> dict[str, Any]:
    """Is the CONFIGURED MODEL there? - not merely "is Ollama up".

    The distinction this wraps is the one that let a build with no model
    installed report ``inference: connected`` and answer chat requests with
    ``success: true`` over an error string. See :mod:`puffpai.brain_health`.
    """
    from puffpai import brain_health

    cfg = get_config()
    resolved_url = url or cfg.ollama.url
    resolved_model = model or cfg.ollama.model
    return brain_health.probe(resolved_url, resolved_model,
                              installed=list_models(resolved_url, timeout=timeout))


def check_ollama(url: Optional[str] = None, timeout: int = 3) -> dict[str, Any]:
    """Report Ollama reachability, installed models, and the selected model."""
    from puffpai import brain_health

    cfg = get_config()
    resolved = url or cfg.ollama.url
    models = list_models(resolved, timeout=timeout)
    # Reachable is not ready. An Ollama that never pulled this build's model
    # answers /api/tags and 404s every chat, and a status page that reports only
    # reachability disagrees with /system/status - which has told the three-state
    # truth since brain_health landed. Same probe, no second round trip.
    state = brain_health.probe(resolved, cfg.ollama.model, installed=models)
    return {
        "reachable": models is not None,
        "models": models or [],
        "selected": cfg.ollama.model,
        "url": resolved,
        "status": state["status"],
        "model_installed": state["status"] == brain_health.CONNECTED,
        "message": state.get("message") or "",
    }


#: Config paths that name a LAN service, mapped to the lan_posture service id.
#: Anything written through :func:`write_main_config` that lands on one of these
#: is checked against the build's run posture before it reaches disk.
_LAN_SERVICE_FIELDS: dict[tuple[str, str], str] = {
    ("ollama", "url"): "ollama",
    ("n8n", "url"): "n8n",
    ("kiwix", "url"): "kiwix",
    ("dashboard", "url"): "dashboard",
    ("ha", "url"): "home_assistant",
    ("media_server", "jellyfin_url"): "jellyfin",
    ("media_server", "calibre_web_url"): "calibre_web",
}


def _check_lan_posture(values: dict[str, Any],
                       posture: Optional[str] = None) -> None:
    """Refuse to persist a service URL the build's run posture forbids.

    This is the surface that refuses outright rather than neutralising: nothing
    is running against the value yet and the person is standing right there to
    read the message, so refusing the write is free. It also means the common
    case — someone typing an address into the wizard — never reaches the
    loader's neutralisation path at all.

    Raises :class:`~puffpai.security.lan_posture.PostureViolation`, whose
    ``user_message`` is written to be shown verbatim. Never raises for anything
    else: a resolver that is down, or a posture module that will not import,
    must not block the wizard.

    ``posture`` lets a caller that has already settled the posture state it,
    rather than letting the write path fall back to ``sealed``. That fallback is
    correct for the CLI, where the posture step always declares the answer
    before anything is written; it is wrong for a caller like the browser
    onboarding route, which has no posture question and would otherwise be able
    to write nothing but loopback. A stated posture is still normalised by
    :mod:`puffpai.security.lan_posture`, so this cannot be used to invent a
    permissive tier.
    """
    try:
        from puffpai.security import lan_posture
    except Exception:  # pragma: no cover - import guard only
        return
    for key, sub in values.items():
        if not isinstance(sub, dict):
            continue
        for field, url in sub.items():
            service_id = _LAN_SERVICE_FIELDS.get((key, field))
            if service_id and isinstance(url, str) and url.strip():
                lan_posture.require_allowed(service_id, url, posture=posture)


def write_main_config(values: dict[str, Any]) -> str:
    """Merge ``values`` into the main config file and write it back.

    ``values`` is a shallow map of section -> dict (e.g. ``{"ollama": {...}}``).
    Dict sections are merged key-by-key onto the existing config (or the shipped
    template on a fresh install); scalars replace. Reloads the config singleton
    so subsequent reads see the change. Returns the file path.
    """
    base = _base_config()
    for key, sub in values.items():
        if isinstance(sub, dict):
            section = base.get(key)
            if not isinstance(section, dict):
                section = {}
            section.update(sub)
            base[key] = section
        else:
            base[key] = sub
    _check_lan_posture(values)
    path = _config_path()
    _write_json(path, base)
    get_config(_force_reload=True)
    return path


def set_ollama_config(model: str, url: Optional[str] = None) -> str:
    """Persist the chosen Ollama model (and optionally URL). Returns config path."""
    cfg = get_config()
    ollama: dict[str, Any] = {"url": url or cfg.ollama.url}
    if model:
        # No model means none fits this machine; an empty name is not written
        # over whatever the config already says.
        ollama["model"] = model
    return write_main_config({"ollama": ollama})


# ---------------------------------------------------------------------------
# Vault
# ---------------------------------------------------------------------------

def create_vault(password: str, vault: Any = None, *,
                 with_recovery: bool = False) -> Optional[str]:
    """Create the encrypted vault, protected by ``password``.

    Goes through the ported reference :class:`~puffpai.security.vault.Vault`
    (PBKDF2 + Fernet) - no crypto is duplicated here. The vault is left locked;
    the password is never stored, logged, or returned. Raises
    :class:`~puffpai.security.vault.VaultError` if a vault already exists or the
    password is empty.

    ``with_recovery`` decides whether a recovery code is issued alongside the
    vault, and it is the reason this function has a return value at all. The two
    wizard faces have to behave the same way: the CLI's Zone 1 step issues a code
    and makes the owner type it back, so the browser onboarding that posts to
    ``/setup/vault`` must be able to do the same rather than quietly creating a
    PIN-only vault nobody can ever get back into. The flag is opt-in so that
    every existing caller keeps exactly the behaviour it had - a legacy caller
    creates a PIN-only vault, and a manifest written for it truthfully says
    ``recovery_issued: false``.

    Returns the recovery code when one was issued, and ``None`` otherwise. The
    code is returned for exactly one screen: it is never written to disk, never
    logged, and never placed in a manifest. A caller that stores it anywhere has
    broken the contract this step makes with the owner.

    Note for the wizard: the CLI does not call this directly. It goes through
    :func:`seal_vault_with_recovery`, which adds the stricter PIN policy a typed
    -back PIN needs (see :mod:`puffpai.zone_vault`) and which a passphrase like
    "correct horse battery staple" would fail. Both land on the same
    :meth:`~puffpai.security.vault.Vault.create_with_recovery` call, so there is
    one issuance behaviour and one crypto path underneath both faces.
    """
    from puffpai.security.vault import Vault

    if vault is None:
        vault = Vault(vault_dir=_vault_dir())
    code: Optional[str] = None
    if with_recovery:
        _session, code = vault.create_with_recovery(password)
    else:
        vault.create(password)
    # Do not leave a dangling unlocked session with no token; the normal flow
    # re-unlocks through /vault/unlock. create() leaves it unlocked, so lock it.
    vault.lock()
    return code


def vault_initialized(vault: Any = None) -> bool:
    """True once the vault's salt + encrypted store exist on disk."""
    from puffpai.security.vault import Vault

    if vault is None:
        vault = Vault(vault_dir=_vault_dir())
    return bool(vault.is_initialized)


# ---------------------------------------------------------------------------
# Home Assistant
# ---------------------------------------------------------------------------

def set_ha_config(url: str, token: str, enabled: bool = True,
                  posture: Optional[str] = None) -> str:
    """Write ``home_assistant_config.json`` with the given URL + token.

    The token is written only to this gitignored file; it is never logged or
    echoed. Returns the file path.

    **Posture-checked before the token touches disk.** Home Assistant is the one
    integration that carries a credential, and it is written to its own file
    rather than through :func:`write_main_config`, so it missed the LAN posture
    check that every other service URL goes through:
    ``--ha-url http://8.8.8.8:8123 --ha-token <token>`` was accepted and
    written under ``island`` in the same run that refused a public Ollama
    address. The check runs here, at the write, so no caller can route around it
    -- and it runs BEFORE :func:`_write_json`, so a refusal leaves no token on
    disk. Raises
    :class:`~puffpai.security.lan_posture.PostureViolation`.
    """
    _check_lan_posture({"ha": {"url": url}}, posture)
    path = _ha_config_path()
    _write_json(path, {"url": url, "token": token, "enabled": bool(enabled)})
    get_config(_force_reload=True)
    return path


def test_ha_connection(url: str, token: str, timeout: int = 5) -> dict[str, Any]:
    """Probe a Home Assistant instance with a candidate URL + token.

    Standalone (does not depend on the configured/enabled bridge) so the wizard
    can validate credentials before saving them. Returns ``{"ok": bool, ...}``
    with a HA version on success or a short ``error`` string on failure. Never
    raises; never includes the token in the result.
    """
    base = (url or "").rstrip("/")
    if not base or not token:
        return {"ok": False, "error": "URL and token are both required."}
    try:
        resp = requests.get(  # lan-egress: Home Assistant /api/config probe, LAN
            f"{base}/api/config",
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
            proxies=_no_proxies(),
        )
    except requests.exceptions.ConnectionError:
        return {"ok": False, "error": f"Cannot reach Home Assistant at {base}."}
    except requests.exceptions.Timeout:
        return {"ok": False, "error": "Home Assistant did not respond in time."}
    except requests.exceptions.RequestException as exc:
        return {"ok": False, "error": f"Request failed: {exc}"}

    if resp.status_code in (401, 403):
        return {"ok": False, "error": "Token rejected (check the long-lived token)."}
    if not resp.ok:
        return {"ok": False, "error": f"Home Assistant returned HTTP {resp.status_code}."}
    try:
        info = resp.json()
    except ValueError:
        info = {}
    return {
        "ok": True,
        "version": info.get("version", "unknown"),
        "location_name": info.get("location_name", ""),
    }


def ha_configured() -> bool:
    """True when HA is configured with a URL + token and enabled."""
    data = _read_json(_ha_config_path())
    return bool(data.get("enabled")) and bool(data.get("url")) and bool(data.get("token"))


def ha_url() -> str:
    """Configured HA URL for prefill (never the token). Empty if unset."""
    return str(_read_json(_ha_config_path()).get("url", ""))


# ---------------------------------------------------------------------------
# Knowledge
# ---------------------------------------------------------------------------

def set_knowledge_config(zim_dir: str, auto_index: bool = True,
                         create_dir: bool = False) -> str:
    """Persist the knowledge-pack folder and auto-index flag. Returns config path.

    When ``create_dir`` is set and ``zim_dir`` is given, the folder is created
    (best effort) so the user has somewhere to drop packs.
    """
    zim_dir = zim_dir or ""
    if create_dir and zim_dir:
        try:
            os.makedirs(zim_dir, exist_ok=True)
        except OSError:
            pass
    # ``data_dir`` is the one setting (knowledge/my_data.py); ``zim_dir``
    # carries the same value for older readers.
    return write_main_config({
        "knowledge": {"zim_dir": zim_dir, "data_dir": zim_dir,
                      "auto_index": bool(auto_index)},
    })


# ---------------------------------------------------------------------------
# BuildBot wizard: hardware census, brain menu, crew, interview, manifest
#
# These wrap the census/catalog/manifest modules so the CLI and the /setup/*
# endpoints drive one code path. The read steps (census, menu, crew, interview)
# are side-effect-free; the manifest is the one that writes, and it is split into
# preview (assemble + diff, no write) and commit (write) so a face can show what
# would change before it lands - the wizard never overwrites blindly.
# ---------------------------------------------------------------------------

def hardware_census() -> dict[str, Any]:
    """Run the hardware census (RAM-first). Never raises."""
    from puffpai import hardware_census as hc
    return hc.run_census()


def brain_menu(census: dict[str, Any], probe_ollama: bool = True) -> dict[str, Any]:
    """Fit the local-model menu to a census, reconciling installed Ollama tags."""
    from puffpai import model_catalog
    installed = list_models() if probe_ollama else None
    return model_catalog.fit_menu(census, installed_models=installed)


def crew_budget(census: dict[str, Any]) -> dict[str, Any]:
    """Return the specialist crew budget for a census."""
    from puffpai import crew_sizing
    return crew_sizing.crew_budget(census)


def build_interview(goal: str, census: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Propose capabilities for a goal, within the census's crew budget."""
    from puffpai import build_interview as bi
    budget = crew_budget(census) if census is not None else None
    return bi.propose(goal, crew_budget=budget)


def preview_manifest(census: dict[str, Any], **choices: Any) -> dict[str, Any]:
    """Assemble a manifest from the census + choices and diff it against any
    existing one, WITHOUT writing. Returns ``{manifest, diff, warnings}``.

    Choices are the optional sections :func:`build_manifest.assemble_manifest`
    knows: ``identity``, ``brain``, ``crew``, ``storage``, ``knowledge``,
    ``egress``, ``integrations``, ``posture``, ``surfaces``, ``provenance``.

    ``vault`` and ``bridge`` are handled here rather than there: they are
    status blocks the zone steps produce (see :mod:`puffpai.zone_vault` and
    :mod:`puffpai.zone_bridge`), so they are attached to the assembled manifest
    as top-level blocks. They go through ``sanitize_manifest`` on the way to
    disk like everything else, which is what keeps a PIN or a recovery code from
    riding along even if a caller tried.
    """
    from puffpai import build_manifest
    vault = choices.pop("vault", None)
    bridge = choices.pop("bridge", None)
    # ``persona`` is the same shape of thing: a record of what the wizard
    # scaffolded for a goal nothing in the catalog matched (see
    # :mod:`puffpai.persona_scaffold`). It is a statement about files on disk,
    # never a setting, and it rides through sanitize_manifest like the rest.
    persona = choices.pop("persona", None)
    manifest = build_manifest.assemble_manifest(census, **choices)
    if vault is not None:
        manifest["vault"] = vault
    if bridge is not None:
        manifest["bridge"] = bridge
    if persona is not None:
        manifest["persona"] = persona
    diff = build_manifest.diff_manifest(build_manifest.read_manifest(), manifest)
    warnings = build_manifest.validate_manifest(manifest)
    return {
        "manifest": build_manifest.sanitize_manifest(manifest),
        "diff": diff,
        "warnings": warnings,
    }


def commit_manifest(manifest: dict[str, Any]) -> str:
    """Sanitize and write a manifest to disk (the contract guard runs here).

    Returns the path. Call after showing :func:`preview_manifest`'s diff.
    """
    from puffpai import build_manifest
    return build_manifest.write_manifest(manifest)


def export_build_bundle(manifest: dict[str, Any], dest_dir: str,
                        payloads: Optional[dict[str, str]] = None,
                        references: Optional[dict[str, Any]] = None,
                        label: str = "") -> dict[str, Any]:
    """Write a provisioning bundle for the sneakernet path. No network."""
    from puffpai import build_bundle
    return build_bundle.export_bundle(manifest, dest_dir, payloads=payloads,
                                      references=references, label=label)


def verify_build_bundle(src_dir: str) -> tuple[bool, list[str]]:
    """Dry-run a bundle's checksums. Returns ``(ok, plain-English problems)``."""
    from puffpai import build_bundle
    return build_bundle.verify_bundle(src_dir)


def import_build_bundle(src_dir: str, dest_dir: Optional[str] = None) -> dict[str, Any]:
    """Verify a bundle whole, then apply it into this install. No network.

    Payload files land under the data dir; the manifest lands where this install
    reads its manifest from, so an imported build is the build that runs.
    """
    from puffpai import build_bundle, build_manifest
    cfg = get_config()
    target = dest_dir or cfg.DATA_DIR
    return build_bundle.import_bundle(src_dir, target,
                                      manifest_target=build_manifest.manifest_path())


# ---------------------------------------------------------------------------
# BuildBot wizard v2: posture, the three zones, surfaces, config health
#
# Same thin-wrapper discipline as the block above. Step 0 (posture) is first
# because its gates decide what every later step is allowed to offer; the zone
# wrappers exist so the CLI and the /setup/* endpoints walk Vault and Bridge
# through one code path rather than two.
# ---------------------------------------------------------------------------

def posture_options() -> dict[str, Any]:
    """The Step 0 menus: both axes plus the enumerated egress table."""
    from puffpai import build_posture
    return {
        "build_network": build_posture.build_network_options(),
        "run_egress": build_posture.run_egress_options(),
        "egress_switches": build_posture.egress_switches(),
    }


def assess_posture(build_network: Optional[str] = None,
                   run_egress: Optional[str] = None,
                   enabled_switches: Optional[list[str]] = None) -> dict[str, Any]:
    """Settle the build posture from what the owner stated, or the defaults.

    ``None`` means "the owner skipped this axis", and the result records that the
    value was defaulted rather than chosen. Never raises.
    """
    from puffpai import build_posture
    return build_posture.assess_posture(build_network, run_egress, enabled_switches)


def persist_posture(posture: dict[str, Any]) -> str:
    """Write the posture the owner just chose into the runtime config.

    The wizard recorded its enumerated egress table in the MANIFEST and nowhere
    else, while the runtime reads ``egress_doors`` out of
    ``config/puffpai_config.json`` — which the wizard seeds from the shipped
    example, which carried an explicit ``"weather": false``. An explicit config
    value outranks the manifest fallback, so
    ``--run-egress enumerated --egress-door weather`` produced a manifest saying
    the door was open and a runtime with it shut. The owner's answer has to be
    the thing the runtime honours, so it is written here, once, into the file
    the runtime and the status page both read.

    Every door the wizard did not open is written ``false`` explicitly rather
    than left out. A door table that lists only what is open cannot be read as
    a statement about what is shut, and this file is meant to be readable.

    Also declares the run posture for the rest of this wizard run
    (:func:`puffpai.security.lan_posture.set_active_posture`), so a URL written
    later in the same run is judged against the answer just given rather than
    against a manifest that does not exist yet.
    """
    from puffpai.security import egress_doors, lan_posture

    lan_posture.set_active_posture(posture)

    chosen = set((posture or {}).get("enabled_switches") or [])
    doors: dict[str, Any] = {}
    for door in egress_doors.DOORS:
        doors[door.config_flag] = bool(
            door.posture_switch and door.posture_switch in chosen)
    path = write_main_config({"egress_doors": doors})
    egress_doors.reset_cache()
    return path


def posture_gates(posture: dict[str, Any]) -> dict[str, bool]:
    """What the posture permits later steps to OFFER. Never a decision to do."""
    from puffpai import build_posture
    return build_posture.gates(posture)


def posture_block(posture: dict[str, Any]) -> dict[str, Any]:
    """The posture, shaped for the manifest's ``posture`` block."""
    from puffpai import build_posture
    return build_posture.posture_manifest_block(posture)


def validate_vault_pin(pin: str) -> tuple[bool, str]:
    """Check a proposed vault PIN. Returns ``(ok, plain-English reason)``.

    Runs both policies, because neither one alone is a superset of the other.
    :func:`puffpai.zone_vault.validate_pin` is the wizard's policy (no spaces,
    no patterns, no common choices); :func:`puffpai.security.vault.validate_vault_secret`
    is the floor the vault itself enforces on the way in, and its length
    requirement is the stricter of the two for a mixed-character PIN. Checking
    only the first would let a PIN through the prompt and then have the seal
    raise, which reads to the owner as the wizard changing its mind.

    The PIN is not logged, stored, or echoed back inside the reason.
    """
    from puffpai import zone_vault
    from puffpai.security.vault import validate_vault_secret

    ok, reason = zone_vault.validate_pin(pin)
    if not ok:
        return ok, reason
    return validate_vault_secret(pin)


def vault_recovery_notice() -> list[str]:
    """The 'write this code down' screen, in order, ready to print."""
    from puffpai import zone_vault
    return zone_vault.recovery_notice_lines()


def confirm_vault_recovery(shown: str, typed: str) -> bool:
    """True when the owner typed back the code we showed. Fails closed."""
    from puffpai import zone_vault
    return zone_vault.verify_recovery_confirmation(shown, typed)


def seal_vault_with_recovery(pin: str, vault: Any = None
                             ) -> tuple[dict[str, Any], str]:
    """Create the vault under ``pin`` and issue its recovery code.

    This is the wizard's vault path, and the only one it may use: the PIN-only
    :func:`puffpai.zone_vault.seal_vault` would run the whole "write this down"
    ceremony and hand back nothing to write. Returns
    ``(manifest_block, recovery_code)`` - the code is a separate return value on
    purpose, so a caller that persists the block cannot persist the code.

    The vault handle is resolved from this install's data dir when one is not
    passed, so a scratch install seals its own vault and never a neighbour's.
    Raises :class:`ValueError` for a PIN that fails
    :func:`validate_vault_pin`, and lets
    :class:`puffpai.security.vault.VaultError` through when a vault already
    exists or the PIN is under the vault's own floor.
    """
    from puffpai import zone_vault
    from puffpai.security.vault import Vault

    handle = vault if vault is not None else Vault(vault_dir=_vault_dir())
    return zone_vault.seal_vault_with_recovery(pin, vault=handle)


def rotate_vault_recovery(current_code: str, pin: str, vault: Any = None) -> str:
    """Burn a recovery code that was shown but never written down, issue another.

    The escape hatch behind "I cannot do this right now": the code already on the
    screen is treated as spent, the vault is re-keyed under the same PIN, and a
    fresh code is returned for a fresh attempt. Better than continuing with a
    code nobody copied. Lets :class:`puffpai.security.vault.VaultError` through.
    """
    from puffpai.security.vault import Vault

    handle = vault if vault is not None else Vault(vault_dir=_vault_dir())
    _session, new_code = handle.redeem_recovery_code(current_code, pin)
    handle.lock()
    return new_code


def vault_block(*, configured: bool = True, sealed: bool = True,
                method: str = "pin", recovery_issued: bool = False,
                recovery_confirmed: bool = False) -> dict[str, Any]:
    """Status-only vault record for the manifest. Takes no PIN and no code."""
    from puffpai import zone_vault
    return zone_vault.vault_manifest_block(
        configured=configured, sealed=sealed, method=method,
        recovery_issued=recovery_issued, recovery_confirmed=recovery_confirmed)


def bridge_options(has_home_assistant: bool = False,
                   has_external_firewall: bool = False,
                   has_managed_lan: bool = False) -> dict[str, Any]:
    """The four Bridge postures plus a suggestion for what the owner says they
    have. A suggestion, never a setting - the ladder always comes back whole.
    """
    from puffpai import zone_bridge
    return zone_bridge.posture_for_environment(
        has_home_assistant, has_external_firewall, has_managed_lan)


def reconcile_bridge(posture: str, run_egress: str) -> tuple[str, str]:
    """Reconcile a Bridge posture with Step 0's egress tier. Stricter wins."""
    from puffpai import zone_bridge
    return zone_bridge.reconcile_with_egress(posture, run_egress)


def bridge_block(posture: str, run_egress: Optional[str] = None,
                 environment: Optional[dict[str, Any]] = None,
                 chosen_by: str = "owner") -> dict[str, Any]:
    """The reconciled Bridge policy, shaped for the manifest."""
    from puffpai import zone_bridge
    return zone_bridge.bridge_manifest_block(
        posture, run_egress=run_egress, environment=environment,
        chosen_by=chosen_by)


def derive_surfaces(proposal: dict[str, Any]) -> dict[str, Any]:
    """Propose the surface allowlist a build interview's matches imply."""
    from puffpai import build_interview as bi
    return bi.derive_surfaces(proposal)


def apply_surface_choices(derivation: dict[str, Any],
                          add: Optional[list[str]] = None,
                          remove: Optional[list[str]] = None) -> dict[str, Any]:
    """Apply the owner's additions and removals to a proposed allowlist."""
    from puffpai import build_interview as bi
    return bi.apply_surface_choices(derivation, add=add, remove=remove)


def surfaces_block(derivation: dict[str, Any]) -> dict[str, Any]:
    """The allowlist, shaped for the manifest's ``surfaces`` block."""
    from puffpai import build_interview as bi
    return bi.surfaces_manifest_block(derivation)


# ---------------------------------------------------------------------------
# Zone 2: where knowledge comes from, and how long learning it will take
#
# Three doors into the Brain zone, and they are equals: the vetted online
# registry, the owner's own folders, and whatever is already sitting in the
# knowledge folder. Same thin-wrapper discipline as everything above - these
# decide nothing, they only give the CLI and the /setup/* endpoints one shape to
# call so the terminal and graphical wizards cannot drift apart.
#
# Two of these wrappers exist as much to *remove* an argument as to add one.
# `fetch_source` has no `registry` parameter and `source_preview` passes none,
# because that is the single argument that can widen what
# :mod:`puffpai.source_fetch` is allowed to reach. A front-end therefore has no
# way to supply it and always gets the shipped list.
# ---------------------------------------------------------------------------

def source_search(goal: Optional[str]) -> dict[str, Any]:
    """Vetted sources matching a goal, plus the plain-words framing to show.

    Reads the shipped registry and opens no socket - this searches a curated
    list, not the web, and the result says so out loud. Every candidate carries
    its license, its size, its address, and the exact terms that earned it a
    place. Never raises.
    """
    from puffpai import source_registry
    return source_registry.search_summary(goal)


def source_search_lines(result: dict[str, Any]) -> list[str]:
    """Plain-language lines for a :func:`source_search` result."""
    from puffpai import source_registry
    return source_registry.candidate_summary_lines(result)


def source_preview(source_id: str, posture: dict[str, Any],
                   dest_dir: Optional[str] = None) -> dict[str, Any]:
    """The consent screen: license, size, address, and where it would land.

    No network at all. :func:`fetch_source` refuses until a caller has been
    through here and says so, which is how "nothing bulky downloads without the
    size shown first" is enforced in code rather than trusted to a front-end.
    """
    from puffpai import source_fetch
    return source_fetch.preview(source_id, posture, None, dest_dir)


def fetch_source(source_id: str, posture: dict[str, Any], *,
                 size_acknowledged: bool = False,
                 dest_dir: Optional[str] = None) -> dict[str, Any]:
    """Download one vetted source. Refusals come back as records; never raises.

    ``size_acknowledged`` is the owner's word after reading
    :func:`source_preview`, and no front-end may set it on their behalf - a
    False here produces a refusal that names the size, which is the intended
    outcome rather than an error. ZIM packs land in the watched knowledge
    folder, so :mod:`puffpai.knowledge.auto_index` finds them with no further
    wiring.
    """
    from puffpai import source_fetch
    if size_acknowledged:
        # Drawbridge's one policy, like every other book download.
        from puffpai.knowledge import find_more
        cand = find_more.look(source_id)
        blocked = find_more.drawbridge_block(cand, via="setup") if cand else None
        if blocked is not None:
            return {"ok": False, "status": "refused", "source_id": source_id,
                    "reason": str(blocked.get("line") or "It did not pass "
                                  "BuildBot's checks, so it was not added."),
                    "drawbridge": blocked}
    return source_fetch.fetch(source_id, posture,
                              size_acknowledged=size_acknowledged,
                              dest_dir=dest_dir)


def fetch_source_lines(result: dict[str, Any]) -> list[str]:
    """Plain-language lines for a :func:`fetch_source` result. Metadata only."""
    from puffpai import source_fetch
    return source_fetch.fetch_summary_lines(result)


def source_manifest_entry(result: dict[str, Any]) -> dict[str, Any]:
    """One fetched source, shaped for the manifest. Metadata only, no path.

    Records what a later audit or a revocation needs - the id, the label, the
    license, the size and the checksum - and nothing about where on this disk it
    landed. The full provenance is written next to the file itself by
    :mod:`puffpai.source_fetch`; this is only the build record's index of it.
    """
    provenance = result.get("provenance") or {}
    return {
        "id": str(result.get("source_id") or ""),
        "label": str(provenance.get("label") or ""),
        "license": str(provenance.get("license") or ""),
        "bytes": int(result.get("bytes") or 0),
        "sha256": str(result.get("sha256") or ""),
    }


def data_location_suggestions() -> list[dict[str, Any]]:
    """Places worth offering the owner. Suggestions only - nothing is scanned."""
    from puffpai import data_sources
    return data_sources.suggested_locations()


def data_source_notes() -> dict[str, str]:
    """The three things the owner has to have read before they say yes.

    The vault note (sensitive material is routed to the vault, and that is not a
    setting), the consent note (a yes to one folder is not a yes to the drive),
    and the record note (what the build record keeps about a folder). The record
    note in particular is useless after the fact, so it belongs on the
    confirmation screen rather than in a summary printed afterwards.
    """
    from puffpai import data_sources
    return {
        "vault": data_sources.VAULT_NOTE,
        "consent": data_sources.CONSENT_NOTE,
        "record": data_sources.RECORD_NOTE,
    }


def survey_data_locations(paths: list[str]) -> dict[str, Any]:
    """Count what is in the folders the owner named. Opens no file.

    Only ever called with paths the owner supplied; the suggestion list is not
    an input to this. Unreadable paths come back as notes on the location rather
    than as exceptions, and nothing is copied, moved or indexed here.
    """
    from puffpai import data_sources
    return data_sources.survey(paths)


def data_survey_lines(surveyed: dict[str, Any]) -> list[str]:
    """Plain-language lines for a survey, ending in the three consent notes."""
    from puffpai import data_sources
    return data_sources.source_summary_lines(surveyed)


def approve_data_locations(surveyed: dict[str, Any],
                           approve: Optional[list[str]] = None) -> dict[str, Any]:
    """Record the per-location yeses. Everything unnamed is declined.

    There is deliberately no approve-all: the caller has to name each path it is
    claiming a yes for, and a location nobody answered about is a no.
    """
    from puffpai import data_sources
    return data_sources.apply_choices(surveyed, approve)


def data_sources_block(selection: dict[str, Any]) -> dict[str, Any]:
    """The approved locations, shaped for the manifest.

    Redacted folder paths and counts by file type, nothing else - no filename,
    no subdirectory, no contents. A declined location leaves no trace at all.
    """
    from puffpai import data_sources
    return data_sources.sources_manifest_block(selection)


def pending_index_volume(zim_dir: str) -> dict[str, Any]:
    """How much unlearned material is waiting in the knowledge folder.

    Reads pack headers only; nothing is indexed here. A missing or empty folder
    is a volume of zero packs rather than an error.
    """
    from puffpai.index_estimate import volume_from_packs
    from puffpai.knowledge.auto_index import PackIndex, find_new_packs
    try:
        packs = find_new_packs(zim_dir, PackIndex())
    except Exception:  # noqa: BLE001 - an unreadable folder is "nothing waiting"
        packs = []
    return volume_from_packs(packs)


def index_estimate_for(volume: dict[str, Any]) -> dict[str, Any]:
    """How long this machine will take to learn ``volume``, or an honest unknown.

    Measured from this box's own indexing history and from nothing else. The
    wizard does **not** calibrate: a calibration sample indexes real articles in
    the foreground, and this step's whole promise is that learning happens in
    the background. With no history the answer is ``basis: unknown``, carrying
    no duration at all - which the bot says out loud rather than papering over
    with a number it invented.
    """
    from puffpai import index_estimate
    return index_estimate.estimate(volume)


def index_estimate_lines(est: dict[str, Any]) -> list[str]:
    """The spoken estimate plus its basis, ready to print. Never empty."""
    from puffpai import index_estimate
    return index_estimate.estimate_summary_lines(est)


def spoken_index_estimate(est: dict[str, Any]) -> str:
    """The one paragraph the bot says before it starts learning."""
    from puffpai import index_estimate
    return index_estimate.spoken_estimate(est)


def start_indexing(zim_dir: str) -> int:
    """Hand the waiting packs to the background indexer; return the pack count.

    Never blocks - the work goes to a daemon thread - and never fails the
    wizard: knowledge is additive, so a background start that will not start is
    reported as zero packs rather than raised as an error.
    """
    from puffpai.knowledge.auto_index import start_background_index
    try:
        count, _thread = start_background_index(zim_dir)
    except Exception:  # noqa: BLE001 - indexing is additive, never a gate
        return 0
    return int(count)


# The configs whose degradation changes *what the build is* rather than how well
# it is tuned. A fallback capability catalog builds somebody a different bot; a
# fallback model catalog offers them a different brain. Those two are worth
# stopping for. A fallback model profile or rulepack degrades the tuning of the
# build the owner asked for, which is worth saying and not worth a gate.
IDENTITY_CONFIGS = ("capability_catalog", "model_catalog")


def config_health_status() -> dict[str, Any]:
    """What the first-party configs did on this run, and whether it matters.

    ``blocking`` names the degraded configs from :data:`IDENTITY_CONFIGS`; when
    it is non-empty the wizard asks for an explicit acknowledgment before it
    commits a manifest. It never refuses: an owner with a corrupt file and no
    internet has to be able to finish, and an acknowledged, recorded degradation
    beats a dead end. The manifest records the degradation either way.
    """
    from puffpai import config_health
    events = config_health.report(degraded_only=True)
    configs = sorted({str(e.get("config")) for e in events})
    return {
        "degraded": config_health.is_degraded(),
        "configs": configs,
        "blocking": [c for c in configs if c in IDENTITY_CONFIGS],
        "reduced": any(e.get("reduced") for e in events),
        "summary": config_health.summary_lines(),
        "events": events,
    }


def manifest_status() -> dict[str, Any]:
    """Whether a build manifest exists yet, and a short summary if so."""
    from puffpai import build_manifest
    existing = build_manifest.read_manifest()
    if not existing:
        return {"exists": False, "summary": []}
    return {"exists": True, "summary": build_manifest.manifest_summary_lines(existing)}


# ---------------------------------------------------------------------------
# completion flag + status
# ---------------------------------------------------------------------------

def mark_complete() -> None:
    """Record that first-run setup has been completed."""
    _write_json(_setup_flag_path(), {"complete": True, "completed_at": time.time()})


def clear_complete() -> None:
    """Clear the setup-complete flag (used to re-run the wizard / in tests)."""
    try:
        os.remove(_setup_flag_path())
    except FileNotFoundError:
        pass


def is_complete() -> bool:
    """True once :func:`mark_complete` has been called for this data dir."""
    return bool(_read_json(_setup_flag_path()).get("complete"))


def setup_status(probe_ollama: bool = True) -> dict[str, Any]:
    """Summarise which setup steps are done - for the wizard and first-run gate.

    Contains no secrets: never the vault password, never the HA token, never a
    vault PIN or recovery code - the zone blocks it reports are status-only by
    construction. Set ``probe_ollama`` False to skip the network round-trip (the
    first-run gate only needs ``complete``).

    Alongside the v1 keys it carries what the v2 wizard decided, read back from
    the committed manifest: the build posture, the three zones, the surface
    allowlist, and whether any first-party config was degraded when this build
    was assembled.
    """
    from puffpai import build_manifest

    cfg = get_config(_force_reload=True)

    if probe_ollama:
        ollama = check_ollama()
    else:
        ollama = {"reachable": None, "models": [], "selected": cfg.ollama.model,
                  "url": cfg.ollama.url}

    manifest = build_manifest.read_manifest() or {}
    knowledge = {
        "zim_dir": cfg.knowledge.zim_dir,
        "auto_index": bool(cfg.knowledge.auto_index),
    }
    recorded_vault = manifest.get("vault")
    zone_vault_state: dict[str, Any] = dict(recorded_vault) if isinstance(recorded_vault, dict) else {}
    # The manifest says what the wizard did; the disk says what is there now.
    zone_vault_state["initialized"] = vault_initialized()

    return {
        "ollama": ollama,
        "vault": {"initialized": vault_initialized()},
        "ha": {"configured": ha_configured(), "url": ha_url()},
        "knowledge": knowledge,
        "posture": manifest.get("posture"),
        "zones": {
            "vault": zone_vault_state,
            "brain": knowledge,
            "bridge": manifest.get("bridge"),
        },
        "surfaces": manifest.get("surfaces"),
        "config_health": config_health_status(),
        "manifest": manifest_status(),
        "complete": is_complete(),
    }


__all__ = [
    "list_models",
    "ollama_survey",
    "find_ollama",
    "brain_status",
    "check_ollama",
    "write_main_config",
    "set_ollama_config",
    "create_vault",
    "vault_initialized",
    "set_ha_config",
    "test_ha_connection",
    "ha_configured",
    "ha_url",
    "set_knowledge_config",
    "default_pack_dir",
    "hardware_census",
    "brain_menu",
    "crew_budget",
    "build_interview",
    "preview_manifest",
    "commit_manifest",
    "manifest_status",
    "export_build_bundle",
    "verify_build_bundle",
    "import_build_bundle",
    "posture_options",
    "assess_posture",
    "posture_gates",
    "posture_block",
    "validate_vault_pin",
    "vault_recovery_notice",
    "confirm_vault_recovery",
    "seal_vault_with_recovery",
    "rotate_vault_recovery",
    "vault_block",
    "bridge_options",
    "reconcile_bridge",
    "bridge_block",
    "derive_surfaces",
    "apply_surface_choices",
    "surfaces_block",
    "source_search",
    "source_search_lines",
    "source_preview",
    "fetch_source",
    "fetch_source_lines",
    "source_manifest_entry",
    "data_location_suggestions",
    "data_source_notes",
    "survey_data_locations",
    "data_survey_lines",
    "approve_data_locations",
    "data_sources_block",
    "pending_index_volume",
    "index_estimate_for",
    "index_estimate_lines",
    "spoken_index_estimate",
    "start_indexing",
    "IDENTITY_CONFIGS",
    "config_health_status",
    "mark_complete",
    "clear_complete",
    "is_complete",
    "setup_status",
]
