"""
PuffPAI ZIM Discovery Module

Scans configured directories and explicit paths for ZIM files.
Extracts metadata from ZIM headers for inventory management.

Usage:
    from puffpai.knowledge.discovery import discover_zim_files, get_zim_metadata

    zim_paths = discover_zim_files(zim_dirs=["F:\\\\"])
    for p in zim_paths:
        meta = get_zim_metadata(p)
        print(meta["filename"], meta["entry_count"])

    # Or use config directly:
    from puffpai.knowledge.discovery import discover_from_config
    zim_paths = discover_from_config()

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import logging
import os
import struct
from typing import Any

logger = logging.getLogger(__name__)

# ZIM magic number (0x44D495A)
_ZIM_MAGIC = 72173914


def discover_zim_files(
    zim_dirs: list[str] | None = None,
    zim_files: list[str] | None = None,
) -> list[str]:
    """Scan directories and explicit paths for ZIM files.

    Args:
        zim_dirs: Directories to scan (non-recursive, first level only).
        zim_files: Explicit ZIM file paths to include.

    Returns:
        Sorted list of absolute paths to valid ZIM files (deduplicated).
    """
    found: set[str] = set()

    # Add explicit file paths
    for path in (zim_files or []):
        abspath = os.path.abspath(path)
        if os.path.isfile(abspath) and _looks_like_zim(abspath):
            found.add(abspath)
        else:
            logger.debug("ZIM path not found or invalid: %s", path)

    # Scan directories
    for dirpath in (zim_dirs or []):
        if not os.path.isdir(dirpath):
            logger.debug("ZIM directory not found: %s", dirpath)
            continue

        try:
            for entry in os.scandir(dirpath):
                if entry.is_file() and entry.name.lower().endswith('.zim'):
                    abspath = os.path.abspath(entry.path)
                    if _looks_like_zim(abspath):
                        found.add(abspath)
        except PermissionError:
            logger.warning("Permission denied scanning: %s", dirpath)
        except OSError as exc:
            logger.warning("Error scanning %s: %s", dirpath, exc)

    return sorted(found)


def _looks_like_zim(filepath: str) -> bool:
    """Quick-check that a file starts with the ZIM magic number.

    Reads only the first 4 bytes, so it's safe for large files
    and removable media.
    """
    try:
        with open(filepath, 'rb') as fh:
            magic_bytes = fh.read(4)
        if len(magic_bytes) < 4:
            return False
        magic = struct.unpack('<I', magic_bytes)[0]
        return magic == _ZIM_MAGIC
    except (OSError, PermissionError):
        return False


def get_zim_metadata(zim_path: str) -> dict[str, Any]:
    """Extract header metadata from a ZIM file without fully parsing it.

    Reads only the 80-byte header. Suitable for large ZIM files
    on removable media.

    Args:
        zim_path: Path to a ZIM file.

    Returns:
        Dictionary with: filename, filepath, entry_count, cluster_count,
        major_version, minor_version, uuid_hex, file_size_bytes.

    Raises:
        FileNotFoundError: If the file doesn't exist.
        ValueError: If the file is not a valid ZIM archive.
    """
    if not os.path.isfile(zim_path):
        raise FileNotFoundError(f"ZIM file not found: {zim_path}")

    with open(zim_path, 'rb') as fh:
        data = fh.read(80)

    if len(data) < 80:
        raise ValueError(f"File too small to be a valid ZIM archive: {zim_path}")

    magic, major, minor = struct.unpack_from('<IHH', data, 0)
    if magic != _ZIM_MAGIC:
        raise ValueError(
            f"Not a ZIM file (magic={magic:#x}, expected {_ZIM_MAGIC:#x})")

    uuid = data[8:24]
    entry_count, cluster_count = struct.unpack_from('<II', data, 24)

    return {
        "filename": os.path.basename(zim_path),
        "filepath": os.path.abspath(zim_path),
        "entry_count": entry_count,
        "cluster_count": cluster_count,
        "major_version": major,
        "minor_version": minor,
        "uuid_hex": uuid.hex(),
        "file_size_bytes": os.path.getsize(zim_path),
    }


def discover_from_config() -> list[str]:
    """Discover ZIM files using paths from centralized config.

    Reads ``get_config().paths.zim_dirs`` and ``get_config().paths.zim_files``
    and delegates to :func:`discover_zim_files`.

    Returns:
        Sorted list of absolute paths to discovered ZIM files.
    """
    try:
        from puffpai.config import get_config
        cfg = get_config()
        return discover_zim_files(
            zim_dirs=cfg.paths.zim_dirs,
            zim_files=cfg.paths.zim_files,
        )
    except Exception as exc:
        logger.warning("Could not load config for ZIM discovery: %s", exc)
        return []
