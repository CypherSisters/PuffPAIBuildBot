"""
PuffPAI Media Server Bridge
=============================
Single source of truth for all media server communication.

Every component (API server, tools, MCP server, dashboard) should
go through this module instead of making direct Jellyfin/Calibre calls.

Usage:
    from puffpai.media_bridge import get_media_bridge

    bridge = get_media_bridge()
    bridge.search_music("miles davis")
    bridge.play_music(item_id, session_id)
    bridge.now_playing()
    bridge.import_ipod()

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import sqlite3
import subprocess

from puffpai import procflags
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import requests

from puffpai.config import get_config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class MediaItem:
    """Normalised representation of a media item (track, movie, or book)."""
    item_id: str
    title: str
    media_type: str  # "music", "movie", "book"
    artist: str = ""
    album: str = ""
    genre: str = ""
    year: int = 0
    duration_seconds: int = 0
    path: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def display(self) -> str:
        if self.media_type == "music":
            parts = [self.title]
            if self.artist:
                parts.append(f"by {self.artist}")
            if self.album:
                parts.append(f"on {self.album}")
            return " ".join(parts)
        elif self.media_type == "movie":
            yr = f" ({self.year})" if self.year else ""
            return f"{self.title}{yr}"
        else:
            parts = [self.title]
            if self.artist:
                parts.append(f"by {self.artist}")
            return " ".join(parts)


@dataclass
class PlaybackSession:
    """Active playback info from Jellyfin."""
    session_id: str
    client_name: str
    device_name: str
    user_name: str
    now_playing: MediaItem | None = None
    is_paused: bool = False
    position_seconds: int = 0
    volume_level: int | None = None


@dataclass
class ImportResult:
    """Result of an iPod import operation."""
    success: bool
    tracks_imported: int = 0
    tracks_skipped: int = 0
    drm_skipped: int = 0
    errors: list[str] = field(default_factory=list)
    message: str = ""


class MediaBridgeError(Exception):
    """Raised when a media server operation fails."""


# ---------------------------------------------------------------------------
# State cache (thread-safe, TTL-based)
# ---------------------------------------------------------------------------

class _StateCache:
    """Simple thread-safe TTL cache for Jellyfin state."""

    def __init__(self, ttl: float = 5.0):
        self._ttl = ttl
        self._lock = threading.Lock()
        self._data: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            ts, val = entry
            if time.time() - ts > self._ttl:
                del self._data[key]
                return None
            return val

    def put(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = (time.time(), value)

    def invalidate(self, key: str | None = None) -> None:
        with self._lock:
            if key:
                self._data.pop(key, None)
            else:
                self._data.clear()


# ---------------------------------------------------------------------------
# Main bridge class
# ---------------------------------------------------------------------------

class MediaBridge:
    """Centralised bridge to Jellyfin + Calibre-Web media servers."""

    def __init__(self):
        cfg = get_config()
        self._jellyfin_url = cfg.media_server.jellyfin_url.rstrip("/")
        # Read now: the plain copy if one is still in the settings, else the
        # vault (its locked copy while the vault is locked).
        self._jellyfin_api_key = cfg.media_server.jellyfin_api_key
        if not self._jellyfin_api_key and cfg.media_server.enabled:
            from puffpai.security import keys
            self._jellyfin_api_key = keys.house_key("jellyfin") or ""
        self._calibre_db = cfg.media_server.calibre_db_path
        self._calibre_web_url = cfg.media_server.calibre_web_url.rstrip("/")
        self._media_mount = cfg.media_server.media_mount
        self._timeout = cfg.media_server.timeout
        self._enabled = cfg.media_server.enabled
        self._user_id = ""  # lazily resolved admin user for playlist/favorite ops
        self._cache = _StateCache(ttl=5.0)
        self._session = requests.Session()  # lan-egress: Jellyfin/Calibre session on the configured LAN url
        # Marked home-network calls never use a proxy (security.lan_guard).
        self._session.trust_env = False
        self._session.headers.update({
            "X-Emby-Authorization": (
                f'MediaBrowser Client="PuffPAI", Device="PuffPAI-Server", '
                f'DeviceId="puffpai-bridge", Version="1.0.0", '
                f'Token="{self._jellyfin_api_key}"'
            ),
        })

    def reload_key(self) -> None:
        """Read the Jellyfin key again (it was sealed again or changed), in
        place, so every holder of this bridge uses it at once. Never raises,
        never logs the key."""
        try:
            cfg = get_config()
            key = cfg.media_server.jellyfin_api_key
            if not key and cfg.media_server.enabled:
                from puffpai.security import keys
                key = keys.house_key("jellyfin") or ""
            self._jellyfin_api_key = key
            self._session.headers["X-Emby-Authorization"] = (
                f'MediaBrowser Client="PuffPAI", Device="PuffPAI-Server", '
                f'DeviceId="puffpai-bridge", Version="1.0.0", '
                f'Token="{key}"')
            self._cache.invalidate()
        except Exception as exc:  # noqa: BLE001
            logger.debug("Jellyfin key not reloaded: %s", type(exc).__name__)

    # --- Low-level Jellyfin HTTP ---

    def _jf_request(
        self, path: str, method: str = "GET",
        params: dict | None = None, data: dict | None = None,
    ) -> Any:
        """Make an authenticated Jellyfin REST API call."""
        if not self._enabled:
            raise MediaBridgeError("Media server integration is disabled. Set enabled=true in media_server_config.json")
        url = f"{self._jellyfin_url}{path}"
        try:
            resp = self._session.request(  # lan-egress: Jellyfin on the configured url; lan_guard refuses an internet address
                method, url, params=params, json=data, timeout=self._timeout,
            )
            resp.raise_for_status()
            if resp.content:
                return resp.json()
            return {}
        except requests.ConnectionError:
            raise MediaBridgeError(
                f"Cannot reach Jellyfin at {self._jellyfin_url}. "
                "Is the media server online and Jellyfin running?"
            )
        except requests.HTTPError as exc:
            raise MediaBridgeError(f"Jellyfin HTTP error: {exc}")
        except Exception as exc:
            raise MediaBridgeError(f"Jellyfin request failed: {exc}")

    # --- Connection check ---

    def is_connected(self) -> bool:
        """Check if Jellyfin is reachable."""
        if not self._enabled:
            return False
        try:
            self._jf_request("/System/Info/Public")
            return True
        except MediaBridgeError:
            return False

    def get_status(self) -> dict[str, Any]:
        """Return connection status and basic server info."""
        result = {
            "enabled": self._enabled,
            "jellyfin_url": self._jellyfin_url,
            "calibre_web_url": self._calibre_web_url,
            "connected": False,
            "server_name": "",
            "version": "",
        }
        if not self._enabled:
            return result
        try:
            info = self._jf_request("/System/Info/Public")
            result["connected"] = True
            result["server_name"] = info.get("ServerName", "")
            result["version"] = info.get("Version", "")
        except MediaBridgeError:
            pass
        return result

    # --- Music search ---

    def search_music(
        self, query: str = "", *, artist: str = "", album: str = "",
        genre: str = "", limit: int = 20,
    ) -> list[MediaItem]:
        """Search the Jellyfin music library."""
        cached = self._cache.get(f"music:{query}:{artist}:{album}:{genre}")
        if cached is not None:
            return cached

        params: dict[str, Any] = {
            "IncludeItemTypes": "Audio",
            "Recursive": "true",
            "Limit": limit,
            "Fields": "Path,Genres,Studios,Overview",
        }
        if query:
            params["SearchTerm"] = query
        if artist:
            params["ArtistIds"] = self._resolve_artist_id(artist)
        if album:
            params["SearchTerm"] = params.get("SearchTerm", "") + f" {album}"
        if genre:
            params["Genres"] = genre

        try:
            data = self._jf_request("/Items", params=params)
        except MediaBridgeError:
            return []

        items = [self._jf_audio_to_item(i) for i in data.get("Items", [])]
        self._cache.put(f"music:{query}:{artist}:{album}:{genre}", items)
        return items

    def _resolve_artist_id(self, artist_name: str) -> str:
        """Look up a Jellyfin artist ID by name."""
        try:
            data = self._jf_request("/Artists", params={
                "SearchTerm": artist_name, "Limit": 1,
            })
            items = data.get("Items", [])
            if items:
                return items[0]["Id"]
        except MediaBridgeError:
            pass
        return ""

    def _jf_audio_to_item(self, raw: dict) -> MediaItem:
        """Convert a Jellyfin Audio item to MediaItem."""
        artists = raw.get("Artists", [])
        return MediaItem(
            item_id=raw.get("Id", ""),
            title=raw.get("Name", "Unknown"),
            media_type="music",
            artist=artists[0] if artists else raw.get("AlbumArtist", ""),
            album=raw.get("Album", ""),
            genre=", ".join(raw.get("Genres", [])),
            year=raw.get("ProductionYear", 0) or 0,
            duration_seconds=int(raw.get("RunTimeTicks", 0) / 10_000_000),
            path=raw.get("Path", ""),
        )

    # --- Movie search ---

    def search_movies(
        self, query: str = "", *, genre: str = "", year: int = 0,
        limit: int = 20,
    ) -> list[MediaItem]:
        """Search the Jellyfin movie library."""
        params: dict[str, Any] = {
            "IncludeItemTypes": "Movie",
            "Recursive": "true",
            "Limit": limit,
            "Fields": "Path,Genres,Overview",
        }
        if query:
            params["SearchTerm"] = query
        if genre:
            params["Genres"] = genre
        if year:
            params["Years"] = str(year)

        try:
            data = self._jf_request("/Items", params=params)
        except MediaBridgeError:
            return []

        return [self._jf_movie_to_item(i) for i in data.get("Items", [])]

    def _jf_movie_to_item(self, raw: dict) -> MediaItem:
        """Convert a Jellyfin Movie item to MediaItem."""
        return MediaItem(
            item_id=raw.get("Id", ""),
            title=raw.get("Name", "Unknown"),
            media_type="movie",
            genre=", ".join(raw.get("Genres", [])),
            year=raw.get("ProductionYear", 0) or 0,
            duration_seconds=int(raw.get("RunTimeTicks", 0) / 10_000_000),
            path=raw.get("Path", ""),
            extra={"overview": raw.get("Overview", "")},
        )

    # --- Book search (Calibre SQLite) ---

    def search_books(
        self, query: str = "", *, author: str = "", subject: str = "",
        limit: int = 20,
    ) -> list[MediaItem]:
        """Search the Calibre book database directly via SQLite."""
        if not os.path.isfile(self._calibre_db):
            logger.warning("Calibre DB not found at %s", self._calibre_db)
            return []

        try:
            conn = sqlite3.connect(f"file:{self._calibre_db}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            # Build query with JOIN on authors
            sql = """
                SELECT b.id, b.title, b.path,
                       GROUP_CONCAT(a.name, ', ') AS authors
                FROM books b
                LEFT JOIN books_authors_link bal ON b.id = bal.book
                LEFT JOIN authors a ON bal.author = a.id
                WHERE 1=1
            """
            params_list: list[str] = []

            if query:
                sql += " AND b.title LIKE ?"
                params_list.append(f"%{query}%")
            if author:
                sql += " AND a.name LIKE ?"
                params_list.append(f"%{author}%")

            sql += f" GROUP BY b.id ORDER BY b.timestamp DESC LIMIT {limit}"

            cur.execute(sql, params_list)
            rows = cur.fetchall()
            conn.close()

            return [
                MediaItem(
                    item_id=str(row["id"]),
                    title=row["title"],
                    media_type="book",
                    artist=row["authors"] or "",
                    path=row["path"] or "",
                )
                for row in rows
            ]
        except Exception as exc:
            logger.error("Calibre search error: %s", exc)
            return []

    # --- Playback control ---

    def play_music(self, item_id: str, session_id: str = "") -> str:
        """Start playback of a music item on a Jellyfin session.

        If session_id is empty, plays on the first active session.
        Returns a status message.
        """
        if not session_id:
            session_id = self._pick_session()

        # Jellyfin takes these as query params, not a JSON body: a body-only
        # request is answered with 400 (checked live).
        self._jf_request(
            f"/Sessions/{session_id}/Playing",
            method="POST",
            params={"ItemIds": item_id, "PlayCommand": "PlayNow"},
        )
        self._cache.invalidate()
        return f"Playing on session {session_id}"

    def _pick_session(self) -> str:
        """Most recently active *controllable* Jellyfin session, or a clear
        error if none exist. API-client sessions (SupportsRemoteControl=false,
        e.g. this backend's own connection) can never play anything, so they
        must not be picked even when they sort first."""
        sessions = self._get_sessions()
        controllable = [s for s in sessions if s.get("SupportsRemoteControl")]
        if not controllable:
            raise MediaBridgeError(
                "No active Jellyfin player to play on. Open the Media page on "
                "the dashboard (or any Jellyfin app) first, then ask again."
            )
        controllable.sort(key=lambda s: s.get("LastActivityDate") or "", reverse=True)
        return controllable[0]["Id"]

    def play_playlist(self, name_or_id: str, session_id: str = "", shuffle: bool = False) -> str:
        """Queue an entire playlist (PlayNow) on a Jellyfin session."""
        pl = self.resolve_playlist(name_or_id)
        tracks = self.playlist_tracks(pl.item_id, limit=700)
        if not tracks:
            raise MediaBridgeError(f"Playlist '{pl.title}' is empty.")
        if not session_id:
            session_id = self._pick_session()
        # Shuffle client-side (before the cap, so the 200-track queue samples
        # the whole playlist instead of always the first 200).
        if shuffle:
            tracks = tracks[:]
            random.shuffle(tracks)
        # Query params, not JSON body (see play_music). Track IDs are
        # comma-joined; cap the count so the URL stays under the server's
        # request-line limit (~8 KB ≈ 230 ids).
        queue = tracks[:200]
        self._jf_request(
            f"/Sessions/{session_id}/Playing",
            method="POST",
            params={"ItemIds": ",".join(t.item_id for t in queue),
                    "PlayCommand": "PlayNow"},
        )
        self._cache.invalidate()
        mode = ", shuffled" if shuffle else ""
        return f"Playing playlist '{pl.title}' ({len(queue)} of {len(tracks)} tracks{mode})"

    # Spoken/LLM synonyms for the canonical playback actions.
    _ACTION_ALIASES = {
        "play": "resume", "unpause": "resume", "start": "resume",
        "continue": "resume", "skip": "next", "back": "previous",
        "prev": "previous",
    }

    def playback_control(self, session_id: str, action: str) -> str:
        """Control playback on a session. Actions: pause, resume, stop, next,
        previous (plus aliases like play/unpause/skip). Empty session_id picks
        the active controllable session automatically."""
        command_map = {
            "pause": "/Sessions/{sid}/Playing/Pause",
            "resume": "/Sessions/{sid}/Playing/Unpause",
            "stop": "/Sessions/{sid}/Playing/Stop",
            "next": "/Sessions/{sid}/Playing/NextTrack",
            "previous": "/Sessions/{sid}/Playing/PreviousTrack",
        }
        action = action.lower().strip()
        action = self._ACTION_ALIASES.get(action, action)
        path_template = command_map.get(action)
        if not path_template:
            raise MediaBridgeError(
                f"Unknown action '{action}'. Use: pause, resume, stop, next, previous"
            )
        if not session_id:
            session_id = self._pick_session()

        self._jf_request(path_template.format(sid=session_id), method="POST")
        self._cache.invalidate()
        return f"Executed {action} on session {session_id}"

    def set_volume(self, session_id: str, volume: int) -> str:
        """Set volume (0-100) on a Jellyfin session."""
        volume = max(0, min(100, volume))
        self._jf_request(
            f"/Sessions/{session_id}/Command",
            method="POST",
            data={"Name": "SetVolume", "Arguments": {"Volume": str(volume)}},
        )
        return f"Volume set to {volume} on session {session_id}"

    # --- Now playing ---

    def now_playing(self) -> list[PlaybackSession]:
        """Return all active playback sessions."""
        sessions = self._get_sessions()
        result = []
        for s in sessions:
            np = s.get("NowPlayingItem")
            now = None
            if np:
                media_type = "music" if np.get("Type") == "Audio" else "movie"
                now = MediaItem(
                    item_id=np.get("Id", ""),
                    title=np.get("Name", ""),
                    media_type=media_type,
                    artist=", ".join(np.get("Artists", [])) if np.get("Artists") else "",
                    album=np.get("Album", ""),
                    duration_seconds=int((np.get("RunTimeTicks", 0) or 0) / 10_000_000),
                    extra={"album_id": np.get("AlbumId", "")},
                )

            play_state = s.get("PlayState", {})
            pos_ticks = play_state.get("PositionTicks", 0) or 0

            result.append(PlaybackSession(
                session_id=s.get("Id", ""),
                client_name=s.get("Client", ""),
                device_name=s.get("DeviceName", ""),
                user_name=s.get("UserName", ""),
                now_playing=now,
                is_paused=play_state.get("IsPaused", False),
                position_seconds=int(pos_ticks / 10_000_000),
                volume_level=play_state.get("VolumeLevel"),
            ))
        return result

    def _get_sessions(self) -> list[dict]:
        """Get active Jellyfin sessions (with NowPlayingItem or controllable)."""
        cached = self._cache.get("sessions")
        if cached is not None:
            return cached
        try:
            sessions = self._jf_request("/Sessions", params={
                "ControllableByUserId": "",
            })
            if isinstance(sessions, list):
                self._cache.put("sessions", sessions)
                return sessions
        except MediaBridgeError:
            pass
        return []

    # --- User resolution ---

    def _get_user_id(self) -> str:
        """Resolve the Jellyfin user ID used to scope playlist ops.

        API keys are not tied to a user, but the playlist endpoints
        require one. Prefers the first administrator account.
        """
        if self._user_id:
            return self._user_id
        data = self._jf_request("/Users")
        if isinstance(data, list) and data:
            admins = [u for u in data if u.get("Policy", {}).get("IsAdministrator")]
            self._user_id = (admins[0] if admins else data[0]).get("Id", "")
        if not self._user_id:
            raise MediaBridgeError("No Jellyfin users found for playlist operations.")
        return self._user_id

    # --- Playlists ---

    def list_playlists(self) -> list[MediaItem]:
        """Return all playlists with track counts."""
        cached = self._cache.get("playlists")
        if cached is not None:
            return cached
        data = self._jf_request("/Items", params={
            "IncludeItemTypes": "Playlist",
            "Recursive": "true",
            "UserId": self._get_user_id(),
            "Fields": "ChildCount",
        })
        items = [
            MediaItem(
                item_id=raw.get("Id", ""),
                title=raw.get("Name", "Unknown"),
                media_type="playlist",
                extra={"track_count": raw.get("ChildCount", 0) or 0},
            )
            for raw in data.get("Items", [])
        ]
        self._cache.put("playlists", items)
        return items

    @staticmethod
    def _fuzzy_key(text: str) -> str:
        """Normalize a name for forgiving comparison: lowercase, drop
        possessive 's and punctuation, collapse whitespace, and singularize
        tokens — so a spoken \"morning's mix\" matches both
        \"Morning Mix\" and \"Mornings Mix\"."""
        text = re.sub(r"'s\b", "", text.lower())
        text = re.sub(r"[^a-z0-9]+", " ", text)
        tokens = [
            t[:-1] if len(t) > 3 and t.endswith("s") and not t.endswith("ss") else t
            for t in text.split()
        ]
        return " ".join(tokens)

    @staticmethod
    def _prefer_nonempty(cands: list[MediaItem]) -> list[MediaItem]:
        """Among matching playlists, drop empty ones if any have tracks
        (empty duplicates left over from failed imports shadow real ones)."""
        nonempty = [p for p in cands if (p.extra.get("track_count") or 0) > 0]
        return nonempty or cands

    def resolve_playlist(self, name_or_id: str) -> MediaItem:
        """Find a playlist by ID, ID prefix, or (partial/fuzzy) name."""
        needle = name_or_id.strip()
        if not needle:
            raise MediaBridgeError("Empty playlist name.")
        playlists = self.list_playlists()
        for p in playlists:
            if p.item_id == needle or (len(needle) >= 8 and p.item_id.startswith(needle)):
                return p
        low = needle.lower()
        exact = self._prefer_nonempty([p for p in playlists if p.title.lower() == low])
        if exact:
            return exact[0]
        fuzzy_needle = self._fuzzy_key(needle)
        if fuzzy_needle:
            fuzzy_exact = self._prefer_nonempty(
                [p for p in playlists if self._fuzzy_key(p.title) == fuzzy_needle]
            )
            if len(fuzzy_exact) == 1:
                return fuzzy_exact[0]
            if fuzzy_exact:
                names = ", ".join(p.title for p in fuzzy_exact[:8])
                raise MediaBridgeError(f"Multiple playlists match '{needle}': {names}")
        partial = [p for p in playlists if low in p.title.lower()]
        if not partial and fuzzy_needle:
            # Fuzzy substring, then all-words-present (voice transcripts often
            # reorder or drop punctuation: "mix morning" etc.)
            partial = [p for p in playlists if fuzzy_needle in self._fuzzy_key(p.title)]
            if not partial:
                tokens = set(fuzzy_needle.split())
                partial = [p for p in playlists if tokens <= set(self._fuzzy_key(p.title).split())]
        partial = self._prefer_nonempty(partial)
        if len(partial) == 1:
            return partial[0]
        if partial:
            names = ", ".join(p.title for p in partial[:8])
            raise MediaBridgeError(f"Multiple playlists match '{needle}': {names}")
        names = ", ".join(p.title for p in playlists[:15])
        raise MediaBridgeError(f"No playlist matching '{needle}'. Available: {names}")

    def playlist_tracks(self, name_or_id: str, limit: int = 200) -> list[MediaItem]:
        """Return the tracks of a playlist (entry IDs kept in extra['entry_id'])."""
        pl = self.resolve_playlist(name_or_id)
        data = self._jf_request(f"/Playlists/{pl.item_id}/Items", params={
            "UserId": self._get_user_id(),
            "Limit": limit,
        })
        items = []
        for raw in data.get("Items", []):
            item = self._jf_audio_to_item(raw)
            item.extra["entry_id"] = raw.get("PlaylistItemId", "")
            items.append(item)
        return items

    # --- Library management ---

    def library_stats(self) -> dict[str, Any]:
        """Get counts of music, movies, and books."""
        stats: dict[str, Any] = {
            "music_tracks": 0, "music_albums": 0, "music_artists": 0,
            "movies": 0, "books": 0,
        }

        try:
            # Music tracks
            data = self._jf_request("/Items/Counts")
            stats["music_tracks"] = data.get("SongCount", 0)
            stats["music_albums"] = data.get("AlbumCount", 0)
            stats["music_artists"] = data.get("ArtistCount", 0)
            stats["movies"] = data.get("MovieCount", 0)
        except MediaBridgeError:
            pass

        # Books from Calibre
        if os.path.isfile(self._calibre_db):
            try:
                conn = sqlite3.connect(f"file:{self._calibre_db}?mode=ro", uri=True)
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM books")
                stats["books"] = cur.fetchone()[0]
                conn.close()
            except Exception:
                pass

        return stats

    def trigger_scan(self, library: str = "") -> str:
        """Trigger a Jellyfin library rescan."""
        self._jf_request("/Library/Refresh", method="POST")
        self._cache.invalidate()
        return "Library scan triggered"

    # --- iPod import ---

    def import_ipod(self, dry_run: bool = False) -> ImportResult:
        """Import music from a connected iPod.

        This calls the ipod_import.py script on the media server host (or
        locally if the bridge runs on that host itself).
        """
        cfg = get_config()
        script_path = os.path.join(cfg.REPO_ROOT, "scripts", "ipod_import.py")

        if not os.path.isfile(script_path):
            return ImportResult(
                success=False,
                message="ipod_import.py not found. Deploy scripts to the media server host first.",
            )

        cmd = ["python3", script_path, "--json"]
        if dry_run:
            cmd.append("--dry-run")

        # The Jellyfin key is never put on the helper's command line (anyone
        # on this computer can read a process list). The helper used it only
        # to ask Jellyfin to rescan after an import; this bridge does that
        # itself below, with the key it already holds in memory.

        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=600,
                **procflags.hidden(),
            )
            if proc.returncode != 0:
                return ImportResult(
                    success=False,
                    message=f"Import failed: {proc.stderr.strip()}",
                )

            # Parse JSON output from the script
            try:
                result_data = json.loads(proc.stdout)
            except json.JSONDecodeError:
                return ImportResult(
                    success=True,
                    message=proc.stdout.strip(),
                )

            imported = result_data.get("imported", 0)
            if not dry_run and self._jellyfin_api_key \
                    and isinstance(imported, int) and imported > 0:
                try:
                    self.trigger_scan()
                except Exception as exc:  # noqa: BLE001 - the import still counts
                    logger.info("Jellyfin rescan after import failed: %s",
                                type(exc).__name__)

            return ImportResult(
                success=result_data.get("success", True),
                tracks_imported=imported,
                tracks_skipped=result_data.get("skipped", 0),
                drm_skipped=result_data.get("drm_skipped", 0),
                errors=result_data.get("errors", []),
                message=result_data.get("message", "Import complete"),
            )

        except subprocess.TimeoutExpired:
            return ImportResult(
                success=False,
                message="iPod import timed out after 10 minutes.",
            )
        except Exception as exc:
            return ImportResult(success=False, message=f"Import error: {exc}")


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_instance: MediaBridge | None = None
_lock = threading.Lock()


def get_media_bridge(*, _force_reload: bool = False) -> MediaBridge:
    """Return the global MediaBridge singleton."""
    global _instance
    if _instance is None or _force_reload:
        with _lock:
            if _instance is None or _force_reload:
                _instance = MediaBridge()
    return _instance


def reload_key() -> None:
    """The Jellyfin key was sealed again or changed: the live bridge (if one
    was built) reads it now, no restart."""
    with _lock:
        bridge = _instance
    if bridge is not None:
        bridge.reload_key()
