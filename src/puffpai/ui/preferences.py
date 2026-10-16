"""Persistent user preferences for PuffPAI UI.

Saves and loads UI state (window geometry, theme, sidebar width,
last active profile, etc.) to a JSON file in the user's app data
directory so that every launch restores the previous session.

Usage:
    from puffpai.ui.preferences import UIPreferences

    prefs = UIPreferences()          # loads from disk (or defaults)
    prefs.dark_mode = True
    prefs.window_geometry = "1200x800+100+50"
    prefs.save()                     # writes to disk

Storage location:
    Windows:  %LOCALAPPDATA%/PuffPAI/ui_preferences.json
    Linux:    ~/.local/share/puffpai/ui_preferences.json
    macOS:    ~/Library/Application Support/PuffPAI/ui_preferences.json

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass, field, asdict
from typing import Any

logger = logging.getLogger(__name__)


def _get_app_data_dir() -> str:
    """Return the platform-appropriate persistent data directory."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
        return os.path.join(base, "PuffPAI")
    elif sys.platform == "darwin":
        return os.path.join(
            os.path.expanduser("~"), "Library", "Application Support", "PuffPAI")
    else:
        xdg = os.environ.get("XDG_DATA_HOME", os.path.join(os.path.expanduser("~"), ".local", "share"))
        return os.path.join(xdg, "puffpai")


@dataclass
class UIPreferences:
    """Persistent UI preferences — loaded once at startup, saved on change."""

    # Window
    window_geometry: str = "1200x800"
    window_maximized: bool = False
    sidebar_width: int = 270

    # Theme
    dark_mode: bool = False
    font_size: int = 10
    accent_color: str = "#00B4D8"

    # Session
    last_profile: str = ""
    last_chat_id: str = ""
    last_view: str = "dashboard"  # "dashboard" | "chat"

    # Voice
    voice_enabled_default: bool = False
    voice_input_device: str = ""
    voice_output_device: str = ""

    # Notifications
    show_notifications: bool = True
    notification_sound: bool = True

    # System tray
    minimize_to_tray: bool = True
    start_minimized: bool = False

    # Advanced
    ollama_url: str = ""
    ollama_model: str = ""
    ollama_temperature: float = 0.7
    max_context_tokens: int = 120000

    # Internal
    _file_path: str = field(default="", repr=False)

    def __post_init__(self):
        if not self._file_path:
            data_dir = _get_app_data_dir()
            os.makedirs(data_dir, exist_ok=True)
            self._file_path = os.path.join(data_dir, "ui_preferences.json")

    # ── Persistence ───────────────────────────────────────────────────

    @classmethod
    def load(cls, path: str = "") -> UIPreferences:
        """Load preferences from disk, falling back to defaults."""
        prefs = cls(_file_path=path)
        if os.path.isfile(prefs._file_path):
            try:
                with open(prefs._file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for key, value in data.items():
                    if key.startswith("_"):
                        continue
                    if hasattr(prefs, key):
                        setattr(prefs, key, value)
                logger.info("Loaded UI preferences from %s", prefs._file_path)
            except Exception as e:
                logger.warning("Failed to load preferences: %s", e)
        else:
            logger.info("No preferences file found — using defaults")
        return prefs

    def save(self) -> None:
        """Write current preferences to disk."""
        try:
            data = {
                k: v for k, v in asdict(self).items()
                if not k.startswith("_")
            }
            os.makedirs(os.path.dirname(self._file_path), exist_ok=True)
            tmp = self._file_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            # Atomic rename (Windows: replace if exists)
            if os.path.exists(self._file_path):
                os.replace(tmp, self._file_path)
            else:
                os.rename(tmp, self._file_path)
            logger.debug("Saved UI preferences to %s", self._file_path)
        except Exception as e:
            logger.warning("Failed to save preferences: %s", e)

    def update(self, **kwargs) -> None:
        """Update one or more fields and auto-save."""
        for key, value in kwargs.items():
            if hasattr(self, key) and not key.startswith("_"):
                setattr(self, key, value)
        self.save()

    def reset(self) -> None:
        """Reset all preferences to defaults and save."""
        defaults = UIPreferences(_file_path=self._file_path)
        for key in asdict(defaults):
            if not key.startswith("_"):
                setattr(self, key, getattr(defaults, key))
        self.save()

    # ── Convenience ───────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """Return preferences as a plain dict (no private fields)."""
        return {k: v for k, v in asdict(self).items() if not k.startswith("_")}

    @property
    def data_dir(self) -> str:
        """Return the app data directory (parent of the prefs file)."""
        return os.path.dirname(self._file_path)
