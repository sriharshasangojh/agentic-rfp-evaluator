"""Smoke test: the app starts, raises no exception and shows the eight tabs in order."""
from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
EXPECTED_TABS = [
    "Setup",
    "New evaluation",
    "Overview",
    "Scorecards",
    "Analysis",
    "Review",
    "Ask the RFP",
    "Audit",
]


def _run_app() -> AppTest:
    app = AppTest.from_file(str(APP_PATH), default_timeout=30)
    app.run()
    return app


def test_app_runs_without_exception() -> None:
    assert not _run_app().exception


def test_app_shows_eight_tabs_in_order() -> None:
    assert [tab.label for tab in _run_app().tabs] == EXPECTED_TABS
