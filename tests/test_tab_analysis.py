"""Analysis tab tests (feature F18): the heatmap, price against quality and what-if data, figures, table and page order.

Saved runs are written straight into a temporary database (the helpers of the Overview tests), so every combination can be set up
exactly. The spec's fixture (spec 16.1) is stored as a real run to check the what-if end to end. No API key or network is used.
"""
from __future__ import annotations

import ast
import html
import re
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("streamlit")
pytest.importorskip("plotly")

from db import seed  # noqa: E402
from tests.test_tab_overview import result_for, seeded_gates, store_run, supplier  # noqa: E402
from tools import insights  # noqa: E402
from ui import tab_analysis, theme  # noqa: E402

FIXTURE = (("Apex Systems", (9, 7, 5, 9, 8), "2026-10-05", 4), ("BrightPath Tech", (5, 4, 8, 3, 3), "2026-10-07", 2),
           ("NexaWorks", (7, 9, 7, 7, 9), "2026-10-06", 4), ("Horizon Datacom", (7, 9, 7, 7, 9), "2026-10-06", 3),
           ("Orbit Digital", (7, 5, 7, 6, 9), "2026-10-08", 5))
WITHIN, OVER = 5_600_000, 7_398_600


@pytest.fixture(autouse=True)
def seeded_database():
    seed.ensure_database()


def entry(name, scores, when, rating, rank, *, total=None, eligible=True, overall=70.0):
    result = result_for(scores, gate_details=seeded_gates() if eligible else seeded_gates(warranty="1 year (at least 3 required)"))
    if total is not None:
        result["extracted_facts"] = {"grand_total_rs": total}
    return supplier(name, rank, eligible=eligible, overall=overall, rating=rating, when=when, result=result)


def fixture_run(**options):
    return store_run(*(entry(n, s, w, r, i + 1, total=WITHIN + i * 400_000, overall=80.0 - i) for i, (n, s, w, r) in enumerate(FIXTURE)), **options)


def analysis_of(*suppliers, **options) -> tab_analysis.Analysis:
    run, stored = store_run(*suppliers, **options)
    return tab_analysis.build_analysis(run, stored)


def fixture_analysis(**options) -> tab_analysis.Analysis:
    run, stored = fixture_run(**options)
    return tab_analysis.build_analysis(run, stored)


# ---------------------------------------------------------------- colours

def test_a_supplier_keeps_its_colour_when_the_ranks_change():
    a = analysis_of(entry("Beta", (5,) * 5, "2026-10-01", 3, 1), entry("Alpha", (5,) * 5, "2026-10-01", 3, 2))
    b = analysis_of(entry("Beta", (5,) * 5, "2026-10-01", 3, 2), entry("Alpha", (5,) * 5, "2026-10-01", 3, 1))
    assert {e.name: e.colour for e in a.entries} == {e.name: e.colour for e in b.entries}
    assert {e.name: e.colour for e in a.entries} == {"Alpha": theme.SUPPLIER_COLOURS[0], "Beta": theme.SUPPLIER_COLOURS[1]}


def test_more_suppliers_than_colours_start_again_from_the_first():
    names = [f"S{i:02d}" for i in range(9)]
    analysis = analysis_of(*(entry(n, (5,) * 5, "2026-10-01", 3, i + 1) for i, n in enumerate(names)))
    colours = {e.name: e.colour for e in analysis.entries}
    assert colours["S07"] == colours["S00"] and len(set(colours.values())) == len(theme.SUPPLIER_COLOURS)


# ---------------------------------------------------------------- the heatmap

def test_the_heatmap_has_a_row_per_supplier_in_rank_order_and_a_column_per_criterion():
    data = fixture_analysis().heatmap
    assert data.suppliers == tuple(n for n, *_ in FIXTURE)
    assert data.criteria == ("Technical Capability", "Implementation Plan", "Commercial Value", "Security & Compliance", "Support & Experience")
    assert data.marks[0] == (9, 7, 5, 9, 8) and data.marks[2] == (7, 9, 7, 7, 9)
    assert all(m == 10.0 for row in data.maxima for m in row)


