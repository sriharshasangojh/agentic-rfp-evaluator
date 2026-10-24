"""All reads and writes of the SQLite database. Other modules never open the database themselves."""
from __future__ import annotations

import json
import os
import re
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "rfp_evaluation.db"
DB_PATH_ENV = "RFP_DB_PATH"  # optional override, used by the tests
SCHEMA_PATH = Path(__file__).with_name("schema.sql")

DEFAULT_TEMPLATE_ID = "startup_laptops"
WEIGHT_TOTAL = 100.0
WEIGHT_TOLERANCE = 0.01


@dataclass(frozen=True)
class Template:
    template_id: str
    name: str
    description: str


@dataclass(frozen=True)
class Criterion:
    criterion_id: int
    template_id: str
    name: str
    description: str
    weight: float
    max_score: float
    is_active: bool


@dataclass(frozen=True)
class Gate:
    gate_id: str
    template_id: str
    name: str
    rule_key: str
    description: str
    is_active: bool


@dataclass(frozen=True)
class Benchmark:
    id: int
    template_id: str
    attribute_key: str
    label: str
    below_rule: str
    meets_rule: str
    above_rule: str
    criterion_id: int


def database_path() -> Path:
    """The database file: the RFP_DB_PATH override if set, otherwise rfp_evaluation.db in the project root."""
    override = os.environ.get(DB_PATH_ENV)
    return Path(override) if override else DEFAULT_DB_PATH


