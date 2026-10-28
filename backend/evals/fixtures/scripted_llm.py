"""Scripted fake LLMClient for offline evals (eval.md section 7.1).

A golden query's optional "script" field picks the behavior. The default, "grounded", answers like a
well-behaved model; the others force the parser and fallback paths without any network call.
"""

from __future__ import annotations

import json

from app.models.llm import CallStats, FilterDecision, LLMError, LLMResult, Preferences
from app.models.recommendation import RecommendationRequest
from app.models.restaurant import Restaurant
from app.services.filter import filter_candidates
from app.services.parser import parse_llm_output
from app.services.prompt import build_messages, result_count

FOREIGN_ID = "truffles-koramangala"
SCRIPTS = {"grounded", "overflow", "foreign_id", "messy", "invalid_json", "empty", "timeout", "rate_limit"}


def _explanation(restaurant: Restaurant) -> str:
    votes = f" from {restaurant.votes:,} votes" if restaurant.votes else ""
    return f"{restaurant.name} is rated {restaurant.rating:.1f}{votes} at ₹{restaurant.cost_for_two:,} for two."


def _items(restaurants: list[Restaurant], first_rank: int = 1) -> list[dict]:
    return [
        {"restaurant_id": restaurant.id, "rank": rank, "explanation": _explanation(restaurant)}
        for rank, restaurant in enumerate(restaurants, start=first_rank)
    ]


class ScriptedLLMClient:
    model = "scripted-offline"

    def __init__(self, script: str | None = None):
        script = script or "grounded"
        if script not in SCRIPTS:
            raise ValueError(f"Unknown script '{script}'; expected one of {sorted(SCRIPTS)}")
        self.script = script
        self.calls = 0

    def interpret(
        self,
        request: RecommendationRequest,
        vocab: dict,
        stats: CallStats | None = None,
    ) -> FilterDecision:
        if self.script == "timeout":
            raise LLMError("timeout", "scripted timeout")
        if self.script == "rate_limit":
            raise LLMError("rate_limit", "scripted 429 after one retry")
        frame = vocab.get("_frame")
        if frame is None:
            return FilterDecision(
                cuisines=list(request.cuisines),
                budget=request.budget.value if request.budget else None,
                min_rating=request.min_rating,
            )
        result = filter_candidates(
            frame,
            location=request.location,
            budget=request.budget,
            cuisines=list(request.cuisines),
            min_rating=request.min_rating,
            top_k=request.top_k,
            max_candidates=10_000,
        )
        budgets = list(result.budgets) if result.budgets else []
        return FilterDecision(
            cuisines=list(result.cuisines),
            budget=budgets[0] if len(budgets) == 1 else None,
            budgets=budgets,
            min_rating=result.min_rating,
            relaxations=list(result.relaxations),
        )

    def rank(self, preferences: Preferences, candidates: list[Restaurant], stats: CallStats | None = None) -> LLMResult:
        stats = stats if stats is not None else CallStats()
        self.calls += 1
        stats.attempts = 1
        count = stats.requested_count = result_count(preferences, candidates)
        messages = build_messages(preferences, candidates)
        stats.prompt_tokens = sum(len(message["content"]) for message in messages) // 4
        if self.script == "timeout":
            raise LLMError("timeout", "scripted timeout")
        if self.script == "rate_limit":
            stats.attempts = 2
            raise LLMError("rate_limit", "scripted 429 after one retry")
        raw = self._reply(preferences, candidates, count)
        stats.raw_output = raw
        stats.finish_reason = "stop"
        stats.completion_tokens = len(raw) // 4
        stats.reasoning_tokens = 0
        return parse_llm_output(raw)

    def _reply(self, preferences: Preferences, candidates: list[Restaurant], count: int) -> str:
        if self.script == "invalid_json":
            return '{"recommendations": [{"restaurant_id": '
        if self.script == "empty":
            return json.dumps({"recommendations": [], "summary": "Nothing fits."})
        best = sorted(candidates, key=lambda restaurant: -restaurant.rating)
        picks = best[:count]
        summary = f"{picks[0].name} leads these {len(picks)} picks in {preferences.location}."
        if self.script == "overflow":
            items = _items(best)
        elif self.script == "foreign_id":
            foreign = {"restaurant_id": FOREIGN_ID, "rank": 1, "explanation": "Famous for its burgers."}
            items = [foreign] + _items(picks, first_rank=2)
            summary = "Truffles in Koramangala tops the list."
        elif self.script == "messy":
            items = _items(picks[:3])
            items[0]["restaurant_id"] = f"  {picks[0].id.upper()} "
            items.insert(1, {**_items(picks[:1])[0], "rank": 3})
            if len(items) > 2:
                items[2] = {**items[2], "rank": 5, "explanation": ""}
        else:
            items = _items(picks)
        return json.dumps({"recommendations": items, "summary": summary}, ensure_ascii=False)
