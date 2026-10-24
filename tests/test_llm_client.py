"""Tests for llm/client.py (spec section 6 and F07). No API key, network, httpx or litellm needed:
HTTP and LiteLLM are replaced by small fakes, and the model lists come from tests/fixtures/models_*.json."""
from __future__ import annotations

import ast
import json
import re
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from llm import client
from llm.client import (
    BACKOFF_SECONDS,
    CALL_TIMEOUT,
    MAX_PAGES,
    HttpReply,
    HttpTimeout,
    HttpUnreachable,
    LLMError,
    LLMSettings,
    call_llm,
    list_models,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
KEY = "test-TESTKEY-123456"

# provider -> (fixture name, expected chat models, address, header names, query parameters on the first request)
CASES = {
    "OpenAI": ("openai", ("gpt-4.1", "gpt-4o", "gpt-4o-mini", "o3-mini"),
               "https://api.openai.com/v1/models", {"Authorization", "Accept"}, {}),
    "Anthropic": ("anthropic", ("claude-haiku-5-5", "claude-opus-5-5", "claude-sonnet-5-5"),
                  "https://api.anthropic.com/v1/models", {"Authorization", "Accept", "anthropic-version"}, {"limit": "1000"}),
    "Google Gemini": ("gemini", ("gemini-2.5-flash", "gemini-2.5-pro", "gemma-3-27b-it"),
                      "https://generativelanguage.googleapis.com/v1beta/models", {"x-goog-api-key", "Accept"}, {"pageSize": "1000"}),
    "Cohere": ("cohere", ("command-a-03-2025", "command-r-plus-08-2024"),
               "https://api.cohere.com/v1/models", {"Authorization", "Accept"}, {"endpoint": "chat", "page_size": "1000"}),
    "xAI Grok": ("xai", ("grok-4", "grok-4-fast"),
                 "https://api.x.ai/v1/language-models", {"Authorization", "Accept"}, {}),
    "DeepSeek": ("deepseek", ("deepseek-flash", "deepseek-v4-pro"),
                 "https://api.deepseek.com/models", {"Authorization", "Accept"}, {}),
}


class FakeHttp:
    """Stands in for httpx.get. Replies are used in order; the last one repeats. An exception is raised, not returned."""

    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def __call__(self, url, headers, params, timeout):
        self.calls.append((url, dict(headers), dict(params), timeout))
        reply = self.replies[min(len(self.calls), len(self.replies)) - 1]
        if isinstance(reply, BaseException):
            raise reply
        return reply


def fixture_replies(name: str) -> list[HttpReply]:
    pages = json.loads((FIXTURES / f"models_{name}.json").read_text(encoding="utf-8"))["pages"]
    return [HttpReply(200, page) for page in pages]


def make_error(name: str, status: int | None = None, message: str = "") -> Exception:
    """An exception with the class name, status_code and message that LiteLLM's exceptions carry."""
    error = type(name, (Exception,), {})(message)
    if status is not None:
        error.status_code = status
    error.message = message
    return error


def ok_response(text="done", tokens=42):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
                           usage=SimpleNamespace(total_tokens=tokens))