@contextmanager
def connection(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    """Open a connection with foreign keys on; commit on success, roll back on error, always close.

    The 10 second timeout makes SQLite wait if another program (for example OneDrive) briefly holds the file.
    """
    conn = sqlite3.connect(path or database_path(), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


# Columns added after the first version of the schema. `CREATE TABLE IF NOT EXISTS` leaves an existing table
# alone, so a database made by an older version gets these through `_add_missing_columns`.
_ADDED_COLUMNS = (
    ("rfp_runs", "budget_rs", "REAL"),
    ("rfp_runs", "quantity", "INTEGER"),
)


def _add_missing_columns(conn: sqlite3.Connection) -> None:
    for table, column, kind in _ADDED_COLUMNS:   # constants only, never user input
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")


def _make_rating_optional(conn: sqlite3.Connection, schema_text: str) -> None:
    """The experience rating became optional in F09 round 2. A database made earlier has the column as NOT NULL, and
    SQLite cannot relax that in place, so the table is rebuilt (same rows) in one transaction."""
    column = next((row for row in conn.execute("PRAGMA table_info(supplier_results)") if row[1] == "experience_rating"), None)
    if column is None or not column[3]:   # row[3] is 1 when the column is NOT NULL
        return
    match = re.search(r"CREATE TABLE IF NOT EXISTS supplier_results \(.*?\n\);", schema_text, re.DOTALL)
    if match is None:
        raise sqlite3.DatabaseError("The supplier_results definition was not found in schema.sql.")
    names = "rfp_run_id, supplier_name, submission_date, experience_rating, absolute_score, ppi, industry_score, final_rank, gates_passed, result_json"
    conn.commit()
    conn.execute("BEGIN")
    try:
        conn.execute("ALTER TABLE supplier_results RENAME TO supplier_results_old")
        conn.execute(match.group(0).replace("IF NOT EXISTS ", ""))
        conn.execute(f"INSERT INTO supplier_results ({names}) SELECT {names} FROM supplier_results_old")
        conn.execute("DROP TABLE supplier_results_old")
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def create_schema(conn: sqlite3.Connection) -> None:
    """Create every table, index and trigger that does not exist yet, and bring an older database up to date."""
    schema_text = SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(schema_text)
    _add_missing_columns(conn)
    _make_rating_optional(conn, schema_text)


def get_template(conn: sqlite3.Connection, template_id: str = DEFAULT_TEMPLATE_ID) -> Template | None:
    row = conn.execute(
        "SELECT template_id, name, description FROM industry_templates WHERE template_id = ?",
        (template_id,),
    ).fetchone()
    return Template(**dict(row)) if row else None


def list_criteria(
    conn: sqlite3.Connection,
    template_id: str = DEFAULT_TEMPLATE_ID,
    active_only: bool = False,
) -> list[Criterion]:
    sql = (
        "SELECT criterion_id, template_id, name, description, weight, max_score, is_active "
        "FROM evaluation_criteria WHERE template_id = ?"
    )
    if active_only:
        sql += " AND is_active = 1"
    rows = conn.execute(sql + " ORDER BY criterion_id", (template_id,)).fetchall()
    return [Criterion(**{**dict(row), "is_active": bool(row["is_active"])}) for row in rows]


def list_gates(
    conn: sqlite3.Connection,
    template_id: str = DEFAULT_TEMPLATE_ID,
    active_only: bool = False,
) -> list[Gate]:
    sql = "SELECT gate_id, template_id, name, rule_key, description, is_active FROM gates WHERE template_id = ?"
    if active_only:
        sql += " AND is_active = 1"
    rows = conn.execute(sql + " ORDER BY rowid", (template_id,)).fetchall()
    return [Gate(**{**dict(row), "is_active": bool(row["is_active"])}) for row in rows]


def list_benchmarks(conn: sqlite3.Connection, template_id: str = DEFAULT_TEMPLATE_ID) -> list[Benchmark]:
    rows = conn.execute(
        "SELECT id, template_id, attribute_key, label, below_rule, meets_rule, above_rule, criterion_id "
        "FROM industry_benchmarks WHERE template_id = ? ORDER BY id",
        (template_id,),
    ).fetchall()
    return [Benchmark(**dict(row)) for row in rows]


def weights_total(criteria: Sequence[Criterion]) -> float:
    """Sum of the weights of the active criteria."""
    return round(sum(c.weight for c in criteria if c.is_active), 6)


def weights_are_valid(total: float) -> bool:
    """True when the active weights add up to 100 (within a tolerance of 0.01)."""
    # Rounded first: 100.01 - 100.0 is 0.010000000000005 in floating point, which would wrongly fail.
    return round(abs(total - WEIGHT_TOTAL), 6) <= WEIGHT_TOLERANCE


# ---------------------------------------------------------------- evaluation runs (F09)

RUN_RUNNING = "Running"
RUN_EVALUATED = "Evaluated"
RUN_FAILED = "Failed"
RUN_INTERRUPTED = "Interrupted"
RUN_FINAL = "Final"                  # the reviewer accepted the recommendation (F13): no more changes


@dataclass(frozen=True)
class AttemptRow:
    """One criterion's result inside one evaluation attempt. `evidence_found` stays None until the evidence check (F15)."""

    criterion_id: int
    score: float
    justification: str
    evidence: str
    evidence_found: bool | None = None
    raw_json: str | None = None


def create_run(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    created_at: str,
    provider: str,
    model: str,
    temperature: float,
    template_id: str,
    criteria_snapshot_json: str,
    reviewer_name: str,
    budget_rs: float,
    quantity: int,
    status: str = RUN_RUNNING,
) -> None:
    """Start a run. The API key is never a parameter here: it is not stored anywhere."""
    conn.execute(
        "INSERT INTO rfp_runs (rfp_run_id, created_at, status, provider, model, temperature, template_id, "
        "criteria_snapshot_json, reviewer_name, budget_rs, quantity) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (run_id, created_at, status, provider, model, temperature, template_id, criteria_snapshot_json,
         reviewer_name, budget_rs, quantity),
    )


def set_run_status(conn: sqlite3.Connection, run_id: str, status: str) -> None:
    conn.execute("UPDATE rfp_runs SET status = ? WHERE rfp_run_id = ?", (status, run_id))


def update_run_weights(conn: sqlite3.Connection, run_id: str, criteria_snapshot_json: str) -> None:
    """Save the weights this run now ranks with (F13: the person was not satisfied and a criterion counts for more). Only this
    run changes, never the marking scheme in Setup. What the weights were before stays in `reviewer_feedback` and the audit log."""
    cursor = conn.execute("UPDATE rfp_runs SET criteria_snapshot_json = ? WHERE rfp_run_id = ?", (criteria_snapshot_json, run_id))
    if cursor.rowcount != 1:
        raise sqlite3.DatabaseError(f"No run {run_id} to change.")


def finish_run(
    conn: sqlite3.Connection,
    run_id: str,
    *,
    status: str,
    total_tokens: int | None,
    total_cost_usd: float | None,
    duration_seconds: float,
) -> None:
    conn.execute(
        "UPDATE rfp_runs SET status = ?, total_tokens = ?, total_cost_usd = ?, duration_seconds = ? WHERE rfp_run_id = ?",
        (status, total_tokens, total_cost_usd, duration_seconds, run_id),
    )


def add_document(conn: sqlite3.Connection, run_id: str, supplier_name: str, file_name: str, sha256: str, pages: int) -> None:
    conn.execute(
        "INSERT INTO run_documents (rfp_run_id, supplier_name, file_name, sha256, pages) VALUES (?, ?, ?, ?, ?)",
        (run_id, supplier_name, file_name, sha256, pages),
    )


def add_attempt(
    conn: sqlite3.Connection,
    run_id: str,
    supplier_name: str,
    attempt_no: int,
    rows: Sequence[AttemptRow],
    created_at: str,
) -> None:
    """Store every criterion of one evaluation attempt. Attempts are only ever added, never changed."""
    conn.executemany(
        "INSERT INTO evaluation_attempts (rfp_run_id, supplier_name, attempt_no, criterion_id, score, justification, "
        "evidence, evidence_found, raw_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (run_id, supplier_name, attempt_no, r.criterion_id, r.score, r.justification, r.evidence,
             None if r.evidence_found is None else int(r.evidence_found), r.raw_json, created_at)
            for r in rows
        ],
    )


