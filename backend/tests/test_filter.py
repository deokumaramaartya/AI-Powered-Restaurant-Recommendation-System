import pandas as pd

from app.models.restaurant import BudgetTier
from app.services.filter import filter_candidates
from app.services.store import RestaurantStore
from evals.fixtures.restaurants import fixture_frame


def run(store, location, *, budget=None, cuisines=(), min_rating=3.5, top_k=5, max_candidates=25):
    return filter_candidates(
        store.frame,
        location=location,
        budget=budget,
        cuisines=list(cuisines),
        min_rating=min_rating,
        top_k=top_k,
        max_candidates=max_candidates,
    )


def test_location_only_keeps_that_locality_rated_at_least_the_minimum(store):
    """Leaking another locality, or a rating below 3.5, would break FLT-01."""
    result = run(store, "Indiranagar", top_k=1)
    assert set(result.candidate_ids) == {f"ind-{n}" for n in range(1, 9)}
    assert result.relaxations == []


def test_budget_filter_uses_inclusive_tier_bounds(store):
    """501 and 1200 are medium; 500 and 1201 are not."""
    result = run(store, "Jayanagar", budget=BudgetTier.medium, top_k=2)
    assert set(result.candidate_ids) == {"jay-2", "jay-3"}
    assert result.relaxations == []


def test_cuisine_filter_matches_any_requested_cuisine_ignoring_case(store):
    """An all-of match, or a case-sensitive match, would drop Toscano or Burma Burma."""
    result = run(store, "Indiranagar", cuisines=["burmese", "Continental"], top_k=1)
    assert set(result.candidate_ids) == {"ind-1", "ind-7", "ind-8"}


def test_cuisine_filter_is_an_exact_token_match(store):
    """A substring match would let 'Indian' match 'North Indian'."""
    result = run(store, "Koramangala 5th Block", cuisines=["Indian"], top_k=1)
    assert result.candidate_ids == []
    assert result.relaxations == []
    assert result.message


def test_empty_cuisine_list_applies_no_cuisine_filter(store):
    """Treating [] as 'match nothing' would empty every request without cuisines."""
    result = run(store, "Indiranagar", cuisines=[], top_k=1)
    assert len(result.candidate_ids) == 8


def test_combined_filters_with_exactly_top_k_matches_need_no_relaxation(store):
    """Five matches for top_k 5 must not trigger relaxation; 4.0 equals the minimum."""
    result = run(store, "Indiranagar", budget=BudgetTier.medium, cuisines=["Italian"], min_rating=4.0)
    assert set(result.candidate_ids) == {"ind-1", "ind-2", "ind-3", "ind-4", "ind-5"}
    assert result.relaxations == []


def test_candidates_are_pre_sorted_by_rating_times_log_votes(store):
    """Sorting by rating alone would put Burma Burma first; by votes alone, Toit then Burma."""
    result = run(store, "Indiranagar", top_k=1)
    assert result.candidate_ids == ["ind-7", "ind-8", "ind-1", "ind-3", "ind-2", "ind-4", "ind-6", "ind-5"]


def test_score_ties_break_by_rating_votes_name_then_id():
    """Zero-vote places all score 0; the order must still be stable."""
    rows = fixture_frame().iloc[:1].copy()
    base = rows.iloc[0].to_dict()
    frame = pd.DataFrame(
        [
            {**base, "id": "t-3", "name": "B Place", "rating": 4.0, "votes": 0},
            {**base, "id": "t-9", "name": "A Place", "rating": 4.0, "votes": 0},
            {**base, "id": "t-2", "name": "A Place", "rating": 4.0, "votes": 0},
            {**base, "id": "t-1", "name": "Z Place", "rating": 4.2, "votes": 0},
        ]
    )
    store = RestaurantStore(frame, 500, 1200)
    result = run(store, "Indiranagar", top_k=1)
    assert result.candidate_ids == ["t-1", "t-2", "t-9", "t-3"]


