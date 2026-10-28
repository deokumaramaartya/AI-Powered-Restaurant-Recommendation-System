import json
from types import SimpleNamespace

import groq
import httpx
import pytest

from app.models.llm import CallStats, LLMError, LLMParseError, Preferences
from app.models.recommendation import RecommendationRequest
from app.services.interpret import build_interpret_messages, store_vocab
from app.services.llm_groq import MIN_RETRY_CALL_SECONDS, RETRY_BACKOFF_SECONDS, GroqLLMClient
from app.services.prompt import build_messages
from tests.conftest import make_settings

VALID = json.dumps({
    "recommendations": [{"restaurant_id": "ind-1", "rank": 1, "explanation": "Rated 4.5 from 2,100 votes."}],
    "summary": "One strong pick.",
})
REQUEST = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")


def completion(content=VALID, finish_reason="stop", reasoning_tokens=120):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason=finish_reason)],
        usage=SimpleNamespace(
            prompt_tokens=900,
            completion_tokens=300,
            completion_tokens_details=SimpleNamespace(reasoning_tokens=reasoning_tokens),
        ),
    )


def status_error(cls, status, headers=None, body=None):
    response = httpx.Response(status, request=REQUEST, headers=headers or {})
    return cls(f"status {status}", response=response, body=body)


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class FakeSDK:
    """Stands in for groq.Groq: records each create() call and plays back scripted outcomes."""

    def __init__(self, clock, *outcomes, call_seconds=1.0):
        self.calls = []
        self._outcomes = list(outcomes)
        self._clock = clock
        self._call_seconds = call_seconds
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        self._clock.now += self._call_seconds
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


PREFS = Preferences(
    location="Indiranagar", budget="medium", cuisines=["Italian"], min_rating=4.0,
    extra_preferences=None, top_k=1,
)


@pytest.fixture
def candidates(store):
    return [store.get("ind-1"), store.get("ind-7")]


def make_client(*outcomes, call_seconds=1.0, **settings_overrides):
    clock = Clock()
    sdk = FakeSDK(clock, *outcomes, call_seconds=call_seconds)
    settings = make_settings(groq_api_key="test-key", **settings_overrides)
    client = GroqLLMClient.from_settings(settings, sdk=sdk, clock=clock, sleep=clock.sleep)
    return client, sdk, clock


def test_request_uses_gpt_oss_with_json_mode_and_reasoning_settings(candidates):
    """The planned model, JSON mode, and reasoning settings must all be sent; reasoning_format never."""
    client, sdk, _ = make_client(
        completion(), llm_reasoning_effort="medium", llm_max_completion_tokens=3000, llm_temperature=0.5
    )
    client.rank(PREFS, candidates)
    sent = sdk.calls[0]
    assert sent["model"] == "openai/gpt-oss-120b"
    assert sent["messages"] == build_messages(PREFS, candidates)
    assert sent["response_format"] == {"type": "json_object"}
    assert sent["reasoning_effort"] == "medium"
    assert sent["include_reasoning"] is False
    assert sent["max_completion_tokens"] == 3000
    assert sent["temperature"] == 0.5
    assert sent["timeout"] == 6
    assert "reasoning_format" not in sent
    assert client.model == "openai/gpt-oss-120b"


def test_success_returns_the_parsed_result_and_fills_call_stats(candidates):
    """Traces need the raw text and the token split, including reasoning tokens (M-21)."""
    client, _, _ = make_client(completion())
    stats = CallStats()
    result = client.rank(PREFS, candidates, stats)
    assert [entry.restaurant_id for entry in result.recommendations] == ["ind-1"]
    assert stats.attempts == 1
    assert stats.requested_count == 1
    assert stats.raw_output == VALID
    assert stats.finish_reason == "stop"
    assert (stats.prompt_tokens, stats.completion_tokens, stats.reasoning_tokens) == (900, 300, 120)


