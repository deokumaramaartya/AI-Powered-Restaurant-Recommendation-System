from enum import Enum

from pydantic import BaseModel


class BudgetTier(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class Restaurant(BaseModel):
    id: str
    name: str
    location: str
    area: str
    address: str
    cuisines: list[str]
    rating: float
    votes: int
    cost_for_two: int
    budget_tier: BudgetTier
    rest_type: str | None
    listing_types: list[str]
    online_order: bool
    book_table: bool
    dish_liked: list[str]
