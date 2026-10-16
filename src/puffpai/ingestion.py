"""
PuffPAI Document & Image Ingestion
====================================
Handles uploads of recipes (photos), school flyers (images/PDFs),
study material (PDFs), schedules (images), and other personal documents.

Pipeline:
    1. Detect file type (image / pdf / docx / txt / xlsx)
    2. Extract raw text via the appropriate engine:
        - PDFs → pdfplumber (text layer) or pytesseract (scanned)
        - Images → pytesseract OCR
        - DOCX → python-docx
        - TXT  → utf-8 read
        - XLSX → openpyxl
    3. Use the local LLM to classify/parse the extracted text into a
       structured payload (recipe, event, study notes, etc.)
    4. Route the payload to the right app data store
    5. ALSO chunk + embed the raw text into the `user_uploads` ChromaDB
       collection so the unified RAG pipeline can find it semantically

This module is designed for OFFLINE use. All OCR + LLM happens locally.

Architecture note:
    Every uploaded file becomes part of PuffPAI's "brain" via ChromaDB.
    Structured data (recipes → FreshTable, events → Calendar) is a
    fast-access projection. The vector DB is the source of truth for
    semantic retrieval and is queried by both _get_rag_context() (RAG
    path) and search_my_documents (agent path).

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from puffpai.security.lan_guard import no_proxies as _no_proxies  # marked home-network calls never use a proxy

logger = logging.getLogger(__name__)

# ── Where uploads and their catalog live ────────────────────────────────────
#
# Both were ``Path(__file__).parent.parent.parent / "data"`` - the SOURCE TREE,
# whatever ``PUFFPAI_DATA_DIR`` said. The ChromaDB store does follow the data
# dir (via ``DualZoneRAG``), so the two halves of the index pointed at different
# installs: a scratch build uploaded a document, the repo-rooted catalog
# answered ``document_count: 1``, and the scratch vector store answered
# ``chunk_count: 0`` - which is exactly the "uploads not indexed" report, and
# why ``/ingest/reindex`` appeared to fix it (it re-embedded the repo catalog
# into the current store). They are resolved per call now, from the same
# ``DATA_DIR`` everything else uses, and the directory is created on first write
# rather than at import - an import must not leave a folder in a checkout.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _data_root() -> Path:
    """The ``data/`` directory of THIS install. Honours ``PUFFPAI_DATA_DIR``."""
    try:
        from puffpai.config import get_config
        return Path(get_config().DATA_DIR) / "data"
    except Exception:  # pragma: no cover - config is always importable
        return _REPO_ROOT / "data"


def _upload_dir() -> Path:
    """Where uploaded files are stored, created on demand."""
    path = _data_root() / "uploads"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _doc_index() -> Path:
    """The JSON catalog of extracted text. Not created until something writes."""
    return _data_root() / "ingested_docs.json"


# ── File-type detection ───────────────────────────────────────────────────────

IMAGE_EXTS = frozenset({".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff"})
PDF_EXTS = frozenset({".pdf"})
DOC_EXTS = frozenset({".docx", ".doc"})
TEXT_EXTS = frozenset({".txt", ".md", ".markdown", ".rst", ".log", ".json",
                       ".yaml", ".yml", ".ini", ".cfg", ".conf", ".toml"})
SHEET_EXTS = frozenset({".xlsx", ".xls", ".csv"})


def detect_file_type(filename: str) -> str:
    """Return one of: 'image', 'pdf', 'docx', 'txt', 'xlsx', 'unknown'."""
    ext = os.path.splitext(filename.lower())[1]
    if ext in IMAGE_EXTS:
        return "image"
    if ext in PDF_EXTS:
        return "pdf"
    if ext in DOC_EXTS:
        return "docx"
    if ext in TEXT_EXTS:
        return "txt"
    if ext in SHEET_EXTS:
        return "xlsx"
    return "unknown"


# ── OCR detection (lazy) ──────────────────────────────────────────────────────

_ocr_available: bool | None = None
_ocr_check_done = False


def _check_ocr_available() -> bool:
    """Detect whether Tesseract is installed. Cache the result."""
    global _ocr_available, _ocr_check_done
    if _ocr_check_done:
        return bool(_ocr_available)
    _ocr_check_done = True
    try:
        import pytesseract
        # Check common Windows install paths
        common_paths = [
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
            os.path.expanduser(r"~\AppData\Local\Programs\Tesseract-OCR\tesseract.exe"),
            shutil.which("tesseract") or "",
        ]
        for path in common_paths:
            if path and os.path.exists(path):
                pytesseract.pytesseract.tesseract_cmd = path
                _ocr_available = True
                logger.info("Tesseract OCR found at: %s", path)
                return True
        # Try plain command (in case it's on PATH)
        try:
            pytesseract.get_tesseract_version()
            _ocr_available = True
            return True
        except Exception:
            pass
    except ImportError:
        pass
    _ocr_available = False
    logger.info("Tesseract OCR not available — image OCR disabled")
    return False


# ── Text extraction engines ───────────────────────────────────────────────────

def extract_text_from_image(file_path: str) -> str:
    """OCR an image file with handwriting-friendly settings.

    Strategy:
      1. Open the image and upscale 2× — handwriting OCR works much
         better on larger inputs.
      2. Convert to grayscale and boost contrast slightly.
      3. Use PSM 6 (uniform block of text) which works best for recipes,
         flyers, and any document with structured text rows.
      4. Try multiple PSM modes and pick the result with the most words.

    Returns empty string if OCR is unavailable.
    """
    if not _check_ocr_available():
        return "[OCR NOT AVAILABLE: install Tesseract OCR to enable image text extraction]"
    try:
        import pytesseract
        from PIL import Image, ImageOps, ImageEnhance

        original = Image.open(file_path)

        # Upscale 2× — handwriting OCR is way more accurate on larger images
        upscaled = original.resize(
            (original.width * 2, original.height * 2),
            Image.Resampling.LANCZOS,
        )

        # Light grayscale + contrast boost helps faded handwriting
        grayscale = ImageOps.grayscale(upscaled)
        enhanced = ImageEnhance.Contrast(grayscale).enhance(1.4)

        # Try multiple page-segmentation modes and keep the best result.
        # PSM 6 (uniform block) works best for recipes and structured docs.
        # PSM 11 (sparse text) works better for scattered/handwritten text.
        # PSM 3 (default fully-automatic) is the catch-all.
        candidates = []
        for psm in (6, 11, 3):
            try:
                text = pytesseract.image_to_string(
                    enhanced,
                    config=f"--psm {psm}",
                ).strip()
                candidates.append((psm, text, len(text.split())))
            except Exception:
                continue

        if not candidates:
            return ""

        # Pick the result with the most words (usually highest-quality OCR)
        candidates.sort(key=lambda c: c[2], reverse=True)
        best_psm, best_text, best_words = candidates[0]
        logger.info("OCR for %s: psm=%d words=%d", file_path, best_psm, best_words)
        return best_text
    except Exception as e:
        logger.error("OCR failed for %s: %s", file_path, e)
        return f"[OCR error: {e}]"


def extract_text_from_pdf(file_path: str) -> str:
    """Extract text from PDF. Tries pdfplumber first (for text-layer PDFs),
    then falls back to OCR each page if no text was found."""
    text = ""
    try:
        import pdfplumber
        with pdfplumber.open(file_path) as pdf:
            pages = []
            for page in pdf.pages:
                page_text = page.extract_text() or ""
                pages.append(page_text)
            text = "\n\n".join(pages).strip()
    except Exception as e:
        logger.warning("pdfplumber failed for %s: %s", file_path, e)

    # If we got nothing, try OCR (scanned PDF)
    if len(text) < 50 and _check_ocr_available():
        try:
            import pdfplumber
            import pytesseract
            from PIL import Image
            import io
            ocr_pages = []
            with pdfplumber.open(file_path) as pdf:
                for page in pdf.pages:
                    img = page.to_image(resolution=200).original
                    page_text = pytesseract.image_to_string(img)
                    ocr_pages.append(page_text)
            ocr_text = "\n\n".join(ocr_pages).strip()
            if len(ocr_text) > len(text):
                text = ocr_text
        except Exception as e:
            logger.warning("PDF OCR fallback failed: %s", e)

    return text


def extract_text_from_docx(file_path: str) -> str:
    """Extract text from a Word document."""
    try:
        from docx import Document
        doc = Document(file_path)
        paras = [p.text for p in doc.paragraphs if p.text.strip()]
        return "\n".join(paras)
    except ImportError:
        return "[python-docx not installed: cannot read .docx files]"
    except Exception as e:
        return f"[docx read error: {e}]"


def extract_text_from_txt(file_path: str) -> str:
    """Read a plain text file."""
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception as e:
        return f"[text read error: {e}]"


def extract_text_from_xlsx(file_path: str) -> str:
    """Extract a flat string representation of all sheets in a workbook."""
    try:
        import openpyxl
        wb = openpyxl.load_workbook(file_path, data_only=True)
        out = []
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            out.append(f"### Sheet: {sheet_name}")
            for row in ws.iter_rows(values_only=True):
                vals = [str(v) if v is not None else "" for v in row]
                if any(v.strip() for v in vals):
                    out.append("\t".join(vals))
        return "\n".join(out)
    except ImportError:
        return "[openpyxl not installed: cannot read .xlsx files]"
    except Exception as e:
        return f"[xlsx read error: {e}]"


def extract_text(file_path: str) -> tuple[str, str]:
    """Detect file type and extract text. Returns (file_type, extracted_text)."""
    ftype = detect_file_type(file_path)
    if ftype == "image":
        return ftype, extract_text_from_image(file_path)
    if ftype == "pdf":
        return ftype, extract_text_from_pdf(file_path)
    if ftype == "docx":
        return ftype, extract_text_from_docx(file_path)
    if ftype == "txt":
        return ftype, extract_text_from_txt(file_path)
    if ftype == "xlsx":
        return ftype, extract_text_from_xlsx(file_path)
    return ftype, "[unknown file type]"


# Markers used by the extractors above when extraction fails (missing parser
# library, read error, OCR error, unknown type). Each extractor returns a
# SINGLE bracketed placeholder string on failure; that placeholder must NEVER
# be indexed as if it were real document content.
_EXTRACTION_FAILURE_MARKERS = (
    "not installed",
    "read error",
    "ocr error",
    "unknown file type",
    "cannot read",
)


def _extraction_failure_reason(text: str) -> str | None:
    """Return a user-facing reason if `text` is an extractor error placeholder
    (e.g. '[python-docx not installed — cannot read .docx files]'), else None."""
    s = (text or "").strip()
    if s.startswith("[") and s.endswith("]") and len(s) < 200:
        low = s.lower()
        if any(m in low for m in _EXTRACTION_FAILURE_MARKERS):
            return s.strip("[]").strip()
    return None


# ── LLM-driven classification & parsing ───────────────────────────────────────

def _llm_chat(prompt: str, max_tokens: int = 600, temperature: float = 0.1) -> str:
    """Send a prompt to the local Ollama LLM and return the response."""
    try:
        import requests
        from puffpai.config import get_config
        cfg = get_config()
        resp = requests.post(  # lan-egress: Ollama chat for classification, LAN
            f"{cfg.ollama.url}/api/chat",
            json={
                "model": cfg.ollama.model,
                "stream": False,
                "messages": [{"role": "user", "content": prompt}],
                "options": {
                    "temperature": temperature,
                    "num_predict": max_tokens,
                },
            },
            timeout=120,
            proxies=_no_proxies(),
        )
        return resp.json().get("message", {}).get("content", "")
    except Exception as e:
        logger.error("LLM chat failed: %s", e)
        return ""


def classify_document(text: str) -> str:
    """Use the LLM to classify what KIND of document this is.

    Returns one of: 'recipe', 'event', 'schedule', 'study', 'shopping_list',
                    'note', 'unknown'.
    """
    if not text or len(text.strip()) < 10:
        return "unknown"

    snippet = text[:1500]  # Don't waste tokens on a huge doc
    prompt = (
        "Classify this document into ONE of these categories. "
        "Respond with ONLY the single category name, nothing else.\n\n"
        "Categories:\n"
        "- recipe (cooking instructions with ingredients)\n"
        "- event (a single dated event like a concert, play, appointment)\n"
        "- schedule (a multi-day calendar/agenda)\n"
        "- study (textbook, lecture notes, study guide)\n"
        "- shopping_list (a list of items to buy)\n"
        "- note (general notes/memo)\n"
        "- unknown (none of the above)\n\n"
        f"Document text:\n---\n{snippet}\n---\n\n"
        "Category:"
    )
    response = _llm_chat(prompt, max_tokens=20, temperature=0.0)
    response = response.strip().lower()
    for cat in ("recipe", "event", "schedule", "study", "shopping_list", "note"):
        if cat in response:
            return cat
    return "unknown"


def parse_recipe(text: str) -> dict | None:
    """Use the LLM to extract a structured recipe from raw text.

    CRITICAL: This is for OCR'd handwriting which is often noisy. The LLM
    is instructed to ONLY copy text that is actually present, never to
    invent or correct ingredients. If the text is too messy, returns the
    raw text as the instructions field rather than risking hallucination.
    """
    snippet = text[:3000]
    prompt = (
        "You are extracting a recipe from raw OCR'd text that may contain "
        "errors from scanning handwritten or photographed paper. Your job is "
        "to ONLY transcribe what is literally in the text — NEVER invent or "
        "correct ingredients, quantities, or steps. If a quantity is unclear, "
        "include it as-is (with a [?]) rather than guessing.\n\n"
        "RULES:\n"
        "- Copy ingredient lines verbatim from the text. Don't change '1 tap' "
        "to '1 cup' even if you think that's what was meant.\n"
        "- Don't invent ingredients that aren't in the text.\n"
        "- Don't add 'salt to taste' or any 'standard' steps.\n"
        "- Don't normalize units or fix typos.\n"
        "- If the text is mostly garbled, leave INSTRUCTIONS empty and put "
        "the raw cleaned text in INGREDIENTS.\n\n"
        "Respond in EXACTLY this format:\n"
        "NAME: <recipe name as written, or 'Untitled Recipe'>\n"
        "INGREDIENTS: <one ingredient per line, copied verbatim>\n"
        "INSTRUCTIONS: <only the literal cooking steps from the text, or empty>\n\n"
        f"Text:\n---\n{snippet}\n---"
    )
    response = _llm_chat(prompt, max_tokens=800, temperature=0.0)
    if not response:
        return None
    name_m = re.search(r"NAME:\s*(.+?)(?:\n|$)", response)
    ing_m = re.search(r"INGREDIENTS:\s*(.+?)(?:\nINSTRUCTIONS:|$)", response, re.DOTALL)
    instr_m = re.search(r"INSTRUCTIONS:\s*(.+?)$", response, re.DOTALL)
    if not name_m:
        return None
    return {
        "name": name_m.group(1).strip(),
        "ingredients": ing_m.group(1).strip() if ing_m else "",
        "instructions": instr_m.group(1).strip() if instr_m else "",
        # Always preserve the raw OCR text so the user can verify
        "raw_text": text[:2000],
    }


def parse_event(text: str) -> dict | None:
    """Extract a calendar event (title, date, time, location) from text."""
    snippet = text[:2000]
    today = datetime.now().strftime("%Y-%m-%d")
    prompt = (
        f"Today is {today}. Extract ONE calendar event from this text. "
        f"Use ISO 8601 for the date (YYYY-MM-DDTHH:MM). If the year is "
        f"missing, assume current year ({datetime.now().year}). "
        f"If no time is given, use 09:00.\n\n"
        f"Respond in EXACTLY this format:\n"
        f"TITLE: <event name>\n"
        f"START: <YYYY-MM-DDTHH:MM>\n"
        f"LOCATION: <location or empty>\n"
        f"DESCRIPTION: <short description or empty>\n\n"
        f"Text:\n---\n{snippet}\n---"
    )
    response = _llm_chat(prompt, max_tokens=300, temperature=0.0)
    if not response:
        return None
    title_m = re.search(r"TITLE:\s*(.+?)(?:\n|$)", response)
    start_m = re.search(r"START:\s*(.+?)(?:\n|$)", response)
    loc_m = re.search(r"LOCATION:\s*(.+?)(?:\n|$)", response)
    desc_m = re.search(r"DESCRIPTION:\s*(.+?)(?:\n|$)", response)
    if not title_m or not start_m:
        return None
    return {
        "title": title_m.group(1).strip(),
        "start": start_m.group(1).strip(),
        "location": loc_m.group(1).strip() if loc_m else "",
        "description": desc_m.group(1).strip() if desc_m else "",
    }


def parse_schedule(text: str) -> list[dict]:
    """Extract MULTIPLE events from a schedule document."""
    snippet = text[:3500]
    today = datetime.now().strftime("%Y-%m-%d")
    prompt = (
        f"Today is {today}. Extract ALL calendar events from this schedule. "
        f"Use ISO 8601 dates (YYYY-MM-DDTHH:MM). If year missing, assume "
        f"{datetime.now().year}.\n\n"
        f"Respond as JSON array. Each event has: title, start, location.\n"
        f"Example: [{{\"title\":\"Math Class\",\"start\":\"2026-04-10T09:00\",\"location\":\"Room 101\"}}]\n\n"
        f"Text:\n---\n{snippet}\n---\n\nJSON:"
    )
    response = _llm_chat(prompt, max_tokens=800, temperature=0.0)
    if not response:
        return []
    # Extract JSON array from response
    try:
        match = re.search(r"\[.*?\]", response, re.DOTALL)
        if match:
            return json.loads(match.group(0))
    except Exception as e:
        logger.warning("Schedule JSON parse failed: %s", e)
    return []


# ── Main ingestion entry points ───────────────────────────────────────────────

@dataclass
class IngestionResult:
    file_id: str
    filename: str
    file_type: str
    classification: str
    text_length: int
    structured_data: dict | None = None
    actions_taken: list[str] = field(default_factory=list)
    error: str | None = None
    pii_detected: bool = False
    pii_summary: str | None = None
    vaulted: bool = False
    waiting_for_vault: bool = False

    def to_dict(self) -> dict:
        return {
            "file_id": self.file_id,
            "filename": self.filename,
            "file_type": self.file_type,
            "classification": self.classification,
            "text_length": self.text_length,
            "structured_data": self.structured_data,
            "actions_taken": self.actions_taken,
            "error": self.error,
            "pii_detected": self.pii_detected,
            "pii_summary": self.pii_summary,
            "vaulted": self.vaulted,
            "waiting_for_vault": self.waiting_for_vault,
        }


def save_uploaded_file(file_bytes: bytes, original_filename: str) -> str:
    """Persist an uploaded file to disk and return its full path."""
    safe_name = re.sub(r"[^\w\.\-]", "_", original_filename)
    timestamp = int(time.time() * 1000)
    file_id = f"{timestamp}_{safe_name}"
    target = _upload_dir() / file_id
    target.write_bytes(file_bytes)
    return str(target)


def _resolve_shared_vault():
    """Return the process-wide Vault singleton that holds live sessions.

    Document/PII vaulting must use the SAME Vault instance the API server
    unlocked, because sessions (and their in-memory encryption keys) are
    instance-scoped — a fresh ``Vault()`` would see no live session and could
    never encrypt. Falls back to a fresh Vault only if the app server isn't
    importable; that instance has no sessions, so vault writes fail closed
    (locked) rather than writing plaintext.
    """
    try:
        from puffpai.api_server import _get_vault
        v = _get_vault()
        if v is not None:
            return v
    except Exception as e:  # pragma: no cover - defensive
        logger.debug("Shared vault unavailable, using fresh instance: %s", e)
    from puffpai.security.vault import Vault
    return Vault()


# ─────────────────────────────────────────────────────────────────────
#  Shared PII redact-and-index gate (used by BOTH upload paths)
# ─────────────────────────────────────────────────────────────────────
#
# Product intent: when a document contains PII, the sensitive VALUES go to
# the encrypted Zone 1 Vault, and the REST of the document stays searchable
# via RAG. Concretely: a personal-info doc with a birthday → birthday vaulted,
# redacted document indexed and retrievable.
#
#   found               outcome
#   ────────────────    ─────────────────────────────────────────────────────
#   nothing             pass through, index original text
#   full names only     pass through, index original text, NO vault
#                       (redacting every name would destroy document utility)
#   anything else       vault ORIGINAL text (encrypted) + append PII record,
#     (email, phone,    index REDACTED text (every detected value replaced by
#     SSN, card, ...)   a plain "[... kept private]" marker)
#
# Email and phone stay on the vault side: the classifier used to rate them
# "high", so documents holding them were always vaulted, and a richer
# classifier must not quietly loosen that. The redacted copy comes from
# DataClassifier.redact_pii, so every type the classifier finds is removed -
# building it here from emails and phones alone once left an SSN in the
# searchable copy of a document whose original had been vaulted for that SSN.
#
# Fail-closed invariants (never relaxed):
#   * vault uninitialized/locked while high/critical PII present  → refuse
#   * secure write fails                                          → refuse
#   * PII scan raises (classifier crash / unavailable)            → refuse
#   Refusal = raise PiiVaultRefusal; the caller must NOT index any text and
#   must remove the plaintext staging copy. Unscanned or unredacted-high-PII
#   text must never reach ChromaDB or the JSON catalogs.

# Human-readable labels for the actionable refusal message. Only the
# high/critical types can trigger a refusal, so only those are mapped.
_HIGH_CRITICAL_LABELS = {
    'ssn': 'SSN',
    'credit_card': 'credit card',
    'address': 'address',
    'date_of_birth': 'DOB',
}

# Types that on their own do not send a document to the vault.
_INDEX_AS_IS_TYPES = frozenset({'name'})


def _needs_vault(detection) -> bool:
    """True when the original must go to the vault and only a redacted copy
    may be indexed: any detected type other than a full name."""
    if detection.severity in _HIGH_CRITICAL_SEVERITIES:
        return True
    return any(t not in _INDEX_AS_IS_TYPES for t in detection.pii_types)

_HIGH_CRITICAL_SEVERITIES = ('high', 'critical')


class PiiVaultRefusal(Exception):
    """Raised when a document contains high/critical PII but it cannot be
    securely vaulted (vault uninitialized/locked, or the secure write failed),
    OR when the PII scan itself could not run. Fail-closed contract: on this
    exception the caller MUST refuse the upload and index NOTHING. Carries an
    actionable, user-facing ``message`` (no PII values in it)."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class VaultWaiting(PiiVaultRefusal):
    """The text holds a key or a personal detail and the vault is not open.

    Raised only for callers that pass ``hold_when_locked=True`` (the data
    folder, linked folders, uploads). Nothing was indexed; the values were
    sealed into the vault's write-only inbox when it could take them
    (``sealed`` is ``vault_inbox.deposit_status``'s answer), and the caller
    keeps the item waiting until the vault is opened, then runs it again. A
    subclass of :class:`PiiVaultRefusal`, so a caller that does not know it
    still refuses (fail closed)."""

    def __init__(self, message: str, sealed: str = ""):
        super().__init__(message)
        self.sealed = sealed


