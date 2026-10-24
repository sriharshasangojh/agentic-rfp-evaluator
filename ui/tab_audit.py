"""Tab 8: Audit (feature F12: the run details and the JSON download; spec sections 13.3, 14 and 15).

Shows the run that was just evaluated (the same one the Overview and Scorecards tabs show): who ran it and with what (run
id, model, marking scheme, reviewer, budget), how long it took and what it cost, every warning raised while it ran, the
tie-break notes, and a **Download JSON** button. The file follows spec section 15 exactly: the same keys in the same order,
nothing added and nothing left out. It is built only from the saved run, so what is downloaded is what was stored.

Only the keys of section 15 are copied into the file (a fixed list, not "everything in the saved result"), and every
free-form part (the extracted facts and the industry breakdown) is cleaned on the way: a field whose name looks like a
secret is dropped and a number that is not finite becomes null. The file never holds an API key or the full text of a
proposal. Clicking the button adds a "json_exported" row to the audit trail (spec section 14).

Not on this tab yet: the three AI-quality tiles, the run history, the change trail and the PDF fingerprints (feature F21).
The feedback rounds and the negotiation points of the file are empty lists until features F19 and F20 save them.

The page is small pieces of HTML (as on the Overview and Scorecards tabs) because Streamlit's own widgets cannot lay out
the tiles. Every piece of text that comes from a proposal, a model or the buyer goes through `html.escape`.
"""
from __future__ import annotations

import html
import json
import math
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import streamlit as st

from db import audit, repository
from tools import insights
from ui import tab_overview, theme
from ui.tab_new_evaluation import SHOWN_RUN_KEY

DOWNLOAD_LABEL = "Download JSON"
DOWNLOAD_KEY = "au_download"
EXPORTED_KEY = "au_exported"            # (run id, file name, whether the audit row was written) after the last download
EXPORT_ACTION = "json_exported"         # the audit_log action (spec 14: "JSON exported")
DEFAULT_ACTOR = "reviewer"              # the audit row's actor when the sidebar's reviewer name is empty

NOT_RECORDED = "Not recorded"
NOT_ENTERED = "Not entered"
ALL_SUPPLIERS = "All suppliers"
JSON_NOTE = ("One file with every score, justification, quoted evidence and warning of this run, and the marking scheme it "
             "used. It never contains an API key or the full text of a proposal.")
NO_WARNINGS_TEXT = "No warnings in this run."
NO_TIES_TEXT = "No ties had to be broken in this run."
EMPTY_TEXT = ("No evaluation yet. Open **New evaluation**, upload the supplier PDFs and click **Evaluate suppliers**. "
              "The run details and the JSON download will appear here.")
NOT_FOUND_TEXT = "The results of this run could not be found. Run a new evaluation on the New evaluation tab."
DATABASE_ERROR_TEXT = ("The saved results could not be read. Close any other program using `rfp_evaluation.db`, "
                       "wait a few seconds, then reload the page.")
MUTED = tab_overview.MUTED

# The keys of spec section 15, in the order the file has them.
TOP_LEVEL_KEYS = ("rfp_run_id", "created_at", "status", "provider", "model", "temperature", "template", "criteria_snapshot",
                  "suppliers", "recommendation", "tie_break_explanations", "feedback_rounds", "negotiation_points", "totals")
SNAPSHOT_KEYS = ("criterion_id", "name", "weight", "max_score")
SUPPLIER_KEYS = ("rank", "supplier_name", "submission_date", "experience_rating", "absolute_score", "ppi", "industry_score",
                 "eligible", "gates", "criteria", "extracted_facts", "industry_breakdown", "pros", "cons", "risks", "warnings",
                 "document_sha256")
CRITERION_KEYS = ("criterion_id", "score", "max_score", "benchmark", "gap", "relative_pct", "weight", "strength", "justification",
                  "evidence", "evidence_found")
RECOMMENDATION_KEYS = ("recommended", "runner_up", "reason", "industry_warning")
TOTALS_KEYS = ("tokens", "cost_usd", "duration_seconds")

