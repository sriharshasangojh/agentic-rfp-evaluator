"""Scorecards tab tests (feature F11, reworked for readable results by change pack R1b).

Most tests call the tab's own functions on saved runs that are written straight into a temporary database, so every
combination (a missing criterion, a failed check, odd data) can be set up exactly. One test pushes a real batch through the
orchestrator and reads it back, so the two sides cannot drift apart. The last group drives the real app headlessly with
Streamlit's AppTest. No API key or network is used. Needs Streamlit and Plotly to import; the AppTest group is skipped
when Streamlit's test runner is missing.
"""
from __future__ import annotations

import html
import itertools
import json
import math
import sqlite3
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("streamlit")
pytest.importorskip("plotly")

from agents import orchestrator  # noqa: E402
from db import repository, seed  # noqa: E402
from llm import client  # noqa: E402
from tools import industry, insights, pdf_tool  # noqa: E402
from ui import tab_new_evaluation, tab_overview, tab_scorecards as S, theme  # noqa: E402

try:
    from streamlit.testing.v1 import AppTest
except ImportError:   # a stand-in for Streamlit, or a very old one
    AppTest = None

APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
needs_app_test = pytest.mark.skipif(AppTest is None, reason="Streamlit's test runner is not available")

CRITERIA = [(1, "Technical Capability", 30.0), (2, "Implementation Plan", 20.0), (3, "Commercial Value", 20.0),
            (4, "Security & Compliance", 20.0), (5, "Support & Experience", 10.0)]
FULL_FACTS = {"grand_total_rs": 7398600, "unit_price_rs": 59000, "warranty_years": 3, "warranty_type": "on-site", "delivery_weeks": 6,
              "battery_hours": 11.5, "battery_measured": True, "ram_gb": 16, "ssd_gb": 512}
BEST = (9, 9, 8, 9, 9)


@pytest.fixture(autouse=True)
def seeded_database():
    seed.ensure_database()


# ---------------------------------------------------------------- saved runs written by hand

def criterion(i: int, name: str, weight: float, score: float, *, benchmark: float = 9, evidence: str = "Battery 11.5 hours (Page 2)",
              justification: str = "Specific numbers.", filled: bool = False, max_score: float = 10.0) -> dict[str, Any]:
    return {"criterion_id": i, "name": name, "score": score, "max_score": max_score, "benchmark": benchmark, "gap": score - benchmark,
            "relative_pct": 100 if benchmark == 0 else round(score / benchmark * 100, 2), "weight": weight, "strength": "Strong",
            "justification": justification, "evidence": evidence, "evidence_found": None, "filled_by_validator": filled}


def card_result(scores=(9, 7, 5, 9, 8), *, facts=FULL_FACTS, failed=(), warnings=(), attributes=None, score=75.0,
                evaluation_failed=False, **changes) -> dict[str, Any]:
    result = {
        "criteria": [criterion(i, n, w, s, benchmark=b) for (i, n, w), s, b in zip(CRITERIA, scores, BEST)],
        "extracted_facts": dict(facts),
        "industry_breakdown": {"score": score, "attributes": attributes if attributes is not None else [
            {"attribute_key": "warranty", "label": "Warranty", "grade": "Above", "points": 100, "weight": 5, "reason": "3 years, on-site"},
            {"attribute_key": "unit_price", "label": "Unit price", "grade": "Below", "points": 25, "weight": 20, "reason": "Rs 73,986 per laptop"}]},
        "gate_details": [{"gate_id": f"g{n}", "name": name, "passed": False, "reason": reason} for n, (name, reason) in enumerate(failed)]
                        + [{"gate_id": "ok", "name": "Delivery", "passed": True, "reason": "6 weeks (at most 8 allowed)"}],
        "warnings": list(warnings), "evaluation_failed": evaluation_failed, "tie_break_explanation": None,
    }
    result.update(changes)
    return result


SEEDED_GATES = [("warranty", "Warranty", "3 years (at least 3 required)"), ("certification", "Certification", "BIS registration named"),
                ("price_table", "Price table", "Price table present"), ("delivery", "Delivery", "6 weeks (at most 8 allowed)")]


def seeded_result(*, evaluation_failed: bool = False, **failing: str) -> dict[str, Any]:
    """A saved result with the four seeded must-haves (so the plain wording applies). `failing` maps a gate id to why it failed."""
    gates = [{"gate_id": gid, "name": name, "passed": gid not in failing, "reason": failing.get(gid, ok)} for gid, name, ok in SEEDED_GATES]
    return card_result(gate_details=gates, evaluation_failed=evaluation_failed)


def supplier(name: str, rank: int, *, eligible: bool = True, result: dict[str, Any] | None = None, industry_score: float = 75.0,
             ppi: float = 80.0, overall: float = 70.0, rating: int | None = 3, when: str = "2026-10-05") -> dict[str, Any]:
    failed = () if eligible else (("Warranty", "1 year (at least 3 required)"),)
    return dict(name=name, rank=rank, eligible=eligible, industry=industry_score, ppi=ppi, overall=overall, rating=rating, when=when,
                result=result if result is not None else card_result(failed=failed))


_run_numbers = itertools.count(1)


def store_run(*suppliers: dict[str, Any], run_id: str | None = None, budget_rs: float | None = 6000000,
              quantity: int | None = 100) -> tuple[repository.RunRecord, list[repository.StoredSupplier]]:
    """Save a run by hand. Each call gets its own run id unless one is given (every test starts with an empty database).

    The run's budget is Rs 60 lakh for 100 laptops unless the test says otherwise (None saves a run without one, like an old run).
    """
    run_id = run_id or f"RUN-TEST-{next(_run_numbers):03d}"
    with repository.connection() as conn:
        repository.create_run(
            conn, run_id=run_id, created_at="2026-10-23T09:30:00+00:00", provider="OpenAI", model="test-model", temperature=0,   # type: ignore[arg-type]
            template_id=repository.DEFAULT_TEMPLATE_ID,
            criteria_snapshot_json=json.dumps([{"criterion_id": i, "name": n, "weight": w, "max_score": 10.0} for i, n, w in CRITERIA]),
            reviewer_name="Sri", budget_rs=budget_rs, quantity=quantity)   # type: ignore[arg-type]
        for s in suppliers:
            repository.save_supplier_result(
                conn, run_id=run_id, supplier_name=s["name"], submission_date=s["when"], experience_rating=s["rating"],
                absolute_score=s["overall"], ppi=s["ppi"], industry_score=s["industry"], final_rank=s["rank"],
                gates_passed=s["eligible"], result_json=json.dumps(s["result"]))
        run = repository.get_run(conn, run_id)
        stored = repository.list_supplier_results(conn, run_id)
    assert run is not None
    return run, stored


def scorecards_of(*suppliers: dict[str, Any], **options) -> S.Scorecards:
    run, stored = store_run(*suppliers, **options)
    return S.build_scorecards(run, stored)


def card_of(**options) -> S.Scorecard:
    return scorecards_of(supplier("Apex Systems", 1, **options)).cards[0]


# ---------------------------------------------------------------- the key-facts strip

def test_the_key_facts_are_five_tiles_in_the_spec_order():
    facts = S.key_facts(FULL_FACTS)
    assert [(f.label, f.value, f.detail) for f in facts] == [
        ("Total price", "Rs 73,98,600", "Rs 59,000 per laptop"), ("Warranty", "3 years", "On-site cover"), ("Delivery", "6 weeks", None),
        ("Battery", "11.5 hours", "Measured"), ("RAM / SSD", "16 GB RAM / 512 GB SSD", None)]


def test_facts_the_proposal_does_not_state_have_no_value():
    assert [(f.value, f.detail) for f in S.key_facts({})] == [(None, None)] * 5


def test_only_a_unit_price_still_shows_as_a_detail_under_an_unstated_total():
    price = S.key_facts({"unit_price_rs": 59000})[0]
    assert (price.value, price.detail) == (None, "Rs 59,000 per laptop")


@pytest.mark.parametrize("facts, expected", [
    ({"warranty_years": 1}, "1 year"), ({"warranty_years": 1.5}, "1.5 years"), ({"warranty_years": 0}, "0 years"),
    ({"delivery_weeks": 1}, "1 week"), ({"delivery_weeks": 7}, "7 weeks"),
    ({"battery_hours": 1}, "1 hour"), ({"battery_hours": 9.5}, "9.5 hours")])
def test_units_are_singular_only_for_exactly_one(facts, expected):
    assert expected in [f.value for f in S.key_facts(facts)]


def test_the_warranty_detail_says_what_kind_of_cover_it_is():
    carry, untyped, no_years = (S.key_facts(f)[1] for f in (
        {"warranty_years": 3, "warranty_type": "carry-in"}, {"warranty_years": 3}, {"warranty_type": "on-site"}))
    assert (carry.value, carry.detail) == ("3 years", "Carry-in only")
    assert (untyped.value, untyped.detail) == ("3 years", "Type of cover not stated")
    assert (no_years.value, no_years.detail) == (None, "On-site cover")


