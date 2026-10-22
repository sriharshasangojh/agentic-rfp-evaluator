"""Sidebar tests (feature F07): drive the real app headlessly with Streamlit's AppTest.

No API key or network is used: the provider call is replaced by a fake, and any real HTTP attempt fails the test.
These tests need Streamlit, so they are skipped where it is not installed.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("streamlit")

from streamlit.proto.TextInput_pb2 import TextInput as TextInputProto  # noqa: E402
from streamlit.testing.v1 import AppTest  # noqa: E402

from llm import client  # noqa: E402
from ui import sidebar  # noqa: E402

APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
GOOD_KEY = "test-good-key-123"
WRONG_KEY = "test-wrong-key-456"


@pytest.fixture(autouse=True)
def no_default_key_and_no_network(monkeypatch):
    """Start every test with no owner-provided key, and fail loudly if anything tries to reach a provider."""
    for provider in client.PROVIDERS:
        for name in provider.key_names:
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(sidebar, "_secret", lambda name: None)

    def refuse(*args, **kwargs):
        raise AssertionError("the sidebar tests must not use the network")

    monkeypatch.setattr(client, "_default_http_get", refuse)


@pytest.fixture
def fake_provider(monkeypatch):
    """Replace the provider call: WRONG_KEY is rejected, anything else returns two models. Records every real lookup."""
    calls = []

    def fake_list_models(provider, api_key, **kwargs):
        if not (api_key or "").strip():  # like the real client: a blank key is refused before any request is made
            raise client.LLMError("Enter an API key first.", "no_key")
        calls.append((provider, api_key))
        if api_key == WRONG_KEY:
            raise client.LLMError(f"Invalid API key for {provider}.", "invalid_key")
        return ("model-a", "model-b")

    monkeypatch.setattr(client, "list_models", fake_list_models)
    return calls


def start() -> AppTest:
    app = AppTest.from_file(str(APP_PATH), default_timeout=30)
    app.run()
    return app


def type_key(app: AppTest, key: str) -> None:
    app.sidebar.text_input(key="llm_api_key").input(key).run()


def click_validate(app: AppTest) -> None:
    app.sidebar.button(key="llm_validate").click().run()


def model_box(app: AppTest):
    return app.sidebar.selectbox(key="llm_model")


def shown_text(app: AppTest) -> str:
    groups = (app.sidebar.markdown, app.sidebar.caption, app.sidebar.error, app.sidebar.success,
              app.sidebar.warning, app.sidebar.info, app.sidebar.header, app.sidebar.subheader)
    return "\n".join(str(item.value) for group in groups for item in group)


def validated_app(fake_provider) -> AppTest:
    app = start()
    type_key(app, GOOD_KEY)
    click_validate(app)
    return app


# ---------------------------------------------------------------- what the sidebar shows at the start

def test_the_app_still_runs_without_errors():
    assert not start().exception


def test_provider_dropdown_lists_the_six_providers_in_order():
    box = start().sidebar.selectbox(key="llm_provider")
    assert list(box.options) == ["OpenAI", "Anthropic", "Google Gemini", "Cohere", "xAI Grok", "DeepSeek"]
    assert box.value == "OpenAI"


def test_the_key_box_is_a_password_box():
    assert start().sidebar.text_input(key="llm_api_key").proto.type == TextInputProto.PASSWORD


def test_the_model_box_starts_empty_and_locked():
    box = model_box(start())
    assert box.disabled and list(box.options) == [] and box.value is None


def test_the_temperature_note_and_reviewer_name_are_there():
    app = start()
    assert "Scoring temperature: 0 (fixed for reproducible results)" in shown_text(app)
    assert app.sidebar.text_input(key="reviewer_name") is not None


# ---------------------------------------------------------------- validating a key

def test_validate_without_a_key_asks_for_one(fake_provider):
    app = start()
    click_validate(app)
    assert [e.value for e in app.sidebar.error] == ["Enter an API key first."]
    assert fake_provider == []


def test_a_wrong_key_shows_the_invalid_key_message_and_keeps_the_models_locked(fake_provider):
    app = start()
    type_key(app, WRONG_KEY)
    click_validate(app)
    assert [e.value for e in app.sidebar.error] == ["Invalid API key for OpenAI."]
    assert model_box(app).disabled and list(model_box(app).options) == []
    assert WRONG_KEY not in shown_text(app)


def test_a_good_key_loads_the_models_and_unlocks_the_dropdown(fake_provider):
    app = validated_app(fake_provider)
    assert [e.value for e in app.sidebar.success] == ["Key accepted. 2 chat models loaded."]
    box = model_box(app)
    assert not box.disabled and list(box.options) == ["model-a", "model-b"] and box.value is None
    assert GOOD_KEY not in shown_text(app)


def test_choosing_a_model_is_remembered(fake_provider):
    app = validated_app(fake_provider)
    model_box(app).select("model-b").run()
    assert app.session_state["llm_model"] == "model-b"


# ---------------------------------------------------------------- stale choices

def test_changing_the_provider_clears_the_list_and_the_choice(fake_provider):
    app = validated_app(fake_provider)
    model_box(app).select("model-a").run()
    app.sidebar.selectbox(key="llm_provider").select("Anthropic").run()
    box = model_box(app)
    assert box.disabled and list(box.options) == [] and box.value is None
    assert list(app.sidebar.success) == []


def test_going_back_to_a_validated_provider_restores_its_list_without_another_call(fake_provider):
    app = validated_app(fake_provider)
    app.sidebar.selectbox(key="llm_provider").select("Anthropic").run()
    app.sidebar.selectbox(key="llm_provider").select("OpenAI").run()
    assert list(model_box(app).options) == ["model-a", "model-b"] and not model_box(app).disabled
    assert fake_provider == [("OpenAI", GOOD_KEY)]


def test_changing_the_key_clears_the_list(fake_provider):
    app = validated_app(fake_provider)
    type_key(app, "test-another-key")
    assert model_box(app).disabled and list(model_box(app).options) == [] and list(app.sidebar.success) == []


# ---------------------------------------------------------------- locked during a run

def test_the_model_box_is_locked_while_a_run_is_in_progress(fake_provider):
    app = validated_app(fake_provider)
    app.session_state["run_in_progress"] = True
    app.run()
    assert model_box(app).disabled and list(model_box(app).options) == ["model-a", "model-b"]
    assert "locked while a batch runs" in shown_text(app)
    app.session_state["run_in_progress"] = False
    app.run()
    assert not model_box(app).disabled


# ---------------------------------------------------------------- a key set up by the app's owner

def test_an_owner_key_is_used_when_nothing_is_typed_and_a_typed_key_wins(fake_provider, monkeypatch):
    monkeypatch.setattr(sidebar, "_secret", lambda name: "owner-key" if name == "OPENAI_API_KEY" else None)
    app = start()
    assert "Using the key set up for this app" in shown_text(app)
    click_validate(app)
    assert fake_provider == [("OpenAI", "owner-key")]
    assert "owner-key" not in shown_text(app)
    type_key(app, "my-own-key")
    assert "Using the key set up for this app" not in shown_text(app)
    click_validate(app)
    assert fake_provider[-1] == ("OpenAI", "my-own-key")


def test_no_owner_key_means_no_notice():
    assert "Using the key set up for this app" not in shown_text(start())


# ---------------------------------------------------------------- what later features will read

def _probe_settings():
    import streamlit as st

    from ui.sidebar import get_llm_settings, render_sidebar

    render_sidebar()
    st.session_state["probe"] = get_llm_settings()


def test_get_llm_settings_is_none_until_a_key_is_validated_and_a_model_chosen(fake_provider):
    app = AppTest.from_function(_probe_settings, default_timeout=30)
    app.run()
    assert app.session_state["probe"] is None
    type_key(app, GOOD_KEY)
    click_validate(app)
    assert app.session_state["probe"] is None
    model_box(app).select("model-b").run()
    settings = app.session_state["probe"]
    assert settings == client.LLMSettings("OpenAI", "model-b", GOOD_KEY)
    assert GOOD_KEY not in repr(settings)
    type_key(app, "test-changed")
    assert app.session_state["probe"] is None
