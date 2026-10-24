"""The report section at the bottom of the Re-evaluation tab (an extension): download the PDF, or open your own mail app.

Nothing is sent from the app and no key is involved. The PDF and the e-mail text are built by `tools.report` from the same data as the
audit file. `st` is a parameter so that each tab's tests can pass their own stub.
"""
from __future__ import annotations

import html
import sqlite3
from typing import Any

from agents import orchestrator
from db import repository
from tools import report as report_tool
from ui import tab_audit, tab_overview

NOTE = "Download this evaluation as a PDF, or open your mail app with a short summary and attach the PDF yourself. Nothing is sent from here."
PDF_LABEL = "Download PDF report"
HTML_LABEL = "Download report (web page)"
MAIL_LABEL = "Write an email with the summary"
PDF_FAILED = "The PDF could not be made here, so the report is offered as a web page instead."
CLICK_ERROR = "The report could not be prepared."


def build(run_id: str) -> tuple[dict[str, Any], dict[str, Any] | None] | None:
    """The export dict and the picks of a run, or None when the run cannot be read."""
    try:
        with repository.connection() as conn:
            run = repository.get_run(conn, run_id)
            suppliers = repository.list_supplier_results(conn, run_id) if run is not None else []
            negotiations = repository.list_negotiations(conn, run_id) if run is not None else []
            feedback = repository.list_feedback(conn, run_id) if run is not None else []
    except (sqlite3.Error, OSError):
        return None
    if run is None or not suppliers:
        return None
    overview = tab_overview.build_overview(run, suppliers)
    export = tab_audit.build_export(run, suppliers, overview, negotiations, feedback)
    return export, report_tool.picks_data(orchestrator.picks_of(run_id))


def _small(st: Any, text: str) -> None:
    st.markdown(f'<p style="color:{tab_overview.MUTED};font-size:0.9rem;margin:0.25rem 0">{html.escape(text)}</p>', unsafe_allow_html=True)


FLOAT_KEY = "report_float"
FLOAT_LABEL = "Report"
FLOAT_CSS = (f"<style>.st-key-{FLOAT_KEY}{{position:fixed;bottom:1.25rem;right:1.25rem;z-index:1000;width:auto;"
             f"box-shadow:0 4px 14px rgba(0,0,0,0.25);border-radius:0.6rem;background:#FFFFFF}}</style>")


def _offer(st: Any, run_id: str, export: dict[str, Any], picks: dict[str, Any] | None) -> None:
    page = report_tool.report_html(export, picks)
    try:
        data, label, mime, extension = report_tool.report_pdf(page), PDF_LABEL, "application/pdf", "pdf"
    except report_tool.ReportError:
        data, label, mime, extension = page.encode("utf-8"), HTML_LABEL, "text/html", "html"
        _small(st, PDF_FAILED)
    st.download_button(label, data=data, file_name=report_tool.file_name(run_id, extension), mime=mime, key=f"report_dl_{run_id}")
    st.link_button(MAIL_LABEL, report_tool.mailto_link(export, picks), key=f"report_mail_{run_id}")


def floating(st: Any, run_id: str | None) -> None:
    """A button fixed at the bottom right of every tab; it opens a small panel with the PDF and the mail link.
    Shown only when the run on show has a recommendation."""
    if not run_id:
        return
    built = build(run_id)
    if built is None or not (built[0].get("recommendation") or {}).get("recommended"):
        return
    export, picks = built
    with st.container(key=FLOAT_KEY):
        st.markdown(FLOAT_CSS, unsafe_allow_html=True)
        with st.popover(FLOAT_LABEL, help=NOTE):
            _small(st, NOTE)
            _offer(st, run_id, export, picks)
