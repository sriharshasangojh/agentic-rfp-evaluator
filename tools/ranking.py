"""Ranking engine (spec section 9.3): absolute score, benchmarks, gaps, relative %, PPI and ranks.

Pure functions only: no I/O, no randomness, no clock, no LLM. Everything is calculated with exact
fractions and rounded half up to 2 decimals, so the same validated scores always give exactly the
same scores and the same ranking, whatever the order of the input or the machine it runs on.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, localcontext
from fractions import Fraction

_HUNDRED = Fraction(100)


@dataclass(frozen=True)
class CriterionWeight:
    """One active criterion, as the ranking needs it (the values come from the database)."""

    criterion_id: int
    weight: float
    max_score: float


@dataclass(frozen=True)
class SupplierInput:
    """One supplier's validated scores plus the facts that decide ties."""

    name: str
    submission_date: date
    experience_rating: int | None  # 1 to 5, or None when the buyer did not rate this supplier
    scores: Mapping[int, float]  # criterion_id -> validated score


@dataclass(frozen=True)
class CriterionResult:
    """How one supplier did on one criterion."""

    criterion_id: int
    score: float
    max_score: float
    weight: float
    benchmark: float      # best score any supplier got on this criterion
    gap: float            # score - benchmark: 0 for the leader, negative otherwise
    relative_pct: float   # score / benchmark * 100 (100 for everyone if the benchmark is 0)


@dataclass(frozen=True)
class SupplierRanking:
    rank: int
    name: str
    submission_date: date
    experience_rating: int | None
    absolute_score: float  # 0-100
    ppi: float             # Peer score, 0-100
    criteria: tuple[CriterionResult, ...]


@dataclass(frozen=True)
class RankingResult:
    suppliers: tuple[SupplierRanking, ...]    # best first
    tie_break_explanations: tuple[str, ...]
    warnings: tuple[str, ...]


def _exact(value: float) -> Fraction:
    """The exact decimal value of a number as written: 0.1 stays 1/10, not its binary approximation."""
    return Fraction(str(value))


