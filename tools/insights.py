"""Insights (spec section 10): decisions that are made in Python and only worded by the LLM.

F05 adds the strength labels (spec 9.6). F10 adds the recommendation (spec 10.2). Pros and cons, the what-if
table and the negotiation selection are added by later features.
Pure functions only: no LLM, no I/O, no randomness, no clock.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction

STRONG = "Strong"
AVERAGE = "Average"
WEAK = "Weak"

STRONG_FROM = Fraction(4, 5)   # score / max_score >= 0.80
AVERAGE_FROM = Fraction(1, 2)  # 0.50 up to (not including) 0.80; below 0.50 is Weak


def strength_label(score: float, max_score: float) -> str:
    """Strong (>= 80% of the maximum), Average (50% to under 80%) or Weak (under 50%).

    Calculated on the numbers as written, so 2.4 out of 3 is exactly 80% and counts as Strong.
    Call it again after any override so the label follows the new score.
    """
    maximum = Fraction(str(max_score))
    if maximum <= 0:
        raise ValueError("max_score must be above 0.")
    share = Fraction(str(score)) / maximum
    if share >= STRONG_FROM:
        return STRONG
    if share >= AVERAGE_FROM:
        return AVERAGE
    return WEAK


# ---------------------------------------------------------------- recommendation (spec 10.2)

INDUSTRY_WARNING_BELOW = 60   # a recommended supplier with an industry score under this gets a warning


@dataclass(frozen=True)
class Contender:
    """A supplier as the recommendation sees it: where it ranks and whether it may be recommended.

    "May be recommended" means it passes every mandatory check. Later features also mark a supplier the buyer set
    aside as not eligible, so this function does not need to change.
    """

    name: str
    rank: int
    eligible: bool


@dataclass(frozen=True)
class Recommendation:
    recommended: str | None   # the highest-ranked eligible supplier, or None when nobody is eligible
    runner_up: str | None     # the next eligible supplier after it (None when there is no other eligible one)
    top_ranked: str | None    # the supplier ranked 1, eligible or not (None only when there are no suppliers)

    @property
    def none_eligible(self) -> bool:
        return self.top_ranked is not None and self.recommended is None


def choose_recommendation(contenders: Sequence[Contender]) -> Recommendation:
    """Recommended = the highest-ranked eligible supplier; runner-up = the next eligible one after it.

    Suppliers that are not eligible are still ranked, but are never recommended and never the runner-up
    (a runner-up is the fallback if the recommendation is not accepted, so it has to be recommendable too).
    """
    ordered = sorted(contenders, key=lambda c: c.rank)
    eligible = [c for c in ordered if c.eligible]
    return Recommendation(
        recommended=eligible[0].name if eligible else None,
        runner_up=eligible[1].name if len(eligible) > 1 else None,
        top_ranked=ordered[0].name if ordered else None,
    )


def industry_warning(industry_score: float, weak_attributes: Sequence[str]) -> str | None:
    """The warning for a recommended supplier whose industry score is under 60, else None.

    `weak_attributes` are the labels of the attributes graded Below or Not stated (the caller picks them).
    """
    if industry_score >= INDUSTRY_WARNING_BELOW:
        return None
    names = ", ".join(weak_attributes) if weak_attributes else "several attributes"
    return f"Below industry standard on: {names}"
