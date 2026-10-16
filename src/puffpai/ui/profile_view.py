"""Profile selector, creation, editing, and settings dialogs.

Extracted from app_ui.py — all profile management UI lives here.
Each method receives shared ``AppState`` and ``AeroWidgets`` via init.
"""

import tkinter as tk
from tkinter import messagebox, ttk, simpledialog
from PIL import ImageTk

from .theme import PuffPAITheme
from .widgets import AeroWidgets


class ProfileView:
    """Profile selection and management screens."""

    def __init__(self, state, widgets: AeroWidgets, theme: PuffPAITheme,
                 on_profile_selected=None, on_init_session=None):
        self.s = state
        self.w = widgets
        self.T = theme
        self._on_profile_selected = on_profile_selected
        self._on_init_session = on_init_session

    # ── Profile selector screen ──────────────────────────────────────────

    def show_profile_selector(self):
        """Full-window profile selection screen (Frutiger Aero styled)."""
        profiles = self.s.profile_manager.get_all_profiles()
        if not profiles:
            self._show_create_profile_dialog(first_run=True)
            return

        self.s._selector_frame = tk.Frame(self.s.window, bg=self.T.C_BG)
        self.s._selector_frame.pack(fill=tk.BOTH, expand=True)
        self.s._profile_avatar_photos = []

        bg_canvas = tk.Canvas(self.s._selector_frame, bg=self.T.C_BG,
                              highlightthickness=0)
        bg_canvas.place(relx=0, rely=0, relwidth=1, relheight=1)
        bg_canvas.bind("<Configure>",
                       lambda e: self.w.draw_bg_bubbles(e.widget, e.width, e.height))

        content = tk.Frame(self.s._selector_frame, bg=self.T.C_BG)
        content.place(relx=0.5, rely=0.4, anchor="center")

        tk.Label(content, text="Who's using PuffPAI?",
                 font=(self.s._FF, 20, "bold"), fg=self.T.C_NAVY,
                 bg=self.T.C_BG).pack(pady=(0, 30))

        cards_frame = tk.Frame(content, bg=self.T.C_BG)
        cards_frame.pack()

        last_active = self.s.profile_manager.get_last_active_id()
        for i, profile in enumerate(profiles):
            self._make_profile_card(cards_frame, profile, i,
                                    highlight=(profile["id"] == last_active))
        self._make_add_profile_card(cards_frame, len(profiles))

        settings_row = tk.Frame(self.s._selector_frame, bg=self.T.C_BG)
        settings_row.place(relx=0.5, rely=0.88, anchor="center")
        gear_btn = tk.Button(settings_row, text="PuffPAI Settings",
                             font=self.T.FONT_SM, fg=self.T.C_TEXT_MED,
                             bg=self.T.C_PALE, relief=tk.FLAT,
                             cursor="hand2", padx=14, pady=6,
                             command=self._show_settings_dialog)
        gear_btn.pack()
        self.w.bind_hover(gear_btn, self.T.C_PALE, self.T.C_AQUA_LT)

    def _make_profile_card(self, parent, profile, col, highlight=False):
        card_bg = self.T.C_WHITE
        card_bg_hover = self.w.tint(self.T.C_AQUA, 0.85)
        card = tk.Canvas(parent, width=140, height=180, bg=self.T.C_BG,
                         highlightthickness=3 if highlight else 0,
                         highlightbackground=self.T.C_AQUA if highlight else self.T.C_BG,
                         bd=0, cursor="hand2")
        card.grid(row=0, column=col, padx=12, pady=8)

        avatar_img = self.s.avatar_gen.generate(
            profile["avatar_seed"], profile["avatar_style"], size=96)
        avatar_img = self.w.circle_crop(avatar_img, 96)
        photo = ImageTk.PhotoImage(avatar_img)
        self.s._profile_avatar_photos.append(photo)

        name = profile["display_name"]
        short = name[:12] + "\u2026" if len(name) > 12 else name

        def _draw(bg=card_bg):
            card.delete("all")
            self.w.rounded_rect(card, 4, 4, 132, 172, r=16, fill=bg, outline="")
            card.create_image(70, 75, image=photo)
            card.create_text(70, 140, text=short, font=self.T.FONT_SM_B,
                             fill=self.T.C_TEXT)

        _draw()
        pid = profile["id"]
        card.bind("<Enter>", lambda e: _draw(card_bg_hover))
        card.bind("<Leave>", lambda e: _draw(card_bg))
        card.bind("<Button-1>", lambda e, p=pid: self._select_profile(p))
        card.bind("<Button-3>", lambda e, p=profile: self._show_profile_context_menu(e, p))

    def _make_add_profile_card(self, parent, col):
        card = tk.Canvas(parent, width=140, height=180, bg=self.T.C_BG,
                         highlightthickness=0, bd=0, cursor="hand2")
        card.grid(row=0, column=col, padx=12, pady=8)
        card_bg = self.T.C_GLASS
        card_bg_hover = self.w.tint(self.T.C_AQUA, 0.85)

        def _draw(bg=card_bg):
            card.delete("all")
            self.w.rounded_rect(card, 4, 4, 132, 172, r=16,
                                fill=bg, outline=self.T.C_BORDER)
            card.create_text(70, 75, text="+", font=(self.s._FF, 32),
                             fill=self.T.C_AQUA)
            card.create_text(70, 120, text="Add Profile",
                             font=self.T.FONT_SM, fill=self.T.C_TEXT_MED)

        _draw()
        card.bind("<Enter>", lambda e: _draw(card_bg_hover))
        card.bind("<Leave>", lambda e: _draw(card_bg))
        card.bind("<Button-1>", lambda e: self._show_create_profile_dialog())

    def _select_profile(self, profile_id):
        self.s.profile_manager.set_last_active(profile_id)
        self.s.current_user = self.s.profile_manager.get_profile(profile_id)
        if (hasattr(self.s, '_selector_frame') and
                self.s._selector_frame and
                self.s._selector_frame.winfo_exists()):
            self.s._selector_frame.destroy()
        self.s._profile_avatar_photos.clear()
        if self._on_init_session:
            self._on_init_session()

    def _show_profile_context_menu(self, event, profile):
        menu = tk.Menu(self.s.window, tearoff=0)
        menu.add_command(label="Edit Profile",
                         command=lambda: self._show_edit_profile_dialog(profile))
        menu.add_separator()
        menu.add_command(label="Delete Profile",
                         command=lambda: self._delete_profile(profile["id"]))
        menu.post(event.x_root, event.y_root)

    def _delete_profile(self, profile_id):
        if not messagebox.askyesno(
                "Delete Profile",
                "Are you sure you want to delete this profile?\n"
                "Chat history will not be deleted."):
            return
        self.s.profile_manager.delete_profile(profile_id)
        if (hasattr(self.s, '_selector_frame') and
                self.s._selector_frame and
                self.s._selector_frame.winfo_exists()):
            self.s._selector_frame.destroy()
        profiles = self.s.profile_manager.get_all_profiles()
        if not profiles:
            self._show_create_profile_dialog(first_run=True)
        else:
            self.show_profile_selector()

    def _show_edit_profile_dialog(self, profile):
        dlg = tk.Toplevel(self.s.window)
        dlg.title("Edit Profile")
        dlg.geometry("440x580")
        dlg.configure(bg=self.T.C_BG)
        dlg.resizable(False, False)
        dlg.transient(self.s.window)
        dlg.grab_set()

        tk.Label(dlg, text="Edit Profile",
                 font=self.T.FONT_XL_B, fg=self.T.C_NAVY,
                 bg=self.T.C_BG).pack(pady=(20, 16))

        tk.Label(dlg, text="Display Name", font=self.T.FONT_SM_B,
                 fg=self.T.C_TEXT_MED, bg=self.T.C_BG).pack(anchor=tk.W, padx=40)
        name_var = tk.StringVar(value=profile["display_name"])
        name_entry = tk.Entry(dlg, textvariable=name_var, font=self.T.FONT,
                              bg=self.T.C_WHITE, fg=self.T.C_TEXT,
                              insertbackground=self.T.C_AQUA, relief=tk.FLAT)
        name_entry.pack(fill=tk.X, padx=40, pady=(4, 12), ipady=6)
        name_entry.focus_set()

        preview_label = tk.Label(dlg, bg=self.T.C_BG)
        preview_label.pack(pady=(4, 8))
        dlg._preview_photo = None
        dlg._preview_timer = None

        tk.Label(dlg, text="Avatar Style", font=self.T.FONT_SM_B,
                 fg=self.T.C_TEXT_MED, bg=self.T.C_BG).pack(anchor=tk.W, padx=40)
        style_var = tk.StringVar(value=profile.get("avatar_style", "adventurer"))
        style_frame = tk.Frame(dlg, bg=self.T.C_BG)
        style_frame.pack(fill=tk.X, padx=40, pady=(4, 12))

        style_buttons = {}
        dlg_photos = []

        def _update_preview(*_args):
            seed = name_var.get().strip() or profile["display_name"]
            img = self.s.avatar_gen.generate(seed, style_var.get(), size=80)
            img = self.w.circle_crop(img, 80)
            photo = ImageTk.PhotoImage(img)
            dlg._preview_photo = photo
            preview_label.config(image=photo)

        def _highlight_selected():
            for s, btn in style_buttons.items():
                if s == style_var.get():
                    btn.configure(bg=self.T.C_AQUA_LT, highlightthickness=2,
                                  highlightbackground=self.T.C_AQUA)
                else:
                    btn.configure(bg=self.T.C_GLASS, highlightthickness=0)

        for i, style in enumerate(self.T.AVATAR_STYLES):
            r, c = divmod(i, 3)
            btn_frame = tk.Frame(style_frame, bg=self.T.C_BG)
            btn_frame.grid(row=r, column=c, padx=4, pady=4)
            preview = self.s.avatar_gen.generate("Preview", style, size=48)
            preview = self.w.circle_crop(preview, 48)
            photo = ImageTk.PhotoImage(preview)
            dlg_photos.append(photo)

            def _pick(s=style):
                style_var.set(s)
                _highlight_selected()
                _update_preview()

            btn = tk.Button(btn_frame, image=photo, bg=self.T.C_GLASS,
                            relief=tk.FLAT, cursor="hand2", bd=0, command=_pick)
            btn.pack()
            style_buttons[style] = btn
            tk.Label(btn_frame, text=style[:10], font=self.T.FONT_XS,
                     fg=self.T.C_TEXT_LT, bg=self.T.C_BG).pack()

        dlg._photos = dlg_photos
        _highlight_selected()
        _update_preview()

        def _on_name_change(*_args):
            if dlg._preview_timer:
                dlg.after_cancel(dlg._preview_timer)
            dlg._preview_timer = dlg.after(300, _update_preview)

        name_var.trace_add("write", _on_name_change)

        def _on_save():
            name = name_var.get().strip()
            if not name:
                messagebox.showwarning("Name Required",
                                       "Please enter a display name.", parent=dlg)
                return
            self.s.profile_manager.update_profile(
                profile["id"], display_name=name,
                avatar_style=style_var.get(), avatar_seed=name)
            self.s.avatar_gen.clear_cache()
            dlg.destroy()
            if self.s.current_user and self.s.current_user["id"] == profile["id"]:
                self.s.current_user = self.s.profile_manager.get_profile(profile["id"])
                user_img = self.s.avatar_gen.generate(
                    self.s.current_user["avatar_seed"],
                    self.s.current_user["avatar_style"], size=40)
                user_img = self.w.circle_crop(user_img, 40)
                self.s._user_avatar_photo = ImageTk.PhotoImage(user_img)
                self.s.window.title(
                    f"PuffPAI ({self.s.current_user['display_name']})")
            if (hasattr(self.s, '_selector_frame') and
                    self.s._selector_frame and
                    self.s._selector_frame.winfo_exists()):
                self.s._selector_frame.destroy()
                self.show_profile_selector()

        save_btn = tk.Button(dlg, text="Save Changes", font=self.T.FONT_B,
                             bg=self.T.C_AQUA, fg=self.T.C_WHITE,
                             relief=tk.FLAT, cursor="hand2", pady=10, padx=20,
                             command=_on_save)
        save_btn.pack(pady=(8, 20))
        self.w.bind_hover(save_btn, self.T.C_AQUA, self.T.C_AQUA_MID)
        dlg.bind("<Return>", lambda e: _on_save())

    def _show_create_profile_dialog(self, first_run=False):
        dlg = tk.Toplevel(self.s.window)
        dlg.title("Create Profile")
        dlg.geometry("440x580")
        dlg.configure(bg=self.T.C_BG)
        dlg.resizable(False, False)
        dlg.transient(self.s.window)
        dlg.grab_set()
        if first_run:
            dlg.protocol("WM_DELETE_WINDOW", lambda: None)

        tk.Label(dlg, text="Create Your Profile",
                 font=self.T.FONT_XL_B, fg=self.T.C_NAVY,
                 bg=self.T.C_BG).pack(pady=(20, 16))

        tk.Label(dlg, text="Display Name", font=self.T.FONT_SM_B,
                 fg=self.T.C_TEXT_MED, bg=self.T.C_BG).pack(anchor=tk.W, padx=40)
        name_var = tk.StringVar()
        name_entry = tk.Entry(dlg, textvariable=name_var, font=self.T.FONT,
                              bg=self.T.C_WHITE, fg=self.T.C_TEXT,
                              insertbackground=self.T.C_AQUA, relief=tk.FLAT)
        name_entry.pack(fill=tk.X, padx=40, pady=(4, 12), ipady=6)
        name_entry.focus_set()

        preview_label = tk.Label(dlg, bg=self.T.C_BG)
        preview_label.pack(pady=(4, 8))
        dlg._preview_photo = None

        tk.Label(dlg, text="Avatar Style", font=self.T.FONT_SM_B,
                 fg=self.T.C_TEXT_MED, bg=self.T.C_BG).pack(anchor=tk.W, padx=40)
        style_var = tk.StringVar(value="adventurer")
        style_frame = tk.Frame(dlg, bg=self.T.C_BG)
        style_frame.pack(fill=tk.X, padx=40, pady=(4, 12))

        style_buttons = {}
        dlg_photos = []

        def _update_preview(*_args):
            seed = name_var.get().strip() or "Preview"
            img = self.s.avatar_gen.generate(seed, style_var.get(), size=80)
            img = self.w.circle_crop(img, 80)
            photo = ImageTk.PhotoImage(img)
            dlg._preview_photo = photo
            preview_label.config(image=photo)

        def _highlight_selected():
            for s, btn in style_buttons.items():
                if s == style_var.get():
                    btn.configure(bg=self.T.C_AQUA_LT, highlightthickness=2,
                                  highlightbackground=self.T.C_AQUA)
                else:
                    btn.configure(bg=self.T.C_GLASS, highlightthickness=0)

        for i, style in enumerate(self.T.AVATAR_STYLES):
            r, c = divmod(i, 3)
            btn_frame = tk.Frame(style_frame, bg=self.T.C_BG)
            btn_frame.grid(row=r, column=c, padx=4, pady=4)
            preview = self.s.avatar_gen.generate("Preview", style, size=48)
            preview = self.w.circle_crop(preview, 48)
            photo = ImageTk.PhotoImage(preview)
            dlg_photos.append(photo)

            def _pick(s=style):
                style_var.set(s)
                _highlight_selected()
                _update_preview()

            btn = tk.Button(btn_frame, image=photo, bg=self.T.C_GLASS,
                            relief=tk.FLAT, cursor="hand2", bd=0, command=_pick)
            btn.pack()
            style_buttons[style] = btn
            tk.Label(btn_frame, text=style[:10], font=self.T.FONT_XS,
                     fg=self.T.C_TEXT_LT, bg=self.T.C_BG).pack()

        dlg._photos = dlg_photos
        _highlight_selected()
        _update_preview()
        dlg._preview_timer = None

        def _on_name_change(*_args):
            if dlg._preview_timer:
                dlg.after_cancel(dlg._preview_timer)
            dlg._preview_timer = dlg.after(300, _update_preview)

        name_var.trace_add("write", _on_name_change)

        def _on_create():
            name = name_var.get().strip()
            if not name:
                messagebox.showwarning("Name Required",
                                       "Please enter a display name.", parent=dlg)
                return
            profile = self.s.profile_manager.create_profile(
                name, avatar_style=style_var.get(), avatar_seed=name)
            dlg.destroy()
            if first_run:
                self._select_profile(profile["id"])
            else:
                if (hasattr(self.s, '_selector_frame') and
                        self.s._selector_frame and
                        self.s._selector_frame.winfo_exists()):
                    self.s._selector_frame.destroy()
                self.show_profile_selector()

        create_btn = tk.Button(dlg, text="Create Profile", font=self.T.FONT_B,
                               bg=self.T.C_AQUA, fg=self.T.C_WHITE,
                               relief=tk.FLAT, cursor="hand2", pady=10, padx=20,
                               command=_on_create)
        create_btn.pack(pady=(8, 20))
        self.w.bind_hover(create_btn, self.T.C_AQUA, self.T.C_AQUA_MID)
        dlg.bind("<Return>", lambda e: _on_create())

    def _show_settings_dialog(self):
        identity = self.s.profile_manager.get_identity()
        dlg = tk.Toplevel(self.s.window)
        dlg.title("PuffPAI Settings")
        dlg.geometry("400x360")
        dlg.configure(bg=self.T.C_BG)
        dlg.resizable(False, False)
        dlg.transient(self.s.window)
        dlg.grab_set()

        tk.Label(dlg, text="PuffPAI Identity",
                 font=self.T.FONT_XL_B, fg=self.T.C_NAVY,
                 bg=self.T.C_BG).pack(pady=(20, 4))
        tk.Label(dlg, text="Customise your AI assistant",
                 font=self.T.FONT_XS_I, fg=self.T.C_TEXT_LT,
                 bg=self.T.C_BG).pack(pady=(0, 16))

        tk.Label(dlg, text="AI Name", font=self.T.FONT_SM_B,
                 fg=self.T.C_TEXT_MED, bg=self.T.C_BG).pack(anchor=tk.W, padx=40)
        name_var = tk.StringVar(value=identity["name"])
        name_entry = tk.Entry(dlg, textvariable=name_var, font=self.T.FONT,
                              bg=self.T.C_WHITE, fg=self.T.C_TEXT,
                              insertbackground=self.T.C_AQUA, relief=tk.FLAT)
        name_entry.pack(fill=tk.X, padx=40, pady=(4, 16), ipady=6)

        tk.Label(dlg, text="Avatar Style", font=self.T.FONT_SM_B,
                 fg=self.T.C_TEXT_MED, bg=self.T.C_BG).pack(anchor=tk.W, padx=40)
        style_var = tk.StringVar(value=identity["avatar_style"])
        style_menu = ttk.Combobox(dlg, textvariable=style_var,
                                  values=self.T.AVATAR_STYLES,
                                  state="readonly", font=self.T.FONT)
        style_menu.pack(fill=tk.X, padx=40, pady=(4, 16))

        preview_label = tk.Label(dlg, bg=self.T.C_BG)
        preview_label.pack(pady=(4, 12))
        dlg._preview_photo = None
        dlg._preview_timer = None

        def _update_preview(*_args):
            img = self.s.avatar_gen.generate(
                name_var.get() or "PuffPAI", style_var.get(), size=80)
            img = self.w.circle_crop(img, 80)
            photo = ImageTk.PhotoImage(img)
            dlg._preview_photo = photo
            preview_label.config(image=photo)

        def _on_change(*_args):
            if dlg._preview_timer:
                dlg.after_cancel(dlg._preview_timer)
            dlg._preview_timer = dlg.after(300, _update_preview)

        style_var.trace_add("write", _on_change)
        name_var.trace_add("write", _on_change)
        _update_preview()

        def _on_save():
            name = name_var.get().strip() or "PuffPAI"
            self.s.profile_manager.set_identity(
                name=name, avatar_style=style_var.get(), avatar_seed=name)
            dlg.destroy()
            if self.s.current_user:
                self.s.ai_name = name
                ai_img = self.s.avatar_gen.generate(name, style_var.get(), 40)
                ai_img = self.w.circle_crop(ai_img, 40)
                self.s._ai_avatar_photo = ImageTk.PhotoImage(ai_img)

        save_btn = tk.Button(dlg, text="Save", font=self.T.FONT_B,
                             bg=self.T.C_AQUA, fg=self.T.C_WHITE,
                             relief=tk.FLAT, cursor="hand2", pady=8, padx=20,
                             command=_on_save)
        save_btn.pack(pady=(0, 16))
        self.w.bind_hover(save_btn, self.T.C_AQUA, self.T.C_AQUA_MID)

    def switch_profile(self, save_fn=None):
        """Save current chat, tear down UI, return to profile selector."""
        if self.s.current_chat_id and self.s.conversation_history and save_fn:
            save_fn()
        for widget in self.s.window.winfo_children():
            widget.destroy()
        self.s.conversation_history = []
        self.s.current_chat_id = None
        self.s.current_chat_title = None
        self.s.message_feedback = {}
        self.s._filtered_chat_files = []
        self.show_profile_selector()
