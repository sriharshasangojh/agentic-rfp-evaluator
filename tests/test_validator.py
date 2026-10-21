"""Tests for tools/validator.py (spec 9.1 and 16.1). No API key or network needed."""
from __future__ import annotations

import ast
import copy
import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from db import repository
from db.seed import ensure_database
from tools.validator import (
    NO_JUSTIFICATION,
    NO_RESULT,
    NOT_STATED,
    ExtractedFacts,
    failed_evaluation,
    validate_evaluation,
)


@dataclass(frozen=True)
class Crit:
    criterion_id: int
    name: str
    max_score: float = 10


CRITERIA = (
    Crit(1, "Technical Capability"),
    Crit(2, "Implementation Plan"),
    Crit(3, "Commercial Value"),
    Crit(4, "Security & Compliance"),
    Crit(5, "Support & Experience"),
)


def entry(criterion_id, score=7, justification="Specific details are given.", evidence="Battery 11.5 hours (Page 2)"):
    return {"criterion_id": criterion_id, "score": score, "justification": justification, "evidence": evidence}


def good_reply():
    return {
        "supplier_name": "Supplier A",
        "criteria": [entry(i, s) for i, s in zip(range(1, 6), (9, 7, 5, 8, 6))],
        "extracted_facts": {"laptop_model": "ProBook 14", "ram_gb": 16, "warranty_years": 3, "bis_named": True},
        "risks": ["Delivery is in two batches"],
        "overall_summary": "A solid proposal.",
    }


def reply_with_criteria(*entries):
    reply = good_reply()
    reply["criteria"] = list(entries)
    return reply


def validate(reply, criteria=CRITERIA):
    return validate_evaluation(reply, criteria)


def score_of(result, criterion_id):
    return next(c for c in result.scorecard.criteria if c.criterion_id == criterion_id)


# ---------------------------------------------------------------- a clean reply

def test_clean_reply_comes_through_unchanged_with_no_warnings():
    result = validate(good_reply())
    assert result.usable and result.error is None
    assert result.warnings == ()
    assert [(c.criterion_id, c.score) for c in result.scorecard.criteria] == [(1, 9), (2, 7), (3, 5), (4, 8), (5, 6)]
    assert not any(c.filled_by_validator for c in result.scorecard.criteria)
    assert score_of(result, 1).justification == "Specific details are given."
    assert score_of(result, 1).evidence == "Battery 11.5 hours (Page 2)"
    assert result.scorecard.extracted_facts.ram_gb == 16
    assert result.scorecard.extracted_facts.laptop_model == "ProBook 14"
    assert result.scorecard.risks == ("Delivery is in two batches",)
    assert result.scorecard.overall_summary == "A solid proposal."


def test_json_text_and_a_parsed_object_give_the_same_result():
    assert validate(json.dumps(good_reply())) == validate(good_reply())


def test_scorecard_follows_database_order_whatever_order_the_reply_used():
    reply = good_reply()
    reply["criteria"] = [entry(5), entry(3), entry(1), entry(4), entry(2)]
    assert [c.criterion_id for c in validate(reply).scorecard.criteria] == [1, 2, 3, 4, 5]


def test_the_suppliers_name_in_the_reply_is_not_kept():
    result = validate(good_reply())
    assert "supplier_name" not in result.scorecard.model_dump()


def test_the_reply_is_never_modified():
    reply = good_reply()
    reply["criteria"][0]["score"] = "12"
    before = copy.deepcopy(reply)
    validate(reply)
    assert reply == before


def test_a_scorecard_is_plain_data_that_can_be_stored_as_json():
    dumped = validate(good_reply()).scorecard.model_dump(mode="json")
    assert json.loads(json.dumps(dumped)) == dumped
    assert set(dumped) == {"criteria", "extracted_facts", "risks", "overall_summary"}
    assert set(dumped["criteria"][0]) == {"criterion_id", "score", "justification", "evidence", "filled_by_validator"}


# ---------------------------------------------------------------- not valid JSON (retry, then fail)

