"""Overview tab tests (feature F10).

Most tests call the tab's own functions on saved runs that are written straight into a temporary database, so every
combination (who is eligible, who is weak on what, odd data) can be set up exactly. One test pushes a real batch through
the orchestrator and reads it back, so the two sides cannot drift apart. The last group drives the real app headlessly
with Streamlit's AppTest, with a fake model. No API key or network is used. Needs Streamlit to import; the AppTest group is
skipped when Streamlit's test runner is missing.
"""
from __future__ import annotations

import html
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
               evaluation_failed=False, max_score=10.0) -> dict[str, Any]:
    """The part of `result_json` that the Overview reads."""
    return {
        "criteria": [{"criterion_id": i, "name": name, "score": score, "max_score": max_score, "weight": weight}
                     for (i, name, weight), score in zip(CRITERIA, scores)],
        "gate_details": [{"gate_id": f"g{n}", "name": name, "passed": False, "reason": reason} for n, (name, reason) in enumerate(failed)]
                        + [{"gate_id": "ok", "name": "Delivery", "passed": True, "reason": "6 weeks"}],
        "industry_breakdown": {"attributes": (
            [{"attribute_key": f"w{i}", "label": label, "grade": BELOW} for i, label in enumerate(weak)]
            + [{"attribute_key": f"n{i}", "label": label, "grade": industry.NOT_STATED} for i, label in enumerate(not_stated)]
            + [{"attribute_key": f"m{i}", "label": label, "grade": MEETS} for i, label in enumerate(meets)])},
        "tie_break_explanation": tie_break, "evaluation_failed": evaluation_failed,
    }


def supplier(name: str, rank: int, *, eligible: bool = True, industry_score: float = 75.0, ppi: float = 80.0, overall: float = 70.0,
             rating: int | None = 3, when: str = "2026-10-05", result: dict[str, Any] | None = None) -> dict[str, Any]:
    return dict(name=name, rank=rank, eligible=eligible, industry=industry_score, ppi=ppi, overall=overall, rating=rating,
                when=when, result=result if result is not None else result_for(failed=() if eligible else (("Warranty", "1 year (at least 3 required)"),)))


def store_run(*suppliers: dict[str, Any], run_id: str = "RUN-TEST-OVERVIEW", snapshot: bool = True, budget: float | None = 6000000,
              quantity: int | None = 100, model: str | None = "test-model") -> tuple[repository.RunRecord, list[repository.StoredSupplier]]:
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
    assert overview.reason == "Ranked 1 of 3 and passes every mandatory check."
    assert overview.industry_warning is None


def test_when_the_leader_is_not_eligible_the_sentence_names_it():
    overview = overview_of(supplier("Vertexa", 1, eligible=False), supplier("Apex", 2), supplier("Nexa", 3))
    assert overview.recommended.name == "Apex"   # type: ignore[union-attr]
    assert overview.reason == ("Ranked 2 of 3. Vertexa ranks higher but does not pass every mandatory check "
                               "(hover over the Not eligible badge in the table to see why).")


def test_when_several_leaders_are_not_eligible_the_sentence_names_them_all():
    overview = overview_of(supplier("A", 1, eligible=False), supplier("B", 2, eligible=False), supplier("C", 3, eligible=False),
                           supplier("D", 4), supplier("E", 5))
    assert overview.recommended.name == "D"   # type: ignore[union-attr]
    assert overview.reason.startswith("Ranked 4 of 5. A, B and C rank higher but do not pass every mandatory check")


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
    assert overview.reason == ("Apex is ranked 1 (peer score 86.94) but does not pass: "
                               "Warranty: 1 year (at least 3 required); Certification: Not stated")
    assert overview.industry_warning is None   # there is no recommended supplier to warn about


def test_a_single_supplier_is_recommended_with_no_runner_up():
    overview = overview_of(supplier("Solo", 1))
    assert overview.recommended.name == "Solo" and overview.runner_up is None   # type: ignore[union-attr]
    assert overview.reason == "Ranked 1 of 1 and passes every mandatory check."


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
    assert overview_of(supplier("Apex", 1), supplier("Nexa", 2)).run_line == (
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
    assert headers == ["Rank", "Supplier", "Overall /100", "Peer score (PPI)", "Industry score", "Submitted", "Experience", "Eligible"]
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
    assert html_text.count(">Eligible</span>") == 1 and html_text.count(">Not eligible</span>") == 1
    assert 'title="Warranty: 1 year (at least 3 required)&#10;Certification: Not stated">Not eligible' in html_text
    eligible_badge = html_text[html_text.index('style="color:#2E7D32'):]
    assert eligible_badge.startswith('style="color:#2E7D32;background:#E8F5E9">Eligible')   # no hover on an eligible badge


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
    assert "Runner-up" in strip and ">Nexa<" in strip and "Ranked 2" in strip and "Peer score 86.39" in strip
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
    assert overview.reason == "Ranked 1 of 5 and passes every mandatory check."
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
    assert overview.reason.startswith("Apex Systems is ranked 1 (peer score 86.94) but does not pass: Warranty: 1 year (at least 3 required)")


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
def test_after_a_run_the_overview_shows_the_recommendation_leaderboard_tie_break_and_strengths(uploaded, monkeypatch):
    app = evaluate(monkeypatch, [model_reply(s, BAD_FACTS if n == "BrightPath Tech" else GOOD_FACTS) for n, _w, _r, s in FIVE])
    assert not app.exception
    page = overview_tab(app)
    text = shown(page)
    assert "Recommended supplier" in text and "Apex Systems" in text
    assert "Runner-up" in text and ">NexaWorks<" in text and "Ranked 2 \u00b7 Peer score 86.39" in text
    assert "NexaWorks above Horizon Datacom: higher experience rating (4 vs 3)" in text
    assert "Hover over a Not eligible badge" in text
    assert [m.label for m in page.metric] == ["Overall /100", "Peer score (PPI)", "Industry score"]
    assert [m.value for m in page.metric][:2] == ["77.00", "86.94"]
    assert "Strength by criterion" in text and "Strong (9/10)" in text
    assert app.session_state[tab_new_evaluation.SHOWN_RUN_KEY].startswith("RUN-")
    assert KEY not in text


@needs_app_test
@needs_pdf_reader
def test_when_no_supplier_passes_every_check_the_overview_says_so(uploaded, monkeypatch):
    app = evaluate(monkeypatch, [model_reply(s, BAD_FACTS) for _n, _w, _r, s in FIVE])
    assert not app.exception
    assert "No supplier meets every mandatory requirement." in " ".join(str(e.value) for e in overview_tab(app).error)
    assert not overview_tab(app).metric


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
