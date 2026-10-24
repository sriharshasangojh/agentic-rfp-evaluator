"""Report tests (an extension): the report text, the mail link, the PDF (when PyMuPDF is there) and the section on the Re-evaluation tab."""
from __future__ import annotations

import urllib.parse

import pytest

pytest.importorskip("streamlit")

from tools import report  # noqa: E402
from ui import report as report_ui  # noqa: E402
from review_support import evaluated_run, KEY  # noqa: E402
from db import seed  # noqa: E402

EXPORT = {
    "rfp_run_id": "RUN-1", "created_at": "2026-10-25", "status": "Evaluated", "provider": "p", "model": "m",
    "criteria_snapshot": [{"criterion_id": 1, "name": "Technical Fit", "weight": 60, "max_score": 60},
                          {"criterion_id": 3, "name": "Commercial Value", "weight": 40, "max_score": 40}],
    "suppliers": [
        {"rank": 1, "supplier_name": "Apex <b>", "ppi": 80.5, "absolute_score": 70, "industry_score": 60, "eligible": True,
         "gates": {"warranty": "pass"}, "criteria": [{"criterion_id": 1, "score": 50, "max_score": 60}],
         "pros": ["Good battery"], "cons": ["Late"]},
        {"rank": 2, "supplier_name": "Orbit", "ppi": 60, "absolute_score": 50, "industry_score": 50, "eligible": False,
         "gates": {}, "criteria": [], "pros": [], "cons": []}],
    "recommendation": {"recommended": "Apex <b>", "runner_up": "Orbit", "reason": "Best score", "industry_warning": None},
    "feedback_rounds": [{"round": 1, "satisfaction": 2, "reason": "cheaper", "recommended_before": "A", "recommended_after": "B"}],
    "negotiation_points": [{"supplier": "Apex <b>", "fit": "Fits well", "points": ["Ask for 3 years"]}],
    "totals": {"tokens": 10, "cost_usd": 0.01, "duration_seconds": 2},
}
PICKS = {"same": False, "request": "Orbit", "ideal": "Apex <b>", "overpay": "You may be overpaying."}


def test_the_report_holds_the_main_parts_and_escapes_html():
    page = report.report_html(EXPORT, PICKS)
    for text in ("Recommendation", "Ranking", "Must-have checks", "Negotiation points for", "Rounds of changing priorities",
                 "Ask for 3 years", "You may be overpaying.", "Best pick based on your priorities", "Technical Fit"):
        assert text in page
    assert "Apex &lt;b&gt;" in page and "Apex <b>" not in page


def test_same_pick_is_one_sentence_and_no_picks_is_fine():
    assert "Based on your priorities and the best fit" in report.report_html(EXPORT, {"same": True, "request": "Apex"})
    assert "Best pick based on" not in report.report_html(EXPORT, None)


def test_a_sparse_export_does_not_crash():
    assert "RFP evaluation report" in report.report_html({})
    assert report.summary_text({}).startswith("RFP evaluation")


def test_mail_link_has_subject_short_body_and_nothing_secret():
    link = report.mailto_link(EXPORT, PICKS)
    assert link.startswith("mailto:?subject=")
    query = urllib.parse.parse_qs(link.split("?", 1)[1])
    assert "RUN-1" in query["subject"][0]
    assert "Apex <b>" in query["body"][0] and "attach" in query["body"][0]
    big = {**EXPORT, "suppliers": [{**EXPORT["suppliers"][0], "supplier_name": "S" * 100, "rank": i} for i in range(60)]}
    assert len(urllib.parse.parse_qs(report.mailto_link(big).split("?", 1)[1])["body"][0]) < report.MAIL_BODY_MAX + 100


def test_file_name_is_safe():
    assert report.file_name("../RUN 1/x") == "rfp_report_RUN1x.pdf"
    assert report.file_name("", "html") == "rfp_report_run.html"


def _with_prices(export):
    suppliers = [{**export["suppliers"][0], "extracted_facts": {"grand_total_rs": 5400000}},
                 {**export["suppliers"][1], "extracted_facts": {"grand_total_rs": 6300000}}]
    return {**export, "suppliers": suppliers}


def test_pdf_has_pages_with_text_and_three_charts_worth_of_data():
    from io import BytesIO
    pypdf = pytest.importorskip("pypdf")
    pdf = report.report_pdf(_with_prices(EXPORT), PICKS, 6000000)
    assert pdf.startswith(b"%PDF")
    pages = pypdf.PdfReader(BytesIO(pdf)).pages
    assert len(pages) >= 1 and all((p.extract_text() or "").strip() for p in pages)     # no blank page
    text = "\n".join(p.extract_text() for p in pages)
    for heading in ("Peer score (PPI) by supplier", "Total price against the budget", "How much each area counted", "Budget 60.0 lakh"):
        assert heading in text
    assert "Apex <b>" in text                                                          # shown as typed, not as markup


def test_chart_data_handles_missing_prices_and_budget():
    data = report.chart_data(EXPORT, None)
    assert data["prices"] == [] and data["budget"] is None and data["areas"] and data["ppi"]
    assert report.chart_data({}, 0) == {"prices": [], "budget": None, "areas": [], "ppi": []}
    assert "Total price against the budget" not in "".join(
        p.extract_text() or "" for p in __import__("pypdf").PdfReader(__import__("io").BytesIO(report.report_pdf(EXPORT))).pages)


def test_a_sparse_export_still_gives_a_pdf_and_a_problem_is_a_friendly_error():
    assert report.report_pdf({}).startswith(b"%PDF")
    with pytest.raises(report.ReportError) as error:
        report.report_pdf({"suppliers": "not a list", "criteria_snapshot": 5, "totals": 3})
    assert "web page" in error.value.user_message


def test_picks_data_handles_none():
    assert report.picks_data(None) is None


class Page:
    def __init__(self):
        self.calls = []

    def markdown(self, text, **kw): self.calls.append(("markdown", text))
    def download_button(self, label, data=None, **kw): self.calls.append(("download", label, kw.get("mime")))
    def link_button(self, label, url, **kw): self.calls.append(("link", label, url))
    def container(self, key=None): self.calls.append(("container", key)); return _Ctx()
    def popover(self, label, **kw): self.calls.append(("popover", label)); return _Ctx()


class _Ctx:
    def __enter__(self): return self
    def __exit__(self, *a): return False


def test_section_offers_download_and_mail_link_for_a_saved_run():
    seed.ensure_database()
    run = evaluated_run()
    page = Page()
    report_ui.floating(page, run.run_id)
    kinds = [c[0] for c in page.calls]
    assert "download" in kinds and "link" in kinds and ("popover", "Report") in page.calls
    assert ("container", report_ui.FLOAT_KEY) in page.calls
    assert any(c[0] == "markdown" and "position:fixed" in c[1] and "#FFFFFF" in c[1] and "bottom" in c[1] and "right" in c[1] for c in page.calls)
    link = [c for c in page.calls if c[0] == "link"][0]
    assert link[2].startswith("mailto:") and KEY not in link[2]


def test_section_is_silent_for_an_unknown_run():
    seed.ensure_database()
    page = Page()
    report_ui.floating(page, "RUN-NOPE")
    report_ui.floating(page, None)
    assert page.calls == []