def test_a_failed_evaluation_has_no_row_and_is_named():
    failed = supplier("Broken", 3, eligible=False, result={"evaluation_failed": True, "criteria": []})
    analysis = analysis_of(entry("Apex", (9,) * 5, "2026-10-05", 3, 1), failed)
    assert analysis.heatmap.suppliers == ("Apex",) and analysis.failed == ("Broken",)


def test_no_heatmap_when_nobody_has_marks():
    failed = supplier("Broken", 1, eligible=False, result={"evaluation_failed": True, "criteria": []})
    analysis = analysis_of(failed)
    assert analysis.heatmap is None and analysis.scatter is None and analysis.what_if is None


def test_a_criterion_a_supplier_has_no_mark_for_is_an_empty_cell():
    short = entry("Short", (5,) * 5, "2026-10-06", 3, 2)
    short["result"]["criteria"] = short["result"]["criteria"][:4]
    data = analysis_of(entry("Apex", (9,) * 5, "2026-10-05", 3, 1), short).heatmap
    assert data.marks[1] == (5, 5, 5, 5, None) and data.maxima[1][4] is None


def test_the_heatmap_figure_uses_the_light_to_dark_theme_scale_and_prints_the_marks():
    figure = tab_analysis.heatmap_figure(fixture_analysis().heatmap)
    trace = figure.data[0]
    assert [stop[1] for stop in trace.colorscale] == [theme.HEATMAP_LOW, theme.HEATMAP_HIGH]
    assert trace.zmin == 0 and trace.zmax == 100 and list(trace.z[0]) == [90.0, 70.0, 50.0, 90.0, 80.0]
    notes = figure.layout.annotations
    assert list(trace.x) == list(fixture_analysis().heatmap.criteria) and list(trace.y) == [n for n, *_ in FIXTURE]       # criteria across, suppliers down
    assert figure.layout.yaxis.autorange == "reversed"                                                                       # best rank on top
    assert len(notes) == 25 and notes[0]["text"] == "9"
    assert notes[0]["font"]["color"] == "#FFFFFF" and notes[2]["font"]["color"] == theme.TEXT      # dark cell: white number, light cell: dark number


def test_an_empty_cell_gets_no_number():
    short = entry("Short", (5,) * 5, "2026-10-06", 3, 2)
    short["result"]["criteria"] = short["result"]["criteria"][:4]
    figure = tab_analysis.heatmap_figure(analysis_of(entry("Apex", (9,) * 5, "2026-10-05", 3, 1), short).heatmap)
    assert len(figure.layout.annotations) == 9 and figure.data[0].z[1][4] is None


# ---------------------------------------------------------------- price against quality

def test_the_scatter_has_a_point_per_supplier_with_a_total_and_names_the_others():
    data = analysis_of(entry("Apex", (9,) * 5, "2026-10-05", 3, 1, total=WITHIN, overall=77.0), entry("Nexa", (7,) * 5, "2026-10-06", 3, 2)).scatter
    assert [e.name for e in data.entries] == ["Apex"] and data.not_stated == ("Nexa",) and data.budget_rs == 6000000.0


def test_no_scatter_without_a_budget_or_without_any_total():
    assert analysis_of(entry("Apex", (9,) * 5, "2026-10-05", 3, 1, total=WITHIN), budget=None).scatter is None
    assert analysis_of(entry("Apex", (9,) * 5, "2026-10-05", 3, 1)).scatter is None


