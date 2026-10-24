"""Overview tab tests (feature F10, made readable by change pack R1a).

Most tests call the tab's own functions on saved runs that are written straight into a temporary database, so every
combination (who is eligible, who is weak on what, odd data) can be set up exactly. One test pushes a real batch through
the orchestrator and reads it back, so the two sides cannot drift apart. The last group drives the real app headlessly
with Streamlit's AppTest, with a fake model. No API key or network is used. Needs Streamlit to import; the AppTest group is
skipped when Streamlit's test runner is missing.
"""
from __future__ import annotations

import html
import itertools
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("streamlit")

from agents import orchestrator  # noqa: E402
from db import repository, seed  # noqa: E402
from llm import client  # noqa: E402
from tools import industry, pdf_tool  # noqa: E402
from ui import sidebar, tab_new_evaluation, tab_overview  # noqa: E402

try:
    from streamlit.testing.v1 import AppTest
except ImportError:   # a stand-in for Streamlit, or a very old one
    AppTest = None

ROOT = Path(__file__).resolve().parents[1]
APP_PATH = ROOT / "app.py"
KEY = "test-key-abc123"
needs_app_test = pytest.mark.skipif(AppTest is None, reason="Streamlit's test runner is not available")
needs_pdf_reader = pytest.mark.skipif(not pdf_tool.available_backends(), reason="no PDF reader installed")

CRITERIA = [(1, "Technical Capability", 30.0), (2, "Implementation Plan", 20.0), (3, "Commercial Value", 20.0),
            (4, "Security & Compliance", 20.0), (5, "Support & Experience", 10.0)]
BELOW, MEETS = industry.BELOW, industry.MEETS


@pytest.fixture(autouse=True)
def seeded_database():
    seed.ensure_database()


# ---------------------------------------------------------------- saved runs written by hand

def result_for(scores=(8, 7, 4, 9, 5), *, failed=(), weak=(), not_stated=(), meets=("Warranty",), tie_break=None,
               evaluation_failed=False, max_score=10.0, bench=None, filled=(), evidence=None, gate_details=None,
               facts=None) -> dict[str, Any]:
    """The part of `result_json` that the Overview reads.

    `failed` is (name, reason) or (name, reason, gate_id). `bench` gives the best mark per criterion, from which the gap and
    the relative % are worked out the way the ranking engine does. `gate_details` replaces the gates altogether.
    """
    def criterion(i, name, weight, score):
        item = {"criterion_id": i, "name": name, "score": score, "max_score": max_score, "weight": weight}
        if bench is not None:
            best = bench[i - 1]
            item.update(benchmark=best, gap=round(score - best, 2), relative_pct=100.0 if best == 0 else round(score / best * 100, 2))
        if i in filled:
            item["filled_by_validator"] = True
        if evidence is not None and i in evidence:
            item["evidence_found"] = evidence[i]
        return item

    result = {
        "criteria": [criterion(i, name, weight, score) for (i, name, weight), score in zip(CRITERIA, scores)],
        "gate_details": gate_details if gate_details is not None else (
            [{"gate_id": f[2] if len(f) > 2 else f"g{n}", "name": f[0], "passed": False, "reason": f[1]} for n, f in enumerate(failed)]
            + [{"gate_id": "ok", "name": "Delivery", "passed": True, "reason": "6 weeks"}]),
        "industry_breakdown": {"attributes": (
            [{"attribute_key": f"w{i}", "label": label, "grade": BELOW} for i, label in enumerate(weak)]
            + [{"attribute_key": f"n{i}", "label": label, "grade": industry.NOT_STATED} for i, label in enumerate(not_stated)]
            + [{"attribute_key": f"m{i}", "label": label, "grade": MEETS} for i, label in enumerate(meets)])},
        "tie_break_explanation": tie_break, "evaluation_failed": evaluation_failed,
    }
    if facts is not None:
        result["extracted_facts"] = facts
    return result


def seeded_gates(**failing: str) -> list[dict[str, Any]]:
    """The four seeded must-haves in their order; a keyword (warranty="1 year (at least 3 required)") makes that one fail."""
    names = {"warranty": "Warranty", "certification": "Certification", "price_table": "Price table", "delivery": "Delivery"}
    ok = {"warranty": "3 years (at least 3 required)", "certification": "BIS registration named", "price_table": "Price table present",
          "delivery": "6 weeks (at most 8 allowed)"}
    return [{"gate_id": g, "name": names[g], "passed": g not in failing, "reason": failing.get(g, ok[g])} for g in names]


def supplier(name: str, rank: int, *, eligible: bool = True, industry_score: float = 75.0, ppi: float = 80.0, overall: float = 70.0,
             rating: int | None = 3, when: str = "2026-10-05", result: dict[str, Any] | None = None,
             total: float | None = None) -> dict[str, Any]:
    """`total` is the grand total the proposal states (the price the verdict compares with the run's budget)."""
    chosen = result if result is not None else result_for(failed=() if eligible else (("Warranty", "1 year (at least 3 required)"),))
    if total is not None:
        chosen = {**chosen, "extracted_facts": {"grand_total_rs": total}}
    return dict(name=name, rank=rank, eligible=eligible, industry=industry_score, ppi=ppi, overall=overall, rating=rating,
                when=when, result=chosen)


_RUN_NUMBERS = itertools.count(1)


def store_run(*suppliers: dict[str, Any], run_id: str | None = None, snapshot: bool = True, budget: float | None = 6000000,
              quantity: int | None = 100, model: str | None = "test-model") -> tuple[repository.RunRecord, list[repository.StoredSupplier]]:
    """Save a run. Without an explicit `run_id` every call gets a new one, so a test can save several runs."""
    run_id = run_id or f"RUN-TEST-OVERVIEW-{next(_RUN_NUMBERS)}"
    with repository.connection() as conn:
        repository.create_run(
            conn, run_id=run_id, created_at="2026-10-23T09:30:00+00:00", provider="OpenAI", model=model, temperature=0,   # type: ignore[arg-type]
            template_id=repository.DEFAULT_TEMPLATE_ID,
            criteria_snapshot_json=json.dumps([{"criterion_id": i, "name": n, "weight": w, "max_score": 10.0} for i, n, w in CRITERIA] if snapshot else []),
            reviewer_name="Sri", budget_rs=budget, quantity=quantity)   # type: ignore[arg-type]
        for s in suppliers:
            repository.save_supplier_result(
                conn, run_id=run_id, supplier_name=s["name"], submission_date=s["when"], experience_rating=s["rating"],
                absolute_score=s["overall"], ppi=s["ppi"], industry_score=s["industry"], final_rank=s["rank"],
                gates_passed=s["eligible"], result_json=json.dumps(s["result"]))
        run = repository.get_run(conn, run_id)
        stored = repository.list_supplier_results(conn, run_id)
    assert run is not None
    return run, stored


def overview_of(*suppliers: dict[str, Any], **options) -> tab_overview.Overview:
    run, stored = store_run(*suppliers, **options)
    return tab_overview.build_overview(run, stored)


# ---------------------------------------------------------------- who is recommended, and the sentence under it

def test_the_top_ranked_eligible_supplier_is_recommended_with_a_runner_up():
    overview = overview_of(supplier("Apex", 1), supplier("Nexa", 2), supplier("Orbit", 3))
    assert (overview.recommended.name, overview.runner_up.name, overview.top_ranked.name) == ("Apex", "Nexa", "Apex")   # type: ignore[union-attr]
    assert overview.industry_warning is None


def test_when_the_leader_is_not_eligible_the_next_eligible_supplier_is_recommended():
    overview = overview_of(supplier("Vertexa", 1, eligible=False), supplier("Apex", 2), supplier("Nexa", 3))
    assert overview.recommended.name == "Apex"   # type: ignore[union-attr]


def test_when_several_leaders_are_not_eligible_the_first_eligible_one_is_recommended():
    overview = overview_of(supplier("A", 1, eligible=False), supplier("B", 2, eligible=False), supplier("C", 3, eligible=False),
                           supplier("D", 4), supplier("E", 5))
    assert overview.recommended.name == "D"   # type: ignore[union-attr]


def test_the_runner_up_skips_a_supplier_that_is_not_eligible():
    overview = overview_of(supplier("Apex", 1), supplier("BrightPath", 2, eligible=False), supplier("Nexa", 3))
    assert (overview.recommended.name, overview.runner_up.name) == ("Apex", "Nexa")   # type: ignore[union-attr]


