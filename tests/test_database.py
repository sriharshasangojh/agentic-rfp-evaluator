"""Tests for the database layer: schema, seed data, repository helpers and the audit log."""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator

import pytest

from db import audit, repository, seed

EXPECTED_TABLES = {
    "industry_templates", "evaluation_criteria", "gates", "industry_benchmarks", "rfp_runs",
    "run_documents", "evaluation_attempts", "reviewer_feedback", "supplier_results", "audit_log",
}
SEED_TABLES = ("industry_templates", "evaluation_criteria", "gates", "industry_benchmarks")


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    with repository.connection() as connection:
        seed.seed_if_empty(connection)
        yield connection


def _count(connection: sqlite3.Connection, table: str) -> int:
    return connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def _add_run(connection: sqlite3.Connection, run_id: str = "RUN-TEST-0001") -> str:
    connection.execute(
        "INSERT INTO rfp_runs (rfp_run_id, created_at, status, template_id, criteria_snapshot_json) "
        "VALUES (?, '2026-10-22T00:00:00+00:00', 'Evaluated', ?, '[]')",
        (run_id, repository.DEFAULT_TEMPLATE_ID),
    )
    return run_id


# --- schema ---------------------------------------------------------------------------------------

def test_schema_creates_all_ten_tables() -> None:
    with repository.connection() as connection:
        repository.create_schema(connection)
        names = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert EXPECTED_TABLES <= names


def test_schema_can_run_twice() -> None:
    with repository.connection() as connection:
        repository.create_schema(connection)
        repository.create_schema(connection)


def test_foreign_keys_are_enforced(conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO evaluation_criteria (criterion_id, template_id, name, description, weight, max_score) "
            "VALUES (99, 'no_such_template', 'x', 'x', 1, 10)"
        )


# --- seed data ------------------------------------------------------------------------------------

def test_seed_inserts_the_marking_scheme(conn: sqlite3.Connection) -> None:
    template = repository.get_template(conn)
    criteria = repository.list_criteria(conn)
    assert template is not None and template.template_id == "startup_laptops"
    assert [c.name for c in criteria] == [
        "Technical Capability", "Implementation Plan", "Commercial Value",
        "Security & Compliance", "Support & Experience",
    ]
    assert [c.weight for c in criteria] == [30, 20, 20, 20, 10]
    assert all(c.max_score == 10 and c.is_active for c in criteria)
    assert [g.gate_id for g in repository.list_gates(conn)] == ["warranty", "certification", "price_table", "delivery"]
    assert [g.rule_key for g in repository.list_gates(conn)] == [
        "warranty_years_min_3", "bis_named", "price_table_present", "delivery_weeks_max_8",
    ]
    assert len(repository.list_benchmarks(conn)) == 8


def test_benchmarks_point_at_real_criteria(conn: sqlite3.Connection) -> None:
    criterion_ids = {c.criterion_id for c in repository.list_criteria(conn)}
    benchmarks = repository.list_benchmarks(conn)
    assert {b.criterion_id for b in benchmarks} <= criterion_ids
    assert {b.attribute_key for b in benchmarks} == {
        "unit_price", "warranty", "repair_time", "battery",
        "memory_storage", "delivery", "certifications", "data_wiping",
    }


def test_seeding_twice_adds_no_duplicates() -> None:
    with repository.connection() as connection:
        first = seed.seed_if_empty(connection)
        counts_after_first = {t: _count(connection, t) for t in SEED_TABLES}
        second = seed.seed_if_empty(connection)
        counts_after_second = {t: _count(connection, t) for t in SEED_TABLES}
    assert first == {"industry_templates": 1, "evaluation_criteria": 5, "gates": 4, "industry_benchmarks": 8}
    assert second == {}
    assert counts_after_first == counts_after_second


def test_seeding_again_keeps_later_edits(conn: sqlite3.Connection) -> None:
    conn.execute("UPDATE evaluation_criteria SET weight = 25 WHERE criterion_id = 1")
    seed.seed_if_empty(conn)
    assert repository.list_criteria(conn)[0].weight == 25


def test_ensure_database_creates_the_file_and_is_repeatable(isolated_database) -> None:
    assert not isolated_database.exists()
    seed.ensure_database()
    assert isolated_database.exists()
    assert seed.ensure_database() == {}


# --- repository helpers ---------------------------------------------------------------------------

def test_active_only_leaves_out_deactivated_criteria(conn: sqlite3.Connection) -> None:
    conn.execute("UPDATE evaluation_criteria SET is_active = 0 WHERE criterion_id = 5")
    assert len(repository.list_criteria(conn)) == 5
    active = repository.list_criteria(conn, active_only=True)
    assert [c.criterion_id for c in active] == [1, 2, 3, 4]


def test_weights_total_counts_only_active_criteria(conn: sqlite3.Connection) -> None:
    assert repository.weights_total(repository.list_criteria(conn)) == 100
    conn.execute("UPDATE evaluation_criteria SET is_active = 0 WHERE criterion_id = 5")
    assert repository.weights_total(repository.list_criteria(conn)) == 90


@pytest.mark.parametrize(
    ("total", "valid"),
    [(100.0, True), (100.01, True), (99.99, True), (100.02, False), (99.0, False), (0.0, False)],
)
def test_weights_are_valid_within_tolerance(total: float, valid: bool) -> None:
    assert repository.weights_are_valid(total) is valid


# --- audit log ------------------------------------------------------------------------------------

def test_audit_event_is_stored(conn: sqlite3.Connection) -> None:
    run_id = _add_run(conn)
    event_id = audit.log_event(conn, run_id, "AI", "supplier_evaluated", {"supplier": "Apex Systems", "attempt_no": 1})
    row = conn.execute("SELECT * FROM audit_log WHERE id = ?", (event_id,)).fetchone()
    assert (row["rfp_run_id"], row["actor"], row["action"]) == (run_id, "AI", "supplier_evaluated")
    assert json.loads(row["details_json"]) == {"supplier": "Apex Systems", "attempt_no": 1}
    assert row["timestamp"].endswith("+00:00")


def test_audit_log_cannot_be_changed_or_deleted(conn: sqlite3.Connection) -> None:
    run_id = _add_run(conn)
    event_id = audit.log_event(conn, run_id, "system", "run_created")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE audit_log SET actor = 'someone else' WHERE id = ?", (event_id,))
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM audit_log WHERE id = ?", (event_id,))
    assert _count(conn, "audit_log") == 1


@pytest.mark.parametrize(
    "details",
    [{"api_key": "x"}, {"provider": "OpenAI", "OPENAI_API_KEY": "x"}, {"nested": {"password": "x"}}, {"items": [{"secret": "x"}]}],
)
def test_audit_log_refuses_secrets(conn: sqlite3.Connection, details: dict) -> None:
    run_id = _add_run(conn)
    with pytest.raises(ValueError, match="never stored"):
        audit.log_event(conn, run_id, "system", "settings", details)
    assert _count(conn, "audit_log") == 0


def test_audit_log_needs_an_existing_run(conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        audit.log_event(conn, "RUN-DOES-NOT-EXIST", "system", "run_created")
