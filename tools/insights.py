"""Insights (spec section 10): decisions that are made in Python and only worded by the LLM.

F05 adds the strength labels (spec 9.6). Pros and cons, the recommendation, the what-if table and the
negotiation selection are added by later features.
Pure functions only: no LLM, no I/O, no randomness, no clock.
"""
from __future__ import annotations

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
