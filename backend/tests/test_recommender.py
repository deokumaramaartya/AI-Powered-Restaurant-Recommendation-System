import logging

import groq
import pytest

from app.models.llm import FilterDecision, LLMError, LLMParseError, LLMRecommendation, LLMResult
from app.models.recommendation import RecommendationRequest
from app.services.llm_groq import GroqLLMClient
from app.services.recommender import recommend
from tests.conftest import make_settings
from tests.test_llm_groq import REQUEST, Clock, FakeSDK, completion, status_error

G01 = {"location": "Indiranagar", "budget": "medium", "cuisines": ["italian"], "min_rating": 4.0, "top_k": 3}
FALLBACK_ORDER = ["ind-1", "ind-3", "ind-2"]


class FakeLLM:
    """A scripted LLMClient: returns a result, or raises the given error."""

    model = "fake-model"

    def __init__(self, outcome, decision=None):
        self.outcome = outcome
        self.decision = decision
        self.calls = []
        self.interpret_calls = []

    def interpret(self, request, vocab, stats=None):
        self.interpret_calls.append((request, vocab))
        if self.decision is not None:
            return self.decision
        return FilterDecision(
            cuisines=list(request.cuisines),
            budget=request.budget.value if request.budget else None,
            min_rating=request.min_rating,
        )

    def rank(self, preferences, candidates, stats=None):
        self.calls.append((preferences, candidates))
        if stats is not None:
            stats.attempts = 1
            stats.raw_output = "raw text"
            stats.prompt_tokens, stats.completion_tokens, stats.reasoning_tokens = 800, 200, 50
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def picks(*ids, summary="Three solid Italian options."):
    return LLMResult(
        recommendations=[
            LLMRecommendation(restaurant_id=restaurant_id, rank=rank, explanation=f"Pick {rank} fits well.")
            for rank, restaurant_id in enumerate(ids, start=1)
        ],
        summary=summary,
    )


@pytest.fixture
def keyed():
    return make_settings(groq_api_key="test-key")


def ask(store, settings, llm, trace=False, **overrides):
    request = RecommendationRequest(**{**G01, **overrides})
    return recommend(request, store, settings, llm=llm, trace=trace)


def ids(response):
    return [item.restaurant.id for item in response.recommendations]


def test_llm_success_returns_llm_ranking_summary_and_meta(store, keyed):
    """The AI ranking, its explanations and summary, and model and latency must reach the response."""
    llm = FakeLLM(picks("ind-5", "ind-1", "ind-4"))
    response = ask(store, keyed, llm)
    assert response.source == "llm"
    assert ids(response) == ["ind-5", "ind-1", "ind-4"]
    assert response.recommendations[0].explanation == "Pick 1 fits well."
    assert response.recommendations[0].restaurant == store.get("ind-5")
    assert response.summary == "Three solid Italian options."
    assert response.meta["model"] == "fake-model"
    assert isinstance(response.meta["latency_ms"], int) and response.meta["latency_ms"] >= 0
    assert response.meta["candidates_considered"] == 5


def test_llm_receives_resolved_preferences_and_the_candidates(store, keyed):
    """The prompt must use canonical names and the same candidates the parser checks against."""
    llm = FakeLLM(picks("ind-1"))
    ask(store, keyed, llm, extra_preferences="  quiet place ")
    preferences, candidates = llm.calls[0]
    assert preferences.location == "Indiranagar"
    assert preferences.cuisines == ["Italian"]
    assert preferences.budget == "medium"
    assert preferences.top_k == 3
    assert preferences.extra_preferences == "  quiet place "
    assert [restaurant.id for restaurant in candidates] == ["ind-1", "ind-3", "ind-2", "ind-4", "ind-5"]


def test_every_request_asks_groq_to_decide_the_filters(store, keyed):
    llm = FakeLLM(picks("ind-1"))
    ask(store, keyed, llm)
    assert len(llm.interpret_calls) == 1
    assert "counts" in llm.interpret_calls[0][1]
    assert llm.calls[0][0].mapped is not None


def test_hard_filters_are_not_relaxed_when_the_llm_asks_to_drop_them(store, keyed):
    llm = FakeLLM(
        picks("btm-1"),
        decision=FilterDecision(
            cuisines=[],
            budget=None,
            min_rating=3.0,
            relaxations=["dropped_cuisine"],
        ),
    )
    response = ask(store, keyed, llm, location="BTM", budget="low", cuisines=["Mexican"], min_rating=4.8)
    assert response.recommendations == []
    assert response.relaxations == []
    assert response.meta["message"]
    assert llm.calls == []


@pytest.mark.parametrize(
    "error",
    [
        LLMError("timeout"),
        LLMError("rate_limit"),
        LLMParseError("invalid JSON"),
        LLMError("auth"),
        LLMError("network"),
    ],
)
def test_llm_failures_fall_back_to_the_rule_based_ranking(store, keyed, error):
    """Every LLM failure still answers 200 with the fallback ranking (LLM-03, LLM-05, LLM-12)."""
    response = ask(store, keyed, FakeLLM(error))
    assert response.source == "fallback"
    assert response.summary is None
    assert ids(response) == FALLBACK_ORDER
    assert response.meta["model"] is None


