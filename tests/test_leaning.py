"""F21b: what a buyer leans towards (tools/leaning.py), the price end, and how a run uses them. Real database, real pipeline."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agents import orchestrator
from db import repository, seed
from review_support import APEX, HORIZON, NEXA, FIVE, evaluated_run
from tools import insights, leaning as L

C = [SimpleNamespace(criterion_id=i, name=n, weight=w) for i, n, w in
     [(1, "Technical Capability", 30.0), (2, "Implementation Plan", 20.0), (3, "Commercial Value", 20.0), (4, "Security & Compliance", 20.0),
      (5, "Support & Experience", 10.0)]]
IDS = [1, 2, 3, 4, 5]


def clean(raw, text, price=3):
    return L.clean(raw, IDS, price, text)


# ---------------------------------------------------------------- reading the AI's leaning

@pytest.mark.parametrize("text", ["I want the most expensive laptop", "the costliest", "Highest price", "priciest one", "I want it expensive"])
def test_a_wish_for_the_dearest_is_the_high_end_only_when_the_words_say_so(text):
    assert clean({"price": "high", "only": True}, text).price == "high"
    assert clean({"price": "high"}, "I want a laptop that lasts all day").price is None


@pytest.mark.parametrize("text", ["The laptops are too expensive", "it is not expensive enough? no, less expensive please", "too expensive for us"])
def test_a_complaint_about_the_price_is_never_a_wish_for_the_dearest(text):
    assert clean({"price": "high", "only": True}, text).price is None


def test_cheapest_words_give_the_low_end_even_when_the_ai_said_nothing():
    assert clean({}, "I want the cheapest").price == "low" and clean(None, "lowest price please").price == "low"


def test_asking_for_both_ends_is_not_a_wish_for_either():
    assert clean({"price": "high"}, "cheapest or most expensive, I do not mind").price is None


def test_only_true_with_a_price_wish_means_price_alone_and_a_criterion_id_means_that_area():
    assert clean({"price": "high", "only": True}, "most expensive").only_id == 3
    assert clean({"only": 1}, "battery is all I care about").only_id == 1
    assert clean({"only": 77}, "x").only_id is None and clean({"only": True}, "x").only_id is None


def test_levels_are_clamped_and_only_real_criteria_count():
    lean = clean({"emphasis": {"1": 9, "2": -9, "77": 2, "x": 1, "3": "high", "4": True}}, "x")
    assert dict(lean.emphasis) == {1: 2, 2: -2}


def test_a_wish_about_price_makes_price_count_for_more_unless_the_ai_said_otherwise():
    assert dict(clean({"price": "high"}, "most expensive").emphasis) == {3: 2}
    assert dict(clean({"price": "high", "emphasis": {"3": -1}}, "expensive but I do not care about price").emphasis).get(3) == -1


def test_garbage_is_a_leaning_that_changes_nothing():
    for raw in (None, "x", 5, [], {"emphasis": "a"}, {"price": 7}):
        assert not clean(raw, "hello").changes_anything


# ---------------------------------------------------------------- the weights

def test_price_only_makes_price_dominate_at_the_cap_and_the_rest_share_what_is_left_as_they_were():
    plan = L.plan(C, clean({"price": "high", "only": True}, "most expensive"))
    assert plan.new_weights() == {1: 15.0, 2: 10.0, 3: 60.0, 4: 10.0, 5: 5.0} and plan.target_id == 3


def test_no_leaning_ever_pushes_a_criterion_above_the_cap():
    for raw in ({"emphasis": {"1": 2}}, {"emphasis": {"1": 2, "2": 2, "3": -2, "4": -2, "5": -2}}, {"price": "high", "only": True}):
        weights = L.plan(C, clean(raw, "most expensive")).new_weights()
        assert max(weights.values()) <= 60 and round(sum(weights.values()), 2) == 100


def test_a_mixed_wish_scales_the_weights_by_level_and_totals_100():
    plan = L.plan(C, clean({"price": "high", "emphasis": {"1": 2, "3": -1}}, "expensive but long lasting battery"))
    weights = plan.new_weights()
    assert weights == {1: 60.0, 2: 13.33, 3: 6.67, 4: 13.33, 5: 6.67} and sum(weights.values()) == pytest.approx(100)
    assert plan.target_id == 1


def test_leaning_on_nothing_raises():
    with pytest.raises(ValueError):
        L.plan(C, L.Leaning())


def test_the_rounding_remainder_goes_on_the_largest_weight_so_the_total_is_exactly_100():
    odd = [SimpleNamespace(criterion_id=i, name=f"c{i}", weight=w) for i, w in [(1, 33.33), (2, 33.33), (3, 33.34)]]
    plan = L.plan(odd, L.Leaning(None, None, ((1, 1), (2, -1), (3, 0))))
    assert round(sum(plan.new_weights().values()), 2) == 100.0


def test_the_sentence_the_person_confirms_is_python_s_own():
    names = {i: c.name for i, c in zip(IDS, C)}
    assert L.describe(clean({"price": "high", "only": True}, "most expensive"), names) == \
        "I understood: you only care about commercial value, and you want the most expensive option."
    assert L.describe(clean({"emphasis": {"1": 2, "3": -1, "4": 1}}, "x"), names) == \
        "I understood: Technical Capability and Security & Compliance should count for more and Commercial Value should count for less."


# ---------------------------------------------------------------- price marks, both ends

def test_the_dearest_total_gets_the_full_mark_and_the_rest_total_over_highest():
    got = insights.price_marks({"A": 5_000_000, "B": 4_000_000, "C": None}, 20, "high")
    assert got.reference_rs == 5_000_000 and got.marks == {"A": 20.0, "B": 16.0} and got.without_total == ("C",)
    low = insights.price_marks({"A": 5_000_000, "B": 4_000_000}, 20)
    assert low.reference_rs == 4_000_000 and low.marks == {"A": 16.0, "B": 20.0}


def test_the_reason_names_the_wish_for_the_dearest():
    text = insights.price_reason(4_000_000, 5_000_000, 16.0, 20, "high")
    assert "asked for the most expensive" in text and "= 16" in text
    assert "highest total" in insights.price_reason(5_000_000, 5_000_000, 20.0, 20, "high")


# ---------------------------------------------------------------- a run

@pytest.fixture(autouse=True)
def seeded_database():
    seed.ensure_database()


def priced_run(totals=None):
    facts = {n: {"grand_total_rs": t} for n, t in (totals or {APEX: 5_200_000, NEXA: 4_000_000, HORIZON: 5_600_000}).items()}
    from review_support import GOOD_FACTS
    return evaluated_run([r for r in FIVE if r[0] in (APEX, NEXA, HORIZON)], facts={n: {**GOOD_FACTS, **f} for n, f in facts.items()})


def board(run_id):
    with repository.connection() as conn:
        return [(s.supplier_name, s.final_rank) for s in sorted(repository.list_supplier_results(conn, run_id), key=lambda s: s.final_rank)]


def snapshot(run_id):
    with repository.connection() as conn:
        return repository.get_run(conn, run_id).criteria_snapshot


def price_mark(run_id, name):
    with repository.connection() as conn:
        stored = next(s for s in repository.list_supplier_results(conn, run_id) if s.supplier_name == name)
    return next(c["score"] for c in stored.result["criteria"] if c["name"] == "Commercial Value")


def test_asking_for_the_most_expensive_flips_the_price_marks_and_puts_everything_on_price():
    run = priced_run()
    assert price_mark(run.run_id, NEXA) == 7                                  # no price wish yet: the AI's own mark
    out = orchestrator.change_priorities(run.run_id, 2, 3, 1, "most expensive", leaning=clean({"price": "high", "only": True}, "most expensive"))
    assert out.ok and "Commercial Value 20% to 60%" in out.message
    assert board(run.run_id)[0][0] == HORIZON and board(run.run_id)[-1][0] == NEXA        # the dearest first, the cheapest last
    assert price_mark(run.run_id, HORIZON) == 10.0 and price_mark(run.run_id, NEXA) == 7.14   # the dearest gets the full mark
    snap = snapshot(run.run_id)
    assert next(i for i in snap if i["name"] == "Commercial Value")["direction"] == "high" and sum(i["weight"] for i in snap) == pytest.approx(100)
    assert orchestrator.price_direction_of(snap) == "high"


def test_the_round_is_recorded_with_the_leaning_and_the_price_end():
    run = priced_run()
    orchestrator.change_priorities(run.run_id, 2, 3, 1, "most expensive", leaning=clean({"price": "high", "only": True}, "most expensive"))
    with repository.connection() as conn:
        details = repository.list_feedback(conn, run.run_id)[0].details
    assert details["price_direction"] == "high" and details["leaning"]["only_id"] == 3 and details["leaning"]["price"] == "high"


def test_a_later_round_asking_for_the_cheapest_flips_it_back():
    run = priced_run()
    orchestrator.change_priorities(run.run_id, 2, 3, 1, "x", leaning=clean({"price": "high", "only": True}, "most expensive"))
    orchestrator.change_priorities(run.run_id, 2, 3, 2, "x", leaning=clean({"price": "low", "only": True}, "cheapest"))
    assert board(run.run_id)[0][0] == NEXA and orchestrator.price_direction_of(snapshot(run.run_id)) == "low"


def test_a_round_without_a_leaning_keeps_the_price_end_it_had():
    run = priced_run()
    orchestrator.change_priorities(run.run_id, 2, 3, 1, "x", leaning=clean({"price": "high", "only": True}, "most expensive"))
    orchestrator.change_priorities(run.run_id, 2, 1, 2)
    assert orchestrator.price_direction_of(snapshot(run.run_id)) == "high"


def test_the_red_warning_appears_when_the_dearest_is_not_the_best_fit_and_not_otherwise():
    run = priced_run()
    assert orchestrator.overpay_warning(run.run_id) is None                                       # nobody asked for the dearest
    best = orchestrator.original_best_fit(run.run_id)
    orchestrator.change_priorities(run.run_id, 2, 3, 1, "x", leaning=clean({"price": "high", "only": True}, "most expensive"))
    warning = orchestrator.overpay_warning(run.run_id)
    chosen = board(run.run_id)[0][0]
    if chosen == best:
        assert warning is None
    else:
        assert warning and chosen in warning and best in warning and "overpaying" in warning


def test_after_all_three_rounds_the_final_pick_goes_back_to_the_original_best_fit():
    run = priced_run()
    best = orchestrator.original_best_fit(run.run_id)
    orchestrator.change_priorities(run.run_id, 2, 3, 1, "x", leaning=clean({"price": "high", "only": True}, "most expensive"))
    orchestrator.change_priorities(run.run_id, 2, 3, 2, "x", leaning=clean({"price": "high", "only": True}, "most expensive"))
    orchestrator.change_priorities(run.run_id, 2, 3, 3, "x", leaning=clean({"price": "high", "only": True}, "most expensive"))
    assert board(run.run_id)[0][0] == HORIZON
    assert orchestrator.restore_original(run.run_id) is True
    assert board(run.run_id)[0][0] == best and orchestrator.price_direction_of(snapshot(run.run_id)) is None
    assert price_mark(run.run_id, NEXA) == 7                                  # the AI's own price mark is back
    with repository.connection() as conn:
        original = {c.criterion_id: c.weight for c in repository.list_criteria(conn, "laptop_procurement")} if False else None
    assert sum(i["weight"] for i in snapshot(run.run_id)) == pytest.approx(100)
    assert orchestrator.restore_original(run.run_id) is False                                      # idempotent
    with repository.connection() as conn:
        events = [e.action for e in repository.list_events(conn, run.run_id)]
        assert len(repository.list_feedback(conn, run.run_id)) == 3                                # the rounds are kept
    assert events.count("returned_to_best_fit") == 1


def test_negotiating_after_three_unhappy_rounds_is_for_the_original_best_fit():
    run = priced_run()
    best = orchestrator.original_best_fit(run.run_id)
    for round_no in (1, 2, 3):
        orchestrator.change_priorities(run.run_id, 2, 3, round_no, "x", leaning=clean({"price": "high", "only": True}, "most expensive"))
    shown = board(run.run_id)[0][0]
    out = orchestrator.suggest_negotiation(run.run_id, shown)
    if best != shown:
        assert out.ok and out.supplier == best and out.message.startswith("You were not satisfied after all 3 rounds")
    assert board(run.run_id)[0][0] == best


def test_a_run_started_with_a_wish_for_the_dearest_uses_it_from_the_start():
    from review_support import GOOD_FACTS, SETTINGS, START, TERMS, FakeBackend, pdf_for, reply
    names = [n for n in (APEX, NEXA, HORIZON)]
    rows = [r for r in FIVE if r[0] in names]
    totals = {APEX: 5_200_000, NEXA: 4_000_000, HORIZON: 5_600_000}
    proposals = [orchestrator.Proposal(f"{n.lower().replace(' ', '_')}.pdf", n, when, rating, pdf_for(n)) for n, when, rating, _s in rows]
    typed = orchestrator.TypedPriority("most expensive", 3, "r", leaning=clean({"price": "high", "only": True}, "most expensive"))
    backend = FakeBackend(*[reply(s, facts={**GOOD_FACTS, "grand_total_rs": totals[n]}) for n, _w, _r, s in rows])
    result = orchestrator.run_evaluation(proposals, TERMS, SETTINGS, "Sri", orchestrator.PriorityRequest((3,), (typed,)),
                                         backend=backend, sleep=lambda _s: None, now=lambda: START)
    assert result.status == "Evaluated"
    assert board(result.run_id)[0][0] == HORIZON and orchestrator.price_direction_of(snapshot(result.run_id)) == "high"
    assert [i["weight"] for i in snapshot(result.run_id) if i["name"] == "Commercial Value"] == [60.0]


def test_in_this_setup_the_dearest_is_not_the_best_fit_so_the_warning_is_really_exercised():
    run = priced_run()
    best = orchestrator.original_best_fit(run.run_id)
    orchestrator.change_priorities(run.run_id, 2, 3, 1, "x", leaning=clean({"price": "high", "only": True}, "most expensive"))
    print("BEST", best, board(run.run_id), orchestrator.overpay_warning(run.run_id))
    assert board(run.run_id)[0][0] != best
    assert orchestrator.overpay_warning(run.run_id)


def test_no_warning_when_the_dearest_is_also_the_best_fit():
    best = orchestrator.original_best_fit(priced_run().run_id)          # the AI's own marks do not depend on the totals
    totals = {n: 5_000_000 for n in (APEX, NEXA, HORIZON)} | {best: 6_000_000}
    run = priced_run(totals)
    orchestrator.change_priorities(run.run_id, 2, 3, 1, "x", leaning=clean({"price": "high", "only": True}, "most expensive"))
    assert board(run.run_id)[0][0] == best and orchestrator.overpay_warning(run.run_id) is None


# ---------------------------------------------------------------- the two picks

def test_a_run_where_nothing_was_asked_has_no_picks_panel():
    assert orchestrator.picks_of(priced_run().run_id) is None


def test_a_ticked_priority_gives_both_picks_and_says_when_they_are_the_same_supplier():
    run = priced_run()
    orchestrator.change_priorities(run.run_id, 2, 1, 1)                                  # an ordinary round: Technical Capability counts for more
    picks = orchestrator.picks_of(run.run_id)
    assert picks is not None and picks.ideal == orchestrator.original_best_fit(run.run_id)
    assert picks.request == board(run.run_id)[0][0] and picks.same == (picks.request == picks.ideal) and picks.overpay is None


def test_a_price_wish_with_a_different_pick_shows_both_and_the_red_warning():
    run = priced_run()
    orchestrator.change_priorities(run.run_id, 2, 3, 1, "x", leaning=clean({"price": "high", "only": True}, "most expensive"))
    picks = orchestrator.picks_of(run.run_id)
    assert picks.request == HORIZON and picks.ideal == orchestrator.original_best_fit(run.run_id) != HORIZON and not picks.same and picks.overpay and "overpaying" in picks.overpay


def test_the_panel_says_based_on_priorities_and_best_fit_when_they_are_the_same_supplier():
    from ui import picks as P
    same = P.picks_html(orchestrator.Picks("Apex Systems", "Apex Systems", True, None, 1, 1, 1, 1, 5))
    assert "Based on your priorities and the best fit:" in same and "Apex Systems" in same
    assert "rank 1 of 5 with your priorities" in same and "rank 1 of 5 with the best-fit weights" in same
    assert P.REQUEST_HEAD not in same and P.IDEAL_HEAD not in same


def test_the_panel_shows_both_picks_with_both_ranks_when_they_differ():
    import html
    from ui import picks as P
    differ = P.picks_html(orchestrator.Picks("Horizon Datacom", "Apex Systems", False, None, 3, 2, 1, 1, 5))
    assert "Best pick based on your priorities:" in differ and "Horizon Datacom" in differ
    assert "rank 1 of 5 with your priorities, rank 3 of 5 with the best-fit weights" in differ
    assert "Best pick based on the best fit:" in differ and "Apex Systems" in differ
    assert "rank 1 of 5 with the best-fit weights, rank 2 of 5 with your priorities" in differ
    assert html.escape(P.NOTE) in differ


def test_the_ranks_in_the_picks_come_from_the_two_rankings():
    run = priced_run()
    orchestrator.change_priorities(run.run_id, 2, 3, 1, "x", leaning=clean({"price": "high", "only": True}, "most expensive"))
    picks = orchestrator.picks_of(run.run_id)
    assert picks.total == 3 and picks.request_rank == 1 and picks.ideal_rank == 1
    assert picks.ideal_rank_request == board(run.run_id)[[n for n, _ in board(run.run_id)].index(picks.ideal)][1]
    assert picks.request_rank_ideal >= 2


def test_a_supplier_name_is_never_drawn_as_html_in_the_panel():
    from ui import picks as P
    drawn = P.picks_html(orchestrator.Picks("<b>X</b>", "<i>Y</i>", False, None, 2, 2, 1, 1, 3))
    assert "<b>X</b>" not in drawn and "&lt;b&gt;X&lt;/b&gt;" in drawn and "<i>Y</i>" not in drawn


class _Out:
    def __init__(self):
        self.calls = []

    def markdown(self, text, **kw):
        self.calls.append(("markdown", text))

    def error(self, text, **kw):
        self.calls.append(("error", text))


def test_the_tabs_draw_nothing_when_nothing_was_asked_and_the_panel_plus_warning_when_it_was():
    from ui import picks as P
    run = priced_run()
    out = _Out()
    P.show(out, run.run_id)
    assert out.calls == []
    orchestrator.change_priorities(run.run_id, 2, 3, 1, "x", leaning=clean({"price": "high", "only": True}, "most expensive"))
    P.show(out, run.run_id)
    kinds = [k for k, _t in out.calls]
    assert kinds == ["markdown", "error"] and "Best pick based on your priorities:" in out.calls[0][1] and "overpaying" in out.calls[1][1]


def test_the_new_evaluation_result_draws_the_panel():
    import inspect
    from ui import tab_new_evaluation
    assert "picks_view.show(st, result.run_id)" in inspect.getsource(tab_new_evaluation._show_result)
