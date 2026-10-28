import pytest

from app.services.budget import assign_budget_tier


def test_default_boundaries_map_to_low_medium_and_high():
    """A wrong comparison at 500, 501, 1200, or 1201 would put a cost in the wrong tier."""
    assert assign_budget_tier(500, 500, 1200) == "low"
    assert assign_budget_tier(501, 500, 1200) == "medium"
    assert assign_budget_tier(1200, 500, 1200) == "medium"
    assert assign_budget_tier(1201, 500, 1200) == "high"


def test_custom_thresholds_are_used_instead_of_defaults():
    """Hard-coded 500/1200 bounds would ignore the thresholds passed in."""
    assert assign_budget_tier(40, 50, 80) == "low"
    assert assign_budget_tier(50, 50, 80) == "low"
    assert assign_budget_tier(51, 50, 80) == "medium"
    assert assign_budget_tier(80, 50, 80) == "medium"
    assert assign_budget_tier(81, 50, 80) == "high"


def test_extreme_cost_is_high():
    """An outlier such as 6000 must stay in high rather than being dropped or capped."""
    assert assign_budget_tier(6000, 500, 1200) == "high"


def test_non_positive_cost_is_rejected():
    """Zero or negative cost must not be labeled low."""
    with pytest.raises(ValueError):
        assign_budget_tier(0, 500, 1200)
    with pytest.raises(ValueError):
        assign_budget_tier(-1, 500, 1200)
