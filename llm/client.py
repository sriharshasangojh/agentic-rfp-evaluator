"""LLM layer (spec section 6): providers, key check, model list, call wrapper and temperature policy.

How the pieces fit
- ``list_models`` asks a provider's own "list models" address (with httpx) whether a key works and which chat
  models it can use. The key travels only in a request header, never in the address, so no error message can
  contain it.
- ``call_llm`` sends one chat request through LiteLLM with a timeout, one retry and the temperature rule.
- Every failure becomes an ``LLMError`` whose message is safe to show: it names the provider and says what to
  do, and it never contains the key or the provider's raw error text (providers sometimes echo part of a key).
- httpx and litellm are imported only when first used, so importing this module is instant and the tests can
  run with plain fakes. Nothing in this module prints or logs.

Addresses, headers and model prefixes were checked against each provider's current documentation on
2026-10-23 (see DECISIONS.md, F07).
"""
from __future__ import annotations

import hashlib
import os
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, NamedTuple, Protocol

MODELS_TIMEOUT = 15.0     # seconds to wait for a provider's model list
CALL_TIMEOUT = 60.0       # seconds to wait for one chat answer (spec 2.4 rule 16)
BACKOFF_SECONDS = 2.0     # pause before the single retry
MAX_PAGES = 20            # safety stop when a provider keeps saying "there is another page"
ANTHROPIC_VERSION = "2026-10-21"

# Fixed temperatures (spec 6.4). Not editable in the app.
TEMPERATURES: Mapping[str, float] = MappingProxyType({
    "scoring": 0,        # scoring proposals
    "feedback": 0,       # reading typed feedback
    "qa": 0.1,           # Ask the RFP
    "wording": 0.4,      # pros/cons, recommendation reason, negotiation wording
})


def temperature_for(job: str) -> float:
    """The fixed temperature for a job: scoring, feedback, qa or wording."""
    try:
        return TEMPERATURES[job]
    except KeyError:
        raise ValueError(f"Unknown LLM job '{job}'. Use one of: {', '.join(TEMPERATURES)}.") from None


# ---------------------------------------------------------------- errors

class LLMError(Exception):
    """A failure with a message that is safe to show. ``kind`` says what happened:
    no_key, invalid_key, network, timeout, rate_limit, server, not_found, bad_request, bad_response,
    no_models, not_installed or other."""

    def __init__(self, user_message: str, kind: str = "other") -> None:
        super().__init__(user_message)
        self.user_message = user_message
        self.kind = kind


class HttpTimeout(Exception):
    """The provider did not answer in time (raised by an ``http_get`` function)."""


class HttpUnreachable(Exception):
    """The provider could not be reached (raised by an ``http_get`` function)."""


class HttpReply(NamedTuple):
    status: int
    data: Any   # the decoded JSON body, or None when the body was not JSON


HttpGet = Callable[[str, Mapping[str, str], Mapping[str, str], float], HttpReply]


class _BadReply(Exception):
    """A provider's answer did not have the documented shape."""


def _message(kind: str, provider: str) -> str:
    """The friendly text for each kind of failure. Never includes a key or a provider's own wording."""
    return {
        "no_key": "Enter an API key first.",
        "invalid_key": f"Invalid API key for {provider}.",
        "network": f"Could not reach {provider}. Check your connection.",
        "timeout": f"{provider} took too long to answer. Try again in a moment.",
        "rate_limit": f"{provider} is limiting requests right now. Wait a minute and try again.",
        "server": f"{provider} had a problem on its side. Try again in a moment.",
        "not_found": f"{provider} does not offer that model for this key. Pick another model in the sidebar.",
        "bad_request": f"{provider} could not process this request. Try another model.",
        "bad_response": f"{provider} sent a reply this app could not read. Try again, or choose another provider.",
        "no_models": f"{provider} accepted the key but listed no chat models. Try another provider.",
        "other": f"The request to {provider} failed. Try again, or choose another model.",
    }[kind]


# ---------------------------------------------------------------- reading each provider's model list