#: Plain words for an item that waits for the vault.
WAITING_TEXT = ("This file has a key, a password or a personal detail in it. "
                "It is waiting for your vault: open your vault with your PIN "
                "and it is added by itself, with those details kept in the "
                "vault.")


@dataclass
class PiiScreenResult:
    """Outcome of :func:`screen_text_for_pii`.

    ``text_for_index`` is the ONLY text a caller may index: redacted when the
    document held high/critical PII, the original otherwise.
    """
    text_for_index: str
    pii_detected: bool = False
    pii_vaulted: bool = False
    pii_types: list = field(default_factory=list)
    severity: str = "none"
    summary: str = "No PII detected."


def _high_critical_label_str(detection) -> str:
    """Plain words for the personal details found, for the refusal message
    (e.g. "a birthday", "a Social Security number and a home address")."""
    try:
        from puffpai.security.data_classifier import PLAIN_WORDS
    except Exception:  # pragma: no cover - the classifier is a hard dependency
        PLAIN_WORDS = {}
    words = []
    for t in detection.pii_types:
        w = PLAIN_WORDS.get(t)
        if w and w not in words:
            words.append(w)
    if not words:
        return 'personal details'
    if len(words) == 1:
        return words[0]
    return ', '.join(words[:-1]) + ' and ' + words[-1]