def _round2(value: Fraction) -> float:
    """Round to 2 decimals, halves away from zero (0.625 -> 0.63). Python's round() would give 0.62."""
    with localcontext() as context:
        context.prec = 60
        exact = Decimal(value.numerator) / Decimal(value.denominator)
        return float(exact.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _check_inputs(suppliers: Sequence[SupplierInput], criteria: Sequence[CriterionWeight]) -> None:
    """Fail loudly on input that the validator and the input checks should already have caught."""
    if not criteria:
        raise ValueError("At least one criterion is needed to rank suppliers.")
    criterion_ids = [c.criterion_id for c in criteria]
    if len(set(criterion_ids)) != len(criterion_ids):
        raise ValueError("Criterion ids must be unique.")
    for criterion in criteria:
        if criterion.max_score <= 0:
            raise ValueError(f"Criterion {criterion.criterion_id} needs a max score above 0.")
        if criterion.weight < 0:
            raise ValueError(f"Criterion {criterion.criterion_id} has a negative weight.")
    if sum(_exact(c.weight) for c in criteria) == 0:
        raise ValueError("The criteria weights must total more than 0.")

    seen: set[str] = set()
    for supplier in suppliers:
        key = supplier.name.lower()
        if key in seen:
            raise ValueError(f"Supplier names must be unique (ignoring capitals): '{supplier.name}' appears twice.")
        seen.add(key)
        for criterion in criteria:
            if criterion.criterion_id not in supplier.scores:
                raise ValueError(f"{supplier.name} has no score for criterion {criterion.criterion_id}.")
            score = supplier.scores[criterion.criterion_id]
            if not 0 <= score <= criterion.max_score:
                raise ValueError(
                    f"{supplier.name}: score {score} for criterion {criterion.criterion_id} "
                    f"is outside 0 to {criterion.max_score}."
                )


def _rating_text(rating: int | None) -> str:
    return "not rated" if rating is None else str(rating)


def _rating_for_sorting(rating: int | None) -> int:
    """A supplier without a rating comes after every rated one (below the lowest rating, 1)."""
    return 0 if rating is None else rating


def _tie_break_reason(upper: SupplierRanking, lower: SupplierRanking) -> str:
    """Which rule put `upper` above `lower` when their Peer scores are equal (the order of section 9.3)."""
    if upper.submission_date != lower.submission_date:
        return (
            f"earlier submission date ({upper.submission_date.isoformat()} "
            f"vs {lower.submission_date.isoformat()})"
        )
    if upper.experience_rating != lower.experience_rating:
        return f"higher experience rating ({_rating_text(upper.experience_rating)} vs {_rating_text(lower.experience_rating)})"
    return f"supplier name comes first alphabetically ({upper.name} vs {lower.name})"


def rank_suppliers(suppliers: Sequence[SupplierInput], criteria: Sequence[CriterionWeight]) -> RankingResult:
    """Score, benchmark and rank every supplier.

    Absolute score = sum(score / max_score * weight). Benchmark = best score per criterion.
    Relative % = score / benchmark * 100 (100 for everyone when the benchmark is 0).
    PPI = sum(relative % * weight) / sum(weight). Both scores are rounded to 2 decimals *before* sorting,
    then suppliers are ordered by: higher PPI, earlier submission date, higher experience rating, name A to Z.
    The rating is optional: at that step a supplier without one comes after every rated supplier.
    """
    _check_inputs(suppliers, criteria)
    if not suppliers:
        return RankingResult((), (), ())

    weights = {c.criterion_id: _exact(c.weight) for c in criteria}
    max_scores = {c.criterion_id: _exact(c.max_score) for c in criteria}
    total_weight = sum(weights.values(), Fraction(0))
    benchmarks = {
        c.criterion_id: max(_exact(s.scores[c.criterion_id]) for s in suppliers) for c in criteria
    }

    warnings: list[str] = []
    if len(suppliers) == 1:
        warnings.append(
            "Only one supplier was evaluated, so the peer comparison is not meaningful (Peer score is 100)."
        )
    for criterion in criteria:
        if benchmarks[criterion.criterion_id] == 0:
            warnings.append(
                f"Criterion {criterion.criterion_id}: every supplier scored 0, so relative % is set to 100 for all."
            )

    unranked: list[SupplierRanking] = []
    for supplier in suppliers:
        results: list[CriterionResult] = []
        absolute = Fraction(0)
        weighted_relative = Fraction(0)
        for criterion in criteria:
            cid = criterion.criterion_id
            score = _exact(supplier.scores[cid])
            benchmark = benchmarks[cid]
            relative = _HUNDRED if benchmark == 0 else score / benchmark * _HUNDRED
            absolute += score / max_scores[cid] * weights[cid]
            weighted_relative += relative * weights[cid]
            results.append(
                CriterionResult(
                    criterion_id=cid,
                    score=float(score),
                    max_score=float(max_scores[cid]),
                    weight=float(weights[cid]),
                    benchmark=float(benchmark),
                    gap=_round2(score - benchmark),
                    relative_pct=_round2(relative),
                )
            )
        unranked.append(
            SupplierRanking(
                rank=0,  # set after sorting
                name=supplier.name,
                submission_date=supplier.submission_date,
                experience_rating=supplier.experience_rating,
                absolute_score=_round2(absolute),
                ppi=_round2(weighted_relative / total_weight),
                criteria=tuple(results),
            )
        )

    ordered = sorted(unranked, key=lambda r: (-r.ppi, r.submission_date, -_rating_for_sorting(r.experience_rating), r.name.lower()))
    ranked = tuple(
        SupplierRanking(
            rank=position,
            name=r.name,
            submission_date=r.submission_date,
            experience_rating=r.experience_rating,
            absolute_score=r.absolute_score,
            ppi=r.ppi,
            criteria=r.criteria,
        )
        for position, r in enumerate(ordered, start=1)
    )
    explanations = tuple(
        f"{upper.name} above {lower.name}: {_tie_break_reason(upper, lower)}"
        for upper, lower in zip(ranked, ranked[1:])
        if upper.ppi == lower.ppi
    )
    return RankingResult(suppliers=ranked, tie_break_explanations=explanations, warnings=tuple(warnings))
