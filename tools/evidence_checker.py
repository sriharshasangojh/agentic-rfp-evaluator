"""Evidence checker (spec 9.2): is each quote the AI gave really in the proposal?

Pure Python, no AI. A quote is FOUND when, after both sides are normalised (lower case, one space between words, the same quote
marks and dashes), it is a piece of the proposal text, or when a close match scores 90 or more (`rapidfuzz.fuzz.partial_ratio`).
"Not stated in the proposal" (or no quote at all) is NOT APPLICABLE (None). A quote that is not found never changes a score: it is
flagged on the scorecard and listed on the Re-evaluation to-do list.

`rapidfuzz` is used when it is installed (it is in requirements.txt). Without it a slower built-in comparison with the same
threshold is used, so the checker still works (and can be tested) on a machine that does not have the package.
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from difflib import SequenceMatcher

try:                                   # the package named in the spec
    from rapidfuzz import fuzz as _fuzz
except ImportError:                    # pragma: no cover - depends on the machine
    _fuzz = None

THRESHOLD = 90                         # partial_ratio needed when the exact text is not there
NOT_STATED = "not stated in the proposal"
MIN_WORDS = 3                          # a piece of a quote shorter than this is not checked on its own
_PAGE_TAIL = re.compile(r"\s*[\(\[]\s*(?:pages?|pp?\.?)\s*(\d+)(?:\s*[-–,]\s*\d+)*\s*[\)\]]\s*[.;,]?\s*$", re.IGNORECASE)
_ELLIPSIS = re.compile(r"\s*(?:\.{3,}|…|\[\s*\.{2,}\s*\]|\[\s*…\s*\])\s*")
_QUOTES = str.maketrans({"“": '"', "”": '"', "„": '"', "‘": "'", "’": "'", "‚": "'", "`": "'", "´": "'",
                         "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "―": "-", "−": "-", "­": "", " ": " ",
                         "•": " ", "▪": " ", "●": " "})


def normalise(text: str) -> str:
    """Lower case, the same quote marks and dashes everywhere, one space between words."""
    return " ".join(str(text).translate(_QUOTES).lower().split())


def split_quote(evidence: str) -> tuple[list[str], int | None]:
    """The quote without its trailing "(Page N)", cut at "..." into the pieces that are checked, and the page number if one was given."""
    text = str(evidence or "").strip()
    page = None
    match = _PAGE_TAIL.search(text)
    if match:
        page = int(match.group(1))
        text = text[:match.start()]
    text = text.strip().strip("\"'“”‘’ ")
    pieces = [normalise(p).strip(" \"'.,;:") for p in _ELLIPSIS.split(text)]
    return [p for p in pieces if p], page


def is_not_applicable(evidence: str) -> bool:
    """No quote at all, or the AI said the proposal does not state it."""
    flat = normalise(evidence).strip(" \"'.")
    return not flat or flat.startswith(NOT_STATED)


def _partial_ratio(needle: str, haystack: str) -> float:
    if _fuzz is not None:
        return float(_fuzz.partial_ratio(needle, haystack))
    n = len(needle)                                         # built-in version: best window of the same length
    if n == 0 or not haystack:
        return 0.0
    if n >= len(haystack):
        return 100 * SequenceMatcher(None, needle, haystack, autojunk=False).ratio()
    step = max(1, n // 8)
    best = 0.0
    for start in range(0, len(haystack) - n + step, step):
        window = haystack[start:start + n]
        matcher = SequenceMatcher(None, needle, window, autojunk=False)
        if matcher.real_quick_ratio() * 100 < best or matcher.quick_ratio() * 100 < best:
            continue
        best = max(best, 100 * matcher.ratio())
        if best >= 100:
            break
    return best


def _piece_found(piece: str, pages: Sequence[str], first: str | None) -> bool:
    texts = ([first] if first else []) + [p for p in pages if p is not first]
    if any(piece in text for text in texts):
        return True
    if len(piece.split()) < MIN_WORDS:
        return False
    return any(_partial_ratio(piece, text) >= THRESHOLD for text in texts)


def evidence_found(evidence: str, pages: Sequence[str]) -> bool | None:
    """True / False for a quote, None when there is nothing to check. `pages` are the page texts of ONE proposal, page 1 first.
    The page the quote names is looked at first; a quote that is on another page still counts as found (the words are in the proposal).
    A quote with "..." in it is found when every piece of it is."""
    if is_not_applicable(evidence):
        return None
    pieces, page = split_quote(evidence)
    if not pieces:
        return None
    normalised = [normalise(p) for p in pages]
    first = normalised[page - 1] if page is not None and 1 <= page <= len(normalised) else None
    # a piece is looked for as a whole first; the rest of the pages are searched the same way
    return all(_piece_found(piece, normalised, first) for piece in pieces)
