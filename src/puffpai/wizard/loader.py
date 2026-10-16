"""Finding packages on disk, and deciding which of them shipped with the build.

Two jobs, and the second is the security-relevant one.

**Where a package is decides what it may do.** :data:`BUILTIN_DIR` is the
``packages/`` folder inside this installed ``puffpai`` package. A folder whose
real path is a direct child of that directory is built-in; anything else is
not, whatever its manifest says. Reserved sections (spec A.2) and code loading
both hang off that finding.

**Only built-in code is ever executed.** There is no sandbox on Windows
(spec A.6) and this module deliberately does not port one, so a non-built-in
package's ``skill.py`` is never imported, never compiled and never read for
execution -- it is refused. The import that does happen uses a file path built
from a directory name this module listed and checked against
:data:`~puffpai.wizard.manifest.ID_RE`, never from a string the manifest
supplied, and the resolved path is checked for containment before it is opened.

Nothing here raises at the caller: :func:`load_builtin_packages` returns the
packages it trusts and a list of plain-language refusals for the rest, because a
bad package must not be able to take down the wizard.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

from puffpai.wizard import manifest as pkg_manifest
from puffpai.wizard.manifest import PackageError

#: The shipped packages directory: ``<install>/puffpai/wizard/packages``.
BUILTIN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "packages")


@dataclass(frozen=True)
class Package:
    """One validated package folder, and whether it shipped with this build."""

    id: str
    path: str
    manifest: dict[str, Any]
    builtin: bool
    _module_cache: dict = field(default_factory=dict, repr=False, compare=False)

    @property
    def writes(self) -> str:
        return self.manifest["writes"]

    @property
    def label(self) -> str:
        return self.manifest["label"]

    @property
    def blocking(self) -> bool:
        return bool(self.manifest.get("blocking"))

    def reads(self) -> tuple[str, ...]:
        return tuple(self.manifest.get("reads") or ())


def _is_builtin_dir(path: str) -> bool:
    """True when *path* is a direct child of the shipped packages directory."""
    try:
        real = os.path.realpath(path)
        root = os.path.realpath(BUILTIN_DIR)
    except OSError:
        return False
    return os.path.dirname(real) == root and os.path.isdir(real)


#: Python writes its bytecode cache next to any module it imports. A built-in
#: package's skill.py is imported, so this folder appears on every machine
#: that does not set PYTHONDONTWRITEBYTECODE -- refusing it would break the
#: wizard the second time it runs. Tolerated for built-in packages only: their
#: code already shipped with the build, and nothing else is ever executed.
_BYTECODE_DIR = "__pycache__"


def _check_shape(path: str, package_id: str, builtin: bool = False) -> None:
    """Regular files only, no symlinks, no subdirectory except ``data/``
    (and, for a built-in package, Python's own ``__pycache__``)."""
    for name in sorted(os.listdir(path)):
        full = os.path.join(path, name)
        if (builtin and name == _BYTECODE_DIR and os.path.isdir(full)
                and not os.path.islink(full)):
            continue
        if os.path.islink(full):
            raise PackageError(
                f"Package '{package_id}': '{name}' is a symlink. A package is "
                f"regular files only -- a link is a way to have the card show "
                f"one thing and the runner read another.")
        if os.path.isdir(full):
            if name not in pkg_manifest.ALLOWED_SUBDIRS:
                raise PackageError(
                    f"Package '{package_id}': '{name}/' is not an allowed "
                    f"directory; the only one is 'data/'.")
            continue
        if not os.path.isfile(full):
            raise PackageError(
                f"Package '{package_id}': '{name}' is not a regular file.")
        if name not in pkg_manifest.PACKAGE_FILES:
            raise PackageError(
                f"Package '{package_id}': '{name}' is not part of a package "
                f"({', '.join(pkg_manifest.PACKAGE_FILES)}).")
    for required in pkg_manifest.REQUIRED_PACKAGE_FILES:
        if not os.path.isfile(os.path.join(path, required)):
            raise PackageError(
                f"Package '{package_id}': the folder has no {required}.")


def load_package(path: str) -> Package:
    """Validate one package folder. Raises :class:`PackageError` with a reason."""
    if not os.path.isdir(path):
        raise PackageError(f"{path} is not a package folder.")
    folder = os.path.basename(os.path.normpath(path))
    if not pkg_manifest.ID_RE.match(folder):
        raise PackageError(
            f"'{folder}' is not usable as a package folder name: lowercase "
            f"letters, digits and underscore, starting with a letter.")

    builtin = _is_builtin_dir(path)
    _check_shape(path, folder, builtin=builtin)

    manifest_path = os.path.join(path, "manifest.json")
    if os.path.getsize(manifest_path) > pkg_manifest.MAX_MANIFEST_BYTES:
        raise PackageError(
            f"Package '{folder}': manifest.json is too large to be a manifest.")
    try:
        with open(manifest_path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError) as exc:
        raise PackageError(
            f"Package '{folder}': manifest.json will not read: {exc}") from exc

    data = pkg_manifest.validate(raw, builtin=builtin)
    if data["id"] != folder:
        raise PackageError(
            f"The folder '{folder}' holds a manifest calling itself "
            f"'{data['id']}'. A package is found by its folder, so the two "
            f"cannot disagree.")
    return Package(id=data["id"], path=os.path.realpath(path), manifest=data,
                   builtin=builtin)


def load_builtin_packages(root: str | None = None) -> tuple[dict[str, Package], list[str]]:
    """Every package under the shipped directory: ``(packages, refusals)``.

    Never raises. A folder that will not validate is left out and named in
    *refusals*, in words the owner could read, because a wizard that dies on a
    bad manifest is worse than a wizard missing one step.
    """
    base = root or BUILTIN_DIR
    packages: dict[str, Package] = {}
    refusals: list[str] = []
    try:
        names = sorted(os.listdir(base))
    except OSError as exc:
        return {}, [f"The packages directory could not be read: {exc}"]
    for name in names:
        full = os.path.join(base, name)
        if not os.path.isdir(full) or name.startswith((".", "_")):
            continue
        try:
            package = load_package(full)
        except PackageError as exc:
            refusals.append(str(exc))
            continue
        except Exception as exc:  # pragma: no cover - defensive
            refusals.append(f"Package '{name}' could not be read: {exc}")
            continue
        if package.id in packages:
            refusals.append(
                f"Two packages claim the id '{package.id}'; neither is used.")
            packages.pop(package.id, None)
            continue
        packages[package.id] = package
    return packages, refusals


def load_module(package: Package):
    """Import a built-in package's ``skill.py``. Refuses anything else.

    The path is rebuilt from :data:`BUILTIN_DIR` and the package id (which
    matched :data:`~puffpai.wizard.manifest.ID_RE` before this point), then
    checked for containment. No string out of a manifest reaches the loader.
    """
    if not package.builtin:
        raise PackageError(
            f"Package '{package.id}' did not ship with this build, so its code "
            f"is not run. This machine has no sandbox to run it in, and a "
            f"package that cannot be confined is a package that does not "
            f"execute.")
    if not pkg_manifest.ID_RE.match(package.id):  # pragma: no cover - belt and braces
        raise PackageError(f"Package id {package.id!r} is not usable.")

    cached = package._module_cache.get("skill")
    if cached is not None:
        return cached

    root = os.path.realpath(BUILTIN_DIR)
    code_path = os.path.realpath(os.path.join(root, package.id, "skill.py"))
    if os.path.dirname(os.path.dirname(code_path)) != root:
        raise PackageError(
            f"Package '{package.id}': its code does not sit inside the shipped "
            f"packages directory.")
    if os.path.islink(code_path) or not os.path.isfile(code_path):
        raise PackageError(
            f"Package '{package.id}' has no skill.py to run.")

    import importlib.util

    spec = importlib.util.spec_from_file_location(
        f"puffpai.wizard.packages.{package.id}.skill", code_path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise PackageError(f"Package '{package.id}': skill.py will not load.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    package._module_cache["skill"] = module
    return module


__all__ = ["BUILTIN_DIR", "Package", "PackageError", "load_builtin_packages",
           "load_module", "load_package"]