# One line on purpose: Streamlit's markdown ends an HTML block at a blank line, so no line breaks inside the HTML.
_CSS = (
    ".au-facts{display:grid;grid-template-columns:repeat(auto-fit,minmax(13rem,1fr));gap:0.6rem;margin:0.25rem 0 0.75rem 0}"
    f".au-fact{{background:{theme.CARD_BACKGROUND};border:1px solid {theme.BORDER};border-radius:0.6rem;padding:0.55rem 0.85rem}}"
    f".au-label{{color:{MUTED};font-size:0.8rem}}"
    f".au-value{{color:{theme.HEADING};font-weight:700;font-size:1.05rem;overflow-wrap:anywhere}}"
    ".au-mono{font-family:Consolas,Menlo,monospace;font-size:0.95rem}"
    f".au-detail{{color:{MUTED};font-size:0.82rem;overflow-wrap:anywhere}}"
    f".au-group{{background:{theme.CARD_BACKGROUND};border:1px solid {theme.BORDER};border-left:5px solid {theme.EDGE_WARN};"
    "border-radius:0.6rem;padding:0.5rem 0.9rem;margin:0.4rem 0}"
    f".au-group-name{{color:{theme.HEADING};font-weight:700}}"
    f".au-list{{margin:0.25rem 0 0.1rem 0;padding-left:1.2rem;color:{theme.TEXT};font-size:0.93rem}}"
    ".au-list li{margin:0.2rem 0;overflow-wrap:anywhere}"
)


# ---------------------------------------------------------------- small helpers

def _number(value: Any) -> float | None:
    """A real, finite number, or None. True/False, text, NaN and infinity are not numbers."""
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def _flat(text: Any) -> str:
    """Text on one line: line breaks and runs of spaces become single spaces."""
    return " ".join(str(text).split())


def _items(value: Any) -> list[Any]:
    """The entries of a saved list; anything that is not a list (hand-edited data) counts as empty."""
    return list(value) if isinstance(value, (list, tuple)) else []


def _e(text: Any) -> str:
    return html.escape(_flat(text), quote=True)


def _small(text: str) -> None:
    """A line of small grey text. `text` is plain text (it is escaped here), never HTML."""
    st.markdown(f'<p style="color:{MUTED};font-size:0.9rem;margin:0.25rem 0">{_e(text)}</p>', unsafe_allow_html=True)


# ---------------------------------------------------------------- the file (spec section 15)

def _clean(value: Any) -> Any:
    """A saved value made safe for the file: a number that is not finite becomes null, a field whose name looks like a secret
    is dropped, and anything that is not plain JSON becomes text."""
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items() if not audit.looks_like_secret_key(k)}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if value is None or isinstance(value, (bool, int, str)):
        return value
    return str(value)


def _texts(value: Any) -> list[str]:
    return [item for item in _items(value) if isinstance(item, str)]


def warnings_of(stored: repository.StoredSupplier) -> tuple[str, ...]:
    """The warnings saved for one supplier, one line each, in the order they were raised."""
    return tuple(_flat(w) for w in _items(stored.result.get("warnings")) if isinstance(w, str) and _flat(w))


