"""
PuffPAI Centralized Configuration
==================================
Single source of truth for all config loading.

    from puffpai.config import get_config

    cfg = get_config()
    cfg.ollama.url        # "http://localhost:11434"
    cfg.REPO_ROOT         # absolute path to the repo root
    cfg.ha.enabled        # True/False

The config is loaded once (singleton) and shared across the whole process.
Missing config files are handled gracefully with sensible defaults.

Data location:
    By default config and data live under the repo root. Set the
    ``PUFFPAI_DATA_DIR`` environment variable to keep config, the vault,
    and knowledge data outside the source tree (e.g. an installed package).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


def _find_repo_root() -> str:
    """Walk up from this file (src/puffpai/config.py) to the repo root."""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _find_data_dir(repo_root: str) -> str:
    """Resolve the data directory.

    Defaults to the repo root; overridable with ``PUFFPAI_DATA_DIR`` so
    config, vault, and knowledge data can live outside the source tree.
    """
    override = os.environ.get("PUFFPAI_DATA_DIR")
    return os.path.abspath(override) if override else repo_root


def _user_state_root() -> str:
    """The platform's per-user application-state directory for PuffPAI.

    Same convention ``puffpai.ui.preferences`` already uses, so a build has one
    place for per-user state rather than two:

        Windows:  %LOCALAPPDATA%/PuffPAI
        macOS:    ~/Library/Application Support/PuffPAI
        Linux:    $XDG_STATE_HOME/puffpai, else ~/.local/state/puffpai
    """
    import sys

    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(base, "PuffPAI")
    if sys.platform == "darwin":
        return os.path.join(
            os.path.expanduser("~"), "Library", "Application Support", "PuffPAI")
    xdg = os.environ.get("XDG_STATE_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "state")
    return os.path.join(xdg, "puffpai")


def get_state_dir(*, create: bool = False) -> str:
    """Absolute path of the directory for *machine-local secret state*.

    This is deliberately NOT ``DATA_DIR``. ``DATA_DIR`` defaults to the repo
    root, so anything written there lands inside the source checkout and is one
    stray ``git add`` away from being published. Credentials the box mints for
    itself (the app API key) live here instead:

        * ``$PUFFPAI_STATE_DIR`` when set (tests and packagers pin it),
        * else ``$PUFFPAI_DATA_DIR/state`` when the data dir was moved out of
          the tree on purpose — one override keeps everything together,
        * else the per-user OS state directory (:func:`_user_state_root`),
          which is outside any checkout on every platform.

    With ``create=True`` the directory is created 0700 on POSIX. On Windows the
    mode argument is ignored by the OS; the directory inherits the ACL of the
    user profile (already user-only for a standard profile), and callers that
    write secrets tighten the file itself — see ``api_server._write_key_file``.
    """
    override = os.environ.get("PUFFPAI_STATE_DIR")
    if override:
        path = os.path.abspath(override)
    else:
        data_override = os.environ.get("PUFFPAI_DATA_DIR")
        path = (os.path.join(os.path.abspath(data_override), "state")
                if data_override else _user_state_root())
    if create:
        os.makedirs(path, mode=0o700, exist_ok=True)
        try:
            os.chmod(path, 0o700)  # no-op on Windows; meaningful on POSIX
        except OSError:  # pragma: no cover - platform dependent
            pass
    return path


def _configured_chat_history_dir(configured: str | None) -> str:
    """The ``paths.chat_history_dir`` value: as passed, else from the config."""
    if configured is None:
        try:
            configured = get_config().paths.chat_history_dir
        except Exception:
            configured = None
    return configured or "chat_history"


def get_chat_history_dir(configured: str | None = None) -> str:
    """Absolute path of the directory saved conversations live in.

    Conversations are the owner's words, so they belong with the rest of the
    owner's state, not beside the code. A relative ``paths.chat_history_dir``
    (the default, ``"chat_history"``) is resolved against
    :func:`get_state_dir`; an absolute value is honoured as written. It used to
    resolve against ``REPO_ROOT``, which put conversations in the checkout even
    with ``PUFFPAI_STATE_DIR`` set, and shared them between every build run
    from the same checkout.

    ``configured`` is the config value; when omitted it is read from the loaded
    config. The directory is not created here.
    """
    configured = _configured_chat_history_dir(configured)
    if os.path.isabs(configured):
        return os.path.abspath(configured)
    return os.path.abspath(os.path.join(get_state_dir(), configured))


def legacy_chat_history_dir(configured: str | None = None) -> str | None:
    """Where a relative ``chat_history_dir`` USED to resolve: under the repo root.

    ``None`` when the configured value is absolute (it never moved) or when the
    old and new locations are the same directory.
    """
    configured = _configured_chat_history_dir(configured)
    if os.path.isabs(configured):
        return None
    legacy = os.path.abspath(os.path.join(_find_repo_root(), configured))
    current = get_chat_history_dir(configured)
    if os.path.normcase(legacy) == os.path.normcase(current):
        return None
    return legacy


@dataclass
class KiwixConfig:
    """Kiwix HTTP server for offline knowledge packs (optional)."""
    url: str = ""
    timeout: int = 10


@dataclass
class OllamaConfig:
    url: str = "http://localhost:11434"
    model: str = "qwen2.5:7b-instruct"
    temperature: float = 0.6
    num_ctx: int = 8192
    num_predict: int = 512


@dataclass
class NabuConfig:
    """Voice pipeline settings (Nabu Casa / Home Assistant voice)."""
    enabled: bool = False
    ha_voice_pipeline: str = "puffpai_voice"
    wake_word: str = "hey puffpai"
    voice_max_length: int = 500


@dataclass
class IdentityConfig:
    name: str = "PuffPAI"
    avatar_style: str = "notionists"
    avatar_seed: str = "PuffPAI"


@dataclass
class HAConfig:
    """Home Assistant connection config.

    Loaded from ``config/home_assistant_config.json`` (gitignored), which the
    first-run wizard writes.
    """
    url: str = "http://homeassistant.local:8123"
    token: str = ""
    enabled: bool = False


@dataclass
class ModelConfig:
    """Centralized model paths and inference parameters."""
    llm_gguf_path: str = ""
    vosk_model_dir: str = "models/vosk-model-en-us"
    piper_model_path: str = "models/en_US-hfc_female-medium.onnx"
    wake_word_model_path: str = "models/hey_puffpai.tflite"
    embedding_model: str = "all-MiniLM-L6-v2"
    llm_n_ctx: int = 2048
    llm_n_gpu_layers: int = 0


@dataclass
class PathConfig:
    """Centralized data and storage paths (resolved against the data dir).

    ``chat_history_dir`` is the exception: relative values resolve against the
    state dir - see :func:`get_chat_history_dir`.
    """
    zim_dirs: list[str] = field(default_factory=list)
    zim_files: list[str] = field(default_factory=list)
    chromadb_path: str = "./kiwix_rag_db"
    vault_data_dir: str = "vault_data"
    chat_history_dir: str = "chat_history"
    feedback_data_path: str = "feedback_data.jsonl"


@dataclass
class KnowledgeConfig:
    """Knowledge-pack auto-indexing settings.

    ``zim_dir`` is the folder PuffPAI watches for ``.zim`` knowledge packs; when
    ``auto_index`` is on, new packs found there are indexed in the background on
    startup. Empty ``zim_dir`` disables the watch (manual ``knowledge sync`` still
    works with an explicit path).

    ``data_dir`` is the owner's one data folder (``knowledge/my_data.py``):
    downloads and her own files (``<data_dir>/My files``). It is THE setting;
    ``zim_dir`` is written with the same value for older readers, and is used
    when ``data_dir`` is empty. ``linked_folders`` are her own folders, read
    in place (``{id, path, name, added_at}``); ``previous_dirs`` are earlier
    data folders, still read; files over ``max_file_mb`` are skipped.
    """
    zim_dir: str = ""
    auto_index: bool = True
    data_dir: str = ""
    linked_folders: list = field(default_factory=list)
    previous_dirs: list = field(default_factory=list)
    max_file_mb: int = 25


@dataclass
class N8nWebhooks:
    """Webhook paths for the shipped n8n workflow templates.

    Each path matches a template in ``n8n_workflows/``. Import a template into
    your own n8n instance and it listens on the matching path.
    """
    weather_alerts: str = "/webhook/weather-alerts"
    puffpai_voice: str = "/webhook/puffpai_voice"
    daily_news: str = "/webhook/daily-news"
    daily_weather: str = "/webhook/daily-weather"
    calendar_sync: str = "/webhook/calendar-sync"
    meal_planner: str = "/webhook/meal-planner"
    grocery_list: str = "/webhook/grocery-list"
    daily_briefing: str = "/webhook/daily-briefing"
    health_check: str = "/webhook/health-check"
    media_now_playing: str = "/webhook/media-now-playing"


@dataclass
class N8nConfig:
    """Optional n8n workflow engine (Majordomo background work).

    ``url`` points at your own self-hosted n8n instance. n8n is fully optional;
    when it is unreachable the Majordomo degrades cleanly.
    """
    enabled: bool = False
    url: str = "http://localhost:5678"
    timeout: int = 10
    webhooks: N8nWebhooks = field(default_factory=N8nWebhooks)


@dataclass
class ApiServerConfig:
    """REST API server bind address (used by ``puffpai serve``)."""
    host: str = "127.0.0.1"
    port: int = 8000


@dataclass
class DashboardConfig:
    """Address of the Next.js dashboard, for anything that hands the owner off.

    ``url`` wins when it is set; otherwise ``host`` and ``port`` are joined.
    The shipped default is not a guessed port: it is the port
    ``dashboard/package.json`` binds both ``npm run dev`` and ``npm start`` to,
    so the default address matches what the shipped dashboard actually serves.
    An owner who runs it somewhere else sets ``dashboard.url`` in
    ``config/puffpai_config.json`` (or ``PUFFPAI_DASHBOARD_URL``) and that wins.
    """
    url: str = ""
    host: str = "localhost"
    port: int = 3003


@dataclass
class MediaServerConfig:
    """Optional media server (Jellyfin + Calibre-Web)."""
    enabled: bool = False
    jellyfin_url: str = "http://localhost:8096"
    jellyfin_api_key: str = ""
    calibre_db_path: str = ""
    calibre_web_url: str = "http://localhost:8083"
    media_mount: str = ""
    timeout: int = 10


@dataclass
class HomeConfig:
    """Where home is, roughly. Unset (``None``) means unset: no default place.

    Written by ``puffpai home set``; see :mod:`puffpai.home_location`, which
    validates and rounds on every read. Only a rounded position (one decimal,
    about 11 km), the town label and the IANA time zone are ever stored.
    """
    label: str = ""
    latitude: float | None = None
    longitude: float | None = None
    timezone: str = ""
    source: str = ""
    set_at: str = ""


@dataclass
class PuffPAIConfig:
    """Root configuration object. All fields have safe defaults."""
    REPO_ROOT: str = ""
    DATA_DIR: str = ""
    SRC_DIR: str = ""
    CONFIG_DIR: str = ""

    kiwix: KiwixConfig = field(default_factory=KiwixConfig)
    ollama: OllamaConfig = field(default_factory=OllamaConfig)
    nabu: NabuConfig = field(default_factory=NabuConfig)
    identity: IdentityConfig = field(default_factory=IdentityConfig)
    ha: HAConfig = field(default_factory=HAConfig)
    models: ModelConfig = field(default_factory=ModelConfig)
    paths: PathConfig = field(default_factory=PathConfig)
    knowledge: KnowledgeConfig = field(default_factory=KnowledgeConfig)
    media_server: MediaServerConfig = field(default_factory=MediaServerConfig)
    api_server: ApiServerConfig = field(default_factory=ApiServerConfig)
    dashboard: DashboardConfig = field(default_factory=DashboardConfig)
    n8n: N8nConfig = field(default_factory=N8nConfig)
    web_search_consent: bool = False
    location: str = ""
    home: HomeConfig = field(default_factory=HomeConfig)
    _raw: dict[str, Any] = field(default_factory=dict, repr=False)


def _load_json(path: str) -> dict[str, Any]:
    """Load a JSON file, returning {} on any failure."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not load %s: %s", path, exc)
        return {}


