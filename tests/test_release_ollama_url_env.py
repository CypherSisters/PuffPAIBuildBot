"""``PUFFPAI_OLLAMA_URL`` actually overrides the configured Ollama address.

docs/INSTALL_LINUX.md and docs/INSTALL_WINDOWS.md both promise this variable —
"the address from config/puffpai_config.json; PUFFPAI_OLLAMA_URL overrides it" —
and until now nothing read it. ``OllamaConfig.url`` defaulted to
``http://localhost:11434`` and ``_build_config`` took the JSON value and nothing
else, so an isolated run aimed at a spare port silently talked to the household's
real Ollama on 11434.

The security-relevant half is the LAN posture. An override must not become a way
to send every prompt in the household to a public host without any egress door
seeing it, so the env value is applied BEFORE ``lan_posture.enforce()`` and gets
exactly the treatment a JSON value gets: on a non-``enumerated`` posture a public
address is neutralised to loopback and reported.

Every test builds a config against a throwaway data dir; nothing binds a port
and no Ollama is contacted.
"""

import json
import os

import pytest

from puffpai import config as config_mod
from puffpai.security import lan_posture


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """No inherited override, and no leaked run-posture from another file."""
    monkeypatch.delenv("PUFFPAI_OLLAMA_URL", raising=False)
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    lan_posture.clear_active_posture()
    lan_posture.record_load_violations([])
    yield
    lan_posture.clear_active_posture()
    lan_posture.record_load_violations([])


def _root(tmp_path, ollama_url=None, run_egress=None):
    """A throwaway repo root with a config dir, returning the root path."""
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    body = {}
    if ollama_url is not None:
        body["ollama"] = {"url": ollama_url}
    (cfg_dir / "puffpai_config.json").write_text(json.dumps(body),
                                                 encoding="utf-8")
    if run_egress is not None:
        (cfg_dir / "build_manifest.json").write_text(
            json.dumps({"posture": {"build_network": "online",
                                    "run_egress": run_egress}}),
            encoding="utf-8")
    return str(tmp_path)


def _build(tmp_path, monkeypatch, **kw):
    root = _root(tmp_path, **kw)
    monkeypatch.setenv("PUFFPAI_DATA_DIR", root)
    return config_mod._build_config(repo_root=root)


# ── the reader ──────────────────────────────────────────────────────────────

def test_env_reader_prefers_the_explicit_variable(monkeypatch):
    monkeypatch.setenv("PUFFPAI_OLLAMA_URL", "http://127.0.0.1:11499")
    monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:11555")
    assert config_mod.ollama_url_from_env() == ("http://127.0.0.1:11499",
                                                "PUFFPAI_OLLAMA_URL")


