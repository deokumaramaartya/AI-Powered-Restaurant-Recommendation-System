from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Literal, overload

from app.core.config import Settings
from app.models.llm import CallStats, FilterDecision, LLMError, Preferences
from app.models.recommendation import RecommendationRequest, RecommendationResponse
from app.models.restaurant import Restaurant
from app.services.fallback import rank_fallback
from app.services.filter import FilterResult, apply_filter_decision, filter_candidates, match_counts
from app.services.interpret import apply_decision, constrain_decision, sanitize_decision, store_vocab
from app.services.parser import Ranking, finalize
from app.services.soft_prefs import rank_by_soft_prefs
from app.services.store import RestaurantStore

if TYPE_CHECKING:
    from app.services.llm_groq import LLMClient

logger = logging.getLogger(__name__)

# Error type for request values that are well-formed but not in the data; their messages are kept as written.
UNKNOWN_VALUE = "unknown_value"
CACHE_TTL_SECONDS = 3600.0
CACHE_MAX_ENTRIES = 256

CacheKey = tuple


def _normalized(text: str) -> str:
    return " ".join(text.split()).casefold()


def cache_key(request: RecommendationRequest) -> CacheKey:
    """Requests that differ only in cuisine order, casing, or whitespace share a key."""
    return (
        _normalized(request.location),
        request.budget.value if request.budget else None,
        tuple(sorted(_normalized(cuisine) for cuisine in request.cuisines)),
        request.min_rating,
        _normalized(request.extra_preferences) if request.extra_preferences else None,
        request.top_k,
    )


class ResponseCache:
    """In-process LRU cache of LLM-ranked responses (architecture 9). Stores and returns copies."""

    def __init__(
        self,
        ttl_seconds: float = CACHE_TTL_SECONDS,
        max_entries: int = CACHE_MAX_ENTRIES,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._ttl = ttl_seconds
        self._max_entries = max_entries
        self._clock = clock
        self._entries: OrderedDict[CacheKey, tuple[float, RecommendationResponse]] = OrderedDict()
        self._lock = threading.Lock()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def get(self, key: CacheKey) -> RecommendationResponse | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            stored_at, response = entry
            if self._clock() - stored_at > self._ttl:
                del self._entries[key]
                return None
            self._entries.move_to_end(key)
        return response.model_copy(deep=True)

    def put(self, key: CacheKey, response: RecommendationResponse) -> None:
        snapshot = response.model_copy(deep=True)
        with self._lock:
            self._entries[key] = (self._clock(), snapshot)
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)


class InvalidPreferences(Exception):
    """Request values that passed schema validation but do not exist in the data."""

    def __init__(self, errors: list[dict]):
        super().__init__(errors)
        self.errors = errors


@dataclass
class Trace:
    """Internal details for evals; never included in API responses."""

    location: str
    cuisines: list[str]
    filter: FilterResult | None
    cached: bool = False
    llm_called: bool = False
    llm_error: str | None = None
    model: str | None = None
    stats: CallStats | None = None
    raw_ids: list[str] = field(default_factory=list)
    dropped_ids: list[str] = field(default_factory=list)
    backfilled: int = 0
    timings_ms: dict[str, float] = field(default_factory=dict)

    @property
    def candidate_ids(self) -> list[str]:
        return self.filter.candidate_ids if self.filter is not None else []


def _field_error(field_name: str, message: str, value) -> dict:
    return {"type": UNKNOWN_VALUE, "loc": ["body", field_name], "msg": message, "input": value}


def log_fields(request: RecommendationRequest, response: RecommendationResponse, details: Trace) -> dict:
    """What one request log line records. Free text is logged by length only (SEC-03)."""
    stats = details.stats
    return {
        "location": details.location,
        "budget": request.budget.value if request.budget else None,
        "cuisines": len(details.cuisines),
        "min_rating": request.min_rating,
        "top_k": request.top_k,
        "extra_preferences_chars": len(request.extra_preferences or ""),
        "candidates": response.meta.get("candidates_considered"),
        "relaxations": response.relaxations,
        "results": len(response.recommendations),
        "source": response.source,
        "cached": details.cached,
        "model": response.meta.get("model"),
        "llm_ms": round(details.timings_ms.get("llm_ms", 0.0)) if details.llm_called else None,
        "llm_error": details.llm_error,
        "llm_attempts": stats.attempts if stats else None,
        "prompt_tokens": stats.prompt_tokens if stats else None,
        "completion_tokens": stats.completion_tokens if stats else None,
        "reasoning_tokens": stats.reasoning_tokens if stats else None,
    }


