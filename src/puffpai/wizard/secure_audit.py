"""The whole-build baseline check: declared permissions against this posture.

The audit half of the security step, and deliberately the small honest
version of it. Every
installed package declares what it may reach (``permissions.network_hosts``),
what of the house it may talk to (``ha_domains``), what credentials it wants
(``secrets``) and whether it reaches the network at build time or at run time
(``egress``). The posture the owner chose says which of those are allowed to
exist at all. This module compares the two and returns the disagreements.

What it is not, stated so nobody reads more into a passing check than is there:

* **Declared, not enforced.** Built-in packages are trusted in-process modules
  (spec A.6); a manifest saying ``network_hosts: []`` is a claim reviewed
  before the package shipped, not a sandbox. This check catches a package whose
  *declaration* does not fit the build. It cannot catch one that lies.
* **Installed packages only.** There is no third-party package path in v1, so
  in practice this audits the twelve built-ins -- which is worth doing anyway,
  because the twelve are what a posture change has to re-check.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

from typing import Any

#: The most a package may claim at each run_egress tier, as a sentence for the
#: owner. Keyed by the tier the posture settled on.
_TIER_WORDS = {
    "sealed": "reaches nothing at all",
    "island": "reaches your own network and nothing beyond it",
    "enumerated": "reaches only the outside services you switched on",
}


def _violations_for(package: Any, posture: dict, gates: dict) -> list[str]:
    manifest = getattr(package, "manifest", None) or {}
    permissions = manifest.get("permissions") or {}
    hosts = list(permissions.get("network_hosts") or [])
    ha_domains = list(permissions.get("ha_domains") or [])
    secrets = list(manifest.get("secrets") or []) or list(
        permissions.get("secrets") or [])
    files = list(permissions.get("files") or [])
    egress = str(manifest.get("egress") or "none")
    run_egress = str((posture or {}).get("run_egress") or "")

    out: list[str] = []
    if secrets:
        # v1 has no credential path at all (spec A.5). A package naming one is
        # a package asking for something this build cannot give it safely.
        out.append(f"declares the credential handle(s) {', '.join(secrets)}, "
                   f"and this build has no path that could fill them")
    # ``run_egress`` governs what the FINISHED bot may reach, so the hosts it
    # rules on are the ones a package would reach at runtime. A package
    # declaring ``egress: build_only`` names hosts the wizard may reach *while
    # building* -- which is the ``build_network`` axis, checked below -- and
    # flagging those against a sealed runtime would put a violation on every
    # build that can download a knowledge pack. An audit that cries wolf on
    # every build is an audit nobody reads.
    if hosts and egress != "build_only" and run_egress == "sealed":
        out.append(f"declares the host(s) {', '.join(hosts)} while this build "
                   f"reaches nothing at all")
    if egress == "runtime" and run_egress == "sealed":
        out.append("reaches the network while it runs, and this build "
                   "reaches nothing at all")
    # Deliberately NOT a violation: a ``build_only`` package declaring hosts on
    # an offline build. The build-network gate is what stops the fetch, the
    # skill records a gap saying nothing was downloaded, and a declaration of
    # where it would have gone is the honest thing for a manifest to carry.
    if ha_domains and not gates.get("may_offer_lan_integrations", True):
        out.append(f"declares the Home Assistant domain(s) "
                   f"{', '.join(ha_domains)} while this build reaches nothing "
                   f"on the network")
    extra = [name for name in files if name != "data"]
    if extra:
        out.append(f"asks for the path(s) {', '.join(extra)}, and a package "
                   f"gets its own data directory and nothing else")
    return out


def baseline(posture: dict, gates: dict | None = None,
             packages: dict | None = None) -> dict:
    """Check every installed package against *posture*. Never raises.

    Returns ``{"checked": int, "violations": [{"package", "why"}],
    "evidence": str}``. A package that cannot be read is a violation of the
    "we know what is installed" kind, not a crash.
    """
    from puffpai import setup_ops
    from puffpai.wizard import loader

    gates = gates if gates is not None else setup_ops.posture_gates(posture or {})
    if packages is None:
        try:
            packages, _refusals = loader.load_builtin_packages()
        except Exception as exc:  # pragma: no cover - defensive
            return {"checked": 0, "violations": [
                {"package": "packages",
                 "why": f"this build could not read what is installed ({exc})"}],
                "evidence": "the installed packages could not be listed"}

    violations: list[dict] = []
    for package_id in sorted(packages):
        for why in _violations_for(packages[package_id], posture or {}, gates):
            violations.append({"package": package_id, "why": why})

    tier = str((posture or {}).get("run_egress") or "")
    words = _TIER_WORDS.get(tier, f"is set to '{tier or 'unset'}'")
    if violations:
        evidence = (f"{len(packages)} installed package(s) checked against a "
                    f"build that {words}; {len(violations)} disagree")
    else:
        evidence = (f"{len(packages)} installed package(s) checked against a "
                    f"build that {words}; none asks for more than that")
    return {"checked": len(packages), "violations": violations,
            "evidence": evidence}


def gaps_for(result: dict) -> list[dict]:
    """The audit's disagreements as gap records the runner can store."""
    out = []
    for row in result.get("violations") or ():
        out.append({
            "skill": "secure",
            "what": f"The '{row['package']}' step asks for more than this "
                    f"build allows.",
            "why": f"It {row['why']}.",
            "impact": "Nothing was granted -- the permission is recorded and "
                      "not enforced yet, so this is a line to read, not "
                      "something that was switched on.",
            "fix": {"kind": "command", "id": "setup.rerun", "args": {}},
        })
    return out


__all__ = ["baseline", "gaps_for"]