def test_env_reader_accepts_ollama_host_only_as_a_url(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:11555")
    assert config_mod.ollama_url_from_env() == ("http://127.0.0.1:11555",
                                                "OLLAMA_HOST")


@pytest.mark.parametrize("bare", [
    "127.0.0.1:11434",   # what the ollama CLI wants there
    "localhost",
    "0.0.0.0:11434",
    "",
    "   ",
])
def test_bare_ollama_host_is_a_probe_hint_not_a_backend(monkeypatch, bare):
    """A host:port set for the ollama CLI must not silently redirect every
    household prompt. Only a spelled-out URL counts."""
    monkeypatch.setenv("OLLAMA_HOST", bare)
    assert config_mod.ollama_url_from_env() is None


def test_no_variables_means_the_config_file_stands():
    assert config_mod.ollama_url_from_env() is None


def test_whitespace_is_stripped(monkeypatch):
    monkeypatch.setenv("PUFFPAI_OLLAMA_URL", "  http://127.0.0.1:11499  ")
    url, src = config_mod.ollama_url_from_env()
    assert url == "http://127.0.0.1:11499"
    assert src == "PUFFPAI_OLLAMA_URL"


# ── the override reaches the built config ───────────────────────────────────

def test_config_file_value_is_used_with_no_env(tmp_path, monkeypatch):
    cfg = _build(tmp_path, monkeypatch, ollama_url="http://127.0.0.1:11434")
    assert cfg.ollama.url == "http://127.0.0.1:11434"


def test_env_overrides_the_config_file(tmp_path, monkeypatch):
    monkeypatch.setenv("PUFFPAI_OLLAMA_URL", "http://127.0.0.1:11499")
    cfg = _build(tmp_path, monkeypatch, ollama_url="http://127.0.0.1:11434")
    assert cfg.ollama.url == "http://127.0.0.1:11499"


def test_env_overrides_the_dataclass_default(tmp_path, monkeypatch):
    """No ollama block in the file at all: the env value still wins over the
    hardcoded http://localhost:11434."""
    monkeypatch.setenv("PUFFPAI_OLLAMA_URL", "http://127.0.0.1:11499")
    cfg = _build(tmp_path, monkeypatch)
    assert cfg.ollama.url == "http://127.0.0.1:11499"


def test_ollama_host_url_overrides_the_config_file(tmp_path, monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:11555")
    cfg = _build(tmp_path, monkeypatch, ollama_url="http://127.0.0.1:11434")
    assert cfg.ollama.url == "http://127.0.0.1:11555"


def test_the_raw_block_is_kept_in_step(tmp_path, monkeypatch):
    """`/system/status` and the wizard read the raw block; a stale value there
    would report an address nothing is talking to."""
    monkeypatch.setenv("PUFFPAI_OLLAMA_URL", "http://127.0.0.1:11499")
    cfg = _build(tmp_path, monkeypatch, ollama_url="http://127.0.0.1:11434")
    assert cfg._raw["ollama"]["url"] == "http://127.0.0.1:11499"


# ── the override is NOT a posture bypass ────────────────────────────────────

def test_public_env_url_is_refused_under_island(tmp_path, monkeypatch):
    """The whole point: an environment variable must not be the one way to
    ship every prompt off the LAN unseen."""
    monkeypatch.setenv("PUFFPAI_OLLAMA_URL", "https://someone-elses-box.example.com")
    cfg = _build(tmp_path, monkeypatch, ollama_url="http://127.0.0.1:11434",
                 run_egress="island")
    assert cfg.ollama.url == "http://localhost:11434", (
        "a public env-supplied Ollama must be neutralised to loopback")
    services = {v.service for v in lan_posture.load_time_violations()}
    assert "ollama" in services, "the refusal must be reported, not silent"


def test_public_env_url_is_refused_under_sealed(tmp_path, monkeypatch):
    monkeypatch.setenv("PUFFPAI_OLLAMA_URL", "http://192.168.1.50:11434")
    cfg = _build(tmp_path, monkeypatch, run_egress="sealed")
    assert cfg.ollama.url == "http://localhost:11434", (
        "sealed allows loopback only, env-supplied or not")


def test_loopback_env_url_survives_every_posture(tmp_path, monkeypatch):
    for posture in ("sealed", "island", "enumerated"):
        lan_posture.record_load_violations([])
        monkeypatch.setenv("PUFFPAI_OLLAMA_URL", "http://127.0.0.1:11499")
        cfg = _build(tmp_path, monkeypatch, run_egress=posture)
        assert cfg.ollama.url == "http://127.0.0.1:11499", posture


# ── the source is logged ────────────────────────────────────────────────────

def test_startup_log_names_the_source(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("PUFFPAI_OLLAMA_URL", "http://127.0.0.1:11499")
    with caplog.at_level("INFO", logger="puffpai.config"):
        _build(tmp_path, monkeypatch)
    line = [r.getMessage() for r in caplog.records
            if "Ollama backend:" in r.getMessage()]
    assert line, "config load must say where the Ollama address came from"
    assert "http://127.0.0.1:11499" in line[-1]
    assert "PUFFPAI_OLLAMA_URL" in line[-1]


def test_startup_log_names_the_config_file_when_no_env(tmp_path, monkeypatch, caplog):
    with caplog.at_level("INFO", logger="puffpai.config"):
        _build(tmp_path, monkeypatch, ollama_url="http://127.0.0.1:11434")
    line = [r.getMessage() for r in caplog.records
            if "Ollama backend:" in r.getMessage()][-1]
    assert "puffpai_config.json" in line
    assert "PUFFPAI_OLLAMA_URL" not in line


def test_startup_log_says_refused_when_the_posture_wins(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("PUFFPAI_OLLAMA_URL", "https://someone-elses-box.example.com")
    with caplog.at_level("INFO", logger="puffpai.config"):
        _build(tmp_path, monkeypatch, run_egress="island")
    line = [r.getMessage() for r in caplog.records
            if "Ollama backend:" in r.getMessage()][-1]
    assert "REFUSED" in line, (
        "the log must not credit a source for a value that was overruled")


def test_get_config_force_reload_picks_up_the_variable(tmp_path, monkeypatch):
    """The singleton is built once; a test or a restart must see the new value."""
    root = _root(tmp_path, ollama_url="http://127.0.0.1:11434")
    monkeypatch.setenv("PUFFPAI_DATA_DIR", root)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PUFFPAI_OLLAMA_URL", "http://127.0.0.1:11499")
    try:
        cfg = config_mod.get_config(_force_reload=True)
        assert cfg.ollama.url == "http://127.0.0.1:11499"
    finally:
        # Never leave a temp-dir singleton behind for the next test file.
        config_mod._instance = None
        os.environ.pop("PUFFPAI_OLLAMA_URL", None)