@pytest.mark.parametrize(
    "reply, error",
    [
        ("", "not valid JSON"),
        ("Here is my evaluation of the supplier.", "not valid JSON"),
        ('{"criteria": [', "not valid JSON"),
        ("{'criteria': []}", "not valid JSON"),
        (b"\xff\xfe\x00", "not valid JSON"),
        ("[]", "not a JSON object"),
        ('"just text"', "not a JSON object"),
        ("null", "not a JSON object"),
        ("42", "not a JSON object"),
        ('{"supplier_name": "Supplier A"}', "no 'criteria' list"),
        ('{"criteria": "none"}', "no 'criteria' list"),
        ('{"criteria": {"1": 5}}', "no 'criteria' list"),
    ],
)
def test_unusable_replies_give_no_scorecard_and_a_reason(reply, error):
    result = validate(reply)
    assert not result.usable
    assert result.scorecard is None
    assert error in result.error


def test_failed_evaluation_scores_everything_zero_and_says_so():
    result = failed_evaluation(CRITERIA)
    assert result.usable and result.error is None
    assert [c.score for c in result.scorecard.criteria] == [0, 0, 0, 0, 0]
    assert all(c.filled_by_validator for c in result.scorecard.criteria)
    assert all(c.justification == NO_RESULT and c.evidence == NOT_STATED for c in result.scorecard.criteria)
    assert result.scorecard.extracted_facts == ExtractedFacts()
    assert result.warnings == ("Evaluation failed: the reply was not valid JSON after one retry.",)


def test_a_json_reply_wrapped_in_a_code_block_is_accepted_with_a_warning():
    text = json.dumps(good_reply())
    for wrapped in (f"```json\n{text}\n```", f"```\n{text}\n```", f"  ```JSON\r\n{text}\r\n```  \n"):
        result = validate(wrapped)
        assert result.usable
        assert result.warnings == ("The reply was wrapped in a code block; the JSON inside was used.",)
        assert score_of(result, 1).score == 9


def test_text_around_json_is_still_invalid():
    assert not validate(f"Here you go:\n{json.dumps(good_reply())}").usable


# ---------------------------------------------------------------- criteria: missing, unknown, duplicate

def test_a_missing_criterion_is_added_with_score_0():
    reply = reply_with_criteria(entry(1), entry(2), entry(4), entry(5))
    result = validate(reply)
    filled = score_of(result, 3)
    assert (filled.score, filled.justification, filled.evidence) == (0.0, "No result returned", "Not stated in the proposal")
    assert filled.filled_by_validator is True
    assert score_of(result, 2).filled_by_validator is False
    assert result.warnings == ("Criterion 3 (Commercial Value) was missing from the reply and was set to 0.",)


def test_an_empty_criteria_list_fills_every_criterion():
    result = validate(reply_with_criteria())
    assert result.usable
    assert [c.score for c in result.scorecard.criteria] == [0, 0, 0, 0, 0]
    assert len(result.warnings) == 5


@pytest.mark.parametrize("bad_id, shown", [(99, "99"), ("abc", "'abc'"), (None, "None"), (True, "True"), (2.5, "2.5"), ([1], "[1]")])
def test_an_unknown_criterion_id_is_dropped(bad_id, shown):
    reply = good_reply()
    reply["criteria"].append(entry(bad_id, score=10))
    result = validate(reply)
    assert result.warnings == (f"Unknown criterion_id {shown} was dropped.",)
    assert [c.score for c in result.scorecard.criteria] == [9, 7, 5, 8, 6]


def test_an_unknown_id_does_not_stand_in_for_a_missing_criterion():
    reply = reply_with_criteria(entry(1), entry(2), entry(3), entry(4), entry(99))
    result = validate(reply)
    assert score_of(result, 5).filled_by_validator is True
    assert len(result.warnings) == 2


@pytest.mark.parametrize("written_id", ["2", " 2 ", 2.0])
def test_an_id_written_as_text_or_a_whole_float_is_understood(written_id):
    reply = good_reply()
    reply["criteria"][1]["criterion_id"] = written_id
    result = validate(reply)
    assert result.warnings == ()
    assert score_of(result, 2).score == 7


def test_a_duplicate_criterion_id_keeps_the_first_entry():
    reply = good_reply()
    reply["criteria"].append(entry(3, score=1, justification="Second try."))
    result = validate(reply)
    assert score_of(result, 3).score == 5
    assert score_of(result, 3).justification != "Second try."
    assert result.warnings == ("Criterion 3 (Commercial Value) appeared more than once; the first entry was kept.",)