def _vault_original_text(
    original_text: str,
    detection,
    *,
    filename: str,
    file_id: str,
    vault=None,
    source: str = "document_upload",
) -> None:
    """Encrypt-store ``original_text`` in the Zone 1 Vault and append a PII
    record. FAIL CLOSED: raises :class:`PiiVaultRefusal` if the vault is
    uninitialized/locked or the secure write fails. NEVER writes plaintext,
    NEVER logs PII values. Shared by the ingest path and the rescan endpoint so
    the vault-write + refusal semantics cannot drift apart.
    """
    active_vault = vault if vault is not None else _resolve_shared_vault()
    labels = _high_critical_label_str(detection)

    if not active_vault.is_initialized:
        logger.warning("Refusing PII content %s — vault not initialized", filename)
        from puffpai.security.vault import missing_words
        missing = missing_words(active_vault)
        if missing:
            raise PiiVaultRefusal(
                f"This document has personal details in it ({labels}), so I "
                f"did not add it. {missing}")
        raise PiiVaultRefusal(
            f"This document has personal details in it ({labels}), so I did "
            f"not add it. Set up your vault, open it with your PIN, then "
            f"upload the document again."
        )

    stem = os.path.splitext(os.path.basename(filename))[0]
    vault_copy_name = f"doc_{file_id}_{stem}.txt"

    # ENCRYPTED write only. False when the vault is locked; raises only on a
    # genuine write error — both fail closed.
    try:
        stored = _store_original_encrypted(
            active_vault, vault_copy_name, original_text)
    except Exception as e:  # genuine write error
        logger.warning(
            "Secure vaulting failed for %s — refusing (no plaintext written): %s",
            filename, e)
        raise PiiVaultRefusal(
            f"This document has personal details in it ({labels}), and I "
            f"could not put it in your vault, so I did not add it. Open your "
            f"vault with your PIN, then upload it again."
        ) from e

    if not stored:
        logger.warning(
            "Refusing PII content %s — vault locked (no plaintext written)",
            filename)
        raise PiiVaultRefusal(
            f"This document has personal details in it ({labels}), and your "
            f"vault is locked, so I did not add it. Open your vault with your "
            f"PIN, then upload it again."
        )

    # Document is encrypted at rest. Append the index record (best-effort;
    # append_auto_pii never raises and quarantines a corrupt log). A missed log
    # entry is logged, never downgraded to a plaintext write.
    pii_record = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'source': source,
        'filename': filename,
        'file_id': file_id,
        'severity': detection.severity,
        'pii_types': detection.pii_types,
        'summary': detection.summary(),
    }
    if not _append_pii_record(active_vault, pii_record):
        logger.warning(
            "PII log append skipped for %s — vault locked or log unwritable "
            "(document is encrypted; index entry missed)", filename)


