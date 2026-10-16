"""PuffPAI Settings Panel — tabbed dialog for all user-facing configuration.

Replaces manual JSON editing with a polished GUI. Tabs:
  - General: theme, font size, system tray, notifications
  - AI Model: Ollama URL, model name, temperature, context length
  - Voice: input/output device, wake word, TTS toggle
  - Smart Home: Home Assistant URL, token (masked), n8n URL
  - About: version info, keyboard shortcuts, feedback stats

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox
from typing import Callable

from .theme import PuffPAITheme
from .widgets import AeroWidgets
from .preferences import UIPreferences
from puffpai.security.lan_guard import no_proxies as _no_proxies  # marked home-network calls never use a proxy


class SettingsView:
    """Full settings dialog with tabbed navigation."""

    VERSION = "1.0.0-sprint4"

    def __init__(self, state, widgets: AeroWidgets, theme: PuffPAITheme,
                 preferences: UIPreferences,
                 on_theme_changed: Callable | None = None,
                 on_save: Callable | None = None):
        self.s = state
        self.w = widgets
        self.T = theme
        self.prefs = preferences
        self._on_theme_changed = on_theme_changed
        self._on_save = on_save
        self._dlg = None

    def show(self, tab: str = "general"):
        """Open the settings dialog. *tab* selects which tab is active."""
        if self._dlg and self._dlg.winfo_exists():
            self._dlg.lift()
            return

        self._dlg = dlg = tk.Toplevel(self.s.window)  # type: ignore[assignment]
        dlg.title("PuffPAI Settings")
        dlg.geometry("620x520")
        dlg.configure(bg=self.T.C_BG)
        dlg.resizable(False, False)
        dlg.transient(self.s.window)
        dlg.grab_set()

        # ── Header ────────────────────────────────────────────────────
        header = tk.Frame(dlg, bg=self.T.C_NAVY, height=50)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        tk.Label(header, text="\u2699  Settings", font=self.T.FONT_LG_B,
                 fg=self.T.C_WHITE, bg=self.T.C_NAVY).pack(
                     side=tk.LEFT, padx=20, pady=10)

        # ── Tab bar ───────────────────────────────────────────────────
        tab_bar = tk.Frame(dlg, bg=self.T.C_PALE)
        tab_bar.pack(fill=tk.X)

        self._tab_buttons = {}
        self._tab_frames = {}
        self._active_tab = None

        tabs = [
            ("general", "\u2699  General"),
            ("model", "\U0001f916  AI Model"),
            ("voice", "\U0001f3a4  Voice"),
            ("smarthome", "\U0001f3e0  Smart Home"),
            ("about", "\u2139  About"),
        ]

        for tab_id, label in tabs:
            btn = tk.Button(
                tab_bar, text=label, font=self.T.FONT_SM,
                bg=self.T.C_PALE, fg=self.T.C_NAVY,
                relief=tk.FLAT, cursor="hand2", padx=14, pady=8,
                command=lambda t=tab_id: self._switch_tab(t))  # type: ignore[misc]
            btn.pack(side=tk.LEFT)
            self._tab_buttons[tab_id] = btn

        tk.Frame(tab_bar, height=2, bg=self.T.C_AQUA).pack(
            side=tk.BOTTOM, fill=tk.X)

        # ── Content area ──────────────────────────────────────────────
        content = tk.Frame(dlg, bg=self.T.C_BG)
        content.pack(fill=tk.BOTH, expand=True)

        self._tab_frames["general"] = self._build_general_tab(content)
        self._tab_frames["model"] = self._build_model_tab(content)
        self._tab_frames["voice"] = self._build_voice_tab(content)
        self._tab_frames["smarthome"] = self._build_smarthome_tab(content)
        self._tab_frames["about"] = self._build_about_tab(content)

        # ── Bottom button bar ─────────────────────────────────────────
        btn_bar = tk.Frame(dlg, bg=self.T.C_GLASS, height=50)
        btn_bar.pack(fill=tk.X, side=tk.BOTTOM)
        btn_bar.pack_propagate(False)

        save_btn = tk.Button(
            btn_bar, text="Save & Close", font=self.T.FONT_B,
            bg=self.T.C_AQUA, fg=self.T.C_WHITE, relief=tk.FLAT,
            cursor="hand2", padx=20, pady=6,
            command=self._save_and_close)
        save_btn.pack(side=tk.RIGHT, padx=16, pady=8)
        self.w.bind_hover(save_btn, self.T.C_AQUA, self.T.C_AQUA_MID)

        cancel_btn = tk.Button(
            btn_bar, text="Cancel", font=self.T.FONT_SM,
            bg=self.T.C_PALE, fg=self.T.C_NAVY, relief=tk.FLAT,
            cursor="hand2", padx=16, pady=6,
            command=dlg.destroy)
        cancel_btn.pack(side=tk.RIGHT, padx=4, pady=8)
        self.w.bind_hover(cancel_btn, self.T.C_PALE, self.T.C_GLASS)

        reset_btn = tk.Button(
            btn_bar, text="Reset Defaults", font=self.T.FONT_XS,
            bg=self.T.C_GLASS, fg=self.T.C_RED, relief=tk.FLAT,
            cursor="hand2", padx=12, pady=6,
            command=self._reset_defaults)
        reset_btn.pack(side=tk.LEFT, padx=16, pady=8)

        # Show requested tab
        self._switch_tab(tab)
        dlg.bind("<Escape>", lambda e: dlg.destroy())

    # ── Tab switching ─────────────────────────────────────────────────

    def _switch_tab(self, tab_id: str):
        for tid, frame in self._tab_frames.items():
            frame.pack_forget()
        self._tab_frames[tab_id].pack(fill=tk.BOTH, expand=True)

        for tid, btn in self._tab_buttons.items():
            if tid == tab_id:
                btn.configure(bg=self.T.C_AQUA_LT, fg=self.T.C_NAVY)
            else:
                btn.configure(bg=self.T.C_PALE, fg=self.T.C_NAVY)
        self._active_tab = tab_id  # type: ignore[assignment]

    # ── General tab ───────────────────────────────────────────────────

    def _build_general_tab(self, parent) -> tk.Frame:
        frame = tk.Frame(parent, bg=self.T.C_BG)
        T = self.T

        self._build_section_header(frame, "Appearance")

        # Dark mode
        self._dark_var = tk.BooleanVar(value=self.prefs.dark_mode)
        row = self._setting_row(frame, "Dark Mode",
                                "Use dark colour scheme for the dashboard")
        tk.Checkbutton(row, variable=self._dark_var,
                       bg=T.C_BG, activebackground=T.C_BG,
                       selectcolor=T.C_AQUA).pack(side=tk.RIGHT)

        # Font size
        self._font_var = tk.IntVar(value=self.prefs.font_size)
        row = self._setting_row(frame, "Font Size", "Base font size (8-18)")
        spin = tk.Spinbox(row, from_=8, to=18, textvariable=self._font_var,
                          width=4, font=T.FONT, bg=T.C_WHITE)
        spin.pack(side=tk.RIGHT)

        self._build_section_header(frame, "System Tray")

        # Minimize to tray
        self._tray_var = tk.BooleanVar(value=self.prefs.minimize_to_tray)
        row = self._setting_row(frame, "Minimize to Tray",
                                "Hide to system tray instead of closing")
        tk.Checkbutton(row, variable=self._tray_var,
                       bg=T.C_BG, activebackground=T.C_BG,
                       selectcolor=T.C_AQUA).pack(side=tk.RIGHT)

        # Start minimized
        self._start_min_var = tk.BooleanVar(value=self.prefs.start_minimized)
        row = self._setting_row(frame, "Start Minimized",
                                "Launch PuffPAI in the system tray")
        tk.Checkbutton(row, variable=self._start_min_var,
                       bg=T.C_BG, activebackground=T.C_BG,
                       selectcolor=T.C_AQUA).pack(side=tk.RIGHT)

        self._build_section_header(frame, "Notifications")

        self._notif_var = tk.BooleanVar(value=self.prefs.show_notifications)
        row = self._setting_row(frame, "Show Notifications",
                                "Display tray notifications for events")
        tk.Checkbutton(row, variable=self._notif_var,
                       bg=T.C_BG, activebackground=T.C_BG,
                       selectcolor=T.C_AQUA).pack(side=tk.RIGHT)

        return frame

    # ── Model tab ─────────────────────────────────────────────────────

    def _build_model_tab(self, parent) -> tk.Frame:
        frame = tk.Frame(parent, bg=self.T.C_BG)
        T = self.T

        self._build_section_header(frame, "Ollama Connection")

        # Ollama URL
        self._ollama_url_var = tk.StringVar(
            value=self.prefs.ollama_url or self.s.ollama_url)
        row = self._setting_row(frame, "Ollama URL", "e.g. http://localhost:11434")
        entry = tk.Entry(row, textvariable=self._ollama_url_var,
                         font=T.FONT, bg=T.C_WHITE, fg=T.C_TEXT,
                         insertbackground=T.C_AQUA, relief=tk.FLAT, width=30)
        entry.pack(side=tk.RIGHT, ipady=4)

        # Test connection button
        test_row = tk.Frame(frame, bg=T.C_BG)
        test_row.pack(fill=tk.X, padx=30, pady=(0, 8))
        self._conn_status = tk.Label(test_row, text="", font=T.FONT_XS,
                                     fg=T.C_TEXT_LT, bg=T.C_BG)
        self._conn_status.pack(side=tk.RIGHT, padx=(8, 0))
        test_btn = tk.Button(
            test_row, text="Test Connection", font=T.FONT_XS,
            bg=T.C_MINT, fg=T.C_WHITE, relief=tk.FLAT, cursor="hand2",
            padx=10, pady=2, command=self._test_ollama)
        test_btn.pack(side=tk.RIGHT)
        self.w.bind_hover(test_btn, T.C_MINT, T.C_GREEN)

        self._build_section_header(frame, "Model Configuration")

        # Model name
        self._model_var = tk.StringVar(
            value=self.prefs.ollama_model or self.s.ollama_model)
        row = self._setting_row(frame, "Model Name",
                                "e.g. dolphin-llama3:8b, llama3:8b")
        entry = tk.Entry(row, textvariable=self._model_var,
                         font=T.FONT, bg=T.C_WHITE, fg=T.C_TEXT,
                         insertbackground=T.C_AQUA, relief=tk.FLAT, width=24)
        entry.pack(side=tk.RIGHT, ipady=4)

        # Temperature
        self._temp_var = tk.DoubleVar(value=self.prefs.ollama_temperature)
        row = self._setting_row(frame, "Temperature",
                                "Creativity (0.0 = focused, 1.0 = creative)")
        temp_frame = tk.Frame(row, bg=T.C_BG)
        temp_frame.pack(side=tk.RIGHT)
        self._temp_label = tk.Label(temp_frame,
                                    text=f"{self._temp_var.get():.1f}",
                                    font=T.FONT_SM, fg=T.C_TEXT, bg=T.C_BG,
                                    width=4)
        self._temp_label.pack(side=tk.RIGHT)
        scale = tk.Scale(temp_frame, from_=0.0, to=1.5,
                         resolution=0.1, orient=tk.HORIZONTAL,
                         variable=self._temp_var, showvalue=False,
                         length=120, bg=T.C_BG, troughcolor=T.C_GLASS,
                         highlightthickness=0,
                         command=lambda v: self._temp_label.config(
                             text=f"{float(v):.1f}"))
        scale.pack(side=tk.RIGHT)

        # Max context tokens
        self._ctx_var = tk.IntVar(value=self.prefs.max_context_tokens)
        row = self._setting_row(frame, "Max Context Tokens",
                                "Maximum conversation context (4096-200000)")
        spin = tk.Spinbox(row, from_=4096, to=200000, increment=4096,
                          textvariable=self._ctx_var,
                          width=8, font=T.FONT, bg=T.C_WHITE)
        spin.pack(side=tk.RIGHT)

        return frame

    # ── Voice tab ─────────────────────────────────────────────────────

    def _build_voice_tab(self, parent) -> tk.Frame:
        frame = tk.Frame(parent, bg=self.T.C_BG)
        T = self.T

        self._build_section_header(frame, "Voice Input (STT)")

        # Input device
        self._voice_in_var = tk.StringVar(
            value=self.prefs.voice_input_device or "Default")
        row = self._setting_row(frame, "Input Device", "Microphone for speech-to-text")
        devices = self._get_audio_devices("input")
        combo = ttk.Combobox(row, textvariable=self._voice_in_var,
                             values=devices, state="readonly",
                             font=T.FONT, width=24)
        combo.pack(side=tk.RIGHT)

        # Voice enabled by default
        self._voice_default_var = tk.BooleanVar(
            value=self.prefs.voice_enabled_default)
        row = self._setting_row(frame, "Auto-Enable Voice",
                                "Start with voice mode active")
        tk.Checkbutton(row, variable=self._voice_default_var,
                       bg=T.C_BG, activebackground=T.C_BG,
                       selectcolor=T.C_AQUA).pack(side=tk.RIGHT)

        self._build_section_header(frame, "Voice Output (TTS)")

        # Output device
        self._voice_out_var = tk.StringVar(
            value=self.prefs.voice_output_device or "Default")
        row = self._setting_row(frame, "Output Device", "Speaker for text-to-speech")
        devices = self._get_audio_devices("output")
        combo = ttk.Combobox(row, textvariable=self._voice_out_var,
                             values=devices, state="readonly",
                             font=T.FONT, width=24)
        combo.pack(side=tk.RIGHT)

        # Status
        self._build_section_header(frame, "Status")
        status_frame = tk.Frame(frame, bg=T.C_BG)
        status_frame.pack(fill=tk.X, padx=30, pady=4)

        stt_ok = self._check_stt()
        tts_ok = self._check_tts()
        sd_ok = self._check_sounddevice()

        for label, ok in [("sounddevice", sd_ok), ("Vosk STT", stt_ok),
                          ("Piper TTS", tts_ok)]:
            dot = "\u25cf" if ok else "\u25cb"
            color = T.C_GREEN if ok else T.C_RED
            text = f"  {dot}  {label}: {'Installed' if ok else 'Not installed'}"
            tk.Label(status_frame, text=text, font=T.FONT_XS,
                     fg=color, bg=T.C_BG, anchor=tk.W).pack(anchor=tk.W)

        return frame

    # ── Smart Home tab ────────────────────────────────────────────────

    def _build_smarthome_tab(self, parent) -> tk.Frame:
        frame = tk.Frame(parent, bg=self.T.C_BG)
        T = self.T

        self._build_section_header(frame, "Home Assistant")

        # HA URL
        ha_url = ""
        try:
            from puffpai.config import get_config
            cfg = get_config()
            if hasattr(cfg, 'ha') and hasattr(cfg.ha, 'url'):
                ha_url = cfg.ha.url
        except Exception:
            pass
        self._ha_url_var = tk.StringVar(value=ha_url)
        row = self._setting_row(frame, "HA URL",
                                "e.g. http://homeassistant.local:8123")
        entry = tk.Entry(row, textvariable=self._ha_url_var,
                         font=T.FONT, bg=T.C_WHITE, fg=T.C_TEXT,
                         insertbackground=T.C_AQUA, relief=tk.FLAT, width=30)
        entry.pack(side=tk.RIGHT, ipady=4)

        # HA Token (masked)
        self._ha_token_var = tk.StringVar(value="")
        row = self._setting_row(frame, "HA Token",
                                "Long-lived access token (stored in vault)")
        entry = tk.Entry(row, textvariable=self._ha_token_var,
                         font=T.FONT, bg=T.C_WHITE, fg=T.C_TEXT,
                         insertbackground=T.C_AQUA, relief=tk.FLAT,
                         width=30, show="\u2022")
        entry.pack(side=tk.RIGHT, ipady=4)

        self._build_section_header(frame, "n8n Workflow Engine")

        n8n_url = ""
        try:
            from puffpai.config import get_config
            cfg = get_config()
            if hasattr(cfg, 'n8n') and hasattr(cfg.n8n, 'url'):
                n8n_url = cfg.n8n.url
        except Exception:
            pass
        self._n8n_url_var = tk.StringVar(value=n8n_url)
        row = self._setting_row(frame, "n8n URL",
                                "e.g. http://localhost:5678")
        entry = tk.Entry(row, textvariable=self._n8n_url_var,
                         font=T.FONT, bg=T.C_WHITE, fg=T.C_TEXT,
                         insertbackground=T.C_AQUA, relief=tk.FLAT, width=30)
        entry.pack(side=tk.RIGHT, ipady=4)

        # Connection status
        self._build_section_header(frame, "Connection Status")
        status_frame = tk.Frame(frame, bg=T.C_BG)
        status_frame.pack(fill=tk.X, padx=30, pady=4)

        for label in ["Home Assistant", "n8n", "Ollama"]:
            dot = "\u25cb"
            tk.Label(status_frame, text=f"  {dot}  {label}: Click 'Test' to check",
                     font=T.FONT_XS, fg=T.C_TEXT_LT, bg=T.C_BG,
                     anchor=tk.W).pack(anchor=tk.W)

        return frame

    # ── About tab ─────────────────────────────────────────────────────

    def _build_about_tab(self, parent) -> tk.Frame:
        frame = tk.Frame(parent, bg=self.T.C_BG)
        T = self.T

        # Version info
        info = tk.Frame(frame, bg=T.C_BG)
        info.pack(fill=tk.X, padx=30, pady=(20, 10))

        tk.Label(info, text="PuffPAI", font=(self.s._FF, 20, "bold"),
                 fg=T.C_NAVY, bg=T.C_BG).pack(anchor=tk.W)
        tk.Label(info, text=f"Version {self.VERSION}",
                 font=T.FONT_SM, fg=T.C_TEXT_MED, bg=T.C_BG).pack(anchor=tk.W)
        tk.Label(info,
                 text="Privacy-Forward Personal AI Assistant",
                 font=T.FONT_SM, fg=T.C_TEXT_LT, bg=T.C_BG).pack(
                     anchor=tk.W, pady=(2, 0))
        tk.Label(info, text="Created by Cypher Sisters",
                 font=T.FONT_XS, fg=T.C_TEXT_LT, bg=T.C_BG).pack(
                     anchor=tk.W, pady=(2, 0))
        tk.Label(info, text="\u00a9 2025 Cypher Sisters. Apache-2.0 License.",
                 font=T.FONT_XS, fg=T.C_TEXT_LT, bg=T.C_BG).pack(
                     anchor=tk.W, pady=(2, 0))

        tk.Frame(frame, height=1, bg=T.C_BORDER).pack(
            fill=tk.X, padx=30, pady=10)

        # Keyboard shortcuts
        self._build_section_header(frame, "Keyboard Shortcuts")
        shortcuts_frame = tk.Frame(frame, bg=T.C_BG)
        shortcuts_frame.pack(fill=tk.X, padx=30)

        shortcuts = [
            ("Ctrl+N", "New chat"),
            ("Ctrl+,", "Open settings"),
            ("Ctrl+D", "Toggle dark mode"),
            ("Ctrl+M", "Toggle voice mode"),
            ("F1", "Show help"),
            ("Ctrl+Q", "Quit PuffPAI"),
            ("Enter", "Send message"),
            ("Shift+Enter", "New line in message"),
        ]

        for key, desc in shortcuts:
            row = tk.Frame(shortcuts_frame, bg=T.C_BG)
            row.pack(fill=tk.X, pady=1)
            tk.Label(row, text=key, font=T.FONT_SM_B, fg=T.C_AQUA,
                     bg=T.C_BG, width=14, anchor=tk.W).pack(side=tk.LEFT)
            tk.Label(row, text=desc, font=T.FONT_SM, fg=T.C_TEXT,
                     bg=T.C_BG, anchor=tk.W).pack(side=tk.LEFT)

        return frame

    # ── Helpers ───────────────────────────────────────────────────────

    def _build_section_header(self, parent, title: str):
        frame = tk.Frame(parent, bg=self.T.C_BG)
        frame.pack(fill=tk.X, padx=30, pady=(16, 4))
        tk.Label(frame, text=title.upper(), font=self.T.FONT_XS,
                 fg=self.T.C_TEXT_LT, bg=self.T.C_BG).pack(anchor=tk.W)
        tk.Frame(frame, height=1, bg=self.T.C_BORDER).pack(fill=tk.X, pady=(2, 0))

    def _setting_row(self, parent, label: str, hint: str) -> tk.Frame:
        row = tk.Frame(parent, bg=self.T.C_BG)
        row.pack(fill=tk.X, padx=30, pady=4)
        left = tk.Frame(row, bg=self.T.C_BG)
        left.pack(side=tk.LEFT)
        tk.Label(left, text=label, font=self.T.FONT_SM_B,
                 fg=self.T.C_TEXT, bg=self.T.C_BG).pack(anchor=tk.W)
        tk.Label(left, text=hint, font=self.T.FONT_XS,
                 fg=self.T.C_TEXT_LT, bg=self.T.C_BG).pack(anchor=tk.W)
        return row

    def _test_ollama(self):
        import requests
        url = self._ollama_url_var.get().strip()
        if not url:
            self._conn_status.config(text="\u2718 No URL", fg=self.T.C_RED)
            return
        try:
            resp = requests.get(f"{url}/api/tags", timeout=5, proxies=_no_proxies())  # lan-egress: Ollama /api/tags probe, LAN
            if resp.status_code == 200:
                models = resp.json().get("models", [])
                self._conn_status.config(
                    text=f"\u2714 Connected ({len(models)} models)",
                    fg=self.T.C_GREEN)
            else:
                self._conn_status.config(
                    text=f"\u2718 HTTP {resp.status_code}", fg=self.T.C_RED)
        except Exception as e:
            self._conn_status.config(text=f"\u2718 {e}", fg=self.T.C_RED)

    @staticmethod
    def _get_audio_devices(direction: str) -> list[str]:
        devices = ["Default"]
        try:
            import sounddevice as sd
            all_devs = sd.query_devices()
            for d in all_devs:
                if direction == "input" and d["max_input_channels"] > 0:
                    devices.append(d["name"])
                elif direction == "output" and d["max_output_channels"] > 0:
                    devices.append(d["name"])
        except Exception:
            pass
        return devices

    @staticmethod
    def _check_stt() -> bool:
        try:
            import vosk  # noqa: F401
            return True
        except ImportError:
            return False

    @staticmethod
    def _check_tts() -> bool:
        try:
            import piper  # noqa: F401
            return True
        except ImportError:
            return False

    @staticmethod
    def _check_sounddevice() -> bool:
        try:
            import sounddevice  # noqa: F401
            return True
        except ImportError:
            return False

    # ── Save / Reset ──────────────────────────────────────────────────

    def _save_and_close(self):
        """Collect all values and persist."""
        self.prefs.update(
            dark_mode=self._dark_var.get(),
            font_size=self._font_var.get(),
            minimize_to_tray=self._tray_var.get(),
            start_minimized=self._start_min_var.get(),
            show_notifications=self._notif_var.get(),
            ollama_url=self._ollama_url_var.get().strip(),
            ollama_model=self._model_var.get().strip(),
            ollama_temperature=self._temp_var.get(),
            max_context_tokens=self._ctx_var.get(),
            voice_input_device=self._voice_in_var.get(),
            voice_output_device=self._voice_out_var.get(),
            voice_enabled_default=self._voice_default_var.get(),
        )

        # Apply theme change immediately
        old_dark = self.s.dark_mode
        self.s.dark_mode = self._dark_var.get()
        self.s.ollama_url = self._ollama_url_var.get().strip() or self.s.ollama_url
        self.s.ollama_model = self._model_var.get().strip() or self.s.ollama_model
        self.s.ollama_temperature = self._temp_var.get()

        if self._on_theme_changed and old_dark != self.s.dark_mode:
            self._on_theme_changed()
        if self._on_save:
            self._on_save()

        self._dlg.destroy()

    def _reset_defaults(self):
        if messagebox.askyesno(
                "Reset Settings",
                "Reset all settings to defaults?\n\n"
                "This cannot be undone.",
                parent=self._dlg):
            self.prefs.reset()
            self._dlg.destroy()
            self.show()
