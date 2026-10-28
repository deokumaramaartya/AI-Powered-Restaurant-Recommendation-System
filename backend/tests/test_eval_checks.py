import pytest

from evals.checks import context_from, run_checks
from app.models.recommendation import RecommendationRequest
from app.services.recommender import recommend


def build(store, settings, body, expect=None, secrets=()):
    request = RecommendationRequest.model_validate(body)
    response, trace = recommend(request, store, settings, trace=True)
    return context_from({"id": "T"}, request, expect or {}, response, trace, store, list(secrets))


def status(ctx, check_id):
    return run_checks(ctx, [check_id])[0].status


def test_c04_fails_when_a_result_is_from_another_locality(store, settings):
    """A filter that leaked localities must be caught."""
    ctx = build(store, settings, {"location": "BTM", "top_k": 2})
    ctx.response.recommendations[0].restaurant = store.get("ind-1")
    assert status(ctx, "C-04") == "fail"


def test_c04_rejects_a_result_below_the_requested_rating(store, settings):
    ctx = build(store, settings, {"location": "Indiranagar", "min_rating": 4.5, "top_k": 3})
    assert status(ctx, "C-04") == "pass"
    ctx.response.recommendations[0].restaurant = store.get("ind-6")
    assert status(ctx, "C-04") == "fail"


def test_c05_fails_on_a_different_order(store, settings):
    """Relaxations must match the frozen list in order."""
    body = {"location": "BTM", "budget": "low", "cuisines": ["Mexican"], "min_rating": 4.8}
    expected = [
        "Removed cuisine filter (Mexican)",
        "Lowered minimum rating from 4.8 to 4.3",
        "Widened budget from low to low and medium",
    ]
    assert status(build(store, settings, body, {"relaxations": expected}), "C-05") == "fail"


def test_c13_fails_when_a_displayed_field_differs_from_the_store(store, settings):
    """Displayed facts must equal the store record."""
    ctx = build(store, settings, {"location": "BTM", "top_k": 1})
    ctx.body["recommendations"][0]["restaurant"]["rating"] = 4.9
    assert status(ctx, "C-13") == "fail"


def test_c03_fails_on_a_restaurant_outside_the_candidates(store, settings):
    """An invented restaurant must count against grounding."""
    ctx = build(store, settings, {"location": "BTM", "top_k": 1})
    ctx.candidate_ids = ["btm-1"]
    ctx.response.recommendations[0].restaurant = store.get("ind-1")
    result = run_checks(ctx, ["C-03"])[0]
    assert result.status == "fail"
    assert (result.ok, result.total) == (0, 1)


def test_c11_fails_when_the_response_contains_a_secret(store, settings):
    """A leaked API key anywhere in the body must fail."""
    ctx = build(store, settings, {"location": "BTM", "top_k": 1}, secrets=["gsk_secret"])
    ctx.body["meta"]["debug"] = "key=gsk_secret"
    assert status(ctx, "C-11") == "fail"


TOSCANO = {"location": "Indiranagar", "budget": "medium", "cuisines": ["Italian"], "min_rating": 4.0, "top_k": 1}


def explained(store, settings, text):
    ctx = build(store, settings, TOSCANO)
    assert ctx.response.recommendations[0].restaurant.id == "ind-1"  # rated 4.5, 2,100 votes, 1,100 for two
    ctx.response.recommendations[0].explanation = text
    return run_checks(ctx, ["C-07"])[0]


@pytest.mark.parametrize(
    "text",
    [
        "Casual Dining rated 4.5 from 2,100 votes, serving Italian for about ₹1,100 for two.",
        "A 4.5-rated spot with over 2,000 votes and Rs. 1100 for two.",
        "Rating of 4.5/5, nearly 2,100 votes, and under ₹1,200 for two.",
        "With 2100+ votes and a 4.5 rating it costs INR 1,100 for two.",
    ],
)
def test_c07_accepts_numbers_that_match_the_data(store, settings, text):
    """Exact figures and honest phrasing such as 'over 2,000 votes' are accurate (M-06)."""
    result = explained(store, settings, text)
    assert result.status == "pass", result.detail
    assert result.total >= 2


