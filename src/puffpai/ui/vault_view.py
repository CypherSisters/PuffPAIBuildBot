"""PuffPAI Vault PIN Dialog & Session Management UI.

Provides PIN setup, login, and vault management as pop-up dialogs.

Usage:
    vault_view = VaultView(state, widgets, theme, on_system_message=...)
    vault_view.show_vault_dialog()

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox
from typing import Callable, Optional

from .theme import PuffPAITheme
from .widgets import AeroWidgets


class VaultView:
    """Vault PIN entry and session management dialogs."""

    def __init__(self, state, widgets: AeroWidgets, theme: PuffPAITheme,
                 on_system_message: Callable[[str], None] | None = None):
        self.s = state
        self.w = widgets
        self.T = theme
        self._on_system_message = on_system_message
        self._vault = None
        self._session = None

    def _get_vault(self):
        """Get or create the Vault instance."""
        if self._vault is None:
            from puffpai.security.vault import Vault
            self._vault = Vault()
        return self._vault

    @property
    def is_unlocked(self) -> bool:
        """Whether a vault session is active."""
        if self._session is None:
            return False
        return not self._session.is_expired

    def show_vault_dialog(self):
        """Show the appropriate vault dialog based on state."""
        vault = self._get_vault()

        if not vault.is_initialized:
            self._show_setup_dialog()
        elif self.is_unlocked:
            self._show_management_dialog()
        else:
            self._show_login_dialog()

    # ── PIN Setup (first run) ─────────────────────────────────────────

    def _show_setup_dialog(self):
        """PIN setup dialog for first-time vault initialization."""
        T = self.T
        dialog = tk.Toplevel(self.s.window)
        dialog.title("Setup Vault PIN")
        dialog.geometry("380x280")
        dialog.resizable(False, False)
        dialog.configure(bg=T.C_BG)
        dialog.transient(self.s.window)
        dialog.grab_set()

        # Header
        header = tk.Frame(dialog, bg=T.C_PALE, height=50)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        tk.Frame(header, height=2, bg=T.C_AQUA_LT).pack(
            side=tk.BOTTOM, fill=tk.X)
        tk.Label(header, text="\U0001f512 Setup Vault PIN",
                 font=T.FONT_LG_B, fg=T.C_NAVY, bg=T.C_PALE).pack(
            side=tk.LEFT, padx=16, pady=10)

        body = tk.Frame(dialog, bg=T.C_BG)
        body.pack(fill=tk.BOTH, expand=True, padx=20, pady=12)

        tk.Label(body, text="Create a PIN to protect your personal vault.\n"
                            "Must be at least 4 characters.",
                 font=T.FONT_SM, fg=T.C_TEXT_MED, bg=T.C_BG,
                 justify=tk.LEFT).pack(anchor=tk.W, pady=(0, 10))

        # PIN entry
        tk.Label(body, text="PIN:", font=T.FONT_SM_B,
                 fg=T.C_TEXT, bg=T.C_BG).pack(anchor=tk.W)
        pin_var = tk.StringVar()
        pin_entry = tk.Entry(body, textvariable=pin_var, show="*",
                             font=T.FONT, bg=T.C_WHITE, fg=T.C_TEXT,
                             relief=tk.FLAT, insertbackground=T.C_AQUA)
        pin_entry.pack(fill=tk.X, pady=(2, 8))
        pin_entry.focus_set()

        # Confirm
        tk.Label(body, text="Confirm PIN:", font=T.FONT_SM_B,
                 fg=T.C_TEXT, bg=T.C_BG).pack(anchor=tk.W)
        confirm_var = tk.StringVar()
        confirm_entry = tk.Entry(body, textvariable=confirm_var, show="*",
                                 font=T.FONT, bg=T.C_WHITE, fg=T.C_TEXT,
                                 relief=tk.FLAT, insertbackground=T.C_AQUA)
        confirm_entry.pack(fill=tk.X, pady=(2, 12))

        # Buttons
        btn_frame = tk.Frame(body, bg=T.C_BG)
        btn_frame.pack(fill=tk.X)

        def _do_setup():
            pin = pin_var.get()
            confirm = confirm_var.get()
            if len(pin) < 4:
                messagebox.showwarning("Invalid PIN",
                                       "PIN must be at least 4 characters.",
                                       parent=dialog)
                return
            if pin != confirm:
                messagebox.showwarning("PIN Mismatch",
                                       "PINs do not match. Try again.",
                                       parent=dialog)
                return
            try:
                vault = self._get_vault()
                vault.initialize(pin)
                dialog.destroy()
                if self._on_system_message:
                    self._on_system_message(
                        "[System] Vault initialized. Use the lock icon to log in.")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to setup vault: {e}",
                                     parent=dialog)

        setup_btn = tk.Button(
            btn_frame, text="Create Vault", command=_do_setup,
            bg=T.C_AQUA, fg=T.C_WHITE, font=T.FONT_B,
            relief=tk.FLAT, cursor="hand2", padx=16, pady=6)
        setup_btn.pack(side=tk.RIGHT)

        cancel_btn = tk.Button(
            btn_frame, text="Cancel", command=dialog.destroy,
            bg=T.C_BORDER, fg=T.C_TEXT, font=T.FONT_SM,
            relief=tk.FLAT, cursor="hand2", padx=16, pady=6)
        cancel_btn.pack(side=tk.RIGHT, padx=(0, 6))

    # ── PIN Login ─────────────────────────────────────────────────────

    def _show_login_dialog(self):
        """PIN login dialog."""
        T = self.T
        dialog = tk.Toplevel(self.s.window)
        dialog.title("Unlock Vault")
        dialog.geometry("360x220")
        dialog.resizable(False, False)
        dialog.configure(bg=T.C_BG)
        dialog.transient(self.s.window)
        dialog.grab_set()

        # Header
        header = tk.Frame(dialog, bg=T.C_PALE, height=50)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        tk.Frame(header, height=2, bg=T.C_AQUA_LT).pack(
            side=tk.BOTTOM, fill=tk.X)
        tk.Label(header, text="\U0001f513 Unlock Vault",
                 font=T.FONT_LG_B, fg=T.C_NAVY, bg=T.C_PALE).pack(
            side=tk.LEFT, padx=16, pady=10)

        body = tk.Frame(dialog, bg=T.C_BG)
        body.pack(fill=tk.BOTH, expand=True, padx=20, pady=12)

        tk.Label(body, text="Enter your vault PIN:",
                 font=T.FONT_SM, fg=T.C_TEXT_MED, bg=T.C_BG).pack(
            anchor=tk.W, pady=(0, 6))

        pin_var = tk.StringVar()
        pin_entry = tk.Entry(body, textvariable=pin_var, show="*",
                             font=T.FONT, bg=T.C_WHITE, fg=T.C_TEXT,
                             relief=tk.FLAT, insertbackground=T.C_AQUA)
        pin_entry.pack(fill=tk.X, pady=(0, 12))
        pin_entry.focus_set()

        status_label = tk.Label(body, text="", font=T.FONT_XS,
                                fg=T.C_RED, bg=T.C_BG)
        status_label.pack(anchor=tk.W)

        btn_frame = tk.Frame(body, bg=T.C_BG)
        btn_frame.pack(fill=tk.X, pady=(8, 0))

        def _do_login(event=None):
            pin = pin_var.get()
            if not pin:
                return
            try:
                vault = self._get_vault()
                self._session = vault.authenticate(pin)
                self.s.vault_unlocked = True
                dialog.destroy()
                if self._on_system_message:
                    self._on_system_message(
                        "[System] Vault unlocked (15-min session)")
            except Exception as e:
                err_msg = str(e)
                status_label.config(text=err_msg)
                pin_var.set("")
                pin_entry.focus_set()

        pin_entry.bind("<Return>", _do_login)

        login_btn = tk.Button(
            btn_frame, text="Unlock", command=_do_login,
            bg=T.C_AQUA, fg=T.C_WHITE, font=T.FONT_B,
            relief=tk.FLAT, cursor="hand2", padx=16, pady=6)
        login_btn.pack(side=tk.RIGHT)

        cancel_btn = tk.Button(
            btn_frame, text="Cancel", command=dialog.destroy,
            bg=T.C_BORDER, fg=T.C_TEXT, font=T.FONT_SM,
            relief=tk.FLAT, cursor="hand2", padx=16, pady=6)
        cancel_btn.pack(side=tk.RIGHT, padx=(0, 6))

    # ── Vault Management ──────────────────────────────────────────────

    def _show_management_dialog(self):
        """Vault management panel when already unlocked."""
        T = self.T
        dialog = tk.Toplevel(self.s.window)
        dialog.title("Vault Management")
        dialog.geometry("400x300")
        dialog.resizable(False, False)
        dialog.configure(bg=T.C_BG)
        dialog.transient(self.s.window)
        dialog.grab_set()

        # Header
        header = tk.Frame(dialog, bg=T.C_PALE, height=50)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        tk.Frame(header, height=2, bg=T.C_GREEN_LT).pack(
            side=tk.BOTTOM, fill=tk.X)
        tk.Label(header, text="\U0001f513 Vault Unlocked",
                 font=T.FONT_LG_B, fg=T.C_GREEN, bg=T.C_PALE).pack(
            side=tk.LEFT, padx=16, pady=10)

        body = tk.Frame(dialog, bg=T.C_BG)
        body.pack(fill=tk.BOTH, expand=True, padx=20, pady=12)

        # Status info
        info = [
            ("Session:", "Active" if self.is_unlocked else "Expired"),
            ("Timeout:", "15 minutes"),
        ]
        for label, value in info:
            row = tk.Frame(body, bg=T.C_BG)
            row.pack(fill=tk.X, pady=2)
            tk.Label(row, text=label, font=T.FONT_SM_B,
                     fg=T.C_TEXT, bg=T.C_BG, width=10,
                     anchor=tk.W).pack(side=tk.LEFT)
            tk.Label(row, text=value, font=T.FONT_SM,
                     fg=T.C_TEXT_MED, bg=T.C_BG).pack(side=tk.LEFT)

        tk.Frame(body, height=1, bg=T.C_BORDER).pack(
            fill=tk.X, pady=12)

        # Actions
        lock_btn = tk.Button(
            body, text="\U0001f512  Lock Vault",
            command=lambda: self._do_lock(dialog),
            bg=T.C_RED, fg=T.C_WHITE, font=T.FONT_B,
            relief=tk.FLAT, cursor="hand2", padx=16, pady=8)
        lock_btn.pack(fill=tk.X, pady=4)

        close_btn = tk.Button(
            body, text="Close",
            command=dialog.destroy,
            bg=T.C_BORDER, fg=T.C_TEXT, font=T.FONT_SM,
            relief=tk.FLAT, cursor="hand2", padx=16, pady=6)
        close_btn.pack(fill=tk.X, pady=4)

    def _do_lock(self, dialog):
        """Lock the vault (end session)."""
        if self._session and self._vault:
            try:
                self._vault.logout(self._session.session_id)
            except Exception:
                pass
        self._session = None
        self.s.vault_unlocked = False
        dialog.destroy()
        if self._on_system_message:
            self._on_system_message("[System] Vault locked")