def test_the_battery_detail_says_whether_it_was_measured():
    measured, claimed, unknown, no_hours = (S.key_facts(f)[3] for f in (
        {"battery_hours": 10, "battery_measured": True}, {"battery_hours": 10, "battery_measured": False}, {"battery_hours": 10},
        {"battery_measured": True}))
    assert measured.detail == "Measured" and claimed.detail == "Claimed, not measured" and unknown.detail == "Not stated whether measured"
    assert (no_hours.value, no_hours.detail) == (None, None)


def test_memory_and_storage_say_which_half_is_missing():
    ram_only, ssd_only, decimals = (S.key_facts(f)[4] for f in ({"ram_gb": 16}, {"ssd_gb": 512}, {"ram_gb": 7.5, "ssd_gb": 256}))
    assert (ram_only.value, ram_only.detail) == ("16 GB RAM", "SSD not stated")
    assert (ssd_only.value, ssd_only.detail) == ("512 GB SSD", "RAM not stated")
    assert decimals.value == "7.5 GB RAM / 256 GB SSD"


def test_facts_of_the_wrong_type_count_as_not_stated():
    facts = S.key_facts({"grand_total_rs": "7398600", "delivery_weeks": True, "battery_hours": math.nan, "warranty_years": None,
                         "warranty_type": 3, "ram_gb": [16]})
    assert all(f.value is None for f in facts)


# ---------------------------------------------------------------- the criterion lines

def test_a_line_carries_what_ranking_stored_and_the_evidence_and_justification():
    line = card_of().lines[1]
    assert (line.criterion_id, line.name, line.score, line.max_score) == (2, "Implementation Plan", 7.0, 10.0)
    assert (line.benchmark, line.gap, line.weight) == (9.0, -2.0, 20.0) and line.relative_pct == pytest.approx(77.78)
    assert (line.evidence, line.justification, line.flag) == ("Battery 11.5 hours (Page 2)", "Specific numbers.", None)


def test_lines_follow_the_order_of_the_marking_scheme():
    assert [line.name for line in card_of().lines] == [name for _i, name, _w in CRITERIA]


def test_a_criterion_the_proposal_does_not_address_is_flagged_not_stated():
    result = card_result()
    result["criteria"][2] = criterion(3, "Commercial Value", 20, 1, benchmark=8, evidence="Not stated in the proposal")
    result["criteria"][3] = criterion(4, "Security & Compliance", 20, 0, evidence="not  stated in the PROPOSAL")
    lines = scorecards_of(supplier("Apex Systems", 1, result=result)).cards[0].lines
    assert [line.flag for line in lines] == [None, None, "Not stated", "Not stated", None]


def test_a_criterion_the_model_did_not_return_is_flagged_no_result_returned_even_with_a_normal_evidence_text():
    result = card_result()
    result["criteria"][3] = criterion(4, "Security & Compliance", 20, 0, filled=True, evidence="Some text", justification="No result returned")
    lines = scorecards_of(supplier("Apex Systems", 1, result=result)).cards[0].lines
    assert lines[3].flag == "No result returned" and lines[0].flag is None


def test_text_with_line_breaks_is_put_on_one_line():
    result = card_result()
    result["criteria"][0] = criterion(1, "Technical Capability", 30, 9, evidence="First line\n\nSecond   line", justification="a\r\nb")
    line = scorecards_of(supplier("Apex Systems", 1, result=result)).cards[0].lines[0]
    assert (line.evidence, line.justification) == ("First line Second line", "a b")


def test_damaged_criterion_entries_are_skipped_or_shown_without_numbers():
    lines = S._lines({"criteria": ["junk", None, {"criterion_id": "x", "score": "8", "max_score": None, "benchmark": True, "gap": math.nan}]})
    assert len(lines) == 1
    line = lines[0]
    assert (line.criterion_id, line.name) == (0, "Criterion 0")
    assert (line.score, line.max_score, line.benchmark, line.gap, line.relative_pct, line.weight) == (None,) * 6
    assert (line.evidence, line.justification, line.flag) == ("", "", None)


def test_a_result_without_criteria_gives_no_lines():
    assert card_of(result={"extracted_facts": {}}).lines == ()


# ---------------------------------------------------------------- industry, checks, warnings

def test_the_industry_attributes_come_from_the_breakdown():
    card = card_of()
    assert [(a.label, a.grade, a.points, a.weight, a.reason) for a in card.attributes] == [
        ("Warranty", "Above", 100.0, 5.0, "3 years, on-site"), ("Unit price", "Below", 25.0, 20.0, "Rs 73,986 per laptop")]


def test_a_missing_breakdown_gives_no_attributes():
    for breakdown in (None, {}, {"attributes": None}, {"attributes": []}):
        result = card_result()
        result["industry_breakdown"] = breakdown
        assert card_of(result=result).attributes == ()


def test_a_damaged_breakdown_is_tolerated():
    for breakdown in ("text", 5, ["x"], {"attributes": "text"}):
        assert S._attributes({"industry_breakdown": breakdown}) == ()
    only = S._attributes({"industry_breakdown": {"attributes": ["junk", {"grade": None}]}})
    assert [(a.label, a.grade, a.points, a.weight, a.reason) for a in only] == [("Attribute", "Not stated", None, None, "")]


def test_the_checks_say_passed_or_not_and_why():
    card = scorecards_of(supplier("BrightPath Tech", 1, eligible=False)).cards[0]
    assert [(g.name, g.passed, g.reason) for g in card.gates] == [
        ("Warranty", False, "1 year (at least 3 required)"), ("Delivery", True, "6 weeks (at most 8 allowed)")]


def test_a_check_without_a_clear_result_is_shown_as_not_passed():
    gates = S._gates({"gate_details": [{"name": "Warranty"}, {"name": "Delivery", "passed": "yes"}, "junk", {"passed": True}]})
    assert [(g.name, g.passed) for g in gates] == [("Warranty", False), ("Delivery", False), ("Check", True)]


def test_warnings_are_kept_as_text_in_order_and_empty_ones_are_dropped():
    result = card_result(warnings=["Unit price estimated from total", "", "  ", 5, "Line one\nline two"])
    assert card_of(result=result).warnings == ("Unit price estimated from total", "Line one line two")
    assert card_of(result=card_result()).warnings == ()
    assert S._warnings({"warnings": "not a list"}) == () and S._warnings({}) == ()


def test_a_failed_evaluation_is_marked():
    assert card_of(result=card_result(evaluation_failed=True)).evaluation_failed
    assert not card_of().evaluation_failed


# ---------------------------------------------------------------- the whole run

def test_cards_are_in_rank_order_and_the_picker_labels_carry_the_rank():
    result = scorecards_of(supplier("Zulu", 3), supplier("Mike", 1), supplier("Alpha", 2))
    assert [S.picker_label(c) for c in result.cards] == ["1. Mike", "2. Alpha", "3. Zulu"]
    assert all(c.count == 3 for c in result.cards)


def test_the_recommended_supplier_is_marked_and_chosen_by_default():
    result = scorecards_of(supplier("Apex", 1, eligible=False), supplier("Nexa", 2), supplier("Orbit", 3))
    assert [c.recommended for c in result.cards] == [False, True, False]
    assert result.default_index == 1


def test_when_nobody_is_eligible_nobody_is_marked_and_the_top_ranked_is_shown():
    result = scorecards_of(supplier("Apex", 1, eligible=False), supplier("Nexa", 2, eligible=False))
    assert [c.recommended for c in result.cards] == [False, False] and result.default_index == 0


def test_a_card_reuses_the_overview_row_for_the_header_numbers():
    card = scorecards_of(supplier("Apex", 1, eligible=False, ppi=86.94, overall=77.0, industry_score=62.5, rating=None, when="2026-10-05")).cards[0]
    assert (card.row.rank, card.row.name, card.row.ppi, card.row.overall, card.row.industry) == (1, "Apex", 86.94, 77.0, 62.5)
    assert (card.row.submitted, card.row.experience, card.row.eligible) == ("5 Oct 2026", "Not rated", False)
    assert card.row.why_not == ("Warranty: 1 year (at least 3 required)",)


def test_a_result_without_facts_gives_five_unstated_tiles():
    card = card_of(result={"criteria": []})
    assert len(card.facts) == 5 and all(f.value is None for f in card.facts)


def test_the_run_line_is_the_overviews():
    run_line = scorecards_of(supplier("Apex", 1), run_id="RUN-TEST-LINE").run_line
    assert run_line.startswith("Run RUN-TEST-LINE \u00b7 test-model (OpenAI) \u00b7 1 supplier(s)")


# ---------------------------------------------------------------- the chart

def test_the_chart_uses_the_criteria_that_have_a_mark_a_benchmark_and_a_maximum():
    lines = (S.Line(1, "A", 9, 10, 9, 0, 100, 30, "", "", None), S.Line(2, "B", None, 10, 9, None, None, 20, "", "", None),
             S.Line(3, "C", 5, 10, None, None, None, 20, "", "", None), S.Line(4, "D", 5, None, 8, None, None, 20, "", "", None),
             S.Line(5, "E", 4, 10, 8, -4, 50, 10, "", "", None))
    data = S.chart_data(lines)
    assert data == S.ChartData(("A", "E"), (9, 4), (9, 8), (10, 10))


