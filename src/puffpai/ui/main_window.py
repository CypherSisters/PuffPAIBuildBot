"""PuffPAI main window — thin assembler for the decomposed UI.

Creates ``AppState``, wires up ``ProfileView``, ``ChatHistoryManager``,
``ChatView``, ``AeroWidgets``, and ``PuffPAITheme`` into a single
coherent application.

Backwards-compatible: the class is still called ``PersonaRAGUI`` and
exposes the same public API as the old monolith.
"""

import json
import logging
import os
import re
import threading
import uuid
import tkinter as tk
import tkinter.font as tkfont
from tkinter import scrolledtext, messagebox
from datetime import datetime

import requests
from PIL import Image, ImageTk
from puffpai.security.lan_guard import no_proxies as _no_proxies  # marked home-network calls never use a proxy

logger = logging.getLogger(__name__)

from puffpai.config import get_config as _get_config
from puffpai.router import (
    route_message as _route_message,
    RouteTarget,
)

from .theme import PuffPAITheme
from .widgets import AeroWidgets
from .app_state import AppState
from .preferences import UIPreferences
from .profile_view import ProfileView
from .chat_history import ChatHistoryManager
from .chat_view import ChatView
from .knowledge_view import KnowledgeView
from .vault_view import VaultView
from .dashboard_view import DashboardView
from .system_tray import SystemTray
from .settings_view import SettingsView
from .onboarding import OnboardingWizard


