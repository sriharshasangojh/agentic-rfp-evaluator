"""Tests for tools/pdf_tool.py (spec 8 step 3, 7.1 and F06). No API key or network needed.

Reader tests run once for each PDF reader installed (PyMuPDF and/or pypdf) and are skipped if neither is.
The chunking tests need no PDF reader at all.
"""
from __future__ import annotations

import ast
import hashlib
import io
import re
from pathlib import Path

import pytest

from tools import pdf_tool
from tools.pdf_tool import (
    CHUNK_WORDS,
    PdfError,
    available_backends,
    build_chunks,
    extract_pdf,
    extract_pdf_file,
    page_marked_text,
    tidy_text,
)

PROPOSALS = Path(__file__).resolve().parent.parent / "data" / "proposals"

# file -> (supplier name, grand total as written in the PDF, pages, first 12 characters of the SHA-256)
SEVEN = {
    "apex_systems.pdf": ("Apex Systems", "73,98,600", 6, "ad179333f0e1"),
    "brightpath_tech.pdf": ("BrightPath Tech", "48,97,000", 5, "2ffcfb6da4a3"),
    "horizon_datacom.pdf": ("Horizon Datacom", "59,47,200", 6, "71efac1f2eb6"),
    "nexaworks.pdf": ("NexaWorks", "59,35,400", 6, "688c6b81c042"),
    "orbit_digital.pdf": ("Orbit Digital", "57,34,800", 6, "5c59b373a91c"),
    "vertexa_solutions.pdf": ("Vertexa Solutions", None, 5, "08bcfcc73a3e"),
    "zenora_systems.pdf": ("Zenora Systems", "56,16,800", 6, "fca2421981e1"),
}


@pytest.fixture(params=available_backends())
def backend(request):
    """Each installed PDF reader in turn (no reader installed: the test is skipped)."""
    return request.param


def flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def make_pdf(pages) -> bytes:
    """A small valid PDF with one text line per '\\n'. A page given as None or '' has no text at all."""
    def esc(line: str) -> bytes:
        return line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)").encode("latin-1")

    count = len(pages)
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(count))
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {count} >>".encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for i, text in enumerate(pages):
        lines = text.split("\n") if text else []
        stream = b"BT /F1 12 Tf 14 TL 72 720 Td " + b" ".join(b"(" + esc(line) + b") Tj T*" for line in lines) + b" ET" if lines else b""
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {5 + 2 * i} 0 R "
            f"/Resources << /Font << /F1 3 0 R >> >> >>".encode()
        )
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


# ---------------------------------------------------------------- reading a PDF

def test_pages_come_back_in_order_with_markers(backend):
    pdf = extract_pdf(make_pdf(["Alpha one\nAlpha two", "Bravo", "Charlie"]), backend)
    assert (pdf.page_count, pdf.backend) == (3, backend)
    assert [page.split() for page in pdf.pages] == [["Alpha", "one", "Alpha", "two"], ["Bravo"], ["Charlie"]]
    text = pdf.text
    order = ["[Page 1]", "Alpha one", "[Page 2]", "Bravo", "[Page 3]", "Charlie"]
    assert [text.index(item) for item in order] == sorted(text.index(item) for item in order)
    assert pdf.warnings == ()


def test_a_page_with_no_text_keeps_its_number_and_gets_a_warning(backend):
    pdf = extract_pdf(make_pdf(["Alpha", None, "Charlie"]), backend)
    assert pdf.page_count == 3 and pdf.pages[1] == ""
    assert "[Page 2]\n\n[Page 3]" in pdf.text
    assert pdf.has_text
    assert pdf.warnings == ("Page 2 has no readable text.",)


def test_a_pdf_with_no_text_at_all_is_flagged(backend):
    pdf = extract_pdf(make_pdf([None, ""]), backend)
    assert (pdf.page_count, pdf.char_count, pdf.has_text) == (2, 0, False)
    assert pdf.warnings[-1] == "No readable text was found in this PDF. It may be a scanned image."


