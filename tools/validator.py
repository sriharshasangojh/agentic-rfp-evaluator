"""Validator (spec 9.1): turns the evaluation agent's raw reply into a clean, complete scorecard.

The LLM reply is untrusted. Everything wrong with it is repaired by fixed rules, and every repair adds a
plain-English warning, so nothing is changed silently. `max_score` always comes from the criteria passed in
(from the database), never from the reply. No I/O, no LLM, no randomness, no clock.
"""
from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from typing import Annotated, Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

NOT_STATED = "Not stated in the proposal"
NO_RESULT = "No result returned"
NO_JUSTIFICATION = "No justification returned"

# Strict types: no quiet conversions. True is not a number, "16" is not a number, NaN is not a number.
Number = Annotated[int, Field(strict=True)] | Annotated[float, Field(strict=True, allow_inf_nan=False)]
Flag = Annotated[bool, Field(strict=True)]
Text = Annotated[str, Field(strict=True)]


class ExtractedFacts(BaseModel):
    """Facts the evaluator read from the proposal. None means 'not stated'. Rupee amounts are plain numbers."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    laptop_model: Text | None = None
    processor: Text | None = None
    ram_gb: Number | None = None
    ssd_gb: Number | None = None
    battery_hours: Number | None = None
    battery_measured: Flag | None = None
    weight_kg: Number | None = None
    warranty_years: Number | None = None
    warranty_type: Literal["on-site", "carry-in"] | None = None
    repair_time_days: Number | None = None
    replacement_within_24h: Flag | None = None
    delivery_weeks: Number | None = None
    grand_total_rs: Number | None = None
    unit_price_rs: Number | None = None
    price_table_present: Flag | None = None
    bis_named: Flag | None = None
    energy_rating_named: Flag | None = None
    security_certification_named: Flag | None = None
    data_wipe: Literal["certified_standard", "described"] | None = None
    encryption_before_delivery: Flag | None = None
    price_lock_later_orders: Flag | None = None


class ScoredCriterion(BaseModel):
    """One criterion's validated result. `filled_by_validator` marks a criterion the reply did not supply."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    criterion_id: int
    score: Annotated[float, Field(allow_inf_nan=False)]
    justification: str
    evidence: str
    filled_by_validator: bool = False


class EvaluationScorecard(BaseModel):
    """A complete, validated evaluation: exactly one entry per active criterion, in database order."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    criteria: tuple[ScoredCriterion, ...]
    extracted_facts: ExtractedFacts
    risks: tuple[str, ...] = ()
    overall_summary: str = ""


@dataclass(frozen=True)
class ValidationResult:
    """`scorecard` is None when the reply could not be used at all (then `error` says why: retry once)."""

    scorecard: EvaluationScorecard | None
    warnings: tuple[str, ...] = ()
    error: str | None = None

    @property
    def usable(self) -> bool:
        return self.scorecard is not None


class CriterionLike(Protocol):
    """What the validator needs from a criterion. `db.repository.Criterion` fits."""

    @property
    def criterion_id(self) -> int: ...

    @property
    def name(self) -> str: ...

    @property
    def max_score(self) -> float: ...


_FENCE = re.compile(r"^\s*```[A-Za-z0-9_-]*[ \t]*\r?\n(.*?)\r?\n?\s*```\s*$", re.DOTALL)
_SCORE_TEXT = re.compile(r"^\s*([+-]?\d+(?:\.\d+)?)\s*(?:/\s*(\d+(?:\.\d+)?)\s*)?$")


def _exact(value: float | int) -> Fraction:
    """The exact decimal value of a number as written (0.1 stays 1/10)."""
    return Fraction(str(value))


def _show(value: Fraction | float) -> str:
    return f"{float(value):g}"


def _short(value: Any, limit: int = 40) -> str:
    text = repr(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _label(criterion: CriterionLike) -> str:
    return f"Criterion {criterion.criterion_id} ({criterion.name})"


def _criterion_id(value: Any) -> int | None:
    """A usable criterion id, or None. Accepts 2, 2.0 and "2"; refuses True, 2.5, "two" and anything else."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isascii() and stripped.isdigit():
            return int(stripped)
    return None


