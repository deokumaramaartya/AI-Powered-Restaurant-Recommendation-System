import re

from app.services.fallback import explain, rank_fallback


def sentence_count(text: str) -> int:
    return len([part for part in re.split(r"[.!?](?:\s|$)", text) if part.strip()])


def test_returns_the_smaller_of_top_k_and_candidate_count(store):
    """Padding to top_k or ignoring it would return the wrong number of cards."""
    restaurants = [store.get("ind-1"), store.get("ind-2"), store.get("ind-3")]
    assert len(rank_fallback(restaurants, top_k=5, cuisines=[])) == 3
    assert len(rank_fallback(restaurants, top_k=2, cuisines=[])) == 2


def test_keeps_pre_sorted_order_and_numbers_ranks_from_one(store):
    """Re-sorting here would disagree with the filter's pre-sort."""
    restaurants = [store.get("ind-3"), store.get("ind-1"), store.get("ind-2")]
    ranked = rank_fallback(restaurants, top_k=3, cuisines=[])
    assert [item.restaurant.id for item in ranked] == ["ind-3", "ind-1", "ind-2"]
    assert [item.rank for item in ranked] == [1, 2, 3]


def test_explanation_mentions_rating_votes_cost_and_type(store):
    """The template must be grounded in the restaurant's own numbers."""
    text = explain(store.get("ind-1"), cuisines=[])
    assert "4.5" in text
    assert "2,100 votes" in text
    assert "₹1,100 for two" in text
    assert "Casual Dining" in text


def test_missing_rest_type_never_prints_none(store):
    """Formatting a null rest_type would show the word None to users."""
    text = explain(store.get("ind-8"), cuisines=[])
    assert "None" not in text
    assert text.startswith("Rated 4.7")


def test_dishes_phrase_lists_at_most_three_and_is_omitted_when_empty(store):
    """Listing every dish bloats the card; an empty list must not leave a dangling phrase."""
    with_dishes = explain(store.get("ind-1"), cuisines=[])
    assert "Known for Pasta, Risotto and Tiramisu." in with_dishes
    assert "Pizza" not in with_dishes.split("Known for")[1]
    without = explain(store.get("ind-3"), cuisines=[])
    assert "Known for" not in without


def test_requested_cuisine_comes_first_then_at_most_two_others(store):
    """A long cuisine list should lead with what the user asked for."""
    text = explain(store.get("ind-7"), cuisines=["pizza"])
    assert "serving Pizza, Continental and Italian" in text
    assert "Finger Food" not in text


def test_vote_count_uses_singular_and_omits_zero(store):
    """'1 votes' and 'from 0 votes' both read as bugs."""
    one = store.get("ind-2").model_copy(update={"votes": 1})
    assert "from 1 vote," in explain(one, cuisines=[])
    zero = explain(store.get("kor-6"), cuisines=[])
    assert "0 vote" not in zero
    assert "Rated 3.5" not in zero
    assert "rated 3.5" in zero


def test_explanation_mentions_extras_the_data_can_confirm(store):
    text = explain(store.get("ind-1"), cuisines=[], extra_preferences="casual dining with table booking")
    assert "Fits your note: casual dining and table booking." in text
    assert "Fits your note" not in explain(store.get("ind-1"), cuisines=[], extra_preferences="need parking")


def test_explanations_are_one_or_two_sentences_under_400_chars(store):
    """C-08 caps explanations at two sentences and 400 characters."""
    for restaurant_id in ("ind-1", "ind-7", "ind-8", "kor-6"):
        text = explain(store.get(restaurant_id), cuisines=["Italian"])
        assert 1 <= sentence_count(text) <= 2
        assert len(text) <= 400
