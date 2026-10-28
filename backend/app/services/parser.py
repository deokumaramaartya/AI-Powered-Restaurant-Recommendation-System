from __future__ import annotations

import json
import re
from dataclasses import dataclass

from pydantic import ValidationError

from app.models.llm import LLMParseError, LLMResult
from app.models.recommendation import Recommendation
from app.models.restaurant import Restaurant
from app.services.fallback import explain

MAX_EXPLANATION_CHARS = 400

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_SENTENCE_END = re.compile(r"[.!?](?=\s|$)")


@dataclass
class Ranking:
    recommendations: list[Recommendation]
    summary: str | None
    dropped_ids: list[str]
    backfilled: int


def _json_text(text: str) -> str:
    stripped = text.strip()
    fenced = _FENCE.search(stripped)
    if fenced:
        stripped = fenced.group(1).strip()
    if not stripped.startswith("{"):
        start, end = stripped.find("{"), stripped.rfind("}")
        if start != -1 and end > start:
            stripped = stripped[start : end + 1]
    return stripped


def parse_llm_output(text: str | None) -> LLMResult:
    """Turn the raw LLM reply into an LLMResult, or raise LLMParseError."""
    if not text or not text.strip():
        raise LLMParseError("empty response")
    try:
        data = json.loads(_json_text(text))
    except json.JSONDecodeError as exc:
        raise LLMParseError(f"invalid JSON: {exc.msg}") from exc
    if not isinstance(data, dict):
        raise LLMParseError("response is not a JSON object")
    try:
        return LLMResult.model_validate(data)
    except ValidationError as exc:
        raise LLMParseError(f"response does not match the schema ({exc.error_count()} errors)") from exc


def _id_key(value: str) -> str:
    return " ".join(str(value).split()).casefold()


def _trim(text: str) -> str:
    if len(text) <= MAX_EXPLANATION_CHARS:
        return text
    ends = [match.end() for match in _SENTENCE_END.finditer(text) if match.end() <= MAX_EXPLANATION_CHARS]
    if ends:
        return text[: ends[-1]]
    return text[: MAX_EXPLANATION_CHARS - 1].rsplit(" ", 1)[0].rstrip() + "…"


def _explanation(text: str, restaurant: Restaurant, cuisines: list[str]) -> str:
    cleaned = " ".join(text.split())
    return _trim(cleaned) if cleaned else explain(restaurant, cuisines)


def finalize(result: LLMResult, candidates: list[Restaurant], top_k: int, cuisines: list[str]) -> Ranking:
    """Keep only candidate picks, order and renumber them, and top up a short list from the pre-sort."""
    by_key = {_id_key(restaurant.id): restaurant for restaurant in candidates}
    by_name: dict[str, Restaurant | None] = {}
    for restaurant in candidates:
        name = _id_key(restaurant.name)
        by_name[name] = None if name in by_name else restaurant

    def match(entry) -> Restaurant | None:
        # The explanation is written about the named restaurant, so a unique name beats a mistyped id.
        named = by_name.get(_id_key(entry.name)) if entry.name else None
        return named or by_key.get(_id_key(entry.restaurant_id)) or by_name.get(_id_key(entry.restaurant_id))

    ordered = list(enumerate(result.recommendations))
    if all(entry.rank is not None for _, entry in ordered):
        ordered.sort(key=lambda pair: (pair[1].rank, pair[0]))

    picks: list[tuple[Restaurant, str]] = []
    seen: set[str] = set()
    dropped: list[str] = []
    for _, entry in ordered:
        restaurant = match(entry)
        if restaurant is None:
            dropped.append(entry.restaurant_id)
            continue
        if restaurant.id in seen:
            continue
        seen.add(restaurant.id)
        picks.append((restaurant, _explanation(entry.explanation, restaurant, cuisines)))
    if not picks:
        raise LLMParseError("no recommendation matched a candidate")

    picks = picks[:top_k]
    wanted = min(top_k, len(candidates))
    backfilled = 0
    for restaurant in candidates:
        if len(picks) >= wanted:
            break
        if restaurant.id not in seen:
            seen.add(restaurant.id)
            picks.append((restaurant, explain(restaurant, cuisines)))
            backfilled += 1

    summary = (result.summary or "").strip() or None
    return Ranking(
        recommendations=[
            Recommendation(rank=rank, restaurant=restaurant, explanation=text)
            for rank, (restaurant, text) in enumerate(picks, start=1)
        ],
        summary=None if dropped else summary,
        dropped_ids=dropped,
        backfilled=backfilled,
    )