def test_candidates_are_capped_after_sorting(store):
    """Capping before sorting would keep arbitrary rows instead of the best three."""
    result = run(store, "Indiranagar", top_k=1, max_candidates=3)
    assert result.candidate_ids == ["ind-7", "ind-8", "ind-1"]


def test_hard_filters_do_not_relax_to_fill_the_list(store):
    """BTM has no low-budget Mexican place at 4.8; the diner must drop a filter."""
    result = run(store, "BTM", budget=BudgetTier.low, cuisines=["Mexican"], min_rating=4.8)
    assert result.candidate_ids == []
    assert result.relaxations == []
    assert result.message


def test_short_lists_are_kept_without_lowering_rating(store):
    """Two Italian places at 4.4+ must not pull in 4.0 places just to reach top_k 3."""
    result = run(store, "Indiranagar", budget=BudgetTier.medium, cuisines=["Italian"], min_rating=4.4, top_k=3)
    assert result.relaxations == []
    assert set(result.candidate_ids) == {"ind-1", "ind-5"}


def test_some_cuisine_matches_are_kept_even_if_fewer_than_top_k(store):
    """Dropping Japanese to fill five slots would replace the one real match with generic Whitefield places."""
    result = run(store, "Whitefield", budget=BudgetTier.medium, cuisines=["Japanese"], min_rating=3.0)
    assert result.relaxations == []
    assert result.candidate_ids == ["wf-1"]
    assert result.cuisines == ["Japanese"]


def test_rating_step_is_skipped_at_or_below_three(store):
    """Lowering 3.0 to 2.5 would let poorly rated places in. Cuisine stays if any match exists."""
    result = run(store, "Whitefield", budget=BudgetTier.medium, cuisines=["Japanese"], min_rating=3.0)
    assert "Lowered minimum rating" not in "".join(result.relaxations)
    assert result.candidate_ids == ["wf-1"]


def test_rating_below_the_floor_does_not_get_a_match(store):
    """Sushi Haus is 3.1; a 3.2 floor must stay empty instead of lowering to 3.0."""
    result = run(store, "Whitefield", budget=BudgetTier.medium, cuisines=["Japanese"], min_rating=3.2)
    assert result.candidate_ids == []
    assert result.relaxations == []


def test_unrequested_filters_are_not_invented(store):
    result = run(store, "Whitefield", top_k=10)
    assert result.relaxations == []
    assert set(result.candidate_ids) == {"wf-3", "wf-4"}


def test_high_budget_is_kept_when_any_high_match_exists(store):
    """One high match must not widen into medium just to fill top_k."""
    result = run(store, "Jayanagar", budget=BudgetTier.high, min_rating=4.0)
    assert result.relaxations == []
    assert result.candidate_ids == ["jay-4"]


def test_high_budget_stays_empty_when_nothing_matches(store):
    """Whitefield has no high-tier rows; do not pull in medium or low places."""
    result = run(store, "Whitefield", budget=BudgetTier.high, min_rating=3.0)
    assert result.candidate_ids == []
    assert result.relaxations == []
    assert "wf-4" not in result.candidate_ids


def test_partial_list_is_returned_when_fewer_than_top_k_match(store):
    result = run(store, "Whitefield", budget=BudgetTier.medium, cuisines=["Japanese"], min_rating=3.0, top_k=5)
    assert result.candidate_ids == ["wf-1"]
    assert result.message is None


def test_nothing_matching_returns_empty_with_message(store):
    result = run(store, "Old Airport Road", budget=BudgetTier.low)
    assert result.candidate_ids == []
    assert result.relaxations == []
    assert result.message


def test_filtering_does_not_modify_the_shared_frame(store):
    """Mutating the store frame would corrupt concurrent requests."""
    before = store.frame.copy(deep=True)
    run(store, "BTM", budget=BudgetTier.low, cuisines=["Mexican"], min_rating=4.8)
    pd.testing.assert_frame_equal(store.frame, before)