# Words that mark a model that is not for chat. Matched against whole pieces of the model name, so a model
# called "deliver-x" is not caught by "live". (Used where the provider gives no structured information.)
NON_CHAT_WORDS = frozenset({
    "embedding", "embeddings", "moderation", "whisper", "tts", "dall", "image", "imagen", "audio",
    "transcribe", "realtime", "live", "davinci", "babbage", "instruct", "sora", "veo", "codex",
    "computer", "rerank",
})


def is_chat_model_name(model_id: str) -> bool:
    """False for embedding, image, audio, moderation, TTS, realtime and legacy-completion models."""
    return not (set(re.split(r"[^a-z0-9]+", model_id.lower())) & NON_CHAT_WORDS)


Page = tuple[list[str], "tuple[str, str] | None"]   # chat model names on this page, and the (parameter, value) for the next page


def _items(body: Any, field_name: str) -> list[dict]:
    if not isinstance(body, dict) or not isinstance(body.get(field_name), list):
        raise _BadReply
    return [item for item in body[field_name] if isinstance(item, dict)]


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _outputs_text(item: dict) -> bool:
    """True unless the provider says this model's output is not text."""
    modalities = item.get("output_modalities")
    return "text" in modalities if isinstance(modalities, list) else True


def _parse_openai(body: Any) -> Page:
    ids = [_text(m.get("id")) for m in _items(body, "data")]
    return [i for i in ids if i and is_chat_model_name(i)], None


def _parse_deepseek(body: Any) -> Page:
    ids = [_text(m.get("id")) for m in _items(body, "data") if _outputs_text(m)]
    return [i for i in ids if i and is_chat_model_name(i)], None


def _parse_anthropic(body: Any) -> Page:
    ids = [_text(m.get("id")) for m in _items(body, "data")]
    last = _text(body.get("last_id"))
    return [i for i in ids if i], (("after_id", last) if body.get("has_more") is True and last else None)


def _parse_gemini(body: Any) -> Page:
    ids = []
    for model in _items(body, "models"):
        name, methods = _text(model.get("name")), model.get("supportedGenerationMethods")
        if not name or not isinstance(methods, list) or "generateContent" not in methods:
            continue
        name = name.removeprefix("models/")
        if is_chat_model_name(name):
            ids.append(name)
    token = _text(body.get("nextPageToken"))
    return ids, (("pageToken", token) if token else None)


def _parse_cohere(body: Any) -> Page:
    ids = []
    for model in _items(body, "models"):
        name, endpoints = _text(model.get("name")), model.get("endpoints")
        if name and isinstance(endpoints, list) and "chat" in endpoints and not model.get("is_deprecated"):
            ids.append(name)
    token = _text(body.get("next_page_token"))
    return ids, (("page_token", token) if token else None)


def _parse_xai(body: Any) -> Page:
    ids = [_text(m.get("id")) for m in _items(body, "models") if _outputs_text(m)]
    return [i for i in ids if i and is_chat_model_name(i)], None


# ---------------------------------------------------------------- providers

def _bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}", "Accept": "application/json"}


@dataclass(frozen=True)
class Provider:
    name: str                                   # shown in the dropdown
    litellm_prefix: str                         # LiteLLM model string = "<prefix>/<model>"
    models_url: str                             # the provider's "list models" address
    key_names: tuple[str, ...]                  # secret / environment names that may hold a default key
    headers: Callable[[str], dict[str, str]]    # request headers for a key
    parse: Callable[[Any], Page]
    params: tuple[tuple[str, str], ...] = ()    # query parameters sent on every page
    key_url: str = ""                           # the page where a person gets a key (a plain link; nothing is sent there)


