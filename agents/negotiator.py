"""Negotiator (spec 7.4, feature F20): the AI WORDS the negotiation points that Python has already chosen (tools/insights.py,
negotiation_points). It never chooses, adds, removes or reorders a point.

One call for one supplier. The call carries that supplier's chosen points and the supplier's own facts only: no other supplier's name,
price or mark (procurement confidentiality, spec rule 12). It also writes one short "fit" note: why this supplier suits what the buyer asked for in the rounds of changing priorities. The AI never
chooses the supplier (Python does: the best-ranked supplier that passes every must-have). Whatever the AI sends back is checked by Python line by line. A line that
is empty, too long, or names another supplier, or talks about another bid or offer, is dropped and the plain text is shown instead; a
reply that is not JSON or has the wrong number of lines is dropped as a whole.
"""
from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agents import summarizer
from llm import client
from tools import insights

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "negotiator_system.md"
LINE_WORDS_MAX = 36        # the prompt asks for 30; a little slack before a line is replaced by the plain text
LINE_CHARS_MAX = 280
FIT_WORDS_MAX = 48         # the prompt asks for 40
FIT_CHARS_MAX = 340
OTHER_BID = re.compile(r"\b(another|other|competing|competitor'?s?|rival)\s+(supplier|suppliers|vendor|vendors|bid|bids|offer|offers|"
                       r"quote|quotes|proposal|proposals|bidder|bidders|company|companies)\b|\bcompetitors?\b|\bcheaper elsewhere\b",
                       re.IGNORECASE)


class NegotiatorError(Exception):
    """The wording could not be used. `user_message` is safe to show; the plain text is used instead."""

    def __init__(self, user_message: str, tokens: int | None = None, cost_usd: float | None = None) -> None:
        super().__init__(user_message)
        self.user_message = user_message
        self.tokens, self.cost_usd = tokens, cost_usd


@dataclass(frozen=True)
class Worded:
    lines: dict[str, str] = field(default_factory=dict)       # point key -> the AI's line (only lines that passed the checks)
    fit: str | None = None                                    # the AI's fit note, when it passed the checks
    kept_plain: int = 0
    tokens: int | None = None
    cost_usd: float | None = None


def read_prompt(path: Path = PROMPT_PATH, sleep: Callable[[float], None] = time.sleep) -> str:
    for attempt in (1, 2):
        try:
            return path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            raise NegotiatorError(f"The negotiation prompt file is missing ({path.name}).") from None
        except PermissionError:
            if attempt == 2:
                raise NegotiatorError(f"The negotiation prompt file ({path.name}) is locked.") from None
            sleep(3)
    raise AssertionError("unreachable")


def build_messages(points: Sequence[insights.NegotiationPoint], prompt: str, priorities: Sequence[str] = (),
                   strengths: Sequence[str] = ()) -> list[dict[str, str]]:
    items = []
    for p in points:
        entry = {"topic": p.topic, "point": p.text}
        if p.detail:
            entry["supplier_evidence"] = p.detail
        items.append(entry)
    return [{"role": "system", "content": prompt}, {"role": "user", "content": json.dumps({"buyer_priorities": list(priorities), "supplier_strengths": list(strengths), "points": items},
                                                                    ensure_ascii=False, indent=1)}]


def _usable(line: object, others: Sequence[str]) -> str | None:
    if not isinstance(line, str):
        return None
    text = " ".join(line.split())
    if (not text or len(text.split()) > LINE_WORDS_MAX or len(text) > LINE_CHARS_MAX or summarizer.names_another(text, others)
            or OTHER_BID.search(text)):
        return None
    return text


def check_reply(text: str, points: Sequence[insights.NegotiationPoint], others: Sequence[str]) -> Worded:
    try:
        body = summarizer._json_object(text)
    except summarizer.SummarizerError:
        raise NegotiatorError("The AI's wording was not in the expected form.") from None
    lines = body.get("points")
    if not isinstance(lines, list) or len(lines) != len(points):
        raise NegotiatorError("The AI's wording did not match the points that were chosen.")
    kept: dict[str, str] = {}
    plain = 0
    for point, line in zip(points, lines):
        good = _usable(line, others)
        if good is None:
            plain += 1
        else:
            kept[point.key] = good
    fit = body.get("fit")
    note = None
    if isinstance(fit, str):
        text = " ".join(fit.split())
        if text and len(text.split()) <= FIT_WORDS_MAX and len(text) <= FIT_CHARS_MAX and not summarizer.names_another(text, others) \
                and not OTHER_BID.search(text):
            note = text
    return Worded(kept, note, plain)


def word_points(
    points: Sequence[insights.NegotiationPoint],
    others: Sequence[str],
    settings: client.LLMSettings,
    *,
    priorities: Sequence[str] = (),
    strengths: Sequence[str] = (),
    backend: client.Backend | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> Worded:
    """One AI call. Raises NegotiatorError (unusable reply) or client.LLMError (provider problem)."""
    messages = build_messages(points, read_prompt(sleep=sleep), priorities, strengths)
    result = client.call_llm(settings, messages, client.temperature_for("wording"), json_mode=True, backend=backend, sleep=sleep)
    try:
        worded = check_reply(result.text, points, others)
    except NegotiatorError as exc:
        raise NegotiatorError(exc.user_message, result.tokens, result.cost_usd) from None
    return Worded(worded.lines, worded.fit, worded.kept_plain, result.tokens, result.cost_usd)