def resolve_request(request: RecommendationRequest, store: RestaurantStore) -> tuple[str, list[str]]:
    errors = []
    location = store.resolve_location(request.location)
    if location is None:
        errors.append(
            _field_error(
                "location",
                f"Unknown location '{request.location}'. The data covers Bangalore localities only; "
                "pick one from /api/meta.",
                request.location,
            )
        )
    cuisines, unknown = store.resolve_cuisines(request.cuisines)
    if unknown:
        errors.append(
            _field_error(
                "cuisines",
                "Unknown cuisine(s): " + ", ".join(unknown) + ". Pick values from /api/meta.",
                unknown,
            )
        )
    if errors:
        raise InvalidPreferences(errors)
    return location, cuisines


def _budget_label(decision: FilterDecision | None, request: RecommendationRequest) -> str | None:
    if decision is None:
        return request.budget.value if request.budget else None
    if decision.budgets:
        return ", ".join(decision.budgets)
    return decision.budget


def _restaurants_for(store: RestaurantStore, restaurant_ids: list[str]) -> list[Restaurant]:
    return [store.get(restaurant_id) for restaurant_id in restaurant_ids]


def _code_filter(
    request: RecommendationRequest,
    store: RestaurantStore,
    location: str,
    cuisines: list[str],
    max_candidates: int,
) -> tuple[FilterResult, list[Restaurant], FilterDecision | None]:
    result = filter_candidates(
        store.frame,
        location=location,
        budget=request.budget,
        cuisines=cuisines,
        min_rating=request.min_rating,
        top_k=request.top_k,
        max_candidates=max_candidates,
    )
    candidates = rank_by_soft_prefs(_restaurants_for(store, result.candidate_ids), request.extra_preferences)
    return result, candidates, None


def _cap_result(result: FilterResult, restaurants: list[Restaurant]) -> FilterResult:
    return FilterResult(
        candidate_ids=[restaurant.id for restaurant in restaurants],
        relaxations=result.relaxations,
        min_rating=result.min_rating,
        cuisines=result.cuisines,
        budgets=result.budgets,
        message=result.message if not restaurants else None,
        matched_before_cap=result.matched_before_cap,
    )


def _llm_filter(
    llm: LLMClient,
    request: RecommendationRequest,
    store: RestaurantStore,
    location: str,
    cuisines: list[str],
    max_candidates: int,
    details: Trace,
) -> tuple[FilterResult, list[Restaurant], FilterDecision | None]:
    vocab = store_vocab(store)
    vocab["counts"] = match_counts(
        store.frame,
        location=location,
        budget=request.budget,
        cuisines=cuisines,
        min_rating=request.min_rating,
        top_k=request.top_k,
    )
    vocab["_frame"] = store.frame
    interpreted = request.model_copy(update={"location": location, "cuisines": cuisines})
    try:
        decision = constrain_decision(
            sanitize_decision(llm.interpret(interpreted, vocab), vocab),
            interpreted,
            vocab.get("counts"),
            cuisines,
        )
    except LLMError as error:
        details.llm_error = error.kind
        logger.warning(
            "LLM filter decision failed (%s: %s) with model %s; using the code filter",
            error.kind,
            error,
            llm.model,
        )
        return _code_filter(request, store, location, cuisines, max_candidates)
    except Exception as error:
        details.llm_error = "unexpected"
        logger.error(
            "Unexpected %s in the LLM filter decision with model %s; using the code filter",
            type(error).__name__,
            llm.model,
        )
        return _code_filter(request, store, location, cuisines, max_candidates)
    result = apply_filter_decision(store.frame, location=location, decision=decision)
    candidates = apply_decision(_restaurants_for(store, result.candidate_ids), decision)[:max_candidates]
    return _cap_result(result, candidates), candidates, decision


