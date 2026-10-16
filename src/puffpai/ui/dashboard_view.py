"""PuffPAI Dashboard Home View — Modern Tile-Based Layout.

The default view after login. Shows a hero greeting card, responsive
status tiles, quick action tiles, recent chat cards, app launcher,
and a dark/light theme toggle.

Uses customtkinter (CTkFrame, CTkButton, CTkScrollableFrame) when
available, falling back to plain tkinter otherwise.

Usage:
    dashboard = DashboardView(state, widgets, theme, callbacks...)
    frame = dashboard.build(parent_frame)
    dashboard.refresh()

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import json
import os
import tkinter as tk
from datetime import datetime
from typing import Callable

from .ctk_compat import ctk, _HAS_CTK, get_appearance_mode, set_appearance_mode
from .theme import PuffPAITheme
from .widgets import AeroWidgets
from .preferences import UIPreferences


class DashboardView:
    """Dashboard home screen — greeting, status, quick actions, apps."""

    def __init__(self, state, widgets: AeroWidgets, theme: PuffPAITheme,
                 on_new_chat: Callable | None = None,
                 on_open_chat: Callable[[str], None] | None = None,
                 on_upload_file: Callable | None = None,
                 on_voice_mode: Callable | None = None,
                 on_open_vault: Callable | None = None,
                 on_open_app: Callable[[str], None] | None = None,
                 on_customize_ai: Callable | None = None,
                 preferences: UIPreferences | None = None):
        self.s = state
        self.w = widgets
        self.T = theme
        self._prefs = preferences
        self._on_new_chat = on_new_chat
        self._on_open_chat = on_open_chat
        self._on_upload_file = on_upload_file
        self._on_voice_mode = on_voice_mode
        self._on_open_vault = on_open_vault
        self._on_open_app = on_open_app
        self._on_customize_ai = on_customize_ai
        self._frame: tk.Frame | None = None
        self._status_frame: tk.Frame | None = None
        self._content = None  # scrollable content container
        # Restore dark mode from persisted preferences
        self._dark = preferences.dark_mode if preferences else False

    # ── Public API ─────────────────────────────────────────────────────

    def build(self, parent: tk.Frame) -> tk.Frame:
        """Build the dashboard into *parent*. Returns the outer frame."""
        if _HAS_CTK:
            return self._build_modern(parent)
        return self._build_legacy(parent)

    def refresh(self):
        """Rebuild the full dashboard content."""
        if self._content and self._frame:
            # Clear and rebuild
            for w in self._content.winfo_children():
                w.destroy()
            self._populate_content(self._content)

    @property
    def is_dark(self) -> bool:
        return self._dark

    def toggle_theme(self):
        """Toggle between dark and light theme and persist the choice."""
        self._dark = not self._dark
        mode = "Dark" if self._dark else "Light"
        set_appearance_mode(mode)
        # Store in app state
        if hasattr(self.s, 'dark_mode'):
            self.s.dark_mode = self._dark
        # Persist to disk immediately
        if self._prefs:
            self._prefs.update(dark_mode=self._dark)
        self.refresh()

    # ── Modern build (customtkinter) ─────────────────────────────────

    def _build_modern(self, parent):
        """Build the tile-based dashboard using customtkinter."""
        T = self.T

        bg = T.C_DARK_BG if self._dark else T.C_BG
        self._frame = ctk.CTkFrame(parent, fg_color=bg, corner_radius=0)

        # CTkScrollableFrame replaces Canvas+Scrollbar
        self._content = ctk.CTkScrollableFrame(
            self._frame, fg_color=bg, corner_radius=0)
        self._content.pack(fill=tk.BOTH, expand=True, padx=0, pady=0)

        self._populate_content(self._content)
        return self._frame

    # ── Legacy build (plain tkinter) ─────────────────────────────────

    def _build_legacy(self, parent):
        """Build the dashboard using plain tkinter (fallback)."""
        T = self.T

        self._frame = tk.Frame(parent, bg=T.C_BG)

        # Scrollable canvas
        canvas = tk.Canvas(self._frame, bg=T.C_BG, highlightthickness=0)
        scrollbar = tk.Scrollbar(self._frame, orient="vertical",
                                 command=canvas.yview)
        content = tk.Frame(canvas, bg=T.C_BG)

        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        canvas_window = canvas.create_window((0, 0), window=content,
                                             anchor="nw")

        content.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind(
            "<Configure>",
            lambda e: canvas.itemconfig(canvas_window, width=e.width))
        canvas.bind(
            "<MouseWheel>",
            lambda e: canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"))

        self._content = content
        self._populate_content(content)

        # Bottom spacing
        tk.Frame(content, height=24, bg=T.C_BG).pack()

        return self._frame

    # ── Populate content (shared by modern + legacy) ────────────────

    def _populate_content(self, parent):
        """Build all dashboard sections into the content area."""
        # Theme toggle header
        self._build_theme_toggle(parent)

        # Hero greeting card
        self._build_greeting_card(parent)

        # Status tiles
        self._status_frame = self._make_section_frame(parent)
        self._build_status_cards(self._status_frame)

        # Quick actions
        self._build_quick_actions(parent)

        # Recent chats
        self._build_recent_chats(parent)

        # App launcher
        self._build_app_launcher(parent)

    # ── Theme toggle ─────────────────────────────────────────────────

    def _build_theme_toggle(self, parent):
        T = self.T
        dark = self._dark
        bg = T.C_DARK_BG if dark else T.C_BG
        fg = T.C_DARK_TEXT if dark else T.C_NAVY

        header = tk.Frame(parent, bg=bg)
        header.pack(fill=tk.X, padx=24, pady=(12, 0))

        # Spacer pushes toggle to the right
        tk.Label(header, text="Dashboard", font=T.FONT_LG_B,
                 fg=fg, bg=bg).pack(side=tk.LEFT)

        # Theme toggle button
        icon = "\u2600" if dark else "\U0001f319"  # ☀ / 🌙
        label = "Light" if dark else "Dark"

        if _HAS_CTK:
            toggle_btn = ctk.CTkButton(
                header, text=f"{icon} {label}",
                command=self.toggle_theme,
                fg_color=T.C_DARK_CARD if dark else T.C_PALE,
                text_color=T.C_DARK_TEXT if dark else T.C_NAVY,
                hover_color=T.C_DARK_CARD_HOVER if dark else T.C_GLASS,
                corner_radius=T.CARD_CORNER_RADIUS_SM,
                font=T.FONT_SM,
                width=80, height=28)
            toggle_btn.pack(side=tk.RIGHT)
        else:
            toggle_btn = tk.Button(
                header, text=f"{icon} {label}",
                command=self.toggle_theme,
                bg=T.C_PALE, fg=T.C_NAVY,
                font=T.FONT_SM, relief=tk.FLAT,
                cursor="hand2", padx=8, pady=2)
            toggle_btn.pack(side=tk.RIGHT)

    # ── Greeting card (Hero tile) ────────────────────────────────────

    def _build_greeting_card(self, parent):
        T = self.T
        s = self.s
        dark = self._dark

        card_bg = T.C_DARK_CARD if dark else T.C_PALE
        text_fg = T.C_DARK_TEXT if dark else T.C_NAVY
        sub_fg = T.C_DARK_TEXT_MED if dark else T.C_TEXT_MED

        if _HAS_CTK:
            card = ctk.CTkFrame(parent, corner_radius=T.HERO_CORNER_RADIUS,
                                fg_color=card_bg)
        else:
            card = tk.Frame(parent, bg=card_bg)

        card.pack(fill=tk.X, padx=24, pady=(16, 16))

        inner = tk.Frame(card, bg=card_bg)
        inner.pack(fill=tk.X, padx=24, pady=20)

        # Accent stripe
        accent_bar = tk.Frame(inner, bg=T.C_AQUA, height=3)
        accent_bar.pack(fill=tk.X, pady=(0, 14))

        content = tk.Frame(inner, bg=card_bg)
        content.pack(fill=tk.X)

        # AI avatar — prefer the large 128px version for dashboard
        avatar_photo = getattr(s, '_ai_avatar_large_photo', None) or \
                       s._ai_avatar_photo
        if avatar_photo:
            tk.Label(content, image=avatar_photo,
                     bg=card_bg).pack(side=tk.LEFT, padx=(0, 16))

        text_frame = tk.Frame(content, bg=card_bg)
        text_frame.pack(side=tk.LEFT, fill=tk.X, expand=True)

        hour = datetime.now().hour
        greeting = self._get_time_greeting(hour)
        user_name = (s.current_user["display_name"]
                     if s.current_user else "there")

        tk.Label(text_frame, text=f"{greeting}, {user_name}!",
                 font=T.FONT_XL_B, fg=text_fg, bg=card_bg,
                 anchor=tk.W).pack(anchor=tk.W)

        ai_name = s.ai_name or "PuffPAI"
        tk.Label(
            text_frame,
            text=(f"I'm {ai_name}, your privacy-first AI assistant. "
                  f"How can I help you today?"),
            font=T.FONT_SM, fg=sub_fg, bg=card_bg,
            wraplength=420, justify=tk.LEFT, anchor=tk.W,
        ).pack(anchor=tk.W, pady=(4, 0))

        # Customize AI button
        if self._on_customize_ai:
            if _HAS_CTK:
                customize_btn = ctk.CTkButton(
                    text_frame, text="\u2699  Customize AI",
                    command=self._on_customize_ai,
                    fg_color=T.C_AQUA_MID,
                    hover_color=T.C_AQUA,
                    text_color=T.C_WHITE,
                    corner_radius=T.CARD_CORNER_RADIUS_SM,
                    font=T.FONT_XS,
                    height=28, width=120)
                customize_btn.pack(anchor=tk.W, pady=(8, 0))
            else:
                customize_btn = tk.Button(
                    text_frame, text="\u2699  Customize AI",
                    command=self._on_customize_ai,
                    bg=T.C_AQUA_MID, fg=T.C_WHITE, font=T.FONT_XS,
                    relief=tk.FLAT, cursor="hand2", padx=10, pady=4)
                customize_btn.pack(anchor=tk.W, pady=(8, 0))
                self.w.bind_hover(customize_btn, T.C_AQUA_MID, T.C_AQUA)

    @staticmethod
    def _get_time_greeting(hour: int) -> str:
        """Return a time-of-day greeting string."""
        if hour < 5:
            return "Good night"
        elif hour < 12:
            return "Good morning"
        elif hour < 17:
            return "Good afternoon"
        elif hour < 21:
            return "Good evening"
        else:
            return "Good night"

    # ── Status tiles ─────────────────────────────────────────────────

    def _build_status_cards(self, parent):
        T = self.T
        dark = self._dark

        # Clear previous content
        for w in parent.winfo_children():
            w.destroy()

        card_bg = T.C_DARK_CARD if dark else T.C_WHITE
        text_fg = T.C_DARK_TEXT if dark else T.C_TEXT
        sub_fg = T.C_DARK_TEXT_MED if dark else T.C_TEXT_MED

        # Section header
        header_bg = T.C_DARK_BG if dark else T.C_BG
        tk.Label(parent, text="System Status", font=T.FONT_SM_B,
                 fg=T.C_DARK_TEXT if dark else T.C_NAVY,
                 bg=header_bg).pack(anchor=tk.W, pady=(0, 8))

        row = tk.Frame(parent, bg=header_bg)
        row.pack(fill=tk.X)

        # Knowledge
        kb_count, kb_chunks = self._get_knowledge_stats()
        self._status_card_tile(
            row, "\U0001f4da", "Knowledge",
            f"{kb_count} libraries \u2022 {kb_chunks} chunks",
            T.C_BLUE, card_bg, text_fg, sub_fg, status_ok=(kb_count > 0))

        # Voice
        voice_ok = self._check_voice_available()
        self._status_card_tile(
            row, "\U0001f3a4", "Voice",
            "Available" if voice_ok else "Not installed",
            T.C_GREEN if voice_ok else T.C_DARK_TEXT_LT,
            card_bg, text_fg, sub_fg, status_ok=voice_ok)


        # Vault
        vault_unlocked = getattr(self.s, 'vault_unlocked', False)
        vault_status = "Unlocked" if vault_unlocked else "Locked"
        self._status_card_tile(
            row, "\U0001f512", "Vault", vault_status,
            T.C_LAVENDER, card_bg, text_fg, sub_fg,
            status_ok=vault_unlocked)

    def _status_card_tile(self, parent, icon, title, value, accent,
                          card_bg, text_fg, sub_fg, status_ok=True):
        T = self.T
        dark = self._dark

        if _HAS_CTK:
            card = ctk.CTkFrame(parent, corner_radius=T.CARD_CORNER_RADIUS,
                                fg_color=card_bg)
        else:
            card = tk.Frame(parent, bg=card_bg, bd=1, relief=tk.GROOVE)

        card.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 8),
                  pady=2)

        # Accent top bar
        tk.Frame(card, height=3, bg=accent).pack(fill=tk.X)

        body = tk.Frame(card, bg=card_bg)
        body.pack(fill=tk.BOTH, padx=T.CARD_PADDING_SM, pady=T.CARD_PADDING_SM)

        # Header row with icon, title, status dot
        header = tk.Frame(body, bg=card_bg)
        header.pack(fill=tk.X)

        tk.Label(header, text=icon, font=("Segoe UI Emoji", 14),
                 fg=accent, bg=card_bg).pack(side=tk.LEFT, padx=(0, 6))
        tk.Label(header, text=title, font=T.FONT_SM_B,
                 fg=text_fg, bg=card_bg).pack(side=tk.LEFT)

        # Status dot
        dot_color = T.C_GREEN if status_ok else T.C_DARK_TEXT_LT
        tk.Label(header, text="\u25cf", font=("Segoe UI", 8),
                 fg=dot_color, bg=card_bg).pack(side=tk.RIGHT)

        # Value
        tk.Label(body, text=value, font=T.FONT_XS,
                 fg=sub_fg, bg=card_bg, anchor=tk.W).pack(
            anchor=tk.W, pady=(4, 0))

    # ── Quick actions ────────────────────────────────────────────────

    def _build_quick_actions(self, parent):
        T = self.T
        dark = self._dark
        bg = T.C_DARK_BG if dark else T.C_BG
        fg = T.C_DARK_TEXT if dark else T.C_NAVY

        section = tk.Frame(parent, bg=bg)
        section.pack(fill=tk.X, padx=24, pady=(0, 16))

        tk.Label(section, text="Quick Actions", font=T.FONT_SM_B,
                 fg=fg, bg=bg).pack(anchor=tk.W, pady=(0, 8))

        row = tk.Frame(section, bg=bg)
        row.pack(fill=tk.X)

        actions = [
            ("\uff0b  New Chat", T.C_AQUA, self._on_new_chat),
            ("\U0001f4c2  Upload File", T.C_BLUE, self._on_upload_file),
            ("\U0001f3a4  Voice Mode", T.C_GREEN, self._on_voice_mode),
            ("\U0001f512  Vault", T.C_LAVENDER, self._on_open_vault),
        ]

        for text, color, cmd in actions:
            if _HAS_CTK:
                btn = ctk.CTkButton(
                    row, text=text,
                    command=cmd if cmd else lambda: None,
                    fg_color=color,
                    hover_color=self.w.tint(color, 0.2) if not dark
                                else self.w.tint(color, -0.2),
                    text_color=T.C_WHITE,
                    corner_radius=T.CARD_CORNER_RADIUS_SM,
                    font=T.FONT_SM,
                    height=36)
                btn.pack(side=tk.LEFT, padx=(0, 6), fill=tk.X, expand=True)
            else:
                btn = tk.Button(
                    row, text=text, bg=color, fg=T.C_WHITE,
                    font=T.FONT_SM, relief=tk.FLAT, cursor="hand2",
                    padx=12, pady=8,
                    command=cmd if cmd else lambda: None)
                btn.pack(side=tk.LEFT, padx=(0, 6))
                self.w.bind_hover(btn, color, T.C_AQUA_MID)

    # ── Recent chats ─────────────────────────────────────────────────

    def _build_recent_chats(self, parent):
        T = self.T
        dark = self._dark
        bg = T.C_DARK_BG if dark else T.C_BG
        fg = T.C_DARK_TEXT if dark else T.C_NAVY

        section = tk.Frame(parent, bg=bg)
        section.pack(fill=tk.X, padx=24, pady=(0, 16))

        tk.Label(section, text="Recent Conversations", font=T.FONT_SM_B,
                 fg=fg, bg=bg).pack(anchor=tk.W, pady=(0, 8))

        recent = self._get_recent_chats(3)

        if not recent:
            empty_fg = T.C_DARK_TEXT_LT if dark else T.C_TEXT_LT
            tk.Label(section,
                     text="No conversations yet. Start a new chat!",
                     font=T.FONT_SM, fg=empty_fg, bg=bg).pack(
                anchor=tk.W, pady=4)
            return

        for idx, chat in enumerate(recent):
            chat_id = chat.get("id", "")
            title = chat.get("title", "Untitled Chat")
            preview = chat.get("preview", "")
            date = chat.get("date", "")

            accent = T.TILE_ACCENTS[idx % len(T.TILE_ACCENTS)]
            card_bg = T.C_DARK_CARD if dark else T.C_WHITE
            card_hover = T.C_DARK_CARD_HOVER if dark else T.C_GLASS
            text_fg = T.C_DARK_TEXT if dark else T.C_NAVY
            sub_fg = T.C_DARK_TEXT_MED if dark else T.C_TEXT_LT

            if _HAS_CTK:
                card = ctk.CTkFrame(section, corner_radius=14,
                                    fg_color=card_bg)
            else:
                card = tk.Frame(section, bg=card_bg, cursor="hand2")

            card.pack(fill=tk.X, pady=3)

            # Accent left border (vertical bar)
            accent_bar = tk.Frame(card, bg=accent, width=4)
            accent_bar.pack(side=tk.LEFT, fill=tk.Y)

            body = tk.Frame(card, bg=card_bg)
            body.pack(side=tk.LEFT, fill=tk.BOTH, expand=True,
                      padx=12, pady=8)

            tk.Label(body, text=title[:60], font=T.FONT_SM_B,
                     fg=text_fg, bg=card_bg, anchor=tk.W).pack(anchor=tk.W)
            if preview:
                short = preview[:80] + ("..." if len(preview) > 80 else "")
                tk.Label(body, text=short, font=T.FONT_XS,
                         fg=sub_fg, bg=card_bg, anchor=tk.W).pack(
                    anchor=tk.W)
            if date:
                tk.Label(body, text=date[:10], font=T.FONT_XS,
                         fg=T.C_DARK_TEXT_LT if dark else T.C_BORDER,
                         bg=card_bg, anchor=tk.E).pack(anchor=tk.E)

            # Click to open chat
            if self._on_open_chat:
                _cid = chat_id
                for widget in [card, body] + body.winfo_children():
                    widget.bind(
                        "<Button-1>",
                        lambda e, cid=_cid: self._on_open_chat(cid))
                    try:
                        widget.configure(cursor="hand2")
                    except tk.TclError:
                        pass

    # ── App launcher ─────────────────────────────────────────────────

    def _build_app_launcher(self, parent):
        T = self.T
        dark = self._dark
        bg = T.C_DARK_BG if dark else T.C_BG
        fg = T.C_DARK_TEXT if dark else T.C_NAVY

        section = tk.Frame(parent, bg=bg)
        section.pack(fill=tk.X, padx=24, pady=(0, 16))

        tk.Label(section, text="Apps", font=T.FONT_SM_B,
                 fg=fg, bg=bg).pack(anchor=tk.W, pady=(0, 8))

        row = tk.Frame(section, bg=bg)
        row.pack(fill=tk.X)

        apps = [
            ("\u23f0", "AlarmFlow", T.C_PEACH, "alarm"),
            ("\U0001f4c5", "Calendar", T.C_BLUE, "calendar"),
            ("\U0001f966", "FreshTable", T.C_GREEN, "freshtable"),
            ("\U0001f4aa", "FitFam", T.C_ROSE, "fitfam"),
            ("\u2753", "Trivia", T.C_VIOLET, "trivia"),
        ]

        for icon, name, color, app_id in apps:
            if _HAS_CTK:
                tile = ctk.CTkFrame(row, corner_radius=T.CARD_CORNER_RADIUS,
                                    fg_color=color)
            else:
                tile = tk.Frame(row, bg=color, cursor="hand2")

            tile.pack(side=tk.LEFT, padx=(0, 8), fill=tk.BOTH, expand=True)

            inner = tk.Frame(tile, bg=color)
            inner.pack(padx=12, pady=10)

            tk.Label(inner, text=icon, font=("Segoe UI Emoji", 22),
                     fg=T.C_WHITE, bg=color).pack()
            tk.Label(inner, text=name, font=T.FONT_XS,
                     fg=T.C_WHITE, bg=color).pack(pady=(2, 0))

            if self._on_open_app:
                _aid = app_id
                for widget in [tile, inner] + inner.winfo_children():
                    widget.bind(
                        "<Button-1>",
                        lambda e, aid=_aid: self._on_open_app(aid))
                    try:
                        widget.configure(cursor="hand2")
                    except tk.TclError:
                        pass

    # ── Helper: make section frame ───────────────────────────────────

    def _make_section_frame(self, parent):
        bg = self.T.C_DARK_BG if self._dark else self.T.C_BG
        frame = tk.Frame(parent, bg=bg)
        frame.pack(fill=tk.X, padx=24, pady=(0, 16))
        return frame

    # ── Data helpers ─────────────────────────────────────────────────

    def _get_knowledge_stats(self) -> tuple[int, int]:
        """Return (library_count, total_chunks) from indexed_libraries.json."""
        try:
            path = os.path.join(self.s.src_dir, "indexed_libraries.json")
            with open(path, "r") as f:
                libs = json.load(f)
            chunks = sum(lib.get("chunks", 0) for lib in libs)
            return len(libs), chunks
        except Exception:
            return 0, 0

    @staticmethod
    def _check_voice_available() -> bool:
        """True if sounddevice is importable."""
        try:
            import sounddevice  # noqa: F401
            return True
        except (ImportError, OSError):
            return False

    def _get_recent_chats(self, count: int = 3) -> list[dict]:
        """Load the most recent *count* chat metadata dicts."""
        s = self.s
        chats: list[dict] = []
        try:
            if not os.path.isdir(s.chat_history_dir):
                return []

            user_slug = ""
            if s.current_user:
                user_slug = (s.current_user.get("display_name", "")
                             .lower().replace(" ", "_"))

            files = sorted(
                [f for f in os.listdir(s.chat_history_dir)
                 if f.endswith(".json")
                 and (not user_slug or user_slug in f)],
                reverse=True,
            )

            for fname in files[:count]:
                try:
                    path = os.path.join(s.chat_history_dir, fname)
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)

                    messages = (data if isinstance(data, list)
                                else data.get("messages", []))
                    title = (data.get("title", "")
                             if isinstance(data, dict) else "")

                    if not title and messages:
                        first_user = next(
                            (m.get("user", "") for m in messages
                             if m.get("user")), "")
                        title = first_user[:50] if first_user else "Untitled"

                    preview = ""
                    if messages:
                        last = messages[-1]
                        preview = last.get("assistant",
                                           last.get("user", ""))[:100]

                    chat_id = fname.replace(".json", "")
                    date = ""
                    if messages and messages[-1].get("timestamp"):
                        date = messages[-1]["timestamp"]

                    chats.append({
                        "id": chat_id,
                        "title": title or "Untitled Chat",
                        "preview": preview,
                        "date": date,
                    })
                except Exception:
                    continue

        except Exception:
            pass

        return chats
