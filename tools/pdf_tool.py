"""PDF reading (spec 8 step 3 and 7.1): a supplier proposal in, page texts + fingerprint + chunks out.

PyMuPDF is used when it is installed, pypdf otherwise. Pages are marked "[Page N]" so the evaluator can
quote "(Page 2)". Chunks of about 300 words, each tagged with its supplier and page, are what the Q&A search
(F14) will index. Proposal text is never printed or logged by this module; the only printing is the
developer summary at the bottom (`python -m tools.pdf_tool`), which shows the first 80 characters of each file.
"""
from __future__ import annotations

import hashlib
import importlib
import io
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

CHUNK_WORDS = 300
BACKENDS = ("pymupdf", "pypdf")  # in order of preference

_PASSWORD_MESSAGE = "This PDF is password protected. Please remove the password and upload it again."


class PdfError(ValueError):
    """A PDF that cannot be used. The message is friendly and safe to show to the user."""


@dataclass(frozen=True)
class ExtractedPdf:
    sha256: str                  # fingerprint of the file's bytes (hex)
    pages: tuple[str, ...]       # tidied text of each page, page 1 first
    backend: str                 # "pymupdf" or "pypdf"
    warnings: tuple[str, ...] = ()

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def char_count(self) -> int:
        return sum(len(page) for page in self.pages)

    @property
    def has_text(self) -> bool:
        return self.char_count > 0

    @property
    def text(self) -> str:
        """The whole proposal with a [Page N] marker before each page (what the evaluator reads)."""
        return page_marked_text(self.pages)


@dataclass(frozen=True)
class Chunk:
    supplier: str
    page: int      # the page this text is on (a chunk never crosses a page)
    number: int    # 1, 2, 3 ... through the whole proposal, in reading order
    text: str

    @property
    def word_count(self) -> int:
        return len(self.text.split())


# ---------------------------------------------------------------- text