def test_the_fingerprint_is_the_sha256_of_the_file_bytes(backend):
    data = make_pdf(["Alpha"])
    pdf = extract_pdf(data, backend)
    assert pdf.sha256 == hashlib.sha256(data).hexdigest() and len(pdf.sha256) == 64
    assert extract_pdf(data, backend).sha256 == pdf.sha256
    assert extract_pdf(make_pdf(["Alpha!"]), backend).sha256 != pdf.sha256


def test_the_fingerprint_does_not_depend_on_the_reader():
    data = make_pdf(["Alpha"])
    assert len({extract_pdf(data, name).sha256 for name in available_backends()}) <= 1


def test_special_characters_in_text_survive(backend):
    pdf = extract_pdf(make_pdf(["Price (incl. GST) Rs 59,35,400 - 18% \\ done"]), backend)
    assert flat(pdf.text).endswith("Price (incl. GST) Rs 59,35,400 - 18% \\ done")


def test_a_bytearray_is_accepted(backend):
    assert extract_pdf(bytearray(make_pdf(["Alpha"])), backend).page_count == 1


@pytest.mark.parametrize(
    "data, message",
    [(b"", "empty"), (b"hello there", "not a PDF"), (b"<html></html>", "not a PDF")],
)
def test_files_that_are_not_pdfs_are_refused_with_a_friendly_message(data, message):
    with pytest.raises(PdfError, match=message):
        extract_pdf(data)


def test_a_damaged_pdf_is_refused(backend):
    with pytest.raises(PdfError):
        extract_pdf(b"%PDF-1.4\nthis is not really a pdf at all\n", backend)


def test_a_password_protected_pdf_is_refused_with_a_friendly_message():
    pypdf = pytest.importorskip("pypdf")
    writer = pypdf.PdfWriter()
    writer.append(pypdf.PdfReader(io.BytesIO(make_pdf(["Secret"]))))
    try:
        writer.encrypt("a-password", algorithm="RC4-128")
    except Exception as exc:  # this pypdf cannot encrypt without an extra library
        pytest.skip(f"cannot build an encrypted test PDF here: {type(exc).__name__}")
    buffer = io.BytesIO()
    writer.write(buffer)
    for name in available_backends():
        with pytest.raises(PdfError, match="password"):
            extract_pdf(buffer.getvalue(), name)


def test_programming_mistakes_are_not_hidden():
    with pytest.raises(TypeError):
        extract_pdf("not bytes")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="Unknown PDF reader"):
        extract_pdf(b"%PDF-1.4\n", backend="adobe")


def test_with_no_reader_installed_the_message_says_what_to_do(monkeypatch):
    monkeypatch.setattr(pdf_tool, "available_backends", lambda: ())
    with pytest.raises(PdfError, match="pip install -r requirements.txt"):
        extract_pdf(b"%PDF-1.4\n")


def test_a_missing_file_gives_a_friendly_message(tmp_path):
    with pytest.raises(PdfError, match=r"'nope\.pdf' could not be opened"):
        extract_pdf_file(tmp_path / "nope.pdf")


# ---------------------------------------------------------------- the 7 real proposals

def test_all_seven_proposals_are_in_the_project():
    assert sorted(path.name for path in PROPOSALS.glob("*.pdf")) == sorted(SEVEN)


@pytest.mark.parametrize("filename", sorted(SEVEN))
def test_each_real_proposal_reads_correctly(filename, backend):
    supplier, total, pages, fingerprint = SEVEN[filename]
    pdf = extract_pdf_file(PROPOSALS / filename, backend)
    assert pdf.page_count == pages
    assert pdf.has_text and pdf.warnings == ()
    assert pdf.sha256[:12] == fingerprint
    text = flat(pdf.text)
    assert len(re.findall(r"\[Page \d+\]", text)) == pages
    assert supplier in text
    if total:
        assert total in text