def test_the_scatter_figure_puts_price_across_and_score_up_with_a_dashed_budget_line():
    figure = tab_analysis.scatter_figure(analysis_of(
        entry("Apex", (9,) * 5, "2026-10-05", 3, 1, total=WITHIN, overall=77.0),
        entry("Nexa", (7,) * 5, "2026-10-06", 3, 2, total=OVER, overall=60.5, eligible=False)).scatter)
    apex, nexa = figure.data
    assert (list(apex.x), list(apex.y)) == ([56.0], [77.0]) and (list(nexa.x), list(nexa.y)) == ([73.986], [60.5])
    assert apex.marker["symbol"] == "circle" and nexa.marker["symbol"] == "x"          # not eligible: a cross
    assert apex.marker["color"] != nexa.marker["color"]
    line = figure.layout.shapes[0]
    assert (line.x0, line.x1) == (60.0, 60.0) and line.line.dash == "dash" and line.line.color == theme.BUDGET_LINE
    low, high = figure.layout.xaxis.range
    assert low < 56.0 and high > 73.986 and list(figure.layout.yaxis.range) == [0, 100]


def test_the_scatter_axis_includes_the_budget_when_every_supplier_is_far_from_it():
    figure = tab_analysis.scatter_figure(analysis_of(entry("Apex", (9,) * 5, "2026-10-05", 3, 1, total=1_000_000)).scatter)
    low, high = figure.layout.xaxis.range
    assert low < 10.0 and high > 60.0


# ---------------------------------------------------------------- what if

def test_the_what_if_uses_the_run_weights_and_the_eligible_suppliers():
    result = fixture_analysis().what_if
    assert result.current_weight == 20 and result.weights == insights.WHAT_IF_WEIGHTS
    assert result.leaders[result.weights.index(20)] == "Apex Systems" and result.leaders[result.weights.index(30)] == "NexaWorks"
    assert result.changes[0].from_leader == "Apex Systems" and result.changes[0].to_leader == "NexaWorks"


def test_a_supplier_that_fails_a_must_have_does_not_lead_the_what_if():
    run, stored = store_run(*(entry(n, s, w, r, i + 1, eligible=(n != "Apex Systems")) for i, (n, s, w, r) in enumerate(FIXTURE)))
    result = tab_analysis.build_analysis(run, stored).what_if
    assert "Apex Systems" not in result.leaders and "Apex Systems" in [l.name for l in result.lines]


def test_there_is_no_what_if_without_a_commercial_value_criterion():
    odd = entry("Apex", (9,) * 5, "2026-10-05", 3, 1)
    for item in odd["result"]["criteria"]:
        item["name"] = {"Commercial Value": "Cost"}.get(item["name"], item["name"])
    assert analysis_of(odd).what_if is None


def test_the_price_criterion_is_found_whatever_its_capitals():
    odd = entry("Apex", (9,) * 5, "2026-10-05", 3, 1)
    for item in odd["result"]["criteria"]:
        item["name"] = item["name"].upper()
    assert analysis_of(odd).what_if is not None


def test_a_supplier_with_other_criteria_is_left_out_of_the_what_if_and_named():
    short = entry("Short", (5,) * 5, "2026-10-06", 3, 2)
    short["result"]["criteria"] = short["result"]["criteria"][:4]
    analysis = analysis_of(entry("Apex", (9,) * 5, "2026-10-05", 3, 1), short)
    assert analysis.left_out == ("Short",) and [l.name for l in analysis.what_if.lines] == ["Apex"]


def test_a_mark_outside_its_range_gives_no_chart_instead_of_an_error():
    bad = entry("Apex", (9,) * 5, "2026-10-05", 3, 1)
    bad["result"]["criteria"][0]["score"] = 99
    assert analysis_of(bad).what_if is None


def test_the_what_if_figure_has_a_line_per_supplier_in_its_colour_and_a_dashed_line_at_this_runs_weight():
    analysis = fixture_analysis()
    figure = tab_analysis.what_if_figure(analysis.what_if, {e.name: e.colour for e in analysis.entries})
    assert len(figure.data) == 5 and list(figure.data[0].x) == [10, 15, 20, 25, 30, 35, 40]
    assert figure.data[0].line["color"] == next(e.colour for e in analysis.entries if e.name == figure.data[0].name)
    line = figure.layout.shapes[0]
    assert (line.x0, line.x1) == (20, 20) and line.line.dash == "dash"