def _store_original_encrypted(vault, name: str, text: str) -> bool:
    """Write ``text`` into the vault's encrypted document store.

    Returns False (writes nothing) when the vault is locked. The BuildBot vault
    exposes ``store_document`` (bytes); an older HomeBot-style vault exposed
    ``store_document_secure`` (text, returns a bool) - both are accepted so the
    fail-closed contract does not depend on which one is installed. This used
    to call ``store_document_secure`` only, which BuildBot's vault never had,
    so every upload holding a personal detail was refused even with the vault
    open.
    """
    secure = getattr(vault, 'store_document_secure', None)
    if callable(secure):
        return bool(secure(name, text))
    if not getattr(vault, 'is_unlocked', False):
        return False
    vault.store_document(name, text.encode('utf-8'))
    return True


def _append_pii_record(vault, record: dict) -> bool:
    """Append ``record`` to the vault's list of detected details. Never raises."""
    legacy = getattr(vault, 'append_auto_pii', None)
    try:
        if callable(legacy):
            return bool(legacy(record))
        if not getattr(vault, 'is_unlocked', False):
            return False
        from puffpai.security.vault_inbox import append_pii_record
        append_pii_record(vault, record)
        return True
    except Exception as e:
        logger.warning("PII record append failed: %s", type(e).__name__)
        return False


def _captured_values(key_hits, detection) -> dict:
    """``{kind: [values]}`` for everything that must be held: every key and
    every personal detail except full names. In memory only, then written
    encrypted."""
    held: dict[str, list] = {}
    for h in key_hits or ():
        held.setdefault("key:" + h.kind, []).append(
            {"value": h.value, "line": h.line})
    if detection is not None and detection.has_pii:
        for kind, values in detection.items().items():
            if kind in _INDEX_AS_IS_TYPES:
                continue
            held.setdefault(kind, []).extend(values)
    return held


def _kinds(key_hits, detection) -> list:
    kinds = sorted({"key:" + h.kind for h in key_hits or ()})
    if detection is not None and detection.has_pii:
        kinds += [t for t in detection.pii_types if t not in kinds]
    return kinds


def _held_summary(key_hits, detection) -> str:
    """Kinds and counts only: safe for a log line."""
    parts = []
    if key_hits:
        from puffpai.security import secret_detector
        parts.append("keys: " + secret_detector.summary(key_hits))
    if detection is not None and detection.has_pii:
        parts.append(detection.summary())
    return "; ".join(parts) or "nothing"


def _vault_captured(active_vault, key_hits, detection, *, filename: str,
                    file_id: str, source_path: str | None, source: str) -> None:
    """Write the captured values plus a pointer (file, path, line) into the
    vault, encrypted. FAIL CLOSED like :func:`_vault_original_text`."""
    if not active_vault.is_initialized:
        raise PiiVaultRefusal(
            "This file has a key, a password or a personal detail in it, so I "
            "did not add it. Set up your vault, then add it again.")
    payload = json.dumps({
        "schema": "puffpai_held_values/v1",
        "filename": filename, "file_id": file_id,
        "path": source_path or "",
        "found_at": datetime.now(timezone.utc).isoformat(),
        "held": _captured_values(key_hits, detection),
    }, ensure_ascii=False)
    safe_id = re.sub(r"[^\w.\-]", "_", str(file_id))[:120]
    try:
        stored = _store_original_encrypted(
            active_vault, f"held_{safe_id}.json", payload)
    except Exception as e:  # genuine write error
        logger.warning("Holding values in the vault failed for %s: %s",
                       filename, type(e).__name__)
        raise PiiVaultRefusal(
            "This file has a key, a password or a personal detail in it, and I "
            "could not put those in your vault, so I did not add it.") from e
    if not stored:
        raise PiiVaultRefusal(
            "This file has a key, a password or a personal detail in it, and "
            "your vault is locked, so I did not add it. Open your vault with "
            "your PIN, then add it again.")
    _append_pii_record(active_vault, {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'source': source, 'filename': filename, 'file_id': file_id,
        'path': source_path or "",
        'severity': 'high', 'pii_types': _kinds(key_hits, detection),
        'summary': _held_summary(key_hits, detection),
    })


def _hold_for_vault(active_vault, key_hits, detection, *, filename: str,
                    file_id: str, source_path: str | None, source: str):
    """The vault is not open: seal the captured values into its write-only
    inbox (no PIN needed) and raise :class:`VaultWaiting`. Never indexes."""
    status = "no_vault"
    try:
        from puffpai.security import vault_inbox
        status = vault_inbox.deposit_status(active_vault.vault_dir, {
            "source": source, "filename": filename, "file_id": file_id,
            "path": source_path or "",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "pii_types": _kinds(key_hits, detection),
            "held": _captured_values(key_hits, detection),
        })
    except Exception as e:  # noqa: BLE001 - the item still waits, unindexed
        logger.info("Vault inbox did not take %s: %s", filename,
                    type(e).__name__)
    logger.info("%s waits for the vault (%s; inbox: %s)", filename,
                _held_summary(key_hits, detection), status)
    raise VaultWaiting(WAITING_TEXT, sealed=str(status))