def test_there_is_nothing_to_draw_without_marks():
    assert S.chart_data(()) is None
    assert S.chart_data((S.Line(1, "A", None, None, None, None, None, None, "", "", None),)) is None


def figure_for(names=("Technical Capability", "Implementation Plan"), supplier_name="Apex Systems"):
    data = S.ChartData(tuple(names), tuple([9.0, 7.0, 5.0][: len(names)]), tuple([9.0, 9.0, 8.0][: len(names)]), tuple([10.0] * len(names)))
    return S.score_vs_best_figure(supplier_name, data)


def test_the_figure_is_two_horizontal_bar_series_in_the_theme_colours():
    mine, best = figure_for().data
    assert (mine.name, best.name) == ("Apex Systems", "Best in this batch")
    assert mine.marker.color == theme.SCORE_SUPPLIER and best.marker.color == theme.SCORE_BEST
    assert mine.orientation == best.orientation == "h"
    assert list(mine.y) == list(best.y) == ["Technical Capability", "Implementation Plan"]
    assert list(mine.x) == [9.0, 7.0] and list(best.x) == [9.0, 9.0]
    assert list(mine.text) == ["9", "7"] and list(best.text) == ["9", "9"]


def test_a_card_gets_a_chart_for_its_own_supplier_or_none_when_there_is_nothing_to_draw():
    cards = scorecards_of(supplier("Apex Systems", 1), supplier("Orbit Digital", 2, result={}), run_id="RUN-TEST-CHARTS").cards
    figure = S.card_figure(cards[0])
    assert figure is not None and figure.data[0].name == "Apex Systems"
    assert list(figure.data[0].y) == [name for _i, name, _w in CRITERIA] and list(figure.data[0].x) == [9.0, 7.0, 5.0, 9.0, 8.0]
    assert list(figure.data[1].x) == list(BEST)
    assert S.card_figure(cards[1]) is None


def test_the_figure_groups_the_bars_lists_criteria_top_down_and_starts_at_zero():
    layout = figure_for().layout
    assert layout.barmode == "group" and layout.yaxis.autorange == "reversed"
    assert layout.xaxis.range[0] == 0 and layout.xaxis.range[1] == pytest.approx(10 * 1.08)
    assert layout.legend.orientation == "h"


def test_the_figure_is_taller_with_more_criteria():
    assert figure_for(("A", "B", "C")).layout.height > figure_for(("A",)).layout.height


def test_the_hover_text_shows_the_mark_and_its_maximum_and_never_the_supplier_name():
    figure = figure_for(supplier_name="Apex %{x} <b>")
    mine, best = figure.data
    for trace in (mine, best):
        assert "%{y}" in trace.hovertemplate and "%{x:g}" in trace.hovertemplate and "%{customdata:g}" in trace.hovertemplate
        assert "Apex" not in trace.hovertemplate
        assert list(trace.customdata) == [10.0, 10.0]
    assert mine.name == "Apex %{x} <b>"


def test_the_axis_follows_the_largest_maximum_when_criteria_have_different_maxima():
    data = S.ChartData(("A", "B"), (4.0, 20.0), (5.0, 25.0), (5.0, 25.0))
    assert S.score_vs_best_figure("X", data).layout.xaxis.range[1] == pytest.approx(25 * 1.08)


# ---------------------------------------------------------------- the HTML pieces

HTML_BUILDERS = {
    "header": lambda card: S.header_html(card),
    "facts": lambda card: S.facts_html(card.facts, card.row.price),
    "reasons": lambda card: S.reasons_html(card.row.pros, card.row.cons),
    "areas": lambda card: S.areas_html(card.lines),
    "criteria": lambda card: S.criteria_html(card.lines),
    "industry": lambda card: S.industry_html(card.attributes),
    "gates": lambda card: S.gates_html(card.gates),
    "warnings": lambda card: S.warnings_html(("One warning", "Another")),
}


@pytest.mark.parametrize("builder", list(HTML_BUILDERS))
def test_the_html_is_one_line_with_its_own_style(builder):
    result = card_result(attributes=[{"label": "A\n\nB", "grade": "Meets", "points": 75, "weight": 5, "reason": "x\n\ny"}])
    result["criteria"][0] = criterion(1, "Technical\n\nCapability", 30, 9, evidence="a\n\nb", justification="c\n\nd")
    html_text = HTML_BUILDERS[builder](card_of(result=result))
    assert "\n" not in html_text and html_text.startswith("<style>") and "</style>" in html_text


# ---------------------------------------------------------------- R1b: the header card

def header_of(*suppliers: dict[str, Any], pick: int = 0, **options) -> str:
    return S.header_html(scorecards_of(*suppliers, **options).cards[pick])


def test_the_header_names_the_supplier_and_its_rank_and_marks_the_pick():
    result = scorecards_of(supplier("Apex Systems", 1), supplier("Orbit", 2))
    text = S.header_html(result.cards[0])
    assert "Apex Systems" in text and "Ranked 1 of 2" in text and '<span class="sc-pill">Recommended</span>' in text
    assert "Recommended" not in S.header_html(result.cards[1])


def test_an_eligible_supplier_gets_a_green_chip_that_says_it_meets_every_must_have():
    text = header_of(supplier("Apex", 1))
    good = theme.PILL_GOOD
    assert f'<span class="sc-chip" style="color:{good[0]};background:{good[1]}">✓ Meets every must-have</span>' in text
    assert f"border-left-color:{theme.EDGE_GOOD}" in text and "Not eligible" not in text


def test_a_not_eligible_supplier_gets_a_red_chip_with_the_first_problem_in_plain_words():
    text = header_of(supplier("Vertexa", 1, eligible=False, result=seeded_result(price_table="No price table")))
    bad = theme.PILL_BAD
    assert (f'<span class="sc-chip" style="color:{bad[0]};background:{bad[1]}" title="Price table: No price table">'
            "✗ <strong>Not eligible:</strong> no price list</span>") in text
    assert f"border-left-color:{theme.EDGE_BAD}" in text
    assert "Recommended" not in text and "Meets every must-have" not in text


def test_several_failed_must_haves_show_the_first_and_count_the_rest_with_every_one_on_hover():
    result = seeded_result(warranty="1 year (at least 3 required)", certification="BIS registration not named", delivery="Not stated")
    text = header_of(supplier("Vertexa", 1, eligible=False, result=result))
    assert "<strong>Not eligible:</strong> warranty under 3 years (+2 more)</span>" in text
    assert 'title="Warranty: 1 year (at least 3 required)&#10;Certification: BIS registration not named&#10;Delivery: Not stated"' in text
    assert "\n" not in text


def test_two_failed_must_haves_say_plus_one_more_and_a_single_one_says_nothing_more():
    two = header_of(supplier("Vertexa", 1, eligible=False, result=seeded_result(warranty="1 year (at least 3 required)", delivery="Not stated")))
    assert "<strong>Not eligible:</strong> warranty under 3 years (+1 more)</span>" in two
    one = header_of(supplier("Vertexa", 1, eligible=False, result=seeded_result(delivery="Not stated")))
    assert "<strong>Not eligible:</strong> delivery time not stated</span>" in one and "more)" not in one


def test_a_must_have_name_or_reason_from_the_saved_run_cannot_inject_html_into_the_header():
    evil = '"><script>alert(1)</script>'
    text = header_of(supplier("Vertexa", 1, eligible=False, result=card_result(failed=((evil, evil),))))
    assert "<script>" not in text and "&lt;script&gt;" in text


def test_a_not_eligible_supplier_whose_must_have_names_are_not_the_seeded_ones_shows_the_name_and_reason():
    text = header_of(supplier("BrightPath Tech", 1, eligible=False))                       # the default failed check is called "Warranty"
    assert "<strong>Not eligible:</strong> Warranty: 1 year (at least 3 required)</span>" in text


def test_a_failed_evaluation_is_not_eligible_because_the_evaluation_failed():
    text = header_of(supplier("Vertexa", 1, eligible=False, result=seeded_result(evaluation_failed=True)))
    assert "<strong>Not eligible:</strong> the evaluation failed</span>" in text
    assert f'title="{html.escape(tab_overview.EVALUATION_FAILED_TEXT)}"' in text


def test_a_supplier_with_no_must_haves_on_record_is_not_said_to_meet_them():
    text = header_of(supplier("Apex", 1, result={"criteria": []}))
    grey = S.STATUS_COLOURS[S.NEUTRAL]
    assert f'style="color:{grey[0]};background:{grey[1]}">– No must-haves were checked</span>' in text
    assert "Meets every must-have" not in text and f"border-left-color:{theme.TEAL}" in text