def _supplier_export(stored: repository.StoredSupplier, row: tab_overview.Row | None) -> dict[str, Any]:
    result = stored.result
    gates = result.get("gates")
    facts, breakdown = result.get("extracted_facts"), result.get("industry_breakdown")
    values: dict[str, Any] = {
        "rank": stored.final_rank,
        "supplier_name": stored.supplier_name,
        "submission_date": stored.submission_date.isoformat(),
        "experience_rating": stored.experience_rating,
        "absolute_score": _clean(stored.absolute_score),
        "ppi": _clean(stored.ppi),
        "industry_score": _clean(stored.industry_score),
        "eligible": stored.gates_passed,
        "gates": _clean(gates) if isinstance(gates, dict) else {},
        "criteria": [{key: _clean(item.get(key)) for key in CRITERION_KEYS}
                     for item in _items(result.get("criteria")) if isinstance(item, dict)],
        "extracted_facts": _clean(facts) if isinstance(facts, dict) else {},
        "industry_breakdown": _clean(breakdown) if isinstance(breakdown, dict) else {},
        # the same pros and cons as the Overview and Scorecards show (the AI's wording where it gave some, feature F16)
        "pros": [reason.text for reason in row.pros] if row else [],
        "cons": [reason.text for reason in row.cons] if row else [],
        "risks": _texts(result.get("risks")),
        "warnings": list(warnings_of(stored)),
        "document_sha256": _clean(result.get("document_sha256")),
    }
    return {key: values[key] for key in SUPPLIER_KEYS}


def build_export(run: repository.RunRecord, suppliers: list[repository.StoredSupplier],
                 overview: tab_overview.Overview) -> dict[str, Any]:
    """The whole file as plain Python data, in the order and with the keys of spec section 15. No Streamlit here."""
    rows = {row.name: row for row in overview.rows}
    values: dict[str, Any] = {
        "rfp_run_id": run.rfp_run_id,
        "created_at": run.created_at,
        "status": run.status,
        "provider": run.provider,
        "model": run.model,
        "temperature": _clean(run.temperature),
        "template": run.template_id,
        "criteria_snapshot": [{key: _clean(item.get(key)) for key in SNAPSHOT_KEYS}
                              for item in run.criteria_snapshot if isinstance(item, dict)],
        "suppliers": [_supplier_export(s, rows.get(s.supplier_name)) for s in suppliers],
        "recommendation": {
            "recommended": overview.recommendation.recommended,
            "runner_up": overview.recommendation.runner_up,
            "reason": overview.verdict.reason or overview.verdict.sentence,
            "industry_warning": overview.industry_warning,
        },
        "tie_break_explanations": [row.tie_break for row in overview.rows if row.tie_break],
        "feedback_rounds": [],          # saved by the "Not happy?" flow (feature F19)
        "negotiation_points": [],       # saved with the negotiation suggestions (feature F20)
        "totals": {"tokens": run.total_tokens, "cost_usd": _clean(run.total_cost_usd),
                   "duration_seconds": _clean(run.duration_seconds)},
    }
    export = {key: values[key] for key in TOP_LEVEL_KEYS}
    export["recommendation"] = {key: export["recommendation"][key] for key in RECOMMENDATION_KEYS}
    export["totals"] = {key: export["totals"][key] for key in TOTALS_KEYS}
    return export


def export_text(export: dict[str, Any]) -> str:
    """The file's text: readable (two-space indent), real characters (not \\u escapes), and never NaN or infinity."""
    return json.dumps(export, indent=2, ensure_ascii=False, allow_nan=False)


def export_bytes(export: dict[str, Any]) -> bytes:
    return export_text(export).encode("utf-8", errors="replace")


def export_file_name(run_id: str) -> str:
    """RUN-20261023-093000-ab12.json: the run id with anything unsafe in a file name turned into an underscore."""
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", run_id).strip("._")
    return f"{stem or 'run'}.json"


# ---------------------------------------------------------------- what the run-details section shows

@dataclass(frozen=True)
class Fact:
    label: str
    value: str
    detail: str | None = None
    mono: bool = False          # the value is an identifier (shown in a fixed-width font)


@dataclass(frozen=True)
class WarningGroup:
    label: str                       # a supplier's name, or ALL_SUPPLIERS
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class RunDetails:
    facts: tuple[Fact, ...]
    warning_groups: tuple[WarningGroup, ...]
    warning_count: int
    tie_breaks: tuple[str, ...]


def started_text(created_at: Any) -> str:
    """'23 Oct 2026, 09:30 UTC' from the saved timestamp; text that cannot be read as a time is shown as it is."""
    try:
        moment = datetime.fromisoformat(str(created_at))
    except ValueError:
        return _flat(created_at) or NOT_RECORDED
    moment = moment.astimezone(timezone.utc) if moment.tzinfo is not None else moment
    return f"{moment.day} {moment:%b %Y}, {moment:%H:%M} UTC"


