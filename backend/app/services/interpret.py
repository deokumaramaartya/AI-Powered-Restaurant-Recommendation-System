"""Groq decides how extras map onto store fields; this module only applies that decision."""

from __future__ import annotations

import json

from app.models.llm import FilterDecision, LLMParseError
from app.models.recommendation import RecommendationRequest
from app.models.restaurant import Restaurant

INTERPRET_SYSTEM = """You map a Bangalore diner's request onto restaurant fields that exist in the dataset.

Rules:
1. Copy location, cuisines, budget, and min_rating from the request exactly. Never drop, ignore, replace, or relax a structured field to fill a result count. Location never changes.
2. Use only the allowed values listed in the user message. Do not invent values (parking, rooftop, quiet, hours).
3. You may add a cuisine only if the extra text names one that is in the allowed cuisine list and the request left cuisine empty.
4. Interpret every extra wish. Map each one onto the nearest allowed rest_type, listing_type, liked-dish idea, table booking, or online ordering. Cozy, intimate, or sit-down dining maps to Casual Dining.
5. Wishes you cannot confirm from the allowed values go in unmapped as short labels. Do not put them in rest_types.
6. Leave relaxations as an empty list. The diner decides whether to drop a filter.
7. Reply with one JSON object only, no markdown."""

_SCHEMA = (
    '{"cuisines":[],"budget":null,"budgets":[],"min_rating":3.5,"rest_types":[],'
    '"listing_types":[],"book_table":null,"online_order":null,"dishes":[],'
    '"unmapped":[],"relaxations":[]}'
)


def store_vocab(store) -> dict:
    rest_types = sorted({str(value) for value in store.frame["rest_type"] if value})
    listing_types = sorted(
        {str(item) for row in store.frame["listing_types"] for item in row if item}
    )
    return {
        "rest_types": rest_types,
        "listing_types": listing_types,
        "cuisines": list(store.meta()["cuisines"]),
    }


def parse_filter_decision(text: str | None) -> FilterDecision:
    try:
        data = json.loads(text or "")
    except json.JSONDecodeError as error:
        raise LLMParseError("filter decision was not JSON") from error
    if not isinstance(data, dict):
        raise LLMParseError("filter decision was not an object")
    return FilterDecision.model_validate(data)


def _fold(value: str | None) -> str:
    return " ".join((value or "").split()).casefold()


def _allowed(values: list[str], vocab: list[str]) -> list[str]:
    known = {_fold(item): item for item in vocab}
    kept: list[str] = []
    seen: set[str] = set()
    for value in values:
        canonical = known.get(_fold(value))
        if canonical and canonical not in seen:
            seen.add(canonical)
            kept.append(canonical)
    return kept


def sanitize_decision(decision: FilterDecision, vocab: dict) -> FilterDecision:
    return decision.model_copy(
        update={
            "rest_types": _allowed(decision.rest_types, vocab.get("rest_types", [])),
            "listing_types": _allowed(decision.listing_types, vocab.get("listing_types", [])),
            "cuisines": _allowed(decision.cuisines, vocab.get("cuisines", [])),
            "dishes": [item for item in decision.dishes if _fold(item)],
            "budget": decision.budget if decision.budget in {"low", "medium", "high"} else None,
            "budgets": [item for item in decision.budgets if item in {"low", "medium", "high"}],
        }
    )


def constrain_decision(
    decision: FilterDecision,
    request: RecommendationRequest,
    counts: dict | None,
    resolved_cuisines: list[str],
) -> FilterDecision:
    """Keep every structured field the diner set. Groq may only map extras."""
    del counts
    extra_cuisines = [] if resolved_cuisines else list(decision.cuisines)
    budget = request.budget.value if request.budget else None
    return decision.model_copy(
        update={
            "cuisines": list(resolved_cuisines) or extra_cuisines,
            "budget": budget,
            "budgets": [budget] if budget else [],
            "min_rating": request.min_rating,
            "relaxations": [],
        }
    )


def build_interpret_messages(request: RecommendationRequest, vocab: dict) -> list[dict]:
    extra = request.extra_preferences or "none"
    counts = vocab.get("counts") or {}
    count_lines = [f"- {key}: {value}" for key, value in counts.items()]
    user = "\n".join(
        [
            "Allowed values (use these exact strings):",
            f"- rest_types: {', '.join(vocab.get('rest_types', [])) or 'none'}",
            f"- listing_types: {', '.join(vocab.get('listing_types', [])) or 'none'}",
            f"- cuisines: {', '.join(vocab.get('cuisines', [])) or 'none'}",
            "",
            "Request:",
            f"- location: {request.location}",
            f"- budget: {request.budget.value if request.budget else 'any'}",
            f"- cuisines: {', '.join(request.cuisines) if request.cuisines else 'any'}",
            f"- min_rating: {request.min_rating}",
            f"- top_k: {request.top_k}",
            f"- extra: {extra}",
            *(["", "Match counts in this locality:", *count_lines] if count_lines else []),
            "",
            "Return JSON:",
            _SCHEMA,
        ]
    )
    return [{"role": "system", "content": INTERPRET_SYSTEM}, {"role": "user", "content": user}]


def _type_hit(restaurant: Restaurant, wanted: list[str]) -> list[str]:
    haystack = _fold(restaurant.rest_type)
    return [name for name in wanted if _fold(name) in haystack]


def _list_hit(values: list[str], wanted: list[str]) -> list[str]:
    haystacks = [_fold(item) for item in values]
    return [name for name in wanted if any(_fold(name) in item or item in _fold(name) for item in haystacks)]


def matched_labels(restaurant: Restaurant, decision: FilterDecision) -> list[str]:
    labels: list[str] = []
    labels.extend(_type_hit(restaurant, decision.rest_types))
    labels.extend(_list_hit(restaurant.listing_types, decision.listing_types))
    labels.extend(_list_hit(restaurant.dish_liked, decision.dishes))
    if decision.book_table is True and restaurant.book_table:
        labels.append("table booking")
    if decision.online_order is True and restaurant.online_order:
        labels.append("online ordering")
    return labels


def apply_decision(restaurants: list[Restaurant], decision: FilterDecision) -> list[Restaurant]:
    """Soft-rank by how many decided fields a restaurant confirms. Never drops a restaurant."""
    if not any(
        [
            decision.rest_types,
            decision.listing_types,
            decision.dishes,
            decision.book_table is True,
            decision.online_order is True,
        ]
    ):
        return list(restaurants)
    return sorted(restaurants, key=lambda restaurant: -len(matched_labels(restaurant, decision)))
