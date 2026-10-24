-- RFP Evaluator schema (spec section 5.1). Safe to run on every start: every statement is IF NOT EXISTS.
-- Timestamps are ISO 8601 text in UTC. Foreign keys are switched on per connection (db/repository.py).

CREATE TABLE IF NOT EXISTS industry_templates (
    template_id TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evaluation_criteria (
    criterion_id INTEGER PRIMARY KEY,
    template_id  TEXT    NOT NULL REFERENCES industry_templates (template_id),
    name         TEXT    NOT NULL,
    description  TEXT    NOT NULL,
    weight       REAL    NOT NULL CHECK (weight >= 0),
    max_score    REAL    NOT NULL CHECK (max_score > 0),
    is_active    INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1))
);

CREATE TABLE IF NOT EXISTS gates (
    gate_id     TEXT PRIMARY KEY,
    template_id TEXT    NOT NULL REFERENCES industry_templates (template_id),
    name        TEXT    NOT NULL,
    rule_key    TEXT    NOT NULL,
    description TEXT    NOT NULL,
    is_active   INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1))
);

CREATE TABLE IF NOT EXISTS industry_benchmarks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    template_id  TEXT    NOT NULL REFERENCES industry_templates (template_id),
    attribute_key TEXT   NOT NULL,
    label        TEXT    NOT NULL,
    below_rule   TEXT    NOT NULL,
    meets_rule   TEXT    NOT NULL,
    above_rule   TEXT    NOT NULL,
    criterion_id INTEGER NOT NULL REFERENCES evaluation_criteria (criterion_id),
    UNIQUE (template_id, attribute_key)
);

CREATE TABLE IF NOT EXISTS rfp_runs (
    rfp_run_id             TEXT PRIMARY KEY,
    created_at             TEXT NOT NULL,
    status                 TEXT NOT NULL,
    provider               TEXT,
    model                  TEXT,
    temperature            REAL,
    template_id            TEXT NOT NULL REFERENCES industry_templates (template_id),
    criteria_snapshot_json TEXT NOT NULL,
    reviewer_name          TEXT,
    total_tokens           INTEGER,
    total_cost_usd         REAL,
    duration_seconds       REAL,
    budget_rs              REAL,      -- the buyer's total budget for this run (added in F09; older databases get it by migration)
    quantity               INTEGER    -- the number of units being bought in this run (added in F09)
);

CREATE TABLE IF NOT EXISTS run_documents (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    rfp_run_id    TEXT    NOT NULL REFERENCES rfp_runs (rfp_run_id),
    supplier_name TEXT    NOT NULL,
    file_name     TEXT    NOT NULL,
    sha256        TEXT    NOT NULL,
    pages         INTEGER NOT NULL
);

-- Every LLM attempt is kept; nothing is overwritten. evidence_found: 1 found, 0 not found, NULL not applicable.
CREATE TABLE IF NOT EXISTS evaluation_attempts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    rfp_run_id     TEXT    NOT NULL REFERENCES rfp_runs (rfp_run_id),
    supplier_name  TEXT    NOT NULL,
    attempt_no     INTEGER NOT NULL,
    criterion_id   INTEGER NOT NULL,
    score          REAL    NOT NULL,
    justification  TEXT    NOT NULL,
    evidence       TEXT    NOT NULL,
    evidence_found INTEGER CHECK (evidence_found IS NULL OR evidence_found IN (0, 1)),
    raw_json       TEXT,
    created_at     TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_attempts_run_supplier ON evaluation_attempts (rfp_run_id, supplier_name);

-- Ratings, re-evaluations, overrides and feedback rounds. Run-wide actions (reweight, accept) have no supplier
-- or attempt number, so those two columns may be NULL.
CREATE TABLE IF NOT EXISTS reviewer_feedback (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    rfp_run_id    TEXT NOT NULL REFERENCES rfp_runs (rfp_run_id),
    supplier_name TEXT,
    criterion_id  INTEGER,
    attempt_no    INTEGER,
    rating        INTEGER CHECK (rating IS NULL OR rating BETWEEN 1 AND 5),
    comment       TEXT NOT NULL DEFAULT '',
    action        TEXT NOT NULL CHECK (action IN
                      ('rate', 're_evaluate', 'override', 'reweight', 'set_aside', 'manual_choice', 'accept')),
    override_score REAL,
    details_json  TEXT,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_feedback_run ON reviewer_feedback (rfp_run_id);

CREATE TABLE IF NOT EXISTS supplier_results (
    rfp_run_id        TEXT    NOT NULL REFERENCES rfp_runs (rfp_run_id),
    supplier_name     TEXT    NOT NULL,
    submission_date   TEXT    NOT NULL,
    experience_rating INTEGER CHECK (experience_rating IS NULL OR experience_rating BETWEEN 1 AND 5),   -- optional (F09 round 2)
    absolute_score    REAL    NOT NULL,
    ppi               REAL    NOT NULL,
    industry_score    REAL    NOT NULL,
    final_rank        INTEGER NOT NULL,
    gates_passed      INTEGER NOT NULL CHECK (gates_passed IN (0, 1)),
    result_json       TEXT    NOT NULL,
    PRIMARY KEY (rfp_run_id, supplier_name)
);

-- Append only: the two triggers below make UPDATE and DELETE fail.
CREATE TABLE IF NOT EXISTS audit_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    rfp_run_id   TEXT NOT NULL REFERENCES rfp_runs (rfp_run_id),
    timestamp    TEXT NOT NULL,
    actor        TEXT NOT NULL,
    action       TEXT NOT NULL,
    details_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_run ON audit_log (rfp_run_id);

CREATE TRIGGER IF NOT EXISTS audit_log_no_update
BEFORE UPDATE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log is append-only');
END;

CREATE TRIGGER IF NOT EXISTS audit_log_no_delete
BEFORE DELETE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log is append-only');
END;

-- Attempts and reviewer actions are history: like the audit log, they can be added but never changed or removed (F13).
CREATE TRIGGER IF NOT EXISTS attempts_no_update
BEFORE UPDATE ON evaluation_attempts
BEGIN
    SELECT RAISE(ABORT, 'evaluation_attempts is append-only');
END;

CREATE TRIGGER IF NOT EXISTS attempts_no_delete
BEFORE DELETE ON evaluation_attempts
BEGIN
    SELECT RAISE(ABORT, 'evaluation_attempts is append-only');
END;

CREATE TRIGGER IF NOT EXISTS feedback_no_update
BEFORE UPDATE ON reviewer_feedback
BEGIN
    SELECT RAISE(ABORT, 'reviewer_feedback is append-only');
END;

CREATE TRIGGER IF NOT EXISTS feedback_no_delete
BEFORE DELETE ON reviewer_feedback
BEGIN
    SELECT RAISE(ABORT, 'reviewer_feedback is append-only');
END;
