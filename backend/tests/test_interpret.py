from app.models.llm import FilterDecision
from app.models.recommendation import RecommendationRequest
from app.services.filter import apply_filter_decision, match_counts
from app.services.interpret import (
    apply_decision,
    build_interpret_messages,
    constrain_decision,
    parse_filter_decision,
    sanitize_decision,
    store_vocab,
)


def test_parse_filter_decision_keeps_only_known_fields():
    raw = '{"rest_types":["Casual Dining"],"unmapped":["rooftop"],"invented":true}'
    decision = parse_filter_decision(raw)
    assert decision.rest_types == ["Casual Dining"]
    assert decision.unmapped == ["rooftop"]


def test_sanitize_drops_values_that_are_not_in_the_store_vocab(store):
    vocab = store_vocab(store)
    dirty = FilterDecision(rest_types=["Casual Dining", "Rooftop Lounge"], listing_types=["Buffet", "Helipad"])
    clean = sanitize_decision(dirty, vocab)
    assert "Casual Dining" in clean.rest_types
    assert "Rooftop Lounge" not in clean.rest_types
    assert "Helipad" not in clean.listing_types


def test_apply_decision_boosts_mapped_rest_types_without_dropping_others(store):
    """Soft rank: Casual Dining leads, Cafe still remains."""
    casual = store.get("ind-1")
    cafe = store.get("ind-4")
    ranked = apply_decision([cafe, casual], FilterDecision(rest_types=["Casual Dining"]))
    assert [item.id for item in ranked] == ["ind-1", "ind-4"]


def test_apply_decision_does_not_invent_unmapped_wishes(store):
    ranked = apply_decision([store.get("ind-1")], FilterDecision(unmapped=["parking"]))
    assert ranked[0].id == "ind-1"


def test_interpret_prompt_sends_the_extra_text_and_the_allowed_values(store):
    request = RecommendationRequest(
        location="Indiranagar",
        cuisines=["Asian"],
        extra_preferences="cozy dining",
    )
    system, user = build_interpret_messages(request, store_vocab(store))
    assert "cozy dining" in user["content"]
    assert "Casual Dining" in user["content"]
    assert "allowed values" in system["content"].casefold()
    assert "do not invent" in system["content"].casefold()
    assert "location never" in system["content"].casefold()


def test_interpret_prompt_includes_match_counts_for_the_llm(store):
    request = RecommendationRequest(
        location="BTM",
        budget="low",
        cuisines=["Mexican"],
        min_rating=4.8,
    )
    vocab = store_vocab(store)
    vocab["counts"] = match_counts(
        store.frame, location="BTM", budget="low", cuisines=["Mexican"], min_rating=4.8
    )
    _, user = build_interpret_messages(request, vocab)
    assert "with_all_requested: 0" in user["content"]
    assert "in_location:" in user["content"]


def test_apply_filter_decision_uses_only_the_llm_fields(store):
    """Code applies Groq's filters; it does not run its own rating/cuisine/budget relaxation."""
    decision = FilterDecision(
        cuisines=["Mexican"],
        budget="low",
        min_rating=4.0,
        relaxations=["Lowered minimum rating from 4.8 to 4.0"],
    )
    result = apply_filter_decision(store.frame, location="BTM", decision=decision)
    assert result.candidate_ids == ["btm-1"]
    assert result.relaxations == []
    assert result.cuisines == ["Mexican"]


def test_match_counts_report_how_many_rows_fit_each_constraint(store):
    counts = match_counts(
        store.frame, location="BTM", budget="low", cuisines=["Mexican"], min_rating=4.8
    )
    assert counts["in_location"] == 6
    assert counts["with_all_requested"] == 0


def test_constrain_restores_cuisine_when_matches_still_exist(store):
    """Groq must not empty the list by dropping a cuisine that still has hits."""
    request = RecommendationRequest(
        location="Whitefield", budget="medium", cuisines=["Japanese"], min_rating=3.0, top_k=5
    )
    counts = match_counts(
        store.frame, location="Whitefield", budget="medium", cuisines=["Japanese"], min_rating=3.0, top_k=5
    )
    assert counts["with_all_requested"] > 0
    dropped = FilterDecision(
        cuisines=[],
        budget="medium",
        min_rating=3.0,
        relaxations=["dropped_cuisine", "lowered_min_rating"],
    )
    kept = constrain_decision(dropped, request, counts, ["Japanese"])
    assert kept.cuisines == ["Japanese"]
    assert kept.budget == "medium"
    assert kept.min_rating == 3.0
    assert kept.relaxations == []


def test_constrain_keeps_user_cuisine_even_when_nothing_matches(store):
    request = RecommendationRequest(
        location="Koramangala 5th Block", cuisines=["Indian"], min_rating=3.5, top_k=5
    )
    dropped = FilterDecision(
        cuisines=[],
        min_rating=3.0,
        relaxations=["Removed cuisine filter (Indian)"],
    )
    kept = constrain_decision(dropped, request, {}, ["Indian"])
    assert kept.cuisines == ["Indian"]
    assert kept.min_rating == 3.5
    assert kept.relaxations == []
