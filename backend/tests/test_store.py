import pandas as pd

from app.models.restaurant import BudgetTier, Restaurant
from app.services.store import RestaurantStore
from evals.fixtures.restaurants import fixture_frame


def test_budget_tier_is_computed_at_load_from_thresholds():
    """Tiers stored at ingest, or an off-by-one at the boundary, would misfile 500 and 1200."""
    store = RestaurantStore(fixture_frame(), 500, 1200)
    assert store.get("jay-1").budget_tier == BudgetTier.low
    assert store.get("jay-2").budget_tier == BudgetTier.medium
    assert store.get("jay-3").budget_tier == BudgetTier.medium
    assert store.get("jay-4").budget_tier == BudgetTier.high


def test_budget_tier_changes_with_thresholds_without_reingest():
    """The same frame loaded with new thresholds must produce new tiers."""
    store = RestaurantStore(fixture_frame(), 300, 600)
    assert store.get("jay-1").budget_tier == BudgetTier.medium
    assert store.get("ind-2").budget_tier == BudgetTier.high
    assert store.get("kor-1").budget_tier == BudgetTier.low


def test_loading_does_not_modify_the_input_frame():
    """Adding columns to the caller's frame would leak budget_tier back into Parquet writes."""
    frame = fixture_frame()
    RestaurantStore(frame, 500, 1200)
    assert "budget_tier" not in frame.columns


def test_get_returns_full_record_or_none():
    """The lookup feeds displayed facts, so every field must come from the data."""
    store = RestaurantStore(fixture_frame(), 500, 1200)
    restaurant = store.get("ind-1")
    assert isinstance(restaurant, Restaurant)
    assert restaurant.name == "Toscano"
    assert restaurant.cuisines == ["Italian", "Continental"]
    assert restaurant.cost_for_two == 1100
    assert restaurant.book_table is True
    assert store.get("missing") is None
    assert store.count == 29


def test_resolve_location_is_case_insensitive_and_keeps_display_casing():
    """Matching on raw text would reject '  btm ' or turn BTM into Btm."""
    store = RestaurantStore(fixture_frame(), 500, 1200)
    assert store.resolve_location("  btm ") == "BTM"
    assert store.resolve_location("koramangala 5TH block") == "Koramangala 5th Block"
    assert store.resolve_location("Delhi") is None


def test_resolve_cuisines_returns_canonical_names_and_unknowns():
    """Unknown cuisines must be reported by name; known ones mapped to data spelling."""
    store = RestaurantStore(fixture_frame(), 500, 1200)
    known, unknown = store.resolve_cuisines(["italian", "Klingon", "NORTH INDIAN"])
    assert known == ["Italian", "North Indian"]
    assert unknown == ["Klingon"]


def test_meta_lists_sorted_values_and_tier_bounds():
    """The dropdowns and tier bounds must match the data and the config thresholds."""
    store = RestaurantStore(fixture_frame(), 500, 1200)
    meta = store.meta()
    assert meta["locations"] == [
        "BTM",
        "Indiranagar",
        "Jayanagar",
        "Koramangala 5th Block",
        "Old Airport Road",
        "Whitefield",
    ]
    assert meta["cuisines"][:3] == ["American", "Andhra", "Biryani"]
    assert "Japanese" in meta["cuisines"]
    assert meta["budget_tiers"] == {
        "low": {"max": 500},
        "medium": {"min": 501, "max": 1200},
        "high": {"min": 1201},
    }
    assert meta["rating_range"] == {"min": 0, "max": 5}


def test_from_parquet_loads_list_columns_as_python_lists(tmp_path):
    """Parquet returns arrays for list columns; the models need plain lists."""
    path = tmp_path / "restaurants.parquet"
    fixture_frame().to_parquet(path, index=False)
    store = RestaurantStore.from_parquet(path, 500, 1200)
    restaurant = store.get("ind-1")
    assert restaurant.dish_liked[:2] == ["Pasta", "Risotto"]
    assert restaurant.listing_types == ["Dine-out"]
    assert isinstance(store.frame, pd.DataFrame)
