import json

import pytest

from app.models.llm import LLMParseError, LLMRecommendation, LLMResult
from app.services.fallback import explain
from app.services.parser import MAX_EXPLANATION_CHARS, finalize, parse_llm_output

INDIRANAGAR = ["ind-7", "ind-8", "ind-1", "ind-3", "ind-2"]


@pytest.fixture
def candidates(store):
    return [store.get(restaurant_id) for restaurant_id in INDIRANAGAR]


def item(restaurant_id, rank, explanation="Fits the brief."):
    return LLMRecommendation(restaurant_id=restaurant_id, rank=rank, explanation=explanation)


def result(*items, summary="Good picks."):
    return LLMResult(recommendations=list(items), summary=summary)


def ids(ranking):
    return [recommendation.restaurant.id for recommendation in ranking.recommendations]


def test_parse_valid_json_into_llm_result():
    """The schema in the prompt must round-trip into LLMResult."""
    text = json.dumps({
        "recommendations": [{"restaurant_id": "ind-1", "rank": 1, "explanation": "Rated 4.5."}],
        "summary": "One pick.",
    })
    parsed = parse_llm_output(text)
    assert parsed.recommendations == [item("ind-1", 1, "Rated 4.5.")]
    assert parsed.summary == "One pick."


@pytest.mark.parametrize("text", ["", "   ", "{not json", "[1, 2]", '"just a string"'])
def test_malformed_or_non_object_json_raises_parse_error(text):
    """Anything that is not a JSON object must send the request to the fallback ranker."""
    with pytest.raises(LLMParseError):
        parse_llm_output(text)


@pytest.mark.parametrize(
    "payload",
    [
        {"summary": "no list"},
        {"recommendations": "ind-1"},
        {"recommendations": [{"rank": 1, "explanation": "missing id"}]},
        {"recommendations": [{"restaurant_id": "ind-1", "rank": 1, "explanation": 42}]},
    ],
)
def test_json_that_breaks_the_schema_raises_parse_error(payload):
    """Valid JSON with the wrong shape is as unusable as invalid JSON (PAR-03)."""
    with pytest.raises(LLMParseError):
        parse_llm_output(json.dumps(payload))


def test_code_fences_and_surrounding_text_are_removed():
    """JSON mode should prevent fences, but a fenced reply must still parse (PAR-02)."""
    body = json.dumps({"recommendations": [{"restaurant_id": "ind-1", "rank": 1, "explanation": "x."}]})
    parsed = parse_llm_output(f"Here you go:\n```json\n{body}\n```\nEnjoy!")
    assert [entry.restaurant_id for entry in parsed.recommendations] == ["ind-1"]


def test_numeric_ids_and_missing_or_text_ranks_are_tolerated():
    """An id sent as a number, or a rank of "first", must not throw away the whole answer."""
    parsed = parse_llm_output(json.dumps({
        "recommendations": [
            {"restaurant_id": 12345, "rank": "first", "explanation": "a."},
            {"restaurant_id": "ind-1", "explanation": "b."},
        ]
    }))
    assert [(entry.restaurant_id, entry.rank) for entry in parsed.recommendations] == [("12345", None), ("ind-1", None)]


def test_valid_output_is_joined_with_store_data(candidates, store):
    """Displayed facts must come from the store, never from LLM text (PAR-01)."""
    ranking = finalize(result(item("ind-1", 1, "Great pasta."), item("ind-7", 2)), candidates, top_k=2, cuisines=[])
    assert ids(ranking) == ["ind-1", "ind-7"]
    assert ranking.recommendations[0].restaurant == store.get("ind-1")
    assert ranking.recommendations[0].explanation == "Great pasta."
    assert [entry.rank for entry in ranking.recommendations] == [1, 2]
    assert ranking.summary == "Good picks."
    assert ranking.backfilled == 0


def test_unknown_ids_are_dropped_and_the_summary_cleared(candidates):
    """An invented restaurant must never reach the user, nor a summary that may mention it (PAR-08, PAR-17)."""
    ranking = finalize(result(item("truffles-kor", 1), item("ind-1", 2)), candidates, top_k=1, cuisines=[])
    assert ids(ranking) == ["ind-1"]
    assert ranking.dropped_ids == ["truffles-kor"]
    assert ranking.summary is None


def test_name_that_names_one_candidate_wins_over_a_mismatched_id(candidates):
    """The explanation is written about the named restaurant, so that card must show it (LLM-14)."""
    entry = LLMRecommendation(restaurant_id="ind-7", name=f"  {candidates[2].name.upper()} ", rank=1,
                              explanation=f"{candidates[2].name} fits.")
    ranking = finalize(result(entry), candidates, top_k=1, cuisines=[])
    assert ids(ranking) == [candidates[2].id]
    assert ranking.recommendations[0].explanation == f"{candidates[2].name} fits."
    assert ranking.dropped_ids == []


def test_a_name_given_in_place_of_the_id_is_matched(candidates):
    """The model sometimes writes the restaurant's name in restaurant_id (LLM-14)."""
    ranking = finalize(result(item(candidates[3].name, 1)), candidates, top_k=1, cuisines=[])
    assert ids(ranking) == [candidates[3].id]
    assert ranking.dropped_ids == []
    assert ranking.summary == "Good picks."


def test_a_name_shared_by_two_candidates_falls_back_to_the_id(candidates):
    """Two outlets with one name can't be told apart by name, so the id decides."""
    twins = [candidates[0], candidates[1].model_copy(update={"name": candidates[0].name}), *candidates[2:]]
    entry = LLMRecommendation(restaurant_id=twins[1].id, name=twins[0].name, rank=1)
    assert ids(finalize(result(entry), twins, top_k=1, cuisines=[])) == [twins[1].id]