def test_with_one_eligible_supplier_there_is_no_runner_up():
    overview = overview_of(supplier("Apex", 1), supplier("Bright", 2, eligible=False))
    assert overview.runner_up is None and overview.recommended.name == "Apex"   # type: ignore[union-attr]


def test_when_nobody_is_eligible_the_top_ranked_supplier_and_its_failed_checks_are_named():
    failed = (("Warranty", "1 year (at least 3 required)"), ("Certification", "Not stated"))
    overview = overview_of(supplier("Apex", 1, eligible=False, ppi=86.94, result=result_for(failed=failed)),
                           supplier("Nexa", 2, eligible=False))
    assert overview.recommended is None and overview.runner_up is None and overview.recommendation.none_eligible
    assert overview.top_ranked.name == "Apex"   # type: ignore[union-attr]
    assert overview.top_ranked.why_not == ("Warranty: 1 year (at least 3 required)", "Certification: Not stated")   # type: ignore[union-attr]
    assert overview.industry_warning is None   # there is no recommended supplier to warn about


def test_a_single_supplier_is_recommended_with_no_runner_up():
    overview = overview_of(supplier("Solo", 1))
    assert overview.recommended.name == "Solo" and overview.runner_up is None   # type: ignore[union-attr]


# ---------------------------------------------------------------- the industry warning

def test_the_recommended_supplier_gets_a_warning_listing_what_is_below_standard():
    weak = result_for(weak=("Unit price", "Repair time"), not_stated=("Data wiping",), meets=("Warranty", "Battery"))
    overview = overview_of(supplier("Apex", 1, industry_score=36.25, result=weak))
    assert overview.industry_warning == "Below industry standard on: Unit price, Repair time, Data wiping"


def test_the_warning_starts_below_60_and_only_concerns_the_recommended_supplier():
    weak = result_for(weak=("Unit price",))
    assert overview_of(supplier("Apex", 1, industry_score=59.99, result=weak)).industry_warning is not None
    assert overview_of(supplier("Apex", 1, industry_score=60.0, result=weak), run_id="RUN-TEST-60").industry_warning is None
    # the leader is not eligible and weak; the recommended supplier is fine: no warning
    other = overview_of(supplier("Vertexa", 1, eligible=False, industry_score=10.0, result=weak), supplier("Apex", 2, industry_score=80.0),
                        run_id="RUN-TEST-OTHER")
    assert other.industry_warning is None


# ---------------------------------------------------------------- the rows

def test_rows_carry_the_leaderboard_values_in_rank_order():
    overview = overview_of(supplier("Second", 2, rating=None, when="2026-10-09", overall=60.5, ppi=70.25, industry_score=40.0),
                           supplier("First", 1, rating=4, when="2026-10-05", overall=77.0, ppi=86.94, industry_score=60.0))
    assert [r.name for r in overview.rows] == ["First", "Second"]
    first, second = overview.rows
    assert (first.rank, first.overall, first.ppi, first.industry, first.submitted, first.experience, first.eligible) == (
        1, 77.0, 86.94, 60.0, "5 Oct 2026", "4", True)
    assert (second.submitted, second.experience) == ("9 Oct 2026", "Not rated")


def test_why_not_lists_the_failed_checks_and_a_failed_evaluation_says_so():
    failed = (("Warranty", "1 year (at least 3 required)"), ("Certification", "Not stated"))
    overview = overview_of(
        supplier("Bright", 1, eligible=False, result=result_for(failed=failed)),
        supplier("Broken", 2, eligible=False, result=result_for(failed=failed, evaluation_failed=True)),
        supplier("Odd", 3, eligible=False, result={}),
        supplier("Fine", 4))
    by_name = {r.name: r for r in overview.rows}
    assert by_name["Bright"].why_not == ("Warranty: 1 year (at least 3 required)", "Certification: Not stated")
    assert by_name["Broken"].why_not == (tab_overview.EVALUATION_FAILED_TEXT,)
    assert by_name["Odd"].why_not == (tab_overview.CHECK_FAILED_TEXT,)   # not eligible, but the details are missing
    assert by_name["Fine"].why_not == ()


def test_the_tie_break_note_comes_from_the_supplier_that_won_the_tie():
    overview = overview_of(supplier("Nexa", 1, result=result_for(tie_break="Nexa above Horizon: higher experience rating (4 vs 3)")),
                           supplier("Horizon", 2))
    assert [r.tie_break for r in overview.rows] == ["Nexa above Horizon: higher experience rating (4 vs 3)", None]


def test_strength_labels_are_worked_out_from_the_marks():
    overview = overview_of(supplier("Apex", 1, result=result_for(scores=(8, 7, 4, 10, 0))))
    assert [(s.label, s.score, s.max_score) for s in overview.rows[0].strengths] == [
        ("Strong", 8.0, 10.0), ("Average", 7.0, 10.0), ("Weak", 4.0, 10.0), ("Strong", 10.0, 10.0), ("Weak", 0.0, 10.0)]


def test_marks_that_cannot_be_graded_do_not_break_the_page():
    overview = overview_of(supplier("Apex", 1, result=result_for(max_score=0.0)), supplier("Nexa", 2, result={"criteria": [{"criterion_id": 1}]}),
                           supplier("Orbit", 3, result={}))
    assert all(s.label is None for s in overview.rows[0].strengths)
    assert [s.label for s in overview.rows[1].strengths] == [None]
    assert overview.rows[2].strengths == () and overview.rows[2].why_not == ()


def test_the_columns_of_the_strength_table_follow_the_runs_marking_scheme():
    overview = overview_of(supplier("Apex", 1))
    assert overview.criteria == tuple(CRITERIA)
    without = overview_of(supplier("Apex", 1), run_id="RUN-TEST-NOSNAP", snapshot=False)   # an old run: taken from the saved marks instead
    assert without.criteria == tuple(CRITERIA)


def test_the_run_line_names_the_run_the_model_and_the_purchase():
    assert overview_of(supplier("Apex", 1), supplier("Nexa", 2), run_id="RUN-TEST-OVERVIEW").run_line == (
        "Run RUN-TEST-OVERVIEW · test-model (OpenAI) · 2 supplier(s) · budget Rs 60,00,000 for 100 laptop(s)")
    older = overview_of(supplier("Apex", 1), run_id="RUN-TEST-OLD", budget=None, quantity=None, model=None)
    assert older.run_line == "Run RUN-TEST-OLD · 1 supplier(s)"


# ---------------------------------------------------------------- the leaderboard table

def table_for(*suppliers, **options) -> str:
    overview = overview_of(*suppliers, **options)
    return tab_overview.leaderboard_html(overview.rows, overview.recommended.name if overview.recommended else None)


def test_the_leaderboard_has_the_spec_columns_in_order_and_a_hover_text_on_the_two_scores():
    html_text = table_for(supplier("Apex", 1))
    headers = re.findall(r"<th[^>]*>([^<]*)</th>", html_text)
    assert headers == ["Rank", "Supplier", "Overall /100", "Score vs other bids (PPI)", "Industry score", "Submitted", "Experience", "Eligible"]
    assert f'title="{html.escape(tab_overview.PPI_HELP)}"' in html_text and f'title="{html.escape(tab_overview.INDUSTRY_HELP)}"' in html_text


def test_the_leaderboard_lists_the_suppliers_in_rank_order_with_their_numbers():
    html_text = table_for(supplier("Second", 2, overall=60.5, ppi=70.25, industry_score=40.0, rating=None),
                          supplier("First", 1, overall=77.0, ppi=86.94, industry_score=60.0, rating=4))
    assert html_text.index("First") < html_text.index("Second")
    assert "77.00" in html_text and "86.94" in html_text and "60.00" in html_text and "60.50" in html_text
    assert "Not rated" in html_text and "5 Oct 2026" in html_text


def test_only_the_recommended_supplier_is_marked_recommended():
    html_text = table_for(supplier("Vertexa", 1, eligible=False), supplier("Apex", 2), supplier("Nexa", 3))
    assert html_text.count("Recommended") == 1
    assert html_text.index("Apex") < html_text.index("Recommended") < html_text.index("Nexa")
    assert html_text.count('class="ov-best"') == 1


def test_nobody_is_marked_recommended_when_nobody_is_eligible():
    html_text = table_for(supplier("A", 1, eligible=False), supplier("B", 2, eligible=False))
    assert "Recommended" not in html_text and 'class="ov-best"' not in html_text


