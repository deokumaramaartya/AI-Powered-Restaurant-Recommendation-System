from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import BACKEND_DIR, Settings


def build(**values) -> Settings:
    return Settings(_env_file=None, **values)


def test_groq_api_key_defaults_to_empty(monkeypatch):
    """A required key would stop the app from starting in fallback-only mode."""
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert build().groq_api_key == ""


@pytest.mark.parametrize("raw", ['  gsk_abc  ', '"gsk_abc"', "'gsk_abc'", ' " gsk_abc " '])
def test_groq_api_key_is_trimmed_of_spaces_and_quotes(raw):
    """A pasted key with quotes or a trailing space would fail with 401 on every call (CFG-09)."""
    assert build(groq_api_key=raw).groq_api_key == "gsk_abc"


def test_llm_defaults_target_gpt_oss_with_low_reasoning(monkeypatch):
    """Without .env the app must still call the planned model with latency-safe settings."""
    for name in ("GROQ_MODEL", "LLM_TEMPERATURE", "LLM_REASONING_EFFORT", "LLM_MAX_COMPLETION_TOKENS"):
        monkeypatch.delenv(name, raising=False)
    settings = build()
    assert settings.groq_model == "openai/gpt-oss-120b"
    assert settings.llm_temperature == 0.6
    assert settings.llm_reasoning_effort == "low"
    assert settings.llm_max_completion_tokens == 1024


def test_env_example_lists_every_setting():
    """A setting missing from .env.example would be invisible to anyone setting up the app."""
    example = (BACKEND_DIR / ".env.example").read_text(encoding="utf-8")
    keys = {line.split("=", 1)[0].strip() for line in example.splitlines() if "=" in line and not line.startswith("#")}
    assert keys == {name.upper() for name in Settings.model_fields}


@pytest.mark.parametrize(
    ("low", "medium"),
    [(600, 600), (700, 600), (-1, 600), (0, 600)],
)
def test_budget_thresholds_must_be_positive_and_increasing(low, medium):
    """Overlapping or negative tiers would make medium empty or swallow every cost."""
    with pytest.raises(ValidationError):
        build(budget_low_max=low, budget_medium_max=medium)


def test_max_candidates_must_cover_the_largest_top_k():
    """With fewer than 10 candidates a top_k of 10 could never be filled."""
    with pytest.raises(ValidationError):
        build(max_candidates=9)
    assert build(max_candidates=10).max_candidates == 10


@pytest.mark.parametrize(
    "values",
    [
        {"llm_temperature": 2.5},
        {"llm_temperature": -0.1},
        {"llm_timeout_seconds": 0},
        {"llm_timeout_seconds": 6, "llm_deadline_seconds": 5},
        {"llm_reasoning_effort": "extreme"},
        {"llm_reasoning_effort": "LOW"},
        {"llm_max_completion_tokens": 0},
        {"llm_max_completion_tokens": 70000},
    ],
)
def test_llm_settings_out_of_range_are_rejected(values):
    """A zero timeout, a deadline shorter than one call, or a value Groq rejects would break the LLM step."""
    with pytest.raises(ValidationError):
        build(**values)


def test_relative_data_path_resolves_against_backend_dir(monkeypatch, tmp_path):
    """Starting the server from another folder must not move the dataset."""
    monkeypatch.chdir(tmp_path)
    settings = build(data_path="../data/restaurants.parquet")
    assert settings.data_path == (BACKEND_DIR / ".." / "data" / "restaurants.parquet").resolve()


def test_absolute_data_path_is_kept(tmp_path):
    """An absolute path from the environment must be used as given."""
    target = tmp_path / "custom.parquet"
    assert build(data_path=str(target)).data_path == Path(target)


def test_cors_origins_split_on_commas_and_trimmed():
    """An untrimmed entry would never match the browser's Origin header."""
    settings = build(cors_origins=" http://localhost:5173/ , https://app.vercel.app/,")
    assert settings.cors_origin_list == ["http://localhost:5173", "https://app.vercel.app"]