def duration_text(seconds: Any) -> str:
    value = _number(seconds)
    if value is None or value < 0:
        return NOT_RECORDED
    if value < 59.95:
        return f"{value:.1f} seconds"
    minutes, rest = divmod(int(round(value)), 60)
    return f"{minutes} min {rest:02d} s"


def _purchase_text(run: repository.RunRecord) -> str:
    budget = _number(run.budget_rs)
    if budget is None or budget <= 0:
        return NOT_RECORDED
    text = insights.format_lakh(budget)
    if run.quantity:
        text += f" for {run.quantity} laptop{'' if run.quantity == 1 else 's'}"
    return text


def _usage(run: repository.RunRecord) -> tuple[str, str]:
    tokens, cost = _number(run.total_tokens), _number(run.total_cost_usd)
    return ("Tokens not reported" if tokens is None else f"{int(tokens):,} tokens",
            "Cost not reported" if cost is None else f"About USD {cost:.4f}")


def group_warnings(suppliers: list[repository.StoredSupplier]) -> tuple[WarningGroup, ...]:
    """The warnings of the run, grouped. A warning that every supplier has (the ranking's own notes are saved with each
    supplier) is listed once under "All suppliers" instead of once per supplier."""
    per_supplier = [(s.supplier_name, list(warnings_of(s))) for s in suppliers]
    shared: list[str] = []
    if len(per_supplier) > 1:
        for text in per_supplier[0][1]:
            if text not in shared and all(text in warnings for _name, warnings in per_supplier):
                shared.append(text)
        for _name, warnings in per_supplier:
            for text in shared:
                warnings.remove(text)
    groups = [WarningGroup(ALL_SUPPLIERS, tuple(shared))] if shared else []
    groups += [WarningGroup(name, tuple(warnings)) for name, warnings in per_supplier if warnings]
    return tuple(groups)


def build_details(run: repository.RunRecord, suppliers: list[repository.StoredSupplier], overview: tab_overview.Overview,
                  template_name: str | None) -> RunDetails:
    tokens, cost = _usage(run)
    provider = _flat(run.provider or "")
    temperature = _number(run.temperature)
    model_detail = " · ".join(part for part in (provider, None if temperature is None else f"temperature {temperature:g}") if part)
    facts = (
        Fact("Run ID (RFP_RUN_ID)", run.rfp_run_id, mono=True),
        Fact("Status", run.status),
        Fact("Started", started_text(run.created_at)),
        Fact("AI model", _flat(run.model or "") or NOT_RECORDED, model_detail or None),
        Fact("Marking scheme (template)", _flat(template_name or "") or run.template_id,
             run.template_id if template_name else None),
        Fact("Reviewer", _flat(run.reviewer_name or "") or NOT_ENTERED),
        Fact("Purchase", _purchase_text(run)),
        Fact("Suppliers", str(len(suppliers))),
        Fact("Time taken", duration_text(run.duration_seconds)),
        Fact("AI usage", tokens, cost),
    )
    groups = group_warnings(suppliers)
    return RunDetails(facts, groups, sum(len(g.warnings) for g in groups),
                      tuple(_flat(row.tie_break) for row in overview.rows if row.tie_break))


# ---------------------------------------------------------------- the HTML pieces

def _tile(fact: Fact) -> str:
    detail = f'<div class="au-detail">{_e(fact.detail)}</div>' if fact.detail else ""
    mono = " au-mono" if fact.mono else ""
    return (f'<div class="au-fact"><div class="au-label">{_e(fact.label)}</div>'
            f'<div class="au-value{mono}">{_e(fact.value)}</div>{detail}</div>')


def facts_html(facts: tuple[Fact, ...]) -> str:
    return f'<style>{_CSS}</style><div class="au-facts">{"".join(_tile(f) for f in facts)}</div>'