@pytest.mark.parametrize("filename", sorted(SEVEN))
def test_each_real_proposal_splits_into_exact_page_tagged_chunks(filename, backend):
    supplier = SEVEN[filename][0]
    pdf = extract_pdf_file(PROPOSALS / filename, backend)
    chunks = build_chunks(pdf.pages, supplier)
    assert chunks and all(c.supplier == supplier for c in chunks)
    assert [c.number for c in chunks] == list(range(1, len(chunks) + 1))
    assert all(1 <= c.page <= pdf.page_count for c in chunks)
    assert [c.page for c in chunks] == sorted(c.page for c in chunks)
    assert max(c.word_count for c in chunks) <= CHUNK_WORDS
    assert sum(c.word_count for c in chunks) == sum(len(p.split()) for p in pdf.pages)  # nothing lost or repeated


@pytest.mark.parametrize("filename", sorted(SEVEN))
def test_real_proposal_text_is_clean(filename, backend):
    """No stray control characters and no spaces at the edges of lines, whichever reader is used."""
    text = extract_pdf_file(PROPOSALS / filename, backend).text
    assert not re.search(r"[\x00-\x08\x0b-\x1f\x7f]", text)
    assert not re.search(r"(?m)^ | $", text)


def test_bullets_come_out_as_real_bullets(backend):
    """The PDFs store bullets as code 0x7F; pypdf used to pass that through as an invisible control character."""
    text = extract_pdf_file(PROPOSALS / "brightpath_tech.pdf", backend).text
    assert "\u2022 Prices are for delivery to Meridian's office in Hyderabad." in text
    assert text.count("\u2022") == 3


@pytest.mark.skipif(len(available_backends()) < 2, reason="needs both PyMuPDF and pypdf installed")
@pytest.mark.parametrize("filename", sorted(SEVEN))
def test_both_readers_give_identical_text(filename):
    """The same PDF must read the same on every machine, so the evaluation does not depend on which reader is installed."""
    pages = [extract_pdf_file(PROPOSALS / filename, name).pages for name in available_backends()]
    assert all(other == pages[0] for other in pages[1:])


def test_the_developer_summary_lists_every_proposal(backend, capsys):
    assert pdf_tool._main(["pdf_tool"]) == 0
    lines = capsys.readouterr().out.strip().split("\n")
    rows = [line for line in lines if line.split()[0].endswith(".pdf")]
    assert [line.split()[0] for line in rows] == sorted(SEVEN)
    for line in rows:
        name, pages, chars, digest = line.split()[:4]
        _supplier, _total, expected_pages, expected_digest = SEVEN[name]
        assert int(pages) == expected_pages
        assert int(chars) > 1000
        assert digest == expected_digest
        assert len(line) <= 140   # only the first 80 characters of text are ever shown


def test_the_developer_summary_copes_with_an_empty_folder(tmp_path, capsys):
    assert pdf_tool._main(["pdf_tool", str(tmp_path)]) == 1
    assert "No PDF files found" in capsys.readouterr().out


# ---------------------------------------------------------------- text helpers

@pytest.mark.parametrize(
    "raw, tidy",
    [
        ("a\r\nb\rc", "a\nb\nc"),
        ("line one   \nline two\t", "line one\nline two"),
        ("a\n\nb", "a\n\nb"),           # one blank line is kept
        ("a\n\n\nb", "a\n\nb"),         # two blank lines become one (the smallest run that is squeezed)
        ("a\n\n\n\n\nb", "a\n\nb"),
        ("\n\n  start\nend  \n\n", "start\nend"),
        ("page\x00 text\fnext", "page text\nnext"),
        ("Notes\n\x7f Prices\n\x7f Items", "Notes\n\u2022 Prices\n\u2022 Items"),   # PDF bullet code 0x7F
        ("  indented\n\t tabbed  ", "indented\ntabbed"),                               # spaces trimmed both ends
        ("", ""),
    ],
)
def test_tidy_text(raw, tidy):
    assert tidy_text(raw) == tidy
    assert tidy_text(tidy) == tidy   # tidying twice changes nothing


