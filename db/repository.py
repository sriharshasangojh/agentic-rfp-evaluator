"""All reads and writes of the SQLite database. Other modules never open the database themselves."""
from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

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


def create_schema(conn: sqlite3.Connection) -> None:
    """Create every table, index and trigger that does not exist yet."""
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))


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