def _safe_init(cls: Any, data: dict[str, Any]) -> Any:
    """Create a dataclass instance, ignoring unknown keys."""
    valid_keys = {f.name for f in cls.__dataclass_fields__.values()}
    return cls(**{k: v for k, v in data.items() if k in valid_keys})


def ollama_url_from_env() -> tuple[str, str] | None:
    """An environment-supplied Ollama address as ``(url, variable)``, or None.

    ``PUFFPAI_OLLAMA_URL`` is the documented override (docs/INSTALL_LINUX.md,
    docs/INSTALL_WINDOWS.md) and wins. It exists so a run can be aimed at
    another Ollama — a second instance on a spare port, an isolated test — without
    editing the household's config file.

    ``OLLAMA_HOST`` is accepted only when the owner actually wrote a URL.
    Ollama's own CLI takes a bare ``host:port`` there, and ``brain_health``
    treats it as a *probe hint*: one address among several to try. Promoting a
    bare host:port to "this is the configured backend" would silently change
    where every prompt in the household goes on a box where the variable was set
    for the CLI's benefit, so the scheme is required.

    Returns None when neither is usable, i.e. the JSON value stands.
    """
    explicit = (os.environ.get("PUFFPAI_OLLAMA_URL") or "").strip()
    if explicit:
        return explicit, "PUFFPAI_OLLAMA_URL"
    host = (os.environ.get("OLLAMA_HOST") or "").strip()
    if host.lower().startswith(("http://", "https://")):
        return host, "OLLAMA_HOST"
    return None


