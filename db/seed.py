"""Create the tables and fill them with the startup-laptops marking scheme (spec section 5.2).

Seeding is idempotent: each table is filled only while it is empty, so running it again never
adds duplicates and never overwrites changes made later.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from db import repository

TEMPLATE_ID = repository.DEFAULT_TEMPLATE_ID
TEMPLATE_NAME = "Startup laptops"
TEMPLATE_DESCRIPTION = "Startup laptops: 100 business laptops for a growing startup"

# (criterion_id, name, description, weight, max_score)
CRITERIA: tuple[tuple[int, str, str, float, float], ...] = (
    (1, "Technical Capability",
     "Exact processor, RAM, SSD, measured battery life and test method, weight, build durability standard, "
     "ports, Windows 11 Pro, same model available for later orders", 30.0, 10.0),
    (2, "Implementation Plan",
     "Delivery weeks and batches, pilot units, imaging and asset tagging, device-management enrolment, "
     "named team, risk plan", 20.0, 10.0),
    (3, "Commercial Value",
     "Itemised price table, GST, grand total vs budget, assumptions and exclusions, payment terms, "
     "price validity", 20.0, 10.0),
    (4, "Security & Compliance",
     "Encryption enabled before delivery, TPM 2.0, secure boot, certified data wiping, BIS and energy rating, "
     "privacy handling", 20.0, 10.0),
    (5, "Support & Experience",
     "Warranty length and type (on-site or carry-in), repair and replacement times, helpdesk, similar orders, "
     "references", 10.0, 10.0),
)

# (gate_id, name, rule_key, description). The rule itself is coded in tools/gates.py (feature F04).
GATES: tuple[tuple[str, str, str, str], ...] = (
    ("warranty", "Warranty", "warranty_years_min_3",
     "Warranty of at least 3 years. Not stated counts as a fail."),
    ("certification", "Certification", "bis_named",
     "BIS registration must be named. Not stated counts as a fail."),
    ("price_table", "Price table", "price_table_present",
     "The proposal must contain a price table. Not stated counts as a fail."),
    ("delivery", "Delivery", "delivery_weeks_max_8",
     "All units delivered within 8 weeks. Not stated counts as a fail."),
)

# (attribute_key, label, below_rule, meets_rule, above_rule, criterion_id). Wording follows spec section 9.5;
# the grading logic itself is coded in tools/industry.py (feature F05).
BENCHMARKS: tuple[tuple[str, str, str, str, str, int], ...] = (
    ("unit_price", "Unit price",
     "More than Rs 60,000", "Rs 45,000 to Rs 60,000", "Less than Rs 45,000", 3),
    ("warranty", "Warranty",
     "Less than 3 years, or carry-in only", "3 years on-site", "3+ years on-site and repair of 1 day or less", 5),
    ("repair_time", "Repair time",
     "More than 5 working days", "2 to 5 days", "1 day or less, or replacement within 24 h", 5),
    ("battery", "Battery",
     "Less than 8 h, or not measured", "8 to 10 h, measured", "More than 10 h, measured", 1),
    ("memory_storage", "Memory and storage",
     "Less than 16 GB RAM or less than 512 GB SSD", "Exactly 16 GB RAM and 512 GB SSD",
     "More than 16 GB RAM or more than 512 GB SSD", 1),
    ("delivery", "Delivery",
     "More than 8 weeks", "6 to 8 weeks", "Less than 6 weeks", 2),
    ("certifications", "Certifications",
     "None named", "BIS and energy rating named", "Plus a named security or handling certification", 4),
    ("data_wiping", "Data wiping",
     "Not described", "Described", "Certificate per device to a named standard", 4),
)


def _is_empty(conn: sqlite3.Connection, table: str) -> bool:
    # `table` is always one of the constants below, never user input.
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def seed_if_empty(conn: sqlite3.Connection) -> dict[str, int]:
    """Create the schema, then fill each empty seed table. Returns how many rows were added per table."""
    repository.create_schema(conn)
    added: dict[str, int] = {}

    if _is_empty(conn, "industry_templates"):
        conn.execute(
            "INSERT INTO industry_templates (template_id, name, description) VALUES (?, ?, ?)",
            (TEMPLATE_ID, TEMPLATE_NAME, TEMPLATE_DESCRIPTION),
        )
        added["industry_templates"] = 1

    if _is_empty(conn, "evaluation_criteria"):
        conn.executemany(
            "INSERT INTO evaluation_criteria (criterion_id, template_id, name, description, weight, max_score, is_active) "
            "VALUES (?, ?, ?, ?, ?, ?, 1)",
            [(cid, TEMPLATE_ID, name, desc, weight, max_score) for cid, name, desc, weight, max_score in CRITERIA],
        )
        added["evaluation_criteria"] = len(CRITERIA)

    if _is_empty(conn, "gates"):
        conn.executemany(
            "INSERT INTO gates (gate_id, template_id, name, rule_key, description, is_active) VALUES (?, ?, ?, ?, ?, 1)",
            [(gate_id, TEMPLATE_ID, name, rule_key, desc) for gate_id, name, rule_key, desc in GATES],
        )
        added["gates"] = len(GATES)

    if _is_empty(conn, "industry_benchmarks"):
        conn.executemany(
            "INSERT INTO industry_benchmarks "
            "(template_id, attribute_key, label, below_rule, meets_rule, above_rule, criterion_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(TEMPLATE_ID, *row) for row in BENCHMARKS],
        )
        added["industry_benchmarks"] = len(BENCHMARKS)

    return added


def ensure_database(path: Path | None = None) -> dict[str, int]:
    """Open the database, create and seed it if needed. The app calls this on every start."""
    with repository.connection(path) as conn:
        return seed_if_empty(conn)