def test_an_entry_that_is_not_an_object_is_dropped():
    reply = good_reply()
    reply["criteria"].append("Criterion 6: great")
    result = validate(reply)
    assert [c.score for c in result.scorecard.criteria] == [9, 7, 5, 8, 6]
    assert len(result.warnings) == 1 and result.warnings[0].startswith("A criteria entry was not an object")


# ---------------------------------------------------------------- scores

def score_for(raw, max_score=10):
    criteria = (Crit(1, "Technical Capability", max_score),)
    reply = {"criteria": [entry(1, raw)], "extracted_facts": {}}
    result = validate(reply, criteria)
    return result.scorecard.criteria[0].score, result.warnings


@pytest.mark.parametrize(
    "raw, expected, warning",
    [
        (8, 8.0, None),
        (8.5, 8.5, None),
        (0, 0.0, None),
        (10, 10.0, None),
        ("8", 8.0, "Criterion 1 (Technical Capability): score '8' was read as 8."),
        ("8/10", 8.0, "Criterion 1 (Technical Capability): score '8/10' was read as 8."),
        (" 7.5 / 10 ", 7.5, "Criterion 1 (Technical Capability): score ' 7.5 / 10 ' was read as 7.5."),
        ("4/5", 8.0, "Criterion 1 (Technical Capability): score '4/5' was read as 8."),
    ],
)
def test_scores_that_are_numbers_or_number_text_are_read(raw, expected, warning):
    score, warnings = score_for(raw)
    assert score == expected
    assert warnings == (() if warning is None else (warning,))


@pytest.mark.parametrize(
    "raw, shown",
    [
        ("eight", "'eight'"),
        ("8 out of 10", "'8 out of 10'"),
        ("8/0", "'8/0'"),
        ("", "''"),
        ("NaN", "'NaN'"),
        (None, "None"),
        (True, "True"),
        ([], "[]"),
        ({}, "{}"),
        (float("nan"), "nan"),
        (float("inf"), "inf"),
    ],
)
def test_a_score_that_is_not_a_number_becomes_0_with_a_warning(raw, shown):
    score, warnings = score_for(raw)
    assert score == 0.0
    assert warnings == (f"Criterion 1 (Technical Capability): score {shown} is not a number and was set to 0.",)


def test_a_missing_score_field_becomes_0():
    reply = good_reply()
    del reply["criteria"][0]["score"]
    result = validate(reply)
    assert score_of(result, 1).score == 0
    assert result.warnings == ("Criterion 1 (Technical Capability): score None is not a number and was set to 0.",)


@pytest.mark.parametrize(
    "raw, expected, message",
    [
        (12, 10.0, "score 12 is above the maximum 10 and was set to 10."),
        (10.04, 10.0, "score 10.04 is above the maximum 10 and was set to 10."),
        (-3, 0.0, "score -3 is below 0 and was set to 0."),
        (-0.5, 0.0, "score -0.5 is below 0 and was set to 0."),
    ],
)
def test_a_score_outside_the_range_is_clipped(raw, expected, message):
    score, warnings = score_for(raw)
    assert score == expected
    assert warnings == (f"Criterion 1 (Technical Capability): {message}",)


def test_text_that_reads_as_an_out_of_range_score_is_clipped_too():
    score, warnings = score_for("12")
    assert score == 10.0
    assert len(warnings) == 2
    assert "was read as 12" in warnings[0] and "above the maximum 10" in warnings[1]


@pytest.mark.parametrize(
    "raw, expected",
    [
        (7.25, 7.3),
        (7.24, 7.2),
        (7.26, 7.3),
        (7.35, 7.4),   # half up on the number as written (a binary float would give 7.3)
        (6.05, 6.1),
        (0.05, 0.1),
        (9.96, 10.0),
    ],
)
def test_scores_are_rounded_half_up_to_one_decimal_with_a_warning(raw, expected):
    score, warnings = score_for(raw)
    assert score == expected
    assert len(warnings) == 1 and "(1 decimal)" in warnings[0]


def test_a_score_with_one_decimal_is_left_alone():
    assert score_for(7.5) == (7.5, ())


