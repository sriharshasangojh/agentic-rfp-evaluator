"""Look-only checks (the UI polish): the banner chips, the stylesheet, the ranked cards, the key link and the tab labels."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("streamlit")

from llm import client  # noqa: E402
from ui import hero, theme  # noqa: E402
from ui import tab_overview as T  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def test_the_journey_marks_finished_steps_and_the_next_one():
    assert hero.step_states(True, False) == ["done", "now", "", ""]
    assert hero.step_states(False, False) == ["now", "", "", ""]
    assert hero.step_states(True, True) == ["done", "done", "done", "now"]


def test_the_banner_shows_the_steps_escapes_text_and_never_the_model_or_budget():
    page = hero.banner_html(hero.step_states(True, True))
    assert "✓ 1 · Criteria" in page and "Scored and ranked" in page and "RFP Evaluation &amp; Supplier Ranking" in page
    for word in ("budget", "laptop", "gpt", "OpenAI", "model"):
        assert word.lower() not in page.lower()
    assert "chips" not in page and "hero-step now" in page


def test_the_stylesheet_has_the_new_look():
    sheet = theme.css()
    for part in ("st-key-hero", "linear-gradient", "radial-gradient", "stSidebar", "stTabs", "hero-step", "data-selected", "#E9C46A", "attachment: fixed"):
        assert part in sheet
    assert sheet.count("<style>") == 1 and sheet.count("</style>") == 1


def test_every_provider_has_a_key_page_that_is_a_plain_https_link():
    for provider in client.PROVIDERS:
        assert re.fullmatch(r"https://[\w./-]+", provider.key_url), provider.name


def test_the_app_has_emoji_tab_labels_and_still_eight_tabs():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    labels = re.findall(r'\(\s*"([^"]+)",\s*tab_\w+\)', source)
    assert len(labels) == 8 and all(label.startswith(("\\u", "\\U")) for label in labels)     # each starts with an emoji escape
    assert "Criteria" in labels[0] and not any("Setup" in label for label in labels)


def test_the_podium_puts_the_winner_in_the_middle_and_the_rest_in_rows():
    rows = tuple(_row(n, r, e) for n, r, e in (("Apex", 1, True), ("Orbit", 2, True), ("Bright", 3, False), ("Delta", 4, True)))
    page = T.ranked_html(rows, "Apex")
    assert len(re.findall(r'class="ov-rk p\d', page)) == 3 and len(re.findall(r'class="ov-row[ "]', page)) == 1
    assert "#1 · RECOMMENDED" in page and "NOT ELIGIBLE" in page and "Ranking" in page
    assert page.index("Orbit") < page.index("Apex") < page.index("Bright")
    assert T.ranked_html((), None) == ""


def test_the_tiles_say_whether_the_pick_fits_the_budget_but_never_print_the_budget():
    import dataclasses
    row = dataclasses.replace(_row("Apex Systems", 1, True), price=T.insights.PriceFacts(5_400_000, 6_000_000, True, None))
    verdict = T.Verdict(T.GOOD, "We recommend ", "Apex Systems", "", (T.Pill("Meets every must-have", T.GOOD), T.Pill("Below typical", T.WARN)))
    overview = dataclasses.replace(_overview_stub(), rows=(row,), recommended=row, verdict=verdict)
    page = T.tiles_html(overview).split("</style>")[1]
    assert "BEST PICK" in page and "Apex" in page and "In budget" in page and "1 / 1" in page and "WARNINGS" in page
    assert "60" not in page.replace("54", "")        # the budget figure (6,000,000) is not shown


def _overview_stub():
    return T.Overview("", (), None, None, None, None, None, (), T.Verdict(T.GOOD, "", None, "", ()), None, ())


def test_the_score_bar_never_leaves_its_box():
    assert T._bar_width(150) == 100.0 and T._bar_width(-5) == 0.0 and T._bar_width(42.5) == 42.5


def _row(name, rank, eligible):
    import dataclasses
    base = T.Row(rank=rank, name=name, overall=80.0 - rank, ppi=90.0 - 10 * rank, industry=50.0, submitted="2026-10-06", experience="4",
                 eligible=eligible, why_not=() if eligible else ("Warranty",), weak_attributes=(), tie_break=None, strengths=())
    return dataclasses.replace(base, problems=() if eligible else ("short warranty",))