def _list(texts: tuple[str, ...]) -> str:
    items = "".join(f"<li>{_e(text)}</li>" for text in texts)
    return f'<ul class="au-list">{items}</ul>'


def warnings_html(groups: tuple[WarningGroup, ...]) -> str:
    boxes = "".join(f'<div class="au-group"><div class="au-group-name">{_e(g.label)}</div>{_list(g.warnings)}</div>' for g in groups)
    return f"<style>{_CSS}</style>{boxes}"


def tie_breaks_html(tie_breaks: tuple[str, ...]) -> str:
    return f"<style>{_CSS}</style>{_list(tie_breaks)}"


def warning_summary(count: int) -> str:
    return NO_WARNINGS_TEXT if count == 0 else f"{count} warning{'' if count == 1 else 's'} in this run."


# ---------------------------------------------------------------- the page

def note_export(run_id: str, file_name: str, suppliers: int, size: int) -> None:
    """Add the "JSON exported" row to the audit trail (spec 14). Runs when the download button is clicked.

    It never raises: the download does not depend on it, and a callback that fails would show a stack trace. The outcome is kept
    in the session so the page can say whether the row was written.
    """
    actor = str(st.session_state.get("reviewer_name") or "").strip() or DEFAULT_ACTOR
    try:
        with repository.connection() as conn:
            audit.log_event(conn, run_id, actor, EXPORT_ACTION, {"file_name": file_name, "suppliers": suppliers, "bytes": size})
        logged = True
    except (sqlite3.Error, OSError, ValueError):
        logged = False
    st.session_state[EXPORTED_KEY] = (run_id, file_name, logged)


def _export_result(run_id: str) -> None:
    done = st.session_state.get(EXPORTED_KEY)
    if not (isinstance(done, tuple) and len(done) == 3 and done[0] == run_id):
        return
    _run_id, file_name, logged = done
    if logged:
        _small(f"Exported as {file_name}. This export was added to the audit trail.")
    else:
        st.warning(f"The file {file_name} was exported, but this export could not be added to the audit trail.")


def _show_download(export: dict[str, Any], run_id: str) -> None:
    data = export_bytes(export)
    file_name = export_file_name(run_id)
    st.download_button(DOWNLOAD_LABEL, data=data, file_name=file_name, mime="application/json", key=DOWNLOAD_KEY,
                       on_click=note_export, args=(run_id, file_name, len(export["suppliers"]), len(data)))
    _small(JSON_NOTE)
    _export_result(run_id)


def _show_page(details: RunDetails, export: dict[str, Any], run_id: str) -> None:
    st.markdown("#### Run details")
    st.markdown(facts_html(details.facts), unsafe_allow_html=True)
    _show_download(export, run_id)

    st.markdown("#### Warnings")
    _small(warning_summary(details.warning_count))
    if details.warning_groups:
        st.markdown(warnings_html(details.warning_groups), unsafe_allow_html=True)

    st.markdown("#### Tie-breaks")
    if details.tie_breaks:
        st.markdown(tie_breaks_html(details.tie_breaks), unsafe_allow_html=True)
    else:
        _small(NO_TIES_TEXT)


def render() -> None:
    run_id = st.session_state.get(SHOWN_RUN_KEY)
    if not run_id:
        st.info(EMPTY_TEXT)
        return
    try:
        with repository.connection() as conn:
            run = repository.get_run(conn, run_id)
            suppliers = repository.list_supplier_results(conn, run_id) if run is not None else []
            template = repository.get_template(conn, run.template_id) if run is not None else None
    except (sqlite3.Error, OSError):
        st.error(DATABASE_ERROR_TEXT)
        return
    if run is None or not suppliers:
        st.info(NOT_FOUND_TEXT)
        return
    overview = tab_overview.build_overview(run, suppliers)
    _show_page(build_details(run, suppliers, overview, template.name if template else None),
               build_export(run, suppliers, overview), run_id)
