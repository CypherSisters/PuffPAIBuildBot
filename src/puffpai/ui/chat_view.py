"""Chat bubble rendering, RAG/Ollama/agent dispatch, feedback.

Extracted from app_ui.py — the core chat experience module.
"""

import json
import os
import re
import uuid
import tkinter as tk
from datetime import datetime

import requests

from .theme import PuffPAITheme
from .widgets import AeroWidgets
from puffpai.security.lan_guard import no_proxies as _no_proxies  # marked home-network calls never use a proxy


class ChatView:
    """Manages chat bubble rendering and message dispatch."""

    _cached_libraries: list | None = None
    _cached_libraries_path: str | None = None

    def __init__(self, state, widgets: AeroWidgets, theme: PuffPAITheme):
        self.s = state
        self.w = widgets
        self.T = theme

    # ── Bubble timestamp formatting ──────────────────────────────────────

    @staticmethod
    def format_bubble_timestamp(iso_str):
        try:
            dt = datetime.fromisoformat(iso_str)
            now = datetime.now()
            if dt.date() == now.date():
                return dt.strftime("Today %I:%M %p").lstrip("0").replace(" 0", " ")
            elif (now - dt).days < 7:
                return dt.strftime("%A %I:%M %p").replace(" 0", " ")
            else:
                return dt.strftime("%b %d, %Y  %I:%M %p").replace(" 0", " ")
        except Exception:
            return ""

    # ── Response post-processing ─────────────────────────────────────────

    @staticmethod
    def clean_response(text):
        text = re.sub(r'^#{1,6}\s*(.+)$', r'\1.', text, flags=re.MULTILINE)
        text = re.sub(r'\*{1,3}([^*]+)\*{1,3}', r'\1', text)
        text = re.sub(r'_{1,3}([^_]+)_{1,3}', r'\1', text)
        text = re.sub(r'`([^`]+)`', r'\1', text)
        text = re.sub(r'```[^\n]*\n(.*?)```', r'\1', text, flags=re.DOTALL)
        text = re.sub(r'^\s*[-*\u2022]\s+', '', text, flags=re.MULTILINE)
        text = re.sub(r'^\s*\d+\.\s+', '', text, flags=re.MULTILINE)
        text = re.sub(r'^[-*_]{3,}\s*$', '', text, flags=re.MULTILINE)
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()

    # ── Token estimation ─────────────────────────────────────────────────

    @staticmethod
    def estimate_tokens(text):
        return len(text) // 4

    # ── Bubble rendering ─────────────────────────────────────────────────

    def add_message_bubble(self, message, is_user=False, is_system=False,
                           message_id=None, timestamp=None):
        msg_container = tk.Frame(self.s.chat_frame, bg=self.T.C_BG)
        msg_container.pack(fill=tk.X, padx=16, pady=6)

        ts_text = self.format_bubble_timestamp(timestamp) if timestamp else ""

        if is_system:
            tk.Label(msg_container, text=message,
                     font=self.T.FONT_XS_I, fg=self.T.C_TEXT_LT,
                     bg=self.T.C_BG).pack(anchor=tk.CENTER)

        elif is_user:
            bubble_frame = tk.Frame(msg_container, bg=self.T.C_BG)
            bubble_frame.pack(side=tk.RIGHT)

            header_row = tk.Frame(bubble_frame, bg=self.T.C_BG)
            header_row.pack(anchor=tk.E, padx=4, pady=(0, 2))
            user_name = (self.s.current_user["display_name"]
                         if self.s.current_user else "You")
            tk.Label(header_row, text=user_name, font=self.T.FONT_SM_B,
                     fg=self.T.C_TEXT_MED, bg=self.T.C_BG).pack(
                         side=tk.LEFT, padx=(0, 4))
            if self.s._user_avatar_photo:
                tk.Label(header_row, image=self.s._user_avatar_photo,
                         bg=self.T.C_BG).pack(side=tk.LEFT)

            bubble = self.w.make_rounded_bubble(
                bubble_frame, message,
                bg_color=self.T.C_USR_BG, fg_color=self.T.C_USR_FG,
                font=self.T.FONT, wraplength=460, radius=22)
            bubble.pack(side=tk.RIGHT)

            if ts_text:
                tk.Label(bubble_frame, text=ts_text, font=self.T.FONT_XS,
                         fg=self.T.C_TEXT_LT, bg=self.T.C_BG).pack(
                             anchor=tk.E, padx=6, pady=(2, 0))

        else:
            bubble_frame = tk.Frame(msg_container, bg=self.T.C_BG)
            bubble_frame.pack(side=tk.LEFT, fill=tk.X, expand=True)

            header_row = tk.Frame(bubble_frame, bg=self.T.C_BG)
            header_row.pack(anchor=tk.W, padx=4, pady=(0, 2))
            if self.s._ai_avatar_photo:
                tk.Label(header_row, image=self.s._ai_avatar_photo,
                         bg=self.T.C_BG).pack(side=tk.LEFT, padx=(0, 4))
            tk.Label(header_row, text=self.s.ai_name,
                     font=self.T.FONT_SM_B, fg=self.T.C_AQUA,
                     bg=self.T.C_BG).pack(side=tk.LEFT)

            bubble = self.w.make_rounded_bubble(
                bubble_frame, message,
                bg_color=self.T.C_AI_BG, fg_color=self.T.C_TEXT,
                font=self.T.FONT, wraplength=480, radius=22)
            bubble.pack(side=tk.LEFT)

            if message_id:
                fb_frame = tk.Frame(bubble_frame, bg=self.T.C_BG)
                fb_frame.pack(side=tk.LEFT, padx=8, anchor=tk.S)
                if message_id in self.s.message_feedback:
                    rated = self.s.message_feedback[message_id]
                    rated_color = self.T.C_MINT if rated == "positive" else self.T.C_ROSE
                    rated_text = "\U0001f44d" if rated == "positive" else "\U0001f44e"
                    tk.Label(fb_frame, text=rated_text, font=(self.s._FF, 13),
                             bg=self.T.C_BG, fg=rated_color).pack()
                else:
                    for emoji, ftype in [("\U0001f44d", "positive"),
                                         ("\U0001f44e", "negative")]:
                        btn = tk.Button(
                            fb_frame, text=emoji, font=(self.s._FF, 12),
                            bg=self.T.C_BG, fg=self.T.C_TEXT_LT,
                            relief=tk.FLAT, cursor="hand2", bd=0,
                            activebackground=self.T.C_BG,
                            command=lambda m=message_id, ft=ftype:
                                self.record_feedback(m, ft))
                        btn.pack(pady=1)
                        self.w.bind_hover(btn, self.T.C_BG, self.T.C_PALE)

            if ts_text:
                tk.Label(bubble_frame, text=ts_text, font=self.T.FONT_XS,
                         fg=self.T.C_TEXT_LT, bg=self.T.C_BG).pack(
                             anchor=tk.W, padx=6, pady=(2, 0))

        self.scroll_to_bottom()

    # ── Feedback ─────────────────────────────────────────────────────────

    def record_feedback(self, message_id, feedback_type):
        if message_id not in self.s.message_feedback:
            self.s.message_feedback[message_id] = feedback_type
            entry = {
                'message_id': message_id,
                'chat_id': self.s.current_chat_id,
                'timestamp': datetime.now().isoformat(),
                'feedback': feedback_type,
                'user_query': None,
                'assistant_response': None,
            }
            for msg in self.s.conversation_history:
                if msg.get('message_id') == message_id:
                    entry['user_query'] = msg['user']
                    entry['assistant_response'] = msg['assistant']
                    break
            with open(self.s.feedback_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(entry) + '\n')
            print(f"[RLHF] Recorded {feedback_type} feedback for message {message_id}")
            if self.s.status_label:
                self.s.status_label.config(
                    text=f"\u25cf  Feedback recorded: {feedback_type}")

    # ── Streaming bubble ────────────────────────────────────────────

    def add_streaming_bubble(self, message_id=None, timestamp=None):
        """Create an empty AI bubble that can be updated character-by-character.

        Returns a dict with 'update(text)', 'finish(text, message_id)' callables.
        Must be called from the main thread.
        """
        msg_container = tk.Frame(self.s.chat_frame, bg=self.T.C_BG)
        msg_container.pack(fill=tk.X, padx=16, pady=6)

        bubble_frame = tk.Frame(msg_container, bg=self.T.C_BG)
        bubble_frame.pack(side=tk.LEFT, fill=tk.X, expand=True)

        header_row = tk.Frame(bubble_frame, bg=self.T.C_BG)
        header_row.pack(anchor=tk.W, padx=4, pady=(0, 2))
        if self.s._ai_avatar_photo:
            tk.Label(header_row, image=self.s._ai_avatar_photo,
                     bg=self.T.C_BG).pack(side=tk.LEFT, padx=(0, 4))
        tk.Label(header_row, text=self.s.ai_name,
                 font=self.T.FONT_SM_B, fg=self.T.C_AQUA,
                 bg=self.T.C_BG).pack(side=tk.LEFT)

        # Text label that will be updated as tokens arrive
        text_label = tk.Label(
            bubble_frame, text="\u2588",  # Blinking cursor
            font=self.T.FONT, fg=self.T.C_TEXT, bg=self.T.C_AI_BG,
            wraplength=480, justify=tk.LEFT, anchor=tk.NW,
            padx=14, pady=10)
        text_label.pack(side=tk.LEFT, fill=tk.X)

        # Placeholder for feedback + timestamp
        footer_frame = tk.Frame(bubble_frame, bg=self.T.C_BG)
        footer_frame.pack(anchor=tk.W, padx=6, pady=(2, 0))

        self.scroll_to_bottom()

        _last_scroll = [0.0]

        def _update(text: str):
            """Update the bubble text (call from main thread)."""
            text_label.config(text=text + " \u2588")
            import time
            now = time.time()
            if now - _last_scroll[0] > 0.15:  # Throttle to ~7 FPS
                _last_scroll[0] = now
                self.scroll_to_bottom()

        def _finish(final_text: str, mid: str | None = None):
            """Finalize the bubble with the complete text."""
            text_label.destroy()
            # Replace with proper rounded bubble
            bubble = self.w.make_rounded_bubble(
                bubble_frame, final_text,
                bg_color=self.T.C_AI_BG, fg_color=self.T.C_TEXT,
                font=self.T.FONT, wraplength=480, radius=22)
            bubble.pack(side=tk.LEFT)

            # Add feedback buttons
            if mid:
                fb_frame = tk.Frame(bubble_frame, bg=self.T.C_BG)
                fb_frame.pack(side=tk.LEFT, padx=8, anchor=tk.S)
                for emoji, ftype in [("\U0001f44d", "positive"),
                                     ("\U0001f44e", "negative")]:
                    btn = tk.Button(
                        fb_frame, text=emoji, font=(self.s._FF, 12),
                        bg=self.T.C_BG, fg=self.T.C_TEXT_LT,
                        relief=tk.FLAT, cursor="hand2", bd=0,
                        activebackground=self.T.C_BG,
                        command=lambda m=mid, ft=ftype:  # type: ignore[misc]
                            self.record_feedback(m, ft))
                    btn.pack(pady=1)
                    self.w.bind_hover(btn, self.T.C_BG, self.T.C_PALE)

            ts_text = self.format_bubble_timestamp(timestamp) if timestamp else ""
            if ts_text:
                tk.Label(footer_frame, text=ts_text, font=self.T.FONT_XS,
                         fg=self.T.C_TEXT_LT, bg=self.T.C_BG).pack(anchor=tk.W)

            self.scroll_to_bottom()

        return {"update": _update, "finish": _finish}

    # ── Scroll helpers ───────────────────────────────────────────────────

    def scroll_to_bottom(self):
        if self.s.chat_canvas:
            self.s.chat_canvas.update_idletasks()
            self.s.chat_canvas.yview_moveto(1.0)

    def clear_chat_display(self):
        if self.s.chat_frame:
            for widget in self.s.chat_frame.winfo_children():
                widget.destroy()

    # ── RAG context ──────────────────────────────────────────────────────

    @staticmethod
    def _select_relevant_collections(question: str, collections: list,
                                      max_collections: int = 8) -> list:
        """Pre-filter collections to only query the most likely relevant ones.

        Uses keyword overlap between the question and library names to avoid
        querying all 52+ collections on every message.
        """
        if not collections:
            return []

        q_words = set(question.lower().split())
        # Remove very common words that don't help with selection
        stop_words = {'the', 'a', 'an', 'is', 'are', 'was', 'were', 'what',
                      'how', 'why', 'when', 'where', 'who', 'which', 'do',
                      'does', 'did', 'can', 'could', 'would', 'should',
                      'will', 'shall', 'may', 'might', 'have', 'has', 'had',
                      'be', 'been', 'being', 'to', 'of', 'in', 'for', 'on',
                      'with', 'at', 'by', 'from', 'about', 'into', 'through',
                      'it', 'its', 'this', 'that', 'these', 'those', 'my',
                      'me', 'i', 'you', 'your', 'we', 'our', 'they', 'them',
                      'and', 'or', 'but', 'not', 'no', 'if', 'so', 'up',
                      'out', 'just', 'than', 'then', 'now', 'here', 'there',
                      'tell', 'know', 'think', 'want', 'need', 'like', 'get',
                      'make', 'go', 'see', 'look', 'find', 'give', 'use'}
        q_words -= stop_words

        scored = []
        for coll in collections:
            lib_name = coll.get('library', '').lower()
            lib_words = set(lib_name.replace('-', ' ').replace('_', ' ').split())

            # Score: number of overlapping meaningful words
            overlap = len(q_words & lib_words)

            # Boost: check if any question word is a substring of the library name
            substring_boost = sum(1 for w in q_words if len(w) > 3 and w in lib_name)

            score = overlap * 2 + substring_boost
            scored.append((score, coll))

        # Sort by score descending, take top matches
        scored.sort(key=lambda x: x[0], reverse=True)

        # Always include Wikipedia (general knowledge fallback) if available
        selected = []
        wiki_added = False
        for score, coll in scored:
            if score > 0:
                selected.append(coll)
            elif not wiki_added and 'wikipedia' in coll.get('library', '').lower():
                selected.append(coll)
                wiki_added = True
            if len(selected) >= max_collections:
                break

        # If no keyword matches at all, fall back to general-purpose collections
        if not selected:
            general_names = {'wikipedia', 'wiki', 'encyclopedia', 'stack exchange'}
            for coll in collections:
                lib_lower = coll.get('library', '').lower()
                if any(g in lib_lower for g in general_names):
                    selected.append(coll)
                if len(selected) >= 5:
                    break

        return selected

    def get_rag_context(self, question, n_results=12):
        all_results = []

        # ── Load indexed library metadata (cached) ───────────────────
        if ChatView._cached_libraries is None:
            try:
                lib_path = os.path.join(self.s.src_dir, 'indexed_libraries.json')
                with open(lib_path, 'r') as f:
                    ChatView._cached_libraries = json.load(f)
                ChatView._cached_libraries_path = lib_path
            except Exception:
                ChatView._cached_libraries = []
        collections = ChatView._cached_libraries

        # ── If the question is about the libraries themselves, include
        #    the library inventory as context so the LLM can answer ──
        q_lower = question.lower()
        is_library_meta_query = (
            any(k in q_lower for k in ('librar', 'database', 'indexed',
                                        'collection', 'knowledge base'))
            and any(k in q_lower for k in ('list', 'how many', 'what',
                                            'which', 'show', 'tell',
                                            'about', 'describe', 'have',
                                            'available', 'access'))
        )

        if is_library_meta_query and collections:
            names = [c['library'] for c in collections]
            inventory = (
                f"You have {len(collections)} indexed knowledge libraries:\n"
                + "\n".join(f"  {i+1}. {lib}" for i, lib in enumerate(names))
            )
            all_results.append({
                'text': inventory,
                'library': 'Library Inventory',
                'distance': 0.0,  # highest priority
            })

        # ── Query relevant indexed ZIM library collections (parallel) ─
        from concurrent.futures import ThreadPoolExecutor, as_completed

        relevant_collections = self._select_relevant_collections(
            question, collections, max_collections=8)

        def _query_one(coll_info):
            try:
                collection = self.s.rag.client.get_collection(
                    name=coll_info['collection'])
                results = collection.query(query_texts=[question], n_results=3)
                hits = []
                for i in range(len(results['documents'][0])):
                    hits.append({
                        'text': results['documents'][0][i],
                        'library': coll_info['library'],
                        'distance': results['distances'][0][i],
                    })
                return hits
            except Exception:
                return []

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {executor.submit(_query_one, c): c
                       for c in relevant_collections}
            for future in as_completed(futures, timeout=10):
                try:
                    all_results.extend(future.result())
                except Exception:
                    pass

        # ── Query DualZoneRAG (vault + knowledge collections) ──────────
        try:
            vault_auth = getattr(self.s, 'vault_unlocked', False)
            rag_response = self.s.rag.query(
                question, n_results=5, authenticated=vault_auth)
            for r in rag_response.results:
                label = ("From your vault"
                         if r.source.value == "vault"
                         else "From knowledge base")
                all_results.append({
                    'text': r.text,
                    'library': label,
                    'distance': r.score,
                })
        except Exception as e:
            print(f"Error querying DualZoneRAG: {e}")

        if not all_results:
            return None
        all_results.sort(key=lambda x: x['distance'])
        top = all_results[:n_results]

        # Cap total context to prevent token overflow
        _MAX_RAG_CHARS = 12_000  # ~3K tokens, safe for 4K-8K context windows
        parts = []
        total_chars = 0
        for r in top:
            chunk = f"[{r['library']}] {r['text'][:700]}"
            if total_chars + len(chunk) > _MAX_RAG_CHARS:
                break
            parts.append(chunk)
            total_chars += len(chunk)
        return "\n\n".join(parts)

    def get_indexed_libraries_direct(self):
        try:
            with open(os.path.join(self.s.src_dir, 'indexed_libraries.json'), 'r') as f:
                collections = json.load(f)
            if not collections:
                return "No libraries are currently indexed."
            names = [c['library'] for c in collections]
            return (f"Indexed libraries ({len(collections)} total):\n" +
                    "\n".join(f"{i+1}. {lib}" for i, lib in enumerate(names)))
        except FileNotFoundError:
            return "No indexed libraries found."
        except Exception as e:
            return f"Error: {e}"

    # ── Conversation context builder ─────────────────────────────────────

    def build_conversation_context(self, max_tokens=3000):
        messages = []
        if self.s.persona:
            messages.append({"role": "system", "content": self.s.persona})
        total_tokens = self.estimate_tokens(self.s.persona)
        for msg in reversed(self.s.conversation_history):
            ut = self.estimate_tokens(msg['user'])
            at = self.estimate_tokens(msg['assistant'])
            if total_tokens + ut + at > max_tokens:
                break
            messages.insert(1, {"role": "user", "content": msg['user']})
            messages.insert(2, {"role": "assistant", "content": msg['assistant']})
            total_tokens += ut + at
        return messages

    # ── Ollama querying ──────────────────────────────────────────────────

    def query_ollama(self, user_message, max_context_tokens=120000):
        try:
            rag_context = ""
            self.s.window.after(
                0, lambda: self.s.status_label.config(
                    text="\u25cf  Searching knowledge base..."))
            context = self.get_rag_context(user_message)
            if context:
                max_rag = 8000
                if len(context) > max_rag:
                    context = context[:max_rag] + "\n[Context truncated...]"
                rag_context = f"\n\n[Knowledge Base Context]\n{context}\n"

            messages = self.build_conversation_context(
                max_tokens=max_context_tokens - 10000)
            final_msg = (f"{rag_context}\n[User Question]\n{user_message}"
                         if rag_context else user_message)
            messages.append({"role": "user", "content": final_msg})

            self.s.window.after(
                0, lambda: self.s.status_label.config(
                    text="\u25cf  Generating response..."))

            response = requests.post(  # lan-egress: Ollama chat, LAN
                f"{self.s.ollama_url}/v1/chat/completions",
                json={
                    "model": self.s.ollama_model,
                    "messages": messages,
                    "temperature": self.s.ollama_temperature,
                    "max_tokens": 256,
                    "stream": False,
                    "options": {
                        "num_ctx": 2048,
                        "num_predict": 256,
                    },
                },
                timeout=30, proxies=_no_proxies())

            if response.status_code == 200:
                return response.json()['choices'][0]['message']['content']
            else:
                return f"Error: Ollama returned status {response.status_code}"

        except requests.exceptions.Timeout:
            return "Error: Request timed out after 30 seconds"
        except requests.exceptions.ConnectionError:
            return (f"Error: Cannot connect to Ollama. "
                    f"Make sure it's running at {self.s.ollama_url}")
        except Exception as e:
            return f"Error: {e}"

    # ── Streaming Ollama query ──────────────────────────────────────────

    def query_ollama_streaming(self, user_message, stream_handle,
                               max_context_tokens=120000):
        """Query Ollama with streaming and push tokens to *stream_handle*.

        *stream_handle* is the dict returned by ``add_streaming_bubble()``.
        This method runs on a background thread — it schedules UI updates
        via ``window.after()``.
        """
        try:
            rag_context = ""
            self.s.window.after(
                0, lambda: self.s.status_label.config(
                    text="\u25cf  Searching knowledge base..."))
            context = self.get_rag_context(user_message)
            if context:
                max_rag = 8000
                if len(context) > max_rag:
                    context = context[:max_rag] + "\n[Context truncated...]"
                rag_context = f"\n\n[Knowledge Base Context]\n{context}\n"

            messages = self.build_conversation_context(
                max_tokens=max_context_tokens - 10000)
            final_msg = (f"{rag_context}\n[User Question]\n{user_message}"
                         if rag_context else user_message)
            messages.append({"role": "user", "content": final_msg})

            self.s.window.after(
                0, lambda: self.s.status_label.config(
                    text="\u25cf  Generating response..."))

            response = requests.post(  # lan-egress: Ollama chat stream, LAN
                f"{self.s.ollama_url}/v1/chat/completions",
                json={
                    "model": self.s.ollama_model,
                    "messages": messages,
                    "temperature": self.s.ollama_temperature,
                    "max_tokens": 256,
                    "stream": True,
                    "options": {
                        "num_ctx": 2048,
                        "num_predict": 256,
                    },
                },
                timeout=30,
                stream=True, proxies=_no_proxies())

            if response.status_code != 200:
                return f"Error: Ollama returned status {response.status_code}"

            full_text = ""
            for line in response.iter_lines():
                if not line:
                    continue
                line_str = line.decode("utf-8", errors="replace")
                if line_str.startswith("data: "):
                    line_str = line_str[6:]
                if line_str.strip() == "[DONE]":
                    break
                try:
                    import json as _json
                    chunk = _json.loads(line_str)
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    token = delta.get("content", "")
                    if token:
                        full_text += token
                        # Schedule UI update
                        text_so_far = full_text
                        self.s.window.after(
                            0, lambda t=text_so_far: stream_handle["update"](t))
                except (ValueError, KeyError, IndexError):
                    continue

            return full_text or "No response received."

        except requests.exceptions.Timeout:
            return "Error: Request timed out after 30 seconds"
        except requests.exceptions.ConnectionError:
            return (f"Error: Cannot connect to Ollama. "
                    f"Make sure it's running at {self.s.ollama_url}")
        except Exception as e:
            return f"Error: {e}"

    # ── Agent querying ───────────────────────────────────────────────────

    def query_with_agent(self, user_message):
        # The tool agent is not part of this build.
        return "Error using tools: the tool agent is not part of this build."

    def query_daily_briefing(self):
        return ("Error generating daily briefing: the tool agent is not part "
                "of this build.")