def test_short_ids_in_the_reply_come_back_as_store_ids(candidates):
    """The prompt shows r1, r2, ...; callers must only ever see real restaurant ids."""
    reply = json.dumps({"recommendations": [
        {"restaurant_id": "r2", "rank": 1, "explanation": "Second candidate."},
        {"restaurant_id": "r1", "rank": 2, "explanation": "First candidate."},
    ]})
    client, _, _ = make_client(completion(reply))
    result = client.rank(PREFS, candidates)
    assert [entry.restaurant_id for entry in result.recommendations] == ["ind-7", "ind-1"]


def test_sdk_is_built_without_its_own_retries():
    """The SDK retries twice by default, which would break the deadline rules."""
    client = GroqLLMClient.from_settings(make_settings(groq_api_key="test-key"))
    assert client.sdk.max_retries == 0


@pytest.mark.parametrize("content", ["", None])
def test_empty_content_is_a_parse_error(candidates, content):
    """Reasoning models can spend the whole budget thinking and return nothing (LLM-09)."""
    client, _, _ = make_client(completion(content=content))
    with pytest.raises(LLMParseError):
        client.rank(PREFS, candidates)


def test_reply_cut_off_by_the_token_limit_is_a_parse_error(candidates):
    """A 'length' finish means the JSON may be incomplete even if it happens to parse (LLM-07)."""
    client, sdk, _ = make_client(completion(finish_reason="length"))
    stats = CallStats()
    with pytest.raises(LLMParseError):
        client.rank(PREFS, candidates, stats)
    assert stats.finish_reason == "length"
    assert len(sdk.calls) == 1


def test_invalid_json_is_not_retried(candidates):
    """Asking again for broken JSON doubles latency for little gain (GAP-04, LLM-12)."""
    client, sdk, _ = make_client(completion(content="{not json"))
    with pytest.raises(LLMParseError):
        client.rank(PREFS, candidates)
    assert len(sdk.calls) == 1


def test_groq_json_validation_failure_counts_as_invalid_json(candidates):
    """In JSON mode Groq answers 400 json_validate_failed when the model breaks JSON."""
    error = status_error(groq.BadRequestError, 400, body={"error": {"code": "json_validate_failed"}})
    client, sdk, _ = make_client(error)
    with pytest.raises(LLMParseError):
        client.rank(PREFS, candidates)
    assert len(sdk.calls) == 1


def test_timeout_is_not_retried(candidates):
    """A slow call followed by a second slow call would blow the deadline (LLM-05)."""
    client, sdk, _ = make_client(groq.APITimeoutError(request=REQUEST))
    with pytest.raises(LLMError) as raised:
        client.rank(PREFS, candidates)
    assert raised.value.kind == "timeout"
    assert len(sdk.calls) == 1


def test_429_then_success_retries_once_after_a_backoff(candidates):
    """One rate-limit blip should not cost the user the AI ranking (LLM-02)."""
    client, sdk, clock = make_client(status_error(groq.RateLimitError, 429), completion())
    stats = CallStats()
    result = client.rank(PREFS, candidates, stats)
    assert result.recommendations
    assert len(sdk.calls) == 2
    assert clock.sleeps == [RETRY_BACKOFF_SECONDS]
    assert stats.attempts == 2


def test_429_after_the_retry_gives_up(candidates):
    """Two 429s in a row mean fallback, not a third try (LLM-03)."""
    client, sdk, _ = make_client(status_error(groq.RateLimitError, 429), status_error(groq.RateLimitError, 429))
    with pytest.raises(LLMError) as raised:
        client.rank(PREFS, candidates)
    assert raised.value.kind == "rate_limit"
    assert len(sdk.calls) == 2


def test_5xx_then_success_retries_once(candidates):
    """A transient server error gets the same single retry as a 429 (LLM-04)."""
    client, sdk, _ = make_client(status_error(groq.InternalServerError, 503), completion())
    client.rank(PREFS, candidates)
    assert len(sdk.calls) == 2


