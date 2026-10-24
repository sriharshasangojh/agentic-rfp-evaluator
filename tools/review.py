"""Review rules (feature F13, spec section 11): the plain-Python decisions behind the Review tab.

Pure functions only: no I/O, no Streamlit, no LLM, no clock. The Review tab and `agents/orchestrator.py` call these, so
the rules are written once and tested on their own:

* how many times the AI may read a supplier again (2) and what it is told when it does (used by `orchestrator.re_evaluate`);
* the to-do list at the top of the tab;
* re-ranking every supplier from their saved marks (after the priorities change, or after one supplier was read again);
* before and after: the leaderboard as saved data, who is recommended, how a supplier moved, and the sentences that say so.

The satisfaction question and the priorities rule are in `tools/reweight.py`.

Nothing here changes what was stored: the caller saves the new rows. A change never overwrites an earlier attempt or decision; it
adds one (see `db/schema.sql`: the history tables refuse UPDATE and DELETE).
"""
from __future__ import annotations

import copy
import math
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from tools import insights, ranking

MAX_RE_EVALUATIONS = 2          # per supplier (spec 11.1 rule 3)
COMMENT_MAX_CHARS = 1000        # the longest comment or reason; it is also sent to the AI, so it is kept short

# The kinds of to-do item, in the order they are listed.
KIND_FAILED = "failed"
KIND_GATE = "gate"
KIND_EVIDENCE = "evidence"
TODO_ORDER = (KIND_FAILED, KIND_GATE, KIND_EVIDENCE)


class ReviewError(ValueError):
    """A review request that cannot be carried out. The message is a plain sentence that is safe to show."""


# ---------------------------------------------------------------- the two re-evaluations

def re_evaluations_used(current_attempt: int) -> int:
    """Attempt 1 is the first evaluation, so a supplier on attempt 3 has been re-evaluated twice."""
    return max(0, current_attempt - 1)


def re_evaluations_left(current_attempt: int) -> int:
    return max(0, MAX_RE_EVALUATIONS - re_evaluations_used(current_attempt))


def clean_text(text: object) -> str:
    """A comment or reason as saved: surrounding space removed, at most COMMENT_MAX_CHARS long."""
    return str(text or "").strip()[:COMMENT_MAX_CHARS].strip()


def feedback_for_prompt(criterion_id: int | None, criterion_name: str | None, comment: str) -> str:
    """The text that goes into the AI's <reviewer_feedback> block (spec 7.1): the comment and the criterion it is about."""
    if criterion_id is None:
        head = "The reviewer disagrees with this supplier's marks."
    else:
        label = f"criterion {criterion_id} ({criterion_name})" if criterion_name else f"criterion {criterion_id}"
        head = f"The reviewer disagrees with the mark for {label}."
    return f"{head}\nReviewer's comment: {clean_text(comment)}"


# ---------------------------------------------------------------- the to-do list

@dataclass(frozen=True)
class TodoCriterion:
    criterion_id: int
    name: str
    evidence_found: bool | None


@dataclass(frozen=True)
class TodoSupplier:
    name: str
    rank: int
    why_not: tuple[str, ...]         # the failed must-haves in words (empty when the supplier is eligible)
    evaluation_failed: bool
    criteria: tuple[TodoCriterion, ...]


@dataclass(frozen=True)
class TodoItem:
    kind: str
    supplier: str
    criterion_id: int | None
    text: str


def build_todo(suppliers: Sequence[TodoSupplier]) -> tuple[TodoItem, ...]:
    """The to-do list (spec 11.1 rule 1): evaluations that failed, failed must-haves, evidence not found. Re-checks that are
    waiting for the AI join it with the "Not happy?" flow (F19)."""
    found: list[tuple[int, int, int, TodoItem]] = []   # (kind order, supplier rank, criterion id, item)

    def add(item: TodoItem, rank: int, criterion_id: int = 0) -> None:
        found.append((TODO_ORDER.index(item.kind), rank, criterion_id, item))

    for s in suppliers:
        if s.evaluation_failed:
            add(TodoItem(KIND_FAILED, s.name, None,
                         "The AI's reply could not be read, so every mark is 0. Run this evaluation again on the New evaluation tab."),
                s.rank)
        else:
            for reason in s.why_not:
                add(TodoItem(KIND_GATE, s.name, None, reason), s.rank)
        for c in s.criteria:
            if c.evidence_found is False:
                add(TodoItem(KIND_EVIDENCE, s.name, c.criterion_id,
                             f"{c.name}: the quoted evidence was not found in the proposal."), s.rank, c.criterion_id)
    found.sort(key=lambda entry: entry[:3])
    return tuple(entry[3] for entry in found)


def todo_counts(items: Sequence[TodoItem]) -> dict[str, int]:
    """How many to-do items each supplier has."""
    counts: dict[str, int] = {}
    for item in items:
        counts[item.supplier] = counts.get(item.supplier, 0) + 1
    return counts


# ---------------------------------------------------------------- re-ranking everyone after a change

def scores_of(result: Mapping[str, Any], criterion_ids: Collection[int]) -> dict[int, float]:
    """The current marks saved in one supplier's result, one per criterion. Raises ReviewError when one is missing or unusable."""
    scores: dict[int, float] = {}
    for item in result.get("criteria") or []:
        if not isinstance(item, Mapping):
            continue
        value = item.get("score")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            continue
        try:
            scores[int(item.get("criterion_id"))] = float(value)   # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
    missing = sorted(set(criterion_ids) - set(scores))
    if missing:
        raise ReviewError("A saved result has no usable mark for criterion " + ", ".join(str(m) for m in missing) + ".")
    return scores


