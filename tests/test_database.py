"""Tests for the database layer: schema, seed data, repository helpers and the audit log."""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from datetime import date

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


# --- runs: columns, migration and the write functions (F09) -----------------------------------------

def _start_run(connection: sqlite3.Connection, run_id: str = "RUN-TEST-0002") -> str:
    repository.create_run(
        connection, run_id=run_id, created_at="2026-10-23T00:00:00+00:00", provider="OpenAI", model="m",
        temperature=0, template_id=repository.DEFAULT_TEMPLATE_ID, criteria_snapshot_json="[]",
        reviewer_name="Sri", budget_rs=6000000, quantity=100,
    )
    return run_id


def test_runs_keep_the_budget_and_quantity(conn: sqlite3.Connection) -> None:
    run_id = _start_run(conn)
    row = conn.execute("SELECT * FROM rfp_runs WHERE rfp_run_id = ?", (run_id,)).fetchone()
    assert (row["status"], row["budget_rs"], row["quantity"], row["reviewer_name"]) == ("Running", 6000000, 100, "Sri")
    assert (row["provider"], row["model"], row["temperature"]) == ("OpenAI", "m", 0)


def test_an_older_database_gets_the_new_columns_and_keeps_its_rows(conn: sqlite3.Connection) -> None:
    _add_run(conn)   # a run with no budget or quantity, as older versions stored them
    conn.execute("ALTER TABLE rfp_runs DROP COLUMN budget_rs")
    conn.execute("ALTER TABLE rfp_runs DROP COLUMN quantity")
    assert "budget_rs" not in {r[1] for r in conn.execute("PRAGMA table_info(rfp_runs)")}
    repository.create_schema(conn)
    repository.create_schema(conn)   # and again: nothing breaks
    columns = {r[1] for r in conn.execute("PRAGMA table_info(rfp_runs)")}
    assert {"budget_rs", "quantity"} <= columns
    kinds = {r[1]: r[2] for r in conn.execute("PRAGMA table_info(rfp_runs)")}
    assert (kinds["budget_rs"], kinds["quantity"]) == ("REAL", "INTEGER")   # the same types a new database has
    row = conn.execute("SELECT * FROM rfp_runs WHERE rfp_run_id = 'RUN-TEST-0001'").fetchone()
    assert row["status"] == "Evaluated" and row["budget_rs"] is None and row["quantity"] is None


def test_finishing_a_run_stores_the_totals(conn: sqlite3.Connection) -> None:
    run_id = _start_run(conn)
    repository.finish_run(conn, run_id, status="Evaluated", total_tokens=12345, total_cost_usd=0.5, duration_seconds=61.5)
    row = conn.execute("SELECT * FROM rfp_runs WHERE rfp_run_id = ?", (run_id,)).fetchone()
    assert (row["status"], row["total_tokens"], row["total_cost_usd"], row["duration_seconds"]) == ("Evaluated", 12345, 0.5, 61.5)
    repository.set_run_status(conn, run_id, "Failed")
    assert conn.execute("SELECT status FROM rfp_runs WHERE rfp_run_id = ?", (run_id,)).fetchone()[0] == "Failed"


def test_documents_attempts_and_results_are_stored_under_the_run(conn: sqlite3.Connection) -> None:
    run_id = _start_run(conn)
    repository.add_document(conn, run_id, "Apex Systems", "apex_systems.pdf", "ab" * 32, 6)
    repository.add_attempt(conn, run_id, "Apex Systems", 1, [
        repository.AttemptRow(1, 9.0, "why", "quote (Page 2)", None, "{}"),
        repository.AttemptRow(2, 7.5, "why 2", "Not stated in the proposal", True),
        repository.AttemptRow(3, 0.0, "why 3", "Not stated in the proposal", False),
    ], "2026-10-23T00:00:01+00:00")
    repository.save_supplier_result(
        conn, run_id=run_id, supplier_name="Apex Systems", submission_date="2026-10-05", experience_rating=4,
        absolute_score=77.0, ppi=86.94, industry_score=60.0, final_rank=1, gates_passed=True, result_json="{}")
    assert conn.execute("SELECT pages, sha256 FROM run_documents").fetchone()[:] == (6, "ab" * 32)
    rows = conn.execute("SELECT criterion_id, score, evidence_found, raw_json FROM evaluation_attempts ORDER BY criterion_id").fetchall()
    assert [tuple(r) for r in rows] == [(1, 9.0, None, "{}"), (2, 7.5, 1, None), (3, 0.0, 0, None)]
    result = conn.execute("SELECT * FROM supplier_results").fetchone()
    assert (result["final_rank"], result["gates_passed"], result["ppi"]) == (1, 1, 86.94)