def save_supplier_result(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    supplier_name: str,
    submission_date: str,
    experience_rating: int | None,
    absolute_score: float,
    ppi: float,
    industry_score: float,
    final_rank: int,
    gates_passed: bool,
    result_json: str,
) -> None:
    conn.execute(
        "INSERT INTO supplier_results (rfp_run_id, supplier_name, submission_date, experience_rating, absolute_score, "
        "ppi, industry_score, final_rank, gates_passed, result_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (run_id, supplier_name, submission_date, experience_rating, absolute_score, ppi, industry_score,
         final_rank, int(gates_passed), result_json),
    )


# ---------------------------------------------------------------- reading a saved run back (F10)

@dataclass(frozen=True)
class RunRecord:
    """One row of `rfp_runs`. `budget_rs` and `quantity` are None for a run saved before F09."""

    rfp_run_id: str
    created_at: str
    status: str
    provider: str | None
    model: str | None
    temperature: float | None
    template_id: str
    criteria_snapshot: list[dict[str, Any]]
    reviewer_name: str
    total_tokens: int | None
    total_cost_usd: float | None
    duration_seconds: float | None
    budget_rs: float | None
    quantity: int | None


@dataclass(frozen=True)
class StoredSupplier:
    """One row of `supplier_results`, with `result_json` opened up into `result` (empty if it cannot be read)."""

    supplier_name: str
    submission_date: date
    experience_rating: int | None
    absolute_score: float
    ppi: float
    industry_score: float
    final_rank: int
    gates_passed: bool
    result: dict[str, Any]


def _json_object(text: str | None) -> Any:
    try:
        return json.loads(text) if text else None
    except ValueError:
        return None


def get_run(conn: sqlite3.Connection, run_id: str) -> RunRecord | None:
    row = conn.execute(
        "SELECT rfp_run_id, created_at, status, provider, model, temperature, template_id, criteria_snapshot_json, "
        "reviewer_name, total_tokens, total_cost_usd, duration_seconds, budget_rs, quantity FROM rfp_runs WHERE rfp_run_id = ?",
        (run_id,),
    ).fetchone()
    if row is None:
        return None
    values = dict(row)
    snapshot = _json_object(values.pop("criteria_snapshot_json"))
    return RunRecord(**values, criteria_snapshot=snapshot if isinstance(snapshot, list) else [])


def list_supplier_results(conn: sqlite3.Connection, run_id: str) -> list[StoredSupplier]:
    """Every supplier of a run, best rank first."""
    rows = conn.execute(
        "SELECT supplier_name, submission_date, experience_rating, absolute_score, ppi, industry_score, final_rank, "
        "gates_passed, result_json FROM supplier_results WHERE rfp_run_id = ? ORDER BY final_rank, supplier_name",
        (run_id,),
    ).fetchall()
    suppliers: list[StoredSupplier] = []
    for row in rows:
        values = dict(row)
        result = _json_object(values.pop("result_json"))
        suppliers.append(StoredSupplier(
            **{**values, "submission_date": date.fromisoformat(values["submission_date"]),
               "gates_passed": bool(values["gates_passed"])},
            result=result if isinstance(result, dict) else {}))
    return suppliers


# ---------------------------------------------------------------- review: satisfaction, priorities, re-evaluations (F13)

ACTION_RE_EVALUATE = "re_evaluate"   # the AI read one supplier again, with a comment
ACTION_REWEIGHT = "reweight"         # the person was not satisfied: one criterion counts for more in this run
ACTION_ACCEPT = "accept"             # the person was satisfied and accepted the recommendation (the run becomes final)


@dataclass(frozen=True)
class FeedbackRow:
    """One row of `reviewer_feedback`, with `details_json` opened up into `details` (empty if it cannot be read)."""

    id: int
    supplier_name: str | None
    criterion_id: int | None
    attempt_no: int | None
    rating: int | None
    comment: str
    action: str
    override_score: float | None
    details: dict[str, Any]
    created_at: str


@dataclass(frozen=True)
class AttemptRecord:
    """One criterion of one stored evaluation attempt (the first evaluation is attempt 1)."""

    supplier_name: str
    attempt_no: int
    criterion_id: int
    score: float
    justification: str
    evidence: str
    evidence_found: bool | None
    created_at: str