def test_the_status_says_what_it_is_made_of():
    cards = scorecards_of(supplier("Apex", 1), supplier("Nexa", 2, eligible=False, result=seeded_result(delivery="12 weeks (at most 8 allowed)")),
                          supplier("Orbit", 3, result={})).cards
    assert S.status_of(cards[0]) == S.Status("good", "Meets every must-have", None, None)
    assert S.status_of(cards[1]) == S.Status("bad", "Not eligible:", "delivery over 8 weeks", "Delivery: 12 weeks (at most 8 allowed)")
    assert S.status_of(cards[2]) == S.Status("neutral", "No must-haves were checked", None, None)
    nameless = replace(cards[1], row=replace(cards[1].row, problems=(), why_not=()))          # not eligible, but nothing to say why
    assert S.status_of(nameless) == S.Status("bad", "Not eligible", None, None)


def test_the_score_vs_other_bids_is_the_big_number_with_the_ppi_tooltip():
    text = header_of(supplier("Apex", 1, ppi=87.784))
    assert '<div class="sc-score-label">Score vs other bids</div>' in text
    assert '<span class="sc-score-num">87.78</span><span class="sc-score-max"> / 100</span>' in text
    assert f'<div class="sc-score-box" title="{html.escape(tab_new_evaluation.PPI_HELP)}">' in text


def test_the_big_number_always_has_two_decimals():
    assert '<span class="sc-score-num">80.00</span>' in header_of(supplier("Apex", 1, ppi=80))


def test_names_from_a_proposal_cannot_inject_html():
    evil = "<script>alert(1)</script>"
    run, stored = store_run(supplier(evil, 1, result=card_result(warnings=[evil], facts={**FULL_FACTS, "laptop_model": evil})))
    card = S.build_scorecards(run, stored).cards[0]
    for html_text in (S.header_html(card), S.warnings_html(card.warnings)):
        assert "<script>" not in html_text and "&lt;script&gt;" in html_text


def test_a_criterion_name_from_the_model_cannot_inject_html_into_the_areas_or_the_good_and_bad_points():
    evil = "<img src=x onerror=alert(1)>"
    result = card_result()
    result["criteria"][0] = criterion(1, evil, 30, 9, benchmark=9)           # a best mark, so it is a good point
    result["criteria"][1] = criterion(2, evil + "2", 20, 1, benchmark=9)     # far behind the best, so it is a weak point
    card = card_of(result=result)
    for text in (S.areas_html(card.lines), S.reasons_html(card.row.pros, card.row.cons)):
        assert "<img" not in text and "&lt;img src=x onerror=alert(1)&gt;" in text
    assert "&lt;img src=x onerror=alert(1)&gt;2" in S.reasons_html(card.row.pros, card.row.cons)


# ---------------------------------------------------------------- R1b: the five key-fact tiles

WITHIN = {**FULL_FACTS, "grand_total_rs": 5600000}        # inside the default budget of Rs 60 lakh


def price_tile(total: float | None, **run_options) -> str:
    facts = dict(FULL_FACTS)
    if total is None:
        facts.pop("grand_total_rs")
    else:
        facts["grand_total_rs"] = total
    card = scorecards_of(supplier("Apex", 1, result=card_result(facts=facts)), **run_options).cards[0]
    return S.tile_html(card.facts[0], card.row.price)


def test_a_stated_fact_is_a_plain_tile_with_its_icon_label_value_and_detail():
    assert S.tile_html(S.KeyFact("Warranty", "3 years", "On-site cover")) == (
        '<div class="sc-fact"><div class="sc-fact-label"><span class="sc-fact-icon" aria-hidden="true">\U0001F6E1️</span>Warranty</div>'
        '<div class="sc-fact-value">3 years</div><div class="sc-fact-detail">On-site cover</div></div>')


@pytest.mark.parametrize("label", ["Total price", "Warranty", "Delivery", "Battery", "RAM / SSD"])
def test_a_fact_the_proposal_does_not_state_is_a_red_tinted_tile_that_says_not_stated(label):
    tile = S.tile_html(S.KeyFact(label, None, None))
    assert tile.startswith('<div class="sc-fact sc-bad">') and '<div class="sc-fact-value sc-unstated">Not stated</div>' in tile


def test_a_detail_still_shows_under_a_value_that_is_not_stated():
    tile = S.tile_html(S.key_facts({"unit_price_rs": 59000})[0])
    assert tile.startswith('<div class="sc-fact sc-bad">') and "Not stated" in tile and "Rs 59,000 per laptop" in tile


def test_each_tile_has_its_own_icon_and_the_icons_are_decoration_only():
    text = S.facts_html(S.key_facts(WITHIN))
    icons = ["\U0001F4B0", "\U0001F6E1️", "\U0001F69A", "\U0001F50B", "\U0001F4BE"]
    positions = [text.index(icon) for icon in icons]
    assert positions == sorted(positions) and len(set(positions)) == 5
    assert text.count('aria-hidden="true"') == 5
    assert "sc-fact-icon" not in S.tile_html(S.KeyFact("Something else", "x", None))      # a label without an icon gets none


def test_a_price_over_the_budget_turns_the_price_tile_red_and_says_by_how_much():
    tile = price_tile(7398600)                                         # Rs 73,98,600 against the run's Rs 60 lakh
    assert tile.startswith('<div class="sc-fact sc-bad">')
    assert '<div class="sc-over">Rs 13.99 lakh over budget</div>' in tile
    assert "Rs 73,98,600" in tile and "Rs 59,000 per laptop" in tile and tile.index("over budget") < tile.index("per laptop")
    assert "Not stated" not in tile


def test_a_price_inside_the_budget_is_a_plain_tile():
    tile = price_tile(5600000)
    assert tile.startswith('<div class="sc-fact">') and "over budget" not in tile and "Rs 56,00,000" in tile


def test_the_budget_is_the_runs_own_not_a_fixed_one():
    assert price_tile(5600000).startswith('<div class="sc-fact">')                                      # inside the default Rs 60 lakh
    smaller = price_tile(5600000, budget_rs=4500000)                                                    # but over a Rs 45 lakh run
    assert smaller.startswith('<div class="sc-fact sc-bad">') and '<div class="sc-over">Rs 11 lakh over budget</div>' in smaller
    assert "over budget" not in price_tile(7398600, budget_rs=8000000)                                  # the dearer bid fits a bigger run


def test_a_price_exactly_on_the_budget_is_not_over_it():
    tile = price_tile(6000000)
    assert tile.startswith('<div class="sc-fact">') and "over budget" not in tile
    assert '<div class="sc-over">Rs 0.01 lakh over budget</div>' in price_tile(6001000)                 # Rs 1,000 over still says so


def test_a_run_saved_without_a_budget_never_says_over_budget():
    assert price_tile(7398600, budget_rs=None, quantity=None).startswith('<div class="sc-fact">')


def test_a_price_that_is_not_stated_says_not_stated_not_over_budget():
    tile = price_tile(None, budget_rs=4500000)
    assert tile.startswith('<div class="sc-fact sc-bad">') and "Not stated" in tile and "over budget" not in tile


def test_only_the_price_tile_ever_says_over_budget():
    over = insights.PriceFacts(7398600.0, 6000000.0, False, 1398600.0)
    tile = S.tile_html(S.KeyFact("Delivery", "6 weeks", None), over)
    assert tile.startswith('<div class="sc-fact">') and "over budget" not in tile


def test_a_price_tile_without_a_value_never_claims_to_be_over_budget_whatever_it_is_told():
    over = insights.PriceFacts(7398600.0, 6000000.0, False, 1398600.0)
    tile = S.tile_html(S.KeyFact("Total price", None, None), over)
    assert "Not stated" in tile and "over budget" not in tile


def test_one_missing_half_of_ram_and_ssd_does_not_tint_the_tile_but_says_which_half():
    tile = S.tile_html(S.key_facts({"ram_gb": 16})[4])
    assert tile.startswith('<div class="sc-fact">') and "16 GB RAM" in tile and "SSD not stated" in tile


def test_the_facts_strip_has_a_tile_per_fact_in_order_and_reddens_what_is_not_stated():
    text = S.facts_html(S.key_facts({"delivery_weeks": 6}))
    positions = [text.index(label) for label in ("Total price", "Warranty", "Delivery", "Battery", "RAM / SSD")]
    assert positions == sorted(positions)
    assert text.count('class="sc-fact-value sc-unstated">Not stated<') == 4 and text.count('<div class="sc-fact sc-bad">') == 4
    assert 'class="sc-fact-value">6 weeks<' in text and text.count('<div class="sc-fact">') == 1


def test_the_facts_strip_passes_the_budget_to_the_price_tile():
    over = insights.PriceFacts(7398600.0, 6000000.0, False, 1398600.0)
    text = S.facts_html(S.key_facts(FULL_FACTS), over)
    assert "Rs 13.99 lakh over budget" in text and text.count('<div class="sc-fact sc-bad">') == 1
    assert "over budget" not in S.facts_html(S.key_facts(FULL_FACTS))              # without a budget to compare with, nothing is said


