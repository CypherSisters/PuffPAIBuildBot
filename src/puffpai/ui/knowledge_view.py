"""PuffPAI Knowledge Upload & Management View.

Provides file upload dialog, preview, and ingestion progress for adding
personal files to the dual-zone knowledge base.

Usage:
    knowledge_view = KnowledgeView(state, widgets, theme, on_system_message=...)
    knowledge_view.show_upload_dialog()

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox
from typing import Callable, Optional

from .theme import PuffPAITheme
from .widgets import AeroWidgets


# Supported file extensions for upload (multi-format via file_processor)
SUPPORTED_EXTENSIONS = (
    '.txt', '.md', '.pdf', '.json', '.csv', '.tsv',
    '.docx', '.doc', '.rtf', '.xlsx', '.xls',
    '.html', '.xml', '.yaml', '.yml',
    '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.tif', '.webp',
    '.py', '.js', '.log', '.ini', '.cfg', '.toml',
)


class KnowledgeView:
    """File upload and knowledge management UI."""

    def __init__(self, state, widgets: AeroWidgets, theme: PuffPAITheme,
                 on_system_message: Callable[[str], None] | None = None):
        """Initialize with shared UI state.

        Args:
            state: AppState dataclass.
            widgets: AeroWidgets helper.
            theme: PuffPAITheme colour & font definitions.
            on_system_message: Callback to display system messages in chat.
        """
        self.s = state
        self.w = widgets
        self.T = theme
        self._on_system_message = on_system_message
        self._ingestion = None  # lazy KnowledgeIngestion instance

    # ── Lazy ingestion pipeline ───────────────────────────────────────

    def _get_ingestion(self):
        """Get or create a KnowledgeIngestion instance."""
        if self._ingestion is None:
            from puffpai.knowledge.ingestion import KnowledgeIngestion
            vault_coll = None
            knowledge_coll = None

            # Get collections from the existing RAG client
            if self.s.rag is not None:
                try:
                    client = self.s.rag.client
                    vault_coll = client.get_or_create_collection(
                        name="puffpai_vault")
                    knowledge_coll = client.get_or_create_collection(
                        name="puffpai_knowledge")
                except Exception:
                    pass

            self._ingestion = KnowledgeIngestion(
                vault_collection=vault_coll,
                knowledge_collection=knowledge_coll,
            )
        return self._ingestion

    # ── Upload dialog ─────────────────────────────────────────────────

    def show_upload_dialog(self):
        """Open a file picker and show the upload preview dialog."""
        # Only the formats the base install can actually parse: PDF (pypdf /
        # pdfplumber) plus plain-text and data files (stdlib). Word/Excel/OCR
        # need optional extras that aren't installed by default, so they are
        # not advertised here.
        filetypes = [
            ("All supported",
             "*.txt *.md *.pdf *.json *.csv *.tsv *.xml *.yaml *.yml"),
            ("Documents (PDF)", "*.pdf"),
            ("Text & data", "*.txt *.md *.json *.csv *.tsv *.xml *.yaml *.yml"),
            ("All files", "*.*"),
        ]

        filepath = filedialog.askopenfilename(
            title="Upload File to PuffPAI Knowledge Base",
            filetypes=filetypes,
            parent=self.s.window,
        )

        if not filepath:
            return  # User cancelled

        self._show_preview_dialog(filepath)

    def _show_preview_dialog(self, filepath: str):
        """Show a preview of the file before ingestion."""
        T = self.T
        filename = os.path.basename(filepath)
        ext = os.path.splitext(filepath)[1].lower()
        file_size = os.path.getsize(filepath)
        size_label = self._format_size(file_size)

        # Quick content check for zone routing hint
        pii_detected = False
        text_preview = ""
        estimated_chunks = 0

        try:
            from puffpai.file_processor import extract_text, IMAGE_EXTENSIONS
            text_content, meta = extract_text(filepath)
            if text_content:
                text_preview = text_content[:2000]
                full_size = len(text_content)
            elif meta.get("error"):
                text_preview = f"({meta['error']})"
                full_size = file_size
            else:
                text_preview = "(No text could be extracted from this file)"
                full_size = file_size

            if ext in IMAGE_EXTENSIONS and text_content:
                text_preview = f"[OCR Text from Image]\n\n{text_preview}"

            if text_preview and text_preview[0] != '(':
                pii_detected = self._quick_pii_check(text_preview)
                estimated_chunks = max(1, full_size // 800)

        except Exception as e:
            text_preview = f"(Cannot preview: {e})"

        zone = "Vault (personal)" if pii_detected else "Knowledge Base (general)"

        # ── Build dialog ──────────────────────────────────────────────
        dialog = tk.Toplevel(self.s.window)
        dialog.title("Upload Preview")
        dialog.geometry("520x460")
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
        tk.Label(header, text="Upload File to Knowledge Base",
                 font=T.FONT_LG_B, fg=T.C_NAVY, bg=T.C_PALE).pack(
            side=tk.LEFT, padx=16, pady=10)

        # File info
        info_frame = tk.Frame(dialog, bg=T.C_BG)
        info_frame.pack(fill=tk.X, padx=16, pady=(12, 6))

        details = [
            ("File:", filename),
            ("Type:", ext.upper().replace('.', '') or "Unknown"),
            ("Size:", size_label),
            ("Est. Chunks:", str(estimated_chunks)),
            ("Zone:", zone),
        ]

        for label_text, value_text in details:
            row = tk.Frame(info_frame, bg=T.C_BG)
            row.pack(fill=tk.X, pady=1)
            tk.Label(row, text=label_text, font=T.FONT_SM_B,
                     fg=T.C_TEXT, bg=T.C_BG, width=12,
                     anchor=tk.W).pack(side=tk.LEFT)
            tk.Label(row, text=value_text, font=T.FONT_SM,
                     fg=T.C_TEXT_MED, bg=T.C_BG,
                     anchor=tk.W).pack(side=tk.LEFT, fill=tk.X)

        if pii_detected:
            pii_label = tk.Label(
                info_frame,
                text="PII detected: this file will be stored in your private Vault.",
                font=T.FONT_SM, fg=T.C_GOLD, bg=T.C_BG)
            pii_label.pack(anchor=tk.W, pady=(6, 0))

        # Preview
        tk.Frame(dialog, height=1, bg=T.C_BORDER).pack(
            fill=tk.X, padx=16, pady=8)
        tk.Label(dialog, text="Preview:", font=T.FONT_SM_B,
                 fg=T.C_TEXT, bg=T.C_BG).pack(anchor=tk.W, padx=16)

        preview_frame = tk.Frame(dialog, bg=T.C_BORDER, padx=1, pady=1)
        preview_frame.pack(fill=tk.BOTH, expand=True, padx=16, pady=(4, 12))

        preview_text = tk.Text(
            preview_frame, wrap=tk.WORD, font=T.FONT_XS,
            bg=T.C_WHITE, fg=T.C_TEXT, relief=tk.FLAT, padx=8, pady=6)
        preview_text.pack(fill=tk.BOTH, expand=True)
        display_text = text_preview[:1000]
        if len(text_preview) > 1000:
            display_text += "\n\n... (truncated)"
        preview_text.insert("1.0", display_text)
        preview_text.config(state=tk.DISABLED)

        # Buttons
        btn_frame = tk.Frame(dialog, bg=T.C_BG)
        btn_frame.pack(fill=tk.X, padx=16, pady=(0, 14))

        cancel_btn = tk.Button(
            btn_frame, text="Cancel", command=dialog.destroy,
            bg=T.C_BORDER, fg=T.C_TEXT, font=T.FONT_SM,
            relief=tk.FLAT, cursor="hand2", padx=18, pady=6)
        cancel_btn.pack(side=tk.RIGHT, padx=(6, 0))

        upload_btn = tk.Button(
            btn_frame, text="Upload",
            command=lambda: self._do_upload(filepath, dialog),
            bg=T.C_AQUA, fg=T.C_WHITE, font=T.FONT_B,
            relief=tk.FLAT, cursor="hand2", padx=18, pady=6,
            activebackground=T.C_AQUA_MID, activeforeground=T.C_WHITE)
        upload_btn.pack(side=tk.RIGHT)
        self.w.bind_hover(upload_btn, T.C_AQUA, T.C_AQUA_MID)

    # ── Upload execution ──────────────────────────────────────────────

    def _do_upload(self, filepath: str, dialog: tk.Toplevel):
        """Ingest the file in a background thread."""
        dialog.destroy()
        filename = os.path.basename(filepath)

        # Show progress message
        if self._on_system_message:
            self._on_system_message(
                f"[System] Uploading {filename}...")

        def _ingest():
            try:
                # Use multi-format file processor for extraction
                from puffpai.file_processor import extract_text as fp_extract
                ext = os.path.splitext(filepath)[1].lower()

                # For non-plain-text files, pre-extract and ingest as text
                if ext not in ('.txt', '.md', '.json'):
                    text_content, meta = fp_extract(filepath)
                    if not text_content.strip():
                        error_msg = meta.get("error", "No text extracted")
                        raise ValueError(f"Could not extract text: {error_msg}")

                    # Write extracted text to a temp file for ingestion
                    import tempfile
                    base_name = os.path.splitext(filename)[0]
                    tmp_path = os.path.join(
                        tempfile.gettempdir(),
                        f"puffpai_{base_name}.txt"
                    )
                    with open(tmp_path, 'w', encoding='utf-8') as f:
                        f.write(f"[Source: {filename}]\n")
                        f.write(f"[Type: {meta.get('extractor', 'unknown')}]\n\n")
                        f.write(text_content)

                    ingestion = self._get_ingestion()
                    doc = ingestion.ingest_file(tmp_path)

                    # Clean up temp file
                    try:
                        os.remove(tmp_path)
                    except OSError:
                        pass
                else:
                    ingestion = self._get_ingestion()
                    doc = ingestion.ingest_file(filepath)

                # Register in the document registry
                try:
                    registry = self._get_registry()
                    registry.register(doc)
                except Exception:
                    pass  # Registry is optional, don't fail the upload

                zone_label = ("your Vault" if doc.target_zone.value == "puffpai_vault"
                              else "the Knowledge Base")
                msg = (
                    f"[System] Uploaded {filename} "
                    f"({doc.chunk_count} chunks, {zone_label})"
                )
                if doc.has_pii:
                    msg += ": PII detected, stored securely"

                if self._on_system_message:
                    self.s.window.after(0, lambda: self._on_system_message(msg))

            except Exception as e:
                if self._on_system_message:
                    self.s.window.after(
                        0, lambda: self._on_system_message(
                            f"[System] ERROR uploading {filename}: {e}"))

        thread = threading.Thread(target=_ingest, daemon=True)
        thread.start()

    # ── Knowledge Manager panel ──────────────────────────────────────

    def show_manager_panel(self):
        """Open the 'My Knowledge' panel showing all ingested documents."""
        T = self.T
        registry = self._get_registry()

        dialog = tk.Toplevel(self.s.window)
        dialog.title("My Knowledge")
        dialog.geometry("600x480")
        dialog.configure(bg=T.C_BG)
        dialog.transient(self.s.window)
        dialog.grab_set()

        # Header
        header = tk.Frame(dialog, bg=T.C_PALE, height=50)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        tk.Frame(header, height=2, bg=T.C_AQUA_LT).pack(
            side=tk.BOTTOM, fill=tk.X)
        tk.Label(header, text="My Knowledge",
                 font=T.FONT_LG_B, fg=T.C_NAVY, bg=T.C_PALE).pack(
            side=tk.LEFT, padx=16, pady=10)

        stats_text = f"{registry.count} documents, {registry.total_chunks} chunks"
        tk.Label(header, text=stats_text, font=T.FONT_SM,
                 fg=T.C_TEXT_MED, bg=T.C_PALE).pack(
            side=tk.RIGHT, padx=16, pady=10)

        # Search bar
        search_frame = tk.Frame(dialog, bg=T.C_BG)
        search_frame.pack(fill=tk.X, padx=16, pady=(10, 4))
        tk.Label(search_frame, text="Search:", font=T.FONT_SM,
                 fg=T.C_TEXT, bg=T.C_BG).pack(side=tk.LEFT, padx=(0, 6))
        search_var = tk.StringVar()
        search_entry = tk.Entry(
            search_frame, textvariable=search_var, font=T.FONT_SM,
            bg=T.C_WHITE, fg=T.C_TEXT, relief=tk.FLAT,
            insertbackground=T.C_AQUA)
        search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # Document list
        list_frame = tk.Frame(dialog, bg=T.C_BG)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=16, pady=(4, 12))

        list_canvas = tk.Canvas(list_frame, bg=T.C_BG, highlightthickness=0)
        scrollbar = tk.Scrollbar(list_frame, orient="vertical",
                                 command=list_canvas.yview)
        doc_frame = tk.Frame(list_canvas, bg=T.C_BG)

        list_canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        list_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        canvas_window = list_canvas.create_window(
            (0, 0), window=doc_frame, anchor="nw")

        doc_frame.bind("<Configure>",
                       lambda e: list_canvas.configure(
                           scrollregion=list_canvas.bbox("all")))
        list_canvas.bind("<Configure>",
                         lambda e: list_canvas.itemconfig(
                             canvas_window, width=e.width))

        def _populate(query: str = ""):
            """Rebuild the document list."""
            for w in doc_frame.winfo_children():
                w.destroy()

            if query:
                docs = registry.search(query)
            else:
                docs = registry.list_documents()

            if not docs:
                tk.Label(doc_frame, text="No documents found.",
                         font=T.FONT_SM, fg=T.C_TEXT_LT,
                         bg=T.C_BG).pack(pady=20)
                return

            for doc in docs:
                self._build_doc_row(doc_frame, doc, registry, _populate, dialog)

        _populate()

        # Wire search
        def _on_search(*args):
            _populate(search_var.get())
        search_var.trace_add("write", _on_search)

        # Close button
        close_btn = tk.Button(
            dialog, text="Close", command=dialog.destroy,
            bg=T.C_BORDER, fg=T.C_TEXT, font=T.FONT_SM,
            relief=tk.FLAT, cursor="hand2", padx=18, pady=6)
        close_btn.pack(pady=(0, 12))

    def _build_doc_row(self, parent, doc: dict, registry, refresh_fn, dialog):
        """Build a single document row in the manager panel."""
        T = self.T
        row = tk.Frame(parent, bg=T.C_WHITE, padx=10, pady=8)
        row.pack(fill=tk.X, pady=2)

        zone_icon = "\U0001f512" if doc.get('zone') == 'puffpai_vault' else "\U0001f4da"
        title_text = f"{zone_icon}  {doc.get('title', 'Untitled')}"
        tk.Label(row, text=title_text, font=T.FONT_SM_B,
                 fg=T.C_TEXT, bg=T.C_WHITE, anchor=tk.W).pack(
            anchor=tk.W)

        zone_label = "Vault" if doc.get('zone') == 'puffpai_vault' else "Knowledge Base"
        date = doc.get('ingested_at', '')[:10]
        meta_text = f"{doc.get('source', '')} | {zone_label} | {doc.get('chunk_count', 0)} chunks | {date}"
        tk.Label(row, text=meta_text, font=T.FONT_XS,
                 fg=T.C_TEXT_LT, bg=T.C_WHITE, anchor=tk.W).pack(
            anchor=tk.W)

        # Delete button
        del_btn = tk.Button(
            row, text="Delete",
            command=lambda: self._delete_doc(
                doc['doc_id'], doc.get('title', ''), registry, refresh_fn, dialog),
            bg=T.C_RED, fg=T.C_WHITE, font=T.FONT_XS,
            relief=tk.FLAT, cursor="hand2", padx=8, pady=2)
        del_btn.pack(anchor=tk.E, pady=(4, 0))

    @staticmethod
    def _doc_collection(doc: dict):
        """The ChromaDB collection holding this document's chunks, or None.

        None means the store could not be opened, and every caller here treats
        that as "delete nothing" rather than "delete the record anyway".
        """
        try:
            from puffpai.knowledge.rag import DualZoneRAG, VAULT_COLLECTION
            rag = DualZoneRAG()
            if doc.get('zone') == VAULT_COLLECTION:
                return rag.vault_collection
            return rag.knowledge_collection
        except Exception:  # noqa: BLE001 - reported to the owner as a refusal
            return None

    def _delete_doc(self, doc_id: str, title: str, registry, refresh_fn, dialog):
        """Delete a document - its chunks and its record - with confirmation.

        The confirmation says what will really happen, worked out against the
        live store before the owner answers: how many chunks go, and how many
        (if any) cannot be identified and will keep answering questions. Telling
        someone their document is gone when its text is still in the store is
        the failure this dialog exists to prevent.
        """
        from tkinter import messagebox

        doc = registry.get(doc_id) or {'doc_id': doc_id, 'title': title}
        collection = self._doc_collection(doc)
        preview = registry.deletion_preview(doc_id, collection)

        if preview.get('reachable') is False or collection is None:
            messagebox.showerror("Delete Document", preview['message'],
                                 parent=dialog)
            if self._on_system_message:
                self._on_system_message(f"[System] {preview['message']}")
            return

        if not messagebox.askyesno("Delete Document", preview['message'],
                                   parent=dialog):
            return

        result = registry.delete_document(doc_id, collection)
        refresh_fn()

        lines = [result['reason']] + list(result.get('limits') or [])
        message = "\n\n".join(lines)
        if result['ok']:
            messagebox.showinfo("Delete Document", message, parent=dialog)
        else:
            messagebox.showwarning("Delete Document", message, parent=dialog)

        if self._on_system_message:
            self._on_system_message(f"[System] {' '.join(lines)}")

    def _get_registry(self):
        """Get or create the DocumentRegistry."""
        if not hasattr(self, '_registry') or self._registry is None:
            from puffpai.knowledge.registry import DocumentRegistry
            import os
            registry_path = os.path.join(
                self.s.repo_root, "data", "document_registry.json")
            self._registry = DocumentRegistry(registry_path)
        return self._registry

    # ── Helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _format_size(size_bytes: int) -> str:
        """Format bytes as human-readable size string."""
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        else:
            return f"{size_bytes / (1024 * 1024):.1f} MB"

    @staticmethod
    def _quick_pii_check(text: str) -> bool:
        """Quick check for PII indicators, using the shared reference detector."""
        from ..security.data_classifier import DataClassifier

        return DataClassifier().scan_for_pii(text).has_pii
