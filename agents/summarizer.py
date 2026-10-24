"""Summarizer (spec 7.3, feature F16): the AI WORDS the good and bad points that Python has already chosen, and writes one sentence on
why a supplier is recommended. It never chooses, adds, removes or reorders a point.

One call per supplier, with that supplier's chosen points only (its name is not sent, and no other supplier's data is in the call).
Whatever the AI sends back is checked by Python line by line. A line that is empty, too long, or names another supplier is dropped
and the plain template text is shown instead; a reply that is not JSON or has the wrong number of lines is dropped as a whole.
"""
from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from llm import client
from tools import insights

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "summarizer_system.md"
LINE_WORDS_MAX = 25         # the prompt asks for 20; a little slack before a line is replaced by the plain text
REASON_WORDS_MAX = 32       # the prompt asks for 25
LINE_CHARS_MAX = 220
REASON_CHARS_MAX = 260


class SummarizerError(Exception):
    """The wording could not be used. `user_message` is safe to show; the run goes on with the plain template text."""

    def __init__(self, user_message: str, tokens: int | None = None, cost_usd: float | None = None) -> None:
        super().__init__(user_message)
        self.user_message = user_message
        self.tokens, self.cost_usd = tokens, cost_usd          # what the call used, when the reply came but was not usable


@dataclass(frozen=True)
class Wording:
    pros: dict[str, str] = field(default_factory=dict)       # key -> the AI's line
    cons: dict[str, str] = field(default_factory=dict)
    reason: str | None = None
    reason_for: tuple[str, ...] = ()                         # the pro keys the reason was written from
    kept_plain: int = 0                                      # lines that were replaced by the template text
    tokens: int | None = None
    cost_usd: float | None = None

    def as_saved(self) -> dict[str, Any]:
        return {"pros": dict(self.pros), "cons": dict(self.cons), "reason": self.reason, "reason_for": list(self.reason_for)}


def read_prompt(path: Path = PROMPT_PATH, sleep: Callable[[float], None] = time.sleep) -> str:
    for attempt in (1, 2):
        try:
            return path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            raise SummarizerError(f"The wording prompt file is missing ({path.name}).") from None
        except PermissionError:
            if attempt == 2:
                raise SummarizerError(f"The wording prompt file ({path.name}) is locked.") from None
            sleep(3)
    raise AssertionError("unreachable")


def _flat(text: object) -> str:
    return " ".join(str(text or "").split())


def build_messages(chosen: insights.ProsCons, support: dict[str, tuple[str, str]], prompt: str) -> list[dict[str, str]]:
    """`support` maps a point's key to (justification, evidence) for the points that come from a criterion."""
    def item(reason: insights.Reason) -> dict[str, str]:
        justification, evidence = support.get(reason.key, ("", ""))
        entry = {"label": reason.basis, "point": reason.text}
        if justification:
            entry["justification"] = _flat(justification)
        if evidence:
            entry["evidence"] = _flat(evidence)
        return entry
    body = {"pros": [item(r) for r in chosen.pros], "cons": [item(r) for r in chosen.cons]}
    return [{"role": "system", "content": prompt}, {"role": "user", "content": json.dumps(body, ensure_ascii=False, indent=1)}]


def _json_object(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        try:
            value = json.loads(cleaned[start:end + 1]) if 0 <= start < end else None
        except json.JSONDecodeError:
            value = None
    if not isinstance(value, dict):
        raise SummarizerError("The AI's wording was not in the expected form.")
    return value


def names_another(text: str, others: Sequence[str]) -> bool:
    """True when the text names one of the other suppliers (the full name, or its first word when that has 4 or more letters)."""
    low = text.lower()
    for name in others:
        full = name.strip().lower()
        first = full.split()[0] if full.split() else ""
        for variant in {full, first if len(first) >= 4 else ""} - {""}:
            if re.search(rf"(?<![a-z0-9]){re.escape(variant)}(?![a-z0-9])", low):
                return True
    return False


def _usable(line: object, limit_words: int, limit_chars: int, others: Sequence[str]) -> str | None:
    if not isinstance(line, str):
        return None
    text = _flat(line)
    if not text or len(text.split()) > limit_words or len(text) > limit_chars or names_another(text, others):
        return None
    return text


def check_reply(text: str, chosen: insights.ProsCons, others: Sequence[str]) -> Wording:
    """Turn the AI's reply into wording, keeping only what passes the checks. Raises SummarizerError when the reply cannot be used."""
    body = _json_object(text)
    pros, cons = body.get("pros"), body.get("cons")
    if not isinstance(pros, list) or not isinstance(cons, list) or len(pros) != len(chosen.pros) or len(cons) != len(chosen.cons):
        raise SummarizerError("The AI's wording did not match the points that were chosen.")
    kept_plain = 0
    worded: list[dict[str, str]] = [{}, {}]
    for slot, (items, lines) in enumerate(((chosen.pros, pros), (chosen.cons, cons))):
        for reason, line in zip(items, lines):
            good = _usable(line, LINE_WORDS_MAX, LINE_CHARS_MAX, others)
            if good is None or not reason.key:
                kept_plain += 1
            else:
                worded[slot][reason.key] = good
    sentence = _usable(body.get("reason"), REASON_WORDS_MAX, REASON_CHARS_MAX, others) if chosen.pros else None
    return Wording(worded[0], worded[1], sentence, tuple(r.key for r in chosen.pros) if sentence else (), kept_plain)


def word_points(
    chosen: insights.ProsCons,
    support: dict[str, tuple[str, str]],
    others: Sequence[str],
    settings: client.LLMSettings,
    *,
    backend: client.Backend | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> Wording:
    """One AI call for one supplier. Raises SummarizerError (unusable reply) or client.LLMError (provider problem)."""
    messages = build_messages(chosen, support, read_prompt(sleep=sleep))
    result = client.call_llm(settings, messages, client.temperature_for("wording"), json_mode=True, backend=backend, sleep=sleep)
    try:
        wording = check_reply(result.text, chosen, others)
    except SummarizerError as exc:
        raise SummarizerError(exc.user_message, result.tokens, result.cost_usd) from None
    return Wording(wording.pros, wording.cons, wording.reason, wording.reason_for, wording.kept_plain, result.tokens, result.cost_usd)
