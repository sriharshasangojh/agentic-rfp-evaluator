"""Scorecards tab tests (feature F11).

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
from tools import industry, pdf_tool  # noqa: E402
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


def supplier(name: str, rank: int, *, eligible: bool = True, result: dict[str, Any] | None = None, industry_score: float = 75.0,
             ppi: float = 80.0, overall: float = 70.0, rating: int | None = 3, when: str = "2026-10-05") -> dict[str, Any]:
    failed = () if eligible else (("Warranty", "1 year (at least 3 required)"),)
    return dict(name=name, rank=rank, eligible=eligible, industry=industry_score, ppi=ppi, overall=overall, rating=rating, when=when,
                result=result if result is not None else card_result(failed=failed))


_run_numbers = itertools.count(1)


def store_run(*suppliers: dict[str, Any], run_id: str | None = None) -> tuple[repository.RunRecord, list[repository.StoredSupplier]]:
    """Save a run by hand. Each call gets its own run id unless one is given (every test starts with an empty database)."""
    run_id = run_id or f"RUN-TEST-{next(_run_numbers):03d}"
    with repository.connection() as conn:
        repository.create_run(
            conn, run_id=run_id, created_at="2026-10-23T09:30:00+00:00", provider="OpenAI", model="test-model", temperature=0,   # type: ignore[arg-type]
            template_id=repository.DEFAULT_TEMPLATE_ID,
            criteria_snapshot_json=json.dumps([{"criterion_id": i, "name": n, "weight": w, "max_score": 10.0} for i, n, w in CRITERIA]),
            reviewer_name="Sri", budget_rs=6000000, quantity=100)   # type: ignore[arg-type]
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
    "facts": lambda card: S.facts_html(card.facts),
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


def test_the_header_names_the_supplier_its_rank_and_whether_it_is_eligible():
    result = scorecards_of(supplier("Apex Systems", 1), supplier("Orbit", 2))
    text = S.header_html(result.cards[0])
    assert "Apex Systems" in text and "Ranked 1 of 2" in text and ">Eligible<" in text and "Recommended" in text and "title=" not in text
    assert "Recommended" not in S.header_html(result.cards[1])


def test_a_not_eligible_header_shows_the_failed_checks_on_hover_and_is_never_recommended():
    card = scorecards_of(supplier("BrightPath Tech", 1, eligible=False)).cards[0]
    text = S.header_html(card)
    assert ">Not eligible<" in text and "Recommended" not in text
    assert 'title="Warranty: 1 year (at least 3 required)"' in text


def test_the_header_badge_is_green_when_eligible_and_red_when_not_like_the_overview():
    eligible = S.header_html(scorecards_of(supplier("Apex", 1), run_id="RUN-TEST-GREEN").cards[0])
    not_eligible = S.header_html(scorecards_of(supplier("Apex", 1, eligible=False), run_id="RUN-TEST-RED").cards[0])
    green, red = tab_overview.ELIGIBLE_COLOURS, tab_overview.NOT_ELIGIBLE_COLOURS
    assert f'style="color:{green[0]};background:{green[1]}">Eligible</span>' in eligible
    assert f'style="color:{red[0]};background:{red[1]}"' in not_eligible and ">Not eligible</span>" in not_eligible


def test_a_header_with_several_failed_checks_lists_each_on_its_own_line_of_the_hover_text():
    result = card_result(failed=(("Warranty", "1 year (at least 3 required)"), ("Certification", "Not stated")))
    text = S.header_html(scorecards_of(supplier("BrightPath Tech", 1, eligible=False, result=result)).cards[0])
    assert 'title="Warranty: 1 year (at least 3 required)&#10;Certification: Not stated"' in text and "\n" not in text


def test_names_from_a_proposal_cannot_inject_html():
    evil = "<script>alert(1)</script>"
    run, stored = store_run(supplier(evil, 1, result=card_result(warnings=[evil], facts={**FULL_FACTS, "laptop_model": evil})))
    card = S.build_scorecards(run, stored).cards[0]
    for html_text in (S.header_html(card), S.warnings_html(card.warnings)):
        assert "<script>" not in html_text and "&lt;script&gt;" in html_text


def test_the_facts_strip_has_a_tile_per_fact_and_greys_out_what_is_not_stated():
    text = S.facts_html(S.key_facts({"delivery_weeks": 6}))
    positions = [text.index(label) for label in ("Total price", "Warranty", "Delivery", "Battery", "RAM / SSD")]
    assert positions == sorted(positions)
    assert text.count('class="sc-fact-value sc-unstated">Not stated<') == 4
    assert 'class="sc-fact-value">6 weeks<' in text


def test_the_facts_strip_shows_the_detail_under_the_value():
    text = S.facts_html(S.key_facts(FULL_FACTS))
    assert "Rs 73,98,600" in text and "Rs 59,000 per laptop" in text and "On-site cover" in text and ">Measured<" in text


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
              "grand_total_rs": 5900000, "battery_hours": 11.5, "battery_measured": True}
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
    assert [f.value for f in apex.facts] == ["Rs 59,00,000", "3 years", "6 weeks", "11.5 hours", None]
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
    for expected in ("Supplier scorecard · Ranked 2 of 3", "NexaWorks", "Recommended", "Key facts", "Score vs best", "Criterion by criterion",
                     "Industry comparison", "Mandatory checks", "Warnings", "Rs 73,98,600", "No warnings for this supplier.",
                     "Submitted 5 Oct 2026 · Experience rating: 3"):
        assert expected in text
    for in_a_table in ("Implementation Plan", "Rs 73,986 per laptop", "6 weeks (at most 8 allowed)"):    # criteria, industry and checks tables
        assert in_a_table in text
    assert [m.label for m in tab.metric] == ["Overall /100", "Peer score (PPI)", "Industry score"]
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
    assert "No result returned" in text and "Not stated in the proposal" in text
    assert "was missing from the reply and was set to 0." in text and "No warnings for this supplier." not in text
    assert ">Not eligible<" in text


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