class FakeBackend:
    def __init__(self, *outcomes, json_ok=True, cost=0.0123):
        self.outcomes, self.calls, self.json_ok, self.cost_value = list(outcomes), [], json_ok, cost

    def completion(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes[min(len(self.calls), len(self.outcomes)) - 1]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def supports_json_mode(self, settings):
        return self.json_ok

    def cost(self, response):
        return self.cost_value


def assert_no_visible_chain(error: BaseException) -> None:
    """The error must not expose an earlier exception (whose text could quote the key): it was either raised with
    `from None`, or raised when nothing was being handled."""
    assert error.__cause__ is None and (error.__context__ is None or error.__suppress_context__)


SETTINGS = LLMSettings("OpenAI", "gpt-4o", KEY)
MESSAGES = [{"role": "user", "content": "hi"}]


# ---------------------------------------------------------------- providers

def test_providers_are_in_the_order_the_spec_lists_them():
    assert client.PROVIDER_NAMES == ("OpenAI", "Anthropic", "Google Gemini", "Cohere", "xAI Grok", "DeepSeek")


@pytest.mark.parametrize("provider, prefix", [
    ("OpenAI", "openai"), ("Anthropic", "anthropic"), ("Google Gemini", "gemini"),
    ("Cohere", "cohere_chat"), ("xAI Grok", "xai"), ("DeepSeek", "deepseek"),
])
def test_litellm_model_strings_use_the_documented_prefix(provider, prefix):
    assert LLMSettings(provider, "some-model", KEY).litellm_model == f"{prefix}/some-model"


def test_unknown_provider_is_a_programming_error():
    with pytest.raises(ValueError, match="Unknown provider"):
        client.provider_named("Acme AI")
    with pytest.raises(ValueError):
        LLMSettings("Acme AI", "m", KEY)
    with pytest.raises(ValueError):
        list_models("Acme AI", KEY, http_get=FakeHttp())


def test_printing_the_settings_never_shows_the_key():
    assert KEY not in repr(SETTINGS) and KEY not in str(SETTINGS)


def test_the_settings_can_say_whether_a_text_contains_the_key_without_handing_it_out():
    long_key = LLMSettings("OpenAI", "m", "fake-key-12345")
    assert long_key.key_is_in("please use fake-key-12345 for this") and long_key.key_is_in("fake-key-12345")
    assert not long_key.key_is_in("a laptop that lasts all day") and not long_key.key_is_in("") and not long_key.key_is_in(None)
    assert LLMSettings("OpenAI", "m", "  fake-key-12345  ").key_is_in("my key is fake-key-12345.")      # the key is looked for as it is used
    assert client.MIN_KEY_CHARS_CHECKED == 8
    short = LLMSettings("OpenAI", "m", "laptop")                                                        # too short: it would match ordinary words
    assert not short.key_is_in("a laptop that lasts all day") and not LLMSettings("OpenAI", "m", "").key_is_in("anything")
    assert LLMSettings("OpenAI", "m", "12345678").key_is_in("x12345678x") and not LLMSettings("OpenAI", "m", "1234567").key_is_in("1234567")


# ---------------------------------------------------------------- the model-list request

@pytest.mark.parametrize("provider", CASES)
def test_the_first_request_goes_to_the_documented_address_with_the_documented_headers(provider):
    name, _models, url, header_names, params = CASES[provider]
    http = FakeHttp(*fixture_replies(name))
    list_models(provider, KEY, http_get=http)
    sent_url, headers, sent_params, timeout = http.calls[0]
    assert sent_url == url
    assert set(headers) == header_names
    assert sent_params == params
    assert timeout == client.MODELS_TIMEOUT


@pytest.mark.parametrize("provider", CASES)
def test_the_key_travels_only_in_a_header(provider):
    http = FakeHttp(*fixture_replies(CASES[provider][0]))
    list_models(provider, KEY, http_get=http)
    for url, headers, params, _ in http.calls:
        assert KEY not in url and not any(KEY in value for value in params.values())
        assert sum(KEY in value for value in headers.values()) == 1


def test_header_formats():
    def headers_for(provider):
        http = FakeHttp(*fixture_replies(CASES[provider][0]))
        list_models(provider, f"  {KEY}  ", http_get=http)   # spaces around a pasted key are ignored
        return http.calls[0][1]

    assert headers_for("OpenAI")["Authorization"] == f"Bearer {KEY}"
    assert headers_for("Anthropic")["Authorization"] == f"Bearer {KEY}"
    assert headers_for("Anthropic")["anthropic-version"] == client.ANTHROPIC_VERSION == "2026-10-21"
    assert headers_for("Google Gemini")["x-goog-api-key"] == KEY
    assert headers_for("Cohere")["Authorization"] == f"Bearer {KEY}"
    assert headers_for("xAI Grok")["Authorization"] == f"Bearer {KEY}"
    assert headers_for("DeepSeek")["Authorization"] == f"Bearer {KEY}"


# ---------------------------------------------------------------- reading the answer

@pytest.mark.parametrize("provider", CASES)
def test_only_chat_models_come_back_sorted(provider):
    name, expected, *_ = CASES[provider]
    assert list_models(provider, KEY, http_get=FakeHttp(*fixture_replies(name))) == expected


def test_anthropic_asks_for_the_next_page_with_after_id():
    http = FakeHttp(*fixture_replies("anthropic"))
    list_models("Anthropic", KEY, http_get=http)
    assert len(http.calls) == 2
    assert http.calls[1][2] == {"limit": "1000", "after_id": "claude-sonnet-5-5"}


def test_gemini_asks_for_the_next_page_with_the_page_token():
    http = FakeHttp(*fixture_replies("gemini"))
    list_models("Google Gemini", KEY, http_get=http)
    assert len(http.calls) == 2
    assert http.calls[1][2] == {"pageSize": "1000", "pageToken": "page-two-token"}


def test_cohere_asks_for_the_next_page_with_page_token_and_keeps_the_chat_filter():
    pages = [HttpReply(200, {"models": [{"name": "command-a", "endpoints": ["chat"]}], "next_page_token": "t2"}),
             HttpReply(200, {"models": [{"name": "command-b", "endpoints": ["chat"]}]})]
    http = FakeHttp(*pages)
    assert list_models("Cohere", KEY, http_get=http) == ("command-a", "command-b")
    assert http.calls[1][2] == {"endpoint": "chat", "page_size": "1000", "page_token": "t2"}


def test_paging_stops_even_if_a_provider_never_says_it_is_done():
    endless = HttpReply(200, {"data": [{"id": "claude-x"}], "has_more": True, "last_id": "claude-x"})
    http = FakeHttp(endless)
    assert list_models("Anthropic", KEY, http_get=http) == ("claude-x",)
    assert len(http.calls) == MAX_PAGES


def test_sorting_ignores_case_and_duplicates_are_removed():
    reply = HttpReply(200, {"data": [{"id": "gpt-b"}, {"id": "GPT-a"}, {"id": "gpt-b"}, {"id": "gpt-c"}]})
    assert list_models("OpenAI", KEY, http_get=FakeHttp(reply)) == ("GPT-a", "gpt-b", "gpt-c")


@pytest.mark.parametrize("name, chat", [
    ("gpt-4o", True), ("o3-mini", True), ("claude-opus-5-5", True), ("gemma-3-27b-it", True), ("deliver-x", True),
    ("text-embedding-3-small", False), ("dall-e-3", False), ("whisper-1", False), ("gpt-4o-mini-tts", False),
    ("omni-moderation-latest", False), ("gpt-4o-realtime-preview", False), ("gpt-4o-audio-preview", False),
    ("gpt-image-1", False), ("gemini-live-2.5-flash", False), ("imagen-4.0-generate-001", False),
    ("veo-3", False), ("davinci-002", False), ("gpt-3.5-turbo-instruct", False), ("sora-2", False),
])
def test_chat_model_name_filter(name, chat):
    assert client.is_chat_model_name(name) is chat


def test_models_are_filtered_by_what_the_provider_says_when_it_says_it():
    gemini = HttpReply(200, {"models": [{"name": "models/a-chat", "supportedGenerationMethods": ["generateContent"]},
                                        {"name": "models/b-embed", "supportedGenerationMethods": ["embedContent"]},
                                        {"name": "models/c-nomethods"}]})
    assert list_models("Google Gemini", KEY, http_get=FakeHttp(gemini)) == ("a-chat",)
    cohere = HttpReply(200, {"models": [{"name": "x", "endpoints": ["embed"]}, {"name": "y", "endpoints": ["chat"], "is_deprecated": True},
                                        {"name": "z", "endpoints": ["chat"]}]})
    assert list_models("Cohere", KEY, http_get=FakeHttp(cohere)) == ("z",)
    xai = HttpReply(200, {"models": [{"id": "img", "output_modalities": ["image"]}, {"id": "txt", "output_modalities": ["text"]},
                                     {"id": "bare"}]})
    assert list_models("xAI Grok", KEY, http_get=FakeHttp(xai)) == ("bare", "txt")


# ---------------------------------------------------------------- errors

SCENARIOS = [
    ("invalid_key", HttpReply(401, {"error": f"Incorrect API key provided: {KEY}"})),
    ("invalid_key", HttpReply(403, None)),
    ("rate_limit", HttpReply(429, None)),
    ("server", HttpReply(500, None)),
    ("server", HttpReply(503, None)),
    ("bad_request", HttpReply(404, None)),
    ("bad_request", HttpReply(400, {"error": KEY})),
    ("timeout", HttpTimeout()),
    ("network", HttpUnreachable()),
    ("bad_response", HttpReply(200, None)),
    ("bad_response", HttpReply(200, ["not", "an", "object"])),
    ("bad_response", HttpReply(200, {"unexpected": []})),
    ("no_models", HttpReply(200, {"data": []})),
]


@pytest.mark.parametrize("kind, reply", SCENARIOS)
@pytest.mark.parametrize("provider", ["OpenAI", "Anthropic"])
def test_every_failure_becomes_a_friendly_error_that_never_contains_the_key(provider, kind, reply):
    with pytest.raises(LLMError) as caught:
        list_models(provider, KEY, http_get=FakeHttp(reply))
    error = caught.value
    assert error.kind == kind
    assert provider in error.user_message
    for text in (error.user_message, str(error), repr(error), repr(error.args)):
        assert KEY not in text
    assert_no_visible_chain(error)


def test_a_provider_with_only_non_chat_models_gives_a_clear_message():
    reply = HttpReply(200, {"data": [{"id": "text-embedding-3-small"}, {"id": "whisper-1"}]})
    with pytest.raises(LLMError) as caught:
        list_models("OpenAI", KEY, http_get=FakeHttp(reply))
    assert caught.value.kind == "no_models"
    assert caught.value.user_message == "OpenAI accepted the key but listed no chat models. Try another provider."


def test_the_two_messages_the_spec_words_exactly():
    with pytest.raises(LLMError) as invalid:
        list_models("OpenAI", KEY, http_get=FakeHttp(HttpReply(401, None)))
    assert invalid.value.user_message == "Invalid API key for OpenAI."
    with pytest.raises(LLMError) as offline:
        list_models("Anthropic", KEY, http_get=FakeHttp(HttpUnreachable()))
    assert offline.value.user_message == "Could not reach Anthropic. Check your connection."


@pytest.mark.parametrize("key", ["", "   ", None])
def test_an_empty_key_is_refused_without_calling_the_provider(key):
    http = FakeHttp(HttpReply(200, {"data": []}))
    with pytest.raises(LLMError) as caught:
        list_models("OpenAI", key, http_get=http)
    assert caught.value.kind == "no_key" and caught.value.user_message == "Enter an API key first."
    assert http.calls == []


@pytest.mark.parametrize("key", ["test abc", "test-a\nb", "test-é", "test-\x00"])
def test_a_key_with_spaces_or_odd_characters_is_refused_without_calling_the_provider(key):
    http = FakeHttp(HttpReply(200, {"data": []}))
    with pytest.raises(LLMError) as caught:
        list_models("Cohere", key, http_get=http)
    assert caught.value.kind == "invalid_key" and caught.value.user_message == "Invalid API key for Cohere."
    assert http.calls == []


def test_a_wrong_key_stops_at_the_first_page():
    http = FakeHttp(HttpReply(401, None), *fixture_replies("anthropic"))
    with pytest.raises(LLMError):
        list_models("Anthropic", KEY, http_get=http)
    assert len(http.calls) == 1


# ---------------------------------------------------------------- default key and cache key

def test_no_default_key_unless_the_owner_set_one():
    assert client.default_key_for("OpenAI", environ={}) is None
    assert client.default_key_for("OpenAI", secret_lookup=lambda name: None, environ={}) is None


def test_default_key_comes_from_secrets_or_the_environment_and_secrets_win():
    assert client.default_key_for("OpenAI", environ={"OPENAI_API_KEY": " env-key "}) == "env-key"
    secrets = {"OPENAI_API_KEY": "secret-key"}.get
    assert client.default_key_for("OpenAI", secret_lookup=secrets, environ={"OPENAI_API_KEY": "env-key"}) == "secret-key"


def test_default_key_is_looked_up_per_provider_and_ignores_junk():
    environ = {"ANTHROPIC_API_KEY": "a-key", "XAI_API_KEY": "   ", "COHERE_API_KEY": 123}
    assert client.default_key_for("Anthropic", environ=environ) == "a-key"
    assert client.default_key_for("OpenAI", environ=environ) is None
    assert client.default_key_for("xAI Grok", environ=environ) is None
    assert client.default_key_for("Cohere", environ=environ) is None
    assert client.default_key_for("Google Gemini", environ={"GOOGLE_API_KEY": "g-key"}) == "g-key"
    assert client.default_key_for("Google Gemini", environ={"GEMINI_API_KEY": "m", "GOOGLE_API_KEY": "g"}) == "m"


def test_cache_key_is_stable_distinct_and_hides_the_key():
    first = client.cache_key("OpenAI", KEY)
    assert first == client.cache_key("OpenAI", KEY)
    assert first != client.cache_key("Anthropic", KEY) and first != client.cache_key("OpenAI", KEY + "x")
    assert KEY not in first and len(first) == 64


# ---------------------------------------------------------------- temperature policy

def test_fixed_temperatures():
    assert [client.temperature_for(job) for job in ("scoring", "feedback", "qa", "wording")] == [0, 0, 0.1, 0.4]


def test_temperatures_cannot_be_changed_or_invented():
    with pytest.raises(TypeError):
        client.TEMPERATURES["scoring"] = 1   # type: ignore[index]
    with pytest.raises(ValueError, match="Unknown LLM job"):
        client.temperature_for("creative")


# ---------------------------------------------------------------- call_llm

def test_a_normal_call_returns_text_tokens_and_cost():
    backend = FakeBackend(ok_response("hello", 42))
    result = call_llm(SETTINGS, MESSAGES, 0, backend=backend, sleep=lambda s: None)
    assert (result.text, result.tokens, result.cost_usd, result.warnings) == ("hello", 42, 0.0123, ())
    assert backend.calls == [{
        "model": "openai/gpt-4o", "messages": MESSAGES, "api_key": KEY,
        "timeout": CALL_TIMEOUT, "num_retries": 0, "temperature": 0,
    }]


def test_the_default_timeout_is_60_seconds_and_can_be_changed():
    assert CALL_TIMEOUT == 60
    backend = FakeBackend(ok_response())
    call_llm(SETTINGS, MESSAGES, 0, timeout=5, backend=backend)
    assert backend.calls[0]["timeout"] == 5


def test_messages_are_copied_not_shared():
    backend = FakeBackend(ok_response())
    call_llm(SETTINGS, MESSAGES, 0, backend=backend)
    assert backend.calls[0]["messages"] == MESSAGES and backend.calls[0]["messages"][0] is not MESSAGES[0]


def test_no_temperature_is_sent_when_none_is_given():
    backend = FakeBackend(ok_response())
    call_llm(SETTINGS, MESSAGES, None, backend=backend)
    assert "temperature" not in backend.calls[0]


@pytest.mark.parametrize("json_mode, supported, sent", [(True, True, True), (True, False, False), (False, True, False)])
def test_json_mode_is_sent_only_when_asked_for_and_supported(json_mode, supported, sent):
    backend = FakeBackend(ok_response(), json_ok=supported)
    call_llm(SETTINGS, MESSAGES, 0, json_mode=json_mode, backend=backend)
    assert ("response_format" in backend.calls[0]) is sent
    if sent:
        assert backend.calls[0]["response_format"] == {"type": "json_object"}


TEMPERATURE_REFUSAL = make_error(
    "BadRequestError", 400, "Unsupported value: 'temperature' does not support 0 with this model. Only the default (1) value is supported.")


def test_a_refused_temperature_is_repeated_once_without_it_with_a_warning():
    sleeps = []
    backend = FakeBackend(TEMPERATURE_REFUSAL, ok_response("fine"))
    result = call_llm(SETTINGS, MESSAGES, 0, backend=backend, sleep=sleeps.append)
    assert result.text == "fine"
    assert len(backend.calls) == 2 and "temperature" in backend.calls[0] and "temperature" not in backend.calls[1]
    assert len(result.warnings) == 1 and "temperature" in result.warnings[0] and "OpenAI" in result.warnings[0]
    assert sleeps == []


def test_the_unsupported_params_error_class_is_recognised_too():
    refusal = make_error("UnsupportedParamsError", 400, "openai does not support parameters: ['temperature']")
    backend = FakeBackend(refusal, ok_response())
    assert call_llm(SETTINGS, MESSAGES, 0, backend=backend).warnings


def test_a_second_temperature_complaint_is_not_retried_again():
    backend = FakeBackend(TEMPERATURE_REFUSAL, TEMPERATURE_REFUSAL)
    with pytest.raises(LLMError) as caught:
        call_llm(SETTINGS, MESSAGES, 0, backend=backend)
    assert caught.value.kind == "bad_request" and len(backend.calls) == 2


def test_a_refused_json_mode_is_repeated_once_without_it():
    refusal = make_error("BadRequestError", 400, "This model does not support response_format of type json_object.")
    backend = FakeBackend(refusal, ok_response())
    result = call_llm(SETTINGS, MESSAGES, 0, json_mode=True, backend=backend)
    assert "response_format" in backend.calls[0] and "response_format" not in backend.calls[1]
    assert "temperature" in backend.calls[1] and "response format" in result.warnings[0]


def test_other_bad_requests_are_not_retried():
    backend = FakeBackend(make_error("BadRequestError", 400, "context length exceeded"))
    with pytest.raises(LLMError) as caught:
        call_llm(SETTINGS, MESSAGES, 0, backend=backend)
    assert caught.value.kind == "bad_request" and len(backend.calls) == 1


@pytest.mark.parametrize("error, kind", [
    (make_error("ServiceUnavailableError", 503, "overloaded"), "server"),
    (make_error("InternalServerError", 500, "oops"), "server"),
    (make_error("RateLimitError", 429, "slow down"), "rate_limit"),
    (make_error("Timeout", 408, "timed out"), "timeout"),
    (make_error("APIConnectionError", 500, "connection reset"), "network"),   # LiteLLM gives these a 500 status
    (TimeoutError("read timed out"), "timeout"),
])
def test_temporary_failures_are_retried_once_after_a_pause_then_raised(error, kind):
    sleeps = []
    backend = FakeBackend(error)
    with pytest.raises(LLMError) as caught:
        call_llm(SETTINGS, MESSAGES, 0, backend=backend, sleep=sleeps.append)
    assert caught.value.kind == kind
    assert len(backend.calls) == 2 and sleeps == [BACKOFF_SECONDS]


def test_a_temporary_failure_that_clears_on_retry_succeeds():
    sleeps = []
    backend = FakeBackend(make_error("ServiceUnavailableError", 503), ok_response("later"))
    assert call_llm(SETTINGS, MESSAGES, 0, backend=backend, sleep=sleeps.append).text == "later"
    assert sleeps == [BACKOFF_SECONDS]


def test_a_refused_temperature_and_a_server_hiccup_in_the_same_call_are_both_handled():
    sleeps = []
    backend = FakeBackend(TEMPERATURE_REFUSAL, make_error("ServiceUnavailableError", 503), ok_response("ok"))
    result = call_llm(SETTINGS, MESSAGES, 0, backend=backend, sleep=sleeps.append)
    assert result.text == "ok" and len(backend.calls) == 3 and sleeps == [BACKOFF_SECONDS] and len(result.warnings) == 1


@pytest.mark.parametrize("error, kind, message", [
    (make_error("AuthenticationError", 401, f"Incorrect API key provided: {KEY}"), "invalid_key", "Invalid API key for OpenAI."),
    (make_error("PermissionDeniedError", 403), "invalid_key", "Invalid API key for OpenAI."),
    (make_error("NotFoundError", 404, "model not found"), "not_found",
     "OpenAI does not offer that model for this key. Pick another model in the sidebar."),
    (ValueError(f"boom {KEY}"), "other", "The request to OpenAI failed. Try again, or choose another model."),
])
def test_permanent_failures_are_not_retried_and_never_leak_the_key_or_the_raw_text(error, kind, message):
    sleeps = []
    backend = FakeBackend(error)
    with pytest.raises(LLMError) as caught:
        call_llm(SETTINGS, MESSAGES, 0, backend=backend, sleep=sleeps.append)
    exc = caught.value
    assert (exc.kind, exc.user_message) == (kind, message)
    assert len(backend.calls) == 1 and sleeps == []
    for text in (exc.user_message, str(exc), repr(exc), repr(exc.args)):
        assert KEY not in text and "boom" not in text and "Incorrect" not in text
    assert_no_visible_chain(exc)


def test_an_llm_error_from_the_backend_passes_through_unchanged():
    problem = LLMError("The LLM library is not installed.", "not_installed")
    with pytest.raises(LLMError) as caught:
        call_llm(SETTINGS, MESSAGES, 0, backend=FakeBackend(problem))
    assert caught.value is problem


@pytest.mark.parametrize("response", [SimpleNamespace(choices=[]), SimpleNamespace(), None, SimpleNamespace(choices=[SimpleNamespace()])])
def test_an_unreadable_answer_is_a_friendly_error(response):
    with pytest.raises(LLMError) as caught:
        call_llm(SETTINGS, MESSAGES, 0, backend=FakeBackend(response))
    assert caught.value.kind == "bad_response"


def test_missing_text_tokens_or_cost_are_reported_as_missing_not_zero():
    response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=None))])
    result = call_llm(SETTINGS, MESSAGES, 0, backend=FakeBackend(response, cost=None))
    assert (result.text, result.tokens, result.cost_usd) == ("", None, None)