def test_the_facts_strip_shows_the_detail_under_the_value():
    text = S.facts_html(S.key_facts(WITHIN))
    assert "Rs 56,00,000" in text and "Rs 59,000 per laptop" in text and "On-site cover" in text and ">Measured<" in text
    assert "sc-bad" not in text.split("</style>")[1]               # nothing is missing and nothing is over the budget


def test_the_criterion_table_has_the_spec_columns_in_order_with_hover_help_on_the_calculated_ones():
    text = S.criteria_html(card_of().lines)
    heads = ["Criterion", "Score", "Benchmark", "Gap", "Relative %", "Weight", "Evidence", "Justification"]
    positions = [text.index(f">{h}</th>") for h in heads]
    assert positions == sorted(positions)
    for hint in (S.BENCHMARK_HELP, S.GAP_HELP, S.RELATIVE_HELP, S.WEIGHT_HELP, S.EVIDENCE_HELP):
        assert f'title="{html.escape(hint)}"' in text
    assert text.count('<th class="sc-num"') == 5      # Score, Benchmark, Gap, Relative %, Weight are right-aligned numbers


def test_the_criterion_table_prints_marks_benchmark_gap_relative_percent_and_weight():
    text = S.criteria_html(card_of().lines)
    row = text[text.index("Implementation Plan"):]
    row = row[:row.index("</tr>")]
    assert "7 / 10" in row and ">9<" in row and ">-2<" in row and ">77.8%<" in row and ">20<" in row
    first = text[text.index("Technical Capability"):]
    first = first[:first.index("</tr>")]
    assert "9 / 10" in first and ">0<" in first and ">100.0%<" in first and ">30<" in first


def test_the_criterion_table_flags_missing_criteria_and_greys_out_the_not_stated_text():
    result = card_result()
    result["criteria"][2] = criterion(3, "Commercial Value", 20, 1, benchmark=8, evidence="Not stated in the proposal")
    result["criteria"][3] = criterion(4, "Security & Compliance", 20, 0, filled=True, evidence="Not stated in the proposal", justification="No result returned")
    text = S.criteria_html(card_of(result=result).lines)
    assert '<span class="sc-flag">Not stated</span>' in text and '<span class="sc-flag">No result returned</span>' in text
    assert text.count('<span class="sc-muted">Not stated in the proposal</span>') == 2
    assert text.count('class="sc-flag"') == 2


def test_the_criterion_table_says_n_a_for_numbers_it_does_not_have_and_none_given_for_empty_text():
    result = card_result()
    result["criteria"] = [{"criterion_id": 1, "name": "Technical Capability"}]
    text = S.criteria_html(card_of(result=result).lines)
    assert text.count(">n/a<") == 5 and text.count("None given") == 2


def test_the_criterion_table_escapes_what_the_model_wrote():
    result = card_result()
    result["criteria"][0] = criterion(1, "<i>Tech</i>", 30, 9, evidence='"quote" <b>bold</b> & more', justification="<img src=x onerror=alert(1)>")
    text = S.criteria_html(card_of(result=result).lines)
    assert "<b>" not in text and "<img" not in text and "<i>" not in text
    assert "&quot;quote&quot; &lt;b&gt;bold&lt;/b&gt; &amp; more" in text and "&lt;img src=x onerror=alert(1)&gt;" in text


def test_the_industry_table_prints_the_grade_word_in_its_colour():
    result = card_result(attributes=[
        {"label": "Warranty", "grade": "Above", "points": 100, "weight": 5, "reason": "ok"},
        {"label": "Delivery", "grade": "Meets", "points": 75, "weight": 20, "reason": "ok"},
        {"label": "Unit price", "grade": "Below", "points": 25, "weight": 20, "reason": "high"},
        {"label": "Data wiping", "grade": "Not stated", "points": 0, "weight": 10, "reason": "not described"}])
    text = S.industry_html(card_of(result=result).attributes)
    for grade in ("Above", "Meets", "Below", "Not stated"):
        colour, background = S.GRADE_COLOURS[grade]
        assert f'style="color:{colour};background:{background}">{grade}</span>' in text
    assert len({S.GRADE_COLOURS[g] for g in ("Above", "Meets", "Below", "Not stated")}) == 4
    positions = [text.index(label) for label in ("Warranty", "Delivery", "Unit price", "Data wiping")]
    assert positions == sorted(positions)
    for hint in (S.POINTS_HELP, S.SHARE_HELP):
        assert f'title="{html.escape(hint)}"' in text
    assert text.count('<th class="sc-num"') == 2


def test_an_unknown_grade_is_shown_as_it_is_in_the_not_stated_colours():
    result = card_result(attributes=[{"label": "X", "grade": "Odd", "points": 1, "weight": 1, "reason": ""}])
    colour, background = S.GRADE_COLOURS["Not stated"]
    assert f'style="color:{colour};background:{background}">Odd</span>' in S.industry_html(card_of(result=result).attributes)


def test_the_checks_table_prints_passed_or_not_passed_in_the_theme_colours():
    card = scorecards_of(supplier("BrightPath Tech", 1, eligible=False)).cards[0]
    text = S.gates_html(card.gates)
    assert f'style="color:{S.NOT_PASSED_COLOURS[0]};background:{S.NOT_PASSED_COLOURS[1]}">Not passed</span>' in text
    assert f'style="color:{S.PASSED_COLOURS[0]};background:{S.PASSED_COLOURS[1]}">Passed</span>' in text
    assert "1 year (at least 3 required)" in text and "6 weeks (at most 8 allowed)" in text
    assert text.index("Warranty") < text.index("Delivery")


def test_the_warnings_are_a_list_in_order():
    text = S.warnings_html(("First", "Second"))
    assert text.index("<li>First</li>") < text.index("<li>Second</li>") and text.count("<li>") == 2


# ---------------------------------------------------------------- R1b: what is good and what to watch out for

def boxes(text: str) -> list[str]:
    """The two cards of reasons_html: [what's good, watch out]."""
    return text.split('<div class="sc-box"')[1:]


def test_the_good_points_are_on_the_left_and_the_things_to_watch_on_the_right_as_chosen_by_insights():
    card = card_of()                                                  # marks 9, 7, 5, 9, 8 against the best 9, 9, 8, 9, 9
    good, watch = boxes(S.reasons_html(card.row.pros, card.row.cons))
    assert f"✓ {html.escape(S.GOOD_TITLE)}</div>" in good and f"⚠ {S.WATCH_TITLE}</div>" in watch
    wanted_good = ["Strong on Laptop specs (9/10, best of all bids)", "Strong on Security (9/10, best of all bids)", "Meets every must-have"]
    assert [r.text for r in card.row.pros] == wanted_good and [r.text for r in card.row.cons] == ["Weaker on Value for money (5/10; best bid 8/10)"]
    positions = [good.index(text) for text in wanted_good]
    assert positions == sorted(positions) and good.count("<li>") == 3
    assert "Weaker on Value for money (5/10; best bid 8/10)" in watch and watch.count("<li>") == 1
    assert not any(text in watch for text in wanted_good) and "Weaker on" not in good


def test_each_point_says_what_it_was_measured_against():
    row = card_of().row
    good, watch = boxes(S.reasons_html(row.pros, row.cons))
    assert '<span class="sc-basis"> · vs this batch</span>' in good and '<span class="sc-basis"> · vs requirement</span>' in good
    assert '<span class="sc-basis"> · vs this batch</span>' in watch


def test_the_lists_hold_at_most_three_points_each():
    scores = (10, 10, 10, 10, 10)
    result = card_result(scores=scores)
    for item in result["criteria"]:
        item["benchmark"], item["gap"], item["relative_pct"] = 10, 0, 100                 # the best on all five, and every must-have passed
    card = card_of(result=result)
    good, _watch = boxes(S.reasons_html(card.row.pros, card.row.cons))
    assert good.count("<li>") == insights.PROS_MAX == 3
    weak = card_result(scores=(0, 0, 0, 0, 0))
    card = card_of(result=weak)
    _good, watch = boxes(S.reasons_html(card.row.pros, card.row.cons))
    assert watch.count("<li>") == insights.CONS_MAX == 3


def test_a_must_have_that_failed_is_a_red_cross_and_other_weak_points_an_amber_warning_sign():
    card = scorecards_of(supplier("BrightPath Tech", 1, eligible=False)).cards[0]
    good, watch = boxes(S.reasons_html(card.row.pros, card.row.cons))
    cross = f'<span class="sc-ico" style="color:{theme.PILL_BAD[0]}">✗</span>Not eligible: Warranty — 1 year (at least 3 required)'
    warn = f'<span class="sc-ico" style="color:{theme.PILL_WARN[0]}">⚠</span>Weaker on Value for money'
    assert cross in watch and warn in watch and watch.index(cross) < watch.index(warn)
    tick = f'<span class="sc-ico" style="color:{theme.PILL_GOOD[0]}">✓</span>Strong on Laptop specs'
    assert tick in good and "Meets every must-have" not in good


