"""The report (an extension beyond the spec): the finished evaluation as a readable document to download or to share by email.

Pure functions: `report_html` and `summary_text` turn the audit export (spec section 15, plus the two picks) into text; `report_pdf`
turns the HTML into PDF bytes with PyMuPDF, which the app already uses to read PDFs. The report holds the same data as the JSON file,
so it never holds an API key or a proposal's full text. Every piece of text goes through `html.escape`.
"""
from __future__ import annotations

import html
import io
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import quote

SUBJECT = "RFP evaluation report {run_id}"
MAX_PAGES = 60
MAIL_BODY_MAX = 1200          # a mailto link has a length limit in some mail apps, so the summary stays short


class ReportError(Exception):
    """The PDF could not be made (PyMuPDF missing or failed). `user_message` is a friendly sentence."""

    def __init__(self, user_message: str) -> None:
        super().__init__(user_message)
        self.user_message = user_message


def _e(value: object) -> str:
    return html.escape("" if value is None else str(value))


def _num(value: object, places: int = 2) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "-"
    return f"{value:.{places}f}".rstrip("0").rstrip(".") if places else str(int(value))


def _list(items: Sequence[object]) -> str:
    return "<ul>" + "".join(f"<li>{_e(i)}</li>" for i in items) + "</ul>" if items else "<p>None.</p>"


def _table(head: Sequence[str], rows: Sequence[Sequence[object]]) -> str:
    th = "".join(f'<th style="background-color:#D7EEEA;text-align:left;padding:3px">{_e(h)}</th>' for h in head)
    body = "".join("<tr>" + "".join(f'<td style="padding:3px;border-bottom:1px solid #cccccc">{_e(c)}</td>' for c in row) + "</tr>"
                   for row in rows)
    return f'<table width="100%" style="border-collapse:collapse;font-size:9pt"><tr>{th}</tr>{body}</table>'


def _gates(gates: object) -> str:
    if not isinstance(gates, Mapping) or not gates:
        return "-"
    return ", ".join(f"{k}: {v}" for k, v in gates.items())


def picks_data(picks: Any) -> dict[str, Any] | None:
    """The two picks as plain data (None when nothing was asked), from `orchestrator.Picks`."""
    if picks is None:
        return None
    keys = ("request", "ideal", "same", "overpay", "request_rank", "request_rank_ideal", "ideal_rank", "ideal_rank_request", "total")
    return {k: getattr(picks, k, None) for k in keys}


def report_html(export: Mapping[str, Any], picks: Mapping[str, Any] | None = None) -> str:
    """The whole report as one HTML page (inline styles only, so PyMuPDF can lay it out)."""
    rec = export.get("recommendation") or {}
    suppliers = [s for s in export.get("suppliers") or [] if isinstance(s, Mapping)]
    snapshot = [c for c in export.get("criteria_snapshot") or [] if isinstance(c, Mapping)]
    names = {c.get("criterion_id"): c.get("name") for c in snapshot}
    parts = [f'<h1 style="color:#0F5C54;font-size:20pt">RFP evaluation report</h1>',
             f'<p style="font-size:9pt;color:#4B5563">Run {_e(export.get("rfp_run_id"))} · {_e(export.get("created_at"))} · '
             f'{_e(export.get("provider"))} / {_e(export.get("model"))} · status {_e(export.get("status"))}</p>']

    parts.append('<h2 style="color:#0F5C54;font-size:14pt">Recommendation</h2>')
    parts.append(f'<p style="font-size:12pt"><b>{_e(rec.get("recommended") or "No supplier meets every must-have")}</b></p>')
    if rec.get("reason"):
        parts.append(f"<p>{_e(rec.get('reason'))}</p>")
    if rec.get("runner_up"):
        parts.append(f"<p>Runner-up: {_e(rec['runner_up'])}</p>")
    if rec.get("industry_warning"):
        parts.append(f'<p style="color:#B45309">{_e(rec["industry_warning"])}</p>')
    if picks:
        if picks.get("same"):
            parts.append(f"<p>Based on your priorities and the best fit: <b>{_e(picks.get('request'))}</b>.</p>")
        else:
            parts.append(f"<p>Best pick based on your priorities: <b>{_e(picks.get('request'))}</b>.<br/>"
                         f"Best pick based on the best fit (original weights): <b>{_e(picks.get('ideal'))}</b>.</p>")
        if picks.get("overpay"):
            parts.append(f'<p style="color:#B91C1C"><b>{_e(picks["overpay"])}</b></p>')

    parts.append('<h2 style="color:#0F5C54;font-size:14pt">Ranking</h2>')
    parts.append(_table(["Rank", "Supplier", "PPI", "Absolute score", "Industry score", "Meets every must-have"],
                        [[s.get("rank"), s.get("supplier_name"), _num(s.get("ppi")), _num(s.get("absolute_score")),
                          _num(s.get("industry_score")), "Yes" if s.get("eligible") else "No"] for s in suppliers]))

    parts.append('<h2 style="color:#0F5C54;font-size:14pt">How much each area counted</h2>')
    parts.append(_table(["Area", "Weight (%)", "Top mark"], [[c.get("name"), _num(c.get("weight")), _num(c.get("max_score"))] for c in snapshot]))

    parts.append('<h2 style="color:#0F5C54;font-size:14pt">Marks by area</h2>')
    heads = ["Supplier", *[str(n) for n in names.values()]]
    rows = []
    for s in suppliers:
        by_id = {c.get("criterion_id"): c for c in s.get("criteria") or [] if isinstance(c, Mapping)}
        rows.append([s.get("supplier_name"), *[f"{_num((by_id.get(cid) or {}).get('score'))}/{_num((by_id.get(cid) or {}).get('max_score'))}"
                                               for cid in names]])
    parts.append(_table(heads, rows))

    parts.append('<h2 style="color:#0F5C54;font-size:14pt">Must-have checks</h2>')
    parts.append(_table(["Supplier", "Checks"], [[s.get("supplier_name"), _gates(s.get("gates"))] for s in suppliers]))

    for s in suppliers:
        parts.append(f'<h3 style="font-size:11pt">{_e(s.get("supplier_name"))}</h3>')
        parts.append("<p><b>Strengths</b></p>" + _list(s.get("pros") or []))
        parts.append("<p><b>Concerns</b></p>" + _list(s.get("cons") or []))

    points = [p for p in export.get("negotiation_points") or [] if isinstance(p, Mapping)]
    if points:
        parts.append('<h2 style="color:#0F5C54;font-size:14pt">Negotiation points</h2>')
        for p in points:
            parts.append(f'<h3 style="font-size:11pt">Negotiation points for {_e(p.get("supplier"))}</h3>')
            if p.get("fit"):
                parts.append(f"<p>{_e(p['fit'])}</p>")
            parts.append("<ol>" + "".join(f"<li>{_e(t)}</li>" for t in p.get("points") or []) + "</ol>")

    rounds = [r for r in export.get("feedback_rounds") or [] if isinstance(r, Mapping)]
    if rounds:
        parts.append('<h2 style="color:#0F5C54;font-size:14pt">Rounds of changing priorities</h2>')
        parts.append(_table(["Round", "Satisfaction", "What was asked", "Recommended before", "Recommended after"],
                            [[r.get("round"), r.get("satisfaction"), r.get("reason"), r.get("recommended_before"),
                              r.get("recommended_after")] for r in rounds]))

    totals = export.get("totals") or {}
    parts.append(f'<p style="font-size:8pt;color:#4B5563">Tokens {_e(totals.get("tokens"))} · cost USD {_e(totals.get("cost_usd"))} · '
                 f'{_e(totals.get("duration_seconds"))} s. Scores are worked out by Python from the AI\'s marks; the AI never picks a '
                 f'supplier. No API key or full proposal text is in this report.</p>')
    return '<div style="font-family:sans-serif;font-size:10pt">' + "".join(parts) + "</div>"


