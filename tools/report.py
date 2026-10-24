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


HEADING, PRIMARY, TEAL, TEXT, BORDER, OVER, MINT = "#0B4F4A", "#0B7A72", "#14A89E", "#1F2937", "#C5DEDA", "#D81B60", "#D5ECE8"
PIE_COLOURS = ("#0B7A72", "#14A89E", "#7CCBC4", "#F0A202", "#6C8EAD", "#B0BEC5", "#8E6C8A", "#C9ADA7")


def _clip(text: object, n: int = 14) -> str:
    text = "" if text is None else str(text)
    return text if len(text) <= n else text[: n - 1] + "…"


def _price_rows(export: Mapping[str, Any]) -> list[tuple[str, float]]:
    rows = []
    for s in export.get("suppliers") or []:
        if isinstance(s, Mapping):
            total = (s.get("extracted_facts") or {}).get("grand_total_rs") if isinstance(s.get("extracted_facts"), Mapping) else None
            if isinstance(total, (int, float)) and not isinstance(total, bool) and total > 0:
                rows.append((str(s.get("supplier_name")), float(total)))
    return rows


def chart_data(export: Mapping[str, Any], budget_rs: float | None = None) -> dict[str, Any]:
    """The numbers behind the three charts: price against budget, the weight of each area, and each supplier's PPI."""
    suppliers = [s for s in export.get("suppliers") or [] if isinstance(s, Mapping)]
    areas = [(str(c.get("name")), float(c["weight"])) for c in export.get("criteria_snapshot") or []
             if isinstance(c, Mapping) and isinstance(c.get("weight"), (int, float)) and c["weight"] > 0]
    ppi = [(str(s.get("supplier_name")), float(s["ppi"])) for s in suppliers if isinstance(s.get("ppi"), (int, float))]
    budget = float(budget_rs) if isinstance(budget_rs, (int, float)) and not isinstance(budget_rs, bool) and budget_rs > 0 else None
    return {"prices": _price_rows(export), "budget": budget, "areas": areas, "ppi": ppi}


def report_pdf(export: Mapping[str, Any], picks: Mapping[str, Any] | None = None, budget_rs: float | None = None) -> bytes:
    """The report as PDF bytes (A4) with three charts, made with ReportLab. Raises ReportError with a friendly message on any problem."""
    try:
        return _build_pdf(export, picks, budget_rs)
    except ImportError:
        raise ReportError("The PDF could not be made because ReportLab is not installed. Download the web page version instead.") from None
    except Exception:                      # a layout or data problem must never crash the page
        raise ReportError("The PDF could not be made. Download the web page version instead.") from None