def test_the_badge_always_prints_the_word_and_a_not_eligible_badge_shows_the_failed_checks_on_hover():
    failed = (("Warranty", "1 year (at least 3 required)"), ("Certification", "Not stated"))
    html_text = table_for(supplier("Bright", 1, eligible=False, result=result_for(failed=failed)), supplier("Apex", 2))
    assert html_text.count(">Meets must-haves</span>") == 1 and html_text.count(">Not eligible</span>") == 1
    assert 'title="Warranty: 1 year (at least 3 required)&#10;Certification: Not stated">Not eligible' in html_text
    eligible_badge = html_text[html_text.index('style="color:#2E7D32'):]
    assert eligible_badge.startswith('style="color:#2E7D32;background:#E8F5E9">Meets must-haves')   # no hover on an eligible badge


def test_the_not_eligible_badge_has_the_short_plain_reason_beside_it():
    failed = (("Warranty", "1 year (at least 3 required)", "warranty"), ("Price table", "Not stated", "price_table"))
    html_text = table_for(supplier("Bright", 1, eligible=False, result=result_for(failed=failed)), supplier("Apex", 2))
    assert '>Not eligible</span><span class="ov-why">warranty under 3 years, no price list</span>' in html_text
    assert html_text.count("ov-why") == 1 + 1      # once in the style sheet, once for the one supplier that is not eligible


def test_names_from_a_proposal_cannot_inject_html():
    nasty = '<script>alert("x")</script> & "Co"'
    html_text = table_for(supplier(nasty, 1), supplier("B", 2, eligible=False, result=result_for(failed=(('Warranty "x"', "<b>bad</b>"),))))
    assert "<script" not in html_text and "<b>" not in html_text
    assert "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt; &amp; &quot;Co&quot;" in html_text
    assert 'title="Warranty &quot;x&quot;: &lt;b&gt;bad&lt;/b&gt;"' in html_text


@pytest.mark.parametrize("builder", ["leaderboard", "strength"])
def test_the_html_is_one_line_with_its_own_style(builder):
    overview = overview_of(supplier("Apex", 1), supplier("Bright", 2, eligible=False))   # Streamlit ends an HTML block at a blank line
    text = (tab_overview.leaderboard_html(overview.rows, "Apex") if builder == "leaderboard"
            else tab_overview.strength_html(overview.rows, overview.criteria))
    assert "\n" not in text and "\r" not in text
    assert text.startswith("<style>") and "</style>" in text and text.count("<table") == 1 and text.count("</table>") == 1


# ---------------------------------------------------------------- the runner-up strip

def test_the_runner_up_is_a_readable_strip_not_small_grey_text():
    overview = overview_of(supplier("Apex", 1), supplier("Nexa", 2, ppi=86.39))
    strip = tab_overview.runner_up_html(overview.runner_up)   # type: ignore[arg-type]
    assert "Runner-up" in strip and ">Nexa<" in strip and "Ranked 2" in strip and "Score vs other bids 86.39" in strip
    assert tab_overview.MUTED not in strip   # the grey used for notes is not used here
    assert f"color:{tab_overview.theme.HEADING}" in strip and f"color:{tab_overview.theme.TEXT}" in strip
    assert f"background:{tab_overview.theme.TAB_HOVER_BACKGROUND}" in strip
    assert "\n" not in strip and strip.count("<div") == 1 and strip.count("</div>") == 1


def test_a_runner_up_name_cannot_inject_html():
    overview = overview_of(supplier("Apex", 1), supplier('<img src=x onerror="bad()"> & Co', 2))
    strip = tab_overview.runner_up_html(overview.runner_up)   # type: ignore[arg-type]
    assert "<img" not in strip and "&lt;img src=x onerror=&quot;bad()&quot;&gt; &amp; Co" in strip


# ---------------------------------------------------------------- the strength table

def strength_for(*suppliers, **options) -> str:
    overview = overview_of(*suppliers, **options)
    return tab_overview.strength_html(overview.rows, overview.criteria)


def test_the_strength_table_prints_the_word_and_the_marks_in_the_theme_colours():
    html_text = strength_for(supplier("Apex", 1, result=result_for(scores=(8, 7, 4, 9, 5))))
    assert 'style="color:#2E7D32;background:#E8F5E9">Strong (8/10)' in html_text
    assert 'style="color:#B26A00;background:#FFF3E0">Average (7/10)' in html_text
    assert 'style="color:#C62828;background:#FFEBEE">Weak (4/10)' in html_text
    assert html_text.count("Strong") == 2 and html_text.count("Average") == 2 and html_text.count("Weak") == 1   # 5 cells (9 and 8 are Strong; 7 and 5 Average)


def test_the_strength_table_has_a_column_per_criterion_with_its_weight_and_a_row_per_supplier():
    html_text = strength_for(supplier("B", 2), supplier("A", 1))
    assert html_text.index("<strong>A</strong>") < html_text.index("<strong>B</strong>")
    for _id, name, weight in CRITERIA:
        assert f"{name.replace('&', '&amp;')}<span class=\"ov-weight\">weight {weight:g}</span>" in html_text
    assert html_text.count("<tr>") == 3   # the header and two suppliers


def test_cells_that_cannot_be_graded_say_not_available():
    html_text = strength_for(supplier("Apex", 1, result={}))
    assert html_text.count("Not available") == 5


# ---------------------------------------------------------------- R1a: the verdict banner (who, in a sentence, and the catches)

GOOD, WARN, BAD = tab_overview.GOOD, tab_overview.WARN, tab_overview.BAD
WITHIN, OVER = 5_600_000, 7_398_600      # against the default budget of Rs 60 lakh


def verdict_of(*suppliers, **options) -> tab_overview.Verdict:
    return overview_of(*suppliers, **options).verdict


def pills(verdict: tab_overview.Verdict) -> list[tuple[str, str]]:
    return [(p.text, p.tone) for p in verdict.pills]


def test_a_rank_1_winner_within_budget_gets_the_good_news_sentence_and_green_pills():
    verdict = verdict_of(supplier("Apex", 1, total=WITHIN), supplier("Nexa", 2, total=OVER), supplier("Orbit", 3))
    assert (verdict.lead, verdict.name, verdict.tone) == ("We recommend ", "Apex", GOOD)
    assert verdict.sentence == "Ranked 1 of 3, meets every must-have and fits the Rs 60 lakh budget."
    assert pills(verdict) == [("Ranked 1 of 3", GOOD), ("Meets every must-have", GOOD), ("Within budget", GOOD)]


def test_a_winner_that_is_not_rank_1_says_who_ranks_higher_and_why_they_are_out():
    verdict = verdict_of(supplier("Vertexa", 1, eligible=False), supplier("Apex", 2, total=WITHIN), supplier("Nexa", 3))
    assert verdict.name == "Apex" and verdict.tone == WARN
    assert verdict.sentence == ("Ranked 2 of 3 (Vertexa ranks higher but is not eligible), meets every must-have "
                                "and fits the Rs 60 lakh budget.")
    assert pills(verdict)[0] == ("Ranked 2 of 3", WARN)         # not rank 1, so it is not shown as a plain good fact


def test_several_suppliers_ranked_higher_are_all_named():
    verdict = verdict_of(supplier("A", 1, eligible=False), supplier("B", 2, eligible=False), supplier("C", 3, eligible=False),
                         supplier("D", 4, total=WITHIN), supplier("E", 5))
    assert verdict.sentence.startswith("Ranked 4 of 5 (A, B and C rank higher but are not eligible), meets every must-have")


def test_a_winner_over_budget_says_by_how_much_in_lakh_and_gets_an_amber_pill():
    verdict = verdict_of(supplier("Apex", 1, total=OVER), supplier("Nexa", 2))
    assert verdict.sentence == "Ranked 1 of 2, meets every must-have, but is Rs 13.99 lakh over the Rs 60 lakh budget."
    assert pills(verdict) == [("Ranked 1 of 2", GOOD), ("Meets every must-have", GOOD), ("Over budget by Rs 13.99 lakh", WARN)]
    assert verdict.tone == WARN


def test_a_winner_whose_price_is_not_stated_says_so():
    verdict = verdict_of(supplier("Apex", 1), supplier("Nexa", 2, total=WITHIN))
    assert verdict.sentence == "Ranked 1 of 2, meets every must-have, but its price is not stated."
    assert pills(verdict)[-1] == ("Price not stated", WARN) and verdict.tone == WARN


def test_the_budget_of_the_run_is_what_the_price_is_compared_with():
    verdict = verdict_of(supplier("Apex", 1, total=5_000_000), budget=4_500_000)
    assert verdict.sentence == "Ranked 1 of 1, meets every must-have, but is Rs 5 lakh over the Rs 45 lakh budget."
    assert verdict_of(supplier("Apex", 1, total=5_000_000), budget=7_000_000, run_id="RUN-TEST-BIG").sentence.endswith(
        "fits the Rs 70 lakh budget.")