@pytest.mark.parametrize(
    ("text", "field"),
    [
        ("Rated 4.8 with 2,100 votes.", "rating"),
        ("Rated 4.5 from 3,000 votes.", "votes"),
        ("Rated 4.5 with over 3,000 votes.", "votes"),
        ("Rated 4.5 and about ₹900 for two.", "cost"),
    ],
)
def test_c07_fails_on_a_number_the_data_does_not_support(store, settings, text, field):
    """An explanation quoting the wrong rating, votes, or cost misleads the user (M-06)."""
    result = explained(store, settings, text)
    assert result.status == "fail"
    assert field in result.detail


def test_c07_skips_numbers_the_user_typed(store, settings):
    """Quoting the user's own 'under 300' back is not a claim about the restaurant (seen in live tuning)."""
    ctx = build(store, settings, {**TOSCANO, "extra_preferences": "something cheap, under 300 for two"})
    ctx.response.recommendations[0].explanation = (
        "It costs ₹1,100 for two and is rated 4.5, though it exceeds the under\u2011₹300 wish."
    )
    result = run_checks(ctx, ["C-07"])[0]
    assert result.status == "pass", result.detail
    assert result.total == 2
    assert "user" in result.detail


def test_c07_still_fails_a_user_number_stated_as_the_restaurant_cost(store, settings):
    """Only an echo with a qualifier ('under ₹300') is the user's figure; a plain cost claim is checked."""
    ctx = build(store, settings, {**TOSCANO, "extra_preferences": "under 300 for two"})
    ctx.response.recommendations[0].explanation = "Only ₹300 for two and rated 4.5."
    assert run_checks(ctx, ["C-07"])[0].status == "fail"


def test_c07_skips_numbers_it_cannot_attribute(store, settings):
    """'5th Block' or 'top 3' are not facts about the restaurant; they are listed, not failed."""
    result = explained(store, settings, "A top 3 pick for the 5th Block crowd.")
    assert result.status == "pass"
    assert result.total == 0
    assert "skipped" in result.detail


def summarized(store, settings, summary, source="llm"):
    ctx = build(store, settings, TOSCANO)
    ctx.response.source = source
    ctx.response.summary = summary
    return run_checks(ctx, ["C-09"])[0]


def test_c09_passes_when_the_summary_names_only_returned_restaurants(store, settings):
    assert summarized(store, settings, "Toscano is the clear pick for Italian.").status == "pass"


def test_c09_fails_when_the_summary_names_a_candidate_that_was_not_returned(store, settings):
    """Pasta Street was a candidate but not returned; naming it would confuse the user (M-13)."""
    result = summarized(store, settings, "Toscano beats Pasta Street on rating.")
    assert result.status == "fail"
    assert "Pasta Street" in result.detail


def test_c09_fails_when_an_llm_response_has_no_summary(store, settings):
    assert summarized(store, settings, None).status == "fail"


def test_c09_is_skipped_for_fallback_responses(store, settings):
    assert summarized(store, settings, None, source="fallback").status == "skip"


@pytest.mark.parametrize(("llm_ms", "expected"), [(8400, "pass"), (8600, "fail")])
def test_c12_allows_the_deadline_plus_half_a_second(store, settings, llm_ms, expected):
    """The LLM step must end within LLM_DEADLINE_SECONDS plus 0.5 s of overhead (M-20)."""
    ctx = build(store, settings, TOSCANO)
    ctx.llm_called = True
    ctx.llm_ms = llm_ms
    ctx.deadline_seconds = 8
    assert status(ctx, "C-12") == expected


def test_c12_is_skipped_when_the_llm_was_not_called(store, settings):
    assert status(build(store, settings, TOSCANO), "C-12") == "skip"


def test_c10_fails_when_results_come_back_for_an_expected_empty_query(store, settings):
    """The empty-result check must reject a non-empty answer."""
    ctx = build(store, settings, {"location": "BTM", "top_k": 1})
    assert status(ctx, "C-10") == "fail"
    empty = build(store, settings, {"location": "Old Airport Road"})
    assert status(empty, "C-10") == "pass"