def test_when_there_is_nothing_to_say_each_card_says_so_calmly():
    text = S.reasons_html((), ())
    good, watch = boxes(text)
    assert tab_overview.NOTHING_STANDS_OUT in good and S.NOTHING_TO_WATCH in watch
    assert "<li>" not in text and text.count('<li class="sc-basis">') == 2


# ---------------------------------------------------------------- R1b: how it did on each area

def area_lines(*marks: float, maximum: float = 10.0) -> tuple[S.Line, ...]:
    """Criterion lines for the five marking-scheme criteria with these marks (the best mark is the maximum)."""
    return S._lines({"criteria": [criterion(i, n, w, m, benchmark=maximum, max_score=maximum) for (i, n, w), m in zip(CRITERIA, marks)]})


def area_row(text: str, label: str) -> str:
    """The table row of the area with this on-screen label."""
    end = text.index(f"{label}</span></td>")
    start = text.rindex("<tr>", 0, end)
    return text[start: text.index("</tr>", start) + len("</tr>")]


@pytest.mark.parametrize("score, maximum, dots, word", [
    (0, 10, 0, "Weak"), (4, 10, 2, "Weak"), (7, 10, 4, "Average"), (10, 10, 5, "Strong"),          # out of 10, as in the change pack
    (5, 5, 5, "Strong"), (3, 5, 3, "Average"), (0, 5, 0, "Weak"), (2, 5, 2, "Weak"),               # out of 5
    (5, 10, 3, "Average"), (8, 10, 4, "Strong"), (9.5, 10, 5, "Strong")])
def test_the_meter_fills_the_dots_for_the_mark_and_the_word_follows_the_same_mark(score, maximum, dots, word):
    row = area_row(S.areas_html(area_lines(score, 1, 1, 1, 1, maximum=maximum)), "Laptop specs")
    assert (row.count(S.DOT_FILLED), row.count(S.DOT_EMPTY)) == (dots, 5 - dots)
    colour, background = theme.STRENGTH_COLOURS[word]
    assert f'<span class="sc-word" style="color:{colour};background:{background}">{word}</span>' in row
    assert f'aria-label="{dots} of 5 dots"' in row and f'title="{score:g} out of {maximum:g}"' in row


def test_filled_dots_carry_the_strength_colour_and_empty_ones_the_meter_grey():
    row = area_row(S.areas_html(area_lines(7, 1, 1, 1, 1)), "Laptop specs")
    average = theme.STRENGTH_COLOURS["Average"][0]
    assert f'<span style="color:{average}">{S.DOT_FILLED * 4}</span><span style="color:{theme.METER_EMPTY}">{S.DOT_EMPTY}</span>' in row
    row = area_row(S.areas_html(area_lines(10, 1, 1, 1, 1)), "Laptop specs")
    assert f'<span style="color:{theme.STRENGTH_COLOURS["Strong"][0]}">{S.DOT_FILLED * 5}</span><span style="color:{theme.METER_EMPTY}"></span>' in row


def test_each_area_is_shown_by_its_everyday_name_with_the_real_name_on_hover_in_scheme_order():
    text = S.areas_html(area_lines(9, 7, 5, 9, 8))
    pairs = (("Technical Capability", "Laptop specs"), ("Implementation Plan", "Delivery plan"), ("Commercial Value", "Value for money"),
             ("Security & Compliance", "Security"), ("Support & Experience", "Support"))
    for real, plain in pairs:
        assert f'<td class="sc-area-name"><span title="{html.escape(real)}">{plain}</span></td>' in text
        assert f">{html.escape(real)}<" not in text
    positions = [text.index(f">{plain}<") for _real, plain in pairs]
    assert positions == sorted(positions)


def test_a_criterion_without_an_everyday_name_shows_its_own_name_and_no_hover():
    text = S.areas_html(S._lines({"criteria": [criterion(9, "Sustainability", 5, 7)]}))
    assert '<td class="sc-area-name"><span>Sustainability</span></td>' in text


def test_not_stated_follows_an_area_the_proposal_does_not_address_or_the_model_did_not_mark():
    result = card_result()
    result["criteria"][2] = criterion(3, "Commercial Value", 20, 0, benchmark=8, evidence="Not stated in the proposal")
    result["criteria"][3] = criterion(4, "Security & Compliance", 20, 0, filled=True, evidence="Some text", justification="No result returned")
    text = S.areas_html(card_of(result=result).lines)
    assert f'<span title="{html.escape(S.NOT_STATED_HOVER)}">· not stated</span>' in area_row(text, "Value for money")
    assert f'<span title="{html.escape(S.NO_RESULT_HOVER)}">· not stated</span>' in area_row(text, "Security")
    for other in ("Laptop specs", "Delivery plan", "Support"):
        assert "not stated" not in area_row(text, other)
    assert text.count("· not stated") == 2
    assert "Weak" in area_row(text, "Security") and S.DOT_FILLED not in area_row(text, "Security")      # still marked: 0 dots, Weak


def test_a_zero_that_the_model_gave_with_evidence_is_not_called_not_stated():
    result = card_result()
    result["criteria"][1] = criterion(2, "Implementation Plan", 20, 0, benchmark=9)
    assert "not stated" not in area_row(S.areas_html(card_of(result=result).lines), "Delivery plan")


def test_marks_that_cannot_be_drawn_show_n_a_without_dots_or_a_word():
    lines = S._lines({"criteria": [{"criterion_id": 1, "name": "Technical Capability", "score": None},
                                    {"criterion_id": 2, "name": "Implementation Plan", "score": 5, "max_score": 0}]})
    text = S.areas_html(lines)
    for label in ("Laptop specs", "Delivery plan"):
        row = area_row(text, label)
        assert '<td class="sc-na">n/a</td><td></td>' in row and S.DOT_FILLED not in row and S.DOT_EMPTY not in row


def test_the_word_comes_from_the_spec_thresholds():
    line = S.Line(1, "A", 8, 10, 8, 0, 100, 10, "", "", None)
    assert S.strength_of(line) == "Strong"
    assert S.strength_of(replace(line, score=5)) == "Average" and S.strength_of(replace(line, score=4.9)) == "Weak"
    assert S.strength_of(replace(line, score=None)) is None and S.strength_of(replace(line, max_score=None)) is None
    assert S.strength_of(replace(line, max_score=0)) is None


# ---------------------------------------------------------------- R1b: the page, top to bottom

class _Column:
    def __init__(self, page: "_Page") -> None:
        self._page = page

    def metric(self, label, value, **kwargs):
        self._page.calls.append(("metric", f"{label}: {value}"))


