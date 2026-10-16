"""
PuffPAI Security Audit Log
Append-only security event trail.

Logs bus sends, policy vetoes, vault access, and authentication
attempts with timestamps.

Storage: JSON Lines format (one JSON object per line).
Production target: tamper-proof storage with cryptographic chaining.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from puffpai.web_search import key_safe_data, key_safe_text

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _default_log_file() -> Path:
    """``<state dir>/security_logs/audit.jsonl``, resolved when a log is made.

    It used to be ``src/security_logs/audit.jsonl`` (this file's grandparent),
    inside the install folder whatever state dir the build was given.
    """
    from puffpai.config import get_state_dir
    return Path(get_state_dir()) / "security_logs" / "audit.jsonl"

_MAX_LOG_SIZE = 10 * 1024 * 1024  # 10 MB
_MAX_ROTATED_FILES = 10


class AuditLog:
    """Append-only security audit trail.

    All entries are written as JSON Lines with timestamps.
    Thread-safe for concurrent access from API server.
    """

    def __init__(self, log_file=None):
        self._log_file = Path(log_file) if log_file else _default_log_file()
        self._lock = threading.Lock()
        self._log_file.parent.mkdir(parents=True, exist_ok=True)

    def log(self, action: str, user: str = "system", result: str = "success",
            details: str = "", extra: dict | None = None) -> dict:
        """Append a security event to the audit log.

        No field ever keeps a key or a password, or a piece of one: each is
        checked WHOLE before it is cut (``web_search.key_safe_text``), and
        ``extra`` is checked string by string, key names too, and kept as
        valid JSON of at most 2,000 characters (``web_search.key_safe_data``).
        A caller hands over the whole text; cutting it first would let a key
        that straddles the cut through.
        """
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "epoch": time.time(),
            "action": key_safe_text(action, 100),
            "user": key_safe_text(user, 100),
            "result": key_safe_text(result, 50),
            "details": key_safe_text(details, 500),
        }
        if extra:
            entry["extra"] = key_safe_data(extra, max_json=2000)

        with self._lock:
            self._rotate_if_needed()
            try:
                with open(self._log_file, 'a', encoding='utf-8') as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + '\n')
            except IOError as e:
                print(f"[AUDIT FAILURE] Cannot write to {self._log_file}: {e}",
                      file=sys.stderr)
                print(f"[AUDIT ENTRY] {json.dumps(entry)}", file=sys.stderr)
        return entry

    def _rotate_if_needed(self):
        """Rotate log files when they exceed the size limit."""
        try:
            if not self._log_file.exists():
                return
            if self._log_file.stat().st_size < _MAX_LOG_SIZE:
                return
            for i in range(_MAX_ROTATED_FILES, 0, -1):
                src = self._log_file.parent / f"audit.{i}.jsonl"
                dst = self._log_file.parent / f"audit.{i + 1}.jsonl"
                if src.exists():
                    if i == _MAX_ROTATED_FILES:
                        src.unlink()
                    else:
                        src.rename(dst)
            self._log_file.rename(self._log_file.parent / "audit.1.jsonl")
        except OSError:
            pass

    def query(self, action=None, user=None, result=None,
              limit: int = 100, since_epoch: float | None = None) -> list:
        """Query recent audit log entries."""
        entries = []
        try:
            if not self._log_file.exists():
                return []
            with open(self._log_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if action and entry.get("action") != action:
                        continue
                    if user and entry.get("user") != user:
                        continue
                    if result and entry.get("result") != result:
                        continue
                    if since_epoch and entry.get("epoch", 0) < since_epoch:
                        continue
                    entries.append(entry)
        except IOError:
            pass
        entries.reverse()
        return entries[:limit]

    def get_failed_auth_count(self, window_seconds: int = 3600) -> int:
        """Count failed authentication attempts in a time window."""
        since = time.time() - window_seconds
        failures = self.query(action="vault_auth", result="failed",
                              since_epoch=since, limit=1000)
        return len(failures)

    def get_recent_activity(self, limit: int = 20) -> list:
        """Get the most recent audit log entries."""
        return self.query(limit=limit)
