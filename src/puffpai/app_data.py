"""
PuffPAI Application Data Manager
Shared data layer for HTML applications (AlarmFlow, Calendar, FreshTable, FitFam, Trivia).
Each app has a JSON file in app_data/ that both PuffPAI tools and HTML apps read/write.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

import copy
import json
import os
import time
import random
import string
import threading
from datetime import datetime, timedelta
from functools import partial

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
#: Where the SHIPPED apps have always kept their stores: ``<checkout>/app_data``.
#: Not the location for anything new - see :func:`data_dir_for`.
_DATA_DIR = os.path.join(_REPO_ROOT, "app_data")
_APPS_DIR = os.path.join(os.path.dirname(_REPO_ROOT), "Applications")

#: The stores directory under the state dir, by name. Same word as the legacy
#: folder so an owner moving files between them does not have to translate.
DATA_DIRNAME = "app_data"


def _state_data_dir() -> str:
    """Where an app an OWNER added keeps its store: ``<state dir>/app_data``.

    ``_DATA_DIR`` is inside the checkout (or inside site-packages on a pip
    install), so a persona scaffolded by ``puffpai persona new`` was writing the
    owner's own data into the install - one stray ``git add`` from being
    published, and unwritable on a packaged build. Owner data belongs beside the
    rest of the machine-local state.
    """
    from puffpai.config import get_state_dir
    return os.path.join(get_state_dir(), DATA_DIRNAME)


def _is_user_app(app_id: str) -> bool:
    """True when this app's manifest lives in the OWNER's apps directory."""
    from puffpai import app_registry

    try:
        folder = app_registry.resolve_within(app_registry.user_apps_dir(), app_id)
    except (ValueError, OSError):
        return False
    return os.path.isfile(os.path.join(folder, app_registry.MANIFEST_NAME))


def data_dir_for(app_id: str) -> str:
    """Which stores directory one app's document lives in.

    Three cases, and the middle one is the reason this is a function:

    * an app found in the owner's own ``apps/`` root keeps its store in the
      state dir, because the install must stay untouched;
    * a SHIPPED app on a build that already has a ``<checkout>/app_data``
      directory keeps using it. Nobody's existing file moves - a "migration"
      that relocates an owner's calendar behind their back is a data-loss bug
      wearing a tidy-up's clothes;
    * a shipped app on a build with no such directory gets the state dir, so a
      fresh install never starts writing into its own checkout at all.

    The second test is the DIRECTORY, not the individual file, deliberately. A
    per-file test would leave an existing owner with their calendar in the
    checkout and their pantry in the state dir the first time they opened
    FreshTable - two halves of one household's data in two places, for no gain.
    An existing stores directory is an existing decision; it is honoured whole.
    """
    from puffpai import app_registry

    if _is_user_app(app_id):
        return _state_data_dir()
    try:
        existing = app_registry.data_path(app_id, _DATA_DIR)
    except ValueError:
        return _state_data_dir()
    if os.path.isdir(_DATA_DIR) or os.path.exists(existing):
        return _DATA_DIR
    return _state_data_dir()


def _uid():
    """Generate a unique ID matching the HTML apps' convention."""
    ts = str(int(time.time() * 1000))
    rand = ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
    return f"{ts}{rand}"


# The apps this build shipped with, and their starting shapes. This is the
# fail-open net, not the source of truth: the real list and the real defaults
# come from the manifests in apps/, the same way the router's vocabulary does.
# A build whose apps/ directory is missing or unreadable keeps working on
# exactly the five it always had, because the alternative - an owner opening
# FreshTable and being told there is no such app - is the worse failure.
_FALLBACK_DEFAULTS = {
    'alarmflow': {"items": [], "last_updated": ""},
    'calendar': {
        "family": [
            {"id": "me", "name": "Me", "color": "#38bdf8", "emoji": "\U0001f642", "role": "parent"},
        ],
        "events": [], "tasks": [], "meds": [], "bills": [],
        "last_updated": ""
    },
    'freshtable': {
        "pantry": [], "shoppingList": [], "mealPlan": [],
        "recipes": [],
        "last_updated": ""
    },
    'fitfam': {
        "users": {
            "alex": {"name": "Adult 1", "emoji": "\U0001f9d4",
                     "prefs": {"level": "mid", "type": "full", "duration": 45},
                     "goals": [], "history": [], "streak": 0, "totalWorkouts": 0},
            "jordan": {"name": "Adult 2", "emoji": "\U0001f3c4",
                       "prefs": {"level": "mid", "type": "full", "duration": 30},
                       "goals": [], "history": [], "streak": 0, "totalWorkouts": 0},
        },
        "last_updated": ""
    },
    'trivia': {"results": [], "last_updated": ""},
}


class AppDataUnreadable(RuntimeError):
    """A document exists on disk and could not be decoded.

    Deliberately NOT the same answer as "no document yet". `_load` used to
    catch JSONDecodeError and return the app's DEFAULTS, so a reader landing on
    a file mid-write was told the household was empty - and the app pages pull,
    merge and push, which turns one unlucky read into a saved empty household.
    A caller that cannot read must find out and say so; a page must never
    receive "empty" for "corrupt".
    """


# One lock per FILE, shared by every AppData instance in the process.
#
# The locks used to live on the instance, so two instances - which is what a
# singleton swap produces, and the request path holds its own reference across
# the swap - serialised against each other not at all, while resolving to the
# same paths. Keying on the resolved path is what makes "the same document" the
# thing that is locked, rather than "the same object".
_FILE_LOCKS: dict = {}
_FILE_LOCKS_GUARD = threading.Lock()


def _lock_for(path: str) -> threading.Lock:
    """The process-wide lock for one document path."""
    key = os.path.normcase(os.path.abspath(path))
    with _FILE_LOCKS_GUARD:
        lock = _FILE_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _FILE_LOCKS[key] = lock
        return lock


