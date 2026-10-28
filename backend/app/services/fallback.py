from app.models.recommendation import Recommendation
from app.models.restaurant import Restaurant
from app.services.soft_prefs import matched_prefs

MAX_OTHER_CUISINES = 2
MAX_DISHES = 3


def _join(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _ordered_cuisines(restaurant: Restaurant, requested: list[str]) -> list[str]:
    wanted = {item.casefold() for item in requested}
    matched = [item for item in restaurant.cuisines if item.casefold() in wanted]
    others = [item for item in restaurant.cuisines if item.casefold() not in wanted]
    lead = matched[:1] if matched else others[:1]
    rest = [item for item in matched[1:] + others if item not in lead]
    return lead + rest[:MAX_OTHER_CUISINES]


def explain(restaurant: Restaurant, cuisines: list[str], extra_preferences: str | None = None) -> str:
    """Template explanation built only from the restaurant's own data fields."""
    rating = f"{restaurant.rating:.1f}"
    opening = f"{restaurant.rest_type} rated {rating}" if restaurant.rest_type else f"Rated {rating}"
    if restaurant.votes == 1:
        opening += " from 1 vote"
    elif restaurant.votes > 1:
        opening += f" from {restaurant.votes:,} votes"
    sentence = (
        f"{opening}, serving {_join(_ordered_cuisines(restaurant, cuisines))} "
        f"for about ₹{restaurant.cost_for_two:,} for two."
    )
    dishes = restaurant.dish_liked[:MAX_DISHES]
    if dishes:
        sentence += f" Known for {_join(dishes)}."
    confirmed = matched_prefs(restaurant, extra_preferences)
    if confirmed:
        sentence += f" Fits your note: {_join(confirmed)}."
    return sentence


def rank_fallback(
    restaurants: list[Restaurant],
    top_k: int,
    cuisines: list[str],
    extra_preferences: str | None = None,
) -> list[Recommendation]:
    """Keep the incoming order and attach template explanations."""
    return [
        Recommendation(
            rank=rank,
            restaurant=restaurant,
            explanation=explain(restaurant, cuisines, extra_preferences),
        )
        for rank, restaurant in enumerate(restaurants[:top_k], start=1)
    ]
