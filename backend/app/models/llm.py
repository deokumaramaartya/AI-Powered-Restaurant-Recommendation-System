from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, field_validator


class LLMRecommendation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    restaurant_id: str
    name: str | None = None
    rank: int | None = None
    explanation: str = ""

    @field_validator("restaurant_id", mode="before")
    @classmethod
    def _number_id_as_text(cls, value):
        if isinstance(value, int) and not isinstance(value, bool):
            return str(value)
        return value

    @field_validator("name", mode="before")
    @classmethod
    def _unusable_name_is_none(cls, value):
        return value if isinstance(value, str) else None

    @field_validator("rank", mode="before")
    @classmethod
    def _unusable_rank_is_none(cls, value):
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, str) and value.strip().isdigit():
            return int(value)
        return None


class LLMResult(BaseModel):
    """The JSON object the LLM is asked to return (architecture 3.4)."""

    model_config = ConfigDict(extra="ignore")

    recommendations: list[LLMRecommendation]
    summary: str | None = None


class FilterDecision(BaseModel):
    """Groq's mapping of a request onto fields that exist in the store."""

    model_config = ConfigDict(extra="ignore")

    cuisines: list[str] = []
    budget: str | None = None
    budgets: list[str] = []
    min_rating: float | None = None
    rest_types: list[str] = []
    listing_types: list[str] = []
    book_table: bool | None = None
    online_order: bool | None = None
    dishes: list[str] = []
    unmapped: list[str] = []
    relaxations: list[str] = []


@dataclass(frozen=True)
class Preferences:
    """What the user asked for, after resolving names against the data."""

    location: str
    budget: str | None
    cuisines: list[str]
    min_rating: float
    extra_preferences: str | None
    top_k: int
    relaxations: list[str] = field(default_factory=list)
    mapped: FilterDecision | None = None


@dataclass
class CallStats:
    """Filled in by an LLMClient during one rank() call, for traces and evals."""

    attempts: int = 0
    requested_count: int | None = None
    raw_output: str | None = None
    finish_reason: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    reasoning_tokens: int | None = None


class LLMError(Exception):
    """Any failure of the LLM step. The recommender answers with the fallback ranker instead."""

    def __init__(self, kind: str, message: str = ""):
        super().__init__(message or kind)
        self.kind = kind


class LLMParseError(LLMError):
    """The LLM answered, but not with usable JSON."""

    def __init__(self, message: str):
        super().__init__("invalid_json", message)
