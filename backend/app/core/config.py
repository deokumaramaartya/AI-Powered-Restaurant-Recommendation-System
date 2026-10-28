from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]

# The largest top_k a request may ask for; MAX_CANDIDATES must be able to fill it.
MAX_TOP_K = 10


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", env_file_encoding="utf-8", extra="ignore")

    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-120b"
    llm_temperature: float = Field(0.6, ge=0, le=2)
    llm_reasoning_effort: Literal["low", "medium", "high"] = "low"
    # Reasoning tokens count toward this cap; 65,536 is the model's output limit.
    # 1024 fits low-effort reasoning plus up to 10 short explanations on the free tier.
    llm_max_completion_tokens: int = Field(1024, gt=0, le=65536)
    llm_timeout_seconds: float = Field(6, gt=0)
    llm_deadline_seconds: float = Field(8, gt=0)
    # 10 matches max top_k and keeps prompt tokens inside the free-tier TPM/TPD budget.
    max_candidates: int = Field(10, ge=MAX_TOP_K)
    budget_low_max: int = Field(300, gt=0)
    budget_medium_max: int = Field(600, gt=0)
    data_path: Path = Path("../data/restaurants.parquet")
    cors_origins: str = "http://localhost:5173"
    log_level: str = "INFO"
    # Used only by the eval tool's AI judge (backend/evals/judge.py), never by the app.
    eval_judge_model: str = "qwen/qwen3.8-27b"

    @field_validator("groq_api_key")
    @classmethod
    def _strip_spaces_and_quotes(cls, value: str) -> str:
        return value.strip().strip("\"'").strip()

    @field_validator("data_path")
    @classmethod
    def _resolve_against_backend_dir(cls, value: Path) -> Path:
        if value.is_absolute():
            return value
        return (BACKEND_DIR / value).resolve()

    @model_validator(mode="after")
    def _check_relationships(self) -> "Settings":
        if self.budget_low_max >= self.budget_medium_max:
            raise ValueError("BUDGET_LOW_MAX must be lower than BUDGET_MEDIUM_MAX")
        if self.llm_deadline_seconds < self.llm_timeout_seconds:
            raise ValueError("LLM_DEADLINE_SECONDS must be at least LLM_TIMEOUT_SECONDS")
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip().rstrip("/") for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