PROVIDERS: tuple[Provider, ...] = (
    Provider("OpenAI", "openai", "https://api.openai.com/v1/models",
             ("OPENAI_API_KEY",), _bearer, _parse_openai, key_url="https://platform.openai.com/api-keys"),
    Provider("Anthropic", "anthropic", "https://api.anthropic.com/v1/models",
             ("ANTHROPIC_API_KEY",),
             lambda key: {**_bearer(key), "anthropic-version": ANTHROPIC_VERSION},
             _parse_anthropic, (("limit", "1000"),), key_url="https://console.anthropic.com/settings/keys"),
    Provider("Google Gemini", "gemini", "https://generativelanguage.googleapis.com/v1beta/models",
             ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
             lambda key: {"x-goog-api-key": key, "Accept": "application/json"},   # header, so the key is never in the address
             _parse_gemini, (("pageSize", "1000"),), key_url="https://aistudio.google.com/app/apikey"),
    Provider("Cohere", "cohere_chat", "https://api.cohere.com/v1/models",
             ("COHERE_API_KEY",), _bearer, _parse_cohere,
             (("endpoint", "chat"), ("page_size", "1000")), key_url="https://dashboard.cohere.com/api-keys"),
    Provider("xAI Grok", "xai", "https://api.x.ai/v1/language-models",
             ("XAI_API_KEY",), _bearer, _parse_xai, key_url="https://console.x.ai"),
    Provider("DeepSeek", "deepseek", "https://api.deepseek.com/models",
             ("DEEPSEEK_API_KEY",), _bearer, _parse_deepseek, key_url="https://platform.deepseek.com/api_keys"),
)
PROVIDER_NAMES: tuple[str, ...] = tuple(p.name for p in PROVIDERS)


def provider_named(name: str) -> Provider:
    for provider in PROVIDERS:
        if provider.name == name:
            return provider
    raise ValueError(f"Unknown provider '{name}'. Use one of: {', '.join(PROVIDER_NAMES)}.")


def default_key_for(
    provider: str,
    *,
    secret_lookup: Callable[[str], Any] | None = None,
    environ: Mapping[str, str] | None = None,
) -> str | None:
    """A key the app's owner set up for deployment (Streamlit secrets first, then environment variables),
    or None. Nothing is set up by default, so a visitor must bring their own key unless the owner decided otherwise."""
    env = os.environ if environ is None else environ
    for name in provider_named(provider).key_names:
        value = secret_lookup(name) if secret_lookup else None
        if not value:
            value = env.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def cache_key(provider: str, api_key: str) -> str:
    """A fingerprint of provider + key for the per-session model cache, so the key itself is never a dictionary key."""
    return hashlib.sha256(f"{provider}\0{api_key}".encode()).hexdigest()


def _clean_key(provider: Provider, api_key: str) -> str:
    key = (api_key or "").strip()
    if not key:
        raise LLMError(_message("no_key", provider.name), "no_key")
    if not key.isascii() or not key.isprintable() or any(ch.isspace() for ch in key):
        raise LLMError(_message("invalid_key", provider.name), "invalid_key")   # a real key has no spaces or odd characters
    return key


def _default_http_get(url: str, headers: Mapping[str, str], params: Mapping[str, str], timeout: float) -> HttpReply:
    """One GET with httpx. httpx's own errors are replaced (without chaining) because they can quote the request."""
    try:
        import httpx
    except ImportError:
        raise LLMError("The HTTP library is not installed. Run: pip install -r requirements.txt", "not_installed") from None
    try:
        response = httpx.get(url, headers=dict(headers), params=dict(params), timeout=timeout)
    except httpx.TimeoutException:
        raise HttpTimeout from None
    except httpx.HTTPError:
        raise HttpUnreachable from None
    try:
        data = response.json()
    except ValueError:
        data = None
    return HttpReply(response.status_code, data)


def _check_status(provider: str, status: int) -> None:
    if 200 <= status < 300:
        return
    if status in (401, 403):
        raise LLMError(_message("invalid_key", provider), "invalid_key")
    if status == 429:
        raise LLMError(_message("rate_limit", provider), "rate_limit")
    if status >= 500:
        raise LLMError(_message("server", provider), "server")
    raise LLMError(f"{provider} refused the request (HTTP {status}). Check the key and try again.", "bad_request")