# ---------------------------------------------------------------- words and table

def test_the_sentence_says_where_the_leader_changes():
    sentence = tab_analysis.what_if_sentence(fixture_analysis().what_if)
    assert "The leader changes from Apex Systems to NexaWorks when the price weight goes from" in sentence and sentence.count("%") >= 2


def test_the_sentence_when_one_supplier_leads_everywhere():
    one = analysis_of(entry("Apex", (9,) * 5, "2026-10-05", 3, 1)).what_if
    assert tab_analysis.what_if_sentence(one) == "Apex leads at every price weight from 10% to 40%."


def test_the_sentence_when_nobody_is_eligible():
    none = analysis_of(entry("Apex", (9,) * 5, "2026-10-05", 3, 1, eligible=False)).what_if
    assert tab_analysis.what_if_sentence(none) == tab_analysis.NO_LEADER_TEXT


def _what_if(leaders):
    weights = insights.WHAT_IF_WEIGHTS
    changes = tuple(insights.LeaderChange(weights[i], weights[i + 1], leaders[i], leaders[i + 1]) for i in range(6) if leaders[i] != leaders[i + 1])
    return insights.WhatIf(weights, (insights.WhatIfLine("A", (1.0,) * 7),), tuple(leaders), changes, 20.0)


def test_the_sentence_when_a_leader_appears_or_disappears():
    appears = tab_analysis.what_if_sentence(_what_if([None, None, "A", "A", "A", "A", "A"]))
    assert appears == "From 20% on, A is the leader."
    gone = tab_analysis.what_if_sentence(_what_if(["A", "A", "A", "A", None, None, None]))
    assert gone == "From 30% on, no supplier that meets every must-have is left to lead."


def test_the_table_has_a_row_per_weight_a_column_per_supplier_and_tags_the_rows_where_the_leader_changes():
    result = fixture_analysis().what_if
    table = tab_analysis.what_if_table_html(result)
    assert table.count("<tr") == 1 + 7 and table.count(tab_analysis.CHANGE_TAG) == len(result.changes) and "\n" not in table
    assert [h for h in re.findall(r"<th>(.*?)</th>", table)] == ["Price weight", *(n for n, *_ in FIXTURE), "Leader"]
    assert "<td>20%</td>" in table and ">86.53<" in table and ">83.89<" in table      # the spec's numbers at 30%
    assert table.count('class="an-change"') == len(result.changes)


def test_names_from_a_proposal_cannot_inject_html_into_the_table():
    result = insights.WhatIf((10, 15), (insights.WhatIfLine("<img src=x onerror=1>", (1.0, 2.0)),), ("<b>x</b>", None), (), 20.0)
    table = tab_analysis.what_if_table_html(result)
    assert "<img" not in table and "<b>x" not in table and "&lt;img" in table and "&lt;b&gt;x" in table


def test_a_row_without_a_leader_says_none():
    assert ">None<" in tab_analysis.what_if_table_html(_what_if([None] * 7))


# ---------------------------------------------------------------- the page

class _Page:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.figures: list[tuple[Any, dict]] = []

    def caption(self, text, **kwargs):
        self.calls.append(("caption", str(text)))

    def markdown(self, text, **kwargs):
        self.calls.append(("markdown", str(text)))

    def plotly_chart(self, figure, **kwargs):
        self.calls.append(("plotly_chart", "<<chart>>"))
        self.figures.append((figure, kwargs))


def drawn(analysis: tab_analysis.Analysis) -> _Page:
    page, original = _Page(), tab_analysis.st
    tab_analysis.st = page
    try:
        tab_analysis._show(analysis)
    finally:
        tab_analysis.st = original
    return page