def test_max_score_comes_from_the_criteria_not_from_the_reply():
    criteria = (Crit(1, "Technical Capability", 20),)
    reply = {"criteria": [{**entry(1, 15), "max_score": 100}]}
    assert validate(reply, criteria).scorecard.criteria[0].score == 15
    reply = {"criteria": [{**entry(1, 25), "max_score": 100}]}
    result = validate(reply, criteria)
    assert result.scorecard.criteria[0].score == 20
    assert "above the maximum 20" in result.warnings[0]


def test_a_fraction_is_scaled_to_the_criterions_own_maximum():
    assert score_for("8/10", max_score=20)[0] == 16.0


def test_rounding_never_pushes_a_score_above_an_odd_maximum():
    score, _ = score_for(7.55, max_score=7.55)
    assert score == 7.55


# ---------------------------------------------------------------- justification and evidence

@pytest.mark.parametrize("blank", [None, "", "   ", 5, []])
def test_a_blank_justification_or_evidence_gets_a_default_and_a_warning(blank):
    reply = good_reply()
    reply["criteria"][0]["justification"] = blank
    reply["criteria"][0]["evidence"] = blank
    result = validate(reply)
    assert score_of(result, 1).justification == NO_JUSTIFICATION
    assert score_of(result, 1).evidence == NOT_STATED
    assert score_of(result, 1).filled_by_validator is False
    assert len(result.warnings) == 2


def test_text_is_trimmed():
    reply = good_reply()
    reply["criteria"][0]["justification"] = "  Good.  \n"
    assert score_of(validate(reply), 1).justification == "Good."


# ---------------------------------------------------------------- extracted facts

def facts_reply(facts):
    reply = good_reply()
    reply["extracted_facts"] = facts
    return reply


@pytest.mark.parametrize(
    "name, bad_value",
    [
        ("ram_gb", "16 GB"),
        ("ram_gb", True),
        ("ram_gb", [16]),
        ("battery_hours", "11.5"),
        ("delivery_weeks", float("nan")),
        ("grand_total_rs", "5,50,000"),
        ("grand_total_rs", float("inf")),
        ("bis_named", "yes"),
        ("bis_named", 1),
        ("price_table_present", "true"),
        ("laptop_model", 14),
        ("processor", ["Intel"]),
        ("warranty_type", "onsite"),
        ("warranty_type", 3),
        ("data_wipe", "certified"),
    ],
)
def test_a_fact_of_the_wrong_type_is_set_to_null_with_a_warning(name, bad_value):
    result = validate(facts_reply({name: bad_value, "warranty_years": 3}))
    facts = result.scorecard.extracted_facts
    assert getattr(facts, name) is None
    assert facts.warranty_years == 3  # the good fact beside it is kept
    assert len(result.warnings) == 1
    assert result.warnings[0].startswith(f"extracted_facts.{name} had the wrong type (")
    assert result.warnings[0].endswith("and was set to null.")


def test_facts_of_the_right_type_are_kept_and_whole_numbers_stay_whole():
    facts_in = {
        "laptop_model": "ProBook 14", "processor": "Core i5-1335U", "ram_gb": 16, "ssd_gb": 512,
        "battery_hours": 11.5, "battery_measured": True, "weight_kg": 1.4, "warranty_years": 3,
        "warranty_type": "on-site", "repair_time_days": 1, "replacement_within_24h": False,
        "delivery_weeks": 6, "grand_total_rs": 5900000, "unit_price_rs": 49999.5,
        "price_table_present": True, "bis_named": True, "energy_rating_named": True,
        "security_certification_named": False, "data_wipe": "certified_standard",
        "encryption_before_delivery": True, "price_lock_later_orders": True,
    }
    result = validate(facts_reply(facts_in))
    assert result.warnings == ()
    assert result.scorecard.extracted_facts.model_dump() == facts_in
    assert type(result.scorecard.extracted_facts.ram_gb) is int
    assert type(result.scorecard.extracted_facts.battery_hours) is float


def test_nulls_and_missing_facts_mean_not_stated_without_a_warning():
    result = validate(facts_reply({"ram_gb": None}))
    assert result.warnings == ()
    assert result.scorecard.extracted_facts == ExtractedFacts()