def test_unexpected_exception_from_the_client_still_falls_back(store, keyed):
    """A bug or new SDK error type must not turn into a 500 (LLM-11)."""
    response = ask(store, keyed, FakeLLM(RuntimeError("surprise")))
    assert response.source == "fallback"
    assert ids(response) == FALLBACK_ORDER


def test_empty_llm_list_falls_back(store, keyed):
    """Zero usable picks is a failed LLM step, not an empty answer (PAR-04)."""
    response = ask(store, keyed, FakeLLM(LLMResult(recommendations=[], summary="Nothing.")))
    assert response.source == "fallback"
    assert ids(response) == FALLBACK_ORDER


def test_short_llm_list_is_backfilled_and_stays_llm(store, keyed):
    """One pick for top_k 3 is still an AI answer, topped up from the pre-sort (PAR-16)."""
    response = ask(store, keyed, FakeLLM(picks("ind-5")))
    assert response.source == "llm"
    assert ids(response) == ["ind-5", "ind-1", "ind-3"]


def test_empty_api_key_never_calls_the_llm(store, settings):
    """With no key the app must not even try Groq (CFG-08)."""
    llm = FakeLLM(picks("ind-1"))
    response = ask(store, settings, llm)
    assert llm.calls == []
    assert response.source == "fallback"


def test_no_client_uses_the_fallback(store, keyed):
    assert ask(store, keyed, None).source == "fallback"


def test_no_candidates_skips_the_llm(store, keyed):
    """Zero candidates is answered with a message, and the LLM is not asked (FLT-12, C-10)."""
    llm = FakeLLM(picks("ind-1"))
    response, trace = ask(store, keyed, llm, trace=True, location="Old Airport Road", budget="low", cuisines=[])
    assert response.recommendations == []
    assert response.meta["message"]
    assert llm.calls == []
    assert trace.llm_called is False


def test_trace_records_llm_details_for_evals(store, keyed):
    """Evals need raw ids, dropped ids, usage, attempts, and the LLM step time (eval.md 7.4)."""
    llm = FakeLLM(picks("ghost-1", "ind-1"))
    response, trace = ask(store, keyed, llm, trace=True)
    assert trace.llm_called is True
    assert trace.llm_error is None
    assert trace.raw_ids == ["ghost-1", "ind-1"]
    assert trace.dropped_ids == ["ghost-1"]
    assert trace.backfilled == 2
    assert trace.stats.raw_output == "raw text"
    assert trace.stats.reasoning_tokens == 50
    assert trace.stats.attempts == 1
    assert trace.timings_ms["llm_ms"] >= 0
    assert response.summary is None


def test_trace_records_the_failure_kind(store, keyed):
    _, trace = ask(store, keyed, FakeLLM(LLMParseError("bad")), trace=True)
    assert trace.llm_called is True
    assert trace.llm_error == "invalid_json"


def test_trace_is_never_part_of_the_response(store, keyed):
    response = ask(store, keyed, FakeLLM(picks("ind-1")))
    body = response.model_dump_json()
    assert "raw text" not in body
    assert "reasoning" not in body


def test_failure_is_logged_with_kind_and_model_but_not_the_key(store, caplog):
    """Logs must say what broke and which model, and never print the key (CFG-11, SEC-01)."""
    settings = make_settings(groq_api_key="sk-secret-value")
    with caplog.at_level(logging.WARNING):
        ask(store, settings, FakeLLM(LLMError("not_found", "Groq model fake-model was not found (404)")))
    assert "not_found" in caplog.text
    assert "fake-model" in caplog.text
    assert "sk-secret-value" not in caplog.text


def test_invalid_json_from_groq_is_not_retried_end_to_end(store, keyed):
    """Through the real client: one call, then fallback (GAP-04)."""
    clock = Clock()
    sdk = FakeSDK(clock, completion(content="{oops"), completion())
    client = GroqLLMClient.from_settings(keyed, sdk=sdk, clock=clock, sleep=clock.sleep)
    assert ask(store, keyed, client).source == "fallback"
    assert len(sdk.calls) == 1


def test_timeout_from_groq_is_not_retried_end_to_end(store, keyed):
    clock = Clock()
    sdk = FakeSDK(clock, groq.APITimeoutError(request=REQUEST), completion())
    client = GroqLLMClient.from_settings(keyed, sdk=sdk, clock=clock, sleep=clock.sleep)
    assert ask(store, keyed, client).source == "fallback"
    assert len(sdk.calls) == 1


def test_429_retry_that_would_pass_the_deadline_is_skipped_end_to_end(store, keyed):
    """A 429 asking for a 10-second wait goes straight to fallback (LLM-10)."""
    clock = Clock()
    sdk = FakeSDK(clock, status_error(groq.RateLimitError, 429, headers={"retry-after": "10"}), completion())
    client = GroqLLMClient.from_settings(keyed, sdk=sdk, clock=clock, sleep=clock.sleep)
    assert ask(store, keyed, client).source == "fallback"
    assert len(sdk.calls) == 1
    assert clock.sleeps == []