def test_a_run_saved_without_a_budget_makes_no_claim_about_it():
    stated = verdict_of(supplier("Apex", 1, total=WITHIN), budget=None, quantity=None)
    assert stated.sentence == "Ranked 1 of 1 and meets every must-have."
    assert pills(stated) == [("Ranked 1 of 1", GOOD), ("Meets every must-have", GOOD)] and stated.tone == GOOD
    unstated = verdict_of(supplier("Apex", 1), budget=None, quantity=None, run_id="RUN-TEST-NOBUDGET")
    assert unstated.sentence == "Ranked 1 of 1, meets every must-have, but its price is not stated."


def test_a_total_exactly_equal_to_the_budget_fits_it():
    assert verdict_of(supplier("Apex", 1, total=6_000_000)).sentence.endswith("and fits the Rs 60 lakh budget.")


def test_nobody_eligible_gives_a_red_banner_with_the_top_ranked_supplier_and_what_it_failed():
    failed = (("Warranty", "1 year (at least 3 required)", "warranty"), ("Certification", "Not stated", "certification"))
    verdict = verdict_of(supplier("Apex", 1, eligible=False, result=result_for(failed=failed)), supplier("Nexa", 2, eligible=False))
    assert (verdict.tone, verdict.lead, verdict.name) == (BAD, "No supplier meets every must-have", None)
    assert verdict.sentence == "Apex is ranked 1 but is not eligible: warranty under 3 years, BIS certificate not named."
    assert pills(verdict) == [("Warranty under 3 years", BAD), ("BIS certificate not named", BAD)]


def test_nobody_eligible_and_the_leader_failed_to_evaluate_says_that():
    broken = result_for(failed=(("Warranty", "Not stated", "warranty"),), evaluation_failed=True)
    verdict = verdict_of(supplier("Apex", 1, eligible=False, result=broken))
    assert verdict.sentence == "Apex is ranked 1 but is not eligible: the evaluation failed."
    assert pills(verdict) == [("The evaluation failed", BAD)]


def test_nobody_eligible_with_no_details_saved_still_says_a_must_have_was_not_met():
    verdict = verdict_of(supplier("Apex", 1, eligible=False, result={}))
    assert verdict.sentence == "Apex is ranked 1 but is not eligible: a must-have was not met."


def test_no_more_than_four_pills_and_the_industry_catch_is_the_short_form_of_the_warning():
    weak = result_for(weak=("Unit price", "Repair time"), not_stated=("Data wiping",))
    verdict = verdict_of(supplier("Apex", 1, industry_score=36.25, result=weak, total=OVER))
    assert pills(verdict) == [("Ranked 1 of 1", GOOD), ("Meets every must-have", GOOD), ("Over budget by Rs 13.99 lakh", WARN),
                              ("Below typical deals on: Unit price, Repair time (+1 more)", WARN)]
    assert len(verdict.pills) <= tab_overview.MAX_PILLS


def test_the_pills_are_capped(monkeypatch):
    monkeypatch.setattr(tab_overview, "MAX_PILLS", 3)
    weak = result_for(weak=("Unit price",))
    assert len(verdict_of(supplier("Apex", 1, industry_score=36.25, result=weak, total=OVER)).pills) == 3


def test_the_industry_pill_lists_two_attributes_without_a_more_and_a_generic_one_when_none_is_named():
    two = result_for(weak=("Unit price",), not_stated=("Data wiping",))
    assert pills(verdict_of(supplier("Apex", 1, industry_score=40.0, result=two, total=WITHIN)))[-1] == (
        "Below typical deals on: Unit price, Data wiping", WARN)
    none_named = pills(verdict_of(supplier("Apex", 1, industry_score=40.0, result=result_for(), total=WITHIN), run_id="RUN-TEST-GENERIC"))
    assert none_named[-1] == ("Below typical deals on several attributes", WARN)


def test_no_industry_pill_from_a_score_of_60():
    weak = result_for(weak=("Unit price",))
    assert all("typical deals" not in text for text, _tone in pills(verdict_of(supplier("Apex", 1, industry_score=60.0, result=weak, total=WITHIN))))


def test_the_industry_pill_only_concerns_the_recommended_supplier():
    weak = result_for(weak=("Unit price",))
    verdict = verdict_of(supplier("Vertexa", 1, eligible=False, industry_score=10.0, result=weak), supplier("Apex", 2, industry_score=80.0, total=WITHIN))
    assert all("typical deals" not in text for text, _tone in pills(verdict))


def test_the_verdict_for_a_run_with_no_suppliers_does_not_break():
    verdict = tab_overview.build_verdict((), None, None, None)
    assert verdict.tone == WARN and verdict.pills == () and verdict.name is None


def test_the_budget_line_names_the_budget_in_lakh_and_the_number_of_laptops():
    assert overview_of(supplier("Apex", 1)).budget_line == "Budget for this run: Rs 60 lakh for 100 laptops"
    assert overview_of(supplier("Apex", 1), budget=4_500_000, quantity=1, run_id="RUN-TEST-ONE").budget_line == "Budget for this run: Rs 45 lakh for 1 laptop"
    assert overview_of(supplier("Apex", 1), quantity=None, run_id="RUN-TEST-NOQ").budget_line == "Budget for this run: Rs 60 lakh"
    assert overview_of(supplier("Apex", 1), budget=None, quantity=None, run_id="RUN-TEST-OLD2").budget_line is None
    assert overview_of(supplier("Apex", 1), budget=0, run_id="RUN-TEST-ZERO").budget_line is None           # no budget is not "Rs 0"


def test_the_banner_html_has_the_edge_colour_the_name_the_sentence_and_symbols_with_words_in_the_pills():
    verdict = verdict_of(supplier("Apex", 1, total=OVER), supplier("Nexa", 2))
    html_text = tab_overview.verdict_html(verdict)
    assert f"border-left-color:{tab_overview.theme.EDGE_WARN}" in html_text
    assert 'We recommend <span class="ov-banner-name">Apex</span>' in html_text
    assert "Ranked 1 of 2, meets every must-have, but is Rs 13.99 lakh over the Rs 60 lakh budget." in html_text
    assert "\u2713 Ranked 1 of 2<" in html_text and "\u26a0 Over budget by Rs 13.99 lakh<" in html_text
    assert f"color:{tab_overview.theme.PILL_GOOD[0]};background:{tab_overview.theme.PILL_GOOD[1]}" in html_text
    assert f"color:{tab_overview.theme.PILL_WARN[0]};background:{tab_overview.theme.PILL_WARN[1]}" in html_text


def test_the_banner_edge_is_green_for_good_news_and_red_when_nobody_is_eligible():
    good = tab_overview.verdict_html(verdict_of(supplier("Apex", 1, total=WITHIN)))
    assert f"border-left-color:{tab_overview.theme.EDGE_GOOD}" in good
    bad = tab_overview.verdict_html(verdict_of(supplier("Apex", 1, eligible=False), run_id="RUN-TEST-RED"))
    assert f"border-left-color:{tab_overview.theme.EDGE_BAD}" in bad and "\u2717 Warranty: 1 year (at least 3 required)<" in bad
    assert 'class="ov-banner-name">No supplier meets every must-have<' in bad


def test_the_banner_html_cannot_be_injected_and_is_one_line():
    nasty = '<img src=x onerror="bad()"> & "Co"'
    html_text = tab_overview.verdict_html(verdict_of(supplier(nasty, 1, total=WITHIN)))
    assert "<img" not in html_text and "&lt;img src=x onerror=&quot;bad()&quot;&gt; &amp; &quot;Co&quot;" in html_text
    assert "\n" not in html_text and "\r" not in html_text and html_text.startswith("<style>")


# ---------------------------------------------------------------- R1a: the must-haves grid

def seeded(**failing: str) -> dict[str, Any]:
    return result_for(gate_details=seeded_gates(**failing))


def grid_for(*suppliers, **options) -> str:
    overview = overview_of(*suppliers, **options)
    return tab_overview.musthaves_html(overview.rows, overview.musthaves, overview.recommended.name if overview.recommended else None)