def test_empty_messages_are_a_programming_error():
    with pytest.raises(ValueError):
        call_llm(SETTINGS, [], 0, backend=FakeBackend(ok_response()))


# ---------------------------------------------------------------- the thin glue around httpx and litellm

def install_fake_httpx(monkeypatch, outcome):
    module = types.ModuleType("httpx")

    class HTTPError(Exception): ...
    class TimeoutException(HTTPError): ...

    module.HTTPError, module.TimeoutException, module.calls = HTTPError, TimeoutException, []

    def get(url, **kwargs):
        module.calls.append((url, kwargs))
        return outcome

    module.get = get
    monkeypatch.setitem(sys.modules, "httpx", module)
    return module


def test_the_real_http_function_passes_everything_to_httpx_and_decodes_json(monkeypatch):
    reply = SimpleNamespace(status_code=200, json=lambda: {"data": []})
    module = install_fake_httpx(monkeypatch, reply)
    assert client._default_http_get("https://x.test/models", {"H": "v"}, {"p": "1"}, 9.0) == HttpReply(200, {"data": []})
    assert module.calls == [("https://x.test/models", {"headers": {"H": "v"}, "params": {"p": "1"}, "timeout": 9.0})]


def test_a_reply_that_is_not_json_gives_none(monkeypatch):
    def not_json():
        raise ValueError("no json")
    install_fake_httpx(monkeypatch, SimpleNamespace(status_code=502, json=not_json))
    assert client._default_http_get("https://x.test", {}, {}, 1.0) == HttpReply(502, None)


