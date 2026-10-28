from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

from app.models.llm import FilterDecision
from app.models.restaurant import BudgetTier

RATING_STEP = 0.5
RATING_FLOOR = 3.0

_WIDENED = {
    "low": ("low", "medium"),
    "medium": ("low", "medium", "high"),
    "high": ("medium", "high"),
}


@dataclass
class FilterResult:
    candidate_ids: list[str]
    relaxations: list[str]
    min_rating: float
    cuisines: list[str]
    budgets: tuple[str, ...] | None
    message: str | None = None
    matched_before_cap: int = 0


def _format_rating(value: float) -> str:
    text = f"{value:.2f}".rstrip("0")
    return text + "0" if text.endswith(".") else text


def _join(items) -> str:
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _matching(base: pd.DataFrame, min_rating: float, cuisine_keys: frozenset[str] | None, budgets) -> pd.DataFrame:
    mask = base["rating"] >= min_rating
    if budgets is not None:
        mask &= base["budget_tier"].isin(budgets)
    if cuisine_keys is not None:
        mask &= base["cuisine_keys"].map(lambda keys: not keys.isdisjoint(cuisine_keys))
    return base[mask]


def _rank(matches: pd.DataFrame) -> pd.DataFrame:
    ranked = matches.assign(_score=[r * math.log1p(v) for r, v in zip(matches["rating"], matches["votes"], strict=True)])
    return ranked.sort_values(
        ["_score", "rating", "votes", "name", "id"],
        ascending=[False, False, False, True, True],
        kind="mergesort",
    )


def match_counts(
    frame: pd.DataFrame,
    *,
    location: str,
    budget: BudgetTier | str | None,
    cuisines: list[str],
    min_rating: float,
    top_k: int = 5,
) -> dict[str, int]:
    """How many rows fit each constraint. The LLM uses these counts to decide relaxations."""
    base = frame[frame["location_key"] == location.casefold()]
    budget_value = BudgetTier(budget).value if budget is not None else None
    budgets: tuple[str, ...] | None = (budget_value,) if budget_value else None
    cuisine_keys = frozenset(item.casefold() for item in cuisines) or None
    lowered = max(RATING_FLOOR, round(min_rating - RATING_STEP, 2))
    widened = _WIDENED[budget_value] if budget_value else budgets
    return {
        "top_k": int(top_k),
        "in_location": int(len(base)),
        "with_min_rating": int(len(_matching(base, min_rating, None, None))),
        "with_cuisines": int(len(_matching(base, 0.0, cuisine_keys, None))),
        "with_budget": int(len(_matching(base, 0.0, None, budgets))),
        "with_all_requested": int(len(_matching(base, min_rating, cuisine_keys, budgets))),
        "after_lowered_rating": int(len(_matching(base, lowered, cuisine_keys, budgets))),
        "after_drop_cuisine": int(len(_matching(base, lowered, None, budgets))),
        "after_widen_budget": int(len(_matching(base, lowered, None, widened))),
        "with_rating_lowered": int(len(_matching(base, lowered, cuisine_keys, budgets))),
        "without_cuisine": int(len(_matching(base, min_rating, None, budgets))),
        "without_budget": int(len(_matching(base, min_rating, cuisine_keys, None))),
    }


def apply_filter_decision(
    frame: pd.DataFrame,
    *,
    location: str,
    decision: FilterDecision,
    max_candidates: int | None = None,
) -> FilterResult:
    """Apply the LLM's chosen filters. Does not decide relaxations."""
    base = frame[frame["location_key"] == location.casefold()]
    min_rating = decision.min_rating if decision.min_rating is not None else 0.0
    cuisine_keys = frozenset(item.casefold() for item in decision.cuisines) or None
    allowed = {"low", "medium", "high"}
    chosen = [item for item in decision.budgets if item in allowed]
    if not chosen and decision.budget in allowed:
        chosen = [decision.budget]
    budgets: tuple[str, ...] | None = tuple(chosen) if chosen else None
    ranked = _rank(_matching(base, min_rating, cuisine_keys, budgets))
    candidate_ids = [str(value) for value in ranked["id"]]
    if max_candidates is not None:
        candidate_ids = candidate_ids[:max_candidates]
    message = None
    if not candidate_ids:
        message = (
            f"No restaurants in {location} match these filters. "
            "Drop a filter to search again, or try another locality."
        )
    return FilterResult(
        candidate_ids=candidate_ids,
        relaxations=[],
        min_rating=min_rating,
        cuisines=list(decision.cuisines),
        budgets=budgets,
        message=message,
        matched_before_cap=len(ranked),
    )


def filter_candidates(
    frame: pd.DataFrame,
    *,
    location: str,
    budget: BudgetTier | str | None,
    cuisines: list[str],
    min_rating: float,
    top_k: int,
    max_candidates: int,
) -> FilterResult:
    """Apply the diner's filters exactly. Never drop a filter to fill top_k."""
    del top_k
    budget_value = BudgetTier(budget).value if budget is not None else None
    return apply_filter_decision(
        frame,
        location=location,
        decision=FilterDecision(
            cuisines=list(cuisines),
            budget=budget_value,
            budgets=[budget_value] if budget_value else [],
            min_rating=min_rating,
        ),
        max_candidates=max_candidates,
    )