def test_there_is_one_column_per_must_have_with_the_short_names_in_order():
    overview = overview_of(supplier("Apex", 1, result=seeded()))
    assert [m.label for m in overview.musthaves] == ["3-yr warranty", "BIS certified", "Price list", "Delivery \u2264 8 wks"]
    assert [m.name for m in overview.musthaves] == ["Warranty", "Certification", "Price table", "Delivery"]
    headers = re.findall(r"<th[^>]*>([^<]*)</th>", grid_for(supplier("Apex", 1, result=seeded())))
    assert headers == ["Supplier", "3-yr warranty", "BIS certified", "Price list", "Delivery \u2264 8 wks"]


def test_a_must_have_without_a_short_name_is_shown_under_its_own_name():
    gates = [{"gate_id": "battery", "name": "Battery life", "passed": True, "reason": "11 hours"}]
    assert [m.label for m in overview_of(supplier("Apex", 1, result=result_for(gate_details=gates))).musthaves] == ["Battery life"]


def test_the_header_hover_gives_the_real_name_of_the_must_have():
    assert 'title="Price table">Price list</th>' in grid_for(supplier("Apex", 1, result=seeded()))


def test_each_cell_is_a_green_tick_or_a_red_cross_with_the_reason_on_hover():
    html_text = grid_for(supplier("Apex", 1, result=seeded()), supplier("Bright", 2, eligible=False, result=seeded(warranty="1 year (at least 3 required)")))
    good, bad = tab_overview.theme.PILL_GOOD, tab_overview.theme.PILL_BAD
    assert f'title="1 year (at least 3 required)" style="color:{bad[0]};background:{bad[1]}">\u2717</td>' in html_text
    assert f'title="3 years (at least 3 required)" style="color:{good[0]};background:{good[1]}">\u2713</td>' in html_text
    assert html_text.count("\u2717") == 1 and html_text.count("\u2713") == 4 + 3


def test_the_rows_are_in_rank_order_and_the_recommended_supplier_is_marked():
    html_text = grid_for(supplier("Second", 2, result=seeded()), supplier("First", 1, eligible=False, result=seeded(delivery="12 weeks (at most 8 allowed)")))
    assert html_text.index("<strong>First</strong>") < html_text.index("<strong>Second</strong>")
    assert html_text.count("Recommended") == 1 and html_text.index("Second") < html_text.index("Recommended")


def test_a_supplier_whose_evaluation_failed_has_a_cross_in_every_column_saying_so():
    broken = result_for(gate_details=seeded_gates(), evaluation_failed=True)
    html_text = grid_for(supplier("Broken", 1, eligible=False, result=broken), supplier("Fine", 2, result=seeded()))
    row = html_text.split("<strong>Broken</strong>")[1].split("</tr>")[0]
    assert row.count("\u2717") == 4 and row.count('title="Evaluation failed"') == 4 and "\u2713" not in row


def test_a_must_have_that_is_missing_for_a_supplier_is_not_shown_as_a_tick():
    short = result_for(gate_details=seeded_gates()[:2])
    html_text = grid_for(supplier("Apex", 1, result=seeded()), supplier("Odd", 2, eligible=False, result=short))
    row = html_text.split("<strong>Odd</strong>")[1].split("</tr>")[0]
    assert row.count("\u2713") == 2 and row.count("\u2013") == 2 and row.count('title="Not checked"') == 2


@pytest.mark.parametrize("saved", ["yes", 1, None, "true"])
def test_only_a_saved_true_counts_as_passed(saved):
    gates = seeded_gates()
    gates[0]["passed"] = saved
    row = grid_for(supplier("Apex", 1, eligible=False, result=result_for(gate_details=gates))).split("<strong>Apex</strong>")[1]
    first_cell = row.split("</td>")[1]                  # [0] is the rest of the name cell, [1] the warranty cell
    assert "\u2717" in first_cell and "\u2713" not in first_cell


def test_a_run_without_any_must_haves_has_no_grid_columns():
    assert overview_of(supplier("Apex", 1, result=result_for(gate_details=[]))).musthaves == ()


def test_hand_edited_gate_details_do_not_break_the_page():
    for junk in ("text", 5, {"a": 1}, [1, "x", None]):
        overview = overview_of(supplier("Apex", 1, result=result_for(gate_details=junk)), run_id=f"RUN-TEST-JUNK-{str(junk)[:3]}")
        assert overview.musthaves == () and overview.rows[0].gates == ()


def test_the_grid_cannot_be_injected_and_is_one_line_with_its_own_style():
    nasty = {"gate_id": "x", "name": '<b>Bad</b> "name"', "passed": False, "reason": "<script>alert(1)</script>\nsecond line"}
    html_text = grid_for(supplier('<i>Co</i>', 1, eligible=False, result=result_for(gate_details=[nasty])))
    assert "<script" not in html_text and "<b>" not in html_text and "<i>" not in html_text
    assert "&lt;script&gt;alert(1)&lt;/script&gt; second line" in html_text and "&lt;b&gt;Bad&lt;/b&gt; &quot;name&quot;" in html_text
    assert "\n" not in html_text and html_text.startswith("<style>") and html_text.count("<table") == 1


# ---------------------------------------------------------------- R1a: the "Why the top three" cards

BENCH = (10, 10, 10, 10, 10)


def graded(scores, **options) -> dict[str, Any]:
    return result_for(scores=scores, bench=BENCH, gate_details=seeded_gates(), **options)


def test_there_is_a_card_for_each_of_the_top_three_and_no_more():
    overview = overview_of(*[supplier(f"S{n}", n) for n in range(5, 0, -1)])
    html_text = tab_overview.cards_html(overview.rows, "S1")
    assert html_text.count('class="ov-card"') == 3
    assert html_text.index("S1") < html_text.index("S2") < html_text.index("S3") and "S4" not in html_text
    assert tab_overview.cards_html(overview_of(supplier("A", 1), supplier("B", 2), run_id="RUN-TEST-TWO").rows, "A").count('class="ov-card"') == 2


def test_a_card_has_the_name_the_rank_and_the_score_vs_other_bids_with_its_hover_text():
    overview = overview_of(supplier("Apex", 1, ppi=87.78, result=graded((9, 7, 4, 10, 6))))
    card = tab_overview.card_html(overview.rows[0], True)
    assert 'class="ov-card-name">Apex<span class="ov-pill">Recommended</span>' in card
    assert f'Ranked 1 \u00b7 <span title="{html.escape(tab_overview.PPI_HELP)}">Score vs other bids</span> 87.78' in card
    assert "Not eligible" not in card


def test_only_the_recommended_supplier_has_the_recommended_pill():
    overview = overview_of(supplier("Vertexa", 1, eligible=False), supplier("Apex", 2), supplier("Nexa", 3))
    html_text = tab_overview.cards_html(overview.rows, "Apex")
    assert html_text.count("Recommended</span>") == 1 and html_text.index("Apex") < html_text.index("Recommended</span>") < html_text.index("Nexa")


def test_a_card_shows_at_most_two_good_points_and_one_thing_to_watch_chosen_by_the_rules():
    scores = (10, 10, 4, 10, 5)      # three criteria at the best mark, two behind the best (by 6 and 5)
    overview = overview_of(supplier("Apex", 1, result=graded(scores)))
    row = overview.rows[0]
    expected = insights_selection(graded(scores))
    assert row.pros == expected.pros and row.cons == expected.cons         # the screen uses the rules and nothing else
    assert len(row.pros) == 3 and len(row.cons) == 2
    card = tab_overview.card_html(row, True)
    assert card.count("\u2713</span>") == 2 and card.count("\u26a0</span>") == 1
    assert html.escape(row.pros[0].text) in card and html.escape(row.pros[1].text) in card and html.escape(row.pros[2].text) not in card
    assert html.escape(row.cons[0].text) in card and html.escape(row.cons[1].text) not in card


def insights_selection(result: dict[str, Any]):
    from tools import insights
    criteria = [insights.CriterionResult(c["criterion_id"], c["name"], c["score"], c["max_score"], c["weight"], c["benchmark"], c["gap"],
                                         c["relative_pct"]) for c in result["criteria"]]
    gates = [insights.GateOutcome(g["gate_id"], g["name"], g["passed"], g["reason"]) for g in result["gate_details"]]
    return insights.select_pros_cons(criteria, gates)


def test_the_good_points_use_the_plain_names_and_say_what_they_are_measured_against():
    overview = overview_of(supplier("Apex", 1, result=graded((10, 8, 4, 9, 5))))
    card = tab_overview.card_html(overview.rows[0], True)
    assert "Strong on Laptop specs (10/10, best of all bids)" in card
    assert "Close to the best bid on Security (9/10; best bid 10/10)" in card
    assert card.count(" \u00b7 vs this batch") == 3        # the two good points and the one thing to watch
    assert "Technical Capability" not in card and "Security &amp; Compliance" not in card


