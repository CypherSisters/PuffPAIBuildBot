"""Chat history tile manager — save, load, rename, delete chats.

Extracted from app_ui.py — all chat file I/O and tile rendering lives here.
"""

import json
import os
import tkinter as tk
from tkinter import messagebox, simpledialog
from datetime import datetime

from .theme import PuffPAITheme
from .widgets import AeroWidgets


class ChatHistoryManager:
    """Manages the sidebar chat-history tile list and JSON persistence."""

    def __init__(self, state, widgets: AeroWidgets, theme: PuffPAITheme):
        self.s = state
        self.w = widgets
        self.T = theme

    # ── Chat tile rendering ──────────────────────────────────────────────

    def _make_chat_tile(self, parent, title, timestamp_str, idx):
        accent = self.T.TILE_ACCENTS[idx % len(self.T.TILE_ACCENTS)]
        tile_h = 64
        tile_bg = self.T.C_WHITE
        tile_bg_hover = self.w.tint(accent, 0.85)

        tile = tk.Canvas(parent, height=tile_h, bg=self.T.C_GLASS,
                         highlightthickness=0, bd=0, cursor="hand2")
        tile.pack(fill=tk.X, padx=6, pady=3)

        def _redraw(bg=tile_bg):
            tile.delete("all")
            w = tile.winfo_width() or 240
            self.w.rounded_rect(tile, 0, 2, w, tile_h - 2, r=14,
                                fill=bg, outline="", tags="bg_rect")
            self.w.rounded_rect(tile, 0, 10, 6, tile_h - 10, r=3,
                                fill=accent, outline="")
            short_title = title[:26] + "\u2026" if len(title) > 26 else title
            tile.create_text(16, 20, text=short_title,
                             fill=self.T.C_TEXT, font=self.T.FONT_SM_B, anchor="w")
            tile.create_text(16, 42, text=timestamp_str,
                             fill=self.T.C_TEXT_LT, font=self.T.FONT_XS, anchor="w")
            tile.tag_lower("bg_rect")

        tile.bind("<Configure>", lambda e: _redraw())
        self.s.window.after(20, _redraw)
        tile.bind("<Enter>", lambda e: _redraw(tile_bg_hover))
        tile.bind("<Leave>", lambda e: _redraw(tile_bg))
        tile.bind("<Button-1>", lambda e, i=idx: self.load_chat_by_index(i))
        tile.bind("<Button-3>", lambda e, i=idx: self.show_chat_context_menu(e, i))
        return tile

    # ── Load list ────────────────────────────────────────────────────────

    def load_chat_list(self):
        """Rebuild the tile-based chat history list, filtered by current user."""
        for w in self.s._tile_frame.winfo_children():
            w.destroy()

        chat_files = sorted(
            [f for f in os.listdir(self.s.chat_history_dir)
             if f.endswith('.json')],
            reverse=True)

        current_uid = self.s.current_user["id"] if self.s.current_user else None
        self.s._filtered_chat_files = []
        display_idx = 0

        for chat_file in chat_files:
            try:
                with open(os.path.join(self.s.chat_history_dir, chat_file),
                          'r', encoding='utf-8') as f:
                    chat_data = json.load(f)

                file_uid = chat_data.get('user_id')
                if file_uid and current_uid and file_uid != current_uid:
                    continue

                self.s._filtered_chat_files.append(chat_file)
                ts = datetime.fromisoformat(chat_data['timestamp'])
                timestamp_str = ts.strftime('%b %d  %H:%M')
                self._make_chat_tile(self.s._tile_frame, chat_data['title'],
                                     timestamp_str, display_idx)
                display_idx += 1
            except Exception as e:
                print(f"Error loading {chat_file}: {e}")

        self.s._tile_frame.update_idletasks()
        self.s._tile_canvas.configure(
            scrollregion=self.s._tile_canvas.bbox("all"))

    # ── Load single chat ─────────────────────────────────────────────────

    def load_chat_by_index(self, idx, add_bubble_fn=None, clear_display_fn=None):
        """Load a chat by its position in the filtered file list."""
        if self.s.current_chat_id and self.s.conversation_history:
            self.save_current_chat()

        if idx >= len(self.s._filtered_chat_files):
            return

        chat_file = os.path.join(self.s.chat_history_dir,
                                 self.s._filtered_chat_files[idx])
        try:
            with open(chat_file, 'r', encoding='utf-8') as f:
                chat_data = json.load(f)

            self.s.current_chat_id = chat_data['id']
            self.s.current_chat_title = chat_data['title']
            self.s.persona = chat_data.get('persona', self.s.persona)
            self.s.conversation_history = chat_data['messages']
            self.s.message_feedback = {}

            if clear_display_fn:
                clear_display_fn()
            if self.s.chat_title:
                self.s.chat_title.config(text=chat_data['title'])

            if add_bubble_fn:
                for msg in self.s.conversation_history:
                    msg_ts = msg.get('timestamp')
                    add_bubble_fn(msg['user'], is_user=True, timestamp=msg_ts)
                    add_bubble_fn(msg['assistant'], is_user=False,
                                  message_id=msg.get('message_id'),
                                  timestamp=msg_ts)

            # Ensure scroll region is updated and scroll to bottom
            if hasattr(self.s, 'chat_canvas') and self.s.chat_canvas:
                self.s.chat_canvas.update_idletasks()
                self.s.chat_canvas.configure(
                    scrollregion=self.s.chat_canvas.bbox("all"))
                self.s.chat_canvas.yview_moveto(1.0)

            if self.s.status_label:
                self.s.status_label.config(
                    text=f"\u25cf  Loaded chat from {chat_data['timestamp']}")

        except Exception as e:
            messagebox.showerror("Error", f"Failed to load chat: {e}")

    # ── Save / rename / delete ───────────────────────────────────────────

    def save_current_chat(self):
        if not self.s.conversation_history:
            return
        chat_file = os.path.join(self.s.chat_history_dir,
                                 f"{self.s.current_chat_id}.json")
        title = (self.s.current_chat_title or
                 (self.s.conversation_history[0]['user'][:50]
                  if self.s.conversation_history else "New Chat"))
        user_id = self.s.current_user["id"] if self.s.current_user else None
        with open(chat_file, 'w', encoding='utf-8') as f:
            json.dump({
                'id': self.s.current_chat_id,
                'user_id': user_id,
                'title': title,
                'timestamp': datetime.now().isoformat(),
                'persona': self.s.persona,
                'messages': self.s.conversation_history
            }, f, indent=2, ensure_ascii=False)

    def rename_chat(self, idx):
        if idx >= len(self.s._filtered_chat_files):
            return
        chat_file = os.path.join(self.s.chat_history_dir,
                                 self.s._filtered_chat_files[idx])
        try:
            with open(chat_file, 'r', encoding='utf-8') as f:
                chat_data = json.load(f)
            new_title = simpledialog.askstring(
                "Rename Chat", "Enter new chat name:",
                initialvalue=chat_data['title'])
            if new_title and new_title.strip():
                chat_data['title'] = new_title.strip()
                with open(chat_file, 'w', encoding='utf-8') as f:
                    json.dump(chat_data, f, indent=2, ensure_ascii=False)
                if self.s.current_chat_id == chat_data['id']:
                    self.s.current_chat_title = new_title.strip()
                    if self.s.chat_title:
                        self.s.chat_title.config(text=new_title.strip())
                self.load_chat_list()
                if self.s.status_label:
                    self.s.status_label.config(text="\u25cf  Chat renamed")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to rename chat: {e}")

    def delete_selected_chat(self, idx):
        if not messagebox.askyesno("Delete Chat",
                                   "Are you sure you want to delete this chat?"):
            return
        if idx >= len(self.s._filtered_chat_files):
            return
        try:
            os.remove(os.path.join(self.s.chat_history_dir,
                                   self.s._filtered_chat_files[idx]))
            self.load_chat_list()
            if self.s.status_label:
                self.s.status_label.config(text="\u25cf  Chat deleted")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to delete chat: {e}")

    def show_chat_context_menu(self, event, idx):
        self.s._context_tile_idx = idx
        menu = tk.Menu(self.s.window, tearoff=0)
        menu.add_command(label="Rename Chat",
                         command=lambda: self.rename_chat(idx))
        menu.add_command(label="Delete Chat",
                         command=lambda: self.delete_selected_chat(idx))
        menu.post(event.x_root, event.y_root)

    def new_chat(self, add_bubble_fn=None, clear_display_fn=None):
        if self.s.conversation_history and not messagebox.askyesno(
                "New Chat",
                "Start a new chat? Current conversation will be saved."):
            return
        if self.s.current_chat_id:
            self.save_current_chat()
        self.s.current_chat_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.s.current_chat_title = None
        self.s.conversation_history = []
        self.s.message_feedback = {}
        if clear_display_fn:
            clear_display_fn()
        if self.s.chat_title:
            self.s.chat_title.config(text="New Chat")
        if add_bubble_fn:
            add_bubble_fn("[System] New chat started \u2014 full context available",
                          is_system=True)
        self.load_chat_list()

    def clear_current_chat(self, add_bubble_fn=None, clear_display_fn=None,
                           check_db_fn=None, check_ollama_fn=None):
        if not messagebox.askyesno("Clear Chat", "Clear current conversation?"):
            return
        self.s.conversation_history = []
        self.s.current_chat_title = None
        self.s.message_feedback = {}
        if clear_display_fn:
            clear_display_fn()
        if add_bubble_fn:
            add_bubble_fn("[System] Chat cleared \u2014 fresh start", is_system=True)
        if check_db_fn:
            check_db_fn()
        if check_ollama_fn:
            check_ollama_fn()