def _build_config(repo_root: str | None = None) -> PuffPAIConfig:
    """Build a PuffPAIConfig from disk."""
    if repo_root is None:
        repo_root = _find_repo_root()

    data_dir = _find_data_dir(repo_root)
    src_dir = os.path.join(repo_root, "src", "puffpai")
    config_dir = os.path.join(data_dir, "config")

    raw = _load_json(os.path.join(config_dir, "puffpai_config.json"))

    ha_d = _load_json(os.path.join(config_dir, "home_assistant_config.json"))
    media_d = _load_json(os.path.join(config_dir, "media_server_config.json"))

    n8n_d = raw.get("n8n", {})
    webhooks_d = n8n_d.pop("webhooks", {}) if isinstance(n8n_d, dict) else {}

    dashboard_d = raw.get("dashboard", {})
    dashboard = _safe_init(
        DashboardConfig, dashboard_d if isinstance(dashboard_d, dict) else {})
    # ``cli._dashboard_url`` resolves the address off the *raw* block rather
    # than this dataclass, so the resolved values are written back into ``raw``.
    # Without it a config file with no ``dashboard`` section leaves the wizard's
    # closing screen with nothing to offer but "start it with npm run dev".
    raw["dashboard"] = {"url": dashboard.url, "host": dashboard.host,
                        "port": dashboard.port}

    built = PuffPAIConfig(
        REPO_ROOT=repo_root,
        DATA_DIR=data_dir,
        SRC_DIR=src_dir,
        CONFIG_DIR=config_dir,
        kiwix=_safe_init(KiwixConfig, raw.get("kiwix", {})),
        ollama=_safe_init(OllamaConfig, raw.get("ollama", {})),
        nabu=_safe_init(NabuConfig, raw.get("nabu", {})),
        identity=_safe_init(IdentityConfig, raw.get("identity", {})),
        ha=_safe_init(HAConfig, ha_d),
        models=_safe_init(ModelConfig, raw.get("models", {})),
        paths=_safe_init(PathConfig, raw.get("paths", {})),
        knowledge=_safe_init(KnowledgeConfig, raw.get("knowledge", {})),
        media_server=_safe_init(MediaServerConfig, media_d),
        api_server=_safe_init(ApiServerConfig, raw.get("api_server", {})),
        dashboard=dashboard,
        n8n=N8nConfig(
            **{k: v for k, v in n8n_d.items()
               if k in N8nConfig.__dataclass_fields__ and k != "webhooks"},
            webhooks=_safe_init(N8nWebhooks, webhooks_d),
        ),
        web_search_consent=bool(raw.get("web_search_consent", False)),
        location=raw.get("location", ""),
        home=_safe_init(HomeConfig, raw.get("home") if isinstance(
            raw.get("home"), dict) else {}),
        _raw=raw,
    )

    # ── Ollama address override from the environment ──
    # Applied BEFORE the posture check below, deliberately: an env-supplied
    # address gets exactly the same LAN-posture treatment as one typed into the
    # config file. An override is not a bypass — `PUFFPAI_OLLAMA_URL` pointing
    # at a public host on an `island` box is neutralised to loopback and
    # reported, the same as if it had been in the JSON. Putting it after would
    # have made an environment variable the one way to send every household
    # prompt off the LAN without any door seeing it.
    _env_ollama = ollama_url_from_env()
    _ollama_source = "config/puffpai_config.json"
    if _env_ollama is not None:
        built.ollama.url, _ollama_source = _env_ollama
        # Keep the raw block in step; the wizard and `/system/status` read it.
        if isinstance(raw.get("ollama"), dict):
            raw["ollama"]["url"] = built.ollama.url
        else:
            raw["ollama"] = {"url": built.ollama.url}

    # ── LAN posture ──
    # The `island` posture promises "your home network, nothing beyond it", and
    # until now nothing checked that promise against the addresses in this very
    # file. An `ollama.url` pointing at a public host is unrestricted egress
    # that no egress door sees, carrying every prompt in the household.
    #
    # This neutralises rather than raises: a bad string in a config file must
    # not be enough to stop the appliance booting. A violating optional service
    # is switched off; one without an enable flag is reset to its safe default.
    # The name-form check is used, never the resolving one — config load must
    # never block on a resolver. `/system/status` re-checks with DNS and
    # reports both what it finds and what was neutralised here.
    try:
        from puffpai.security import lan_posture
        _violations = lan_posture.enforce(
            built, lan_posture.posture_from_config_dir(config_dir))
        lan_posture.record_load_violations(_violations)
        if any(v.service == "ollama" for v in _violations):
            # enforce() reset the address. Say so rather than naming a source
            # for a value that is no longer the one that source supplied.
            _ollama_source = f"{_ollama_source}, REFUSED by LAN posture"
    except Exception as exc:  # posture policy must never gate startup
        logger.debug("LAN posture check unavailable: %s", exc)

    # One line naming the address every prompt goes to and where it came from.
    # Silence here is how "PUFFPAI_OLLAMA_URL is set but ignored" went unnoticed.
    logger.info("Ollama backend: %s (from %s)", built.ollama.url, _ollama_source)

    return built


