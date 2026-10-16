"""Reusable tile components for the PuffPAI dashboard.

Provides DashboardTile using customtkinter for modern rounded cards.
Falls back gracefully to plain tkinter when CTk is unavailable.

Usage:
    tile = DashboardTile(parent, title="Voice", value="Available",
                         accent="#52B788", icon="\U0001f3a4")
    tile.grid(row=0, column=0, padx=6, pady=6, sticky="nsew")

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import tkinter as tk
from typing import Callable

from .ctk_compat import ctk, _HAS_CTK
from .theme import PuffPAITheme


class DashboardTile:
    """Base tile card for the dashboard.

    Uses CTkFrame when available, falls back to plain tk.Frame.
    """

    def __init__(
        self,
        parent,
        title: str = "",
        value: str = "",
        icon: str = "",
        accent: str = PuffPAITheme.C_AQUA,
        on_click: Callable | None = None,
        dark: bool = False,
    ):
        T = PuffPAITheme
        self.accent = accent
        self.dark = dark
        self._on_click = on_click

        # Colours based on mode
        if dark:
            bg = T.C_DARK_CARD
            hover_bg = T.C_DARK_CARD_HOVER
            fg = T.C_DARK_TEXT
            fg_sub = T.C_DARK_TEXT_MED
        else:
            bg = T.C_WHITE
            hover_bg = T.C_GLASS
            fg = T.C_TEXT
            fg_sub = T.C_TEXT_MED

        self._bg = bg
        self._hover_bg = hover_bg

        # Build the frame
        if _HAS_CTK:
            self.frame = ctk.CTkFrame(
                parent,
                corner_radius=T.CARD_CORNER_RADIUS,
                fg_color=bg,
                border_width=0,
            )
        else:
            self.frame = tk.Frame(parent, bg=bg, bd=1, relief=tk.GROOVE)

        # Accent top bar
        accent_bar = tk.Frame(self.frame, bg=accent, height=4)
        accent_bar.pack(fill=tk.X, side=tk.TOP)

        # Content area
        content = tk.Frame(self.frame, bg=bg)
        content.pack(fill=tk.BOTH, expand=True, padx=T.CARD_PADDING,
                     pady=(T.CARD_PADDING_SM, T.CARD_PADDING))

        # Icon + title row
        header = tk.Frame(content, bg=bg)
        header.pack(fill=tk.X, anchor=tk.W)

        if icon:
            self._icon_label = tk.Label(
                header, text=icon, font=("Segoe UI Emoji", 18),
                bg=bg, fg=accent)
            self._icon_label.pack(side=tk.LEFT, padx=(0, 8))

        if title:
            self._title_label = tk.Label(
                header, text=title, font=T.FONT_SM_B,
                bg=bg, fg=fg_sub, anchor=tk.W)
            self._title_label.pack(side=tk.LEFT)

        # Value
        if value:
            self._value_label = tk.Label(
                content, text=value, font=T.FONT_LG_B,
                bg=bg, fg=fg, anchor=tk.W)
            self._value_label.pack(fill=tk.X, pady=(6, 0), anchor=tk.W)

        # Store references for subclass customization
        self._content = content
        self._header = header

        # Click binding
        if on_click:
            self._bind_click_recursive(self.frame, on_click)

        # Hover effect
        if on_click:
            self._bind_hover_recursive(self.frame, bg, hover_bg)

    def _bind_click_recursive(self, widget, callback):
        """Bind click to widget and all children."""
        widget.bind("<Button-1>", lambda e: callback())
        widget.configure(cursor="hand2")
        for child in widget.winfo_children():
            self._bind_click_recursive(child, callback)

    def _bind_hover_recursive(self, widget, normal_bg, hover_bg):
        """Bind hover colour to widget and children (tk.Frame only)."""
        def _enter(e):
            for w in self._get_tk_frames(self.frame):
                try:
                    w.configure(bg=hover_bg)
                except tk.TclError:
                    pass

        def _leave(e):
            for w in self._get_tk_frames(self.frame):
                try:
                    w.configure(bg=normal_bg)
                except tk.TclError:
                    pass

        widget.bind("<Enter>", _enter)
        widget.bind("<Leave>", _leave)

    def _get_tk_frames(self, widget):
        """Get all plain tk.Frame/Label children for bg recolouring."""
        result = []
        if isinstance(widget, (tk.Frame, tk.Label)):
            result.append(widget)
        for child in widget.winfo_children():
            result.extend(self._get_tk_frames(child))
        return result

    def grid(self, **kw):
        """Delegate grid() to the underlying frame."""
        self.frame.grid(**kw)

    def pack(self, **kw):
        """Delegate pack() to the underlying frame."""
        self.frame.pack(**kw)
