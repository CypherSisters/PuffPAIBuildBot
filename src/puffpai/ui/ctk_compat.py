"""customtkinter compatibility layer for PuffPAI.

Provides a graceful import wrapper so the dashboard can use modern
CTk widgets when available, falling back to plain tkinter otherwise.

Usage:
    from puffpai.ui.ctk_compat import ctk, _HAS_CTK

    if _HAS_CTK:
        frame = ctk.CTkFrame(parent, corner_radius=16)
    else:
        frame = tk.Frame(parent)

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

try:
    import customtkinter as ctk

    _HAS_CTK = True
except ImportError:
    ctk = None  # type: ignore[assignment]
    _HAS_CTK = False


def get_appearance_mode() -> str:
    """Return current CTk appearance mode ('Light' or 'Dark').

    Falls back to 'Light' when customtkinter is unavailable.
    """
    if _HAS_CTK:
        return ctk.get_appearance_mode()
    return "Light"


def set_appearance_mode(mode: str) -> None:
    """Set CTk appearance mode ('Light', 'Dark', or 'System').

    No-op when customtkinter is unavailable.
    """
    if _HAS_CTK:
        ctk.set_appearance_mode(mode)


__all__ = ["ctk", "_HAS_CTK", "get_appearance_mode", "set_appearance_mode"]