_instance: PuffPAIConfig | None = None


def get_config(*, _force_reload: bool = False) -> PuffPAIConfig:
    """Return the global PuffPAIConfig singleton.

    Pass _force_reload=True in tests to rebuild from disk.
    """
    global _instance
    if _instance is None or _force_reload:
        _instance = _build_config()
    return _instance


# ---------------------------------------------------------------------------
# LLM backend factory
# ---------------------------------------------------------------------------

_backend_instance: Any = None


def get_llm_backend(*, _force_reload: bool = False) -> Any:
    """Return the best available LLMBackend based on config.

    Priority:
      1. LlamaCppBackend  — if ``models.llm_gguf_path`` is set and the file exists
      2. OllamaBackend    — if ``ollama.url`` is reachable
      3. Raise RuntimeError

    The result is cached as a singleton.  Pass ``_force_reload=True`` in
    tests to rebuild.
    """
    global _backend_instance
    if _backend_instance is not None and not _force_reload:
        return _backend_instance

    cfg = get_config()

    # Attempt 1: llama.cpp with a local GGUF model
    gguf = cfg.models.llm_gguf_path
    if gguf:
        resolved = gguf if os.path.isabs(gguf) else os.path.join(cfg.DATA_DIR, gguf)
        if os.path.isfile(resolved):
            from puffpai.inference.llamacpp_backend import LlamaCppBackend
            backend = LlamaCppBackend(
                model_path=resolved,
                n_ctx=cfg.models.llm_n_ctx,
                n_gpu_layers=cfg.models.llm_n_gpu_layers,
            )
            if backend.is_available():
                _backend_instance = backend
                return _backend_instance

    # Attempt 2: Ollama HTTP server
    from puffpai.inference.ollama_backend import OllamaBackend
    ollama = OllamaBackend(url=cfg.ollama.url, model=cfg.ollama.model,
                           num_ctx=cfg.ollama.num_ctx,
                           num_predict=cfg.ollama.num_predict,
                           timeout=120)
    if ollama.is_available():
        _backend_instance = ollama
        return _backend_instance

    raise RuntimeError(
        "No LLM backend available. Set models.llm_gguf_path to a valid "
        "GGUF file or start Ollama at " + cfg.ollama.url
    )


def get_voice_models() -> dict[str, str]:
    """Return resolved absolute paths for voice model assets.

    Returns a dict with keys ``vosk_model_dir``, ``piper_model_path``,
    and ``wake_word_model_path``.  Relative paths are resolved against
    ``DATA_DIR``.
    """
    cfg = get_config()
    root = cfg.DATA_DIR

    def _resolve(p: str) -> str:
        return p if os.path.isabs(p) else os.path.join(root, p)

    return {
        "vosk_model_dir": _resolve(cfg.models.vosk_model_dir),
        "piper_model_path": _resolve(cfg.models.piper_model_path),
        "wake_word_model_path": _resolve(cfg.models.wake_word_model_path),
    }