def test_an_unknown_name_with_a_valid_id_keeps_the_id(candidates):
    entry = LLMRecommendation(restaurant_id="ind-8", name="Truffles", rank=1)
    assert ids(finalize(result(entry), candidates, top_k=1, cuisines=[])) == ["ind-8"]


def test_duplicates_keep_the_best_ranked_occurrence(candidates):
    """Listing a restaurant twice must not show it twice (PAR-10)."""
    ranking = finalize(
        result(item("ind-1", 3, "late"), item("ind-1", 1, "early"), item("ind-7", 2)), candidates, top_k=2, cuisines=[]
    )
    assert ids(ranking) == ["ind-1", "ind-7"]
    assert ranking.recommendations[0].explanation == "early"


def test_rank_gaps_and_repeats_are_sorted_then_renumbered(candidates):
    """Ranks 1, 7, 3, 3 must become 1 to 4 in rank order, ties kept in list order (PAR-06)."""
    ranking = finalize(
        result(item("ind-7", 1), item("ind-8", 7), item("ind-1", 3), item("ind-3", 3)), candidates, top_k=4, cuisines=[]
    )
    assert ids(ranking) == ["ind-7", "ind-1", "ind-3", "ind-8"]
    assert [entry.rank for entry in ranking.recommendations] == [1, 2, 3, 4]


def test_missing_ranks_fall_back_to_list_order(candidates):
    """Without usable ranks the order the model listed is the best signal (PAR-07)."""
    ranking = finalize(result(item("ind-3", None), item("ind-7", 1), item("ind-1", None)), candidates, top_k=3, cuisines=[])
    assert ids(ranking) == ["ind-3", "ind-7", "ind-1"]


def test_more_than_top_k_items_are_truncated(candidates):
    """An injected 'list 50 restaurants' must not lengthen the response (PAR-05)."""
    many = [item(restaurant_id, rank) for rank, restaurant_id in enumerate(INDIRANAGAR, start=1)]
    assert len(finalize(result(*many), candidates, top_k=2, cuisines=[]).recommendations) == 2


def test_ids_are_matched_ignoring_case_and_whitespace(candidates, store):
    """'  IND-1 ' and a numeric id are the same restaurant as 'ind-1' and '12345' (PAR-09)."""
    numeric = store.get("ind-2").model_copy(update={"id": "12345"})
    pool = candidates + [numeric]
    ranking = finalize(result(item("  IND-1 ", 1), item("12345", 2)), pool, top_k=2, cuisines=[])
    assert ids(ranking) == ["ind-1", "12345"]


def test_empty_explanation_is_replaced_with_the_template(candidates, store):
    """A blank card explanation is worse than the rule-based one (PAR-11)."""
    ranking = finalize(result(item("ind-1", 1, "   ")), candidates, top_k=1, cuisines=["Italian"])
    assert ranking.recommendations[0].explanation == explain(store.get("ind-1"), ["Italian"])


def test_long_explanation_is_trimmed_at_a_sentence_boundary(candidates):
    """Explanations stay readable on a card: at most 400 characters (PAR-12)."""
    long_text = "Rated 4.5 and loved by regulars. " + "Very long praise " * 40 + "end."
    explanation = finalize(result(item("ind-1", 1, long_text)), candidates, top_k=1, cuisines=[]).recommendations[0].explanation
    assert len(explanation) <= MAX_EXPLANATION_CHARS
    assert explanation == "Rated 4.5 and loved by regulars."


def test_long_explanation_without_a_sentence_break_is_cut_with_an_ellipsis(candidates):
    explanation = finalize(result(item("ind-1", 1, "word " * 200)), candidates, top_k=1, cuisines=[]).recommendations[0].explanation
    assert len(explanation) <= MAX_EXPLANATION_CHARS
    assert explanation.endswith("…")


@pytest.mark.parametrize("summary", [None, "", "   "])
def test_blank_summary_becomes_none(candidates, summary):
    """The UI hides a null summary; an empty string would render an empty box (PAR-14)."""
    assert finalize(result(item("ind-1", 1), summary=summary), candidates, top_k=1, cuisines=[]).summary is None


def test_short_list_is_backfilled_from_pre_sorted_candidates(candidates, store):
    """Two picks for top_k 4 must be topped up in pre-sort order with template text (PAR-16)."""
    ranking = finalize(result(item("ind-1", 1), item("ind-8", 2)), candidates, top_k=4, cuisines=[])
    assert ids(ranking) == ["ind-1", "ind-8", "ind-7", "ind-3"]
    assert ranking.backfilled == 2
    assert ranking.recommendations[2].explanation == explain(store.get("ind-7"), [])


def test_backfill_stops_when_candidates_run_out_and_never_repeats(candidates):
    """Asking for 10 from 5 candidates returns 5 distinct restaurants (PAR-15)."""
    ranking = finalize(result(item("ind-2", 1), item("ind-7", 2)), candidates, top_k=10, cuisines=[])
    assert sorted(ids(ranking)) == sorted(INDIRANAGAR)
    assert len(set(ids(ranking))) == 5


@pytest.mark.parametrize("items", [[], [item("nobody", 1), item("ghost", 2)]])
def test_no_usable_pick_is_a_parse_error(candidates, items):
    """An empty list, or only invented ids, means the LLM did not do the job (PAR-04)."""
    with pytest.raises(LLMParseError):
        finalize(result(*items), candidates, top_k=3, cuisines=[])