def test_a_failed_must_have_is_the_red_flag_and_is_not_repeated_among_the_points():
    failed = result_for(scores=(10, 10, 4, 10, 5), bench=BENCH, gate_details=seeded_gates(price_table="Not stated"))
    row = overview_of(supplier("Apex", 1, eligible=False, result=failed)).rows[0]
    assert row.cons[0].kind == "gate"                      # the rules put the failed must-have first ...
    card = tab_overview.card_html(row, False)
    assert '<div class="ov-card-flag">Not eligible: no price list</div>' in card
    assert "Not eligible: Price table" not in card         # ... but the card does not say it twice
    assert html.escape(row.cons[1].text) in card           # the one thing to watch is the next one


def test_a_card_with_nothing_to_say_says_so():
    quiet = result_for(scores=(5, 5, 5, 5, 5), bench=(6, 6, 6, 6, 6), gate_details=[])
    card = tab_overview.card_html(overview_of(supplier("Apex", 1, result=quiet)).rows[0], False)
    assert tab_overview.NOTHING_STANDS_OUT in card and "<li><span" not in card


def test_a_failed_evaluation_shows_no_points_and_says_why():
    broken = result_for(scores=(0, 0, 0, 0, 0), bench=BENCH, gate_details=seeded_gates(warranty="Not stated"), evaluation_failed=True, filled=(1, 2, 3, 4, 5))
    row = overview_of(supplier("Apex", 1, eligible=False, result=broken)).rows[0]
    assert row.pros == () and row.cons == ()
    card = tab_overview.card_html(row, False)
    assert "Not eligible: the evaluation failed" in card and tab_overview.NOTHING_TO_SHOW in card


def test_marks_that_are_missing_or_not_numbers_are_left_out_of_the_points():
    result = graded((10, 10, 4, 10, 5))
    result["criteria"][0]["score"] = "ten"
    result["criteria"][1]["weight"] = float("nan")
    result["criteria"][2]["max_score"] = None
    row = overview_of(supplier("Apex", 1, result=result)).rows[0]
    assert all("Laptop specs" not in r.text and "Delivery plan" not in r.text and "Value for money" not in r.text for r in (*row.pros, *row.cons))


def test_a_card_cannot_be_injected_and_the_cards_are_one_line():
    nasty = '<script>x</script> & "Co"'
    result = graded((10, 10, 4, 10, 5), failed=(('Gate <b>', "<i>bad</i>", "unknown_gate"),))
    overview = overview_of(supplier(nasty, 1, eligible=False, result=result), supplier("B", 2, result=graded((10, 8, 8, 8, 8))))
    html_text = tab_overview.cards_html(overview.rows, None)
    assert "<script" not in html_text and "<b>" not in html_text and "<i>" not in html_text
    assert "&lt;script&gt;x&lt;/script&gt; &amp; &quot;Co&quot;" in html_text
    assert "\n" not in html_text and html_text.startswith("<style>")


def test_a_criterion_name_with_html_cannot_inject_into_the_good_points():
    result = graded((10, 10, 4, 10, 5))
    result["criteria"][0]["name"] = '<script>x</script> & "Specs"'
    result["criteria"][2]["name"] = "<b>Value</b>"
    card = tab_overview.card_html(overview_of(supplier("Apex", 1, result=result)).rows[0], True)
    assert "<script" not in card and "<b>" not in card
    assert "Strong on &lt;script&gt;x&lt;/script&gt; &amp; &quot;Specs&quot; (10/10, best of all bids)" in card
    assert "Weaker on &lt;b&gt;Value&lt;/b&gt; (4/10; best bid 10/10)" in card


def test_a_gate_the_app_does_not_know_still_reads_sensibly_in_the_short_reason():
    result = result_for(gate_details=[{"gate_id": "battery", "name": "Battery", "passed": False, "reason": "6 hours (at least 10 required)"}])
    row = overview_of(supplier("Apex", 1, eligible=False, result=result)).rows[0]
    assert row.problems == ("Battery: 6 hours (at least 10 required)",) and row.short_reason == "Battery: 6 hours (at least 10 required)"


def test_the_short_reason_lists_every_failed_must_have_in_plain_words():
    row = overview_of(supplier("Apex", 1, eligible=False, result=seeded(warranty="1 year (at least 3 required)", delivery="Not stated", price_table="No price table"))).rows[0]
    assert row.short_reason == "warranty under 3 years, no price list, delivery time not stated"


def test_an_eligible_supplier_has_no_short_reason():
    assert overview_of(supplier("Apex", 1, result=seeded())).rows[0].short_reason == ""


# ---------------------------------------------------------------- R1a: the page order and what is under "Show details"