class PersonaRAGUI:
    """PuffPAI Frutiger-Aero GUI — assembled from decomposed modules."""

    def __init__(self):
        # ── State ────────────────────────────────────────────────────────
        self.state = AppState()
        s = self.state

        # ── Persistent preferences ────────────────────────────────────────
        self.prefs = UIPreferences.load()
        s.dark_mode = self.prefs.dark_mode

        # ── External dependencies (lazy) ─────────────────────────────────
        from puffpai.knowledge.rag import DualZoneRAG
        from puffpai.profile_manager import ProfileManager
        from puffpai.avatar_generator import AvatarGenerator

        s.rag = DualZoneRAG()
        s.profile_manager = ProfileManager()
        s.avatar_gen = AvatarGenerator()

        # ── Config ───────────────────────────────────────────────────────
        _cfg = _get_config()
        s.ollama_url = self.prefs.ollama_url or _cfg.ollama.url
        s.ollama_model = self.prefs.ollama_model or _cfg.ollama.model
        s.ollama_temperature = (self.prefs.ollama_temperature
                                if self.prefs.ollama_temperature != 0.7
                                else _cfg.ollama.temperature)

        os.makedirs(s.chat_history_dir, exist_ok=True)

        # ── Background service ───────────────────────────────────────────
        # The bundled REST service is not part of this build; the UI talks to
        # Ollama directly. Nothing to auto-start here.
        self._service_thread = None

        # ── Tk root ──────────────────────────────────────────────────────
        s.window = tk.Tk()
        s.window.title("PuffPAI: Privacy-Forward Personal AI")
        s.window.geometry(self.prefs.window_geometry)
        s.window.minsize(900, 600)

        # Restore maximized state
        if self.prefs.window_maximized:
            try:
                s.window.state("zoomed")
            except tk.TclError:
                pass  # Not all platforms support "zoomed"

        s.window.configure(bg=PuffPAITheme.C_BG)

        # ── Font detection ───────────────────────────────────────────────
        fonts = PuffPAITheme.detect_font(s.window)
        s._FF = fonts["_FF"]

        # ── Theme & widgets (apply detected fonts) ───────────────────────
        self.theme = PuffPAITheme()
        # Override class-level font tuples with detected family
        for key, val in fonts.items():
            if key != "_FF":
                setattr(self.theme, key, val)

        self.widgets = AeroWidgets(self.theme, s.window, font_family=s._FF)

        # ── Sub-views ────────────────────────────────────────────────────
        self.profile_view = ProfileView(
            s, self.widgets, self.theme,
            on_init_session=self._init_session)
        self.chat_history = ChatHistoryManager(s, self.widgets, self.theme)
        self.chat_view = ChatView(s, self.widgets, self.theme)
        self.knowledge_view = KnowledgeView(
            s, self.widgets, self.theme,
            on_system_message=lambda msg: self.chat_view.add_message_bubble(
                msg, is_system=True))
        self.vault_view = VaultView(
            s, self.widgets, self.theme,
            on_system_message=lambda msg: self.chat_view.add_message_bubble(
                msg, is_system=True))
        self.dashboard_view = None  # built after _setup_ui (needs widgets)
        self._dashboard_container = None
        self._chat_container = None
        self._current_view = "dashboard"  # "dashboard" | "chat"

        # ── Settings view ─────────────────────────────────────────────────
        self.settings_view = SettingsView(
            s, self.widgets, self.theme, self.prefs,
            on_theme_changed=self._on_theme_changed,
            on_save=lambda: None)

        # ── Onboarding (first-run) ────────────────────────────────────────
        self._onboarding = OnboardingWizard(
            s, self.widgets, self.theme, self.prefs)

        # ── Show profile selector (or onboarding first) ──────────────────
        if self._onboarding.should_show:
            self._onboarding._on_complete = self.profile_view.show_profile_selector
            s.window.after(200, self._onboarding.show)
        else:
            self.profile_view.show_profile_selector()

    # ── Session init (after profile selection) ───────────────────────────

    def _init_session(self):
        s = self.state
        identity = s.profile_manager.get_identity()
        s.ai_name = identity["name"]

        ai_img = s.avatar_gen.generate(
            identity["avatar_seed"], identity["avatar_style"], size=40)
        ai_img = self.widgets.circle_crop(ai_img, 40)
        s._ai_avatar_photo = ImageTk.PhotoImage(ai_img)

        # Large avatar for dashboard (128px)
        ai_img_lg = s.avatar_gen.generate(
            identity["avatar_seed"], identity["avatar_style"], size=128)
        ai_img_lg = self.widgets.circle_crop(ai_img_lg, 128)
        s._ai_avatar_large_photo = ImageTk.PhotoImage(ai_img_lg)

        user_img = s.avatar_gen.generate(
            s.current_user["avatar_seed"],
            s.current_user["avatar_style"], size=40)
        user_img = self.widgets.circle_crop(user_img, 40)
        s._user_avatar_photo = ImageTk.PhotoImage(user_img)

        s.window.title(f"PuffPAI ({s.current_user['display_name']})")

        # ── User memory ───────────────────────────────────────────────
        from puffpai.user_memory import UserMemory
        s.user_memory = UserMemory(user_id=s.current_user["id"])

        self._load_persona()
        self._setup_ui()
        self.chat_history.load_chat_list()
        self._show_dashboard()

    # ── Persona ──────────────────────────────────────────────────────────

    def _load_persona(self):
        s = self.state
        try:
            # Same file as api_server._load_persona.
            with open(os.path.join(s.src_dir, 'persona_puffpai.txt'), 'r',
                      encoding='utf-8') as f:
                raw = f.read()
            raw = raw.replace('PuffPAI', s.ai_name)
            s.persona = raw
            if s.current_user:
                s.persona += (
                    f"\n\nUSER CONTEXT: The person you are speaking with is "
                    f"called {s.current_user['display_name']}. "
                    f"Address them by name naturally when appropriate, "
                    f"not every message, but when it feels warm and personal."
                )
            # Inject user memory if available
            if s.user_memory:
                memory_ctx = s.user_memory.get_memory_context()
                if memory_ctx:
                    s.persona += "\n" + memory_ctx
        except FileNotFoundError:
            s.persona = "You are a helpful AI assistant with access to a local knowledge base."

    # ── Main UI layout ───────────────────────────────────────────────────

    def _setup_ui(self):
        s = self.state
        T = self.theme
        W = self.widgets

        try:
            logo_img = Image.open(os.path.join(s.src_dir, 'puffpai_logo_final.png'))
            logo_img.thumbnail((200, 90), Image.LANCZOS)
            s._logo_photo = ImageTk.PhotoImage(logo_img)
        except Exception:
            s._logo_photo = None

        # ── Root paned window ────────────────────────────────────────────
        main_container = tk.PanedWindow(
            s.window, orient=tk.HORIZONTAL, sashwidth=4,
            sashrelief=tk.FLAT, bg=T.C_BORDER)
        main_container.pack(fill=tk.BOTH, expand=True)

        # ── LEFT SIDEBAR ─────────────────────────────────────────────────
        left_panel = tk.Frame(main_container, width=270, bg=T.C_GLASS, bd=0)
        main_container.add(left_panel, minsize=230)

        s._header_canvas = tk.Canvas(left_panel, height=130,
                                     highlightthickness=0, bd=0)
        s._header_canvas.pack(fill=tk.X)
        s._header_canvas.bind(
            "<Configure>",
            lambda e: W.draw_header(e.widget, s._logo_photo, e.width, e.height))
        s.window.after(60, lambda: W.draw_header(s._header_canvas, s._logo_photo))

        # User profile bar
        if s.current_user:
            profile_bar = tk.Frame(left_panel, bg=T.C_GLASS)
            profile_bar.pack(fill=tk.X, padx=10, pady=(8, 4))
            if s._user_avatar_photo:
                tk.Label(profile_bar, image=s._user_avatar_photo,
                         bg=T.C_GLASS).pack(side=tk.LEFT, padx=(0, 6))
            tk.Label(profile_bar, text=s.current_user["display_name"],
                     font=T.FONT_SM_B, fg=T.C_TEXT,
                     bg=T.C_GLASS).pack(side=tk.LEFT)
            switch_btn = tk.Button(
                profile_bar, text="Switch",
                command=lambda: self.profile_view.switch_profile(
                    self.chat_history.save_current_chat),
                bg=T.C_AQUA_MID, fg=T.C_WHITE, font=T.FONT_XS,
                relief=tk.FLAT, cursor="hand2", padx=8, pady=2)
            switch_btn.pack(side=tk.RIGHT)
            W.bind_hover(switch_btn, T.C_AQUA_MID, T.C_AQUA)

        # Home button
        home_btn = tk.Button(
            left_panel, text="\U0001f3e0  Home",
            command=self._show_dashboard,
            bg=T.C_PALE, fg=T.C_NAVY, font=T.FONT_B, relief=tk.FLAT,
            cursor="hand2", pady=9, padx=14,
            activebackground=T.C_AQUA_LT, activeforeground=T.C_NAVY)
        home_btn.pack(fill=tk.X, padx=10, pady=(12, 6))
        W.bind_hover(home_btn, T.C_PALE, T.C_AQUA_LT)

        # New Chat button
        new_chat_btn = tk.Button(
            left_panel, text="\uff0b   New Chat",
            command=self._new_chat_and_show,
            bg=T.C_AQUA, fg=T.C_WHITE, font=T.FONT_B, relief=tk.FLAT,
            cursor="hand2", pady=9, padx=14,
            activebackground=T.C_AQUA_MID, activeforeground=T.C_WHITE)
        new_chat_btn.pack(fill=tk.X, padx=10, pady=(0, 6))
        W.bind_hover(new_chat_btn, T.C_AQUA, T.C_AQUA_MID)

        # Upload File button
        upload_btn = tk.Button(
            left_panel, text="\U0001f4c2  Upload File",
            command=self.knowledge_view.show_upload_dialog,
            bg=T.C_BLUE, fg=T.C_WHITE, font=T.FONT_SM, relief=tk.FLAT,
            cursor="hand2", pady=7, padx=14,
            activebackground=T.C_AQUA, activeforeground=T.C_WHITE)
        upload_btn.pack(fill=tk.X, padx=10, pady=(0, 6))
        W.bind_hover(upload_btn, T.C_BLUE, T.C_AQUA)

        # My Knowledge button
        knowledge_btn = tk.Button(
            left_panel, text="\U0001f4da  My Knowledge",
            command=self.knowledge_view.show_manager_panel,
            bg=T.C_NAVY, fg=T.C_PALE, font=T.FONT_SM, relief=tk.FLAT,
            cursor="hand2", pady=7, padx=14,
            activebackground=T.C_BLUE, activeforeground=T.C_WHITE)
        knowledge_btn.pack(fill=tk.X, padx=10, pady=(0, 6))
        W.bind_hover(knowledge_btn, T.C_NAVY, T.C_BLUE)

        # Status info
        info_frame = tk.Frame(left_panel, bg=T.C_GLASS)
        info_frame.pack(fill=tk.X, padx=12, pady=(2, 6))
        tk.Label(info_frame, text="\u25c8  Autonomous Mode",
                 font=T.FONT_SM_B, fg=T.C_NAVY,
                 bg=T.C_GLASS).pack(anchor=tk.W)
        tk.Label(info_frame,
                 text=("\u2022 Knowledge base always active\n"
                       "\u2022 Tools auto-enabled when needed\n"
                       "\u2022 Learning from your feedback"),
                 font=T.FONT_XS, fg=T.C_TEXT_MED,
                 bg=T.C_GLASS, justify=tk.LEFT).pack(anchor=tk.W, padx=6, pady=(2, 0))

        # Divider
        tk.Frame(left_panel, height=1, bg=T.C_BORDER).pack(
            fill=tk.X, padx=10, pady=8)

        # Chat History heading
        tk.Label(left_panel, text="CHAT HISTORY",
                 font=(s._FF, 8, "bold"), fg=T.C_TEXT_LT,
                 bg=T.C_GLASS).pack(anchor=tk.W, padx=12, pady=(0, 4))

        # Tile-based chat history
        list_frame = tk.Frame(left_panel, bg=T.C_GLASS)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=6, pady=(0, 6))
        tile_sb = tk.Scrollbar(list_frame, width=6)
        tile_sb.pack(side=tk.RIGHT, fill=tk.Y)

        s._tile_canvas = tk.Canvas(list_frame, bg=T.C_GLASS,
                                   highlightthickness=0, bd=0,
                                   yscrollcommand=tile_sb.set)
        s._tile_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tile_sb.config(command=s._tile_canvas.yview)

        s._tile_frame = tk.Frame(s._tile_canvas, bg=T.C_GLASS)
        s._tile_canvas_window = s._tile_canvas.create_window(
            (0, 0), window=s._tile_frame, anchor="nw")
        s._tile_frame.bind(
            "<Configure>",
            lambda e: s._tile_canvas.configure(
                scrollregion=s._tile_canvas.bbox("all")))
        s._tile_canvas.bind(
            "<Configure>",
            lambda e: s._tile_canvas.itemconfig(
                s._tile_canvas_window, width=e.width))
        def _bind_scroll(canvas):
            """Bind mousewheel scrolling only when hovering over this canvas."""
            def _on_enter(e):
                canvas.bind_all("<MouseWheel>",
                    lambda ev: canvas.yview_scroll(int(-1 * (ev.delta / 120)), "units"))
            def _on_leave(e):
                canvas.unbind_all("<MouseWheel>")
            canvas.bind("<Enter>", _on_enter)
            canvas.bind("<Leave>", _on_leave)

        _bind_scroll(s._tile_canvas)

        # Divider
        tk.Frame(left_panel, height=1, bg=T.C_BORDER).pack(
            fill=tk.X, padx=10, pady=4)

        # Bottom buttons
        vault_btn = tk.Button(
            left_panel, text="\U0001f512  Vault",
            command=self.vault_view.show_vault_dialog,
            bg=T.C_LAVENDER, fg=T.C_WHITE, font=T.FONT_SM, relief=tk.FLAT,
            cursor="hand2", pady=7)
        vault_btn.pack(fill=tk.X, padx=10, pady=4)
        W.bind_hover(vault_btn, T.C_LAVENDER, T.C_VIOLET)

        feedback_btn = tk.Button(
            left_panel, text="\u25c6  Feedback Stats",
            command=self._show_feedback_stats,
            bg=T.C_GOLD, fg=T.C_WHITE, font=T.FONT_SM, relief=tk.FLAT,
            cursor="hand2", pady=7)
        feedback_btn.pack(fill=tk.X, padx=10, pady=4)
        W.bind_hover(feedback_btn, T.C_GOLD, "#D4882A")

        settings_btn = tk.Button(
            left_panel, text="\u2699  Settings",
            command=lambda: self.settings_view.show(),
            bg=T.C_BLUE, fg=T.C_WHITE, font=T.FONT_SM, relief=tk.FLAT,
            cursor="hand2", pady=7)
        settings_btn.pack(fill=tk.X, padx=10, pady=4)
        W.bind_hover(settings_btn, T.C_BLUE, T.C_AQUA)

        about_btn = tk.Button(
            left_panel, text="\u25c9  About PuffPAI",
            command=lambda: self.settings_view.show("about"),
            bg=T.C_NAVY, fg=T.C_PALE, font=T.FONT_SM, relief=tk.FLAT,
            cursor="hand2", pady=7)
        about_btn.pack(fill=tk.X, padx=10, pady=(0, 12))
        W.bind_hover(about_btn, T.C_NAVY, T.C_BLUE)

        # ── RIGHT MAIN PANEL ─────────────────────────────────────────────
        right_panel = tk.Frame(main_container, bg=T.C_GLASS)
        main_container.add(right_panel)

        # ── Dashboard container ────────────────────────────────────────
        self._dashboard_container = tk.Frame(right_panel, bg=T.C_BG)

        self.dashboard_view = DashboardView(
            s, self.widgets, self.theme,
            on_new_chat=self._new_chat_and_show,
            on_open_chat=self._open_chat_and_show,
            on_upload_file=self.knowledge_view.show_upload_dialog,
            on_voice_mode=self._toggle_voice,
            on_open_vault=self.vault_view.show_vault_dialog,
            on_open_app=self._open_app,
            on_customize_ai=self.profile_view._show_settings_dialog,
            preferences=self.prefs,
        )
        self.dashboard_view.build(
            self._dashboard_container).pack(fill=tk.BOTH, expand=True)

        # ── Chat container ─────────────────────────────────────────────
        self._chat_container = tk.Frame(right_panel, bg=T.C_GLASS)

        # Chat header
        chat_header = tk.Frame(self._chat_container, bg=T.C_PALE, height=52)
        chat_header.pack(fill=tk.X)
        chat_header.pack_propagate(False)
        tk.Frame(chat_header, height=2, bg=T.C_AQUA_LT).pack(
            side=tk.BOTTOM, fill=tk.X)

        s.chat_title = tk.Label(chat_header, text="New Chat",
                                font=T.FONT_LG_B, fg=T.C_NAVY, bg=T.C_PALE)
        s.chat_title.pack(side=tk.LEFT, padx=20, pady=10)

        clear_btn = tk.Button(
            chat_header, text="Clear",
            command=lambda: self.chat_history.clear_current_chat(
                self.chat_view.add_message_bubble,
                self.chat_view.clear_chat_display,
                self._check_database, self._check_ollama),
            bg=T.C_RED, fg=T.C_WHITE, font=T.FONT_SM, relief=tk.FLAT,
            cursor="hand2", padx=12, pady=4)
        clear_btn.pack(side=tk.RIGHT, padx=14, pady=10)
        W.bind_hover(clear_btn, T.C_RED, "#BF3030")

        # Chat area
        chat_area = tk.Frame(self._chat_container, bg=T.C_BG)
        chat_area.pack(fill=tk.BOTH, expand=True)

        s.chat_canvas = tk.Canvas(chat_area, bg=T.C_BG,
                                  highlightthickness=0)
        chat_scrollbar = tk.Scrollbar(chat_area, orient="vertical",
                                      command=s.chat_canvas.yview)
        s.chat_frame = tk.Frame(s.chat_canvas, bg=T.C_BG)
        s.chat_canvas_window = s.chat_canvas.create_window(
            (0, 0), window=s.chat_frame, anchor="nw")
        s.chat_canvas.configure(yscrollcommand=chat_scrollbar.set)
        chat_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        s.chat_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        s.chat_canvas.bind("<Configure>", lambda e: (
            self._on_canvas_configure(e),
            W.draw_bg_bubbles(e.widget, e.width, e.height)))
        _bind_scroll(s.chat_canvas)
        s.chat_frame.bind(
            "<Configure>",
            lambda e: s.chat_canvas.configure(
                scrollregion=s.chat_canvas.bbox("all")))

        # Input area
        input_outer = tk.Frame(self._chat_container, bg=T.C_PALE)
        input_outer.pack(fill=tk.X)
        tk.Frame(input_outer, height=2, bg=T.C_AQUA_LT).pack(fill=tk.X)

        s._input_frame = tk.Frame(input_outer, bg=T.C_PALE)
        s._input_frame.pack(fill=tk.X, padx=14, pady=10)
        input_outer.bind("<Configure>", self._on_input_resize)

        tk.Label(s._input_frame,
                 text="Enter  \u2192  Send     Shift+Enter  \u2192  New Line",
                 font=T.FONT_XS_I, fg=T.C_TEXT_LT,
                 bg=T.C_PALE).pack(anchor=tk.W, pady=(0, 4))

        input_row = tk.Frame(s._input_frame, bg=T.C_PALE)
        input_row.pack(fill=tk.X)

        text_wrapper = tk.Frame(input_row, bg=T.C_BORDER, padx=1, pady=1)
        text_wrapper.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        s.input_text = scrolledtext.ScrolledText(
            text_wrapper, height=3, wrap=tk.WORD, font=T.FONT,
            bg=T.C_WHITE, fg=T.C_TEXT, relief=tk.FLAT,
            insertbackground=T.C_AQUA, selectbackground=T.C_AQUA_MID,
            selectforeground=T.C_WHITE)
        s.input_text.pack(fill=tk.BOTH, expand=True)
        s.input_text.bind("<Return>", self._on_enter_press)
        s.input_text.bind("<Shift-Return>", lambda e: None)

        # Mic toggle button
        self._mic_btn = tk.Button(
            input_row, text="\U0001f3a4", command=self._toggle_voice,
            bg=T.C_BORDER, fg=T.C_TEXT, font=(s._FF, 14),
            relief=tk.FLAT, cursor="hand2", padx=8, pady=0,
            activebackground=T.C_AQUA_MID, activeforeground=T.C_WHITE)
        self._mic_btn.pack(side=tk.LEFT, padx=(8, 0), fill=tk.Y)
        W.bind_hover(self._mic_btn, T.C_BORDER, T.C_AQUA_LT)

        send_btn = tk.Button(
            input_row, text="Send  \u2192", command=self._send_message,
            bg=T.C_BLUE, fg=T.C_WHITE, font=T.FONT_B, relief=tk.FLAT,
            cursor="hand2", padx=22, pady=0,
            activebackground=T.C_AQUA, activeforeground=T.C_WHITE)
        send_btn.pack(side=tk.LEFT, padx=(8, 0), fill=tk.Y)
        W.bind_hover(send_btn, T.C_BLUE, T.C_AQUA)

        # Status bar
        s.status_label = tk.Label(
            s.window, text="\u25cf  Ready", bd=0, relief=tk.FLAT,
            anchor=tk.W, font=T.FONT_XS, bg=T.C_STATUS_BG,
            fg=T.C_PALE, padx=12, pady=5)
        s.status_label.pack(side=tk.BOTTOM, fill=tk.X)

        self._check_database()
        self._check_ollama()

        # ── Keyboard shortcuts ────────────────────────────────────────────
        self._bind_shortcuts()

    def _bind_shortcuts(self):
        """Register global keyboard shortcuts."""
        s = self.state
        s.window.bind_all("<Control-n>", lambda e: self._new_chat_and_show())
        s.window.bind_all("<Control-comma>", lambda e: self.settings_view.show())
        s.window.bind_all("<Control-d>", lambda e: self._toggle_dark_mode())
        s.window.bind_all("<Control-m>", lambda e: self._toggle_voice())
        s.window.bind_all("<Control-q>", lambda e: self._quit_app())
        s.window.bind_all("<F1>", lambda e: self.settings_view.show("about"))

    def _toggle_dark_mode(self):
        """Toggle dark mode from keyboard shortcut."""
        if self.dashboard_view:
            self.dashboard_view.toggle_theme()

    def _on_theme_changed(self):
        """Called when theme changes via settings panel."""
        if self.dashboard_view:
            self.dashboard_view._dark = self.state.dark_mode
            self.dashboard_view.refresh()

    # ── Dashboard / Chat navigation ──────────────────────────────────────

    def _show_dashboard(self):
        """Switch the right panel to the dashboard view."""
        if self._chat_container:
            self._chat_container.pack_forget()
        if self._dashboard_container:
            self._dashboard_container.pack(fill=tk.BOTH, expand=True)
            if self.dashboard_view:
                self.dashboard_view.refresh()
        self._current_view = "dashboard"

    def _show_chat(self):
        """Switch the right panel to the chat view."""
        if self._dashboard_container:
            self._dashboard_container.pack_forget()
        if self._chat_container:
            self._chat_container.pack(fill=tk.BOTH, expand=True)
        self._current_view = "chat"

    def _new_chat_and_show(self):
        """Create a new chat and switch to chat view."""
        self.chat_history.new_chat(
            self.chat_view.add_message_bubble,
            self.chat_view.clear_chat_display)
        self._show_chat()

    def _open_chat_and_show(self, chat_id: str):
        """Open an existing chat by ID and switch to chat view."""
        self._show_chat()
        # Find the index in the filtered file list
        fname = f"{chat_id}.json"
        files = self.state._filtered_chat_files
        if fname in files:
            idx = files.index(fname)
            self.chat_history.load_chat_by_index(
                idx,
                self.chat_view.add_message_bubble,
                self.chat_view.clear_chat_display)

    def _open_app(self, app_id: str):
        """Open a bundled HTML application in the default browser.

        The bundled-apps pack is not part of this build; degrade with a note.
        """
        try:
            from puffpai.app_data import open_app as _open_app_fn
        except ImportError:
            messagebox.showinfo(
                "Apps not available",
                "The bundled apps are not part of this build.")
            return
        if not _open_app_fn(app_id):
            messagebox.showwarning("App Not Found",
                                   f"Could not find app '{app_id}'")

    # ── Canvas resize helpers ────────────────────────────────────────────

    def _on_canvas_configure(self, event):
        s = self.state
        w = event.width
        chat_w = min(w, PuffPAITheme.MAX_CHAT_WIDTH)
        x_off = max(0, (w - chat_w) // 2)
        s.chat_canvas.itemconfig(s.chat_canvas_window, width=chat_w)
        s.chat_canvas.coords(s.chat_canvas_window, x_off, 0)

    def _on_input_resize(self, event):
        pad = max(14, (event.width - PuffPAITheme.MAX_CHAT_WIDTH) // 2)
        self.state._input_frame.pack_configure(padx=pad)

    def _on_enter_press(self, event):
        if event.state & 0x1:
            return None
        self._send_message()
        return "break"

    # ── Voice toggle ──────────────────────────────────────────────────

    def _toggle_voice(self):
        """Toggle voice input mode on/off."""
        s = self.state
        T = self.theme

        if not s.voice_enabled:
            # Start voice capture
            s.voice_enabled = True
            self._mic_btn.config(bg=T.C_RED, fg=T.C_WHITE, text="\U0001f3a4")

            # Start recording in background
            self._voice_record_thread = threading.Thread(
                target=self._voice_capture, daemon=True)
            self._voice_record_thread.start()

            self.chat_view.add_message_bubble(
                "[System] Voice mode ON, speak now...", is_system=True)
        else:
            # Stop voice capture
            s.voice_enabled = False
            self._mic_btn.config(bg=T.C_BORDER, fg=T.C_TEXT, text="\U0001f3a4")

    def _voice_capture(self):
        """Record from mic and transcribe (runs on background thread)."""
        s = self.state
        try:
            from puffpai.voice.audio_devices import AudioDeviceManager, _HAS_SOUNDDEVICE

            if not _HAS_SOUNDDEVICE:
                s.window.after(0, lambda: self.chat_view.add_message_bubble(
                    "[System] Voice unavailable: sounddevice not installed",
                    is_system=True))
                s.voice_enabled = False
                s.window.after(0, lambda: self._mic_btn.config(
                    bg=self.theme.C_BORDER, fg=self.theme.C_TEXT))
                return

            import sounddevice as sd

            # Record 5 seconds
            duration = 5.0
            frames = int(duration * 16000)
            audio = sd.rec(frames, samplerate=16000, channels=1, dtype='int16')
            sd.wait()

            if not s.voice_enabled:
                return  # User cancelled

            pcm = audio.tobytes()

            # Try STT
            from puffpai.voice.stt import VoskSTT
            stt = VoskSTT()
            if stt.is_available():
                text = stt.transcribe(pcm)
                if text.strip():
                    # Put transcription in the input box
                    s.window.after(0, lambda: self._insert_transcription(text))
                else:
                    s.window.after(0, lambda: self.chat_view.add_message_bubble(
                        "[System] No speech detected, try again",
                        is_system=True))
            else:
                s.window.after(0, lambda: self.chat_view.add_message_bubble(
                    "[System] Vosk STT not available: install vosk + model",
                    is_system=True))

        except Exception as e:
            s.window.after(0, lambda: self.chat_view.add_message_bubble(
                f"[System] Voice error: {e}", is_system=True))
        finally:
            s.voice_enabled = False
            s.window.after(0, lambda: self._mic_btn.config(
                bg=self.theme.C_BORDER, fg=self.theme.C_TEXT))

    def _insert_transcription(self, text: str):
        """Insert transcribed text into the input box."""
        s = self.state
        current = s.input_text.get("1.0", "end-1c")
        if current.strip():
            s.input_text.insert("end", " " + text)
        else:
            s.input_text.delete("1.0", "end")
            s.input_text.insert("1.0", text)
        s.input_text.focus_set()

    # ── System checks ────────────────────────────────────────────────────

    def _check_database(self):
        s = self.state
        try:
            with open(os.path.join(s.src_dir, 'indexed_libraries.json'), 'r') as f:
                libs = json.load(f)
            self.chat_view.add_message_bubble(
                f"[System] Knowledge base active \u2014 {len(libs)} libraries loaded",
                is_system=True)
        except Exception:
            self.chat_view.add_message_bubble(
                "[System] WARNING: No indexed libraries found", is_system=True)

    def _check_ollama(self):
        import requests as _req
        s = self.state
        try:
            resp = _req.get(f"{s.ollama_url}/api/tags", timeout=5, proxies=_no_proxies())  # lan-egress: Ollama /api/tags on the configured LAN url
            if resp.status_code == 200:
                models = resp.json().get('models', [])
                self.chat_view.add_message_bubble(
                    f"[System] Ollama connected \u2014 {len(models)} models available",
                    is_system=True)
                for m in models:
                    self.chat_view.add_message_bubble(
                        f"  \u2014 {m['name']}", is_system=True)
                s.status_label.config(text="\u25cf  Ready \u2014 Autonomous mode active")
                return
        except Exception:
            pass
        self.chat_view.add_message_bubble(
            f"[System] ERROR: Cannot connect to Ollama at {s.ollama_url}",
            is_system=True)
        s.status_label.config(text="\u25cf  ERROR: Ollama not connected")

    # ── Message dispatch ─────────────────────────────────────────────────

    _MAX_MESSAGE_LEN = 10_000
    _MIN_SEND_INTERVAL = 0.5  # seconds between messages
    _last_send_time: float = 0.0

    def _send_message(self):
        s = self.state
        user_message = s.input_text.get("1.0", tk.END).strip()
        if not user_message:
            return

        # ── Input validation ──────────────────────────────────────
        if len(user_message) > self._MAX_MESSAGE_LEN:
            messagebox.showwarning(
                "Message Too Long",
                f"Messages are limited to {self._MAX_MESSAGE_LEN:,} characters.\n"
                f"Your message has {len(user_message):,} characters.")
            return

        import time as _time
        now = _time.time()
        if now - self._last_send_time < self._MIN_SEND_INTERVAL:
            return  # Rate limit — ignore rapid-fire sends
        self._last_send_time = now

        if len(s.conversation_history) >= 20:
            if messagebox.askyesno(
                    "Long Conversation",
                    "This conversation has 20+ messages. "
                    "Start a new chat for optimal performance?"):
                if s.current_chat_id:
                    self.chat_history.save_current_chat()
                s.current_chat_id = datetime.now().strftime("%Y%m%d_%H%M%S")
                s.current_chat_title = None
                s.conversation_history = []
                s.message_feedback = {}
                self.chat_view.clear_chat_display()
                s.chat_title.config(text="New Chat")
                self.chat_history.load_chat_list()
                s.input_text.insert("1.0", user_message)
                return

        if not s.current_chat_id:
            s.current_chat_id = datetime.now().strftime("%Y%m%d_%H%M%S")
            if not s.current_chat_title:
                s.chat_title.config(text=user_message[:50])

        s.input_text.delete("1.0", tk.END)
        self.chat_view.add_message_bubble(
            user_message, is_user=True,
            timestamp=datetime.now().isoformat())

        thread = threading.Thread(
            target=self._process_message, args=(user_message,))
        thread.daemon = True
        thread.start()

    def _process_message(self, user_message):
        s = self.state
        try:
            # Refresh persona with latest memory every 5 messages
            if s.user_memory and len(s.conversation_history) % 5 == 0:
                self._load_persona()

            route = _route_message(user_message)
            message_id = str(uuid.uuid4())
            msg_ts = datetime.now().isoformat()

            # Use streaming for default RAG/Ollama route
            use_streaming = route not in (
                RouteTarget.BRIEFING, RouteTarget.LIBRARY,
                RouteTarget.APP_LAUNCH, RouteTarget.AGENT,
                RouteTarget.HOME)

            if use_streaming:
                # Create streaming bubble on main thread, then stream tokens
                stream_handle = [None]

                def _create_bubble():
                    stream_handle[0] = self.chat_view.add_streaming_bubble(
                        message_id=message_id, timestamp=msg_ts)

                s.window.after(0, _create_bubble)
                # Brief wait for bubble creation
                import time
                time.sleep(0.05)

                answer = self.chat_view.query_ollama_streaming(
                    user_message, stream_handle[0] or {"update": lambda t: None})
            else:
                if route == RouteTarget.BRIEFING:
                    answer = self.chat_view.query_daily_briefing()
                elif route == RouteTarget.LIBRARY:
                    answer = self.chat_view.get_indexed_libraries_direct()
                elif route == RouteTarget.APP_LAUNCH:
                    answer = self._handle_app_launch(user_message)
                elif route in (RouteTarget.AGENT, RouteTarget.HOME):
                    answer = self.chat_view.query_with_agent(user_message)
                else:
                    answer = self.chat_view.query_ollama(user_message)

            answer = ChatView.clean_response(answer)

            s.conversation_history.append({
                'user': user_message,
                'assistant': answer,
                'message_id': message_id,
                'timestamp': msg_ts,
            })

            # ── Update user memory ────────────────────────────────
            if s.user_memory:
                s.user_memory.extract_and_store(user_message)
                if s.current_chat_title:
                    s.user_memory.update_recent_topic(s.current_chat_title)
                s.user_memory.save()

            self.chat_history.save_current_chat()
            s.window.after(0, self.chat_history.load_chat_list)

            if use_streaming and stream_handle[0]:
                # Finalize the streaming bubble with proper formatting
                s.window.after(0, lambda: stream_handle[0]["finish"](
                    answer, message_id))
            else:
                s.window.after(0, lambda: self.chat_view.add_message_bubble(
                    answer, is_user=False, message_id=message_id,
                    timestamp=msg_ts))

            s.window.after(0, lambda: s.status_label.config(
                text="\u25cf  Ready"))

        except requests.exceptions.ConnectionError:
            _err = (
                "Could not connect to Ollama.\n"
                f"Make sure Ollama is running at {s.ollama_url}\n"
                "You can change this in Settings (Ctrl+,)."
            )
            logger.warning("Ollama connection failed: %s", s.ollama_url)
            s.window.after(0, lambda: self.chat_view.add_message_bubble(
                f"[Connection Error] {_err}", is_system=True))
            s.window.after(0, lambda: s.status_label.config(
                text="\u25cf  Ollama not connected"))
        except requests.exceptions.Timeout:
            logger.warning("Ollama request timed out")
            s.window.after(0, lambda: self.chat_view.add_message_bubble(
                "[Timeout] The AI took too long to respond. "
                "Try a shorter question or check Ollama status.",
                is_system=True))
            s.window.after(0, lambda: s.status_label.config(
                text="\u25cf  Ready"))
        except Exception as e:
            logger.exception("Unexpected error in _process_message")
            # Show friendly message, not raw exception
            err_type = type(e).__name__
            _user_msg = {
                "JSONDecodeError": "Received an invalid response from the AI.",
                "FileNotFoundError": "A required file was not found.",
                "PermissionError": "Access denied to a required resource.",
            }.get(err_type, "Something went wrong processing your message.")
            s.window.after(0, lambda: self.chat_view.add_message_bubble(
                f"[Error] {_user_msg}", is_system=True))
            s.window.after(0, lambda: s.status_label.config(
                text="\u25cf  Ready"))

    # ── App Launcher ──────────────────────────────────────────────────────

    def _handle_app_launch(self, user_message: str) -> str:
        """Directly open a PuffPAI app and return a user-friendly message.

        Bypasses the LLM agent entirely for deterministic behaviour.
        """
        try:
            from puffpai.app_data import open_app, get_app_paths
            from puffpai.router import detect_app_launch  # type: ignore[attr-defined]
        except ImportError:
            return "The bundled apps are not part of this build."

        app_id = detect_app_launch(user_message)
        if not app_id:
            return "I couldn't determine which app to open."

        labels = {
            'trivia': ('Trivia Game', 'Pick a category and difficulty, then hit Start the Show!'),
            'calendar': ('Family Calendar', 'You can view and manage your events, tasks, and reminders.'),
            'alarm': ('AlarmFlow', 'Set timers and alarms from the app interface.'),
            'freshtable': ('FreshTable', 'Manage your pantry inventory and shopping list.'),
            'fitfam': ('FitFam', 'Track workouts, streaks, and fitness goals.'),
        }

        available = get_app_paths()
        if app_id not in available:
            name = labels.get(app_id, (app_id,))[0]
            return (f"The {name} app file wasn't found on disk. "
                    f"Make sure the Applications folder is set up.")

        if open_app(app_id):
            name, hint = labels.get(app_id, (app_id, ''))
            return f"I've opened {name} for you! {hint}"

        return f"Something went wrong opening the {app_id} app."

    # ── Dialogs ──────────────────────────────────────────────────────────

    def _show_feedback_stats(self):
        s = self.state
        if not os.path.exists(s.feedback_file):
            messagebox.showinfo(
                "Feedback Stats",
                "No feedback recorded yet.\n\n"
                "Use \U0001f44d\U0001f44e buttons to help PuffPAI learn!")
            return
        positive = negative = 0
        try:
            with open(s.feedback_file, 'r', encoding='utf-8') as f:
                for line in f:
                    entry = json.loads(line)
                    if entry['feedback'] == 'positive':
                        positive += 1
                    else:
                        negative += 1
            total = positive + negative
            pct = (positive / total * 100) if total > 0 else 0
            messagebox.showinfo("Feedback Stats",
                f"RLHF Feedback Statistics\n\n"
                f"Total Feedback: {total}\n"
                f"\U0001f44d Positive: {positive} ({pct:.1f}%)\n"
                f"\U0001f44e Negative: {negative} ({100-pct:.1f}%)")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load feedback stats: {e}")

    # ── Lifecycle ────────────────────────────────────────────────────────

    def run(self):
        s = self.state
        s.window.protocol("WM_DELETE_WINDOW", self._on_closing)

        # ── System tray ───────────────────────────────────────────────────
        self._tray = SystemTray(
            on_show=self._restore_from_tray,
            on_quit=self._quit_app,
            on_new_chat=lambda: s.window.after(0, self._new_chat_and_show),
        )
        if self.prefs.minimize_to_tray:
            self._tray.start()

        s.window.mainloop()

    def _minimize_to_tray(self):
        """Hide the window to system tray instead of closing."""
        s = self.state
        if self._tray and self._tray.available and self.prefs.minimize_to_tray:
            s.window.withdraw()
            self._tray.notify("PuffPAI is still running in the system tray.")
        else:
            self._quit_app()

    def _restore_from_tray(self):
        """Restore the window from system tray."""
        s = self.state
        s.window.after(0, lambda: (
            s.window.deiconify(),
            s.window.lift(),
            s.window.focus_force(),
        ))

    def _save_preferences(self):
        """Save all UI preferences to disk."""
        s = self.state
        try:
            is_maximized = s.window.state() == "zoomed"
            self.prefs.window_maximized = is_maximized
            if not is_maximized:
                self.prefs.window_geometry = s.window.geometry()
            self.prefs.dark_mode = s.dark_mode
            self.prefs.last_view = self._current_view
            if s.current_chat_id:
                self.prefs.last_chat_id = s.current_chat_id
            if s.current_user:
                self.prefs.last_profile = s.current_user.get("display_name", "")
            self.prefs.save()
        except Exception:
            pass

    def _quit_app(self):
        """Full application shutdown — save state, stop tray, destroy window."""
        s = self.state

        # Save conversation
        if s.current_chat_id and s.conversation_history:
            self.chat_history.save_current_chat()

        # Save preferences
        self._save_preferences()

        # Stop tray
        if hasattr(self, '_tray') and self._tray:
            self._tray.stop()

        s.window.destroy()

    def _on_closing(self):
        """Window close handler — minimize to tray or quit."""
        if self.prefs.minimize_to_tray and hasattr(self, '_tray') and self._tray and self._tray.available:
            self._minimize_to_tray()
        else:
            self._quit_app()


def launch() -> None:
    """Construct and run the desktop UI.

    Entry point for ``puffpai ui``. Requires a display session; chat talks to
    Ollama directly, and panels that need optional integrations degrade with a
    clear message rather than failing.
    """
    app = PersonaRAGUI()
    app.run()


__all__ = ["PersonaRAGUI", "launch"]
