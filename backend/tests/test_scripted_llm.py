import pytest

from app.models.recommendation import RecommendationRequest
from app.services.recommender import recommend
from evals.checks import context_from, run_checks
from evals.fixtures.scripted_llm import FOREIGN_ID, SCRIPTS, ScriptedLLMClient
from tests.conftest import make_settings

G01 = {"location": "Indiranagar", "budget": "medium", "cuisines": ["Italian"], "min_rating": 4.0, "top_k": 3}


def run(store, script, **overrides):
    settings = make_settings(groq_api_key="offline-scripted")
    request = RecommendationRequest(**{**G01, **overrides})
    llm = ScriptedLLMClient(script)
    response, trace = recommend(request, store, settings, llm=llm, trace=True)
    return response, trace, llm


def test_grounded_script_passes_the_grounding_and_summary_checks(store):
    """The default script must look like a good LLM answer, so offline runs exercise the LLM path."""
    response, trace, _ = run(store, None)
    assert response.source == "llm"
    assert len(response.recommendations) == 3
    ctx = context_from({"id": "T"}, RecommendationRequest(**G01), {}, response, trace, store, [])
    for result in run_checks(ctx, ["C-03", "C-07", "C-08", "C-09"]):
        assert result.status == "pass", (result.id, result.detail)


def test_grounded_script_asks_for_no_more_than_the_candidates(store):
    """top_k 10 keeps the five medium Italian matches at 4.0 instead of dropping cuisine (PRM-13)."""
    response, trace, _ = run(store, None, top_k=10)
    assert len(trace.candidate_ids) == 5
    assert trace.stats.requested_count == 5
    assert len(response.recommendations) == 5


def test_overflow_script_is_cut_to_top_k(store):
    """An injected 'list 50 restaurants' that the model obeys must still return top_k (PAR-05)."""
    response, trace, _ = run(store, "overflow", top_k=2)
    assert len(trace.raw_ids) == 5
    assert len(response.recommendations) == 2


def test_foreign_id_script_is_dropped_and_clears_the_summary(store):
    """A restaurant from outside the candidates must never be shown (PRM-03, PAR-17)."""
    response, trace, _ = run(store, "foreign_id")
    assert trace.dropped_ids == [FOREIGN_ID]
    assert FOREIGN_ID not in [item.restaurant.id for item in response.recommendations]
    assert response.source == "llm"
    assert response.summary is None


def test_messy_script_is_cleaned_up_by_the_parser(store):
    """Duplicates, odd casing, gaps in ranks, and a short list still give a clean top_k list."""
    response, _, _ = run(store, "messy", top_k=4)
    ids = [item.restaurant.id for item in response.recommendations]
    assert response.source == "llm"
    assert len(ids) == len(set(ids)) == 4
    assert [item.rank for item in response.recommendations] == [1, 2, 3, 4]


@pytest.mark.parametrize(
    ("script", "kind"),
    [("invalid_json", "invalid_json"), ("empty", "invalid_json"), ("timeout", "timeout"), ("rate_limit", "rate_limit")],
)
def test_failure_scripts_fall_back(store, script, kind):
    """Each forced failure must end in the fallback ranking with a full list (M-17)."""
    response, trace, _ = run(store, script)
    assert response.source == "fallback"
    assert len(response.recommendations) == 3
    assert trace.llm_error == kind


def test_unknown_script_is_rejected():
    with pytest.raises(ValueError, match="nonsense"):
        ScriptedLLMClient("nonsense")


def test_every_documented_script_is_known():
    assert {"grounded", "overflow", "foreign_id", "messy", "invalid_json", "empty", "timeout", "rate_limit"} <= SCRIPTS
