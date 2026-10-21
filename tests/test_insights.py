"""Tests for tools/insights.py (spec 9.6 and 16.1): strength labels. Pros/cons and the rest come in later features."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tools.insights import AVERAGE, STRONG, WEAK, strength_label


def test_the_spec_examples():
    assert strength_label(8, 10) == "Strong"
    assert strength_label(7, 10) == "Average"
    assert strength_label(4, 10) == "Weak"


@pytest.mark.parametrize(
    "score, label",
    [
        (10, STRONG), (9, STRONG), (8, STRONG),
        (7.9, AVERAGE), (7, AVERAGE), (5, AVERAGE),
        (4.9, WEAK), (4, WEAK), (0, WEAK),
    ],
)
def test_boundaries_on_a_maximum_of_10(score, label):
    assert strength_label(score, 10) == label


@pytest.mark.parametrize(
    "score, max_score, label",
    [
        (16, 20, STRONG), (15.9, 20, AVERAGE), (10, 20, AVERAGE), (9.9, 20, WEAK),
        (4, 5, STRONG), (2.5, 5, AVERAGE), (2.4, 5, WEAK),
        (2.4, 3, STRONG),   # exactly 80%: a binary float would say 0.7999999999999999
        (12, 10, STRONG),   # above the maximum still counts as Strong
        (-1, 10, WEAK),
    ],
)
def test_other_maximums_and_odd_values(score, max_score, label):
    assert strength_label(score, max_score) == label


@pytest.mark.parametrize("bad_max", [0, -10])
def test_a_maximum_of_zero_or_less_is_refused(bad_max):
    with pytest.raises(ValueError, match="above 0"):
        strength_label(5, bad_max)


def test_the_label_follows_a_changed_score():
    assert strength_label(4, 10) == WEAK
    assert strength_label(8.5, 10) == STRONG  # e.g. after a human override


def test_insights_module_stays_pure():
    """Spec 2.1 and 21: no LLM, I/O, randomness or clock in insights.py. Only these imports are allowed."""
    allowed = {"__future__", "collections", "dataclasses", "fractions", "math", "typing"}
    source = Path(__file__).resolve().parent.parent / "tools" / "insights.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    imported, called = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
        elif isinstance(node, ast.Call):
            func = node.func
            called.add(func.id if isinstance(func, ast.Name) else getattr(func, "attr", ""))
    assert imported <= allowed, f"unexpected imports in insights.py: {sorted(imported - allowed)}"
    assert not called & {"open", "print", "input", "now", "today", "utcnow", "random"}, sorted(called)
