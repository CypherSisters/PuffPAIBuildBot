"""PuffPAI Onboarding Wizard — first-run setup experience.

Shown when no preferences file exists (fresh install). Guides the user
through:
  1. Welcome screen
  2. Ollama connection setup + test
  3. Theme preference (light/dark)
  4. Quick feature tour

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import threading
import tkinter as tk
from typing import Callable

from .theme import PuffPAITheme
from .widgets import AeroWidgets
from .preferences import UIPreferences
from puffpai.security.lan_guard import no_proxies as _no_proxies  # marked home-network calls never use a proxy


class OnboardingWizard:
    """Multi-step onboarding wizard for first-time users."""

    def __init__(self, state, widgets: AeroWidgets, theme: PuffPAITheme,
                 preferences: UIPreferences,
                 on_complete: Callable | None = None):
        self.s = state
        self.w = widgets
        self.T = theme
        self.prefs = preferences
        self._on_complete = on_complete
        self._step = 0
        self._dlg = None
        self._content = None

    @property
    def should_show(self) -> bool:
        """True if this looks like a first run (no saved prefs on disk)."""
        import os
        return not os.path.isfile(self.prefs._file_path)

    def show(self):
        """Display the onboarding wizard as a modal dialog."""
        self._dlg = dlg = tk.Toplevel(self.s.window)
        dlg.title("Welcome to PuffPAI")
        dlg.geometry("560x440")
        dlg.configure(bg=self.T.C_BG)
        dlg.resizable(False, False)
        dlg.transient(self.s.window)
        dlg.grab_set()
        dlg.protocol("WM_DELETE_WINDOW", self._skip)

        # Progress bar
        self._progress = tk.Canvas(dlg, height=4, bg=self.T.C_GLASS,
                                   highlightthickness=0)
        self._progress.pack(fill=tk.X)

        # Content area
        self._content = tk.Frame(dlg, bg=self.T.C_BG)
        self._content.pack(fill=tk.BOTH, expand=True)

        # Navigation
        nav = tk.Frame(dlg, bg=self.T.C_GLASS, height=52)
        nav.pack(fill=tk.X, side=tk.BOTTOM)
        nav.pack_propagate(False)

        self._skip_btn = tk.Button(
            nav, text="Skip", font=self.T.FONT_SM,
            bg=self.T.C_GLASS, fg=self.T.C_TEXT_LT, relief=tk.FLAT,
            cursor="hand2", padx=12, command=self._skip)
        self._skip_btn.pack(side=tk.LEFT, padx=12, pady=10)

        self._next_btn = tk.Button(
            nav, text="Next \u2192", font=self.T.FONT_B,
            bg=self.T.C_AQUA, fg=self.T.C_WHITE, relief=tk.FLAT,
            cursor="hand2", padx=20, pady=4, command=self._next)
        self._next_btn.pack(side=tk.RIGHT, padx=12, pady=10)
        self.w.bind_hover(self._next_btn, self.T.C_AQUA, self.T.C_AQUA_MID)

        self._back_btn = tk.Button(
            nav, text="\u2190 Back", font=self.T.FONT_SM,
            bg=self.T.C_PALE, fg=self.T.C_NAVY, relief=tk.FLAT,
            cursor="hand2", padx=12, command=self._back)
        self._back_btn.pack(side=tk.RIGHT, padx=4, pady=10)

        self._show_step()

    # ── Steps ─────────────────────────────────────────────────────────

    TOTAL_STEPS = 4

    def _show_step(self):
        # Clear content
        for w in self._content.winfo_children():
            w.destroy()

        # Progress bar
        self._progress.delete("all")
        pct = (self._step + 1) / self.TOTAL_STEPS
        pw = self._progress.winfo_width() or 560
        self._progress.create_rectangle(0, 0, int(pw * pct), 4,
                                        fill=self.T.C_AQUA, outline="")

        # Navigation state
        self._back_btn.pack_forget() if self._step == 0 else self._back_btn.pack(
            side=tk.RIGHT, padx=4, pady=10)

        if self._step == self.TOTAL_STEPS - 1:
            self._next_btn.config(text="Get Started \u2192")
        else:
            self._next_btn.config(text="Next \u2192")

        [self._step_welcome, self._step_ollama,
         self._step_theme, self._step_tour][self._step]()

    def _step_welcome(self):
        T = self.T
        f = self._content

        tk.Label(f, text="\U0001f44b", font=("Segoe UI Emoji", 48),
                 bg=T.C_BG).pack(pady=(30, 10))
        tk.Label(f, text="Welcome to PuffPAI!",
                 font=(self.s._FF, 22, "bold"), fg=T.C_NAVY,
                 bg=T.C_BG).pack()
        tk.Label(f,
                 text="Your privacy-first, offline AI assistant.",
                 font=T.FONT_SM, fg=T.C_TEXT_MED, bg=T.C_BG).pack(pady=(4, 20))

        features = [
            "\u2714  All AI processing happens locally on your device",
            "\u2714  Nothing leaves your computer without your consent.",
            "\u2714  Smart home integration with Home Assistant",
            "\u2714  Custom knowledge base from your own files",
            "\u2714  Voice control with offline speech recognition",
        ]
        for feat in features:
            tk.Label(f, text=feat, font=T.FONT_SM, fg=T.C_TEXT,
                     bg=T.C_BG, anchor=tk.W).pack(anchor=tk.W, padx=80, pady=2)

    def _step_ollama(self):
        T = self.T
        f = self._content

        tk.Label(f, text="\U0001f916", font=("Segoe UI Emoji", 36),
                 bg=T.C_BG).pack(pady=(20, 8))
        tk.Label(f, text="Connect Your AI Model",
                 font=T.FONT_XL_B, fg=T.C_NAVY, bg=T.C_BG).pack()
        tk.Label(f,
                 text="PuffPAI uses Ollama to run AI models locally.\n"
                      "Make sure Ollama is running on your machine.",
                 font=T.FONT_SM, fg=T.C_TEXT_MED, bg=T.C_BG,
                 justify=tk.CENTER).pack(pady=(4, 16))

        # URL input
        input_frame = tk.Frame(f, bg=T.C_BG)
        input_frame.pack(pady=4)
        tk.Label(input_frame, text="Ollama URL:", font=T.FONT_SM_B,
                 fg=T.C_TEXT, bg=T.C_BG).pack(side=tk.LEFT, padx=(0, 8))
        self._ob_url_var = tk.StringVar(
            value=self.prefs.ollama_url or self.s.ollama_url or "http://localhost:11434")
        entry = tk.Entry(input_frame, textvariable=self._ob_url_var,
                         font=T.FONT, bg=T.C_WHITE, fg=T.C_TEXT,
                         insertbackground=T.C_AQUA, relief=tk.FLAT, width=28)
        entry.pack(side=tk.LEFT, ipady=4)

        # Model input
        model_frame = tk.Frame(f, bg=T.C_BG)
        model_frame.pack(pady=4)
        tk.Label(model_frame, text="Model:", font=T.FONT_SM_B,
                 fg=T.C_TEXT, bg=T.C_BG).pack(side=tk.LEFT, padx=(0, 8))
        self._ob_model_var = tk.StringVar(
            value=self.prefs.ollama_model or self.s.ollama_model or "qwen2.5:7b-instruct")
        entry = tk.Entry(model_frame, textvariable=self._ob_model_var,
                         font=T.FONT, bg=T.C_WHITE, fg=T.C_TEXT,
                         insertbackground=T.C_AQUA, relief=tk.FLAT, width=28)
        entry.pack(side=tk.LEFT, ipady=4)

        # Test button + status
        test_frame = tk.Frame(f, bg=T.C_BG)
        test_frame.pack(pady=(12, 4))
        self._ob_status = tk.Label(test_frame, text="", font=T.FONT_SM,
                                   fg=T.C_TEXT_LT, bg=T.C_BG)
        self._ob_status.pack(side=tk.RIGHT, padx=(8, 0))
        test_btn = tk.Button(
            test_frame, text="\u26a1 Test Connection", font=T.FONT_SM_B,
            bg=T.C_MINT, fg=T.C_WHITE, relief=tk.FLAT,
            cursor="hand2", padx=16, pady=6,
            command=self._test_ollama_onboarding)
        test_btn.pack(side=tk.LEFT)
        self.w.bind_hover(test_btn, T.C_MINT, T.C_GREEN)

    def _step_theme(self):
        T = self.T
        f = self._content

        tk.Label(f, text="\U0001f3a8", font=("Segoe UI Emoji", 36),
                 bg=T.C_BG).pack(pady=(20, 8))
        tk.Label(f, text="Choose Your Theme",
                 font=T.FONT_XL_B, fg=T.C_NAVY, bg=T.C_BG).pack()
        tk.Label(f, text="You can change this anytime in Settings.",
                 font=T.FONT_SM, fg=T.C_TEXT_MED, bg=T.C_BG).pack(pady=(4, 20))

        self._ob_dark_var = tk.BooleanVar(value=self.prefs.dark_mode)

        cards = tk.Frame(f, bg=T.C_BG)
        cards.pack()

        # Light card
        light_card = tk.Frame(cards, bg=T.C_WHITE, cursor="hand2",
                              highlightthickness=3, highlightbackground=T.C_BG)
        light_card.pack(side=tk.LEFT, padx=16, ipadx=20, ipady=16)
        tk.Label(light_card, text="\u2600", font=("Segoe UI Emoji", 28),
                 bg=T.C_WHITE).pack()
        tk.Label(light_card, text="Light", font=T.FONT_B,
                 fg=T.C_NAVY, bg=T.C_WHITE).pack()
        tk.Label(light_card, text="Bright & airy", font=T.FONT_XS,
                 fg=T.C_TEXT_MED, bg=T.C_WHITE).pack()

        # Dark card
        dark_card = tk.Frame(cards, bg="#1A1A2E", cursor="hand2",
                             highlightthickness=3, highlightbackground=T.C_BG)
        dark_card.pack(side=tk.LEFT, padx=16, ipadx=20, ipady=16)
        tk.Label(dark_card, text="\U0001f319", font=("Segoe UI Emoji", 28),
                 bg="#1A1A2E").pack()
        tk.Label(dark_card, text="Dark", font=T.FONT_B,
                 fg="#E8E8F0", bg="#1A1A2E").pack()
        tk.Label(dark_card, text="Easy on the eyes", font=T.FONT_XS,
                 fg="#A0A0C0", bg="#1A1A2E").pack()

        def _select_light(e=None):
            self._ob_dark_var.set(False)
            light_card.config(highlightbackground=T.C_AQUA)
            dark_card.config(highlightbackground=T.C_BG)

        def _select_dark(e=None):
            self._ob_dark_var.set(True)
            dark_card.config(highlightbackground=T.C_AQUA)
            light_card.config(highlightbackground=T.C_BG)

        for w in [light_card] + light_card.winfo_children():
            w.bind("<Button-1>", _select_light)
        for w in [dark_card] + dark_card.winfo_children():
            w.bind("<Button-1>", _select_dark)

        # Pre-select
        if self._ob_dark_var.get():
            _select_dark()
        else:
            _select_light()

    def _step_tour(self):
        T = self.T
        f = self._content

        tk.Label(f, text="\U0001f389", font=("Segoe UI Emoji", 36),
                 bg=T.C_BG).pack(pady=(20, 8))
        tk.Label(f, text="You're All Set!",
                 font=T.FONT_XL_B, fg=T.C_NAVY, bg=T.C_BG).pack()
        tk.Label(f, text="Here's what you can do:",
                 font=T.FONT_SM, fg=T.C_TEXT_MED, bg=T.C_BG).pack(pady=(4, 16))

        tips = [
            ("\uff0b  New Chat", "Start a conversation with your AI"),
            ("\U0001f4c2  Upload File", "Add PDFs, text files, or ZIM libraries to your knowledge base"),
            ("\U0001f3a4  Voice Mode", "Talk to your AI with speech-to-text"),
            ("\U0001f512  Vault", "Store sensitive data in encrypted local storage"),
            ("\u2699  Settings", "Customize everything (Ctrl+,)"),
        ]

        for icon_text, desc in tips:
            row = tk.Frame(f, bg=T.C_BG)
            row.pack(fill=tk.X, padx=60, pady=3)
            tk.Label(row, text=icon_text, font=T.FONT_SM_B,
                     fg=T.C_AQUA, bg=T.C_BG, width=16,
                     anchor=tk.W).pack(side=tk.LEFT)
            tk.Label(row, text=desc, font=T.FONT_SM,
                     fg=T.C_TEXT, bg=T.C_BG, anchor=tk.W).pack(side=tk.LEFT)

    # ── Navigation ────────────────────────────────────────────────────

    def _next(self):
        # Save step-specific data
        if self._step == 1:  # Ollama
            self.prefs.ollama_url = self._ob_url_var.get().strip()
            self.prefs.ollama_model = self._ob_model_var.get().strip()
        elif self._step == 2:  # Theme
            self.prefs.dark_mode = self._ob_dark_var.get()

        if self._step < self.TOTAL_STEPS - 1:
            self._step += 1
            self._show_step()
        else:
            self._finish()

    def _back(self):
        if self._step > 0:
            self._step -= 1
            self._show_step()

    def _skip(self):
        self.prefs.save()
        self._dlg.destroy()
        if self._on_complete:
            self._on_complete()

    def _finish(self):
        self.prefs.save()
        self._dlg.destroy()
        if self._on_complete:
            self._on_complete()

    def _test_ollama_onboarding(self):
        """Test Ollama connection from the onboarding step."""
        import requests
        url = self._ob_url_var.get().strip()
        self._ob_status.config(text="Testing...", fg=self.T.C_TEXT_LT)
        self._dlg.update()

        def _test():
            try:
                resp = requests.get(f"{url}/api/tags", timeout=5, proxies=_no_proxies())  # lan-egress: Ollama /api/tags probe, LAN
                if resp.status_code == 200:
                    models = resp.json().get("models", [])
                    names = [m["name"] for m in models[:5]]
                    self._dlg.after(0, lambda: self._ob_status.config(
                        text=f"\u2714 Connected! Models: {', '.join(names) or 'none'}",
                        fg=self.T.C_GREEN))
                else:
                    self._dlg.after(0, lambda: self._ob_status.config(
                        text=f"\u2718 HTTP {resp.status_code}", fg=self.T.C_RED))
            except Exception as e:
                msg = str(e)[:60]
                self._dlg.after(0, lambda: self._ob_status.config(
                    text=f"\u2718 {msg}", fg=self.T.C_RED))

        threading.Thread(target=_test, daemon=True).start()