@dataclass(frozen=True)
class DocumentRecord:
    supplier_name: str
    file_name: str
    sha256: str
    pages: int


def add_feedback(
    conn: sqlite3.Connection,
    run_id: str,
    *,
    action: str,
    created_at: str,
    supplier_name: str | None = None,
    criterion_id: int | None = None,
    attempt_no: int | None = None,
    rating: int | None = None,
    comment: str = "",
    override_score: float | None = None,
    details: dict[str, Any] | None = None,
) -> int:
    """Store one reviewer action and return its id. Rows are only ever added: the table refuses UPDATE and DELETE."""
    cursor = conn.execute(
        "INSERT INTO reviewer_feedback (rfp_run_id, supplier_name, criterion_id, attempt_no, rating, comment, action, "
        "override_score, details_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (run_id, supplier_name, criterion_id, attempt_no, rating, comment, action, override_score,
         json.dumps(details or {}, sort_keys=True, default=str), created_at),
    )
    return int(cursor.lastrowid)


def list_feedback(conn: sqlite3.Connection, run_id: str) -> list[FeedbackRow]:
    """Every reviewer action of a run, oldest first."""
    rows = conn.execute(
        "SELECT id, supplier_name, criterion_id, attempt_no, rating, comment, action, override_score, details_json, "
        "created_at FROM reviewer_feedback WHERE rfp_run_id = ? ORDER BY id",
        (run_id,),
    ).fetchall()
    found: list[FeedbackRow] = []
    for row in rows:
        values = dict(row)
        details = _json_object(values.pop("details_json"))
        found.append(FeedbackRow(**values, details=details if isinstance(details, dict) else {}))
    return found


def list_attempts(conn: sqlite3.Connection, run_id: str) -> list[AttemptRecord]:
    """Every stored criterion result of every attempt of a run, by supplier, attempt and criterion."""
    rows = conn.execute(
        "SELECT supplier_name, attempt_no, criterion_id, score, justification, evidence, evidence_found, created_at "
        "FROM evaluation_attempts WHERE rfp_run_id = ? ORDER BY supplier_name, attempt_no, criterion_id",
        (run_id,),
    ).fetchall()
    return [AttemptRecord(**{**dict(row), "evidence_found": None if row["evidence_found"] is None else bool(row["evidence_found"])})
            for row in rows]


def current_attempt_no(conn: sqlite3.Connection, run_id: str, supplier_name: str) -> int:
    """The number of the latest stored attempt for a supplier (0 when it has none)."""
    row = conn.execute(
        "SELECT MAX(attempt_no) FROM evaluation_attempts WHERE rfp_run_id = ? AND supplier_name = ?", (run_id, supplier_name)
    ).fetchone()
    return int(row[0] or 0)


def list_documents(conn: sqlite3.Connection, run_id: str) -> list[DocumentRecord]:
    rows = conn.execute(
        "SELECT supplier_name, file_name, sha256, pages FROM run_documents WHERE rfp_run_id = ? ORDER BY id", (run_id,)
    ).fetchall()
    return [DocumentRecord(**dict(row)) for row in rows]


def update_supplier_result(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    supplier_name: str,
    absolute_score: float,
    ppi: float,
    industry_score: float,
    final_rank: int,
    gates_passed: bool,
    result_json: str,
) -> None:
    """Bring a supplier's row up to date after a re-evaluation, an override or a re-ranking. The row is the CURRENT
    result; what it used to say stays in `evaluation_attempts`, `reviewer_feedback` and the audit log."""
    cursor = conn.execute(
        "UPDATE supplier_results SET absolute_score = ?, ppi = ?, industry_score = ?, final_rank = ?, gates_passed = ?, "
        "result_json = ? WHERE rfp_run_id = ? AND supplier_name = ?",
        (absolute_score, ppi, industry_score, final_rank, int(gates_passed), result_json, run_id, supplier_name),
    )
    if cursor.rowcount != 1:
        raise sqlite3.DatabaseError(f"No saved result for {supplier_name} in {run_id}.")


def add_to_run_totals(conn: sqlite3.Connection, run_id: str, tokens: int | None, cost_usd: float | None) -> None:
    """Add the tokens and cost of a later AI call (a re-evaluation) to the run's totals. Unknown stays unknown."""
    conn.execute(
        "UPDATE rfp_runs SET total_tokens = CASE WHEN ? IS NULL THEN total_tokens ELSE COALESCE(total_tokens, 0) + ? END, "
        "total_cost_usd = CASE WHEN ? IS NULL THEN total_cost_usd ELSE COALESCE(total_cost_usd, 0) + ? END "
        "WHERE rfp_run_id = ?",
        (tokens, tokens, cost_usd, cost_usd, run_id),
    )
