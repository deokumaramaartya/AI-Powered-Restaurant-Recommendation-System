from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.config import MAX_TOP_K
from app.models.restaurant import BudgetTier, Restaurant

DEFAULT_MIN_RATING = 3.5
MAX_CUISINES = 20
# Counted in code points on the raw text, before the prompt sanitizer runs.
MAX_EXTRA_PREFERENCES = 300


class RecommendationRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    location: str
    budget: BudgetTier | None = None
    cuisines: list[str] = Field(default_factory=list, max_length=MAX_CUISINES)
    min_rating: float = Field(DEFAULT_MIN_RATING, ge=0, le=5, allow_inf_nan=False)
    extra_preferences: str | None = Field(None, max_length=MAX_EXTRA_PREFERENCES)
    top_k: int = Field(5, ge=1, le=MAX_TOP_K)

    @field_validator("location")
    @classmethod
    def _location_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("location must not be empty")
        return value

    @field_validator("cuisines")
    @classmethod
    def _dedupe_cuisines(cls, value: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for item in value:
            text = " ".join(item.split())
            key = text.casefold()
            if not text or key in seen:
                continue
            seen.add(key)
            result.append(text)
        return result

    @field_validator("min_rating", mode="before")
    @classmethod
    def _null_rating_means_default(cls, value):
        return DEFAULT_MIN_RATING if value is None else value

    @field_validator("extra_preferences")
    @classmethod
    def _blank_preferences_are_none(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        return value


class Recommendation(BaseModel):
    rank: int
    restaurant: Restaurant
    explanation: str


class RecommendationResponse(BaseModel):
    source: Literal["llm", "fallback"]
    summary: str | None
    relaxations: list[str]
    recommendations: list[Recommendation]
    meta: dict[str, Any]
