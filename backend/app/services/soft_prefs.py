"""Map free-text extras onto fields the store can actually confirm."""

from __future__ import annotations

from app.models.restaurant import Restaurant

# Phrase → (label shown to the model/UI, predicate on a restaurant).
# Only signals that exist as structured fields. Parking, rooftop, quiet, cheap: none.
_SIGNALS: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (("table booking", "book table", "book a table", "reservation"), "table booking", "book_table"),
    (("order online", "online order", "online for delivery"), "online ordering", "online_order"),
    (("delivery",), "online ordering", "online_order"),
    (("buffet",), "buffet", "buffet"),
    (("quick bites", "quick service", "takeaway", "lunch break"), "quick service", "quick"),
    (("casual dining", "cozy dining", "cozy"), "casual dining", "casual"),
    (("cafe", "coffee"), "cafe", "cafe"),
)


def _folded(value: str | None) -> str:
    return " ".join((value or "").split()).casefold()


def _has_type(restaurant: Restaurant, needle: str) -> bool:
    return needle in _folded(restaurant.rest_type)


def _in_lists(restaurant: Restaurant, needle: str) -> bool:
    values = [*restaurant.listing_types, *restaurant.dish_liked]
    return any(needle in _folded(item) for item in values)


def _confirms(restaurant: Restaurant, kind: str) -> bool:
    if kind == "book_table":
        return restaurant.book_table
    if kind == "online_order":
        return restaurant.online_order
    if kind == "buffet":
        return _in_lists(restaurant, "buffet")
    if kind == "quick":
        return _has_type(restaurant, "quick bites") or _in_lists(restaurant, "takeaway")
    if kind == "casual":
        return _has_type(restaurant, "casual dining")
    if kind == "cafe":
        return _has_type(restaurant, "cafe")
    return False


def _requested(extra: str) -> list[tuple[str, str]]:
    text = _folded(extra)
    if not text:
        return []
    found: list[tuple[int, str, str]] = []
    seen: set[str] = set()
    for phrases, default_label, kind in _SIGNALS:
        hits = [(text.find(phrase), phrase) for phrase in phrases if phrase in text]
        if not hits:
            continue
        index, phrase = min(hits, key=lambda item: (item[0], -len(item[1])))
        label = "cozy dining" if phrase in {"cozy", "cozy dining"} else default_label
        if label not in seen:
            seen.add(label)
            found.append((index, label, kind))
    found.sort(key=lambda item: item[0])
    return [(label, kind) for _, label, kind in found]


def matched_prefs(restaurant: Restaurant, extra: str | None) -> list[str]:
    """Labels from the extra text that this restaurant's own fields confirm."""
    if not extra:
        return []
    return [label for label, kind in _requested(extra) if _confirms(restaurant, kind)]


def rank_by_soft_prefs(restaurants: list[Restaurant], extra: str | None) -> list[Restaurant]:
    """Keep relative order; restaurants that confirm more extras come first."""
    if not extra or not _requested(extra):
        return list(restaurants)
    return sorted(
        restaurants,
        key=lambda restaurant: -len(matched_prefs(restaurant, extra)),
    )
