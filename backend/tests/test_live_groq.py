import time

import pytest

import app.services.recommender as recommender_mod
from app.core.config import get_settings
from app.models.recommendation import RecommendationRequest
from app.services.filter import filter_candidates
from app.services.llm_groq import GroqLLMClient
from app.services.recommender import recommend
from app.services.store import load_store
from tests.conftest import make_settings, make_store

pytestmark = pytest.mark.live
THROTTLE_SECONDS = 20


class CountingLLM:
    """Wraps Groq so tests can see that interpret and rank both ran."""

    def __init__(self, inner: GroqLLMClient):
        self.inner = inner
        self.model = inner.model
        self.interprets = 0
        self.ranks = 0

    def interpret(self, request, vocab, stats=None):
        self.interprets += 1
        return self.inner.interpret(request, vocab, stats)

    def rank(self, preferences, candidates, stats=None):
        self.ranks += 1
        return self.inner.rank(preferences, candidates, stats)


@pytest.fixture
def live_settings():
    key = get_settings().groq_api_key
    if not key:
        pytest.skip("GROQ_API_KEY is empty in backend/.env")
    real = get_settings()
    return make_settings(
        groq_api_key=key,
        groq_model=real.groq_model,
        llm_temperature=real.llm_temperature,
        llm_reasoning_effort=real.llm_reasoning_effort,
        llm_max_completion_tokens=real.llm_max_completion_tokens,
    )


def test_groq_ranks_fixture_candidates_with_grounded_output(live_settings):
    """One real call: Groq must return only candidates, a summary, and no reasoning text."""
    store = make_store(live_settings)
    llm = GroqLLMClient.from_settings(live_settings)
    request = RecommendationRequest(
        location="Indiranagar", budget="medium", cuisines=["Italian"], min_rating=4.0, top_k=3,
        extra_preferences="quiet place for a date",
    )
    response, trace = recommend(request, store, live_settings, llm=llm, trace=True)
    assert trace.llm_error is None, trace.llm_error
    assert response.source == "llm"
    assert response.meta["model"] == live_settings.groq_model
    assert response.summary
    assert len(response.recommendations) == 3
    assert {item.restaurant.id for item in response.recommendations} <= set(trace.candidate_ids)
    assert all(item.explanation.strip() for item in response.recommendations)
    exposed_keys = list(response.model_dump()) + list(response.meta)
    assert not [key for key in exposed_keys if "reasoning" in key]


@pytest.fixture
def live_store(live_settings):
    store = load_store(live_settings.data_path, live_settings.budget_low_max, live_settings.budget_medium_max)
    if store is None:
        pytest.skip("restaurant parquet is not loaded")
    return store


LIVE_SCENARIOS = [
    {
        "name": "asian-cozy",
        "request": RecommendationRequest(
            location="Indiranagar",
            cuisines=["Asian"],
            extra_preferences="cozy dining",
            min_rating=3.5,
            top_k=5,
        ),
    },
    {
        "name": "btm-mexican-strict",
        "request": RecommendationRequest(
            location="BTM",
            budget="low",
            cuisines=["Mexican"],
            min_rating=4.8,
            top_k=5,
        ),
    },
    {
        "name": "hsr-japanese-high",
        "request": RecommendationRequest(
            location="HSR",
            budget="high",
            cuisines=["Japanese"],
            min_rating=4.5,
            top_k=5,
        ),
    },
    {
        "name": "parking-unmapped",
        "request": RecommendationRequest(
            location="Indiranagar",
            extra_preferences="need parking and rooftop seating",
            min_rating=4.0,
            top_k=5,
        ),
    },
    {
        "name": "conflicting-cheap-high",
        "request": RecommendationRequest(
            location="Indiranagar",
            budget="high",
            min_rating=4.0,
            extra_preferences="something cheap, under 300 for two",
            top_k=5,
        ),
    },
]


def test_live_groq_owns_filter_and_rank_across_scenarios(live_settings, live_store, monkeypatch):
    """Real Groq must decide filters and ranking; the code filter must not run."""
    filter_calls = []
    original_filter = recommender_mod.filter_candidates

    def wrapped_filter(*args, **kwargs):
        filter_calls.append(kwargs)
        return original_filter(*args, **kwargs)

    monkeypatch.setattr(recommender_mod, "filter_candidates", wrapped_filter)
    llm = CountingLLM(GroqLLMClient.from_settings(live_settings))
    results = []
    time.sleep(THROTTLE_SECONDS)
    for index, scenario in enumerate(LIVE_SCENARIOS):
        if index:
            time.sleep(THROTTLE_SECONDS)
        request = scenario["request"]
        response, trace = recommend(
            request, live_store, live_settings, llm=llm, use_cache=False, trace=True
        )
        results.append((scenario["name"], response, trace, llm.interprets, llm.ranks))
        assert llm.interprets == index + 1, scenario["name"]
        assert filter_calls == [], scenario["name"]
        assert response.relaxations == [], scenario["name"]
        if not response.recommendations:
            assert response.source == "fallback", scenario["name"]
            assert response.meta["message"]
            continue
        assert response.source == "llm", (scenario["name"], response.source, trace.llm_error)
        assert trace.llm_error is None, (scenario["name"], trace.llm_error)
        assert response.summary
        locations = {item.restaurant.location for item in response.recommendations}
        assert locations == {trace.location}, scenario["name"]
        if request.cuisines:
            wanted = {item.casefold() for item in request.cuisines}
            for item in response.recommendations:
                assert wanted & {cuisine.casefold() for cuisine in item.restaurant.cuisines}, (
                    scenario["name"],
                    item.restaurant.name,
                    item.restaurant.cuisines,
                )
    assert [name for name, *_ in results] == [scenario["name"] for scenario in LIVE_SCENARIOS]


def test_live_without_groq_key_uses_code_filter_only(live_store, monkeypatch):
    filter_calls = []
    original_filter = recommender_mod.filter_candidates

    def wrapped_filter(*args, **kwargs):
        filter_calls.append(1)
        return original_filter(*args, **kwargs)

    monkeypatch.setattr(recommender_mod, "filter_candidates", wrapped_filter)
    settings = make_settings(
        groq_api_key="",
        data_path=get_settings().data_path,
        budget_low_max=get_settings().budget_low_max,
        budget_medium_max=get_settings().budget_medium_max,
    )
    llm = CountingLLM(GroqLLMClient.from_settings(make_settings(groq_api_key="unused")))
    request = RecommendationRequest(location="Indiranagar", cuisines=["Asian"], min_rating=3.5, top_k=5)
    response, trace = recommend(request, live_store, settings, llm=llm, use_cache=False, trace=True)
    assert response.source == "fallback"
    assert llm.interprets == 0
    assert llm.ranks == 0
    assert filter_calls == [1]
    assert trace.llm_called is False
    code = filter_candidates(
        live_store.frame,
        location=trace.location,
        budget=request.budget,
        cuisines=list(request.cuisines),
        min_rating=request.min_rating,
        top_k=request.top_k,
        max_candidates=settings.max_candidates,
    )
    assert [item.restaurant.id for item in response.recommendations] == code.candidate_ids[: request.top_k]
    assert response.relaxations == code.relaxations
