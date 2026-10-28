from app.services.soft_prefs import matched_prefs, rank_by_soft_prefs


def test_cozy_dining_maps_to_casual_dining(store):
    """Users type cozy, not the dataset's Casual Dining label."""
    assert matched_prefs(store.get("ind-1"), "cozy dining") == ["cozy dining"]
    assert matched_prefs(store.get("ind-4"), "cozy dining") == []


def test_table_booking_and_casual_dining_are_confirmed_from_data(store):
    """G-14 extras must use book_table and rest_type, not invented ambience."""
    toscano = store.get("ind-1")
    assert matched_prefs(toscano, "family-friendly, casual dining with table booking") == [
        "casual dining",
        "table booking",
    ]


def test_unconfirmable_wishes_do_not_invent_a_match(store):
    """Parking and rooftop are not in the data, so they must not score a restaurant (G-19)."""
    assert matched_prefs(store.get("ind-1"), "need parking and rooftop seating") == []


def test_cheap_does_not_override_the_budget_filter(store):
    """PRM-09: extra text must not rewrite structured budget."""
    assert matched_prefs(store.get("ind-1"), "something cheap, under 300 for two") == []


def test_candidates_with_confirmed_extras_rank_ahead_of_the_rest(store):
    """A Quick Bites place that takes online orders should beat a sit-down place for a lunch-break ask."""
    sit_down = store.get("ind-1")
    quick = store.get("kor-1")
    ranked = rank_by_soft_prefs([sit_down, quick], "quick service, takeaway during a lunch break")
    assert [item.id for item in ranked] == ["kor-1", "ind-1"]


def test_no_extra_text_keeps_the_original_order(store):
    restaurants = [store.get("ind-3"), store.get("ind-1"), store.get("ind-2")]
    assert [item.id for item in rank_by_soft_prefs(restaurants, None)] == ["ind-3", "ind-1", "ind-2"]


def test_buffet_matches_listing_or_liked_dish(store):
    buffet = store.get("ind-1").model_copy(update={"listing_types": ["Buffet", "Dine-out"]})
    other = store.get("ind-2")
    assert matched_prefs(buffet, "lunch buffet") == ["buffet"]
    assert matched_prefs(other, "lunch buffet") == []
    ranked = rank_by_soft_prefs([other, buffet], "lunch buffet")
    assert [item.id for item in ranked] == [buffet.id, other.id]
