"""New evaluation tab tests (feature F09): drive the real app headlessly with Streamlit's AppTest.

No API key or network is used: the model is a fake that returns prepared replies. AppTest cannot click a file-upload
box, so the tab's `_uploaded_files` is replaced by a function that returns stand-ins for the sample PDFs; everything
else (the forms, the checks, the button, the run, the saved result) is the real code. Skipped without Streamlit.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("streamlit")

from streamlit.testing.v1 import AppTest  # noqa: E402

from agents import orchestrator  # noqa: E402
from llm import client  # noqa: E402
from tools import pdf_tool  # noqa: E402
from ui import sidebar, tab_new_evaluation  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
APP_PATH = ROOT / "app.py"
KEY = "test-key-abc123"
needs_pdf_reader = pytest.mark.skipif(not pdf_tool.available_backends(), reason="no PDF reader installed")

# the spec's ranking fixture (16.1): file, name, submission date, experience rating, the five criterion scores
FIVE = [
    ("apex_systems.pdf", "Apex Systems", date(2026, 10, 5), 4, [9, 7, 5, 9, 8]),
    ("brightpath_tech.pdf", "BrightPath Tech", date(2026, 10, 7), 2, [5, 4, 8, 3, 3]),
    ("nexaworks.pdf", "NexaWorks", date(2026, 10, 6), 4, [7, 9, 7, 7, 9]),
    ("horizon_datacom.pdf", "Horizon Datacom", date(2026, 10, 6), 3, [7, 9, 7, 7, 9]),
    ("orbit_digital.pdf", "Orbit Digital", date(2026, 10, 8), 5, [7, 5, 7, 6, 9]),
]
FACTS = {"warranty_years": 3, "warranty_type": "on-site", "bis_named": True, "price_table_present": True, "delivery_weeks": 6}


def model_reply(scores: list[int]) -> str:
    return json.dumps({
        "supplier_name": "Supplier A",
        "criteria": [{"criterion_id": i, "score": s, "justification": f"why {i}", "evidence": "Not stated in the proposal"}
                     for i, s in enumerate(scores, start=1)],
        "extracted_facts": FACTS, "risks": [], "overall_summary": "ok"})


class FakeUpload:
    """What the tab needs from an uploaded file."""

    def __init__(self, path: Path, file_id: str) -> None:
        self.name, self.file_id, self._data = path.name, file_id, path.read_bytes()
        self.size = len(self._data)

    def getvalue(self) -> bytes:
        return self._data


class FakeModel:
    """The provider: one prepared reply per call, or an error."""

    def __init__(self, outcomes) -> None:
        self.outcomes, self.calls = list(outcomes), 0

    def completion(self, **kwargs):
        outcome = self.outcomes[min(self.calls, len(self.outcomes) - 1)]
        self.calls += 1
        if isinstance(outcome, BaseException):
            raise outcome
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=outcome))],
                               usage=SimpleNamespace(total_tokens=1000))

    def supports_json_mode(self, settings) -> bool:
        return True

    def cost(self, response) -> float:
        return 0.01


@pytest.fixture(autouse=True)
def no_default_key_and_no_network(monkeypatch):
    for provider in client.PROVIDERS:
        for name in provider.key_names:
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(sidebar, "_secret", lambda name: None)

    def refuse(*args, **kwargs):
        raise AssertionError("the New evaluation tests must not use the network")

    monkeypatch.setattr(client, "_default_http_get", refuse)


def start() -> AppTest:
    app = AppTest.from_file(str(APP_PATH), default_timeout=60)
    app.run()
    return app


def tab(app: AppTest):
    return app.tabs[1]


def texts(block, *kinds: str) -> str:
    return "\n".join(str(item.value) for kind in kinds for item in getattr(block, kind))


# ---------------------------------------------------------------- the empty tab

def test_the_tab_starts_with_the_meridian_budget_and_quantity():
    app = start()
    assert not app.exception
    assert tab(app).number_input(key="ne_budget").value == 6000000
    assert tab(app).number_input(key="ne_quantity").value == 100
    assert "Rs 60,00,000 for 100 laptop(s), about Rs 60,000 per laptop." in texts(tab(app), "caption")


def test_with_nothing_uploaded_it_explains_what_to_do_and_the_button_is_off():
    app = start()
    assert "Upload 1 to 10 supplier proposal PDFs" in texts(tab(app), "info")
    assert "choose a provider, validate your API key and pick a model in the sidebar" in texts(tab(app), "info")
    assert tab(app).button(key="ne_evaluate").disabled
    assert not tab(app).dataframe and not tab(app).error


def test_changing_the_budget_and_quantity_changes_the_per_laptop_figure():
    app = start()
    tab(app).number_input(key="ne_budget").set_value(4500000)
    tab(app).number_input(key="ne_quantity").set_value(50).run()
    assert "Rs 45,00,000 for 50 laptop(s), about Rs 90,000 per laptop." in texts(tab(app), "caption")


# ---------------------------------------------------------------- with proposals uploaded

@pytest.fixture
def uploaded(monkeypatch):
    """Five sample PDFs 'uploaded', and a model and key 'chosen' in the sidebar."""
    files = [FakeUpload(ROOT / "data" / "proposals" / file, f"f{i}") for i, (file, *_rest) in enumerate(FIVE)]
    monkeypatch.setattr(tab_new_evaluation, "_uploaded_files", lambda disabled: files)
    monkeypatch.setattr(sidebar, "get_llm_settings", lambda: client.LLMSettings("OpenAI", "test-model", KEY))
    return files


def use_model(monkeypatch, *outcomes) -> FakeModel:
    model = FakeModel(outcomes)
    monkeypatch.setattr(client, "_LiteLLMBackend", lambda: model)
    return model


def fill_in(app: AppTest, with_ratings: bool = True) -> None:
    """Type the supplier names (the dates come from the PDFs) and, optionally, the experience ratings."""
    for i, (_file, name, _when, rating, _scores) in enumerate(FIVE):
        tab(app).text_input(key=f"ne_name_f{i}").input(name)
        if with_ratings:
            tab(app).selectbox(key=f"ne_rating_f{i}").select(rating)


@needs_pdf_reader
def test_each_pdf_gets_a_form_with_the_name_from_its_file_name_and_the_date_from_the_pdf(uploaded):
    app = start()
    assert [tab(app).text_input(key=f"ne_name_f{i}").value for i in range(5)] == [
        "Apex Systems", "Brightpath Tech", "Nexaworks", "Horizon Datacom", "Orbit Digital"]
    assert [tab(app).date_input(key=f"ne_date_f{i}").value for i in range(5)] == [when for _f, _n, when, _r, _s in FIVE]
    assert all(tab(app).selectbox(key=f"ne_rating_f{i}").value is None for i in range(5))   # the rating is optional
    assert "Submission date read from the PDF" in texts(tab(app), "caption")
    assert not tab(app).error
    assert not tab(app).button(key="ne_evaluate").disabled   # nothing is missing, so it can start straight away


@needs_pdf_reader
@pytest.mark.parametrize("source, note", [
    ("labelled", "Submission date read from the PDF; change it if it is wrong."),
    ("first date on page 1", "No submission date label found, so the first date on page 1 is filled in; change it if it is wrong."),
    ("today", "No date found in the PDF, so today's date is filled in; change it if it is wrong."),
])
def test_every_card_says_where_its_date_came_from(uploaded, monkeypatch, source, note):
    monkeypatch.setattr(orchestrator, "suggest_submission_date", lambda pdf, today: orchestrator.DateSuggestion(date(2026, 10, 23), source))
    app = start()
    assert all(tab(app).date_input(key=f"ne_date_f{i}").value == date(2026, 10, 23) for i in range(5))
    assert note in texts(tab(app), "caption")
    assert not tab(app).error and not tab(app).button(key="ne_evaluate").disabled   # the box is never left empty


@needs_pdf_reader
def test_a_duplicate_supplier_name_is_refused_with_a_message_and_the_button_stays_off(uploaded):
    app = start()
    fill_in(app)
    tab(app).text_input(key="ne_name_f1").input("apex systems")
    app.run()
    assert ("The supplier name 'Apex Systems' is used for 2 files (apex_systems.pdf, brightpath_tech.pdf). "
            "Each supplier needs its own name.") in texts(tab(app), "error")
    assert tab(app).button(key="ne_evaluate").disabled
    tab(app).text_input(key="ne_name_f1").input("BrightPath Tech").run()   # fixing it clears the message
    assert not tab(app).error and not tab(app).button(key="ne_evaluate").disabled


@needs_pdf_reader
def test_fewer_than_four_proposals_show_the_information_note(uploaded, monkeypatch):
    monkeypatch.setattr(tab_new_evaluation, "_uploaded_files", lambda disabled: uploaded[:2])
    app = start()
    assert "The brief expects at least 4 for a meaningful peer comparison." in texts(tab(app), "info")


@needs_pdf_reader
def test_evaluating_runs_the_batch_and_shows_the_run_id_and_the_ranking(uploaded, monkeypatch):
    model = use_model(monkeypatch, *[model_reply(scores) for *_rest, scores in FIVE])
    app = start()
    fill_in(app)
    app.run()
    assert not tab(app).button(key="ne_evaluate").disabled
    tab(app).button(key="ne_evaluate").click().run()
    assert not app.exception and model.calls == 5

    assert texts(tab(app), "success").startswith("Run RUN-") and "evaluated 5 supplier(s)" in texts(tab(app), "success")
    run_id = tab(app).code[0].value
    assert run_id.startswith("RUN-") and run_id in texts(tab(app), "success")
    table = tab(app).dataframe[0].value
    assert table["Supplier"].tolist() == ["Apex Systems", "NexaWorks", "Horizon Datacom", "Orbit Digital", "BrightPath Tech"]
    assert table["Rank"].tolist() == [1, 2, 3, 4, 5]
    assert table["Overall /100"].tolist() == [77.0, 76.0, 76.0, 66.0, 48.0]
    assert table["Peer score (PPI)"].tolist() == [86.94, 86.39, 86.39, 75.28, 55.56]
    assert "NexaWorks above Horizon Datacom: higher experience rating (4 vs 3)" in texts(tab(app), "caption")
    assert "Created run" in tab(app).code[1].value and "Saved the results" in tab(app).code[1].value   # the step log
    assert app.session_state["run_in_progress"] is False
    assert KEY not in texts(tab(app), "markdown", "caption", "code", "success", "error", "info")


@needs_pdf_reader
def test_a_run_with_no_ratings_at_all_works_and_shows_not_rated(uploaded, monkeypatch):
    use_model(monkeypatch, *[model_reply(scores) for *_rest, scores in FIVE])
    app = start()
    fill_in(app, with_ratings=False)
    app.run()
    tab(app).button(key="ne_evaluate").click().run()
    assert not app.exception
    table = tab(app).dataframe[0].value
    assert table["Supplier"].tolist() == ["Apex Systems", "Horizon Datacom", "NexaWorks", "Orbit Digital", "BrightPath Tech"]
    assert table["Experience"].tolist() == ["Not rated"] * 5
    assert "Horizon Datacom above NexaWorks: supplier name comes first alphabetically (Horizon Datacom vs NexaWorks)" in texts(tab(app), "caption")


@needs_pdf_reader
def test_a_rejected_key_shows_a_friendly_message_and_unlocks_the_page(uploaded, monkeypatch):
    error = type("AuthenticationError", (Exception,), {})("Incorrect API key provided")
    error.status_code = 401
    error.message = "Incorrect API key provided"
    use_model(monkeypatch, model_reply([9, 7, 5, 9, 8]), error)
    app = start()
    fill_in(app)
    app.run()
    tab(app).button(key="ne_evaluate").click().run()
    assert not app.exception
    assert "Stopped while evaluating BrightPath Tech: Invalid API key for OpenAI." in texts(tab(app), "error")
    assert "Traceback" not in texts(tab(app), "error")
    assert app.session_state["run_in_progress"] is False
    assert not tab(app).button(key="ne_evaluate").disabled   # the visitor can fix the key and try again


def test_the_tab_never_clears_a_run_flag_it_did_not_start():
    """The sidebar's lock belongs to whoever set it. Drawing this tab must not clear it (it once did, and F07 caught it)."""
    app = start()
    app.session_state["run_in_progress"] = True
    app.run()
    assert not app.exception
    assert app.session_state["run_in_progress"] is True
