"""Automated checks C-01 to C-13 (eval.md section 4.1). No AI involved."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable

from pydantic import ValidationError

from app.models.recommendation import RecommendationRequest, RecommendationResponse
from app.services.store import RestaurantStore

RATING_FLOOR = 3.0
MAX_EXPLANATION_CHARS = 400

# Re-derived here rather than imported from filter.py so the eval does not grade the code with itself.
_WIDENED_BUDGET = {"low": {"low", "medium"}, "medium": {"low", "medium", "high"}, "high": {"medium", "high"}}
_LOWERED_RATING = re.compile(r"^Lowered minimum rating from ([\d.]+) to ([\d.]+)$")
_SENTENCE_END = re.compile(r"[.!?](?:\s|$)")

RATING_TOLERANCE = 0.05
APPROXIMATE_VOTES = 0.10
DEADLINE_OVERHEAD_SECONDS = 0.5
_CURRENCY = r"(?:₹|rs\.?|inr)"
_NUMBER = re.compile(rf"(?<![\w./])({_CURRENCY}\s*)?(\d[\d,]*(?:\.\d+)?)(?![\d])", re.IGNORECASE)
_VOTES_AFTER = re.compile(r"^\+?\s*(?:votes?|reviews?|ratings)\b", re.IGNORECASE)
_FOR_TWO_AFTER = re.compile(r"^\s*(?:for\s+(?:two|2)\b)", re.IGNORECASE)
_RATING_AFTER = re.compile(r"^(?:\s*/\s*5|-?\s*(?:star|rated|rating))", re.IGNORECASE)
_RATING_BEFORE = re.compile(r"(?:rated|rating(?:\s+of)?)\s*$", re.IGNORECASE)
_JOIN = r"[\s\-\u2010\u2011]*"
_QUALIFIERS = {
    ">": re.compile(rf"(?:over|more\s+than|above|upwards\s+of){_JOIN}{_CURRENCY}?\s*$", re.IGNORECASE),
    "<": re.compile(rf"(?:under|below|less\s+than|within){_JOIN}{_CURRENCY}?\s*$", re.IGNORECASE),
    "~": re.compile(rf"(?:nearly|almost|about|around|roughly|approximately|close\s+to|~){_JOIN}{_CURRENCY}?\s*$", re.IGNORECASE),
}
_USER_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


@dataclass
class CheckResult:
    id: str
    status: str  # "pass", "fail", or "skip"
    detail: str = ""
    ok: int = 0
    total: int = 0

    @property
    def passed(self) -> bool:
        return self.status == "pass"


@dataclass
class CheckContext:
    query: dict
    request: RecommendationRequest
    expect: dict
    response: RecommendationResponse
    body: dict
    candidate_ids: list[str]
    llm_called: bool
    store: RestaurantStore
    secrets: list[str]
    llm_ms: float = 0.0
    deadline_seconds: float = 8.0
    log_text: str = ""


def _result(check_id: str, failures: list[str], ok: int = 0, total: int = 0) -> CheckResult:
    if failures:
        return CheckResult(check_id, "fail", "; ".join(failures[:5]), ok, total)
    return CheckResult(check_id, "pass", "", ok, total)


def _skip(check_id: str, reason: str) -> CheckResult:
    return CheckResult(check_id, "skip", reason)


def sentence_count(text: str) -> int:
    return len([part for part in _SENTENCE_END.split(text) if part.strip()])


def c01_schema(ctx: CheckContext) -> CheckResult:
    try:
        RecommendationResponse.model_validate(ctx.body)
    except ValidationError as exc:
        return _result("C-01", [f"response does not match schema: {exc.error_count()} errors"])
    return _result("C-01", [])


def c02_count(ctx: CheckContext) -> CheckResult:
    returned = len(ctx.response.recommendations)
    expected = min(ctx.request.top_k, len(ctx.candidate_ids))
    failures = []
    if returned != expected:
        failures.append(f"returned {returned}, expected min(top_k, candidates) = {expected}")
    if returned > ctx.request.top_k:
        failures.append(f"returned {returned}, more than top_k {ctx.request.top_k}")
    if "count" in ctx.expect and returned != ctx.expect["count"]:
        failures.append(f"returned {returned}, frozen count is {ctx.expect['count']}")
    if "ids" in ctx.expect:
        got = [item.restaurant.id for item in ctx.response.recommendations]
        if sorted(got) != sorted(ctx.expect["ids"]):
            failures.append(f"ids {got}, expected {ctx.expect['ids']}")
    return _result("C-02", failures)


def c03_candidates_only(ctx: CheckContext) -> CheckResult:
    candidates = set(ctx.candidate_ids)
    ids = [item.restaurant.id for item in ctx.response.recommendations]
    invented = [restaurant_id for restaurant_id in ids if restaurant_id not in candidates]
    failures = [f"{restaurant_id} is not a candidate" for restaurant_id in invented]
    if len(set(ids)) != len(ids):
        failures.append("duplicate restaurant ids")
    ranks = [item.rank for item in ctx.response.recommendations]
    if ranks != list(range(1, len(ranks) + 1)):
        failures.append(f"ranks {ranks} are not 1..n")
    return _result("C-03", failures, ok=len(ids) - len(invented), total=len(ids))


def _allowed(ctx: CheckContext) -> tuple[float, set[str] | None, set[str] | None]:
    request = ctx.request
    min_rating = request.min_rating
    cuisines = {item.casefold() for item in request.cuisines} or None
    budgets = {request.budget.value} if request.budget else None
    for relaxation in ctx.response.relaxations:
        lowered = _LOWERED_RATING.match(relaxation)
        if lowered:
            min_rating = max(RATING_FLOOR, float(lowered.group(2)))
        elif relaxation.startswith("Removed cuisine filter"):
            cuisines = None
        elif relaxation.startswith("Widened budget") and request.budget:
            budgets = _WIDENED_BUDGET[request.budget.value]
    return min_rating, cuisines, budgets


def c04_constraints(ctx: CheckContext) -> CheckResult:
    location = " ".join(ctx.request.location.split()).casefold()
    min_rating, cuisines, budgets = _allowed(ctx)
    failures = []
    ok = 0
    for item in ctx.response.recommendations:
        restaurant = item.restaurant
        problems = []
        if restaurant.location.casefold() != location:
            problems.append(f"location {restaurant.location}")
        if restaurant.rating < min_rating:
            problems.append(f"rating {restaurant.rating} < {min_rating}")
        if budgets is not None and restaurant.budget_tier.value not in budgets:
            problems.append(f"budget {restaurant.budget_tier.value} not in {sorted(budgets)}")
        if cuisines is not None and not cuisines & {item.casefold() for item in restaurant.cuisines}:
            problems.append(f"cuisines {restaurant.cuisines}")
        if problems:
            failures.append(f"{restaurant.id}: " + ", ".join(problems))
        else:
            ok += 1
    return _result("C-04", failures, ok=ok, total=len(ctx.response.recommendations))


def c05_relaxations(ctx: CheckContext) -> CheckResult:
    if "relaxations" not in ctx.expect:
        return _skip("C-05", "no frozen relaxations for this mode")
    expected = ctx.expect["relaxations"]
    if ctx.response.relaxations != expected:
        return _result("C-05", [f"got {ctx.response.relaxations}, expected {expected}"])
    return _result("C-05", [])


def c06_source(ctx: CheckContext) -> CheckResult:
    expected = ctx.expect.get("source")
    if expected and ctx.response.source != expected:
        return _result("C-06", [f"source {ctx.response.source}, expected {expected}"])
    return _result("C-06", [])


def _attribute(text: str, match: re.Match) -> str | None:
    before, after = text[: match.start()], text[match.end() :]
    value = match.group(2)
    if match.group(1) or _FOR_TWO_AFTER.match(after):
        return "cost"
    if _VOTES_AFTER.match(after):
        return "votes"
    number = float(value.replace(",", ""))
    if number <= 5 and ("." in value or _RATING_AFTER.match(after) or _RATING_BEFORE.search(before)):
        return "rating"
    return None


def _qualifier(text: str, match: re.Match) -> str | None:
    if text[match.end() : match.end() + 1] == "+":
        return ">"
    before = text[: match.start()]
    for symbol, pattern in _QUALIFIERS.items():
        if pattern.search(before):
            return symbol
    return None


def _number_holds(field_name: str, quoted: float, actual: float, qualifier: str | None) -> bool:
    if qualifier == ">":
        return actual >= quoted
    if qualifier == "<":
        return actual <= quoted
    if field_name == "rating":
        return abs(actual - quoted) <= RATING_TOLERANCE
    if qualifier == "~" and field_name == "votes":
        return abs(actual - quoted) <= APPROXIMATE_VOTES * actual
    return actual == quoted


def c07_number_accuracy(ctx: CheckContext) -> CheckResult:
    failures: list[str] = []
    skipped: list[str] = []
    ok = total = 0
    user_numbers = {float(value.replace(",", "")) for value in _USER_NUMBER.findall(ctx.request.extra_preferences or "")}
    for item in ctx.response.recommendations:
        restaurant = item.restaurant
        actual = {"rating": restaurant.rating, "votes": restaurant.votes, "cost": restaurant.cost_for_two}
        for match in _NUMBER.finditer(item.explanation):
            field_name = _attribute(item.explanation, match)
            if field_name is None:
                skipped.append(f"{restaurant.id}: '{match.group(0).strip()}'")
                continue
            quoted = float(match.group(2).replace(",", ""))
            qualifier = _qualifier(item.explanation, match)
            # "under ₹300" echoing the user's own "under 300" restates their wish, not a fact about the place.
            if qualifier and quoted in user_numbers:
                skipped.append(f"{restaurant.id}: '{match.group(0).strip()}' (the user's own figure)")
                continue
            total += 1
            if _number_holds(field_name, quoted, actual[field_name], qualifier):
                ok += 1
            else:
                failures.append(f"{restaurant.id}: {field_name} quoted as {match.group(0).strip()}, data says {actual[field_name]}")
    note = f"skipped {len(skipped)}: " + ", ".join(skipped[:5]) if skipped else ""
    if failures:
        return CheckResult("C-07", "fail", "; ".join(failures[:5] + ([note] if note else [])), ok, total)
    return CheckResult("C-07", "pass", note, ok, total)


def c08_explanation_format(ctx: CheckContext) -> CheckResult:
    failures = []
    ok = 0
    for item in ctx.response.recommendations:
        text = item.explanation.strip()
        sentences = sentence_count(text)
        if not text or not 1 <= sentences <= 2 or len(text) > MAX_EXPLANATION_CHARS:
            failures.append(f"{item.restaurant.id}: {sentences} sentences, {len(text)} chars")
        else:
            ok += 1
    return _result("C-08", failures, ok=ok, total=len(ctx.response.recommendations))


def _names(text: str, name: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text, re.IGNORECASE) is not None


def c09_summary(ctx: CheckContext) -> CheckResult:
    if ctx.response.source != "llm":
        return _skip("C-09", "fallback responses have no summary")
    summary = (ctx.response.summary or "").strip()
    if not summary:
        return _result("C-09", ["no summary in an llm response"])
    returned = {item.restaurant.id for item in ctx.response.recommendations}
    returned_names = [item.restaurant.name for item in ctx.response.recommendations]
    named = []
    for candidate_id in ctx.candidate_ids:
        record = ctx.store.get(candidate_id)
        if record is None or candidate_id in returned:
            continue
        if any(_names(shown, record.name) for shown in returned_names):
            continue
        if _names(summary, record.name):
            named.append(record.name)
    return _result("C-09", [f"summary names {name}, which was not returned" for name in named])


def c10_empty_result(ctx: CheckContext) -> CheckResult:
    failures = []
    if ctx.response.recommendations:
        failures.append(f"{len(ctx.response.recommendations)} results, expected 0")
    if not ctx.response.meta.get("message"):
        failures.append("no message for the empty result")
    if ctx.llm_called:
        failures.append("LLM was called for an empty candidate list")
    return _result("C-10", failures)


def c11_no_secrets(ctx: CheckContext) -> CheckResult:
    text = json.dumps(ctx.body, ensure_ascii=False)
    failures = []
    if any(secret and secret in text for secret in ctx.secrets):
        failures.append("response contains a secret value")
    if any(secret and secret in ctx.log_text for secret in ctx.secrets):
        failures.append("captured logs contain a secret value")
    return _result("C-11", failures)


def c12_timing(ctx: CheckContext) -> CheckResult:
    if not ctx.llm_called:
        return _skip("C-12", "LLM not called")
    limit_ms = (ctx.deadline_seconds + DEADLINE_OVERHEAD_SECONDS) * 1000
    if ctx.llm_ms > limit_ms:
        return _result("C-12", [f"LLM step took {ctx.llm_ms:.0f} ms, limit {limit_ms:.0f} ms"])
    return _result("C-12", [])


def c13_displayed_facts(ctx: CheckContext) -> CheckResult:
    failures = []
    ok = 0
    total = 0
    for item in ctx.body.get("recommendations", []):
        shown = item["restaurant"]
        record = ctx.store.get(shown.get("id"))
        if record is None:
            failures.append(f"{shown.get('id')} is not in the store")
            total += len(shown)
            continue
        stored = record.model_dump(mode="json")
        for key, value in stored.items():
            total += 1
            if shown.get(key) == value:
                ok += 1
            else:
                failures.append(f"{record.id}.{key}: shown {shown.get(key)!r}, stored {value!r}")
    return _result("C-13", failures, ok=ok, total=total)


CHECKS: dict[str, Callable[[CheckContext], CheckResult]] = {
    "C-01": c01_schema,
    "C-02": c02_count,
    "C-03": c03_candidates_only,
    "C-04": c04_constraints,
    "C-05": c05_relaxations,
    "C-06": c06_source,
    "C-07": c07_number_accuracy,
    "C-08": c08_explanation_format,
    "C-09": c09_summary,
    "C-10": c10_empty_result,
    "C-11": c11_no_secrets,
    "C-12": c12_timing,
    "C-13": c13_displayed_facts,
}


def run_checks(ctx: CheckContext, check_ids: list[str]) -> list[CheckResult]:
    return [CHECKS[check_id](ctx) for check_id in check_ids]


def context_from(
    query: dict, request: RecommendationRequest, expect: dict, response, trace, store, secrets,
    deadline_seconds: float = 8.0,
    log_text: str = "",
) -> CheckContext:
    body: dict[str, Any] = response.model_dump(mode="json")
    return CheckContext(
        query=query,
        request=request,
        expect=expect,
        response=response,
        body=body,
        candidate_ids=list(trace.candidate_ids),
        llm_called=trace.llm_called,
        store=store,
        secrets=secrets,
        llm_ms=trace.timings_ms.get("llm_ms", 0.0),
        deadline_seconds=deadline_seconds,
        log_text=log_text,
    )