def list_models(
    provider: str,
    api_key: str,
    *,
    http_get: HttpGet | None = None,
    timeout: float = MODELS_TIMEOUT,
) -> tuple[str, ...]:
    """Check a key and return the provider's chat models, sorted alphabetically. Raises LLMError."""
    spec = provider_named(provider)
    key = _clean_key(spec, api_key)
    fetch = http_get or _default_http_get
    headers = spec.headers(key)
    base = dict(spec.params)
    params = dict(base)
    found: list[str] = []
    for _ in range(MAX_PAGES):
        try:
            reply = fetch(spec.models_url, headers, params, timeout)
        except HttpTimeout:
            raise LLMError(_message("timeout", spec.name), "timeout") from None
        except HttpUnreachable:
            raise LLMError(_message("network", spec.name), "network") from None
        _check_status(spec.name, reply.status)
        try:
            ids, following = spec.parse(reply.data)
        except _BadReply:
            raise LLMError(_message("bad_response", spec.name), "bad_response") from None
        found.extend(ids)
        if following is None:
            break
        params = {**base, following[0]: following[1]}
    models = sorted(set(found), key=lambda name: (name.lower(), name))
    if not models:
        raise LLMError(_message("no_models", spec.name), "no_models")
    return tuple(models)


# ---------------------------------------------------------------- one chat call

MIN_KEY_CHARS_CHECKED = 8       # see LLMSettings.key_is_in


@dataclass(frozen=True)
class LLMSettings:
    """What the sidebar hands to every call. The key is left out of repr, so printing this never shows it."""
    provider: str
    model: str
    api_key: str = field(repr=False)

    def __post_init__(self) -> None:
        provider_named(self.provider)

    @property
    def litellm_model(self) -> str:
        return f"{provider_named(self.provider).litellm_prefix}/{self.model}"

    def key_is_in(self, text: str) -> bool:
        """True when `text` contains the key itself, as when a person pastes it into a box by mistake. Only this module looks at the
        key; callers just ask. A key shorter than MIN_KEY_CHARS_CHECKED is not looked for: it would match ordinary words."""
        key = self.api_key.strip()
        return len(key) >= MIN_KEY_CHARS_CHECKED and key in (text or "")


@dataclass(frozen=True)
class LLMResult:
    text: str
    tokens: int | None          # None when the provider did not report usage
    cost_usd: float | None      # None when LiteLLM has no price for the model
    warnings: tuple[str, ...] = ()


class Backend(Protocol):
    """What call_llm needs from LiteLLM. Tests pass a fake."""

    def completion(self, **kwargs: Any) -> Any: ...
    def supports_json_mode(self, settings: LLMSettings) -> bool: ...
    def cost(self, response: Any) -> float | None: ...


class _LiteLLMBackend:
    @staticmethod
    def _library() -> Any:
        try:
            import litellm
        except ImportError:
            raise LLMError("The LLM library is not installed. Run: pip install -r requirements.txt", "not_installed") from None
        return litellm

    def completion(self, **kwargs: Any) -> Any:
        return self._library().completion(**kwargs)

    def supports_json_mode(self, settings: LLMSettings) -> bool:
        try:
            params = self._library().get_supported_openai_params(
                model=settings.model, custom_llm_provider=provider_named(settings.provider).litellm_prefix)
            return bool(params) and "response_format" in params
        except Exception:   # unknown model or old library: rely on the prompt instead (spec 6.3)
            return False

    def cost(self, response: Any) -> float | None:
        try:
            value = self._library().completion_cost(completion_response=response)
            return float(value) if value is not None else None
        except Exception:   # no price known for this model
            return None


class ErrorInfo(NamedTuple):
    kind: str
    retriable: bool
    rejected_param: str | None = None   # "temperature" or "response_format" when the provider refused that setting


_TIMEOUT_NAMES = {"Timeout", "APITimeoutError", "ReadTimeout", "ConnectTimeout", "TimeoutError"}
_NETWORK_NAMES = {"APIConnectionError", "ConnectError", "ConnectionError"}
_REJECTABLE = ("temperature", "response_format")


