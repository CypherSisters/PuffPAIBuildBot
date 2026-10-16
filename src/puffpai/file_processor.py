"""
PuffPAI Multi-Format File Processor — Extract text from various file types.

Supports: .txt, .md, .json, .pdf, .docx, .doc, .pptx, .csv, .xlsx, .html,
          .jpg, .jpeg, .png, .gif, .bmp, .tiff (OCR), .rtf, .epub

    from puffpai.file_processor import extract_text, SUPPORTED_EXTENSIONS

    text, metadata = extract_text("/path/to/document.docx")

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
from html.parser import HTMLParser
from typing import Any

logger = logging.getLogger(__name__)

# ── Supported file types ─────────────────────────────────────────────────

TEXT_EXTENSIONS = {'.txt', '.md', '.log', '.ini', '.cfg', '.yaml', '.yml',
                   '.toml', '.xml', '.py', '.js', '.html', '.css', '.sh',
                   '.bat', '.ps1', '.env'}

DOCUMENT_EXTENSIONS = {'.pdf', '.docx', '.doc', '.pptx', '.rtf', '.odt', '.epub'}

SPREADSHEET_EXTENSIONS = {'.csv', '.tsv', '.xlsx', '.xls'}

IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.tif',
                    '.webp'}

SUPPORTED_EXTENSIONS = (TEXT_EXTENSIONS | DOCUMENT_EXTENSIONS |
                        SPREADSHEET_EXTENSIONS | IMAGE_EXTENSIONS |
                        {'.json'})


def _check_available(module_name: str) -> bool:
    """Check if a Python module is importable."""
    try:
        __import__(module_name)
        return True
    except ImportError:
        return False


# ── Extraction functions ─────────────────────────────────────────────────

def _extract_text_file(filepath: str) -> tuple[str, dict[str, Any]]:
    """Extract text from plain text files."""
    with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
        content = f.read()
    return content, {"extractor": "plaintext", "encoding": "utf-8"}


def _extract_json_file(filepath: str) -> tuple[str, dict[str, Any]]:
    """Extract text from JSON files."""
    with open(filepath, 'r', encoding='utf-8') as f:
        data = json.load(f)
    content = json.dumps(data, indent=2, ensure_ascii=False)
    return content, {"extractor": "json", "keys": list(data.keys()) if isinstance(data, dict) else []}


def _extract_pdf(filepath: str) -> tuple[str, dict[str, Any]]:
    """Extract text from PDF files using available libraries."""
    # Try pypdf
    try:
        from pypdf import PdfReader
        reader = PdfReader(filepath)
        pages = []
        for i, page in enumerate(reader.pages):
            text = page.extract_text() or ""
            if text.strip():
                pages.append(text)
        if pages:
            return "\n\n".join(pages), {
                "extractor": "pypdf",
                "page_count": len(reader.pages),
                "pages_with_text": len(pages),
            }
    except ImportError:
        pass
    except Exception as e:
        logger.warning("pypdf failed: %s", e)

    # Try pdfplumber
    try:
        import pdfplumber
        with pdfplumber.open(filepath) as pdf:
            pages = []
            for page in pdf.pages:  # type: ignore[assignment]
                text = page.extract_text() or ""
                if text.strip():
                    pages.append(text)
            if pages:
                return "\n\n".join(pages), {
                    "extractor": "pdfplumber",
                    "page_count": len(pdf.pages),
                    "pages_with_text": len(pages),
                }
    except ImportError:
        pass
    except Exception as e:
        logger.warning("pdfplumber failed: %s", e)

    # If no text extracted, try OCR on image-based PDF
    if _check_available("pdf2image") and _check_available("pytesseract"):
        return _ocr_pdf(filepath)

    return "", {"extractor": "none", "error": "No PDF library available. Install pypdf: pip install pypdf"}


def _ocr_pdf(filepath: str) -> tuple[str, dict[str, Any]]:
    """OCR an image-based PDF using pdf2image + pytesseract."""
    try:
        from pdf2image import convert_from_path
        import pytesseract

        images = convert_from_path(filepath, dpi=200)
        pages = []
        for img in images:
            text = pytesseract.image_to_string(img)
            if text.strip():
                pages.append(text)

        return "\n\n".join(pages), {
            "extractor": "ocr_pdf",
            "page_count": len(images),
            "pages_with_text": len(pages),
        }
    except Exception as e:
        logger.warning("OCR PDF failed: %s", e)
        return "", {"extractor": "ocr_pdf", "error": str(e)}


def _extract_docx(filepath: str) -> tuple[str, dict[str, Any]]:
    """Extract text from Word .docx files."""
    try:
        from docx import Document
        doc = Document(filepath)
        paragraphs = []
        for para in doc.paragraphs:
            if para.text.strip():
                paragraphs.append(para.text)

        # Also extract tables
        tables_text = []
        for table in doc.tables:
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells]
                tables_text.append(" | ".join(cells))

        content = "\n\n".join(paragraphs)
        if tables_text:
            content += "\n\n--- Tables ---\n" + "\n".join(tables_text)

        return content, {
            "extractor": "python-docx",
            "paragraphs": len(paragraphs),
            "tables": len(doc.tables),
        }
    except ImportError:
        return "", {"extractor": "none", "error": "Install python-docx: pip install python-docx"}
    except Exception as e:
        logger.warning("DOCX extraction failed: %s", e)
        return "", {"extractor": "python-docx", "error": str(e)}


def _extract_pptx(filepath: str) -> tuple[str, dict[str, Any]]:
    """Extract text from PowerPoint .pptx files."""
    try:
        from pptx import Presentation
        prs = Presentation(filepath)
        slides_text = []

        for i, slide in enumerate(prs.slides, 1):
            parts = []
            for shape in slide.shapes:
                if shape.has_text_frame:
                    for paragraph in shape.text_frame.paragraphs:
                        text = paragraph.text.strip()
                        if text:
                            parts.append(text)
                if shape.has_table:
                    for row in shape.table.rows:
                        cells = [cell.text.strip() for cell in row.cells]
                        parts.append(" | ".join(cells))
            if parts:
                slides_text.append(f"--- Slide {i} ---\n" + "\n".join(parts))

        return "\n\n".join(slides_text), {
            "extractor": "python-pptx",
            "slide_count": len(prs.slides),
            "slides_with_text": len(slides_text),
        }
    except ImportError:
        return "", {"extractor": "none", "error": "Install python-pptx: pip install python-pptx"}
    except Exception as e:
        logger.warning("PPTX extraction failed: %s", e)
        return "", {"extractor": "python-pptx", "error": str(e)}


def _extract_csv(filepath: str) -> tuple[str, dict[str, Any]]:
    """Extract text from CSV/TSV files."""
    ext = os.path.splitext(filepath)[1].lower()
    delimiter = '\t' if ext == '.tsv' else ','

    rows = []
    with open(filepath, 'r', encoding='utf-8', errors='replace', newline='') as f:
        reader = csv.reader(f, delimiter=delimiter)
        for row in reader:
            rows.append(" | ".join(row))

    return "\n".join(rows), {
        "extractor": "csv",
        "row_count": len(rows),
        "delimiter": "tab" if delimiter == '\t' else "comma",
    }


def _extract_xlsx(filepath: str) -> tuple[str, dict[str, Any]]:
    """Extract text from Excel .xlsx files."""
    try:
        from openpyxl import load_workbook
        wb = load_workbook(filepath, read_only=True, data_only=True)
        sheets_text = []

        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            rows = []
            for row in ws.iter_rows(values_only=True):
                cells = [str(c) if c is not None else "" for c in row]
                if any(c.strip() for c in cells):
                    rows.append(" | ".join(cells))
            if rows:
                sheets_text.append(f"--- {sheet_name} ---\n" + "\n".join(rows))

        wb.close()
        return "\n\n".join(sheets_text), {
            "extractor": "openpyxl",
            "sheet_count": len(wb.sheetnames),
        }
    except ImportError:
        return "", {"extractor": "none", "error": "Install openpyxl: pip install openpyxl"}
    except Exception as e:
        logger.warning("XLSX extraction failed: %s", e)
        return "", {"extractor": "openpyxl", "error": str(e)}


def _extract_image(filepath: str) -> tuple[str, dict[str, Any]]:
    """Extract text from images using OCR (pytesseract)."""
    try:
        from PIL import Image
        import pytesseract

        img = Image.open(filepath)
        text = pytesseract.image_to_string(img)

        return text.strip(), {
            "extractor": "tesseract_ocr",
            "image_size": f"{img.width}x{img.height}",
            "image_mode": img.mode,
        }
    except ImportError as e:
        missing = "pytesseract" if "pytesseract" in str(e) else "Pillow"
        return "", {
            "extractor": "none",
            "error": f"Install {missing}: pip install {missing.lower()}",
        }
    except Exception as e:
        logger.warning("Image OCR failed: %s", e)
        return "", {"extractor": "tesseract_ocr", "error": str(e)}


def _extract_html(filepath: str) -> tuple[str, dict[str, Any]]:
    """Extract text from HTML files."""
    try:
        from bs4 import BeautifulSoup
        with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
            soup = BeautifulSoup(f.read(), 'html.parser')

        # Remove script and style elements
        for tag in soup(['script', 'style', 'nav', 'footer']):
            tag.decompose()

        text = soup.get_text(separator='\n', strip=True)
        return text, {"extractor": "beautifulsoup", "title": soup.title.string if soup.title else ""}
    except ImportError:
        # Fallback: strip tags with regex
        import re
        with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
        text = re.sub(r'<[^>]+>', ' ', content)
        text = re.sub(r'\s+', ' ', text).strip()
        return text, {"extractor": "regex_fallback"}


#: An EPUB whose files unpack to more than this is refused (a zip bomb).
EPUB_MAX_BYTES = 200 * 1024 * 1024


class _EpubText(HTMLParser):
    """The words of one EPUB chapter. Nothing in it runs."""

    _BREAKS = ("p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section")

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):  # noqa: ANN001
        if tag in ("script", "style"):
            self.skip += 1
        elif tag in self._BREAKS:
            self.parts.append("\n")

    def handle_endtag(self, tag):  # noqa: ANN001
        if tag in ("script", "style") and self.skip:
            self.skip -= 1

    def handle_data(self, data):  # noqa: ANN001
        if not self.skip:
            self.parts.append(data)


def _extract_epub(filepath: str) -> tuple[str, dict[str, Any]]:
    """Text of an EPUB book, its chapters in reading order (its spine)."""
    import posixpath
    import re
    import zipfile

    with zipfile.ZipFile(filepath) as book:
        if sum(i.file_size for i in book.infolist()) > EPUB_MAX_BYTES:
            raise ValueError("This book unpacks to more than BuildBot reads.")
        names = set(book.namelist())
        container = book.read("META-INF/container.xml").decode("utf-8", "replace")
        found = re.search(r'full-path="([^"]+)"', container)
        opf_path = found.group(1) if found else ""
        if opf_path not in names:
            raise ValueError("This EPUB does not say where its chapters are.")
        opf = book.read(opf_path).decode("utf-8", "replace")
        base = posixpath.dirname(opf_path)
        items = {}
        for tag in re.findall(r"<item\b[^>]*>", opf):
            ident = re.search(r'\bid="([^"]+)"', tag)
            href = re.search(r'\bhref="([^"]+)"', tag)
            if ident and href:
                items[ident.group(1)] = posixpath.normpath(posixpath.join(base, href.group(1)))
        chapters = []
        for ref in re.findall(r'<itemref\b[^>]*\bidref="([^"]+)"', opf):
            path = items.get(ref)
            if path not in names:
                continue
            parser = _EpubText()
            parser.feed(book.read(path).decode("utf-8", "replace"))
            lines = "".join(parser.parts).splitlines()
            text = "\n".join(" ".join(line.split()) for line in lines)
            text = re.sub(r"\n{3,}", "\n\n", text).strip()
            if text:
                chapters.append(text)
    title = re.search(r"<dc:title[^>]*>([^<]+)</dc:title>", opf)
    return "\n\n".join(chapters), {"extractor": "epub", "chapters": len(chapters),
                                   "title": title.group(1).strip() if title else ""}


def _extract_rtf(filepath: str) -> tuple[str, dict[str, Any]]:
    """Extract text from RTF files."""
    try:
        from striprtf.striprtf import rtf_to_text
        with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
        text = rtf_to_text(content)
        return text, {"extractor": "striprtf"}
    except ImportError:
        # Basic fallback — strip RTF control words
        import re
        with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
        text = re.sub(r'\\[a-z]+\d*\s?', '', content)
        text = re.sub(r'[{}]', '', text)
        return text.strip(), {"extractor": "regex_fallback"}


# ── Main extraction function ─────────────────────────────────────────────

def extract_text(filepath: str) -> tuple[str, dict[str, Any]]:
    """Extract text content from any supported file type.

    Args:
        filepath: Path to the file.

    Returns:
        (text_content, metadata_dict) where metadata includes extractor used,
        page counts, errors, etc.

    Raises:
        FileNotFoundError: If the file doesn't exist.
        ValueError: If the file type is not supported.
    """
    if not os.path.isfile(filepath):
        raise FileNotFoundError(f"File not found: {filepath}")

    ext = os.path.splitext(filepath)[1].lower()
    filename = os.path.basename(filepath)
    file_size = os.path.getsize(filepath)

    base_meta = {
        "filename": filename,
        "extension": ext,
        "file_size": file_size,
    }

    if ext in TEXT_EXTENSIONS:
        text, meta = _extract_text_file(filepath)
    elif ext == '.json':
        text, meta = _extract_json_file(filepath)
    elif ext == '.pdf':
        text, meta = _extract_pdf(filepath)
    elif ext in ('.docx', '.doc'):
        if ext == '.doc':
            # .doc (legacy) — try converting to text
            text, meta = _extract_text_file(filepath)
            meta["warning"] = "Legacy .doc format: text extraction may be incomplete"
        else:
            text, meta = _extract_docx(filepath)
    elif ext == '.pptx':
        text, meta = _extract_pptx(filepath)
    elif ext in ('.csv', '.tsv'):
        text, meta = _extract_csv(filepath)
    elif ext in ('.xlsx', '.xls'):
        text, meta = _extract_xlsx(filepath)
    elif ext in IMAGE_EXTENSIONS:
        text, meta = _extract_image(filepath)
    elif ext == '.html':
        text, meta = _extract_html(filepath)
    elif ext == '.rtf':
        text, meta = _extract_rtf(filepath)
    elif ext == '.epub':
        text, meta = _extract_epub(filepath)
    else:
        raise ValueError(
            f"Unsupported file type: {ext}. "
            f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        )

    base_meta.update(meta)
    base_meta["text_length"] = len(text)
    base_meta["has_content"] = bool(text.strip())

    return text, base_meta
