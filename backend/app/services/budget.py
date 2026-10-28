def assign_budget_tier(cost: int, low_max: int, medium_max: int) -> str:
    """Map a cost for two onto low, medium, or high.

    low is cost <= low_max, medium is cost <= medium_max, and high is everything above.
    """
    if cost <= 0:
        raise ValueError("cost must be positive")
    if cost <= low_max:
        return "low"
    if cost <= medium_max:
        return "medium"
    return "high"