def _manifest_tables():
    """``(valid_apps, files, defaults)`` read fresh from the installed apps.

    Read at call time, not at import: this is what makes a rescan possible
    without replacing the AppData instance.
    """
    from puffpai import app_registry

    defaults: dict = {}
    for manifest in app_registry.load_manifests():
        app_id = str(manifest["id"])
        try:
            app_registry.data_path(app_id, data_dir_for(app_id))
        except ValueError:
            continue
        defaults[app_id] = app_registry.default_data(manifest)
    if not defaults:
        defaults = {k: copy.deepcopy(v) for k, v in _FALLBACK_DEFAULTS.items()}

    valid = list(defaults)
    # One directory per app, decided by :func:`data_dir_for` rather than by one
    # module constant, which is what lets an owner's app be stored outside the
    # install while a shipped app's existing document stays exactly where it is.
    files = {app: app_registry.data_path(app, data_dir_for(app)) for app in valid}
    for directory in {os.path.dirname(path) for path in files.values()}:
        try:
            os.makedirs(directory, exist_ok=True)
        except OSError:
            # An unwritable stores directory is reported by the first read or
            # write that needs it, in that app's own words. Failing the whole
            # table here would take the other apps down with it.
            pass
    return valid, files, defaults


class AppData:
    """Unified CRUD layer for all PuffPAI HTML applications.

    The valid apps, their data files and their starting shapes are read from
    the installed manifests in ``apps/`` (see :mod:`puffpai.app_registry`), so
    adding an app is dropping a folder in rather than editing this file. WHERE
    each app's document lives is :func:`data_dir_for`'s decision: an existing
    ``<checkout>/app_data/<id>.json`` keeps its place, and everything else -
    every app an owner adds, and every shipped app with nothing stored yet -
    lives under the state dir, outside the install. The id is put through the
    registry's containment check before it becomes a path either way.
    """

    VALID_APPS = list(_FALLBACK_DEFAULTS)

    def __init__(self):
        # The stores directories are created by :func:`_manifest_tables`, one
        # per app, so a build whose shipped apps all live in the state dir never
        # creates an empty ``app_data/`` inside its own checkout.
        self._swap_lock = threading.Lock()
        self._install(*_manifest_tables())

    def _install(self, valid, files, defaults):
        """Point this instance at one set of manifest-derived tables.

        **Publication order is the whole point.** Every reader gates on
        ``VALID_APPS`` and then indexes ``_locks`` / ``_files`` / ``_defaults``
        WITHOUT holding a lock (it cannot hold one - the lock is what it is
        looking up), so a reader can run between any two assignments here. When
        ``VALID_APPS`` was published first, a reader that got past the gate on
        a newly named app found ``self._locks[app]`` still on the old dict and
        raised KeyError.

        So: the three lookup tables go first, ``VALID_APPS`` last. A reader
        that sees the old list is answered by the old entries, and a reader
        that sees the new list is answered by entries that are already there.

        The tables are a SUPERSET - old entries are kept alongside new ones -
        because ordering alone only fixes one direction. An app being REMOVED
        publishes narrower tables while the old, wider ``VALID_APPS`` is still
        visible, and that reader would KeyError on the way out instead of on
        the way in. A retired app's entry is unreachable anyway: ``VALID_APPS``
        is the gate, and it no longer names it.
        """
        # A fresh copy per call: the caller mutates what it gets back, so
        # handing out the stored dict would make one owner's first write the
        # next owner's "default".
        new_defaults = {
            app: partial(copy.deepcopy, shape) for app, shape in defaults.items()
        }
        # Shared, per-file, process-wide. See :func:`_lock_for`.
        new_locks = {app: _lock_for(path) for app, path in files.items()}

        self._defaults = {**getattr(self, '_defaults', {}), **new_defaults}
        self._locks = {**getattr(self, '_locks', {}), **new_locks}
        self._files = {**getattr(self, '_files', {}), **dict(files)}
        # Last, and only once every lookup above it will succeed.
        self.VALID_APPS = list(valid)

    def reload_manifests(self):
        """Re-read ``apps/`` into THIS instance. Returns the new VALID_APPS.

        The install mechanism used to replace the singleton, which opened a
        window: a request thread that had already resolved its AppData kept
        writing under the old instance's locks while every later request used
        the new one's. Mutating in place closes it - there is only ever one
        instance, and the locks are keyed on the file anyway, so even a caller
        holding a stale reference is serialised against the fresh one.

        Every affected lock is held across the swap, taken in a fixed order
        (sorted by path) so two concurrent reloads cannot deadlock. Callers
        hold at most one lock at a time, so they cannot be the other half of a
        cycle either.
        """
        valid, files, defaults = _manifest_tables()
        paths = sorted(set(list(self._files.values()) + list(files.values())),
                       key=lambda q: os.path.normcase(os.path.abspath(q)))
        locks = [_lock_for(q) for q in paths]
        with self._swap_lock:
            acquired = []
            try:
                for lock in locks:
                    lock.acquire()
                    acquired.append(lock)
                self._install(valid, files, defaults)
            finally:
                for lock in reversed(acquired):
                    lock.release()
        return list(self.VALID_APPS)

    # ── Generic load/save ────────────────────────────────────────────────────

    def _load(self, app):
        """This app's document, or its starting shape when there is no file.

        Raises :class:`AppDataUnreadable` when a file EXISTS and is non-empty
        and will not decode. Returning defaults there is the bug that turns a
        torn read into a saved empty household: the page pulls, sees nothing,
        merges nothing and pushes that back.

        A zero-byte file is still treated as "not written yet" - that is what a
        crash during an old non-atomic write left behind, and there is nothing
        in it to lose.
        """
        path = self._files[app]
        if not os.path.exists(path):
            return self._defaults[app]()
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except json.JSONDecodeError as exc:
            try:
                empty = os.path.getsize(path) == 0
            except OSError:
                empty = False
            if empty:
                return self._defaults[app]()
            # Names only. The contents are the household's, and a decode error
            # is exactly when a log line would carry a fragment of them.
            print(f"[AppData] {app}.json exists but does not decode: "
                  f"{exc.msg} (line {exc.lineno})")
            raise AppDataUnreadable(
                f"The saved {app} document could not be read.") from exc
        except OSError as exc:
            print(f"[AppData] {app}.json could not be opened: {exc}")
            raise AppDataUnreadable(
                f"The saved {app} document could not be read.") from exc

    def _save(self, app, data):
        """Write one document atomically, owner-only.

        `open(path, 'w')` truncates in place, so every concurrent reader had a
        window in which the household document was short or empty, and the
        file kept whatever mode it was first created with forever. The writer
        in :mod:`puffpai.security.app_data_store` writes a sibling temp file at
        0600 and `os.replace`s it in: a reader sees the whole old document or
        the whole new one, and a failed write leaves the previous one intact.
        """
        from puffpai.security.app_data_store import write_json_secure

        data['last_updated'] = datetime.now().isoformat()
        write_json_secure(self._files[app], data)

    def get_data(self, app):
        """Get full data for an app (used by REST API)."""
        if app not in self.VALID_APPS:
            return None
        with self._locks[app]:
            return self._load(app)

    def set_data(self, app, data, recover=False):
        """Replace full data for an app (used by REST API).

        Raises :class:`AppDataUnreadable` when a document already exists and
        will not decode, UNLESS ``recover`` is set. Reads were taught not to
        report corrupt as empty; writes had the same hole facing the other way.
        A page that pulls, fails, and pushes what it has would overwrite the
        one copy of a household's data with whatever the browser happened to be
        holding - and because the pull error is asynchronous, it would do it
        without anyone deciding to.

        ``recover`` is the deliberate override for an owner (or an operator)
        who has looked at the file and wants it replaced. The app pages never
        send it.
        """
        if app not in self.VALID_APPS:
            return False
        with self._locks[app]:
            if not recover:
                # Raises if the file exists, is non-empty and will not decode.
                self._load(app)
            self._save(app, data)
        return True

    def get_updated(self, app):
        """Get last_updated timestamp for an app (lightweight poll)."""
        if app not in self.VALID_APPS:
            return None
        with self._locks[app]:
            data = self._load(app)
        return data.get('last_updated', '')

    # ══════════════════════════════════════════════════════════════════════════
    #  ALARMFLOW
    # ══════════════════════════════════════════════════════════════════════════

    def alarm_create_timer(self, desc, hours=0, minutes=0, seconds=0, sound='chime'):
        """Create a countdown timer. Returns confirmation string."""
        total = int(hours) * 3600 + int(minutes) * 60 + int(seconds)
        if total <= 0:
            return "Error: timer duration must be greater than zero."

        item = {
            "id": f"a{_uid()}",
            "mode": "timer",
            "desc": desc or "Timer",
            "tot": total,
            "rem": total,
            "sound": sound,
            "status": "running",
            "source": "puffpai",
            "ts": int(time.time() * 1000),
            "targetMs": None
        }

        with self._locks['alarmflow']:
            data = self._load('alarmflow')
            data['items'].append(item)
            self._save('alarmflow', data)

        parts = []
        if hours:
            parts.append(f"{int(hours)} hour{'s' if int(hours) != 1 else ''}")
        if minutes:
            parts.append(f"{int(minutes)} minute{'s' if int(minutes) != 1 else ''}")
        if seconds and not hours:
            parts.append(f"{int(seconds)} second{'s' if int(seconds) != 1 else ''}")
        time_str = " and ".join(parts) if parts else f"{total} seconds"
        return f"Timer set: {desc}, {time_str} counting down."

    def alarm_create_alarm(self, desc, hour, minute, sound='chime'):
        """Create an alarm for a specific time. Returns confirmation string."""
        hour, minute = int(hour), int(minute)
        now = datetime.now()
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)

        target_ms = int(target.timestamp() * 1000)
        remaining = int((target - now).total_seconds())

        item = {
            "id": f"a{_uid()}",
            "mode": "alarm",
            "desc": desc or "Alarm",
            "tot": remaining,
            "rem": remaining,
            "sound": sound,
            "status": "running",
            "source": "puffpai",
            "ts": int(time.time() * 1000),
            "targetMs": target_ms
        }

        with self._locks['alarmflow']:
            data = self._load('alarmflow')
            data['items'].append(item)
            self._save('alarmflow', data)

        h12 = hour % 12 or 12
        ampm = "AM" if hour < 12 else "PM"
        return f"Alarm set: {desc}, ringing at {h12}:{minute:02d} {ampm}."

    def alarm_list(self):
        """List active timers and alarms."""
        with self._locks['alarmflow']:
            data = self._load('alarmflow')

        active = [i for i in data.get('items', []) if i['status'] in ('running', 'paused', 'ringing')]
        if not active:
            return "No active timers or alarms."

        lines = []
        for item in active:
            if item['mode'] == 'timer':
                mins = item['rem'] // 60
                secs = item['rem'] % 60
                status = f" ({item['status']})" if item['status'] != 'running' else ""
                lines.append(f"{item['desc']}: {mins}m {secs}s remaining{status}")
            else:
                target_ms = item.get('targetMs', 0)
                if target_ms:
                    target = datetime.fromtimestamp(target_ms / 1000)
                    h12 = target.hour % 12 or 12
                    ampm = "AM" if target.hour < 12 else "PM"
                    lines.append(f"{item['desc']}: alarm at {h12}:{target.minute:02d} {ampm}")
                else:
                    lines.append(f"{item['desc']}: alarm ({item['status']})")

        return f"You have {len(active)} active item{'s' if len(active) != 1 else ''}:\n" + "\n".join(lines)

    def _alarm_find(self, data, item_id, active_only=True):
        """Voice-friendly item lookup: exact id/desc, then unambiguous
        substring. Returns (item, error_message) - exactly one is set."""
        ident = (item_id or '').lower().strip()
        for item in data.get('items', []):
            if item['id'] == item_id or item['desc'].lower() == ident:
                return item, None
        if len(ident) >= 3:
            hits = [i for i in data.get('items', [])
                    if ident in i['desc'].lower()
                    and (not active_only or i.get('status') != 'stopped')]
            if len(hits) == 1:
                return hits[0], None
            if len(hits) > 1:
                names = ", ".join(i['desc'] for i in hits[:4])
                return None, f"Several match '{item_id}': {names}. Which one?"
        return None, f"No alarm or timer found matching '{item_id}'."

    def alarm_snooze(self, item_id, minutes=9):
        """Push a ringing (or running) item out by `minutes`."""
        minutes = max(1, int(minutes))
        with self._locks['alarmflow']:
            data = self._load('alarmflow')
            item, err = self._alarm_find(data, item_id)
            if err:
                return err
            if item['status'] == 'stopped':
                return f"'{item['desc']}' is already dismissed."
            now_ms = int(time.time() * 1000)
            if item['mode'] == 'alarm':
                item['targetMs'] = now_ms + minutes * 60_000
            else:
                item['ts'] = now_ms
                item['tot'] = minutes * 60
            item['rem'] = minutes * 60
            item['status'] = 'running'
            item['snoozed'] = int(item.get('snoozed', 0)) + 1
            self._save('alarmflow', data)
        return f"Snoozed {item['desc']} for {minutes} minutes."

    def alarm_pause(self, item_id):
        """Freeze a running timer (alarms have a fixed time - no pause)."""
        with self._locks['alarmflow']:
            data = self._load('alarmflow')
            item, err = self._alarm_find(data, item_id)
            if err:
                return err
            if item['mode'] != 'timer':
                return ("Alarms ring at a set time and can't be paused - "
                        "snooze or dismiss instead.")
            if item['status'] != 'running':
                return f"'{item['desc']}' isn't running."
            elapsed = (int(time.time() * 1000) - item['ts']) / 1000
            item['rem'] = max(0, int(item['tot'] - elapsed))
            item['status'] = 'paused'
            item['ts'] = None
            self._save('alarmflow', data)
        mins, secs = divmod(item['rem'], 60)
        return f"Paused {item['desc']} with {mins}m {secs}s left."

    def alarm_resume(self, item_id):
        """Resume a paused timer from where it froze."""
        with self._locks['alarmflow']:
            data = self._load('alarmflow')
            item, err = self._alarm_find(data, item_id)
            if err:
                return err
            if item['status'] != 'paused':
                return f"'{item['desc']}' isn't paused."
            item['tot'] = item['rem']
            item['ts'] = int(time.time() * 1000)
            item['status'] = 'running'
            self._save('alarmflow', data)
        return f"Resumed {item['desc']}."

    def alarm_dismiss(self, item_id):
        """Dismiss/stop a specific alarm or timer."""
        ident = (item_id or '').lower().strip()
        with self._locks['alarmflow']:
            data = self._load('alarmflow')
            for item in data.get('items', []):
                if item['id'] == item_id or item['desc'].lower() == ident:
                    item['status'] = 'stopped'
                    self._save('alarmflow', data)
                    return f"Dismissed: {item['desc']}"
            # Spoken names rarely match labels exactly ("seven minute
            # timer" vs "7 minutes timer") — accept a substring match
            # when it is UNAMBIGUOUS.
            if len(ident) >= 3:
                hits = [i for i in data.get('items', [])
                        if ident in i['desc'].lower()
                        and i.get('status') != 'stopped']
                if len(hits) == 1:
                    hits[0]['status'] = 'stopped'
                    self._save('alarmflow', data)
                    return f"Dismissed: {hits[0]['desc']}"
                if len(hits) > 1:
                    names = ", ".join(i['desc'] for i in hits[:4])
                    return f"Several match '{item_id}': {names}. Which one?"
            return f"No alarm or timer found matching '{item_id}'."

    def alarm_dismiss_all(self):
        """Dismiss all ringing alarms/timers."""
        with self._locks['alarmflow']:
            data = self._load('alarmflow')
            count = 0
            for item in data.get('items', []):
                if item['status'] in ('running', 'ringing', 'paused'):
                    item['status'] = 'stopped'
                    count += 1
            self._save('alarmflow', data)
        return f"Dismissed {count} item{'s' if count != 1 else ''}." if count else "No active alarms to dismiss."

    def alarm_delete(self, item_id):
        """Permanently remove an alarm/timer."""
        ident = (item_id or '').lower().strip()
        if len(ident) < 2:
            return "I need the timer or alarm's name or id to delete it."
        with self._locks['alarmflow']:
            data = self._load('alarmflow')
            before = len(data.get('items', []))
            data['items'] = [i for i in data.get('items', [])
                             if i['id'] != item_id and i['desc'].lower() != ident]
            removed = before - len(data['items'])
            if not removed and len(ident) >= 3:
                # Unambiguous substring fallback for spoken names
                # ("seven minute timer" vs stored "7 minutes timer").
                hits = [i for i in data['items'] if ident in i['desc'].lower()]
                if len(hits) == 1:
                    data['items'] = [i for i in data['items']
                                     if i['id'] != hits[0]['id']]
                    removed = 1
                elif len(hits) > 1:
                    names = ", ".join(i['desc'] for i in hits[:4])
                    return f"Several match '{item_id}': {names}. Which one?"
            self._save('alarmflow', data)
        return f"Deleted {removed} item{'s' if removed != 1 else ''}." if removed else f"No item found matching '{item_id}'."

    # ══════════════════════════════════════════════════════════════════════════
    #  CALENDAR
    # ══════════════════════════════════════════════════════════════════════════

    def cal_add_event(self, title, start_iso, end_iso=None, cat='other',
                      assigned_to='all', prio='medium', location=None, recurrence=None):
        """Add a calendar event.

        Recurrence may be one of: None, "daily", "weekdays", "weekly",
        "biweekly", "monthly", "yearly". The frontend expands it into
        concrete instances when rendering.
        """
        if not end_iso:
            try:
                start_dt = datetime.fromisoformat(start_iso)
                end_dt = start_dt + timedelta(hours=1)
                end_iso = end_dt.isoformat()
            except ValueError:
                end_iso = start_iso

        valid_recurrence = {
            None, "", "daily", "weekdays", "weekly",
            "biweekly", "monthly", "yearly",
        }
        if recurrence and recurrence not in valid_recurrence:
            recurrence = None
        if recurrence == "":
            recurrence = None

        event = {
            "id": f"e{_uid()}",
            "title": title,
            "cat": cat if cat in ('medical', 'sports', 'school', 'work', 'family', 'bills', 'appointment', 'other') else 'other',
            "start": start_iso,
            "end": end_iso,
            "to": assigned_to,
            "prio": prio if prio in ('low', 'medium', 'high', 'urgent') else 'medium',
            "src": "puffpai",
            "rec": recurrence,
            "loc": location
        }

        with self._locks['calendar']:
            data = self._load('calendar')
            data['events'].append(event)
            self._save('calendar', data)

        try:
            dt = datetime.fromisoformat(start_iso)
            date_str = dt.strftime("%B %d").replace(" 0", " ")
            time_str = dt.strftime("%I:%M %p").lstrip("0")
            return f"Added {title} on {date_str} at {time_str}, assigned to {assigned_to}."
        except ValueError:
            return f"Added {title} on {start_iso}, assigned to {assigned_to}."

    def cal_add_task(self, title, due_iso, cat='other', assigned_to='all', prio='medium'):
        """Add a task/to-do and a corresponding calendar event."""
        task_id = f"t{_uid()}"
        task = {
            "id": task_id,
            "title": title,
            "cat": cat,
            "due": due_iso,
            "to": assigned_to,
            "prio": prio if prio in ('low', 'medium', 'high', 'urgent') else 'medium',
            "status": "pending",
            "src": "puffpai"
        }

        # Build a corresponding event so the task appears on the calendar month view
        try:
            due_dt = datetime.fromisoformat(due_iso)
            # If the ISO string has no explicit time component (date-only), default to 09:00
            if "T" not in due_iso:
                due_dt = due_dt.replace(hour=9, minute=0, second=0)
            start_iso = due_dt.isoformat()
            end_iso = (due_dt + timedelta(hours=1)).isoformat()
        except ValueError:
            start_iso = due_iso
            end_iso = due_iso

        task_event = {
            "id": f"te{_uid()}",
            "title": title,
            "cat": cat if cat in ('medical', 'sports', 'school', 'work', 'family', 'bills', 'appointment', 'other') else 'other',
            "start": start_iso,
            "end": end_iso,
            "to": assigned_to,
            "prio": prio if prio in ('low', 'medium', 'high', 'urgent') else 'medium',
            "src": "task",
            "taskId": task_id,
            "rec": None,
            "loc": None
        }

        with self._locks['calendar']:
            data = self._load('calendar')
            data['tasks'].append(task)
            data['events'].append(task_event)
            self._save('calendar', data)

        try:
            dt = datetime.fromisoformat(due_iso)
            date_str = dt.strftime("%B %d").replace(" 0", " ")
            return f"Task added: {title}, due {date_str}, assigned to {assigned_to}."
        except ValueError:
            return f"Task added: {title}, due {due_iso}."

    def cal_add_med(self, name, dose, assigned_to, freq='daily', next_time='8:00 AM'):
        """Add a medication reminder."""
        med = {
            "id": f"m{_uid()}",
            "name": name,
            "dose": dose,
            "to": assigned_to,
            "freq": freq,
            "next": next_time,
            "status": "due",
            "refill": (datetime.now() + timedelta(days=30)).isoformat()
        }

        with self._locks['calendar']:
            data = self._load('calendar')
            data['meds'].append(med)
            self._save('calendar', data)

        return f"Medication added: {name} ({dose}), {freq} for {assigned_to}."

    def cal_add_bill(self, name, amount, due_iso, cat='utilities', auto=False):
        """Add a bill to track and a corresponding calendar event."""
        bill_id = f"b{_uid()}"
        bill = {
            "id": bill_id,
            "name": name,
            "amt": float(amount) if amount else 0,
            "due": due_iso,
            "cat": cat,
            "status": "pending",
            "auto": auto
        }

        # Build a corresponding event so the bill appears on the calendar month view
        amt_str = f"${float(amount):.2f}" if amount else "$0.00"
        bill_title = f"{name} ({amt_str})"
        try:
            due_dt = datetime.fromisoformat(due_iso)
            if "T" not in due_iso:
                due_dt = due_dt.replace(hour=9, minute=0, second=0)
            start_iso = due_dt.isoformat()
            end_iso = (due_dt + timedelta(hours=1)).isoformat()
        except ValueError:
            start_iso = due_iso
            end_iso = due_iso

        bill_event = {
            "id": f"be{_uid()}",
            "title": bill_title,
            "cat": "bills",
            "start": start_iso,
            "end": end_iso,
            "to": "all",
            "prio": "medium",
            "src": "bill",
            "billId": bill_id,
            "rec": None,
            "loc": None
        }

        with self._locks['calendar']:
            data = self._load('calendar')
            data['bills'].append(bill)
            data['events'].append(bill_event)
            self._save('calendar', data)

        try:
            dt = datetime.fromisoformat(due_iso)
            date_str = dt.strftime("%B %d").replace(" 0", " ")
            return f"Bill tracked: {name}, ${float(amount):.2f}, due {date_str}."
        except (ValueError, TypeError):
            return f"Bill tracked: {name}, ${amount}, due {due_iso}."

    def cal_list_events(self, days_ahead=7):
        """List upcoming events within N days."""
        with self._locks['calendar']:
            data = self._load('calendar')

        events = data.get('events', [])
        now = datetime.now()
        cutoff = now + timedelta(days=int(days_ahead))

        upcoming = []
        for e in events:
            try:
                raw = e['start'].replace('Z', '+00:00') if e['start'].endswith('Z') else e['start']
                start = datetime.fromisoformat(raw)
                # Strip timezone info for comparison with naive datetime
                if start.tzinfo is not None:
                    start = start.replace(tzinfo=None)
                if now - timedelta(hours=1) <= start <= cutoff:
                    upcoming.append((start, e))
            except (ValueError, KeyError):
                continue

        if not upcoming:
            return f"No events in the next {days_ahead} days."

        upcoming.sort(key=lambda x: x[0])
        lines = []
        for start, e in upcoming:
            date_str = start.strftime("%b %d, %I:%M %p").replace(" 0", " ")
            lines.append(f"{date_str} - {e['title']} ({e.get('cat', 'other')})")

        return f"{len(upcoming)} upcoming event{'s' if len(upcoming) != 1 else ''}:\n" + "\n".join(lines)

    def cal_list_tasks(self, status='pending'):
        """List tasks filtered by status."""
        with self._locks['calendar']:
            data = self._load('calendar')

        tasks = [t for t in data.get('tasks', []) if t.get('status') == status]
        if not tasks:
            return f"No {status} tasks."

        lines = []
        for t in tasks:
            due_str = ""
            try:
                due = datetime.fromisoformat(t['due'])
                due_str = f" (due {due.strftime('%b %d')})"
            except (ValueError, KeyError):
                pass
            prio = f" [{t.get('prio', '')}]" if t.get('prio') in ('urgent', 'high') else ""
            lines.append(f"{t['title']}{due_str}{prio}")

        return f"{len(tasks)} {status} task{'s' if len(tasks) != 1 else ''}:\n" + "\n".join(lines)

    def cal_list_meds(self):
        """List medication reminders."""
        with self._locks['calendar']:
            data = self._load('calendar')

        meds = data.get('meds', [])
        if not meds:
            return "No medications tracked."

        lines = []
        for m in meds:
            lines.append(f"{m['name']} ({m['dose']}) - {m['freq']} for {m['to']}, next: {m['next']}")
        return f"{len(meds)} medication{'s' if len(meds) != 1 else ''}:\n" + "\n".join(lines)

    def cal_list_bills(self, status='pending'):
        """List bills filtered by status."""
        with self._locks['calendar']:
            data = self._load('calendar')

        bills = [b for b in data.get('bills', []) if b.get('status') == status]
        if not bills:
            return f"No {status} bills."

        total = sum(b.get('amt', 0) for b in bills)
        lines = []
        for b in bills:
            due_str = ""
            try:
                due = datetime.fromisoformat(b['due'])
                due_str = f" due {due.strftime('%b %d')}"
            except (ValueError, KeyError):
                pass
            lines.append(f"{b['name']}: ${b.get('amt', 0):.2f}{due_str}")

        return f"{len(bills)} {status} bill{'s' if len(bills) != 1 else ''} (total ${total:.2f}):\n" + "\n".join(lines)

    def cal_edit_event(self, event_id, **kwargs):
        """Edit an existing calendar event. Match by ID or title (partial match).
        Accept optional: title, cat, start, end, to (assigned_to), prio, loc, rec."""
        with self._locks['calendar']:
            data = self._load('calendar')
            for event in data.get('events', []):
                if (event['id'] == event_id or
                        event_id.lower() in event['title'].lower()):
                    field_map = {
                        'title': 'title', 'cat': 'cat', 'start': 'start',
                        'end': 'end', 'to': 'to', 'prio': 'prio',
                        'loc': 'loc', 'rec': 'rec',
                    }
                    updated = []
                    for param, field in field_map.items():
                        if param in kwargs and kwargs[param] is not None:
                            event[field] = kwargs[param]
                            updated.append(param)
                    if not updated:
                        return f"No fields to update for '{event['title']}'."
                    self._save('calendar', data)
                    return f"Updated '{event['title']}': changed {', '.join(updated)}."
            return f"No event found matching '{event_id}'."

    def cal_edit_task(self, task_id_or_title, **kwargs):
        """Edit an existing task. Match by ID or title (partial match).
        Accept optional: title, due, cat, to, prio."""
        with self._locks['calendar']:
            data = self._load('calendar')
            for task in data.get('tasks', []):
                if (task['id'] == task_id_or_title or
                        task_id_or_title.lower() in task['title'].lower()):
                    field_map = {
                        'title': 'title', 'due': 'due', 'cat': 'cat',
                        'to': 'to', 'prio': 'prio',
                    }
                    updated = []
                    for param, field in field_map.items():
                        if param in kwargs and kwargs[param] is not None:
                            task[field] = kwargs[param]
                            updated.append(param)
                    if not updated:
                        return f"No fields to update for '{task['title']}'."
                    self._save('calendar', data)
                    return f"Updated '{task['title']}': changed {', '.join(updated)}."
            return f"No task found matching '{task_id_or_title}'."

    def cal_complete_task(self, task_identifier):
        """Set a task as completed by title or ID, and update its linked calendar event."""
        with self._locks['calendar']:
            data = self._load('calendar')
            for task in data.get('tasks', []):
                if (task['id'] == task_identifier or
                        task_identifier.lower() in task['title'].lower()):
                    task['status'] = 'completed'
                    # Update the linked calendar event to show completion
                    for event in data.get('events', []):
                        if event.get('taskId') == task['id']:
                            if not event['title'].startswith('\u2705 '):
                                event['title'] = f'\u2705 {event["title"]}'
                            break
                    self._save('calendar', data)
                    return f"Done, marked '{task['title']}' as completed."
            return f"No task found matching '{task_identifier}'."

    def cal_delete_event(self, event_identifier):
        """Delete a calendar event by title or ID."""
        # Substring matching means a blank/tiny identifier matches EVERY
        # title — an empty string deleted the whole calendar. Refuse.
        event_identifier = (event_identifier or '').strip()
        if len(event_identifier) < 2:
            return "I need the event's name or id to delete it."
        with self._locks['calendar']:
            data = self._load('calendar')
            before = len(data.get('events', []))
            data['events'] = [e for e in data.get('events', [])
                              if e['id'] != event_identifier and
                              event_identifier.lower() not in e['title'].lower()]
            removed = before - len(data['events'])
            self._save('calendar', data)
        return f"Deleted {removed} event{'s' if removed != 1 else ''}." if removed else f"No event found matching '{event_identifier}'."

    def cal_delete_task(self, task_identifier):
        """Delete a task by title or ID."""
        # Same blank-identifier mass-delete guard as cal_delete_event.
        task_identifier = (task_identifier or '').strip()
        if len(task_identifier) < 2:
            return "I need the task's name or id to delete it."
        with self._locks['calendar']:
            data = self._load('calendar')
            before = len(data.get('tasks', []))
            data['tasks'] = [t for t in data.get('tasks', [])
                             if t['id'] != task_identifier and
                             task_identifier.lower() not in t['title'].lower()]
            removed = before - len(data['tasks'])
            self._save('calendar', data)
        return f"Deleted {removed} task{'s' if removed != 1 else ''}." if removed else f"No task found matching '{task_identifier}'."

    def cal_take_med(self, med_identifier):
        """Record a medication as taken."""
        with self._locks['calendar']:
            data = self._load('calendar')
            for med in data.get('meds', []):
                if (med['id'] == med_identifier or
                        med_identifier.lower() in med['name'].lower()):
                    med['status'] = 'taken'
                    self._save('calendar', data)
                    return f"Recorded: {med['name']} taken."
            return f"No medication found matching '{med_identifier}'."

    def cal_pay_bill(self, bill_identifier):
        """Record a bill as paid and update its linked calendar event."""
        with self._locks['calendar']:
            data = self._load('calendar')
            for bill in data.get('bills', []):
                if (bill['id'] == bill_identifier or
                        bill_identifier.lower() in bill['name'].lower()):
                    bill['status'] = 'paid'
                    # Update the linked calendar event to show payment
                    for event in data.get('events', []):
                        if event.get('billId') == bill['id']:
                            if not event['title'].startswith('\u2705 '):
                                event['title'] = f'\u2705 {event["title"]}'
                            break
                    self._save('calendar', data)
                    return f"Marked '{bill['name']}' as paid."
            return f"No bill found matching '{bill_identifier}'."

    # ══════════════════════════════════════════════════════════════════════════
    #  FRESHTABLE
    # ══════════════════════════════════════════════════════════════════════════

    _FOOD_CATEGORIES = {
        'Protein': ['chicken', 'beef', 'pork', 'fish', 'salmon', 'shrimp', 'turkey', 'steak',
                     'bacon', 'sausage', 'lamb', 'tofu', 'eggs', 'egg'],
        'Produce': ['apple', 'banana', 'orange', 'tomato', 'lettuce', 'spinach', 'broccoli',
                    'carrot', 'onion', 'pepper', 'avocado', 'potato', 'garlic', 'cucumber',
                    'celery', 'mushroom', 'corn', 'berry', 'grape', 'lemon', 'lime', 'mango'],
        'Dairy': ['milk', 'cheese', 'yogurt', 'butter', 'cream', 'sour cream', 'cottage'],
        'Grain': ['bread', 'rice', 'pasta', 'flour', 'oat', 'cereal', 'tortilla', 'noodle'],
        'Pantry': ['oil', 'vinegar', 'sauce', 'soup', 'can', 'bean', 'sugar', 'salt',
                   'spice', 'honey', 'peanut butter', 'jam', 'jelly', 'ketchup', 'mustard'],
        'Frozen': ['frozen', 'ice cream', 'pizza'],
        'Beverage': ['water', 'juice', 'soda', 'coffee', 'tea', 'beer', 'wine'],
        'Snack': ['chips', 'crackers', 'cookies', 'nuts', 'popcorn', 'candy', 'chocolate'],
    }

    def _guess_category(self, name):
        lower = name.lower()
        for cat, keywords in self._FOOD_CATEGORIES.items():
            if any(kw in lower for kw in keywords):
                return cat
        return "Other"

    def fresh_add_pantry(self, name, quantity=1, unit='pcs', category=None, price=0):
        """Add an item to the pantry."""
        if not category:
            category = self._guess_category(name)

        item = {
            "id": f"id_{_uid()}",
            "name": name,
            "emoji": "",
            "category": category,
            "quantity": int(quantity),
            "maxQuantity": max(int(quantity) * 3, 5),
            "unit": unit,
            "priceEach": float(price) if price else 0,
            "addedDate": datetime.now().isoformat()
        }

        with self._locks['freshtable']:
            data = self._load('freshtable')
            # Check if item already exists, increment quantity
            for existing in data.get('pantry', []):
                if existing['name'].lower() == name.lower():
                    existing['quantity'] += int(quantity)
                    self._save('freshtable', data)
                    return f"Updated {name}: now {existing['quantity']} {existing['unit']} in pantry."
            data['pantry'].append(item)
            self._save('freshtable', data)

        return f"Added {quantity} {unit} of {name} to the pantry ({category})."

    def fresh_list_pantry(self, filter_cat=None):
        """List pantry items, optionally filtered by category or 'low' for low stock."""
        with self._locks['freshtable']:
            data = self._load('freshtable')

        pantry = data.get('pantry', [])
        if not pantry:
            return "Pantry is empty."

        if filter_cat and filter_cat.lower() == 'low':
            pantry = [i for i in pantry if i['quantity'] <= i.get('maxQuantity', 5) * 0.3]
            if not pantry:
                return "Nothing is running low in the pantry."
            label = "Low stock items"
        elif filter_cat:
            pantry = [i for i in pantry if filter_cat.lower() in i.get('category', '').lower()]
            label = f"Pantry ({filter_cat})"
        else:
            label = "Pantry"

        lines = []
        for item in pantry:
            lines.append(f"{item['name']}: {item['quantity']} {item['unit']} ({item.get('category', '')})")

        return f"{label} ({len(pantry)} item{'s' if len(pantry) != 1 else ''}):\n" + "\n".join(lines)

    def fresh_add_shopping(self, name, quantity=1, est_price=0, category=None):
        """Add an item to the shopping list."""
        if not category:
            category = self._guess_category(name)

        item = {
            "id": f"id_{_uid()}",
            "name": name,
            "quantity": int(quantity),
            "unit": "pcs",
            "category": category,
            "estimatedPrice": float(est_price) if est_price else 0,
            "checked": False
        }

        with self._locks['freshtable']:
            data = self._load('freshtable')
            data.setdefault('shoppingList', []).append(item)
            self._save('freshtable', data)

        price_str = f", about ${float(est_price):.2f}" if est_price else ""
        return f"Added {name} to the shopping list, quantity {quantity}{price_str}."

    def fresh_list_shopping(self):
        """List current shopping list items."""
        with self._locks['freshtable']:
            data = self._load('freshtable')

        items = [i for i in data.get('shoppingList', []) if not i.get('checked')]
        if not items:
            return "Shopping list is empty."

        total = sum(i.get('estimatedPrice', 0) * i.get('quantity', 1) for i in items)
        lines = []
        for item in items:
            price = f" ~${item.get('estimatedPrice', 0):.2f}" if item.get('estimatedPrice') else ""
            lines.append(f"{item['name']}: {item['quantity']}{price}")

        result = f"Shopping list ({len(items)} item{'s' if len(items) != 1 else ''}):\n" + "\n".join(lines)
        if total > 0:
            result += f"\nEstimated total: ${total:.2f}"
        return result

    def fresh_create_recipe(self, name, ingredients, instructions):
        """Create a new recipe with name, ingredients list, and cooking instructions.

        Stores ingredients as a comma-joined string so the frontend can render
        it directly. Pass either a list or a string.
        """
        if isinstance(ingredients, list):
            ingredients_str = ", ".join(str(i).strip() for i in ingredients)
        else:
            ingredients_str = str(ingredients or "").strip()
        ingredient_count = len([i for i in ingredients_str.split(",") if i.strip()])

        recipe = {
            "id": f"r{_uid()}",
            "name": name,
            "ingredients": ingredients_str,
            "instructions": str(instructions or "").strip(),
            "createdAt": datetime.now().isoformat(),
        }

        with self._locks['freshtable']:
            data = self._load('freshtable')
            data.setdefault('recipes', []).append(recipe)
            self._save('freshtable', data)

        return f"Recipe created: {name} with {ingredient_count} ingredients. Open FreshTable > Recipes to see it."

    # ══════════════════════════════════════════════════════════════════════════
    #  FITFAM
    # ══════════════════════════════════════════════════════════════════════════

    def fit_log_workout(self, user, workout_type, duration, notes=None):
        """Log a completed workout for a family member."""
        user = user.lower().strip()

        with self._locks['fitfam']:
            data = self._load('fitfam')
            users = data.get('users', {})
            if user not in users:
                return f"Unknown user '{user}'. Available: {', '.join(users.keys())}"

            entry = {
                "date": datetime.now().isoformat(),
                "type": workout_type,
                "duration": int(duration),
                "notes": notes or ""
            }

            u = users[user]
            u.setdefault('history', []).append(entry)
            u['totalWorkouts'] = u.get('totalWorkouts', 0) + 1

            # Update streak
            today = datetime.now().date()
            yesterday = today - timedelta(days=1)
            has_yesterday = any(
                datetime.fromisoformat(h['date']).date() == yesterday
                for h in u.get('history', []) if h != entry
            )
            if has_yesterday or u.get('streak', 0) == 0:
                u['streak'] = u.get('streak', 0) + 1
            else:
                u['streak'] = 1

            self._save('fitfam', data)

        streak_str = f" That's {u['streak']} day{'s' if u['streak'] != 1 else ''} in a row!" if u['streak'] > 1 else ""
        return f"Logged {users[user]['name']}'s {int(duration)}-minute {workout_type} workout.{streak_str}"

    def fit_get_status(self, user=None):
        """Get fitness status for a user or all users."""
        with self._locks['fitfam']:
            data = self._load('fitfam')

        users = data.get('users', {})
        if user and user.lower().strip() in users:
            u = users[user.lower().strip()]
            last = ""
            if u.get('history'):
                h = u['history'][-1]
                last = f" Last workout: {h['type']} ({h['duration']}min)"
            return (f"{u['name']}: {u.get('streak', 0)}-day streak, "
                    f"{u.get('totalWorkouts', 0)} total workouts.{last}")

        lines = []
        for uid, u in users.items():
            lines.append(f"{u['name']}: {u.get('streak', 0)}-day streak, "
                         f"{u.get('totalWorkouts', 0)} total workouts")
        return "\n".join(lines) if lines else "No fitness data."

    def fit_log_set(self, user, exercise_id, weight, reps, rpe=None, notes=""):
        """Log a single structured set for an exercise.

        This is the science-based logging path: per-set tracking with
        weight, reps, and optional RPE for autoregulation.

        The set is appended to the user's `sets` history. We also bump
        their `totalWorkouts` if this is their first set today.
        """
        with self._locks['fitfam']:
            data = self._load('fitfam')
            users = data.get('users', {})
            if user not in users:
                return f"Unknown user '{user}'. Available: {', '.join(users.keys())}"

            try:
                weight = float(weight)
                reps = int(reps)
                rpe_val = float(rpe) if rpe not in (None, "") else None
            except (TypeError, ValueError):
                return "Weight, reps, and RPE must be numeric."

            entry = {
                "id": f"s{_uid()}",
                "date": datetime.now().isoformat(),
                "exerciseId": exercise_id,
                "weight": weight,
                "reps": reps,
                "rpe": rpe_val,
                "notes": notes or "",
            }

            u = users[user]
            u.setdefault('sets', []).append(entry)

            # Bump totalWorkouts only if this is the first entry today
            today = datetime.now().date().isoformat()
            existing_today = [
                s for s in u['sets'][:-1]
                if s.get('date', '').startswith(today)
            ]
            if not existing_today:
                u['totalWorkouts'] = u.get('totalWorkouts', 0) + 1
                # Update streak
                yesterday = (datetime.now().date() - timedelta(days=1)).isoformat()
                had_yesterday = any(
                    s.get('date', '').startswith(yesterday)
                    for s in u.get('sets', [])
                ) or any(
                    h.get('date', '').startswith(yesterday)
                    for h in u.get('history', [])
                )
                if had_yesterday:
                    u['streak'] = u.get('streak', 0) + 1
                else:
                    u['streak'] = 1

            self._save('fitfam', data)

        return (f"Logged: {weight}x{reps}"
                + (f" @ RPE {rpe_val}" if rpe_val else "")
                + f" of {exercise_id} for {users[user]['name']}.")

    # ══════════════════════════════════════════════════════════════════════════
    #  TRIVIA
    # ══════════════════════════════════════════════════════════════════════════

    def trivia_get_stats(self):
        """Get trivia game history and aggregate stats."""
        with self._locks['trivia']:
            data = self._load('trivia')

        results = data.get('results', [])
        if not results:
            return "No trivia games played yet."

        total = len(results)
        avg_score = sum(r.get('score', 0) for r in results) / total
        best = max(results, key=lambda r: r.get('score', 0))
        return (f"Trivia stats: {total} game{'s' if total != 1 else ''} played. "
                f"Average score: {avg_score:.1f}. "
                f"Best: {best.get('score', 0)} in {best.get('category', 'unknown')} "
                f"({best.get('difficulty', 'unknown')}).")


# ── Module-level app launcher helpers ─────────────────────────────────

# Map from app_id → HTML filename (relative to _APPS_DIR)
_APP_FILES = {
    "alarm": "alarm.html",
    "calendar": "Calendar.html",
    "freshtable": "freshtable.html",
    "fitfam": "FitFam Index.html",
    "trivia": "trivia-app.html",
}


def get_app_paths() -> dict[str, str]:
    """Return ``{app_id: absolute_path}`` for every app whose HTML file exists.

    Example::

        >>> paths = get_app_paths()
        >>> paths.get("alarm")
        '<app-dir>/Applications/alarm.html'
    """
    found: dict[str, str] = {}
    for app_id, filename in _APP_FILES.items():
        full = os.path.join(_APPS_DIR, filename)
        if os.path.isfile(full):
            found[app_id] = full
    return found


def open_app(app_id: str) -> bool:
    """Open an HTML application in the default browser.

    Returns ``True`` if the file was found and opened, ``False`` otherwise.
    """
    import webbrowser

    filename = _APP_FILES.get(app_id)
    if not filename:
        return False

    full = os.path.join(_APPS_DIR, filename)
    if not os.path.isfile(full):
        return False

    webbrowser.open(f"file:///{full}")
    return True