class _Page:
    """A recording stand-in for the parts of Streamlit that the Overview draws with, to check what is drawn and in what order."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.expanders: list[tuple[str, bool]] = []
        self.inside: list[str] = []
        self.figures: list[tuple[Any, dict]] = []
        self._depth = 0

    def markdown(self, text, **kwargs):
        self.calls.append(("markdown", str(text)))

    def warning(self, text, **kwargs):
        self.calls.append(("warning", str(text)))

    def plotly_chart(self, figure, **kwargs):
        self.calls.append(("plotly_chart", "<<chart>>"))
        self.figures.append((figure, kwargs))

    def expander(self, label, expanded=False, **kwargs):
        page = self
        page.expanders.append((label, expanded))
        page.calls.append(("expander", label))

        class _Open:
            def __enter__(self_inner):
                page._depth += 1
                page.calls.append(("enter", label))
                return self_inner

            def __exit__(self_inner, *exc):
                page._depth -= 1
                page.calls.append(("exit", label))
                return False

        return _Open()


def drawn(*suppliers, **options) -> _Page:
    page = _Page()
    options.setdefault("run_id", "RUN-TEST-OVERVIEW")
    overview = overview_of(*suppliers, **options)
    original = tab_overview.st
    tab_overview.st = page
    try:
        tab_overview._show(overview)
    finally:
        tab_overview.st = original
    return page


def headings(page: _Page) -> list[str]:
    return [text for kind, text in page.calls if kind == "markdown" and text.startswith("####")]


def test_the_page_order_is_banner_budget_must_haves_strength_cards_then_details():
    page = drawn(supplier("Apex", 1, total=WITHIN, result=seeded()), supplier("Nexa", 2, result=seeded()))
    assert page.calls[0][1].startswith("<style>") and "ov-banner" in page.calls[0][1]
    assert "Budget for this run: Rs 60 lakh for 100 laptops" in page.calls[1][1]
    assert headings(page) == ["#### Must-haves", "#### Strength by criterion", "#### Price against budget", "#### Why the top three", "#### Leaderboard"]
    marks = [text for kind, text in page.calls if (kind == "markdown" and text.startswith("####")) or kind == "expander"]
    assert marks == ["#### Must-haves", "#### Strength by criterion", "#### Price against budget", "#### Why the top three", tab_overview.DETAILS_LABEL, "#### Leaderboard"]


def test_the_strength_table_stays_visible_and_unchanged_outside_the_expander():
    suppliers = (supplier("Apex", 1, total=WITHIN, result=seeded()), supplier("Nexa", 2, result=seeded()), supplier("Orbit", 3, result=seeded()))
    overview = overview_of(*suppliers)
    page = drawn(*suppliers)
    expected = tab_overview.strength_html(overview.rows, overview.criteria)
    assert expected.count("<tr>") == 4                                   # the header and all three suppliers
    enter = next(i for i, (kind, _t) in enumerate(page.calls) if kind == "enter")
    before = [text for kind, text in page.calls[:enter] if kind == "markdown"]
    assert expected in before and tab_overview.STRENGTH_NOTE in "".join(before)
    assert "Strong (8/10)" in expected and "weight 30" in expected          # the table is the F10 table: real names and weights


def test_the_details_are_closed_and_hold_the_run_line_the_explanations_the_leaderboard_and_the_rest():
    page = drawn(supplier("Nexa", 1, result=seeded(), total=WITHIN, ppi=86.39, industry_score=40.0), supplier("Horizon", 2, result=seeded()),
                 supplier("Vertexa", 3, eligible=False, result=seeded(warranty="1 year (at least 3 required)")))
    assert page.expanders == [(tab_overview.DETAILS_LABEL, False)]
    enter = next(i for i, (kind, _t) in enumerate(page.calls) if kind == "enter")
    leave = next(i for i, (kind, _t) in enumerate(page.calls) if kind == "exit")
    inside = "\n".join(text for kind, text in page.calls[enter:leave])
    assert "Run RUN-TEST-OVERVIEW" in inside and tab_overview.EXPLAIN_OVERALL in inside
    assert tab_overview.EXPLAIN_PPI in inside and tab_overview.EXPLAIN_INDUSTRY in inside
    assert "#### Leaderboard" in inside and "ov-table" in inside and "Score vs other bids (PPI)" in inside
    assert tab_overview.HOVER_NOTE in inside                                  # there is a supplier that is not eligible
    assert "Runner-up" in inside and ">Horizon<" in inside
    assert ("warning", "Below industry standard on: several attributes") in page.calls[enter:leave]       # the full warning, not just the pill


def test_nothing_that_the_overview_used_to_show_is_lost():
    """The sentence under the banner and the cards replace the old recommendation card; every number is under Show details."""
    tie = result_for(tie_break="Nexa above Horizon: higher experience rating (4 vs 3)")
    page = drawn(supplier("Nexa", 1, result=tie, overall=77.0, ppi=86.94, industry_score=60.0), supplier("Horizon", 2, overall=76.0, ppi=86.94, industry_score=50.0))
    enter = next(i for i, (kind, _t) in enumerate(page.calls) if kind == "enter")
    inside = "\n".join(text for kind, text in page.calls[enter:])
    for needle in ("77.00", "86.94", "60.00", "76.00", "50.00", "Nexa above Horizon: higher experience rating (4 vs 3)", "Runner-up"):
        assert needle in inside, needle
    outside = "\n".join(text for kind, text in page.calls[:enter])
    assert "ov-banner" in outside and "Why the top three" in outside


def test_when_nobody_is_eligible_the_details_do_not_name_a_runner_up_or_warn():
    page = drawn(supplier("Apex", 1, eligible=False), supplier("Nexa", 2, eligible=False))
    enter = next(i for i, (kind, _t) in enumerate(page.calls) if kind == "enter")
    inside = "\n".join(text for kind, text in page.calls[enter:])
    assert "Runner-up" not in inside and "No other supplier" not in inside and not any(k == "warning" for k, _t in page.calls)


def test_the_hover_tip_is_only_shown_when_a_supplier_is_not_eligible():
    assert not any(tab_overview.HOVER_NOTE in text for _kind, text in drawn(supplier("Apex", 1), supplier("Nexa", 2)).calls)
    assert any(tab_overview.HOVER_NOTE in text for _kind, text in drawn(supplier("Apex", 1), supplier("Nexa", 2, eligible=False), run_id="RUN-TEST-HOVER").calls)


def test_with_one_eligible_supplier_the_details_say_there_is_no_other():
    page = drawn(supplier("Apex", 1), supplier("Bright", 2, eligible=False))
    assert any("No other supplier meets every must-have." in text for _kind, text in page.calls)


def test_a_run_without_must_haves_says_so_instead_of_drawing_an_empty_grid():
    page = drawn(supplier("Apex", 1, result=result_for(gate_details=[])))
    assert any(tab_overview.NO_MUSTHAVES_TEXT in text for _kind, text in page.calls)
    assert not any('<td class="ov-mark"' in text for _kind, text in page.calls)


def test_the_hover_hint_for_the_grid_is_shown_under_it():
    page = drawn(supplier("Apex", 1, result=seeded()))
    assert any(tab_overview.MUSTHAVES_NOTE in text for _kind, text in page.calls)


def test_the_page_has_exactly_one_chart_and_no_slider():
    source = Path(tab_overview.__file__).read_text(encoding="utf-8")
    assert "st.slider" not in source and source.count("st.plotly_chart(") == 1       # spec 18: the Overview's one chart


# ---------------------------------------------------------------- price against budget (feature F17)

def chart_of(*suppliers, **options) -> tab_overview.PriceChart | None:
    return overview_of(*suppliers, **options).price_chart


def test_one_bar_per_supplier_that_states_a_total_in_rank_order_with_the_budget():
    chart = chart_of(supplier("Apex", 1, total=WITHIN), supplier("Nexa", 2, total=OVER), supplier("Orbit", 3))
    assert chart == tab_overview.PriceChart(("Apex", "Nexa"), (WITHIN, OVER), (False, True), 6000000.0, ("Orbit",))


def test_a_total_exactly_at_the_budget_is_within_it():
    chart = chart_of(supplier("Apex", 1, total=6000000))
    assert chart is not None and chart.over == (False,)


def test_there_is_no_chart_without_a_budget_or_without_any_total():
    assert chart_of(supplier("Apex", 1, total=WITHIN), budget=None) is None
    assert chart_of(supplier("Apex", 1), supplier("Nexa", 2)) is None


def test_the_figure_uses_the_theme_colours_and_a_dashed_budget_line_in_lakh():
    chart = chart_of(supplier("Apex", 1, total=WITHIN), supplier("Nexa", 2, total=OVER))
    figure = tab_overview.price_figure(chart)
    bar = figure.data[0]
    assert list(bar.marker.color) == [tab_overview.theme.WITHIN_BUDGET, tab_overview.theme.OVER_BUDGET]
    assert list(bar.x) == [56.0, 73.986] and list(bar.y) == ["Apex", "Nexa"]
    assert list(bar.text) == ["Rs 56 lakh", "Rs 73.99 lakh"] and bar.textposition == "inside" and bar.insidetextanchor == "start"     # inside, so the dashed line never cuts a label
    line = figure.layout.shapes[0]
    assert (line.x0, line.x1) == (60.0, 60.0) and line.line.dash == "dash" and line.line.color == tab_overview.theme.BUDGET_LINE
    assert figure.layout.xaxis.range[1] > 73.986            # room for the longest bar


def test_the_budget_line_is_inside_the_axis_when_every_bar_is_shorter():
    figure = tab_overview.price_figure(chart_of(supplier("Apex", 1, total=1_000_000)))
    assert figure.layout.xaxis.range[1] > 60.0


def test_the_page_draws_the_chart_after_the_strength_table_and_names_suppliers_without_a_total():
    page = drawn(supplier("Apex", 1, total=WITHIN, result=seeded()), supplier("Nexa", 2, result=seeded()))
    kinds = [kind for kind, _t in page.calls]
    assert kinds.count("plotly_chart") == 1 and page.figures[0][1] == {"width": "stretch"}
    texts = [t for _k, t in page.calls]
    assert texts.index("#### Strength by criterion") < texts.index("#### Price against budget") < texts.index("<<chart>>") < texts.index("#### Why the top three")
    assert any(tab_overview.PRICE_NOTE in t for t in texts)
    assert any("No total price stated, so no bar: Nexa." in t for t in texts)


def test_the_page_says_why_there_is_no_chart():
    page = drawn(supplier("Apex", 1, result=seeded()))
    assert not any(kind == "plotly_chart" for kind, _t in page.calls)
    assert any(tab_overview.NO_PRICE_CHART_TEXT in t for _k, t in page.calls)


def test_a_supplier_name_with_html_is_only_text_in_the_figure():
    chart = chart_of(supplier("<b>X</b>", 1, total=WITHIN))
    assert list(tab_overview.price_figure(chart).data[0].y) == ["<b>X</b>"]


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
GOOD_FACTS = {"warranty_years": 3, "warranty_type": "on-site", "bis_named": True, "price_table_present": True, "delivery_weeks": 6}
BAD_FACTS = {**GOOD_FACTS, "warranty_years": 1, "warranty_type": "carry-in", "bis_named": None}


def model_reply(scores: list[int], facts: dict[str, Any]) -> str:
    return json.dumps({"supplier_name": "Supplier A", "criteria": [
        {"criterion_id": i, "score": s, "justification": f"why {i}", "evidence": "Not stated in the proposal"} for i, s in enumerate(scores, start=1)],
        "extracted_facts": facts, "risks": [], "overall_summary": "ok"})


def run_batch(facts_for=lambda name: GOOD_FACTS) -> orchestrator.RunResult:
    proposals = []
    for name, when, rating, _scores in FIVE:
        pdf = pdf_tool.ExtractedPdf("0" * 64, (f"Offer from {name}.",), "pypdf")
        proposals.append(orchestrator.Proposal(f"{name.lower().replace(' ', '_')}.pdf", name, when, rating, pdf))
    backend = _FakeBackend(*[model_reply(scores, facts_for(name)) for name, _w, _r, scores in FIVE])
    return orchestrator.run_evaluation(
        proposals, industry.PurchaseTerms(6000000, 100), client.LLMSettings("OpenAI", "test-model", KEY), "Sri", backend=backend,
        sleep=lambda _s: None, now=lambda: datetime(2026, 10, 23, 9, 30, tzinfo=timezone.utc))


def read_back(run_id: str) -> tab_overview.Overview:
    with repository.connection() as conn:
        run = repository.get_run(conn, run_id)
        stored = repository.list_supplier_results(conn, run_id)
    assert run is not None
    return tab_overview.build_overview(run, stored)


def test_a_batch_that_was_really_run_reads_back_into_the_recommendation_leaderboard_and_strengths():
    result = run_batch(lambda name: BAD_FACTS if name == "BrightPath Tech" else GOOD_FACTS)
    assert result.status == "Evaluated"
    overview = read_back(result.run_id)
    assert [r.name for r in overview.rows] == ["Apex Systems", "NexaWorks", "Horizon Datacom", "Orbit Digital", "BrightPath Tech"]
    assert (overview.recommended.name, overview.runner_up.name) == ("Apex Systems", "NexaWorks")   # type: ignore[union-attr]
    assert overview.verdict.sentence == "Ranked 1 of 5, meets every must-have, but its price is not stated."   # these facts carry no price
    assert [r.tie_break for r in overview.rows if r.tie_break] == ["NexaWorks above Horizon Datacom: higher experience rating (4 vs 3)"]
    bright = overview.rows[-1]
    assert not bright.eligible and bright.why_not == ("Warranty: 1 year (at least 3 required)", "Certification: Not stated")
    assert (overview.rows[0].overall, overview.rows[0].ppi, overview.rows[0].submitted, overview.rows[0].experience) == (77.0, 86.94, "5 Oct 2026", "4")
    assert [s.label for s in overview.rows[0].strengths] == ["Strong", "Average", "Average", "Strong", "Strong"]
    assert [c[1] for c in overview.criteria] == [c[1] for c in CRITERIA] and overview.run_line.startswith(f"Run {result.run_id} · test-model (OpenAI) · 5 supplier(s)")
    assert overview.industry_warning is not None and overview.industry_warning.startswith("Below industry standard on: ")   # these facts leave most attributes unstated


def test_when_every_supplier_fails_a_mandatory_check_the_real_batch_has_no_recommendation():
    overview = read_back(run_batch(lambda name: BAD_FACTS).run_id)
    assert overview.recommended is None and overview.recommendation.none_eligible
    assert overview.top_ranked.name == "Apex Systems"   # type: ignore[union-attr]
    assert overview.verdict.sentence == ("Apex Systems is ranked 1 but is not eligible: warranty under 3 years, BIS certificate not named.")
    assert [p.text for p in overview.verdict.pills] == ["Warranty under 3 years", "BIS certificate not named"]


# ---------------------------------------------------------------- the real app (needs Streamlit's AppTest)

class FakeUpload:
    def __init__(self, name: str, file_id: str) -> None:
        self.name, self.file_id, self._data = name, file_id, ROOT.joinpath("data", "proposals", name).read_bytes()
        self.size = len(self._data)

    def getvalue(self) -> bytes:
        return self._data


class FakeModel:
    def __init__(self, replies: list[Any]) -> None:
        self.replies, self.calls = replies, 0

    def completion(self, **kwargs):
        outcome = self.replies[min(self.calls, len(self.replies) - 1)]
        self.calls += 1
        if isinstance(outcome, BaseException):
            raise outcome
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=outcome))], usage=SimpleNamespace(total_tokens=1000))

    def supports_json_mode(self, settings) -> bool:
        return True

    def cost(self, response) -> float:
        return 0.01


SAMPLE_FILES = ["apex_systems.pdf", "brightpath_tech.pdf", "nexaworks.pdf", "horizon_datacom.pdf", "orbit_digital.pdf"]


@pytest.fixture(autouse=True)
def no_default_key_and_no_network(monkeypatch):
    for provider in client.PROVIDERS:
        for name in provider.key_names:
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(sidebar, "_secret", lambda name: None)

    def refuse(*args, **kwargs):
        raise AssertionError("the Overview tests must not use the network")

    monkeypatch.setattr(client, "_default_http_get", refuse)


@pytest.fixture
def uploaded(monkeypatch):
    """Five sample PDFs 'uploaded', and a model and key 'chosen' in the sidebar."""
    files = [FakeUpload(name, f"f{i}") for i, name in enumerate(SAMPLE_FILES)]
    monkeypatch.setattr(tab_new_evaluation, "_uploaded_files", lambda disabled: files)
    monkeypatch.setattr(sidebar, "get_llm_settings", lambda: client.LLMSettings("OpenAI", "test-model", KEY))
    return files


def start() -> Any:
    app = AppTest.from_file(str(APP_PATH), default_timeout=60)
    app.run()
    return app


def overview_tab(app):
    return app.tabs[2]


def shown(block) -> str:
    return "\n".join(str(item.value) for kind in ("markdown", "caption", "info", "error", "warning", "success") for item in getattr(block, kind))


def evaluate(monkeypatch, replies: list[Any]) -> Any:
    """Open the app, fill in the five proposals (the dates come from the PDFs), click Evaluate, and return the app."""
    model = FakeModel(replies)   # one model for the whole batch: it hands out one reply per call, in upload order
    monkeypatch.setattr(client, "_LiteLLMBackend", lambda: model)
    app = start()
    for i, (name, _when, rating, _scores) in enumerate(FIVE):
        app.tabs[1].text_input(key=f"ne_name_f{i}").input(name)
        app.tabs[1].selectbox(key=f"ne_rating_f{i}").select(rating)
    app.run()
    app.tabs[1].button(key="ne_evaluate").click().run()
    return app


@needs_app_test
def test_before_any_run_the_overview_explains_what_to_do():
    app = start()
    assert not app.exception
    assert "No evaluation yet" in shown(overview_tab(app))
    assert not overview_tab(app).metric


@needs_app_test
@needs_pdf_reader
def test_after_a_run_the_overview_shows_the_banner_must_haves_strengths_cards_and_the_details(uploaded, monkeypatch):
    app = evaluate(monkeypatch, [model_reply(s, BAD_FACTS if n == "BrightPath Tech" else GOOD_FACTS) for n, _w, _r, s in FIVE])
    assert not app.exception
    page = overview_tab(app)
    text = shown(page)
    assert "We recommend" in text and "Apex Systems" in text and "Ranked 1 of 5, meets every must-have" in text
    assert "Budget for this run: Rs 60 lakh for 100 laptops" in text
    assert "Must-haves" in text and "3-yr warranty" in text and "Delivery \u2264 8 wks" in text
    assert "Strength by criterion" in text and "Strong (9/10)" in text
    assert "Why the top three" in text and "ov-card-name" in text
    assert not page.metric                      # the three scores are in the leaderboard under Show details
    assert "Leaderboard" in text and "77.00" in text and "86.94" in text
    assert "Runner-up" in text and ">NexaWorks<" in text and "Ranked 2 \u00b7 Score vs other bids 86.39" in text
    assert "NexaWorks above Horizon Datacom: higher experience rating (4 vs 3)" in text
    assert "Hover over a Not eligible badge" in text
    assert app.session_state[tab_new_evaluation.SHOWN_RUN_KEY].startswith("RUN-")
    assert KEY not in text


@needs_app_test
@needs_pdf_reader
def test_when_no_supplier_passes_every_check_the_overview_says_so(uploaded, monkeypatch):
    app = evaluate(monkeypatch, [model_reply(s, BAD_FACTS) for _n, _w, _r, s in FIVE])
    assert not app.exception
    text = shown(overview_tab(app))
    assert "No supplier meets every must-have" in text and "Apex Systems is ranked 1 but is not eligible" in text
    assert "We recommend" not in text and not overview_tab(app).metric


@needs_app_test
def test_a_run_that_cannot_be_found_gives_a_friendly_message():
    app = start()
    app.session_state[tab_new_evaluation.SHOWN_RUN_KEY] = "RUN-NOPE"
    app.run()
    assert not app.exception
    assert "could not be found" in shown(overview_tab(app))


@needs_app_test
@needs_pdf_reader
def test_a_run_that_stopped_does_not_replace_what_the_overview_shows(uploaded, monkeypatch):
    error = type("AuthenticationError", (Exception,), {})("Incorrect API key provided")
    error.status_code = 401
    error.message = "Incorrect API key provided"
    app = evaluate(monkeypatch, [model_reply(FIVE[0][3], GOOD_FACTS), error])
    assert tab_new_evaluation.SHOWN_RUN_KEY not in app.session_state
    assert "No evaluation yet" in shown(overview_tab(app))