def tidy_text(text: str) -> str:
    """Make the text the same whichever PDF reader produced it: unify line endings, turn the PDF bullet code
    (0x7F, which pypdf passes through as an invisible control character) into a real bullet, trim spaces at
    both ends of every line, and squeeze runs of blank lines."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "").replace("\f", "\n")
    text = text.replace("\x7f", "\u2022")
    text = "\n".join(line.strip() for line in text.split("\n"))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def page_marked_text(pages: Sequence[str]) -> str:
    """Join page texts with '[Page N]' markers, e.g. '[Page 1]\\n...\\n\\n[Page 2]\\n...'."""
    parts = []
    for number, page in enumerate(pages, start=1):
        parts.append(f"[Page {number}]\n{page}" if page else f"[Page {number}]")
    return "\n\n".join(parts)


# ---------------------------------------------------------------- reading a PDF

def available_backends() -> tuple[str, ...]:
    """The PDF readers that can be imported here, best first."""
    found = []
    for name in BACKENDS:
        try:
            importlib.import_module(name)
        except Exception:  # not installed, or a broken install: either way, not usable
            continue
        found.append(name)
    return tuple(found)


def _read_with_pymupdf(data: bytes) -> list[str]:
    import pymupdf

    document = pymupdf.open(stream=data, filetype="pdf")
    try:
        if document.needs_pass:
            raise PdfError(_PASSWORD_MESSAGE)
        return [page.get_text("text") for page in document]
    finally:
        document.close()


def _read_with_pypdf(data: bytes) -> list[str]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        try:
            unlocked = reader.decrypt("")
        except Exception:
            unlocked = 0
        if not unlocked:
            raise PdfError(_PASSWORD_MESSAGE)
    return [page.extract_text() or "" for page in reader.pages]


_READERS = {"pymupdf": _read_with_pymupdf, "pypdf": _read_with_pypdf}


def extract_pdf(data: bytes, backend: str | None = None) -> ExtractedPdf:
    """Read a PDF given as bytes. `backend` forces one reader (for tests); by default the best one installed.

    Raises PdfError, with a friendly message, for an empty file, a file that is not a PDF, a damaged or
    password-protected PDF, or a PDF with no pages.
    """
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("extract_pdf needs the PDF as bytes.")
    data = bytes(data)
    if not data:
        raise PdfError("The file is empty.")
    if b"%PDF-" not in data[:1024]:
        raise PdfError("This file is not a PDF.")

    installed = available_backends()
    if backend is None:
        if not installed:
            raise PdfError("No PDF reader is installed. Run: pip install -r requirements.txt")
        backend = installed[0]
    elif backend not in _READERS:
        raise ValueError(f"Unknown PDF reader '{backend}'. Choose one of: {', '.join(BACKENDS)}.")

    try:
        raw_pages = _READERS[backend](data)
    except PdfError:
        raise
    except Exception as exc:
        raise PdfError("This PDF could not be read. It may be damaged.") from exc
    if not raw_pages:
        raise PdfError("This PDF has no pages.")

    pages = tuple(tidy_text(page) for page in raw_pages)
    warnings = [f"Page {number} has no readable text." for number, page in enumerate(pages, start=1) if not page]
    if not any(pages):
        warnings.append("No readable text was found in this PDF. It may be a scanned image.")
    return ExtractedPdf(
        sha256=hashlib.sha256(data).hexdigest(),
        pages=pages,
        backend=backend,
        warnings=tuple(warnings),
    )


def extract_pdf_file(path: str | Path, backend: str | None = None) -> ExtractedPdf:
    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise PdfError(f"The file '{path.name}' could not be opened.") from exc
    return extract_pdf(data, backend=backend)


# ---------------------------------------------------------------- chunks

def _chunk_page(page: str, target_words: int) -> list[str]:
    """Split one page at line breaks into pieces of at most `target_words` words. A single line longer than
    that is split by words."""
    chunks: list[str] = []
    current: list[str] = []
    count = 0

    def flush() -> None:
        nonlocal current, count
        text = "\n".join(current).strip()
        if text:
            chunks.append(text)
        current, count = [], 0

    for line in page.split("\n"):
        words = line.split()
        if len(words) > target_words:
            flush()
            chunks.extend(" ".join(words[i : i + target_words]) for i in range(0, len(words), target_words))
            continue
        if count + len(words) > target_words:
            flush()
        current.append(line)
        count += len(words)
    flush()
    return chunks


def build_chunks(pages: Sequence[str], supplier: str, target_words: int = CHUNK_WORDS) -> tuple[Chunk, ...]:
    """Chunks of about `target_words` words, each tagged with the supplier and its page. Chunks follow the
    page and line breaks, never cross a page, and keep their reading order, so a citation like
    '(Apex Systems, page 3)' is always exact."""
    if target_words < 1:
        raise ValueError("target_words must be at least 1.")
    chunks: list[Chunk] = []
    for page_number, page in enumerate(pages, start=1):
        for text in _chunk_page(page, target_words):
            chunks.append(Chunk(supplier=supplier, page=page_number, number=len(chunks) + 1, text=text))
    return tuple(chunks)


# ---------------------------------------------------------------- developer summary

def _main(argv: list[str]) -> int:
    """`python -m tools.pdf_tool [folder]`: one line per PDF (pages, characters, fingerprint, first 80 characters)."""
    try:
        sys.stdout.reconfigure(errors="replace")  # a Windows console may not show every character
    except (AttributeError, ValueError):
        pass
    folder = Path(argv[1]) if len(argv) > 1 else Path(__file__).resolve().parent.parent / "data" / "proposals"
    files = sorted(folder.glob("*.pdf"))
    if not files:
        print(f"No PDF files found in {folder}")
        return 1
    print(f"PDF reader: {(available_backends() or ('none',))[0]}")
    print(f"{'File':<24}{'Pages':>6}{'Characters':>12}  {'SHA-256':<13}First 80 characters")
    for path in files:
        try:
            pdf = extract_pdf_file(path)
        except PdfError as exc:
            print(f"{path.name:<24}  ERROR: {exc}")
            continue
        first = re.sub(r"\s+", " ", pdf.pages[0]).strip()[:80]
        print(f"{path.name:<24}{pdf.page_count:>6}{pdf.char_count:>12}  {pdf.sha256[:12]:<13}{first}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
