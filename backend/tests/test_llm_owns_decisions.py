"""Filter and ranking choices belong to the LLM when Groq is available."""

import pytest

import app.services.recommender as recommender_mod
from app.models.llm import FilterDecision, LLMError
from app.models.recommendation import RecommendationRequest
from app.services.filter import filter_candidates
from app.services.recommender import recommend
from tests.conftest import make_settings
from tests.test_recommender import FakeLLM, ask, ids, picks


@pytest.fixture
def keyed():
    return make_settings(groq_api_key="test-key")


def _spy(monkeypatch, name):
    calls = []
    original = getattr(recommender_mod, name)

    def wrapper(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(recommender_mod, name, wrapper)
    return calls


class InterpretFailLLM(FakeLLM):
    def interpret(self, request, vocab, stats=None):
        self.interpret_calls.append((request, vocab))
        raise LLMError("timeout", "interpret timed out")


def test_successful_llm_path_never_runs_code_filter_or_fallback_rank(store, keyed, monkeypatch):
    filter_calls = _spy(monkeypatch, "filter_candidates")
    fallback_calls = _spy(monkeypatch, "rank_fallback")
    phrase_calls = _spy(monkeypatch, "rank_by_soft_prefs")
    llm = FakeLLM(
        picks("ind-5", "ind-4", "ind-2"),
        decision=FilterDecision(cuisines=["Italian"], budget="medium", min_rating=4.0),
    )
    response = ask(store, keyed, llm)
    assert response.source == "llm"
    assert ids(response) == ["ind-5", "ind-4", "ind-2"]
    assert filter_calls == []
    assert fallback_calls == []
    assert phrase_calls == []
    assert len(llm.interpret_calls) == 1
    assert len(llm.calls) == 1


def test_llm_cannot_relax_hard_filters_when_nothing_matches(store, keyed):
    code = filter_candidates(
        store.frame,
        location="BTM",
        budget="low",
        cuisines=["Mexican"],
        min_rating=4.8,
        top_k=5,
        max_candidates=25,
    )
    assert code.candidate_ids == []
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
    assert llm.calls == []


def test_llm_cannot_drop_cuisine_when_matches_still_exist(store, keyed):
    """A short Japanese list must stay Japanese, not become every Whitefield place."""
    llm = FakeLLM(
        picks("wf-1"),
        decision=FilterDecision(
            cuisines=[],
            budget="medium",
            min_rating=3.0,
            relaxations=["dropped_cuisine"],
        ),
    )
    request = RecommendationRequest(
        location="Whitefield", budget="medium", cuisines=["Japanese"], min_rating=3.0, top_k=5
    )
    response = recommend(request, store, keyed, llm=llm)
    assert ids(response) == ["wf-1"]
    assert not any("cuisine" in item.casefold() for item in response.relaxations)
    assert llm.calls[0][0].mapped.cuisines == ["Japanese"]


def test_llm_maps_extras_without_the_phrase_list(store, keyed, monkeypatch):
    monkeypatch.setattr(
        recommender_mod,
        "rank_by_soft_prefs",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("phrase map used on Groq path")),
    )
    llm = FakeLLM(
        picks("ind-1", "ind-3", "ind-2"),
        decision=FilterDecision(
            cuisines=["Italian"],
            budget="medium",
            min_rating=4.0,
            rest_types=["Casual Dining"],
        ),
    )
    response = ask(store, keyed, llm, extra_preferences="cozy dining")
    assert response.source == "llm"
    assert llm.calls[0][0].mapped.rest_types == ["Casual Dining"]
    assert llm.calls[0][1][0].id == "ind-1"


def test_no_api_key_uses_only_code_filter_and_fallback_rank(store, settings, monkeypatch):
    filter_calls = _spy(monkeypatch, "filter_candidates")
    fallback_calls = _spy(monkeypatch, "rank_fallback")
    llm = FakeLLM(picks("ind-5", "ind-1", "ind-4"))
    response = ask(store, settings, llm)
    assert response.source == "fallback"
    assert llm.interpret_calls == []
    assert llm.calls == []
    assert len(filter_calls) == 1
    assert len(fallback_calls) == 1
    assert ids(response) == ["ind-1", "ind-3", "ind-2"]


def test_no_client_uses_code_filter(store, keyed, monkeypatch):
    filter_calls = _spy(monkeypatch, "filter_candidates")
    response = ask(store, keyed, None)
    assert response.source == "fallback"
    assert len(filter_calls) == 1


def test_interpret_failure_falls_back_to_code_filter(store, keyed, monkeypatch):
    filter_calls = _spy(monkeypatch, "filter_candidates")
    llm = InterpretFailLLM(picks("ind-5"))
    response = ask(store, keyed, llm)
    assert response.source == "fallback"
    assert llm.calls == []
    assert len(filter_calls) == 1
    assert ids(response) == ["ind-1", "ind-3", "ind-2"]


def test_whitefield_japanese_asks_the_llm_for_the_filter(store, keyed, monkeypatch):
    filter_calls = _spy(monkeypatch, "filter_candidates")
    llm = FakeLLM(
        picks("wf-1"),
        decision=FilterDecision(cuisines=["Japanese"], budget="medium", min_rating=3.0),
    )
    request = RecommendationRequest(
        location="Whitefield", budget="medium", cuisines=["Japanese"], min_rating=3.0, top_k=5
    )
    response = recommend(request, store, keyed, llm=llm)
    assert response.source == "llm"
    assert ids(response) == ["wf-1"]
    assert filter_calls == []
    assert len(llm.interpret_calls) == 1


def test_unmapped_extras_do_not_invent_filters(store, keyed, monkeypatch):
    filter_calls = _spy(monkeypatch, "filter_candidates")
    llm = FakeLLM(
        picks("ind-1", "ind-3", "ind-2"),
        decision=FilterDecision(
            cuisines=["Italian"],
            budget="medium",
            min_rating=4.0,
            unmapped=["parking", "rooftop"],
        ),
    )
    response = ask(store, keyed, llm, extra_preferences="need parking and rooftop seating")
    assert response.source == "llm"
    assert llm.calls[0][0].mapped.unmapped == ["parking", "rooftop"]
    assert llm.calls[0][0].mapped.rest_types == []
    assert filter_calls == []
