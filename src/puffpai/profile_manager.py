"""
PuffPAI Profile & Identity Manager
===================================
Handles user profiles and PuffPAI AI identity persistence.

Data files (both under the configured data dir, so a scratch install started
with ``PUFFPAI_DATA_DIR`` set reads and writes its own copies):
  config/profiles.json       — user profile list
  config/puffpai_config.json — AI identity (merged into existing config)

Paths resolve per call rather than at import, for the same reason
:func:`puffpai.setup_ops._config_path` does: the data dir is an environment
decision that can be made after this module is imported.

The AI name is untrusted text — it is rendered in the UI and substituted into
the persona the model is handed — so it goes through
:func:`puffpai.first_run.sanitize_name` on the way in *and* on the way out.
That is the one sanitizer; nothing here re-implements it.
"""

import os
import json
import uuid
from datetime import datetime

_DEFAULT_IDENTITY = {
    "name": "PuffPAI",
    "avatar_style": "adventurer",
    "avatar_seed": "PuffPAI",
}


def _config_dir() -> str:
    """The config directory for this install (honours ``PUFFPAI_DATA_DIR``)."""
    from puffpai.config import get_config
    return get_config().CONFIG_DIR


def _profiles_path() -> str:
    """Absolute path of the user-profile list."""
    return os.path.join(_config_dir(), "profiles.json")


def _main_config_path() -> str:
    """Absolute path of the main config file the running app reads."""
    return os.path.join(_config_dir(), "puffpai_config.json")


def _clean_name(raw) -> str:
    """Sanitize an AI name through the single shared sanitizer."""
    from puffpai.first_run import sanitize_name
    return sanitize_name(raw)


class ProfileManager:
    """Manages user profiles and PuffPAI identity config."""

    def __init__(self):
        os.makedirs(_config_dir(), exist_ok=True)
        self._profiles = []
        self._last_active_profile = None
        self._identity = dict(_DEFAULT_IDENTITY)
        self._load()

    # ── File I/O ───────────────────────────────────────────────────────────

    def _load(self):
        """Load profiles and identity from disk."""
        # Profiles
        profiles_path = _profiles_path()
        if os.path.exists(profiles_path):
            try:
                with open(profiles_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._profiles = data.get("profiles", [])
                self._last_active_profile = data.get("last_active_profile")
            except Exception as e:
                print(f"[ProfileManager] Failed to load profiles: {e}")
                self._profiles = []

        # Identity from main config. Sanitizing on read as well as on write
        # means a hand-edited config cannot smuggle a name past us either.
        config_path = _main_config_path()
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    config = json.load(f)
                identity = config.get("identity", {})
                for key in _DEFAULT_IDENTITY:
                    if key in identity:
                        self._identity[key] = identity[key]
                clean = _clean_name(self._identity.get("name"))
                self._identity["name"] = clean or _DEFAULT_IDENTITY["name"]
            except Exception as e:
                print(f"[ProfileManager] Failed to load identity: {e}")

    def _save_profiles(self):
        """Write profiles list to profiles.json."""
        data = {
            "profiles": self._profiles,
            "last_active_profile": self._last_active_profile,
        }
        path = _profiles_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def _save_identity(self):
        """Merge the identity block into the main config the running app reads.

        Goes through :func:`puffpai.setup_ops.write_main_config` — the one
        writer for that file — so this is not a third code path: it honours
        ``PUFFPAI_DATA_DIR``, preserves every other section, and reloads the
        config singleton so the change is live without a restart.
        """
        from puffpai import setup_ops
        setup_ops.write_main_config({"identity": dict(self._identity)})

    # ── Profile CRUD ───────────────────────────────────────────────────────

    def get_all_profiles(self):
        """Return profiles sorted by last_active descending."""
        return sorted(
            self._profiles,
            key=lambda p: p.get("last_active", ""),
            reverse=True,
        )

    def get_profile(self, profile_id):
        """Look up a single profile by id. Returns dict or None."""
        for p in self._profiles:
            if p["id"] == profile_id:
                return p
        return None

    def create_profile(self, display_name, avatar_style="adventurer",
                       avatar_seed=None):
        """Create and persist a new profile. Returns the profile dict."""
        profile = {
            "id": f"usr_{uuid.uuid4().hex[:8]}",
            "display_name": display_name,
            "avatar_style": avatar_style,
            "avatar_seed": avatar_seed or display_name,
            "created": datetime.now().isoformat(),
            "last_active": datetime.now().isoformat(),
        }
        self._profiles.append(profile)
        self._save_profiles()
        return profile

    def update_profile(self, profile_id, **kwargs):
        """Update fields on an existing profile. Allowed: display_name,
        avatar_style, avatar_seed."""
        profile = self.get_profile(profile_id)
        if not profile:
            return
        allowed = {"display_name", "avatar_style", "avatar_seed"}
        for key, val in kwargs.items():
            if key in allowed:
                profile[key] = val
        self._save_profiles()

    def delete_profile(self, profile_id):
        """Remove a profile (does NOT delete its chat history)."""
        self._profiles = [p for p in self._profiles if p["id"] != profile_id]
        if self._last_active_profile == profile_id:
            self._last_active_profile = None
        self._save_profiles()

    def set_last_active(self, profile_id):
        """Mark a profile as most recently used."""
        profile = self.get_profile(profile_id)
        if profile:
            profile["last_active"] = datetime.now().isoformat()
            self._last_active_profile = profile_id
            self._save_profiles()

    def get_last_active_id(self):
        """Return the id of the most recently used profile, or None."""
        return self._last_active_profile

    # ── PuffPAI Identity ───────────────────────────────────────────────────

    def get_identity(self):
        """Return identity dict: {name, avatar_style, avatar_seed}."""
        return dict(self._identity)

    def set_identity(self, name=None, avatar_style=None, avatar_seed=None):
        """Update identity fields and persist.

        A name that sanitizes to nothing is refused rather than stored, so the
        identity block can never end up holding an empty or structural name.
        """
        if name is not None:
            clean = _clean_name(name)
            if not clean:
                raise ValueError(
                    "A name needs at least one letter or number.")
            self._identity["name"] = clean
        if avatar_style is not None:
            self._identity["avatar_style"] = avatar_style
        if avatar_seed is not None:
            self._identity["avatar_seed"] = avatar_seed
        self._save_identity()