def _rank_with_llm(
    llm: LLMClient, preferences: Preferences, candidates: list[Restaurant], details: Trace
) -> Ranking | None:
    details.llm_called = True
    details.stats = CallStats()
    try:
        result = llm.rank(preferences, candidates, details.stats)
        details.raw_ids = [entry.restaurant_id for entry in result.recommendations]
        ranking = finalize(result, candidates, preferences.top_k, preferences.cuisines)
    except LLMError as error:
        details.llm_error = error.kind
        logger.warning("LLM step failed (%s: %s) with model %s; using the fallback ranker", error.kind, error, llm.model)
        return None
    except Exception as error:
        details.llm_error = "unexpected"
        logger.error("Unexpected %s in the LLM step with model %s; using the fallback ranker", type(error).__name__, llm.model)
        return None
    details.dropped_ids = ranking.dropped_ids
    details.backfilled = ranking.backfilled
    return ranking


@overload
def recommend(
    request: RecommendationRequest,
    store: RestaurantStore,
    settings: Settings,
    *,
    llm: LLMClient | None = None,
    cache: ResponseCache | None = None,
    use_cache: bool = True,
    trace: Literal[False] = False,
) -> RecommendationResponse: ...


@overload
def recommend(
    request: RecommendationRequest,
    store: RestaurantStore,
    settings: Settings,
    *,
    llm: LLMClient | None = None,
    cache: ResponseCache | None = None,
    use_cache: bool = True,
    trace: Literal[True],
) -> tuple[RecommendationResponse, Trace]: ...


def recommend(request, store, settings, *, llm=None, cache=None, use_cache=True, trace=False):
    started = time.perf_counter()
    location, cuisines = resolve_request(request, store)
    key = cache_key(request) if cache is not None and use_cache else None
    if key is not None:
        hit = cache.get(key)
        if hit is not None:
            elapsed_ms = (time.perf_counter() - started) * 1000
            hit.meta.update(cached=True, latency_ms=round(elapsed_ms))
            if not trace:
                return hit
            details = Trace(location=location, cuisines=cuisines, filter=None, cached=True, model=hit.meta.get("model"))
            details.timings_ms = {"filter_ms": 0.0, "llm_ms": 0.0, "total_ms": elapsed_ms}
            return hit, details

    details = Trace(location=location, cuisines=cuisines, filter=None)
    if llm is not None and settings.groq_api_key:
        result, candidates, decision = _llm_filter(
            llm, request, store, location, cuisines, settings.max_candidates, details
        )
    else:
        result, candidates, decision = _code_filter(
            request, store, location, cuisines, settings.max_candidates
        )
    details.filter = result
    filtered = time.perf_counter()

    ranking = None
    if llm is not None and settings.groq_api_key and candidates and details.llm_error is None:
        preferences = Preferences(
            location=location,
            budget=_budget_label(decision, request),
            cuisines=decision.cuisines if decision is not None else cuisines,
            min_rating=(
                decision.min_rating
                if decision is not None and decision.min_rating is not None
                else request.min_rating
            ),
            extra_preferences=request.extra_preferences,
            top_k=request.top_k,
            relaxations=result.relaxations,
            mapped=decision,
        )
        ranking = _rank_with_llm(llm, preferences, candidates, details)
    ranked = time.perf_counter()

    if ranking is not None:
        details.model = llm.model
        source, summary, recommendations = "llm", ranking.summary, ranking.recommendations
    else:
        source, summary, recommendations = (
            "fallback",
            None,
            rank_fallback(candidates, request.top_k, cuisines, request.extra_preferences),
        )
    finished = time.perf_counter()
    response = RecommendationResponse(
        source=source,
        summary=summary,
        relaxations=result.relaxations,
        recommendations=recommendations,
        meta={
            "candidates_considered": len(result.candidate_ids),
            "model": details.model,
            "latency_ms": round((finished - started) * 1000),
            "message": result.message,
            "cached": False,
        },
    )
    if key is not None and source == "llm" and recommendations:
        cache.put(key, response)
    if not trace:
        return response
    details.timings_ms = {
        "filter_ms": (filtered - started) * 1000,
        "llm_ms": (ranked - filtered) * 1000,
        "total_ms": (finished - started) * 1000,
    }
    return response, details
