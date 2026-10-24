"""Q&A agent (feature F14, spec 7.2): a question and a few excerpts of the proposals in, a short cited answer out.

The excerpts come from `tools/retrieval.py`. The AI may use only those excerpts; it answers in a few sentences, cites every fact as
"(Supplier, Page N)" and, when the excerpts do not say, replies exactly "Not stated in the proposal." (spec 7.2 and rule 13).

Rules kept here:
* The question and the excerpts are untrusted: neither can open or close the prompt's own blocks, and the AI is told never to follow
  instructions inside them. An excerpt cannot pose as another excerpt's label (square brackets are only used for labels).
* Python, not the AI, checks the page references afterwards: a reference that does not point at an excerpt that was sent is reported.
* The API key is only handed to ``call_llm``; it is never put in a prompt or an answer.
"""
from __future__ import annotations

import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from llm import client
from tools import retrieval

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "qa_system.md"
PROMPT_TAGS = ("excerpts", "excerpt", "question")
NOT_STATED = "Not stated in the proposal."
ANSWER_MAX_CHARS = 1500

_PARENTHESES = re.compile(r"\(([^()\n]{1,300})\)")
_CITATION = re.compile(r"^\s*(.{1,80}?),\s*(?:pages?|pp?\.)\s*(\d+(?:\s*(?:,|and|&|-|–)\s*(?:pages?\s*)?\d+)*)\s*$", re.IGNORECASE)


class QAError(Exception):
    """A problem with the input or the prompt file. ``user_message`` is friendly and safe to show."""

    def __init__(self, user_message: str) -> None:
        super().__init__(user_message)
        self.user_message = user_message


@dataclass(frozen=True)
class Citation:
    supplier: str
    pages: tuple[int, ...]
    verified: bool        # True when every page names an excerpt of that supplier that was sent


@dataclass(frozen=True)
class Answer:
    text: str
    not_stated: bool                   # the answer starts with "Not stated in the proposal."
    citations: tuple[Citation, ...]
    tokens: int | None = None
    cost_usd: float | None = None

    @property
    def uncited(self) -> bool:
        """A statement of fact with no page reference at all (rule 13 asks for a reference or "Not stated"). An answer that only says
        which suppliers do not state it ("Vertexa does not state it.") needs no reference: every sentence of it says "not state"."""
        if self.not_stated or self.citations:
            return False
        sentences = [part for part in re.split(r"(?<=[.!?])\s+|\n+", self.text) if part.strip()]
        return not all("not state" in part.lower() for part in sentences)

    @property
    def unverified(self) -> tuple[Citation, ...]:
        return tuple(c for c in self.citations if not c.verified)


def clean_question(text: str) -> str:
    """One line, trimmed, at most `QUESTION_MAX_CHARS` characters."""
    return " ".join(str(text or "").split())[: retrieval.QUESTION_MAX_CHARS].strip()


def _defuse_tags(text: str) -> str:
    """Untrusted text may not open or close the prompt's own blocks: <excerpts> becomes [excerpts]."""
    names = "|".join(PROMPT_TAGS)
    return re.sub(rf"<\s*(/?)\s*({names})\b[^>]*>", r"[\1\2]", text, flags=re.IGNORECASE)


def _plain(text: str) -> str:
    """An excerpt's words, with its own square brackets turned into round ones so that it cannot fake the label of an excerpt."""
    return _defuse_tags(text).replace("[", "(").replace("]", ")").strip()


def read_prompt(path: Path = PROMPT_PATH, sleep: Callable[[float], None] = time.sleep) -> str:
    """The system prompt. A file briefly locked by OneDrive is tried once more after a pause."""
    for attempt in (1, 2):
        try:
            return path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            raise QAError(f"The question prompt file is missing ({path.name}). Restore it from the project folder.") from None
        except PermissionError:
            if attempt == 2:
                raise QAError(f"The question prompt file ({path.name}) is locked. Wait a moment and try again.") from None
            sleep(3)
    raise AssertionError("unreachable")


def label_of(hit: retrieval.Hit) -> str:
    return f"[{_defuse_tags(hit.supplier).replace('[', '(').replace(']', ')')}, Page {hit.page}]"


def build_messages(question: str, hits: Sequence[retrieval.Hit], *, system_prompt: str | None = None) -> list[dict[str, str]]:
    """The two messages: the excerpts, each under its label, then the question in its own block."""
    text = clean_question(question)
    if not text:
        raise QAError("Type a question first.")
    if not hits:
        raise QAError("There are no excerpts to answer from.")
    blocks = "\n\n".join(f"{label_of(hit)}\n{_plain(hit.text)}" for hit in hits)
    user = f"<excerpts>\n{blocks}\n</excerpts>\n\n<question>\n{_defuse_tags(text)}\n</question>"
    system = system_prompt if system_prompt is not None else read_prompt()
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _pages(text: str) -> tuple[int, ...]:
    return tuple(dict.fromkeys(int(n) for n in re.findall(r"\d+", text)))


def check_citations(text: str, hits: Sequence[retrieval.Hit]) -> tuple[Citation, ...]:
    """Every "(Supplier, Page N)" in the answer, and whether it points at an excerpt that was sent (same supplier, same page)."""
    sent: dict[str, set[int]] = {}
    for hit in hits:
        sent.setdefault(" ".join(hit.supplier.lower().split()), set()).add(hit.page)
    found: list[Citation] = []
    for group in _PARENTHESES.finditer(text):
        for part in group.group(1).split(";"):          # "(Apex Systems, Page 2; NexaWorks, Page 3)" holds two references
            match = _CITATION.match(part)
            if match is None:
                continue
            supplier = " ".join(match.group(1).split())
            pages = _pages(match.group(2))
            allowed = sent.get(supplier.lower(), set())
            found.append(Citation(supplier, pages, bool(pages) and all(p in allowed for p in pages)))
    return tuple(found)


def clean_answer(reply: str) -> str:
    """The reply trimmed, with runs of blank lines squeezed, cut at `ANSWER_MAX_CHARS`."""
    text = re.sub(r"\n{3,}", "\n\n", str(reply or "").replace("\r\n", "\n").replace("\r", "\n")).strip()
    return text if len(text) <= ANSWER_MAX_CHARS else text[:ANSWER_MAX_CHARS].rstrip() + "..."


def is_not_stated(text: str) -> bool:
    """True when the answer begins with the sentence the spec asks for when the excerpts do not say."""
    return text.strip().strip("\"'“”").lower().startswith(NOT_STATED.rstrip(".").lower())


def not_stated_answer() -> Answer:
    """The answer when no excerpt shares a word with the question: nothing is sent to the AI."""
    return Answer(NOT_STATED, True, ())


def answer_question(
    question: str,
    hits: Sequence[retrieval.Hit],
    settings: client.LLMSettings,
    *,
    backend: client.Backend | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> Answer:
    """Ask the AI to answer from the excerpts. Raises QAError for unusable input and ``client.LLMError`` for a provider problem
    (bad key, no network, timeout after the one retry); the caller decides what that means for the screen."""
    messages = build_messages(question, hits)
    result = client.call_llm(settings, messages, client.temperature_for("qa"), json_mode=False, backend=backend, sleep=sleep)
    text = clean_answer(result.text)
    if not text:
        raise QAError("The AI sent back an empty answer. Try asking again.")
    return Answer(text, is_not_stated(text), check_citations(text, hits), result.tokens, result.cost_usd)