class _Page:
    """A recording stand-in for the parts of Streamlit that one scorecard is drawn with, to check what is drawn and in what order."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.expanders: list[tuple[str, bool]] = []

    def markdown(self, text, **kwargs):
        self.calls.append(("markdown", str(text)))

    def error(self, text, **kwargs):
        self.calls.append(("error", str(text)))

    def info(self, text, **kwargs):
        self.calls.append(("info", str(text)))

    def plotly_chart(self, figure, **kwargs):
        self.calls.append(("plotly_chart", "<<chart>>"))

    def columns(self, count, **kwargs):
        return [_Column(self) for _ in range(count)]

    def expander(self, label, expanded=False, **kwargs):
        page = self
        page.expanders.append((label, expanded))
        page.calls.append(("expander", label))

        class _Open:
            def __enter__(self_inner):
                page.calls.append(("enter", label))
                return self_inner

            def __exit__(self_inner, *exc):
                page.calls.append(("exit", label))
                return False

        return _Open()


def drawn(*suppliers: dict[str, Any], pick: int = 0, **options) -> tuple[_Page, S.Scorecard]:
    page = _Page()
    card = scorecards_of(*suppliers, **options).cards[pick]
    original = S.st
    S.st = page
    try:
        S._show_card(card)
    finally:
        S.st = original
    return page, card


def split_page(page: _Page) -> tuple[str, str]:
    """(everything drawn outside the expander, everything drawn inside it)."""
    enter = next(i for i, (kind, _t) in enumerate(page.calls) if kind == "enter")
    leave = next(i for i, (kind, _t) in enumerate(page.calls) if kind == "exit")
    text = lambda calls: "\n".join(t for _k, t in calls)      # noqa: E731
    return text(page.calls[:enter]), text(page.calls[enter:leave])


def test_the_page_order_is_header_facts_good_and_bad_areas_chart_then_the_calculation():
    page, _card = drawn(supplier("Apex Systems", 1), supplier("Orbit", 2))
    assert page.calls[0][1].startswith("<style>") and '<div class="sc-head"' in page.calls[0][1]
    assert "Submitted 5 Oct 2026 · Experience rating: 3" in page.calls[1][1]
    everything = "\n".join(t for _k, t in page.calls)
    needles = ['<div class="sc-head"', "#### Key facts", '<div class="sc-facts"', '<div class="sc-two"', "#### How it did on each area",
               '<table class="sc-areas"', "#### Score vs best", "<<chart>>", S.CALCULATION_LABEL, "#### Criterion by criterion",
               '<table class="sc-table"', "#### Compared with typical laptop deals", "#### Must-haves", "#### Warnings"]
    positions = [everything.index(needle) for needle in needles]
    assert positions == sorted(positions) and len(set(positions)) == len(needles)


def test_the_page_tells_the_tiles_about_the_runs_budget():
    page, _card = drawn(supplier("Apex Systems", 1))                                      # Rs 73,98,600 in a run with a Rs 60 lakh budget
    outside, _inside = split_page(page)
    assert "Rs 13.99 lakh over budget" in outside
    page, _card = drawn(supplier("Apex Systems", 1), budget_rs=8000000)
    assert "over budget" not in split_page(page)[0]


def test_the_dots_are_explained_under_the_areas():
    page, _card = drawn(supplier("Apex Systems", 1))
    outside, inside = split_page(page)
    assert html.escape(S.AREAS_NOTE) in outside and "Five dots mean full marks" in outside and S.AREAS_NOTE not in inside


def test_the_calculation_is_closed_and_holds_the_f11_tables_the_scores_and_the_warnings():
    result = card_result(warnings=["Unit price estimated from total"])
    page, card = drawn(supplier("Apex Systems", 1, overall=77.0, ppi=86.94, industry_score=62.5, result=result))
    assert page.expanders == [(S.CALCULATION_LABEL, False)]
    outside, inside = split_page(page)
    assert inside.index("Overall /100: 77.00") < inside.index("Score vs other bids (PPI): 86.94") < inside.index("Industry score: 62.50")
    headings = [line for line in inside.split("\n") if line.startswith("####")]
    assert headings == ["#### Criterion by criterion", "#### Compared with typical laptop deals", "#### Must-haves", "#### Warnings"]
    assert S.criteria_html(card.lines) in inside and S.industry_html(card.attributes) in inside
    assert S.gates_html(card.gates) in inside and S.warnings_html(card.warnings) in inside
    assert "Industry score 62.50 out of 100." in inside and html.escape(S.CRITERIA_NOTE) in inside
    assert "<table" not in outside.replace('<table class="sc-areas"', "")           # the only table outside is the areas list
    assert "Evidence" not in outside and "Benchmark" not in outside and "Relative %" not in outside


def test_the_headline_scores_are_in_the_header_and_all_three_numbers_are_in_the_calculation():
    page, _card = drawn(supplier("Apex Systems", 1, overall=77.0, ppi=86.94, industry_score=62.5))
    outside, inside = split_page(page)
    assert "86.94" in outside and "77.00" not in outside and "62.50" not in outside        # only the one big number up top
    assert "77.00" in inside and "86.94" in inside and "62.50" in inside


def test_a_supplier_with_no_warnings_says_so_inside_the_calculation():
    page, _card = drawn(supplier("Apex Systems", 1))
    outside, inside = split_page(page)
    assert S.NO_WARNINGS_TEXT in inside and S.NO_WARNINGS_TEXT not in outside


def test_a_failed_evaluation_keeps_its_red_message_at_the_top_outside_the_calculation():
    result = card_result(evaluation_failed=True, warnings=["Evaluation failed: bad reply."])
    page, _card = drawn(supplier("Vertexa Solutions", 1, eligible=False, result=result))
    error_at = next(i for i, (kind, _t) in enumerate(page.calls) if kind == "error")
    enter = next(i for i, (kind, _t) in enumerate(page.calls) if kind == "enter")
    key_facts_at = next(i for i, (_k, t) in enumerate(page.calls) if t == "#### Key facts")
    assert page.calls[error_at][1] == S.EVALUATION_FAILED_TEXT and error_at < key_facts_at < enter
    outside, inside = split_page(page)
    assert "Evaluation failed: bad reply." in inside and "Evaluation failed: bad reply." not in outside
    assert '<div class="sc-two"' not in outside and tab_overview.NOTHING_TO_SHOW in outside        # no praise or blame for a failed run
    assert "<strong>Not eligible:</strong> the evaluation failed" in outside


def test_the_old_screen_words_are_gone_and_the_new_ones_are_there():
    page, _card = drawn(supplier("Apex", 1, eligible=False), supplier("Nexa", 2))
    everything = "\n".join(t for _k, t in page.calls)
    for old in ("Mandatory checks", "Peer score", "Industry comparison", ">Eligible<"):
        assert old not in everything, old
    for new in ("#### Must-haves", "Score vs other bids (PPI)", "#### Compared with typical laptop deals", "Not eligible"):
        assert new in everything, new


def test_a_supplier_with_nothing_saved_gets_friendly_notes_not_blanks():
    page, _card = drawn(supplier("Apex Systems", 1, result={}))
    assert ("info", S.NO_CRITERIA_TEXT) in page.calls and ("info", S.NO_CHART_TEXT) in page.calls
    outside, inside = split_page(page)
    assert S.NO_INDUSTRY_TEXT in inside and S.NO_CHECKS_TEXT in inside and S.NO_WARNINGS_TEXT in inside
    assert not any(kind == "plotly_chart" for kind, _t in page.calls) and '<table class="sc-areas"' not in outside


def test_a_card_with_marks_draws_the_chart_once_between_the_areas_and_the_calculation():
    page, _card = drawn(supplier("Apex Systems", 1))
    kinds = [kind for kind, _t in page.calls]
    assert kinds.count("plotly_chart") == 1
    assert next(i for i, k in enumerate(kinds) if k == "plotly_chart") < kinds.index("expander")


# ---------------------------------------------------------------- a real batch, read back

class _FakeBackend:
    def __init__(self, *replies: str) -> None:
        self.replies, self.calls = list(replies), 0

    def completion(self, **kwargs):
        text = self.replies[self.calls]
        self.calls += 1
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))], usage=SimpleNamespace(total_tokens=1000))

    def supports_json_mode(self, settings) -> bool:
        return True

    def cost(self, response) -> float:
        return 0.01


FIVE = [("Apex Systems", date(2026, 10, 5), 4, [9, 7, 5, 9, 8]), ("BrightPath Tech", date(2026, 10, 7), 2, [5, 4, 8, 3, 3]),
        ("NexaWorks", date(2026, 10, 6), 4, [7, 9, 7, 7, 9]), ("Horizon Datacom", date(2026, 10, 6), 3, [7, 9, 7, 7, 9]),
        ("Orbit Digital", date(2026, 10, 8), 5, [7, 5, 7, 6, 9])]
GOOD_FACTS = {"warranty_years": 3, "warranty_type": "on-site", "bis_named": True, "price_table_present": True, "delivery_weeks": 6,
              "battery_hours": 11.5, "battery_measured": True}
BAD_FACTS = {"warranty_years": 1, "warranty_type": "carry-in", "bis_named": None, "price_table_present": True, "delivery_weeks": 6}


def model_reply(scores: list[int], facts: dict[str, Any], *, skip: int | None = None) -> str:
    return json.dumps({"supplier_name": "Supplier A", "criteria": [
        {"criterion_id": i, "score": s, "justification": f"why {i}", "evidence": f"quote {i} (Page {i})"}
        for i, s in enumerate(scores, start=1) if i != skip], "extracted_facts": facts, "risks": [], "overall_summary": "ok"})


def run_batch(replies: list[str] | None = None) -> orchestrator.RunResult:
    """Five suppliers through the real orchestrator. `replies` is the model's answers in the order they are asked for."""
    proposals = []
    for name, when, rating, _scores in FIVE:
        pdf = pdf_tool.ExtractedPdf("0" * 64, (f"Offer from {name}.",), "pypdf")
        proposals.append(orchestrator.Proposal(f"{name.lower().replace(' ', '_')}.pdf", name, when, rating, pdf))
    texts = replies or [model_reply(scores, BAD_FACTS if name == "BrightPath Tech" else GOOD_FACTS) for name, _w, _r, scores in FIVE]
    return orchestrator.run_evaluation(
        proposals, industry.PurchaseTerms(6000000, 100), client.LLMSettings("OpenAI", "test-model", "test-key-abc123"), "Sri",
        backend=_FakeBackend(*texts), sleep=lambda _s: None, now=lambda: datetime(2026, 10, 23, 9, 30, tzinfo=timezone.utc))


def read_back(run_id: str) -> S.Scorecards:
    with repository.connection() as conn:
        run = repository.get_run(conn, run_id)
        stored = repository.list_supplier_results(conn, run_id)
    assert run is not None
    return S.build_scorecards(run, stored)


def test_a_batch_that_was_really_run_reads_back_into_every_part_of_the_scorecard():
    result = run_batch()
    assert result.status == "Evaluated"
    cards = {c.row.name: c for c in read_back(result.run_id).cards}
    apex = cards["Apex Systems"]
    assert [(l.score, l.benchmark, l.gap) for l in apex.lines] == [(9, 9, 0), (7, 9, -2), (5, 8, -3), (9, 9, 0), (8, 9, -1)]
    assert [round(l.relative_pct or 0, 1) for l in apex.lines] == [100.0, 77.8, 62.5, 100.0, 88.9]
    assert [l.weight for l in apex.lines] == [30.0, 20.0, 20.0, 20.0, 10.0]
    assert apex.lines[0].evidence == "quote 1 (Page 1)" and apex.lines[0].justification == "why 1"
    assert [f.value for f in apex.facts] == [None, "3 years", "6 weeks", "11.5 hours", None]
    assert len(apex.attributes) == 8
    assert all(g.passed for g in apex.gates) and len(apex.gates) == 4
    bright = cards["BrightPath Tech"]
    assert [(g.name, g.passed) for g in bright.gates if not g.passed] == [("Warranty", False), ("Certification", False)]
    assert not bright.row.eligible and bright.row.why_not == ("Warranty: 1 year (at least 3 required)", "Certification: Not stated")