def test_httpx_errors_are_replaced_so_their_text_cannot_leak_the_key(monkeypatch):
    module = install_fake_httpx(monkeypatch, None)
    for raised, expected in ((module.TimeoutException, HttpTimeout), (module.HTTPError, HttpUnreachable)):
        def get(url, _raised=raised, **kwargs):
            raise _raised(f"request to {url} with {kwargs['headers']} failed")   # httpx errors can quote the request

        module.get = get
        with pytest.raises(expected) as caught:
            client._default_http_get("https://x.test", {"Authorization": f"Bearer {KEY}"}, {}, 1.0)
        assert caught.value.__cause__ is None and caught.value.__suppress_context__
        assert KEY not in repr(caught.value) and KEY not in repr(caught.value.args)


def test_a_missing_httpx_gives_a_friendly_message(monkeypatch):
    monkeypatch.setitem(sys.modules, "httpx", None)
    with pytest.raises(LLMError, match="pip install -r requirements.txt") as caught:
        list_models("OpenAI", KEY)
    assert caught.value.kind == "not_installed"


def install_fake_litellm(monkeypatch, **attrs):
    module = types.ModuleType("litellm")
    module.__dict__.update(attrs)
    monkeypatch.setitem(sys.modules, "litellm", module)
    return module


def test_the_litellm_backend_forwards_the_call(monkeypatch):
    seen = {}
    install_fake_litellm(monkeypatch, completion=lambda **kw: seen.update(kw) or "response")
    assert client._LiteLLMBackend().completion(model="openai/gpt-4o", api_key=KEY) == "response"
    assert seen == {"model": "openai/gpt-4o", "api_key": KEY}


