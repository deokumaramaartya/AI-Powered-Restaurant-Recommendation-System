from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    restaurants_loaded: int


class TierBounds(BaseModel):
    min: int | None = None
    max: int | None = None


class RatingRange(BaseModel):
    min: float
    max: float


class MetaResponse(BaseModel):
    locations: list[str]
    cuisines: list[str]
    budget_tiers: dict[str, TierBounds]
    rating_range: RatingRange