def ordinary_replies() -> list[str]:
    return [model_reply(scores, BAD_FACTS if name == "BrightPath Tech" else GOOD_FACTS) for name, _w, _r, scores in FIVE]


def test_a_criterion_missing_from_the_reply_shows_up_flagged_with_its_warning():
    replies = ordinary_replies()
    replies[4] = model_reply(FIVE[4][3], GOOD_FACTS, skip=4)       # Orbit Digital is evaluated last
    orbit = {c.row.name: c for c in read_back(run_batch(replies).run_id).cards}["Orbit Digital"]
    assert [l.flag for l in orbit.lines] == [None, None, None, "No result returned", None]
    assert orbit.lines[3].score == 0 and orbit.lines[3].justification == "No result returned"
    assert any("Security & Compliance" in w and "missing from the reply" in w for w in orbit.warnings)


def test_a_supplier_whose_evaluation_failed_is_marked_scored_zero_and_has_its_warning():
    replies = ordinary_replies()
    replies[2:3] = ["this is not json", "still not json"]            # NexaWorks gets two unusable replies (the second is the retry)
    cards = {c.row.name: c for c in read_back(run_batch(replies).run_id).cards}
    nexa = cards["NexaWorks"]
    assert nexa.evaluation_failed and all(l.score == 0 for l in nexa.lines) and not nexa.row.eligible
    assert any(w.startswith("Evaluation failed") for w in nexa.warnings)
    assert not cards["Apex Systems"].evaluation_failed and not any(w.startswith("Evaluation failed") for w in cards["Apex Systems"].warnings)


# ---------------------------------------------------------------- the real app (needs Streamlit's AppTest)

def start() -> Any:
    app = AppTest.from_file(str(APP_PATH), default_timeout=60)
    app.run()
    return app


def scorecards_tab(app):
    return app.tabs[3]


def shown(block) -> str:
    return "\n".join(str(item.value) for kind in ("markdown", "caption", "info", "error", "warning", "success") for item in getattr(block, kind))


def open_run(*suppliers: dict[str, Any], run_id: str = "RUN-TEST-SCORECARDS") -> Any:
    store_run(*suppliers, run_id=run_id)
    app = start()
    app.session_state[tab_new_evaluation.SHOWN_RUN_KEY] = run_id
    app.run()
    return app


@needs_app_test
def test_before_any_run_the_scorecards_tab_explains_what_to_do():
    app = start()
    assert not app.exception
    text = shown(scorecards_tab(app))
    assert "No evaluation yet" in text and "key facts" in text
    assert not scorecards_tab(app).selectbox and not scorecards_tab(app).get("plotly_chart")


@needs_app_test
def test_a_saved_run_shows_the_picker_the_recommended_supplier_and_every_section():
    app = open_run(supplier("Apex Systems", 1, eligible=False), supplier("NexaWorks", 2), supplier("Orbit Digital", 3))
    assert not app.exception
    tab = scorecards_tab(app)
    picker = tab.selectbox(key="sc_supplier_RUN-TEST-SCORECARDS")
    assert picker.options == ["1. Apex Systems", "2. NexaWorks", "3. Orbit Digital"] and picker.value == "2. NexaWorks"
    text = shown(tab)
    for expected in ("Supplier scorecard · Ranked 2 of 3", "NexaWorks", "Recommended", "Meets every must-have", "Key facts", "Rs 73,98,600",
                     "Rs 13.99 lakh over budget", "How it did on each area", "Laptop specs", "Score vs best", "Warnings", "No warnings for this supplier.",
                     "Submitted 5 Oct 2026 · Experience rating: 3", "Criterion by criterion", "Compared with typical laptop deals", "Must-haves"):
        assert expected in text
    assert html.escape(S.GOOD_TITLE) in text and S.WATCH_TITLE in text
    for in_a_table in ("Implementation Plan", "Rs 73,986 per laptop", "6 weeks (at most 8 allowed)"):    # criteria, industry and checks tables
        assert in_a_table in text
    for old_words in ("Mandatory checks", "Peer score", "Industry comparison"):
        assert old_words not in text
    assert [m.label for m in tab.metric] == ["Overall /100", "Score vs other bids (PPI)", "Industry score"]
    assert len(tab.get("plotly_chart")) == 1


@needs_app_test
def test_choosing_another_supplier_shows_that_suppliers_scorecard():
    app = open_run(supplier("Apex Systems", 1), supplier("BrightPath Tech", 2, eligible=False))
    assert "Apex Systems" in shown(scorecards_tab(app)) and "Not passed" not in shown(scorecards_tab(app))
    app.tabs[3].selectbox(key="sc_supplier_RUN-TEST-SCORECARDS").select_index(1)
    app.run()
    assert not app.exception
    text = shown(scorecards_tab(app))
    assert "BrightPath Tech" in text and "Ranked 2 of 2" in text and "Not passed" in text and "1 year (at least 3 required)" in text
    assert "Recommended" not in text


@needs_app_test
def test_a_supplier_with_a_failed_check_a_missing_criterion_and_a_warning_shows_all_three():
    result = card_result(failed=(("Price table", "No price table"),), warnings=["Criterion 4 (Security & Compliance) was missing from the reply and was set to 0."])
    result["criteria"][3] = criterion(4, "Security & Compliance", 20, 0, filled=True, evidence="Not stated in the proposal", justification="No result returned")
    app = open_run(supplier("Vertexa Solutions", 1, eligible=False, result=result))
    assert not app.exception
    text = shown(scorecards_tab(app))
    assert "No price table" in text and "Not passed" in text
    assert "No result returned" in text and "Not stated in the proposal" in text and "\u00b7 not stated" in text
    assert "was missing from the reply and was set to 0." in text and "No warnings for this supplier." not in text
    assert "<strong>Not eligible:</strong>" in text


@needs_app_test
def test_a_failed_evaluation_says_so_at_the_top():
    app = open_run(supplier("Vertexa Solutions", 1, eligible=False, result=card_result(evaluation_failed=True, warnings=["Evaluation failed: bad reply."])))
    assert not app.exception
    assert S.EVALUATION_FAILED_TEXT in " ".join(str(e.value) for e in scorecards_tab(app).error)
    assert "Evaluation failed: bad reply." in shown(scorecards_tab(app))


@needs_app_test
def test_a_supplier_with_nothing_saved_gets_friendly_messages_not_an_error():
    app = open_run(supplier("Apex Systems", 1, result={}))
    assert not app.exception
    text = shown(scorecards_tab(app))
    for expected in (S.NO_CHART_TEXT, "No criterion results were saved", S.NO_INDUSTRY_TEXT, S.NO_CHECKS_TEXT):
        assert expected in text
    assert not scorecards_tab(app).get("plotly_chart")


@needs_app_test
def test_each_run_gets_its_own_picker_so_a_new_run_starts_on_its_own_default():
    app = open_run(supplier("Apex Systems", 1), supplier("NexaWorks", 2))
    app.tabs[3].selectbox(key="sc_supplier_RUN-TEST-SCORECARDS").select_index(1)
    app.run()
    store_run(supplier("Orbit Digital", 1), supplier("Horizon Datacom", 2), run_id="RUN-TEST-SECOND")
    app.session_state[tab_new_evaluation.SHOWN_RUN_KEY] = "RUN-TEST-SECOND"
    app.run()
    assert not app.exception
    picker = scorecards_tab(app).selectbox(key="sc_supplier_RUN-TEST-SECOND")
    assert picker.options == ["1. Orbit Digital", "2. Horizon Datacom"] and picker.value == "1. Orbit Digital"


@needs_app_test
def test_a_database_problem_gives_a_friendly_message_not_a_stack_trace(monkeypatch):
    def locked():
        raise sqlite3.OperationalError("database is locked")

    app = start()
    app.session_state[tab_new_evaluation.SHOWN_RUN_KEY] = "RUN-ANY"
    monkeypatch.setattr(S, "repository", SimpleNamespace(connection=locked))
    app.run()
    assert not app.exception
    assert "could not be read" in " ".join(str(e.value) for e in scorecards_tab(app).error)


@needs_app_test
def test_a_run_that_cannot_be_found_gives_a_friendly_message():
    app = start()
    app.session_state[tab_new_evaluation.SHOWN_RUN_KEY] = "RUN-NOPE"
    app.run()
    assert not app.exception
    assert "could not be found" in shown(scorecards_tab(app))
