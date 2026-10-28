from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from app.models.restaurant import Restaurant
from app.services.budget import assign_budget_tier

logger = logging.getLogger(__name__)

LIST_COLUMNS = ("cuisines", "listing_types", "dish_liked")
REQUIRED_COLUMNS = (
    "id", "name", "location", "area", "address", "cuisines", "rating", "votes", "cost_for_two",
    "rest_type", "listing_types", "online_order", "book_table", "dish_liked",
)
DATA_NOT_LOADED = "Dataset not loaded. Run `python -m scripts.ingest` from backend/ and restart the server."


def _as_list(value) -> list[str]:
    if value is None:
        return []
    return [str(item) for item in value]


def _optional_text(value) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    return str(value)


class RestaurantStore:
    """In-memory restaurant data with budget tiers computed from the given thresholds."""

    def __init__(self, frame: pd.DataFrame, low_max: int, medium_max: int):
        self.low_max = low_max
        self.medium_max = medium_max
        frame = frame.copy()
        for column in LIST_COLUMNS:
            frame[column] = frame[column].map(_as_list)
        frame["budget_tier"] = [assign_budget_tier(int(cost), low_max, medium_max) for cost in frame["cost_for_two"]]
        frame["location_key"] = frame["location"].map(str.casefold)
        frame["cuisine_keys"] = frame["cuisines"].map(lambda items: frozenset(item.casefold() for item in items))
        self.frame = frame.reset_index(drop=True)

        self._by_id = {restaurant.id: restaurant for restaurant in map(self._to_restaurant, self.frame.itertuples())}
        self._locations: dict[str, str] = {}
        for location in self.frame["location"]:
            self._locations.setdefault(location.casefold(), location)
        self._cuisines: dict[str, str] = {}
        for items in self.frame["cuisines"]:
            for item in items:
                self._cuisines.setdefault(item.casefold(), item)

    @classmethod
    def from_parquet(cls, path: Path | str, low_max: int, medium_max: int) -> RestaurantStore:
        return cls(pd.read_parquet(path), low_max, medium_max)

    @staticmethod
    def _to_restaurant(row) -> Restaurant:
        return Restaurant(
            id=str(row.id),
            name=str(row.name),
            location=str(row.location),
            area=str(row.area),
            address=str(row.address),
            cuisines=list(row.cuisines),
            rating=float(row.rating),
            votes=int(row.votes),
            cost_for_two=int(row.cost_for_two),
            budget_tier=row.budget_tier,
            rest_type=_optional_text(row.rest_type),
            listing_types=list(row.listing_types),
            online_order=bool(row.online_order),
            book_table=bool(row.book_table),
            dish_liked=list(row.dish_liked),
        )

    @property
    def count(self) -> int:
        return len(self.frame)

    def get(self, restaurant_id: str) -> Restaurant | None:
        return self._by_id.get(restaurant_id)

    def resolve_location(self, text: str) -> str | None:
        return self._locations.get(" ".join(text.split()).casefold())

    def resolve_cuisines(self, names: list[str]) -> tuple[list[str], list[str]]:
        known: list[str] = []
        unknown: list[str] = []
        for name in names:
            canonical = self._cuisines.get(" ".join(name.split()).casefold())
            if canonical is None:
                unknown.append(name)
            elif canonical not in known:
                known.append(canonical)
        return known, unknown

    def meta(self) -> dict:
        return {
            "locations": sorted(self._locations.values(), key=str.casefold),
            "cuisines": sorted(self._cuisines.values(), key=str.casefold),
            "budget_tiers": {
                "low": {"max": self.low_max},
                "medium": {"min": self.low_max + 1, "max": self.medium_max},
                "high": {"min": self.medium_max + 1},
            },
            "rating_range": {"min": 0, "max": 5},
        }


def _read(path: Path, low_max: int, medium_max: int) -> tuple[RestaurantStore | None, str]:
    if not Path(path).is_file():
        return None, f"{path} was not found."
    try:
        frame = pd.read_parquet(path)
    except Exception as exc:
        return None, f"{path} is unreadable ({type(exc).__name__}); it may be corrupt, so regenerate it."
    missing = [column for column in REQUIRED_COLUMNS if column not in frame.columns]
    if missing:
        return None, f"{path} is missing required columns: {', '.join(missing)}."
    if frame.empty:
        return None, f"{path} has no rows."
    try:
        return RestaurantStore(frame, low_max, medium_max), ""
    except Exception as exc:
        return None, f"{path} has values that could not be loaded ({type(exc).__name__}); regenerate it."


def load_store(path: Path, low_max: int, medium_max: int) -> RestaurantStore | None:
    """Load the dataset, or log one clear error (no stack trace) and return None so the API can answer 503."""
    store, problem = _read(path, low_max, medium_max)
    if store is None:
        logger.error("%s %s", DATA_NOT_LOADED, problem)
        return None
    logger.info("Loaded %d restaurants from %s", store.count, path)
    return store
