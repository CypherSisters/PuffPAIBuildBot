"""`puffpai chat` stops when the app's secret key is unusable.

With a 13-character PUFFPAI_API_KEY the
chat command printed "FATAL: refusing to start" twice and then answered anyway,
from a bare model call with none of the build's facts or honesty guard. The
line said it refused; it did not. Now it does, and says so once, plainly.

Run in a child process so the failed import cannot disturb the API server
module other tests have already loaded.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _chat(tmp_path: Path, key: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.update({
        "PYTHONPATH": str(ROOT / "src"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PUFFPAI_DATA_DIR": str(tmp_path / "data"),
        "PUFFPAI_STATE_DIR": str(tmp_path / "state"),
        "PUFFPAI_API_KEY": key,
        # Nothing may reach a real model from this test.
        "OLLAMA_HOST": "http://127.0.0.1:9",
        "PUFFPAI_OLLAMA_URL": "http://127.0.0.1:9",
    })
    return subprocess.run(
        [sys.executable, "-m", "puffpai", "chat", "--once", "what can you do?"],
        cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=120)


@pytest.mark.parametrize("key", ["too-short-13c", "changeme"])
def test_chat_stops_when_the_key_is_unusable(tmp_path, key):
    run = _chat(tmp_path, key)
    out = run.stdout + run.stderr
    assert run.returncode == 1, out
    assert "Chat did not start" in out
    # Said once, not twice, and nothing answered after it.
    assert out.count("refusing to start") == 1, out
    assert out.rstrip().endswith("then run this again.")