def test_fact_names_we_do_not_know_are_ignored():
    result = validate(facts_reply({"ram_gb": 16, "favourite_colour": "teal"}))
    assert result.warnings == ()
    assert result.scorecard.extracted_facts == ExtractedFacts(ram_gb=16)


@pytest.mark.parametrize("not_an_object", [None, "none", [], ["ram_gb"], 5])
def test_extracted_facts_that_is_not_an_object_becomes_all_null(not_an_object):
    result = validate(facts_reply(not_an_object))
    assert result.scorecard.extracted_facts == ExtractedFacts()
    assert result.warnings == (
        "extracted_facts was missing or not an object, so every fact was set to null (not stated).",
    )


def test_missing_extracted_facts_becomes_all_null_with_a_warning():
    reply = good_reply()
    del reply["extracted_facts"]
    result = validate(reply)
    assert result.scorecard.extracted_facts == ExtractedFacts()
    assert len(result.warnings) == 1


# ---------------------------------------------------------------- risks and summary

def test_risks_that_are_not_a_list_are_ignored_with_a_warning():
    reply = good_reply()
    reply["risks"] = "late delivery"
    result = validate(reply)
    assert result.scorecard.risks == ()
    assert result.warnings == ("risks was not a list and was ignored.",)


def test_risk_entries_that_are_empty_or_not_text_are_dropped_with_one_warning():
    reply = good_reply()
    reply["risks"] = ["Late delivery", "", 5, "  ", None, " Single supplier for parts "]
    result = validate(reply)
    assert result.scorecard.risks == ("Late delivery", "Single supplier for parts")
    assert result.warnings == ("Some entries in risks were empty or not text and were dropped.",)


def test_missing_risks_and_summary_are_fine():
    reply = good_reply()
    del reply["risks"]
    del reply["overall_summary"]
    result = validate(reply)
    assert result.warnings == ()
    assert (result.scorecard.risks, result.scorecard.overall_summary) == ((), "")


def test_a_summary_that_is_not_text_is_ignored_with_a_warning():
    reply = good_reply()
    reply["overall_summary"] = {"text": "fine"}
    result = validate(reply)
    assert result.scorecard.overall_summary == ""
    assert result.warnings == ("overall_summary was not text and was ignored.",)


# ---------------------------------------------------------------- several problems at once, and real rows

def test_every_problem_is_reported_in_one_pass():
    reply = good_reply()
    reply["criteria"] = [
        entry(1, "8/10"),        # read as 8
        entry(2, 12),            # clipped
        entry(99, 5),            # unknown
        entry(4, 6.66),          # rounded
        entry(4, 2),             # duplicate
    ]                            # criteria 3 and 5 missing
    reply["extracted_facts"] = {"ram_gb": "16 GB"}
    result = validate(reply)
    assert [c.score for c in result.scorecard.criteria] == [8.0, 10.0, 0.0, 6.7, 0.0]
    assert [c.filled_by_validator for c in result.scorecard.criteria] == [False, False, True, False, True]
    assert len(result.warnings) == 8


def test_database_criteria_rows_work_directly():
    ensure_database()
    with repository.connection() as conn:
        rows = repository.list_criteria(conn, active_only=True)
    reply = {
        "criteria": [entry(row.criterion_id, 7) for row in rows],
        "extracted_facts": {},
    }
    result = validate(reply, rows)
    assert result.warnings == ()
    assert [c.criterion_id for c in result.scorecard.criteria] == [row.criterion_id for row in rows]


# ---------------------------------------------------------------- guardrail

def test_validator_module_has_no_io_clock_or_randomness():
    allowed = {"__future__", "collections", "dataclasses", "fractions", "json", "math", "pydantic", "re", "typing"}
    source = Path(__file__).resolve().parent.parent / "tools" / "validator.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    imported, called = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
        elif isinstance(node, ast.Call):
            func = node.func
            called.add(func.id if isinstance(func, ast.Name) else getattr(func, "attr", ""))
    assert imported <= allowed, f"unexpected imports in validator.py: {sorted(imported - allowed)}"
    assert not called & {"open", "print", "input", "now", "today", "utcnow", "random"}, sorted(called)