def test_page_markers_have_the_exact_format():
    assert page_marked_text(["A", "", "B"]) == "[Page 1]\nA\n\n[Page 2]\n\n[Page 3]\nB"
    assert page_marked_text([]) == ""


# ---------------------------------------------------------------- chunks (no PDF reader needed)

def words_page(first: int, lines: int, per_line: int = 20) -> str:
    """A page of `lines` lines, each of `per_line` distinct words (w1, w2, ...)."""
    number = first
    out = []
    for _ in range(lines):
        out.append(" ".join(f"w{number + k}" for k in range(per_line)))
        number += per_line
    return "\n".join(out)


def test_a_long_page_is_cut_at_line_breaks_into_pieces_of_at_most_300_words():
    page = words_page(1, 30)   # 600 words in 30 lines
    chunks = build_chunks([page], "Apex Systems")
    assert [c.word_count for c in chunks] == [300, 300]
    assert " ".join(c.text for c in chunks).split() == page.split()   # nothing lost, same order
    assert all(not c.text.startswith("\n") and not c.text.endswith("\n") for c in chunks)


def test_a_short_page_is_one_chunk_with_its_line_breaks_kept():
    page = "Item        Price\nLaptop      Rs 50,000\nTotal       Rs 59,35,400"
    (chunk,) = build_chunks([page], "NexaWorks")
    assert chunk.text == page


def test_chunks_never_cross_a_page_and_carry_supplier_page_and_number():
    pages = [words_page(1, 10), "", words_page(1000, 10)]   # 200 words, empty, 200 words
    chunks = build_chunks(pages, "Orbit Digital")
    assert [(c.supplier, c.page, c.number, c.word_count) for c in chunks] == [
        ("Orbit Digital", 1, 1, 200),
        ("Orbit Digital", 3, 2, 200),
    ]


def test_a_single_huge_line_is_split_by_words():
    line = " ".join(f"w{i}" for i in range(700))
    chunks = build_chunks([line], "Zenora Systems")
    assert [c.word_count for c in chunks] == [300, 300, 100]
    assert " ".join(c.text for c in chunks).split() == line.split()


def test_a_different_chunk_size_can_be_chosen():
    chunks = build_chunks([words_page(1, 10)], "Apex Systems", target_words=50)
    assert max(c.word_count for c in chunks) <= 50
    # 200 words in lines of 20: two lines (40 words) fit, a third would make 60, so 5 chunks of 40.
    assert [c.word_count for c in chunks] == [40] * 5


@pytest.mark.parametrize("bad", [0, -1])
def test_a_chunk_size_below_one_is_refused(bad):
    with pytest.raises(ValueError):
        build_chunks(["text"], "Apex Systems", target_words=bad)


def test_nothing_to_chunk_gives_nothing():
    assert build_chunks([], "Apex Systems") == ()
    assert build_chunks(["", ""], "Apex Systems") == ()


def test_chunking_is_deterministic():
    pages = [words_page(1, 30), words_page(5000, 12)]
    assert build_chunks(pages, "Apex Systems") == build_chunks(pages, "Apex Systems")


# ---------------------------------------------------------------- guardrail

def test_pdf_tool_never_prints_or_logs_proposal_text():
    """Spec 2.2: never log full proposal text. Only the developer summary (`_main`) prints, and only 80 characters."""
    source = Path(pdf_tool.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    allowed = {"__future__", "collections", "dataclasses", "hashlib", "importlib", "io", "pathlib", "re", "sys", "pymupdf", "pypdf"}
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported <= allowed, f"unexpected imports: {sorted(imported - allowed)}"
    main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_main")
    inside_main = {id(n) for n in ast.walk(main)}
    stray = [n.lineno for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "print" and id(n) not in inside_main]
    assert stray == [], f"print() outside _main on lines {stray}"
