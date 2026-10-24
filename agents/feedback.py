"""Feedback interpreter (feature F13, spec 7.5): a typed need in, one proposed action out.

On the Review tab a person who is not satisfied can pick a plain option, or choose "None of the above" and write what they need
in their own words ("I want a laptop that lasts a full day"). This module asks the AI to read that sentence and propose ONE
action. It proposes; it never changes anything: the person sees the one-sentence "reading" and confirms it first, and Python does the
rest (`orchestrator.change_priorities`).

Rules kept here:
* The typed text is untrusted: it cannot open or close the prompt's own blocks, and the AI is told never to follow it.
* The AI may only point at a criterion of this run. Anything else it says is treated as "unclear".
* A reply that is not usable JSON is retried once; after that the answer is "unclear" and the person picks an option instead.
* The API key is only handed to ``call_llm``; it is never put in a prompt, a message or a reading.
"""
from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from llm import client
from tools import insights, leaning as leaning_tool, reweight

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "feedback_system.md"
PROMPT_TAGS = ("criteria", "client_text")
ACTIONS = ("re_evaluate", "reweight", "set_aside", "unclear")
READING_MAX_CHARS = 300
RETRY_INSTRUCTION = "Your previous reply was not valid JSON matching the required shape. Reply again with the JSON object only."
MAX_ECHO_CHARS = 2000


class FeedbackError(Exception):
    """A problem with the input or the prompt file. ``user_message`` is friendly and safe to show."""

    def __init__(self, user_message: str) -> None:
        super().__init__(user_message)
        self.user_message = user_message


class PromptCriterion(Protocol):
    @property
    def criterion_id(self) -> int: ...

    @property
    def name(self) -> str: ...

    @property
    def description(self) -> str: ...


@dataclass(frozen=True)
class Reading:
    """What the AI understood. `readable` is False when two replies in a row could not be read (the action is then "unclear")."""

    action: str                       # one of ACTIONS
    criterion_id: int | None          # set only for "reweight", and only to a criterion of the run
    supplier: str | None
    reading: str                      # one sentence for the person to confirm
    readable: bool = True
    leaning: leaning_tool.Leaning | None = None   # what the person leans towards (F21b); set only when it changes something
    tokens: int | None = None
    cost_usd: float | None = None
    attempts: int = 1


def _defuse_tags(text: str) -> str:
    """Untrusted text may not open or close the prompt's own blocks: <client_text> becomes [client_text]."""
    names = "|".join(PROMPT_TAGS)
    return re.sub(rf"<\s*(/?)\s*({names})\b[^>]*>", r"[\1\2]", text, flags=re.IGNORECASE)


def read_prompt(path: Path = PROMPT_PATH, sleep: Callable[[float], None] = time.sleep) -> str:
    """The system prompt. A file briefly locked by OneDrive is tried once more after a pause."""
    for attempt in (1, 2):
        try:
            return path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            raise FeedbackError(f"The feedback prompt file is missing ({path.name}). Restore it from the project folder.") from None
        except PermissionError:
            if attempt == 2:
                raise FeedbackError(f"The feedback prompt file ({path.name}) is locked. Wait a moment and try again.") from None
            sleep(3)
    raise AssertionError("unreachable")


def build_messages(need: str, criteria: Sequence[PromptCriterion], *, system_prompt: str | None = None) -> list[dict[str, str]]:
    """The two messages for one reading. One line per criterion: criterion_id | name | description."""
    text = reweight.clean_need(need)
    if not text:
        raise FeedbackError("Write what you are not satisfied with.")
    if not criteria:
        raise FeedbackError("There are no criteria to match your words to.")
    lines = "\n".join(f"{c.criterion_id} | {c.name} | {' '.join(c.description.split())}" for c in criteria)
    user = f"<criteria>\n{lines}\n</criteria>\n\n<client_text>\n{_defuse_tags(text)}\n</client_text>"
    system = system_prompt if system_prompt is not None else read_prompt()
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _json_object(reply: str) -> Mapping[str, Any] | None:
    """The reply as one JSON object, or None. A reply wrapped in a code fence or in a sentence is still read."""
    text = reply.strip()
    for candidate in (text, text[text.find("{"):text.rfind("}") + 1] if "{" in text and "}" in text else ""):
        try:
            value = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _whole_number(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def parse_reading(reply: str, criteria: Sequence[PromptCriterion], need: str = "") -> Reading | None:
    """The AI's reply as a Reading, or None when it is not usable (not JSON, or no known action). A "reweight" that does not point at
    a criterion of the run becomes "unclear": the person picks an option instead."""
    data = _json_object(reply)
    if data is None:
        return None
    action = data.get("action")
    if not isinstance(action, str) or action.strip().lower() not in ACTIONS:
        return None
    action = action.strip().lower()
    known = {c.criterion_id: c for c in criteria}
    criterion_id = _whole_number(data.get("criterion_id"))
    if criterion_id not in known:
        criterion_id = None
    supplier = data.get("supplier")
    supplier = " ".join(supplier.split())[:80] if isinstance(supplier, str) and supplier.strip() else None
    said = data.get("reading")
    reading = " ".join(said.split())[:READING_MAX_CHARS].strip() if isinstance(said, str) else ""
    price_id = next((c.criterion_id for c in criteria if insights.is_price_criterion(c.name)), None)
    lean = leaning_tool.clean(data.get("leaning"), list(known), price_id, need)
    if lean.changes_anything and action in ("reweight", "unclear"):     # a wish about price or about how much areas count: Python reads it
        target = max(dict(lean.emphasis), key=lambda i: (dict(lean.emphasis)[i], -i)) if lean.emphasis else (price_id or next(iter(known)))
        names = {c.criterion_id: c.name for c in criteria}
        return Reading("reweight", lean.only_id or target, None, leaning_tool.describe(lean, names), leaning=lean)
    if action == "reweight" and criterion_id is None:
        action, reading = "unclear", ""
    if action == "reweight":
        reading = reading or f"I understood: {known[criterion_id].name} matters more to you."   # type: ignore[index]
    return Reading(action, criterion_id if action in ("reweight", "re_evaluate") else None, supplier, reading)


def _total(values: Sequence[float | int | None]) -> float | int | None:
    known = [v for v in values if v is not None]
    return sum(known) if known else None


def interpret(
    need: str,
    criteria: Sequence[PromptCriterion],
    settings: client.LLMSettings,
    *,
    backend: client.Backend | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> Reading:
    """Ask the AI what a typed need means. Raises FeedbackError for unusable input and ``client.LLMError`` for a provider problem
    (bad key, no network, timeout after the one retry); the caller decides what that means for the screen."""
    messages = build_messages(need, criteria)
    temperature = client.temperature_for("feedback")
    tokens: list[int | None] = []
    costs: list[float | None] = []
    sent: list[dict[str, str]] = list(messages)
    reading: Reading | None = None
    attempts = 0
    for attempts in (1, 2):
        result = client.call_llm(settings, sent, temperature, json_mode=True, backend=backend, sleep=sleep)
        tokens.append(result.tokens)
        costs.append(result.cost_usd)
        reading = parse_reading(result.text, criteria, need)
        if reading is not None:
            break
        sent = [*messages, {"role": "assistant", "content": result.text[:MAX_ECHO_CHARS]},
                {"role": "user", "content": RETRY_INSTRUCTION}]
    unreadable = reading is None
    if reading is None:
        reading = Reading("unclear", None, None, "", readable=False)
    return Reading(reading.action, reading.criterion_id, reading.supplier, reading.reading, not unreadable, reading.leaning,
                   _total(tokens), _total(costs), attempts)   # type: ignore[arg-type]