def summary_text(export: Mapping[str, Any], picks: Mapping[str, Any] | None = None) -> str:
    """A short plain-text summary (for the preview and the email body)."""
    rec = export.get("recommendation") or {}
    lines = [f"RFP evaluation {export.get('rfp_run_id')}", f"Recommended: {rec.get('recommended') or 'no supplier meets every must-have'}"]
    if picks and not picks.get("same"):
        lines.append(f"Best fit (original weights): {picks.get('ideal')}")
    if picks and picks.get("overpay"):
        lines.append(str(picks["overpay"]))
    for s in export.get("suppliers") or []:
        if isinstance(s, Mapping):
            lines.append(f"{s.get('rank')}. {s.get('supplier_name')} - PPI {_num(s.get('ppi'))}"
                         + ("" if s.get("eligible") else " (does not meet every must-have)"))
    return "\n".join(lines)


def mailto_link(export: Mapping[str, Any], picks: Mapping[str, Any] | None = None) -> str:
    """A link that opens the person's own mail app with the subject and a short summary. Nothing is sent from the app."""
    body = summary_text(export, picks)
    body = (body[:MAIL_BODY_MAX].rsplit("\n", 1)[0] if len(body) > MAIL_BODY_MAX else body) + "\n\n(Please attach the downloaded PDF report.)"
    subject = SUBJECT.format(run_id=export.get("rfp_run_id"))
    return f"mailto:?subject={quote(subject)}&body={quote(body)}"


def file_name(run_id: str, extension: str = "pdf") -> str:
    safe = "".join(ch for ch in str(run_id) if ch.isalnum() or ch in "-_") or "run"
    return f"rfp_report_{safe}.{extension}"


def report_pdf(page_html: str) -> bytes:
    """The HTML as PDF bytes (A4, 40 pt margins). Raises ReportError with a friendly message when PyMuPDF is missing or fails."""
    try:
        import pymupdf
    except ImportError:
        raise ReportError("The PDF could not be made because PyMuPDF is not installed. Download the HTML version instead.") from None
    try:
        buffer = io.BytesIO()
        writer = pymupdf.DocumentWriter(buffer)
        story = pymupdf.Story(html=page_html)
        media = pymupdf.paper_rect("a4")
        where = media + (40, 40, -40, -40)
        more, pages = 1, 0
        while more and pages < MAX_PAGES:
            device = writer.begin_page(media)
            more, _filled = story.place(where)
            story.draw(device)
            writer.end_page()
            pages += 1
        writer.close()
        data = buffer.getvalue()
    except Exception:                      # a layout problem must never crash the page
        raise ReportError("The PDF could not be made. Download the HTML version instead.") from None
    if not data.startswith(b"%PDF"):
        raise ReportError("The PDF could not be made. Download the HTML version instead.")
    return data