def _read_score(raw: Any, max_score: Fraction) -> Fraction | None:
    """The score as an exact number, or None if it is not a number. "8" and "8/10" are read; "8/10" means
    8 out of 10, so on a different maximum it is scaled (4/5 on a max of 10 is 8)."""
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, (int, float)):
        return _exact(raw) if math.isfinite(raw) else None
    if isinstance(raw, str):
        match = _SCORE_TEXT.match(raw)
        if not match:
            return None
        top = Fraction(match.group(1))
        if match.group(2) is None:
            return top
        bottom = Fraction(match.group(2))
        return None if bottom == 0 else top / bottom * max_score
    return None


def _round_one_decimal(value: Fraction) -> Fraction:
    """Round half up to 1 decimal (value is never negative here)."""
    return Fraction(math.floor(value * 10 + Fraction(1, 2)), 10)


def _clean_score(raw: Any, criterion: CriterionLike, warnings: list[str]) -> float:
    label = _label(criterion)
    maximum = _exact(criterion.max_score)
    value = _read_score(raw, maximum)
    if value is None:
        warnings.append(f"{label}: score {_short(raw)} is not a number and was set to 0.")
        return 0.0
    if isinstance(raw, str):
        warnings.append(f"{label}: score {_short(raw)} was read as {_show(value)}.")
    if value < 0:
        warnings.append(f"{label}: score {_show(value)} is below 0 and was set to 0.")
        value = Fraction(0)
    elif value > maximum:
        warnings.append(
            f"{label}: score {_show(value)} is above the maximum {_show(maximum)} and was set to {_show(maximum)}."
        )
        value = maximum
    rounded = min(_round_one_decimal(value), maximum)  # never above the maximum, even for an odd maximum
    if rounded != value:
        warnings.append(f"{label}: score {_show(value)} was rounded to {_show(rounded)} (1 decimal).")
    return float(rounded)


def _clean_text(raw: Any, default: str, field: str, criterion: CriterionLike, warnings: list[str]) -> str:
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    warnings.append(f"{_label(criterion)}: {field} was missing and was set to \"{default}\".")
    return default


def _clean_facts(raw: Any, warnings: list[str]) -> ExtractedFacts:
    """Keep every fact of the right type; set the rest to null (not stated). Unknown fact names are ignored."""
    if not isinstance(raw, Mapping):
        warnings.append("extracted_facts was missing or not an object, so every fact was set to null (not stated).")
        return ExtractedFacts()
    kept: dict[str, Any] = {}
    for name in ExtractedFacts.model_fields:
        value = raw.get(name)
        if value is None:
            continue
        try:
            ExtractedFacts.model_validate({name: value})
        except ValidationError:
            warnings.append(f"extracted_facts.{name} had the wrong type ({_short(value)}) and was set to null.")
        else:
            kept[name] = value
    return ExtractedFacts(**kept)


def _clean_risks(raw: Any, warnings: list[str]) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, (list, tuple)):
        warnings.append("risks was not a list and was ignored.")
        return ()
    texts = tuple(item.strip() for item in raw if isinstance(item, str) and item.strip())
    if len(texts) != len(raw):
        warnings.append("Some entries in risks were empty or not text and were dropped.")
    return texts