def _build_pdf(export: Mapping[str, Any], picks: Mapping[str, Any] | None, budget_rs: float | None) -> bytes:
    from reportlab.graphics.charts.barcharts import HorizontalBarChart
    from reportlab.graphics.charts.piecharts import Pie
    from reportlab.graphics.shapes import Drawing, Line, String
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    base = getSampleStyleSheet()
    body = ParagraphStyle("b", parent=base["BodyText"], fontSize=9.5, leading=13, textColor=colors.HexColor(TEXT))
    small = ParagraphStyle("s", parent=body, fontSize=8, leading=10, textColor=colors.HexColor("#4B5563"))
    h1 = ParagraphStyle("h1", parent=base["Title"], fontSize=20, leading=24, textColor=colors.HexColor(HEADING), alignment=0)
    h2 = ParagraphStyle("h2", parent=base["Heading2"], fontSize=13, leading=16, textColor=colors.HexColor(HEADING), spaceBefore=10, spaceAfter=4)
    h3 = ParagraphStyle("h3", parent=base["Heading3"], fontSize=10.5, leading=13, textColor=colors.HexColor(HEADING), spaceBefore=6, spaceAfter=2)
    warn = ParagraphStyle("w", parent=body, textColor=colors.HexColor("#B91C1C"))
    cell = ParagraphStyle("c", parent=body, fontSize=8.5, leading=10.5)
    head = ParagraphStyle("hd", parent=cell, textColor=colors.HexColor(HEADING), fontName="Helvetica-Bold")

    def P(text: object, style=body) -> Paragraph:
        return Paragraph(html.escape("" if text is None else str(text)).replace("\n", "<br/>"), style)

    def bold(text: object, style=body) -> Paragraph:
        return Paragraph("<b>" + html.escape("" if text is None else str(text)) + "</b>", style)

    def table(heads: Sequence[str], rows: Sequence[Sequence[object]], widths: Sequence[float] | None = None) -> Table:
        data = [[Paragraph(html.escape(h), head) for h in heads]] + [[P("" if c is None else c, cell) for c in row] for row in rows]
        t = Table(data, colWidths=widths, repeatRows=1)
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(MINT)), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                               ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor(BORDER)),
                               ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
        return t

    rec = export.get("recommendation") or {}
    suppliers = [s for s in export.get("suppliers") or [] if isinstance(s, Mapping)]
    snapshot = [c for c in export.get("criteria_snapshot") or [] if isinstance(c, Mapping)]
    data = chart_data(export, budget_rs)
    story: list[Any] = [P("RFP evaluation report", h1),
                        P(f"Run {export.get('rfp_run_id')} · {export.get('created_at')} · {export.get('provider')} / {export.get('model')} · "
                          f"status {export.get('status')}", small),
                        P("Recommendation", h2), bold(rec.get("recommended") or "No supplier meets every must-have")]
    for key, label in (("reason", ""), ("runner_up", "Runner-up: ")):
        if rec.get(key):
            story.append(P(f"{label}{rec[key]}"))
    if rec.get("industry_warning"):
        story.append(P(rec["industry_warning"], warn))
    if picks:
        if picks.get("same"):
            story.append(P(f"Based on your priorities and the best fit: {picks.get('request')}."))
        else:
            story.append(P(f"Best pick based on your priorities: {picks.get('request')}.\nBest pick based on the best fit (original weights): {picks.get('ideal')}."))
        if picks.get("overpay"):
            story.append(P(picks["overpay"], warn))

    story += [P("Ranking", h2), table(["Rank", "Supplier", "PPI", "Absolute", "Industry", "Meets every must-have"],
              [[s.get("rank"), s.get("supplier_name"), _num(s.get("ppi")), _num(s.get("absolute_score")), _num(s.get("industry_score")),
                "Yes" if s.get("eligible") else "No"] for s in suppliers], [14 * mm, 50 * mm, 20 * mm, 22 * mm, 22 * mm, 45 * mm])]

    width = 170 * mm
    # chart 1: PPI of each supplier
    if data["ppi"]:
        n = len(data["ppi"])
        d = Drawing(width, 36 + 24 * n)
        bar = HorizontalBarChart()
        bar.x, bar.y, bar.width, bar.height = 90, 26, width - 130, 24 * n
        bar.data = [[v for _n, v in reversed(data["ppi"])]]
        bar.categoryAxis.categoryNames = [_clip(nm, 16) for nm, _v in reversed(data["ppi"])]
        bar.categoryAxis.labels.fontSize = 8
        bar.valueAxis.valueMin, bar.valueAxis.valueMax = 0, 100
        bar.valueAxis.labels.fontSize = 7
        bar.bars[0].fillColor = colors.HexColor(TEAL)
        bar.bars.strokeWidth = 0
        bar.barLabelFormat = "%.1f"
        bar.barLabels.nudge, bar.barLabels.fontSize = 10, 7
        d.add(bar)
        story += [KeepTogether([P("Peer score (PPI) by supplier", h2), d])]

    # chart 2: price against budget
    if data["prices"]:
        prices = data["prices"]
        n = len(prices)
        lakh = 100000.0
        top = max([v for _n, v in prices] + ([data["budget"]] if data["budget"] else [])) / lakh * 1.15
        d = Drawing(width, 46 + 24 * n)
        bar = HorizontalBarChart()
        bar.x, bar.y, bar.width, bar.height = 90, 26, width - 130, 24 * n
        bar.data = [[v / lakh for _n, v in reversed(prices)]]
        bar.categoryAxis.categoryNames = [_clip(nm, 16) for nm, _v in reversed(prices)]
        bar.categoryAxis.labels.fontSize = 8
        bar.valueAxis.valueMin, bar.valueAxis.valueMax = 0, top
        bar.valueAxis.labels.fontSize = 7
        bar.bars[0].fillColor = colors.HexColor(TEAL)
        bar.bars.strokeWidth = 0
        for idx, (_nm, value) in enumerate(reversed(prices)):
            over = data["budget"] is not None and value > data["budget"]
            bar.bars[(0, idx)].fillColor = colors.HexColor(OVER if over else TEAL)
        bar.barLabelFormat = "%.1f"
        bar.barLabels.nudge, bar.barLabels.fontSize = 12, 7
        bar.barLabels.dx = 4
        d.add(bar)
        if data["budget"]:
            x = bar.x + bar.width * (data["budget"] / lakh) / top
            d.add(Line(x, 22, x, 26 + 24 * n + 4, strokeColor=colors.HexColor(TEXT), strokeDashArray=[3, 2], strokeWidth=1.2))
            d.add(String(x, 34 + 24 * n, f"Budget {data['budget'] / lakh:.1f} lakh", fontSize=7.5, textAnchor="middle"))
        d.add(String(bar.x + bar.width / 2, 4, "Total price (Rs lakh)", fontSize=7.5, textAnchor="middle"))
        missing = [str(s.get("supplier_name")) for s in suppliers if str(s.get("supplier_name")) not in {nm for nm, _ in prices}]
        note = [P("Not stated in the proposal: " + ", ".join(missing), small)] if missing else []
        story += [KeepTogether([P("Total price against the budget", h2), d, *note])]

    # chart 3: pie of the weights
    if data["areas"]:
        d = Drawing(width, 150)
        pie = Pie()
        pie.x, pie.y, pie.width, pie.height = 20, 15, 120, 120
        pie.data = [w for _n, w in data["areas"]]
        pie.labels = [f"{w:g}%" for _n, w in data["areas"]]
        pie.sideLabels = False
        pie.slices.strokeWidth = 0.5
        pie.slices.strokeColor = colors.white
        pie.slices.fontSize = 8
        pie.slices.labelRadius = 1.15
        for idx in range(len(data["areas"])):
            pie.slices[idx].fillColor = colors.HexColor(PIE_COLOURS[idx % len(PIE_COLOURS)])
        d.add(pie)
        for idx, (nm, w) in enumerate(data["areas"]):
            y = 125 - idx * 16
            d.add(String(190, y, "■", fontSize=11, fillColor=colors.HexColor(PIE_COLOURS[idx % len(PIE_COLOURS)])))
            d.add(String(206, y + 1, f"{nm} ({w:g}%)", fontSize=8.5))
        story += [KeepTogether([P("How much each area counted", h2), d])]

    story += [P("Marks by area", h2)]
    names = {c.get("criterion_id"): c.get("name") for c in snapshot}
    rows = []
    for s in suppliers:
        by_id = {c.get("criterion_id"): c for c in s.get("criteria") or [] if isinstance(c, Mapping)}
        rows.append([s.get("supplier_name"), *[f"{_num((by_id.get(cid) or {}).get('score'))}/{_num((by_id.get(cid) or {}).get('max_score'))}"
                                               for cid in names]])
    story.append(table(["Supplier", *[str(n) for n in names.values()]], rows))
    story += [P("Must-have checks", h2), table(["Supplier", "Checks"], [[s.get("supplier_name"), _gates(s.get("gates"))] for s in suppliers])]

    story.append(P("Strengths and concerns", h2))
    for s in suppliers:
        block = [P(s.get("supplier_name"), h3)]
        block += [P("Strengths: " + ("; ".join(map(str, s.get("pros") or [])) or "none"), body),
                  P("Concerns: " + ("; ".join(map(str, s.get("cons") or [])) or "none"), body)]
        story.append(KeepTogether(block))

    points = [p for p in export.get("negotiation_points") or [] if isinstance(p, Mapping)]
    if points:
        story.append(P("Negotiation points", h2))
        for p in points:
            block = [P(f"Negotiation points for {p.get('supplier')}", h3)]
            if p.get("fit"):
                block.append(P(p["fit"]))
            block += [P(f"{i}. {t}") for i, t in enumerate(p.get("points") or [], 1)]
            story.append(KeepTogether(block))

    rounds = [r for r in export.get("feedback_rounds") or [] if isinstance(r, Mapping)]
    if rounds:
        story += [P("Rounds of changing priorities", h2),
                  table(["Round", "Satisfaction", "What was asked", "Before", "After"],
                        [[r.get("round"), r.get("satisfaction"), r.get("reason"), r.get("recommended_before"), r.get("recommended_after")]
                         for r in rounds])]

    totals = export.get("totals") or {}
    story += [Spacer(1, 8), P(f"Tokens {totals.get('tokens')} · cost USD {totals.get('cost_usd')} · {totals.get('duration_seconds')} s. "
                              "Scores are worked out by Python from the AI's marks; the AI never picks a supplier. "
                              "No API key or full proposal text is in this report.", small)]

    buffer = io.BytesIO()
    SimpleDocTemplate(buffer, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm, bottomMargin=18 * mm,
                      title=f"RFP evaluation report {export.get('rfp_run_id')}", author="RFP Evaluator").build(story)
    out = buffer.getvalue()
    if not out.startswith(b"%PDF"):
        raise ReportError("The PDF could not be made.")
    return out