def screen_text_for_pii(
    text: str,
    filename: str,
    *,
    file_id: str | None = None,
    vault=None,
    source: str = "document_upload",
    source_path: str | None = None,
    hold_when_locked: bool = False,
    keep_original: bool = False,
) -> PiiScreenResult:
    """Scan ``text``; vault + redact keys and high/critical PII, pass
    everything else through. See the module-level matrix. Returns a
    :class:`PiiScreenResult` whose ``text_for_index`` is the only text the
    caller may index.

    Keys, tokens and passwords (:mod:`puffpai.security.secret_detector`) are
    found first and always held: the searchable copy says ``[held in vault]``
    and the values go into the vault with a pointer (file name, path, line).
    ``keep_original`` (a file that stays where the owner keeps it: the data
    folder, a linked folder) holds only the captured values plus that
    pointer; otherwise the whole original is vaulted too, as before.

    Raises :class:`PiiVaultRefusal` on every fail-closed condition (scan crash,
    vault locked/uninitialized while something must be held, secure-write
    failure). With ``hold_when_locked`` a closed vault raises
    :class:`VaultWaiting` instead: the item waits, unindexed.
    """
    if file_id is None:
        file_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")

    # FAIL CLOSED around the scan. If the classifier can't run (import failure,
    # mid-scan crash), we do NOT know whether this text holds PII, so it must
    # never reach the index — refuse. (This is stricter than the old Path A,
    # which skipped on ImportError; the classifier is a hard local dependency
    # and a broken import should be loud, not a silent PII bypass.)
    try:
        from puffpai.security import secret_detector
        keyless, key_hits = secret_detector.redact(text)
        from puffpai.security.pii_classifier import PIIClassifier
        classifier = PIIClassifier()
        # One call does both: every type the classifier finds is removed from
        # the copy that may be indexed.
        redacted, detection = classifier.redact_pii(keyless)
    except Exception as e:
        logger.warning(
            "PII scan failed for %s — refusing (text not indexed): %s",
            filename, type(e).__name__)
        raise PiiVaultRefusal(
            "I could not check this document for personal details, so I did "
            "not add it. Nothing from it was saved. Try uploading it again."
        ) from e

    if not key_hits and not detection.has_pii:
        return PiiScreenResult(text_for_index=text)

    pii_vault = detection.has_pii and _needs_vault(detection)
    if key_hits or pii_vault:
        active_vault = vault if vault is not None else _resolve_shared_vault()
        held_pii = detection if pii_vault else None
        if hold_when_locked and not (
                active_vault.is_initialized
                and getattr(active_vault, "is_unlocked", False)):
            _hold_for_vault(active_vault, key_hits, held_pii,
                            filename=filename, file_id=file_id,
                            source_path=source_path, source=source)
        if pii_vault and not keep_original:
            # Vault the ORIGINAL (raises PiiVaultRefusal if it can't be
            # secured), then index only the REDACTED text.
            _vault_original_text(
                text, detection,
                filename=filename, file_id=file_id, vault=active_vault,
                source=source)
        if key_hits or keep_original:
            _vault_captured(active_vault, key_hits, held_pii,
                            filename=filename, file_id=file_id,
                            source_path=source_path, source=source)
        logger.info("Held in the vault for %s: %s; redacted copy indexed",
                    filename, _held_summary(key_hits, held_pii))
        return PiiScreenResult(
            text_for_index=redacted if pii_vault else keyless,
            pii_detected=True,
            pii_vaulted=True,
            pii_types=_kinds(key_hits, detection),
            severity=detection.severity if pii_vault else "high",
            summary=_held_summary(key_hits, detection),
        )

    # Full names only: index as-is, no vault — redacting every name would gut
    # document utility. Detection is still reported.
    return PiiScreenResult(
        text_for_index=text,
        pii_detected=True,
        pii_vaulted=False,
        pii_types=detection.pii_types,
        severity=detection.severity,
        summary=detection.summary(),
    )


def ingest_file(
    file_path: str,
    original_filename: str | None = None,
    user_hint: str | None = None,
    auto_apply: bool = True,
    *,
    vault=None,
    file_id: str | None = None,
    keep_original: bool = False,
    hold_when_locked: bool = False,
    source: str = "document_upload",
) -> IngestionResult:
    """End-to-end pipeline: extract text, classify, parse, route to apps.

    Args:
        file_path: Path to the saved upload.
        original_filename: Original name from the user (for display).
        user_hint: Optional hint from the user about what this is
            (e.g. "this is mom's lasagna recipe", "school play flyer").
            Skips classification if provided.
        auto_apply: When True, automatically save the parsed result to
            the appropriate app store (recipe → freshtable, event →
            calendar). When False, just return the parsed payload.
        vault: Optional Vault instance (dependency injection for tests). When
            None, the process-wide singleton is used so live unlock sessions
            are visible.

    Returns:
        IngestionResult with the final state.
    """
    filename = original_filename or os.path.basename(file_path)
    # ``keep_original``: the file is the owner's, where she keeps it (the data
    # folder, a linked folder). It is read, never changed, moved or removed.
    # ``hold_when_locked``: a closed vault makes it wait, unindexed, instead
    # of refusing it.
    file_id = file_id or os.path.basename(file_path)
    result = IngestionResult(
        file_id=file_id,
        filename=filename,
        file_type="",
        classification="",
        text_length=0,
    )

    try:
        ftype, text = extract_text(file_path)
        result.file_type = ftype
        result.text_length = len(text)

        # Hard-fail on extractor error placeholders — never index the
        # literal "[python-docx not installed …]" / "[OCR error …]" string.
        fail_reason = _extraction_failure_reason(text)
        if fail_reason:
            result.text_length = 0
            result.error = f"Could not read the file: {fail_reason}"
            return result

        if not text or len(text) < 5:
            result.error = "No text could be extracted from the file."
            return result

        # ── PII scan — vault sensitive VALUES, index the REDACTED rest ──
        # Product intent: a doc with PII → the sensitive values go to the
        # encrypted Zone 1 Vault and the REDACTED document stays searchable via
        # RAG. High/critical PII (SSN/CC/address/DOB) → vault original + index
        # redacted; medium-only (email/phone/name) → index as-is (redacting
        # every name would gut utility); none → pass through. Shared gate keeps
        # the fail-closed invariants identical across both upload paths.
        def _remove_staging():
            # Best-effort: delete the unencrypted staging copy so the plaintext
            # original does not linger in /uploads once its sensitive values
            # live only in the vault (or the upload is refused). Never the
            # owner's own file.
            if keep_original:
                return
            try:
                staging = Path(file_path)
                if staging.exists():
                    staging.unlink()
            except Exception as _e:
                logger.debug("Could not remove staging copy: %s", _e)

        try:
            screen = screen_text_for_pii(
                text, filename, file_id=file_id, vault=vault,
                source=source, source_path=file_path,
                hold_when_locked=hold_when_locked,
                keep_original=keep_original)
        except VaultWaiting as waiting:
            # Not refused and not indexed: it waits for the vault. The file
            # stays where it is; knowledge/my_data.py runs it again once the
            # vault is open.
            result.pii_detected = True
            result.waiting_for_vault = True
            result.actions_taken.append(waiting.message)
            return result
        except PiiVaultRefusal as refusal:
            # FAIL CLOSED: high/critical PII that could not be secured, or a
            # scan crash. Index NOTHING; remove the plaintext staging copy.
            result.pii_detected = True
            result.error = refusal.message
            result.actions_taken.append(f"⚠️ Upload refused: {refusal.message}")
            _remove_staging()
            return result

        if screen.pii_detected:
            result.pii_detected = True
            result.pii_summary = screen.summary
        if screen.pii_vaulted:
            result.vaulted = True
            result.actions_taken.append(
                "This document had a key or personal details in it. Those are "
                "kept in your vault; a copy with them taken out is searchable."
            )
            # The original (unredacted) staging copy must not linger as
            # plaintext now that its sensitive values live only in the vault.
            _remove_staging()

        # Everything downstream — classify, parse, index — operates on the
        # screened text: redacted when vaulted, original otherwise. Nothing
        # unredacted-high-PII reaches ingested_docs.json or ChromaDB.
        text = screen.text_for_index

        # If user gave a hint, use it; otherwise classify
        if user_hint:
            hint_lower = user_hint.lower()
            if any(k in hint_lower for k in ("recipe", "ingredient", "cook")):
                classification = "recipe"
            elif any(k in hint_lower for k in ("event", "flyer", "party", "play", "concert", "appointment")):
                classification = "event"
            elif any(k in hint_lower for k in ("schedule", "calendar", "agenda", "syllabus")):
                classification = "schedule"
            elif any(k in hint_lower for k in ("study", "textbook", "notes", "homework", "lecture")):
                classification = "study"
            elif any(k in hint_lower for k in ("shopping", "grocer", "buy")):
                classification = "shopping_list"
            else:
                classification = classify_document(text)
        else:
            classification = classify_document(text)

        result.classification = classification

        # ALWAYS index the raw text so it can be searched later, regardless
        # of classification. This way recipes/events/etc are still findable
        # by `search_my_documents` even after they've been routed to apps.
        # A failure here must NOT be swallowed — an un-indexed document that
        # the UI reports as "indexed" is exactly the bug this path had. Let it
        # propagate to the outer handler so `result.error` is set honestly.
        _index_study_doc(file_id, filename, text, classification=classification)

        # Parse + (optionally) apply
        if classification == "recipe":
            parsed = parse_recipe(text)
            result.structured_data = parsed
            if auto_apply and parsed:
                from puffpai.app_data import AppData
                app = AppData()
                app.fresh_create_recipe(
                    parsed["name"],
                    parsed["ingredients"],
                    parsed["instructions"],
                )
                result.actions_taken.append(f"Added recipe '{parsed['name']}' to FreshTable")

        elif classification == "event":
            parsed = parse_event(text)
            result.structured_data = parsed
            if auto_apply and parsed:
                from puffpai.app_data import AppData
                app = AppData()
                app.cal_add_event(
                    title=parsed["title"],
                    start_iso=parsed["start"],
                    location=parsed.get("location") or None,
                )
                result.actions_taken.append(f"Added event '{parsed['title']}' to Calendar")

        elif classification == "schedule":
            events = parse_schedule(text)
            result.structured_data = {"events": events}
            if auto_apply and events:
                from puffpai.app_data import AppData
                app = AppData()
                for ev in events:
                    try:
                        app.cal_add_event(
                            title=ev.get("title", "Untitled"),
                            start_iso=ev.get("start", ""),
                            location=ev.get("location") or None,
                        )
                    except Exception as e:
                        logger.warning("Failed to add event %s: %s", ev, e)
                result.actions_taken.append(
                    f"Added {len(events)} events to Calendar from schedule"
                )

        elif classification == "study":
            # Already indexed above; just record the action.
            result.actions_taken.append(f"Indexed '{filename}' for study Q&A")
            result.structured_data = {
                "preview": text[:500],
                "word_count": len(text.split()),
            }

        elif classification == "shopping_list":
            # Parse line-by-line for items
            items = _parse_shopping_lines(text)
            result.structured_data = {"items": items}
            if auto_apply and items:
                from puffpai.app_data import AppData
                app = AppData()
                for item in items:
                    try:
                        app.fresh_add_shopping(
                            item.get("name", ""),
                            item.get("quantity", 1),
                            0,
                        )
                    except Exception as e:
                        logger.warning("Failed to add shopping item %s: %s", item, e)
                result.actions_taken.append(
                    f"Added {len(items)} items to shopping list"
                )

        else:  # 'note' or 'unknown'
            result.actions_taken.append(f"Saved '{filename}' as a searchable note")
            result.structured_data = {"preview": text[:500]}

    except Exception as e:
        logger.exception("Ingestion failed for %s", file_path)
        result.error = str(e)

    return result