def texts(page: _Page) -> list[str]:
    return [t for _k, t in page.calls]


def test_the_page_shows_three_charts_in_order_with_their_notes_and_the_table():
    page = drawn(fixture_analysis())
    headings = [t for k, t in page.calls if k == "markdown" and t.startswith("####")]
    assert headings == [f"#### {tab_analysis.HEATMAP_HEADING}", f"#### {tab_analysis.SCATTER_HEADING}", f"#### {tab_analysis.WHAT_IF_HEADING}"]
    assert [k for k, _t in page.calls].count("plotly_chart") == 3 and all(kw == {"width": "stretch"} for _f, kw in page.figures)
    joined = "".join(texts(page))
    for note in (tab_analysis.HEATMAP_NOTE, tab_analysis.SCATTER_NOTE, tab_analysis.WHAT_IF_NOTE):
        assert html.escape(note, quote=True) in joined
    assert "an-table" in joined and "The leader changes from Apex Systems to NexaWorks" in joined
    assert texts(page).index(f"#### {tab_analysis.WHAT_IF_HEADING}") < texts(page).index("<<chart>>", texts(page).index(f"#### {tab_analysis.WHAT_IF_HEADING}"))


def test_the_page_says_why_a_chart_is_missing():
    page = drawn(analysis_of(entry("Apex", (9,) * 5, "2026-10-05", 3, 1), budget=None))
    joined = "".join(texts(page))
    assert tab_analysis.NO_SCATTER_TEXT in joined and [k for k, _t in page.calls].count("plotly_chart") == 2
    odd = entry("Apex", (9,) * 5, "2026-10-05", 3, 1)
    odd["result"]["criteria"] = odd["result"]["criteria"][:2] + odd["result"]["criteria"][3:]
    assert tab_analysis.NO_WHAT_IF_TEXT in "".join(texts(drawn(analysis_of(odd))))


def test_the_page_says_when_a_supplier_was_left_out_of_the_what_if_and_stays_quiet_otherwise():
    short = entry("Short", (5,) * 5, "2026-10-06", 3, 2)
    short["result"]["criteria"] = short["result"]["criteria"][:4]
    joined = "".join(texts(drawn(analysis_of(entry("Apex", (9,) * 5, "2026-10-05", 3, 1), short))))
    assert "Left out of this comparison because their marks do not cover the same criteria: Short." in joined
    assert "Left out of this comparison" not in "".join(texts(drawn(fixture_analysis())))


def test_the_page_names_suppliers_without_a_total_or_marks():
    failed = supplier("Broken", 3, eligible=False, result={"evaluation_failed": True, "criteria": []})
    joined = "".join(texts(drawn(analysis_of(entry("Apex", (9,) * 5, "2026-10-05", 3, 1, total=WITHIN), entry("Nexa", (7,) * 5, "2026-10-06", 3, 2), failed))))
    assert "No total price stated, so no point: Nexa." in joined and "No marks to show for: Broken" in joined


def test_the_tab_has_three_charts_in_its_source_and_nothing_else_draws():
    source = Path(tab_analysis.__file__).read_text(encoding="utf-8")
    assert source.count("st.plotly_chart(") == 3 and "st.slider" not in source and "st.button" not in source


def test_the_tab_module_does_not_write_or_call_the_ai():
    tree = ast.parse(Path(tab_analysis.__file__).read_text(encoding="utf-8"))
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert not any(m.startswith(("llm", "agents")) for m in imported)


def test_a_failed_evaluation_is_not_listed_as_a_missing_price_as_well():
    failed = supplier("Broken", 3, eligible=False, result={"evaluation_failed": True, "criteria": []})
    data = analysis_of(entry("Apex", (9,) * 5, "2026-10-05", 3, 1, total=WITHIN), failed).scatter
    assert data.not_stated == ()