def _parse_reply(reply: Any) -> tuple[Mapping[str, Any] | None, list[str], str | None]:
    """The reply as a JSON object plus any warnings, or (None, [], reason) when it cannot be used."""
    warnings: list[str] = []
    data = reply
    if isinstance(reply, (bytes, bytearray)):
        try:
            reply = bytes(reply).decode("utf-8")
        except UnicodeDecodeError:
            return None, [], "The reply was not valid JSON."
    if isinstance(reply, str):
        text = reply
        fenced = _FENCE.match(text)
        if fenced:
            text = fenced.group(1)
            warnings.append("The reply was wrapped in a code block; the JSON inside was used.")
        try:
            data = json.loads(text)
        except (ValueError, RecursionError):
            return None, [], "The reply was not valid JSON."
    if not isinstance(data, Mapping):
        return None, [], "The reply was JSON but not a JSON object."
    if not isinstance(data.get("criteria"), (list, tuple)):
        return None, [], "The reply has no 'criteria' list."
    return data, warnings, None


def _filled(criterion: CriterionLike) -> ScoredCriterion:
    return ScoredCriterion(
        criterion_id=criterion.criterion_id,
        score=0.0,
        justification=NO_RESULT,
        evidence=NOT_STATED,
        filled_by_validator=True,
    )


def validate_evaluation(reply: Any, criteria: Sequence[CriterionLike]) -> ValidationResult:
    """Validate one evaluation reply (JSON text, or an already parsed object) against the active criteria.

    Rules (spec 9.1): invalid JSON -> no scorecard (the caller retries once, then uses `failed_evaluation`);
    missing criterion -> added with score 0; unknown id -> dropped; duplicate id -> first kept; score text
    "8" or "8/10" -> read, anything else -> 0; score outside 0..max -> clipped; score rounded to 1 decimal;
    a fact of the wrong type -> null. Every repair adds a warning.
    """
    data, warnings, error = _parse_reply(reply)
    if data is None:
        return ValidationResult(scorecard=None, warnings=(), error=error)

    expected = {c.criterion_id: c for c in criteria}
    chosen: dict[int, Mapping[str, Any]] = {}
    for entry in data["criteria"]:
        if not isinstance(entry, Mapping):
            warnings.append(f"A criteria entry was not an object and was dropped: {_short(entry)}.")
            continue
        raw_id = entry.get("criterion_id")
        criterion_id = _criterion_id(raw_id)
        if criterion_id is None or criterion_id not in expected:
            warnings.append(f"Unknown criterion_id {_short(raw_id)} was dropped.")
        elif criterion_id in chosen:
            warnings.append(f"{_label(expected[criterion_id])} appeared more than once; the first entry was kept.")
        else:
            chosen[criterion_id] = entry

    scored: list[ScoredCriterion] = []
    for criterion in criteria:
        entry = chosen.get(criterion.criterion_id)
        if entry is None:
            warnings.append(f"{_label(criterion)} was missing from the reply and was set to 0.")
            scored.append(_filled(criterion))
            continue
        scored.append(
            ScoredCriterion(
                criterion_id=criterion.criterion_id,
                score=_clean_score(entry.get("score"), criterion, warnings),
                justification=_clean_text(entry.get("justification"), NO_JUSTIFICATION, "justification", criterion, warnings),
                evidence=_clean_text(entry.get("evidence"), NOT_STATED, "evidence", criterion, warnings),
            )
        )

    summary = data.get("overall_summary")
    if summary is not None and not isinstance(summary, str):
        warnings.append("overall_summary was not text and was ignored.")
        summary = ""
    scorecard = EvaluationScorecard(
        criteria=tuple(scored),
        extracted_facts=_clean_facts(data.get("extracted_facts"), warnings),
        risks=_clean_risks(data.get("risks"), warnings),
        overall_summary=(summary or "").strip(),
    )
    return ValidationResult(scorecard=scorecard, warnings=tuple(warnings))


def failed_evaluation(criteria: Sequence[CriterionLike], reason: str = "the reply was not valid JSON after one retry") -> ValidationResult:
    """The scorecard for a supplier whose evaluation failed twice: every criterion 0, one 'Evaluation failed' warning."""
    scorecard = EvaluationScorecard(
        criteria=tuple(_filled(c) for c in criteria),
        extracted_facts=ExtractedFacts(),
    )
    return ValidationResult(scorecard=scorecard, warnings=(f"Evaluation failed: {reason}.",))