def classify_error(exc: BaseException) -> ErrorInfo:
    """Sort a LiteLLM / provider exception into a kind, using its class name and status code (not its text,
    except to see which setting a provider refused). Class names come first because LiteLLM gives
    connection errors a 500 status."""
    name = type(exc).__name__
    status = getattr(exc, "status_code", None)
    status = status if isinstance(status, int) else None
    if isinstance(exc, TimeoutError) or name in _TIMEOUT_NAMES:
        return ErrorInfo("timeout", True)
    if name in _NETWORK_NAMES:
        return ErrorInfo("network", True)
    if status in (401, 403) or name in {"AuthenticationError", "PermissionDeniedError"}:
        return ErrorInfo("invalid_key", False)
    if status == 429 or name == "RateLimitError":
        return ErrorInfo("rate_limit", True)
    if (status is not None and status >= 500) or name in {"ServiceUnavailableError", "InternalServerError"}:
        return ErrorInfo("server", True)
    if status == 404 or name == "NotFoundError":
        return ErrorInfo("not_found", False)
    if status in (400, 422) or name in {"BadRequestError", "UnsupportedParamsError"}:
        text = str(getattr(exc, "message", "") or exc).lower()
        rejected = next((param for param in _REJECTABLE if param in text), None)
        return ErrorInfo("bad_request", False, rejected)
    return ErrorInfo("other", False)


def _read_response(provider: str, response: Any, backend: Backend) -> LLMResult:
    try:
        text = response.choices[0].message.content
    except (AttributeError, IndexError, TypeError, KeyError):
        raise LLMError(_message("bad_response", provider), "bad_response") from None
    usage = getattr(response, "usage", None)
    tokens = getattr(usage, "total_tokens", None)
    return LLMResult(
        text=text if isinstance(text, str) else "",
        tokens=tokens if isinstance(tokens, int) else None,
        cost_usd=backend.cost(response),
    )


def call_llm(
    settings: LLMSettings,
    messages: Sequence[Mapping[str, Any]],
    temperature: float | None,
    json_mode: bool = False,
    timeout: float = CALL_TIMEOUT,
    *,
    backend: Backend | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> LLMResult:
    """One chat call (spec 6.3). Sends ``response_format=json_object`` only when ``json_mode`` is asked for and the
    model supports it. If the provider refuses the temperature (or JSON mode), repeats once without it and adds a
    warning. Retries once after a pause on a timeout, connection problem, 5xx or rate limit. Raises LLMError."""
    if not messages:
        raise ValueError("messages must not be empty.")
    llm = backend or _LiteLLMBackend()
    kwargs: dict[str, Any] = {
        "model": settings.litellm_model,
        "messages": [dict(message) for message in messages],
        "api_key": settings.api_key,
        "timeout": timeout,
        "num_retries": 0,   # this function does the one retry, so attempts are not multiplied
    }
    if temperature is not None:
        kwargs["temperature"] = temperature
    if json_mode and llm.supports_json_mode(settings):
        kwargs["response_format"] = {"type": "json_object"}

    warnings: list[str] = []
    retried = False
    while True:
        try:
            response = llm.completion(**kwargs)
            break
        except LLMError:
            raise
        except Exception as exc:   # every provider failure becomes an LLMError below; nothing is allowed to crash a run
            info = classify_error(exc)
            if info.rejected_param and info.rejected_param in kwargs:
                del kwargs[info.rejected_param]
                warnings.append(
                    f"{settings.provider} did not accept the {info.rejected_param.replace('_', ' ')} setting for this "
                    "model, so the call was repeated without it. Results may vary slightly between runs.")
                continue
            if info.retriable and not retried:
                retried = True
                sleep(BACKOFF_SECONDS)
                continue
            raise LLMError(_message(info.kind, settings.provider), info.kind) from None   # no chaining: the raw error may quote the key
    result = _read_response(settings.provider, response, llm)
    return LLMResult(result.text, result.tokens, result.cost_usd, tuple(warnings))