def rank_results(
    results: Mapping[str, Mapping[str, Any]],
    criteria: Sequence[ranking.CriterionWeight],
    submitted: Mapping[str, date],
    experience: Mapping[str, int | None],
) -> ranking.RankingResult:
    """Rank every supplier from the marks saved in its result (so an override counts), with the usual tie-breaks."""
    ids = [c.criterion_id for c in criteria]
    inputs = [ranking.SupplierInput(name, submitted[name], experience.get(name), scores_of(result, ids))
              for name, result in results.items()]
    try:
        return ranking.rank_suppliers(inputs, criteria)
    except ValueError as exc:     # a mark outside its range, a duplicate name...: the saved data is not usable
        raise ReviewError(f"The saved marks cannot be ranked ({exc})") from exc


def apply_ranking(
    results: Mapping[str, Mapping[str, Any]],
    ranked: ranking.RankingResult,
    dropped_warnings: Collection[str] = (),
) -> dict[str, dict[str, Any]]:
    """New saved results: each keeps every field it had, and gets the new rank, scores, benchmarks, gaps, relative %,
    strength labels and tie-break note. `dropped_warnings` are the OLD ranking notes, replaced by the new ones."""
    dropped = set(dropped_warnings)
    ties = {line.split(" above ", 1)[0]: line for line in ranked.tie_break_explanations}
    updated: dict[str, dict[str, Any]] = {}
    for r in ranked.suppliers:
        result = copy.deepcopy(dict(results[r.name]))
        by_id = {cr.criterion_id: cr for cr in r.criteria}
        for item in result.get("criteria") or []:
            cr = by_id.get(item.get("criterion_id")) if isinstance(item, dict) else None
            if cr is None:
                continue
            item.update(score=cr.score, max_score=cr.max_score, benchmark=cr.benchmark, gap=cr.gap,
                        relative_pct=cr.relative_pct, weight=cr.weight, strength=insights.strength_label(cr.score, cr.max_score))
        old_warnings = [w for w in result.get("warnings") or [] if isinstance(w, str) and w not in dropped]
        result.update(rank=r.rank, absolute_score=r.absolute_score, ppi=r.ppi, tie_break_explanation=ties.get(r.name),
                      warnings=[*old_warnings, *(w for w in ranked.warnings if w not in old_warnings)])
        updated[r.name] = result
    return updated


# ---------------------------------------------------------------- before and after

def standings(ranked: ranking.RankingResult, eligible: Mapping[str, bool]) -> list[dict[str, Any]]:
    """The leaderboard as plain data, for saving with a change: rank, supplier, scores and eligibility."""
    return [{"rank": r.rank, "supplier": r.name, "ppi": r.ppi, "absolute_score": r.absolute_score,
             "eligible": bool(eligible.get(r.name, False))} for r in ranked.suppliers]


def recommended_of(board: Sequence[Mapping[str, Any]]) -> str | None:
    """Who would be recommended from a saved leaderboard: the best-ranked supplier that is eligible."""
    contenders = [insights.Contender(str(row["supplier"]), int(row["rank"]), bool(row.get("eligible"))) for row in board]
    return insights.choose_recommendation(contenders).recommended


def movement(before: Sequence[Mapping[str, Any]], after: Sequence[Mapping[str, Any]], supplier: str) -> tuple[int | None, int | None]:
    """The supplier's rank before and after (None when it is not on that board)."""
    def rank_in(board: Sequence[Mapping[str, Any]]) -> int | None:
        return next((int(row["rank"]) for row in board if row.get("supplier") == supplier), None)
    return rank_in(before), rank_in(after)


def _mark(value: object) -> str:
    return f"{value:g}" if isinstance(value, (int, float)) and not isinstance(value, bool) else str(value)


def describe_changes(changes: Sequence[Mapping[str, Any]]) -> str:
    """One sentence on which marks a re-evaluation changed ("Commercial Value 5 to 7")."""
    moved = [c for c in changes if c.get("old") != c.get("new")]
    if not moved:
        return "The AI kept every mark the same."
    return "Marks changed: " + "; ".join(f"{c.get('name')} {_mark(c.get('old'))} to {_mark(c.get('new'))}" for c in moved) + "."


def describe_move(supplier: str, before: int | None, after: int | None) -> str:
    """One sentence on where a supplier moved in the ranking ("Apex Systems moved up from rank 3 to rank 2.")."""
    if before is None or after is None:
        return ""
    if before == after:
        return f"{supplier} stays at rank {after}."
    return f"{supplier} moved {'up' if after < before else 'down'} from rank {before} to rank {after}."


def describe_recommendation(before: str | None, after: str | None) -> str:
    """One sentence on who is recommended after a change, and whether that changed ("We now recommend Apex Systems instead of Orbit Digital.")."""
    if after is None:
        return "No supplier meets every must-have now." if before is not None else "No supplier meets every must-have."
    if before == after:
        return f"The recommended supplier is unchanged: {after}."
    return f"We now recommend {after}" + (f" instead of {before}." if before else ".")