def _parse_shopping_lines(text: str) -> list[dict]:
    """Naive parsing of a shopping list — one item per line."""
    items = []
    for line in text.splitlines():
        line = line.strip().strip("-*•").strip()
        if not line or len(line) > 200:
            continue
        # Try to extract leading quantity
        m = re.match(r"^(\d+)\s+(.+)$", line)
        if m:
            items.append({"name": m.group(2).strip(), "quantity": int(m.group(1))})
        else:
            items.append({"name": line, "quantity": 1})
    return items[:50]  # Cap at 50 items per upload


def _index_study_doc(
    file_id: str, filename: str, text: str, classification: str = "study"
):
    """Append a doc to the JSON index AND chunk it into the ChromaDB
    `user_uploads` collection so semantic search works.

    The JSON index is kept as a fast metadata catalog (filename,
    classification, indexed_at) and as the source of truth for the full
    raw text. ChromaDB is the search index.
    """
    # ── 1. JSON metadata catalog ────────────────────────────────────────
    try:
        if _doc_index().exists():
            with open(_doc_index(), "r", encoding="utf-8") as f:
                index = json.load(f)
        else:
            index = []
    except Exception:
        index = []

    entry = {
        "id": file_id,
        "filename": filename,
        "classification": classification,
        "indexed_at": datetime.now().isoformat(),
        "text": text,
        "word_count": len(text.split()),
    }
    # Replace existing if same ID, else append
    index = [e for e in index if e.get("id") != file_id]
    index.append(entry)

    _doc_index().parent.mkdir(parents=True, exist_ok=True)
    with open(_doc_index(), "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2, ensure_ascii=False)

    # ── 2. ChromaDB semantic index ──────────────────────────────────────
    # Chunk the text and embed it into the user_uploads collection so
    # the existing RAG pipeline can find it semantically. Failures propagate
    # to the caller (ingest_file) so the upload is honestly reported as failed
    # rather than silently un-searchable.
    _add_to_user_uploads_collection(file_id, filename, text, classification)


# ─────────────────────────────────────────────────────────────────────
#  ChromaDB user_uploads collection
# ─────────────────────────────────────────────────────────────────────

USER_UPLOADS_COLLECTION = "user_uploads"

# Conservative chunk size — fits comfortably in the LLM context window
# and gives good semantic granularity. ~150 words per chunk with overlap.
_CHUNK_SIZE_CHARS = 900
_CHUNK_OVERLAP_CHARS = 150


def _chunk_text(text: str) -> list[str]:
    """Split text into overlapping chunks at sentence/paragraph boundaries.

    Tries to break on \\n\\n first, then \\n, then ". ", then falls back
    to a hard split. Each chunk has roughly 150-word coherent context.
    """
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= _CHUNK_SIZE_CHARS:
        return [text]

    chunks = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + _CHUNK_SIZE_CHARS, n)
        # Try to find a natural break near the end
        if end < n:
            for boundary in ("\n\n", "\n", ". ", " "):
                b = text.rfind(boundary, start + _CHUNK_SIZE_CHARS // 2, end)
                if b > 0:
                    end = b + len(boundary)
                    break
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        # Step forward, leaving overlap
        start = max(start + 1, end - _CHUNK_OVERLAP_CHARS)
        if start >= n:
            break
    return chunks


_upload_embedder = None


def _get_upload_embedder():
    """Cached embedding function used to PIN new upload collections to the same
    model the RAG query path uses (`_get_query_embedder` in api_server).

    Uses ChromaDB's bundled default embedder (onnxruntime all-MiniLM-L6-v2),
    which ships with chromadb and needs no extra ``sentence-transformers``
    dependency, so document indexing works out of the box on a clean install.
    Both the upload (write) and query (read) paths use this same default
    embedder, so their vectors are directly comparable.

    The model comes only from :mod:`puffpai.knowledge.reading_helper`, which
    never downloads it. Returns None when the reading helper is not on this
    computer; uploads then answer through their word look-up alone."""
    global _upload_embedder
    if _upload_embedder is None:
        from puffpai.knowledge import reading_helper
        _upload_embedder = reading_helper.get_embedder()
    return _upload_embedder


def get_or_create_upload_collection(
    client, name, extra_metadata=None, embedding_function=None
):
    """Get or create a Chroma collection for user-uploaded content.

    NEW collections are pinned to all-MiniLM-L6-v2 + cosine space, so the
    0.75/0.25 relevance math in `_get_rag_context` is exact. EXISTING
    collections are returned untouched: Chroma 1.x raises on an embedding-
    function conflict and ignores metadata on get, so we must NOT pass either
    when the collection already exists. The space-aware relevance filter in
    `_get_rag_context` handles their legacy l2 space.
    """
    try:
        return client.get_collection(name=name)
    except Exception:
        pass  # Does not exist yet — create it pinned below.

    ef = embedding_function or _get_upload_embedder()
    if ef is None:
        from puffpai.knowledge.reading_helper import HelperMissing
        raise HelperMissing()
    metadata = {"hnsw:space": "cosine"}
    if extra_metadata:
        metadata.update(extra_metadata)
    try:
        return client.create_collection(
            name=name, embedding_function=ef, metadata=metadata)
    except Exception:
        # Lost a create race with a concurrent request — return the existing.
        return client.get_collection(name=name)


def _get_user_uploads_collection():
    """Return (or lazily create) the ChromaDB collection for user uploads.

    Raises on failure (import/client/creation) — callers on the WRITE path
    must surface this so an upload is never reported "indexed" when it was
    not. Read-only callers (query/delete) catch and degrade gracefully.
    """
    from puffpai.knowledge.rag import DualZoneRAG

    rag = DualZoneRAG()
    client = rag.client
    if client is None:
        raise RuntimeError("ChromaDB client unavailable (DualZoneRAG.client is None)")
    return get_or_create_upload_collection(
        client,
        USER_UPLOADS_COLLECTION,
        extra_metadata={
            "description": "User-uploaded documents indexed by PuffPAI ingestion"
        },
    )


def _add_to_user_uploads_collection(
    file_id: str, filename: str, text: str, classification: str
) -> str:
    """Chunk text into the uploads' word look-up, then the ChromaDB collection.

    The word look-up (:mod:`puffpai.knowledge.upload_search`) is the quick
    stage every upload gets; a failure there raises, so the upload is never
    reported searchable when it is not. The meaning look-up in ChromaDB is
    added on top when the reading helper is on this computer. Without the
    helper, or if the store will not take it, the upload still answers by its
    own words: it is never refused for that. Returns ``"both"`` or ``"words"``.
    """
    chunks = _chunk_text(text)
    if not chunks:
        raise RuntimeError(
            f"No indexable text chunks produced for {filename}"
        )
    from puffpai.knowledge import upload_search
    upload_search.add(file_id, filename, chunks)
    try:
        _add_upload_meaning(file_id, filename, chunks, classification)
    except Exception as e:  # noqa: BLE001 - the words already answer
        logger.info("Upload %s answers by its words only for now: %s",
                    filename, e)
        return "words"
    return "both"


def _add_upload_meaning(file_id: str, filename: str, chunks: list[str],
                        classification: str) -> None:
    """The ChromaDB half of an upload. Raises when it could not be stored."""
    collection = _get_user_uploads_collection()

    # Remove old chunks for this file (idempotent re-upload). A delete of a
    # non-existent file_id is a no-op, but a real failure here should surface.
    collection.delete(where={"file_id": file_id})

    ids = [f"{file_id}_chunk_{i}" for i in range(len(chunks))]
    indexed_at = datetime.now().isoformat()
    metadatas = [
        {
            "file_id": file_id,
            "filename": filename,
            "classification": classification,
            "chunk_index": i,
            "total_chunks": len(chunks),
            "indexed_at": indexed_at,
            "source": "user_upload",
        }
        for i in range(len(chunks))
    ]
    collection.add(ids=ids, documents=chunks, metadatas=metadatas)

    # Verify the chunks actually landed before claiming success.
    stored = collection.get(where={"file_id": file_id}, include=[])
    stored_ids = stored.get("ids", []) if stored else []
    if len(stored_ids) < len(chunks):
        raise RuntimeError(
            f"Indexing verification failed for {filename}: "
            f"stored {len(stored_ids)} of {len(chunks)} chunks"
        )
    logger.info(
        "Added %d chunks of %s to user_uploads collection",
        len(chunks), filename,
    )


def _delete_from_user_uploads_collection(file_id: str) -> dict:
    """Remove every chunk of a file from user_uploads, and say what happened.

    Delegates the finding-and-counting to
    :func:`puffpai.knowledge.registry.evict_document`, which is the same
    eviction used for registry documents and for revoked source packs - one
    implementation, one definition of "actually gone". The only difference here
    is how the uploads writer names its chunks: ``{file_id}_chunk_{i}``, under a
    ``file_id`` metadata label.

    Returns the eviction report. It never raises, because a store that cannot be
    reached is a result the caller has to act on, not an exception to swallow.
    """
    from puffpai.knowledge import upload_search
    from puffpai.knowledge.registry import evict_document

    from puffpai.knowledge.reading_helper import HelperMissing

    try:
        collection = _get_user_uploads_collection()
    except HelperMissing:
        # There is no uploads collection and no reading helper to make one:
        # the upload was only ever in its word look-up.
        words = upload_search.remove(file_id)
        return {"ok": True, "found": words, "evicted": words, "remaining": 0,
                "unidentified": 0,
                "reason": "Its text is out of the search."}
    except Exception as e:  # noqa: BLE001 - reported, not swallowed
        logger.warning("ChromaDB unavailable for %s: %s", file_id, e)
        return {
            "ok": False, "found": None, "evicted": 0, "remaining": None,
            "unidentified": 0,
            "reason": (
                "The knowledge store could not be reached, so nothing was "
                "removed. The text is still there and still answering."
            ),
        }
    result = evict_document(collection, file_id,
                            metadata_key="file_id",
                            id_template="{doc}_chunk_{i}")
    if result.get("remaining") == 0:
        upload_search.remove(file_id)
    logger.info("user_uploads eviction for %s: %s", file_id, result["reason"])
    return result


def query_user_uploads(query: str, n_results: int = 5) -> list[dict]:
    """Semantic vector search across all user-uploaded documents.

    Returns a list of {filename, classification, snippet, score, file_id}
    dicts, one per matching chunk, sorted by relevance.

    The uploads' word look-up is merged in (one row per passage), so an
    upload answers by its own words even with no reading helper. A word hit
    carries ``distance = 1 - relevance`` in cosine space, which the chat's
    relevance maths turns back into the same number.
    """
    from puffpai.knowledge import upload_search
    words = upload_search.search(query, n=max(1, min(n_results, 20)))
    try:
        if _get_upload_embedder() is None:  # no reading helper: words only
            return _upload_word_rows(words, [])
        collection = _get_user_uploads_collection()
        results = collection.query(
            query_texts=[query],
            n_results=max(1, min(n_results, 20)),
        )
    except Exception as e:
        logger.warning("ChromaDB query failed: %s", e)
        return _upload_word_rows(words, [])

    # Expose the collection's distance space so callers can normalize
    # distances to a comparable relevance score (l2 is squared, cosine is not).
    space = (collection.metadata or {}).get("hnsw:space", "l2")

    if not results or not results.get("documents"):
        return _upload_word_rows(words, [])

    docs = results["documents"][0] if results["documents"] else []
    metas = results["metadatas"][0] if results.get("metadatas") else []
    distances = results["distances"][0] if results.get("distances") else []

    out = []
    for i, doc in enumerate(docs):
        meta = metas[i] if i < len(metas) else {}
        distance = distances[i] if i < len(distances) else 1.0
        out.append({
            "file_id": meta.get("file_id", ""),
            "filename": meta.get("filename", "(unknown)"),
            "classification": meta.get("classification", "unknown"),
            "snippet": doc,
            "score": round(1.0 - min(distance, 1.0), 3),
            "distance": distance,
            "space": space,
            "chunk_index": meta.get("chunk_index", 0),
        })
    return _upload_word_rows(words, out)


def _upload_word_rows(words: list[dict], vector_rows: list[dict]) -> list[dict]:
    """Vector rows plus word rows, one row per (file, passage), best first."""
    seen = {(r.get("file_id"), int(r.get("chunk_index") or 0)): r for r in vector_rows}
    catalog: dict = {}
    for hit in words:
        key = (hit["file_id"], int(hit["chunk_index"]))
        rel = float(hit["relevance"])
        if key in seen:
            row = seen[key]
            if rel > row.get("score", 0):
                row["score"] = round(rel, 3)
                row["distance"], row["space"] = 1.0 - rel, "cosine"
            continue
        if not catalog:
            catalog = {e.get("id"): e for e in _read_catalog()}
        meta = catalog.get(hit["file_id"], {})
        seen[key] = {"file_id": hit["file_id"], "filename": hit["filename"],
                     "classification": meta.get("classification", "unknown"),
                     "snippet": hit["snippet"], "score": round(rel, 3),
                     "distance": 1.0 - rel, "space": "cosine",
                     "chunk_index": int(hit["chunk_index"])}
    return sorted(seen.values(), key=lambda r: r.get("score", 0), reverse=True)


def _read_catalog() -> list[dict]:
    try:
        with open(_doc_index(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:  # noqa: BLE001
        return []


def reindex_all_uploads():
    """Re-chunk and re-embed every doc in ingested_docs.json into ChromaDB.

    Used as a one-shot migration when the schema changes or after a
    fresh checkout. Safe to call repeatedly — each file_id is replaced
    in place.
    """
    if not _doc_index().exists():
        return {"reindexed": 0}
    with open(_doc_index(), "r", encoding="utf-8") as f:
        index = json.load(f)

    count = 0
    for entry in index:
        try:
            _add_to_user_uploads_collection(
                entry["id"],
                entry["filename"],
                entry.get("text", ""),
                entry.get("classification", "unknown"),
            )
            count += 1
        except Exception as e:
            logger.warning("Failed to reindex %s: %s", entry.get("id"), e)
    return {"reindexed": count, "total": len(index)}


_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "from", "by", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will", "would",
    "could", "should", "may", "might", "can", "this", "that", "these",
    "those", "i", "me", "my", "you", "your", "we", "our", "it", "its",
    "what", "where", "when", "why", "how", "who", "which",
    "tell", "show", "give", "find", "search", "list", "please",
    "about", "into", "than", "then", "out", "up", "down",
})


def search_indexed_docs(query: str, max_results: int = 5) -> list[dict]:
    """Hybrid search across user-uploaded documents.

    Combines:
      1. ChromaDB vector search (semantic, primary)
      2. Filename substring boost (catches acronyms/exact-name queries
         where the body never contains the term)
      3. JSON metadata catalog (for word_count, indexed_at, etc.)

    Returns one result per UNIQUE document, with the best matching
    chunk as the snippet.
    """
    if not query or not query.strip():
        return []

    # ── 1. Vector search (semantic) ────────────────────────────────────
    vector_hits = query_user_uploads(query, n_results=max_results * 3)

    # ── 2. Filename match — boost docs whose name contains query words ─
    raw_words = re.findall(r"\w+", query.lower())
    query_words = [w for w in raw_words if w not in _STOPWORDS and len(w) >= 3]
    if not query_words:
        query_words = raw_words

    # Load metadata catalog for filename matching + word_count
    catalog = {}
    if _doc_index().exists():
        try:
            with open(_doc_index(), "r", encoding="utf-8") as f:
                for entry in json.load(f):
                    catalog[entry["id"]] = entry
        except Exception:
            pass

    # ── 3. Aggregate per-document, keeping the best chunk ─────────────
    by_doc: dict[str, dict] = {}
    for hit in vector_hits:
        fid = hit.get("file_id", "")
        if not fid:
            continue
        # Score combines semantic relevance + filename hit boost
        fname_lower = hit.get("filename", "").lower()
        fname_hits = sum(1 for w in query_words if w in fname_lower)
        combined_score = hit.get("score", 0) + fname_hits * 0.3

        if fid not in by_doc or combined_score > by_doc[fid]["score"]:
            meta = catalog.get(fid, {})
            by_doc[fid] = {
                "id": fid,
                "filename": hit.get("filename", "(unknown)"),
                "classification": hit.get("classification", "study"),
                "indexed_at": meta.get("indexed_at", ""),
                "word_count": meta.get("word_count", 0),
                "score": combined_score,
                "snippet": hit.get("snippet", "")[:800],
            }

    # ── 4. Filename-only fallback (for acronym matches like a club's initials) ───
    # Pull in any docs from the catalog whose filename matches but
    # weren't returned by the vector search.
    for fid, entry in catalog.items():
        if fid in by_doc:
            continue
        fname_lower = entry.get("filename", "").lower()
        fname_hits = sum(1 for w in query_words if w in fname_lower)
        if fname_hits == 0:
            continue
        # Use the start of the document as a snippet
        text = entry.get("text", "")
        snippet = text[:600].strip()
        if len(text) > 600:
            snippet += "…"
        by_doc[fid] = {
            "id": fid,
            "filename": entry["filename"],
            "classification": entry.get("classification", "study"),
            "indexed_at": entry.get("indexed_at", ""),
            "word_count": entry.get("word_count", 0),
            "score": fname_hits * 0.5,  # filename-only is a moderate hit
            "snippet": snippet,
        }

    # Sort by score, return top N
    results = sorted(by_doc.values(), key=lambda r: r["score"], reverse=True)
    return results[:max_results]


def list_ingested_docs() -> list[dict]:
    """Return a summary of all ingested documents."""
    if not _doc_index().exists():
        return []
    try:
        with open(_doc_index(), "r", encoding="utf-8") as f:
            index = json.load(f)
    except Exception:
        return []
    return [
        {
            "id": e.get("id"),
            "filename": e.get("filename"),
            "classification": e.get("classification"),
            "indexed_at": e.get("indexed_at"),
            "word_count": e.get("word_count", 0),
        }
        for e in index
    ]


def delete_ingested_doc_report(doc_id: str) -> dict:
    """Delete an indexed document and report exactly what went with it.

    Order matters and is the whole fix: the chunks come out of ChromaDB FIRST,
    and the catalog entry is dropped only once the store has confirmed nothing
    identifiable is left. The old order wrote the catalog first and swallowed a
    ChromaDB failure, which meant a "deleted" document could keep answering
    questions with no record left to find it by. If the store cannot be reached
    now, nothing at all is touched - not the catalog, not the file on disk.

    Returns ``{ok, doc_id, catalog_removed, chunks_evicted, chunks_remaining,
    file_removed, reason, limits}``. ``ok`` is True only when the catalog entry,
    the chunks and the uploaded file all went together.
    """
    if not _doc_index().exists():
        return {"ok": False, "doc_id": doc_id, "catalog_removed": False,
                "chunks_evicted": 0, "chunks_remaining": None,
                "file_removed": False,
                "reason": "No documents have been indexed on this build.",
                "limits": []}
    try:
        with open(_doc_index(), "r", encoding="utf-8") as f:
            index = json.load(f)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "doc_id": doc_id, "catalog_removed": False,
                "chunks_evicted": 0, "chunks_remaining": None,
                "file_removed": False,
                "reason": f"The document catalog could not be read: {e}",
                "limits": []}

    new_index = [e for e in index if e.get("id") != doc_id]
    if len(new_index) == len(index):
        return {"ok": False, "doc_id": doc_id, "catalog_removed": False,
                "chunks_evicted": 0, "chunks_remaining": None,
                "file_removed": False,
                "reason": "No indexed document has that id.",
                "limits": []}

    # 1. The text, first. Everything after this is bookkeeping.
    eviction = _delete_from_user_uploads_collection(doc_id)
    limits: list[str] = []
    if eviction["remaining"] != 0:
        limits.append(str(eviction["reason"]))
        limits.append(
            "The catalog entry and the uploaded file were kept, because the "
            "text is still in the knowledge store and they are the only way to "
            "find it again."
        )
        return {"ok": False, "doc_id": doc_id, "catalog_removed": False,
                "chunks_evicted": eviction["evicted"],
                "chunks_remaining": eviction["remaining"],
                "file_removed": False,
                "reason": f"'{doc_id}' was not deleted.",
                "limits": limits}
    if not eviction["ok"]:
        limits.append(str(eviction["reason"]))

    # 2. The catalog entry.
    catalog_removed = False
    try:
        with open(_doc_index(), "w", encoding="utf-8") as f:
            json.dump(new_index, f, indent=2, ensure_ascii=False)
        catalog_removed = True
    except Exception as e:  # noqa: BLE001
        limits.append(
            f"The chunks are out of the knowledge store, but the catalog entry "
            f"could not be rewritten ({e}), so this document still shows in the "
            f"document list."
        )

    # 3. The uploaded file on disk: the old uploads folder, and an upload
    # kept in My files (never a linked folder: knowledge/my_data.py).
    file_removed = False
    try:
        upload_path = _upload_dir() / doc_id
        if upload_path.exists():
            upload_path.unlink()
        from puffpai.knowledge import my_data
        my_data.remove_upload_file(doc_id)
        file_removed = True
    except Exception as e:  # noqa: BLE001
        limits.append(
            f"The uploaded file could not be removed from disk ({e}), so a copy "
            f"of this document is still in the uploads folder."
        )

    ok = bool(eviction["ok"]) and catalog_removed and file_removed and not limits
    reason = (
        f"Deleted. {eviction['evicted']} chunks are out of the knowledge store, "
        f"the catalog entry is gone and the uploaded file is gone."
        if ok else f"'{doc_id}' was not fully deleted."
    )
    return {"ok": ok, "doc_id": doc_id, "catalog_removed": catalog_removed,
            "chunks_evicted": eviction["evicted"],
            "chunks_remaining": eviction["remaining"],
            "file_removed": file_removed, "reason": reason, "limits": limits}


def delete_ingested_doc(doc_id: str) -> bool:
    """Remove an indexed document by ID - from ChromaDB, the JSON catalog,
    AND the uploads directory on disk.

    True means all three went. Anything less is False, including a document
    whose chunks are still in the store, because a delete that leaves the text
    answering questions is not a delete. Callers that need to say what happened
    should use :func:`delete_ingested_doc_report`.
    """
    return bool(delete_ingested_doc_report(doc_id)["ok"])