def test_the_litellm_backend_checks_json_support_for_the_right_model_and_provider(monkeypatch):
    seen = {}

    def supported(**kwargs):
        seen.update(kwargs)
        return ["temperature", "response_format"]

    install_fake_litellm(monkeypatch, get_supported_openai_params=supported)
    assert client._LiteLLMBackend().supports_json_mode(LLMSettings("Cohere", "command-a", KEY)) is True
    assert seen == {"model": "command-a", "custom_llm_provider": "cohere_chat"}


@pytest.mark.parametrize("returned", [["temperature"], [], None])
def test_json_mode_is_off_when_the_model_does_not_list_it(monkeypatch, returned):
    install_fake_litellm(monkeypatch, get_supported_openai_params=lambda **kw: returned)
    assert client._LiteLLMBackend().supports_json_mode(SETTINGS) is False


def test_json_mode_is_off_when_litellm_cannot_tell(monkeypatch):
    def fail(**kwargs):
        raise RuntimeError("unknown model")
    install_fake_litellm(monkeypatch, get_supported_openai_params=fail)
    assert client._LiteLLMBackend().supports_json_mode(SETTINGS) is False


def test_cost_is_a_float_or_none(monkeypatch):
    install_fake_litellm(monkeypatch, completion_cost=lambda completion_response: 3)
    assert client._LiteLLMBackend().cost(object()) == 3.0 and isinstance(client._LiteLLMBackend().cost(object()), float)
    install_fake_litellm(monkeypatch, completion_cost=lambda completion_response: None)
    assert client._LiteLLMBackend().cost(object()) is None

    def unknown(completion_response):
        raise ValueError("no price")
    install_fake_litellm(monkeypatch, completion_cost=unknown)
    assert client._LiteLLMBackend().cost(object()) is None


def test_a_missing_litellm_gives_a_friendly_message(monkeypatch):
    monkeypatch.setitem(sys.modules, "litellm", None)
    with pytest.raises(LLMError, match="pip install -r requirements.txt") as caught:
        call_llm(SETTINGS, MESSAGES, 0)
    assert caught.value.kind == "not_installed"


# ---------------------------------------------------------------- guardrails

def test_the_client_never_prints_logs_or_puts_a_key_in_an_address():
    source = Path(client.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    top_level = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            top_level.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            top_level.add(node.module.split(".")[0])
    # httpx and litellm are imported only inside functions, so importing the module never needs them
    assert top_level <= {"__future__", "hashlib", "os", "re", "time", "collections", "dataclasses", "types", "typing"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in {"print", "open", "exec", "eval"}
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            assert "logging" not in ast.dump(node)
    assert "logger" not in source and not re.search(r"[?&]key=", source)
