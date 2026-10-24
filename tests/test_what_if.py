"""Tests for the what-if calculation in tools/insights.py (spec 10.3 and the 16.1 fixture). No API key, database or network."""
from __future__ import annotations

import pytest

from tests.test_ranking import CRITERIA, FIXTURE, supplier
from tools import insights, ranking
from tools.ranking import CriterionWeight, rank_suppliers

NAMES = [s.name for s in FIXTURE]
PRICE = 3        # criterion 3 is the price criterion in the fixture (weight 20)


def run(eligible=NAMES, criteria=CRITERIA, suppliers=FIXTURE, **kwargs):
    return insights.what_if(suppliers, eligible, criteria, PRICE, **kwargs)


def ppi_at(result, name, weight):
    line = next(l for l in result.lines if l.name == name)
    return line.ppis[result.weights.index(weight)]


def test_the_grid_is_the_spec_one():
    assert insights.WHAT_IF_WEIGHTS == (10, 15, 20, 25, 30, 35, 40)
    assert run().weights == insights.WHAT_IF_WEIGHTS


def test_the_spec_numbers_at_a_price_weight_of_30():
    result = run()
    assert ppi_at(result, "NexaWorks", 30) == 86.53 and ppi_at(result, "Apex Systems", 30) == 83.89


def test_apex_leads_at_20_and_nexaworks_leads_at_30():
    result = run()
    assert result.leaders[result.weights.index(20)] == "Apex Systems"
    assert result.leaders[result.weights.index(30)] == "NexaWorks"


def test_at_the_current_weight_the_ppis_are_the_ones_the_run_has():
    result = run()
    now = {r.name: r.ppi for r in rank_suppliers(FIXTURE, CRITERIA).suppliers}
    assert {l.name: ppi_at(result, l.name, 20) for l in result.lines} == now
    assert result.current_weight == 20


def test_the_leader_changes_are_listed_between_neighbouring_weights():
    result = run()
    assert len(result.changes) >= 1
    first = result.changes[0]
    assert (first.from_leader, first.to_leader) == ("Apex Systems", "NexaWorks")
    assert result.leaders[result.weights.index(first.from_weight)] == first.from_leader
    assert result.leaders[result.weights.index(first.to_weight)] == first.to_leader
    assert result.weights.index(first.to_weight) == result.weights.index(first.from_weight) + 1


def test_a_batch_with_one_leader_everywhere_has_no_changes():
    only = [FIXTURE[0]]
    result = run(eligible=[only[0].name], suppliers=only)
    assert result.changes == () and set(result.leaders) == {"Apex Systems"}


def test_a_supplier_that_fails_a_must_have_never_leads():
    result = run(eligible=["NexaWorks", "Orbit Digital"])
    assert set(result.leaders) == {"NexaWorks"} and "Apex Systems" in [l.name for l in result.lines]


def test_nobody_eligible_means_no_leader_and_no_change():
    result = run(eligible=[])
    assert result.leaders == (None,) * 7 and result.changes == ()


def test_eligible_names_are_matched_ignoring_capitals():
    assert run(eligible=["APEX SYSTEMS"]).leaders[2] == "Apex Systems"


def test_every_scaled_total_is_100():
    seen = []
    original = ranking.rank_suppliers

    def spy(suppliers, criteria):
        seen.append(sum(c.weight for c in criteria))
        return original(suppliers, criteria)

    try:
        ranking.rank_suppliers = spy
        run()
    finally:
        ranking.rank_suppliers = original
    assert len(seen) == 7 and all(abs(t - 100) < 1e-9 for t in seen)


def test_the_other_weights_keep_their_proportions():
    seen = []
    original = ranking.rank_suppliers

    def spy(suppliers, criteria):
        seen.append({c.criterion_id: c.weight for c in criteria})
        return original(suppliers, criteria)

    try:
        ranking.rank_suppliers = spy
        run(weights=(30,))
    finally:
        ranking.rank_suppliers = original
    got = seen[0]
    assert got[3] == 30 and got[1] == pytest.approx(26.25) and got[2] == pytest.approx(17.5) and got[5] == pytest.approx(8.75)


def test_the_marks_are_not_changed():
    before = [dict(s.scores) for s in FIXTURE]
    run()
    assert [dict(s.scores) for s in FIXTURE] == before


def test_no_price_criterion_or_nothing_to_scale_gives_none():
    assert insights.what_if(FIXTURE, NAMES, CRITERIA, 99) is None
    only_price = (CriterionWeight(3, 100, 10),)
    assert insights.what_if([supplier("A", (5,), "2026-10-01", 1, only_price)], ["A"], only_price, 3) is None
    zero_others = (CriterionWeight(1, 0, 10), CriterionWeight(3, 100, 10))
    assert insights.what_if([supplier("A", (5, 5), "2026-10-01", 1, zero_others)], ["A"], zero_others, 3) is None


def test_no_suppliers_gives_none():
    assert insights.what_if([], [], CRITERIA, PRICE) is None


def test_a_price_weight_of_zero_is_handled():
    result = run(weights=(0, 100 - 1))
    assert result.weights == (0, 99) and len(result.lines[0].ppis) == 2


def test_each_line_has_a_point_per_weight():
    assert all(len(l.ppis) == 7 for l in run().lines) and [l.name for l in run().lines] == NAMES
