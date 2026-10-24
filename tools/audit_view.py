"""What the Audit tab works out from saved runs (feature F21, spec 13.3): the three AI-quality tiles, the run-history filters and the
change trail with its "Score changes only" filter. Pure functions: no database, no Streamlit, no AI.

The spec's first tile is the "override rate". This app has no score overrides (the person changes what counts for more instead), so
the tile is the share of runs in which the person did that at least once.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from fractions import Fraction
from typing import Any

ALL = "All"                                      # the filter entry that lets everything through
SCORE_CHANGE_ACTIONS = frozenset({"price_marks_set", "priorities_set", "priorities_changed", "ranking_computed"})


def _amount(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value == value and abs(value) != float("inf")


def percent(part: int, whole: int) -> float | None:
    """part / whole as a percentage with one decimal (half up), or None when there is nothing to divide by."""
    if whole <= 0 or part < 0:
        return None
    tenths = int(Fraction(part * 1000, whole) + Fraction(1, 2))
    return tenths / 10


def average_cost(costs: Sequence[object]) -> tuple[float | None, int]:
    """(average cost in USD, how many runs reported a cost). A run that did not report a cost is left out, not counted as free."""
    known = [float(c) for c in costs if _amount(c) and c >= 0]       # type: ignore[operator]
    if not known:
        return None, 0
    return sum(known) / len(known), len(known)


@dataclass(frozen=True)
class QualityTiles:
    runs: int
    changed_rate: float | None               # % of runs in which the person changed the priorities at least once
    changed_runs: int
    evidence_rate: float | None              # % of checked quotes that were not found in the proposal
    evidence_missing: int
    evidence_checked: int
    average_cost_usd: float | None
    runs_with_cost: int


def quality_tiles(runs: int, changed_runs: int, evidence_missing: int, evidence_checked: int, costs: Sequence[object]) -> QualityTiles:
    average, with_cost = average_cost(costs)
    return QualityTiles(runs, percent(changed_runs, runs), changed_runs, percent(evidence_missing, evidence_checked),
                        evidence_missing, evidence_checked, average, with_cost)


# ---------------------------------------------------------------- run history

@dataclass(frozen=True)
class HistoryLine:
    run_id: str
    started: datetime | None
    model: str
    winner: str | None
    status: str
    suppliers: int
    cost_usd: float | None


def moment_of(text: object) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(text))
    except ValueError:
        return None
    return moment.astimezone(timezone.utc) if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)


def history_lines(summaries: Sequence[Any]) -> tuple[HistoryLine, ...]:
    """One line per run, in the order given. `summaries` are repository.RunSummary rows."""
    return tuple(HistoryLine(s.run.rfp_run_id, moment_of(s.run.created_at), (s.run.model or "").strip(), s.winner, s.run.status,
                             s.suppliers, float(s.run.total_cost_usd) if _amount(s.run.total_cost_usd) else None) for s in summaries)


def choices(lines: Sequence[HistoryLine], field: str) -> tuple[str, ...]:
    """The entries of a filter drop-down: ALL, then each distinct value once, sorted. Runs without a value are not offered."""
    values = {getattr(line, field) for line in lines if getattr(line, field)}
    return (ALL, *sorted(values, key=str.casefold))


def filter_history(lines: Sequence[HistoryLine], *, start: date | None = None, end: date | None = None,
                   model: str = ALL, winner: str = ALL) -> tuple[HistoryLine, ...]:
    """Runs started on or after `start` and on or before `end` (UTC days), with that model and that winner."""
    kept = []
    for line in lines:
        day = line.started.date() if line.started is not None else None
        if (start is not None or end is not None) and day is None:
            continue
        if start is not None and day < start:                # type: ignore[operator]
            continue
        if end is not None and day > end:                    # type: ignore[operator]
            continue
        if model != ALL and line.model != model:
            continue
        if winner != ALL and line.winner != winner:
            continue
        kept.append(line)
    return tuple(kept)


# ---------------------------------------------------------------- the change trail

@dataclass(frozen=True)
class TrailRow:
    when: str
    actor: str
    text: str
    score_change: bool


def _count(value: object) -> int:
    return len(value) if isinstance(value, (list, tuple)) else 0


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _lakh(value: object) -> str:
    from tools import insights
    return insights.format_lakh(value) if _amount(value) else "an unknown amount"


def describe(action: str, d: Mapping[str, Any]) -> str:
    """One plain sentence for an audit event. An event this does not know is shown by its name."""
    supplier = str(d.get("supplier") or "").strip()
    if action == "run_created":
        return f"Run started with {_plural(_count(d.get('suppliers')), 'supplier')}, model {d.get('model') or 'not recorded'}."
    if action == "documents_read":
        return f"Read {_plural(_count(d.get('documents')), 'proposal')}."
    if action == "supplier_evaluated":
        return f"The AI evaluated {supplier or 'a supplier'} (attempt {d.get('attempt_no', 1)})" + (" and the evaluation failed." if d.get("evaluation_failed") else ".")
    if action == "validation_warnings":
        return f"{supplier or 'A supplier'}: {_plural(_count(d.get('warnings')), 'warning')} about the AI's reply."
    if action == "evidence_not_found":
        return f"{supplier or 'A supplier'}: {_plural(_count(d.get('criterion_ids')), 'quote')} not found in the proposal."
    if action == "gate_results":
        failed = _count(d.get("failed"))
        return f"{supplier or 'A supplier'}: must-haves checked" + (f", {failed} not met." if failed else ", all met.")
    if action == "price_marks_set":
        return f"Price marks worked out from the totals; the lowest total is {_lakh(d.get('lowest_total_rs'))}."
    if action == "need_interpreted":
        return "The AI read what the person wrote."
    if action == "need_interpretation_failed":
        return "The AI could not read what the person wrote."
    if action == "priorities_set":
        ticked = d.get("ticked")
        n = _count(ticked)
        return f"Priorities chosen before the run: {_plural(n, 'area')} {'counts' if n == 1 else 'count'} for more." if n else "Priorities chosen before the run."
    if action == "ranking_computed":
        top = d.get("top3") if isinstance(d.get("top3"), list) else []
        first = top[0].get("supplier") if top and isinstance(top[0], Mapping) else None
        return f"Ranking worked out{f'; first: {first}' if first else ''}."
    if action == "priorities_changed":
        before, after = d.get("recommended_before"), d.get("recommended_after")
        moved = f" Recommended: {after}." if after and after == before else (f" Recommended changed from {before} to {after}." if after else "")
        return f"Round {d.get('round', '?')}: {d.get('criterion') or 'an area'} now counts for more.{moved}"
    if action == "points_worded":
        return f"The AI worded the good and bad points of {supplier or 'a supplier'}."
    if action == "wording_failed":
        return f"The AI's wording for {supplier or 'a supplier'} was not used."
    if action == "run_evaluated":
        return f"Run finished with {_plural(int(d['suppliers']) if _amount(d.get('suppliers')) else 0, 'supplier')}."
    if action == "recommendation_accepted":
        return f"{supplier or 'The recommendation'} accepted; the run is final."
    if action == "negotiation_suggested":
        return f"Final recommendation and negotiation points made for {supplier or 'a supplier'}."
    if action in ("question_answered", "question_failed"):
        return "A question about the proposals was answered." if action == "question_answered" else "A question about the proposals could not be answered."
    if action == "json_exported":
        return "The JSON file was downloaded."
    if action in ("run_failed", "run_interrupted"):
        return "The run stopped before it finished."
    return f"Event: {action.replace('_', ' ')}."


def trail_rows(events: Sequence[Any], when_text=lambda s: str(s)) -> tuple[TrailRow, ...]:
    """The audit trail of a run, oldest first, one plain sentence each. `events` are repository.AuditEvent rows."""
    return tuple(TrailRow(when_text(e.timestamp), e.actor, describe(e.action, e.details), e.action in SCORE_CHANGE_ACTIONS) for e in events)


def only_score_changes(rows: Sequence[TrailRow]) -> tuple[TrailRow, ...]:
    """The events that changed marks, weights or ranks."""
    return tuple(r for r in rows if r.score_change)