def test_run_data_needs_an_existing_run(conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        repository.add_document(conn, "RUN-NOPE", "A", "a.pdf", "x", 1)
    with pytest.raises(sqlite3.IntegrityError):
        repository.add_attempt(conn, "RUN-NOPE", "A", 1, [repository.AttemptRow(1, 1.0, "w", "e")], "t")


def test_the_same_supplier_cannot_be_saved_twice_in_one_run(conn: sqlite3.Connection) -> None:
    run_id = _start_run(conn)
    kwargs = dict(run_id=run_id, supplier_name="A", submission_date="2026-10-05", experience_rating=3, absolute_score=1.0,
                  ppi=1.0, industry_score=1.0, final_rank=1, gates_passed=False, result_json="{}")
    repository.save_supplier_result(conn, **kwargs)
    with pytest.raises(sqlite3.IntegrityError):
        repository.save_supplier_result(conn, **kwargs)


def _result_kwargs(run_id: str, name: str = "A", rating: int | None = 3) -> dict:
    return dict(run_id=run_id, supplier_name=name, submission_date="2026-10-05", experience_rating=rating, absolute_score=1.0,
                ppi=1.0, industry_score=1.0, final_rank=1, gates_passed=False, result_json="{}")


def test_the_experience_rating_is_optional_but_must_be_1_to_5_when_given(conn: sqlite3.Connection) -> None:
    run_id = _start_run(conn)
    repository.save_supplier_result(conn, **_result_kwargs(run_id, "Unrated", None))
    repository.save_supplier_result(conn, **_result_kwargs(run_id, "Rated", 5))
    stored = {r["supplier_name"]: r["experience_rating"] for r in conn.execute("SELECT * FROM supplier_results")}
    assert stored == {"Unrated": None, "Rated": 5}
    for bad in (0, 6):
        with pytest.raises(sqlite3.IntegrityError):
            repository.save_supplier_result(conn, **_result_kwargs(run_id, f"Bad{bad}", bad))


def test_a_database_that_still_requires_the_rating_is_rebuilt_without_losing_rows(conn: sqlite3.Connection) -> None:
    run_id = _start_run(conn)
    conn.execute("DROP TABLE supplier_results")
    conn.execute(
        "CREATE TABLE supplier_results (rfp_run_id TEXT NOT NULL REFERENCES rfp_runs (rfp_run_id), supplier_name TEXT NOT NULL, "
        "submission_date TEXT NOT NULL, experience_rating INTEGER NOT NULL CHECK (experience_rating BETWEEN 1 AND 5), "
        "absolute_score REAL NOT NULL, ppi REAL NOT NULL, industry_score REAL NOT NULL, final_rank INTEGER NOT NULL, "
        "gates_passed INTEGER NOT NULL CHECK (gates_passed IN (0, 1)), result_json TEXT NOT NULL, PRIMARY KEY (rfp_run_id, supplier_name))")
    conn.execute("INSERT INTO supplier_results VALUES (?, 'Old', '2026-10-05', 4, 77.0, 86.94, 36.25, 1, 1, '{\"kept\": true}')", (run_id,))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO supplier_results VALUES (?, 'New', '2026-10-05', NULL, 1, 1, 1, 2, 0, '{}')", (run_id,))

    repository.create_schema(conn)
    statements: list[str] = []
    conn.set_trace_callback(statements.append)
    repository.create_schema(conn)   # a second time changes nothing: the table is not rebuilt again
    conn.set_trace_callback(None)
    assert not [s for s in statements if "supplier_results" in s and ("RENAME" in s.upper() or "DROP" in s.upper())]

    column = next(r for r in conn.execute("PRAGMA table_info(supplier_results)") if r[1] == "experience_rating")
    assert column[3] == 0   # no longer NOT NULL
    assert "supplier_results_old" not in {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
    kept = conn.execute("SELECT * FROM supplier_results").fetchall()
    assert [tuple(r) for r in kept] == [(run_id, "Old", "2026-10-05", 4, 77.0, 86.94, 36.25, 1, 1, '{"kept": true}')]
    repository.save_supplier_result(conn, **_result_kwargs(run_id, "Unrated", None))   # now allowed
    with pytest.raises(sqlite3.IntegrityError):   # the other rules came across too
        repository.save_supplier_result(conn, **_result_kwargs(run_id, "Old", 2))
    with pytest.raises(sqlite3.IntegrityError):
        repository.save_supplier_result(conn, **_result_kwargs(run_id, "Bad", 9))
    with pytest.raises(sqlite3.IntegrityError):
        repository.save_supplier_result(conn, **_result_kwargs("RUN-NOPE", "X", 3))


# --- reading a saved run back (F10) -------------------------------------------------------------------

def _save(conn: sqlite3.Connection, run_id: str, name: str, rank: int, *, passed: bool = True, result_json: str = "{}",
          rating: int | None = 3, when: str = "2026-10-05") -> None:
    repository.save_supplier_result(
        conn, run_id=run_id, supplier_name=name, submission_date=when, experience_rating=rating, absolute_score=70.5,
        ppi=80.25, industry_score=61.0, final_rank=rank, gates_passed=passed, result_json=result_json)


def test_a_run_is_read_back_with_its_settings_and_the_criteria_snapshot(conn: sqlite3.Connection) -> None:
    run_id = "RUN-TEST-0003"
    repository.create_run(
        conn, run_id=run_id, created_at="2026-10-23T09:30:00+00:00", provider="OpenAI", model="gpt-4o-mini", temperature=0,
        template_id=repository.DEFAULT_TEMPLATE_ID,
        criteria_snapshot_json=json.dumps([{"criterion_id": 1, "name": "Technical Capability", "weight": 30, "max_score": 10}]),
        reviewer_name="Sri", budget_rs=6000000, quantity=100)
    repository.finish_run(conn, run_id, status="Evaluated", total_tokens=900, total_cost_usd=0.02, duration_seconds=12.5)
    run = repository.get_run(conn, run_id)
    assert run == repository.RunRecord(
        rfp_run_id=run_id, created_at="2026-10-23T09:30:00+00:00", status="Evaluated", provider="OpenAI", model="gpt-4o-mini",
        temperature=0, template_id=repository.DEFAULT_TEMPLATE_ID,
        criteria_snapshot=[{"criterion_id": 1, "name": "Technical Capability", "weight": 30, "max_score": 10}],
        reviewer_name="Sri", total_tokens=900, total_cost_usd=0.02, duration_seconds=12.5, budget_rs=6000000, quantity=100)


def test_an_unknown_run_is_none_and_a_run_without_budget_reads_back_empty(conn: sqlite3.Connection) -> None:
    assert repository.get_run(conn, "RUN-NOPE") is None
    older = repository.get_run(conn, _add_run(conn))   # saved before F09: no budget, quantity, model or reviewer
    assert older is not None and (older.budget_rs, older.quantity, older.model, older.criteria_snapshot) == (None, None, None, [])


def test_a_damaged_criteria_snapshot_reads_as_empty_not_as_an_error(conn: sqlite3.Connection) -> None:
    run_id = _start_run(conn)
    conn.execute("UPDATE rfp_runs SET criteria_snapshot_json = ? WHERE rfp_run_id = ?", ("{not json", run_id))
    assert repository.get_run(conn, run_id).criteria_snapshot == []   # type: ignore[union-attr]
    conn.execute("UPDATE rfp_runs SET criteria_snapshot_json = ? WHERE rfp_run_id = ?", ('{"a": 1}', run_id))
    assert repository.get_run(conn, run_id).criteria_snapshot == []   # type: ignore[union-attr]


def test_suppliers_come_back_best_rank_first_with_their_result_opened_up(conn: sqlite3.Connection) -> None:
    run_id = _start_run(conn)
    # the names are in the opposite order to the ranks, so sorting by name would give the wrong answer
    _save(conn, run_id, "Alpha", 3, passed=False, result_json='{"eligible": false}', rating=None, when="2026-10-09")
    _save(conn, run_id, "Zulu", 1, result_json='{"gates": {"warranty": "pass"}}')
    _save(conn, run_id, "Mike", 2, result_json='{"tie_break_explanation": "Mike above Alpha: x"}', rating=5)
    found = repository.list_supplier_results(conn, run_id)
    assert [s.supplier_name for s in found] == ["Zulu", "Mike", "Alpha"]
    first, second, third = found
    assert first == repository.StoredSupplier("Zulu", date(2026, 10, 5), 3, 70.5, 80.25, 61.0, 1, True, {"gates": {"warranty": "pass"}})
    assert second.experience_rating == 5 and second.result == {"tie_break_explanation": "Mike above Alpha: x"}
    assert (third.experience_rating, third.gates_passed, third.submission_date) == (None, False, date(2026, 10, 9))


def test_only_the_suppliers_of_that_run_are_returned(conn: sqlite3.Connection) -> None:
    one, two = _start_run(conn, "RUN-ONE"), _start_run(conn, "RUN-TWO")
    _save(conn, one, "A", 1)
    _save(conn, two, "B", 1)
    assert [s.supplier_name for s in repository.list_supplier_results(conn, one)] == ["A"]
    assert repository.list_supplier_results(conn, "RUN-NOPE") == []


@pytest.mark.parametrize("stored", ["", "{broken", "[1, 2]", '"text"'])
def test_a_result_that_cannot_be_read_comes_back_as_an_empty_mapping(conn: sqlite3.Connection, stored: str) -> None:
    run_id = _start_run(conn)
    _save(conn, run_id, "A", 1, result_json=stored)
    assert repository.list_supplier_results(conn, run_id)[0].result == {}
