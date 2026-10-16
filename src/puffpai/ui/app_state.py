"""Shared application state for PuffPAI UI.

Replaces the monolithic ``self`` on PersonaRAGUI with an explicit
dataclass so that each sub-view can access shared state without
circular imports.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
_SRC_DIR = os.path.join(_REPO_ROOT, "src", "puffpai")


def _default_chat_history_dir() -> str:
    """Same directory the API server uses: under the state dir, not the checkout."""
    from puffpai.config import get_chat_history_dir
    return get_chat_history_dir()


@dataclass
class AppState:
    """Shared context passed to every UI sub-component.

    Thin bag-of-state — no logic lives here.  Each field is set by
    ``main_window.PersonaRAGUI.__init__`` and shared via reference.
    """

    # Paths
    repo_root: str = _REPO_ROOT
    src_dir: str = _SRC_DIR
    chat_history_dir: str = field(default_factory=_default_chat_history_dir)
    feedback_file: str = field(
        default_factory=lambda: os.path.join(_REPO_ROOT, "feedback_data.jsonl"))

    # Config (set at init)
    ollama_url: str = ""
    ollama_model: str = ""
    ollama_temperature: float = 0.7

    # Identity
    ai_name: str = "PuffPAI"
    current_user: dict | None = None

    # Conversation
    persona: str = ""
    conversation_history: list = field(default_factory=list)
    current_chat_id: str | None = None
    current_chat_title: str | None = None
    message_feedback: dict = field(default_factory=dict)

    # Singleton references (set lazily)
    rag: Any = None               # DualZoneRAG instance
    profile_manager: Any = None   # ProfileManager instance
    avatar_gen: Any = None        # AvatarGenerator instance
    knowledge_ingestion: Any = None  # KnowledgeIngestion instance
    user_memory: Any = None       # UserMemory instance (lazy)
    voice_pipeline: Any = None    # VoicePipeline instance (lazy)
    voice_enabled: bool = False   # Whether voice mode is active
    vault_unlocked: bool = False  # Whether vault session is active
    dark_mode: bool = False       # Theme mode (False=light, True=dark)

    # tkinter widget references (set by setup_ui)
    window: Any = None            # tk.Tk
    chat_frame: Any = None        # tk.Frame (chat bubble container)
    chat_canvas: Any = None       # tk.Canvas (scrollable chat area)
    chat_canvas_window: int = 0   # canvas window id
    chat_title: Any = None        # tk.Label (chat title bar)
    status_label: Any = None      # tk.Label (status bar)
    input_text: Any = None        # ScrolledText

    # Photo references (keep alive for tkinter GC)
    _ai_avatar_photo: Any = None
    _ai_avatar_large_photo: Any = None  # 128px for dashboard
    _user_avatar_photo: Any = None
    _profile_avatar_photos: list = field(default_factory=list)
    _logo_photo: Any = None

    # Internal
    _filtered_chat_files: list = field(default_factory=list)
    _context_tile_idx: int | None = None
    _header_canvas: Any = None
    _tile_canvas: Any = None
    _tile_frame: Any = None
    _tile_canvas_window: int = 0
    _selector_frame: Any = None
    _input_frame: Any = None
    _FF: str = "Segoe UI"        # detected font family
