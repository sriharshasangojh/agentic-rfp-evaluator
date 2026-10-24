"""Tests for tools/evidence_checker.py (spec 9.2) and for how a run uses it. No network, no model."""
from __future__ import annotations

import pytest

from tools import evidence_checker as ec
from tools.evidence_checker import evidence_found, is_not_applicable, normalise, split_quote

PAGES = (
    "Apex Systems | Page 1\nWe offer a three-year on-site warranty with next-business-day repair.\nBattery life was measured at 11.5 hours.",
    "Page 2\nMeridian can order additional units of the same model at the same unit price of Rs 59,000 for 12 months\nfrom the date of this proposal.",
    "Page 3\nThe “Z14” laptop weighs 1.38 kg — light enough for travel.",
)


def test_an_exact_quote_is_found():
    assert evidence_found("We offer a three-year on-site warranty with next-business-day repair. (Page 1)", PAGES) is True


def test_a_quote_that_wraps_over_lines_or_changes_case_is_found():
    assert evidence_found("same unit price of rs 59,000 for 12 MONTHS from the date of this proposal (Page 2)", PAGES) is True


def test_curly_quotes_and_dashes_are_treated_as_plain_ones():
    assert evidence_found('The "Z14" laptop weighs 1.38 kg - light enough for travel. (Page 3)', PAGES) is True


def test_an_invented_quote_is_not_found():
    assert evidence_found("Apex gives a ten year warranty with a free replacement every year. (Page 1)", PAGES) is False


def test_a_small_slip_in_a_long_quote_still_counts_as_found():
    assert evidence_found("We offer a three-year on-site warrenty with next-business-day repair. (Page 1)", PAGES) is True


def test_a_quote_on_another_page_than_the_one_named_is_still_found():
    assert evidence_found("Battery life was measured at 11.5 hours. (Page 3)", PAGES) is True


def test_a_page_number_outside_the_proposal_is_ignored():
    assert evidence_found("Battery life was measured at 11.5 hours. (Page 99)", PAGES) is True
    assert evidence_found("Battery life was measured at 11.5 hours. (Page 0)", PAGES) is True


def test_a_quote_without_a_page_is_searched_in_every_page():
    assert evidence_found("Battery life was measured at 11.5 hours.", PAGES) is True


def test_a_quote_with_dots_is_found_when_each_piece_is():
    assert evidence_found("three-year on-site warranty ... measured at 11.5 hours (Page 1)", PAGES) is True
    assert evidence_found("three-year on-site warranty … a free laptop for every user (Page 1)", PAGES) is False


@pytest.mark.parametrize("text", ["Not stated in the proposal", "not stated in the proposal.", "  Not stated in the proposal ", "", "   ", '"Not stated in the proposal"', "Not stated in the proposal; the document gives no figure"])
def test_nothing_to_check_gives_none(text):
    assert evidence_found(text, PAGES) is None and is_not_applicable(text)


def test_a_real_quote_is_applicable():
    assert not is_not_applicable("We offer laptops. (Page 1)")


def test_a_quote_that_is_only_a_page_reference_is_not_checked():
    assert evidence_found("(Page 2)", PAGES) is None


def test_no_pages_means_not_found():
    assert evidence_found("We offer laptops. (Page 1)", ()) is False


@pytest.mark.parametrize("text,page,pieces", [
    ("a quote here (Page 3)", 3, ["a quote here"]),
    ("a quote here (page 12).", 12, ["a quote here"]),
    ("a quote here [Page 2]", 2, ["a quote here"]),
    ("a quote here (p. 4)", 4, ["a quote here"]),
    ("a quote here (Pages 2-3)", 2, ["a quote here"]),
    ("“A quote here” (Page 5)", 5, ["a quote here"]),
    ("first part ... second part (Page 1)", 1, ["first part", "second part"]),
    ("no page at all", None, ["no page at all"]),
])
def test_the_page_tail_and_dots_are_taken_off(text, page, pieces):
    assert split_quote(text) == (pieces, page)


def test_normalise_unifies_case_space_quotes_and_dashes():
    assert normalise("  A “B”\n\n‘C’ – D — E  ") == "a \"b\" 'c' - d - e"


def test_a_short_piece_is_only_found_exactly():
    assert evidence_found("xyz abc", PAGES) is False                       # two words: no fuzzy match
    assert evidence_found("11.5 hours", PAGES) is True
    assert evidence_found("11.5 hoors", PAGES) is False                    # one slip in two words is not close enough to trust
    assert evidence_found("measured at 11.5 hoors", PAGES) is True         # but three words or more may carry a slip


def test_the_threshold_is_the_one_in_the_spec():
    assert ec.THRESHOLD == 90


def test_the_built_in_comparison_gives_the_same_answers(monkeypatch):
    monkeypatch.setattr(ec, "_fuzz", None)
    assert evidence_found("We offer a three-year on-site warrenty with next-business-day repair. (Page 1)", PAGES) is True
    assert evidence_found("Apex gives a ten year warranty with a free replacement every year. (Page 1)", PAGES) is False


def test_partial_ratio_of_equal_and_unrelated_text(monkeypatch):
    monkeypatch.setattr(ec, "_fuzz", None)
    assert ec._partial_ratio("abc def ghi", "xx abc def ghi yy") == 100
    assert ec._partial_ratio("abc def ghi", "zzz zzz zzz zzz zzz") < 50
    assert ec._partial_ratio("", "abc") == 0 and ec._partial_ratio("abc", "") == 0
    assert ec._partial_ratio("a longer needle than the text", "short") > 0


def test_rapidfuzz_agrees_when_installed():
    rapidfuzz = pytest.importorskip("rapidfuzz")
    assert ec._fuzz is rapidfuzz.fuzz
    assert evidence_found("We offer a three-year on-site warrenty with next-business-day repair. (Page 1)", PAGES) is True
