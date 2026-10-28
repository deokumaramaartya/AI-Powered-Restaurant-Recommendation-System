import pytest
from fastapi.testclient import TestClient

from app.api.routes import get_llm_client
from app.main import create_app
from app.models.llm import LLMError
from app.models.recommendation import RecommendationRequest
from app.services.recommender import ResponseCache, cache_key, recommend
from tests.conftest import make_settings, make_store
from tests.test_recommender import G01, FakeLLM, picks

ITALIAN = ("ind-1", "ind-3", "ind-2")


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def keyed():
    return make_settings(groq_api_key="test-key")


def ask(store, settings, llm, cache, use_cache=True, **overrides):
    request = RecommendationRequest(**{**G01, **overrides})
    return recommend(request, store, settings, llm=llm, cache=cache, use_cache=use_cache)


def test_second_identical_request_is_served_from_cache(store, keyed):
    """CCH-01: a repeat must not call the LLM again."""
    llm, cache = FakeLLM(picks(*ITALIAN)), ResponseCache()
    first = ask(store, keyed, llm, cache)
    second = ask(store, keyed, llm, cache)
    assert len(llm.calls) == 1
    assert second.source == "llm"
    assert second.recommendations == first.recommendations
    assert second.summary == first.summary


def test_different_requests_call_the_llm_again(store, keyed):
    llm, cache = FakeLLM(picks(*ITALIAN)), ResponseCache()
    ask(store, keyed, llm, cache)
    ask(store, keyed, llm, cache, min_rating=3.5)
    assert len(llm.calls) == 2


def test_cached_response_reports_cached_and_its_own_latency(store, keyed):
    """CCH-10: meta says whether the cache answered, and latency is the time actually taken."""
    llm, cache = FakeLLM(picks(*ITALIAN)), ResponseCache()
    first = ask(store, keyed, llm, cache)
    second = ask(store, keyed, llm, cache)
    assert first.meta["cached"] is False
    assert second.meta["cached"] is True
    assert isinstance(second.meta["latency_ms"], int)
    assert second.meta["model"] == "fake-model"


def test_fallback_response_is_not_cached(store, keyed):
    """CCH-05: after Groq recovers, the same query must reach the LLM and return source llm."""
    llm, cache = FakeLLM(LLMError("rate_limit")), ResponseCache()
    assert ask(store, keyed, llm, cache).source == "fallback"
    llm.outcome = picks(*ITALIAN)
    recovered = ask(store, keyed, llm, cache)
    assert len(llm.calls) == 2
    assert recovered.source == "llm"
    assert recovered.meta["cached"] is False


def test_empty_result_is_not_cached(store, keyed):
    cache = ResponseCache()
    ask(store, keyed, FakeLLM(picks(*ITALIAN)), cache, location="Old Airport Road", budget=None, cuisines=[])
    assert len(cache) == 0


def test_use_cache_false_bypasses_lookup_and_store(store, keyed):
    """Evals pass use_cache=False so every run measures a real LLM call."""
    llm, cache = FakeLLM(picks(*ITALIAN)), ResponseCache()
    ask(store, keyed, llm, cache, use_cache=False)
    ask(store, keyed, llm, cache, use_cache=False)
    assert len(llm.calls) == 2
    assert len(cache) == 0


def test_requests_differing_in_cuisine_order_casing_or_spaces_share_a_key():
    """CCH-02 and CCH-04."""
    base = RecommendationRequest(
        location="Indiranagar", cuisines=["Italian", "Cafe"], extra_preferences="Quiet  place"
    )
    variant = RecommendationRequest(
        location="  indiranagar ", cuisines=["cafe", " ITALIAN"], extra_preferences="  quiet place "
    )
    assert cache_key(base) == cache_key(variant)


def test_other_text_differences_give_a_different_key():
    """CCH-04: only casing and whitespace are ignored."""
    quiet = RecommendationRequest(location="Indiranagar", extra_preferences="quiet place")
    loud = RecommendationRequest(location="Indiranagar", extra_preferences="lively place")
    assert cache_key(quiet) != cache_key(loud)


@pytest.mark.parametrize(
    "change",
    [{"top_k": 3}, {"budget": "low"}, {"min_rating": 4.0}, {"cuisines": ["Italian"]}, {"location": "BTM"}],
)
def test_each_request_field_is_part_of_the_key(change):
    """CCH-03: top_k and every filter change the key."""
    base = {"location": "Indiranagar"}
    assert cache_key(RecommendationRequest(**base)) != cache_key(RecommendationRequest(**{**base, **change}))


def test_entries_expire_after_the_ttl(store, keyed):
    """CCH-06."""
    clock = Clock()
    llm, cache = FakeLLM(picks(*ITALIAN)), ResponseCache(ttl_seconds=3600, clock=clock)
    ask(store, keyed, llm, cache)
    clock.now += 3599
    ask(store, keyed, llm, cache)
    assert len(llm.calls) == 1
    clock.now += 2
    ask(store, keyed, llm, cache)
    assert len(llm.calls) == 2


def test_least_recently_used_entry_is_evicted_at_the_size_limit(store, keyed):
    """CCH-08."""
    llm, cache = FakeLLM(picks(*ITALIAN)), ResponseCache(max_entries=2)
    ask(store, keyed, llm, cache, top_k=1)
    ask(store, keyed, llm, cache, top_k=2)
    ask(store, keyed, llm, cache, top_k=1)
    ask(store, keyed, llm, cache, top_k=3)
    assert len(cache) == 2
    calls = len(llm.calls)
    ask(store, keyed, llm, cache, top_k=1)
    assert len(llm.calls) == calls
    ask(store, keyed, llm, cache, top_k=2)
    assert len(llm.calls) == calls + 1


def test_cache_returns_copies_that_cannot_change_the_entry(store, keyed):
    """CCH-11."""
    llm, cache = FakeLLM(picks(*ITALIAN)), ResponseCache()
    first = ask(store, keyed, llm, cache)
    first.recommendations[0].explanation = "changed"
    first.meta["model"] = "changed"
    second = ask(store, keyed, llm, cache)
    second.recommendations.clear()
    third = ask(store, keyed, llm, cache)
    assert third.recommendations[0].explanation == "Pick 1 fits well."
    assert third.meta["model"] == "fake-model"


def test_unknown_location_is_still_rejected_with_a_warm_cache(store, keyed):
    """Validation runs before the cache lookup."""
    from app.services.recommender import InvalidPreferences

    cache = ResponseCache()
    ask(store, keyed, FakeLLM(picks(*ITALIAN)), cache)
    with pytest.raises(InvalidPreferences):
        ask(store, keyed, FakeLLM(picks(*ITALIAN)), cache, location="Delhi")


def test_api_serves_a_repeated_request_from_the_app_cache():
    """The running app keeps one cache, so the second POST skips the LLM."""
    settings = make_settings(groq_api_key="test-key")
    fake = FakeLLM(picks(*ITALIAN))
    app = create_app(settings=settings, store=make_store(settings))
    app.dependency_overrides[get_llm_client] = lambda: fake
    with TestClient(app) as client:
        first = client.post("/api/recommendations", json=G01).json()
        second = client.post("/api/recommendations", json=G01).json()
    assert len(fake.calls) == 1
    assert (first["meta"]["cached"], second["meta"]["cached"]) == (False, True)
    assert second["recommendations"] == first["recommendations"]
