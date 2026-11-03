"""The resume as text, read once per run, from whichever form it arrives in:

- pasted text (the `resume_text` textarea);
- a local path to a PDF, Word or text file (a builder's `gravity run --input resume_file=cv.pdf`);
- a signed https link (the platform's `file` input). A v2 agent makes no network calls of its own, so the link
  is read through the manifest's `pages` source (web_page.get), passed in as `read_page`. Not verified on a hosted
  run: Firecrawl parses PDF and Word links; other page apps may return nothing for a file.

Text extraction is plain code (pypdf, python-docx); the model never sees the file. Contact details are redacted
before the text is kept anywhere (state is checkpointed)."""
from __future__ import annotations

import hashlib
import io
import re
from collections.abc import Awaitable, Callable
from pathlib import Path

from security import redact, safe_url

MAX_CHARS = 20_000
MIN_USEFUL_CHARS = 200        # less than this from a PDF usually means a scanned image
MAX_FILE_BYTES = 10 * 1024 * 1024

ReadPage = Callable[[str], Awaitable[dict | None]]


def _clean(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace(" ", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def words_per_line(text: str) -> float:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return sum(len(ln.split()) for ln in lines) / len(lines) if lines else 0.0


def pdf_text(data: bytes) -> str:
    """Plain extraction first; designed (multi-column) PDFs often come out one word per line, which breaks dates
    and headings apart, so those are re-read in layout mode, which rebuilds the lines as they appear on the page."""
    from pypdf import PdfReader  # imported here: only a file upload needs it
    reader = PdfReader(io.BytesIO(data))
    pages = reader.pages[:12]
    plain = "\n".join((page.extract_text() or "") for page in pages)
    if words_per_line(plain) >= 3:
        return plain
    try:
        layout = "\n".join((page.extract_text(extraction_mode="layout") or "") for page in pages)
    except Exception:  # noqa: BLE001 - an older or odd PDF: keep the plain text
        return plain
    return layout if words_per_line(layout) > words_per_line(plain) else plain


def docx_text(data: bytes) -> str:
    import docx  # python-docx
    doc = docx.Document(io.BytesIO(data))
    lines = [p.text for p in doc.paragraphs]
    for table in doc.tables:
        for row in table.rows:
            lines.append(" | ".join(c.text for c in row.cells))
    return "\n".join(lines)


def text_of_bytes(data: bytes, name: str) -> str:
    low = name.lower()
    if low.endswith(".pdf") or data[:5] == b"%PDF-":
        return pdf_text(data)
    if low.endswith(".docx") or data[:2] == b"PK":
        return docx_text(data)
    return data.decode("utf-8", errors="replace")


def _result(text: str, source: str, note: str | None = None) -> dict:
    text = _clean(text)
    if len(text) < MIN_USEFUL_CHARS:
        return {"text": "", "source": source, "hash": None,
                "note": note or ("The resume had almost no readable text (a scanned image?), so it was not used. "
                                 "Paste the text instead for a resume-based match.")}
    text = redact(text)[:MAX_CHARS]
    return {"text": text, "source": source, "hash": hashlib.sha256(text.encode()).hexdigest()[:16], "note": note}


async def load_resume(pasted: str | None, file_ref: object = None, read_page: ReadPage | None = None) -> dict:
    """{"text", "source", "hash", "note"}. Never raises for a bad file: the note says what went wrong."""
    if isinstance(file_ref, list):
        file_ref = file_ref[0] if file_ref else None
    if isinstance(file_ref, str) and file_ref.strip():
        ref = file_ref.strip()
        path = Path(ref).expanduser()
        if not ref.startswith(("http://", "https://")) and path.is_file():
            try:
                if path.stat().st_size > MAX_FILE_BYTES:
                    return _result("", "file", "The resume file is over 10 MB, so it was not read.")
                return _result(text_of_bytes(path.read_bytes(), path.name), f"file {path.suffix.lstrip('.') or 'text'}")
            except Exception as exc:  # noqa: BLE001 - a malformed file is reported, never a crash
                return _result("", "file", f"The resume file could not be read ({type(exc).__name__}).")
        url = safe_url(ref, https_only=True)
        if url and read_page is not None:
            try:
                page = await read_page(url)
            except Exception as exc:  # noqa: BLE001
                page, why = None, str(exc)[:120]
            else:
                why = "no text came back"
            if page and (page.get("text") or "").strip():
                return _result(page["text"], "uploaded file")
            if not (pasted or "").strip():
                return _result("", "uploaded file", f"The uploaded resume could not be read ({why}). Paste the text instead.")
        elif not (pasted or "").strip() and len(ref) >= MIN_USEFUL_CHARS:
            return _result(ref, "pasted text")  # `gravity run --input resume_file=@cv.txt` hands over the text itself
    if (pasted or "").strip():
        return _result(pasted, "pasted text")
    return {"text": "", "source": "none", "hash": None, "note": None}