def test_long_retry_after_skips_the_retry(candidates):
    """Waiting 10 seconds would break the 8-second deadline (LLM-10)."""
    client, sdk, clock = make_client(status_error(groq.RateLimitError, 429, headers={"retry-after": "10"}))
    with pytest.raises(LLMError):
        client.rank(PREFS, candidates)
    assert len(sdk.calls) == 1
    assert clock.sleeps == []


def test_short_retry_after_is_honoured(candidates):
    client, _, clock = make_client(status_error(groq.RateLimitError, 429, headers={"retry-after": "1.5"}), completion())
    client.rank(PREFS, candidates)
    assert clock.sleeps == [1.5]


def test_retry_is_skipped_when_the_deadline_is_nearly_used(candidates):
    """After a 6-second 429 only 2 seconds remain: not enough for backoff plus 2 seconds of call."""
    client, sdk, _ = make_client(status_error(groq.RateLimitError, 429), completion(), call_seconds=6.0)
    with pytest.raises(LLMError):
        client.rank(PREFS, candidates)
    assert len(sdk.calls) == 1
    assert 8 - 6 < RETRY_BACKOFF_SECONDS + MIN_RETRY_CALL_SECONDS


def test_retry_timeout_is_capped_at_the_time_left(candidates):
    """3 s for the first call plus 0.5 s backoff leaves 4.5 s of the 8 s deadline, less than the 6 s timeout."""
    client, sdk, _ = make_client(status_error(groq.RateLimitError, 429), completion(), call_seconds=3.0)
    client.rank(PREFS, candidates)
    assert sdk.calls[1]["timeout"] == pytest.approx(8 - 3 - RETRY_BACKOFF_SECONDS)


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (status_error(groq.AuthenticationError, 401), "auth"),
        (status_error(groq.NotFoundError, 404), "not_found"),
        (groq.APIConnectionError(request=REQUEST), "network"),
        (status_error(groq.BadRequestError, 400, body={"error": {"code": "invalid_request"}}), "bad_request"),
    ],
)
def test_non_retryable_errors_fail_fast(candidates, error, kind):
    """A bad key, unknown model, bad request, or network failure will not fix itself (LLM-01, LLM-06, LLM-08)."""
    client, sdk, _ = make_client(error)
    with pytest.raises(LLMError) as raised:
        client.rank(PREFS, candidates)
    assert raised.value.kind == kind
    assert len(sdk.calls) == 1


def test_unknown_model_error_names_the_model(candidates):
    """A typo in GROQ_MODEL should be obvious from the log line (CFG-11)."""
    client, _, _ = make_client(status_error(groq.NotFoundError, 404), groq_model="openai/gpt-oss-9000")
    with pytest.raises(LLMError) as raised:
        client.rank(PREFS, candidates)
    assert "openai/gpt-oss-9000" in str(raised.value)


INTERPRET_JSON = json.dumps({
    "cuisines": ["Italian"],
    "budget": "medium",
    "min_rating": 4.0,
    "rest_types": ["Casual Dining"],
    "listing_types": [],
    "book_table": None,
    "online_order": None,
    "dishes": [],
    "unmapped": ["rooftop"],
    "relaxations": [],
})


def test_interpret_sends_the_filter_prompt_and_parses_the_decision(store):
    request = RecommendationRequest(
        location="Indiranagar", cuisines=["Italian"], extra_preferences="cozy dining"
    )
    vocab = store_vocab(store)
    client, sdk, _ = make_client(completion(INTERPRET_JSON))
    stats = CallStats()
    decision = client.interpret(request, vocab, stats)
    assert sdk.calls[0]["messages"] == build_interpret_messages(request, vocab)
    assert decision.rest_types == ["Casual Dining"]
    assert decision.unmapped == ["rooftop"]
    assert stats.raw_output == INTERPRET_JSON
