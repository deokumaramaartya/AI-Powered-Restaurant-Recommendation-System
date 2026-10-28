"""Eval command-line entry point (eval.md section 7.2).

Run from backend/:
    python -m evals.run_eval --mode offline [--queries G-01,G-07] [--repeat 3] [--no-fail]
    python -m evals.run_eval --mode live [--queries G-01,G-14] [--throttle-seconds 30] [--no-fail]
    python -m evals.run_eval --mode live --repeat 3 --judge [--repeat-queries G-01,G-14] [--compare-to <previous>.json]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.models.recommendation import RecommendationRequest
from app.services.llm_groq import GroqLLMClient, LLMClient
from app.services.prompt import SYSTEM_PROMPT
from app.services.recommender import InvalidPreferences, recommend
from app.services.store import RestaurantStore, load_store
from evals.checks import context_from, run_checks
from evals.fixtures.restaurants import FIXTURE_LOW_MAX, FIXTURE_MEDIUM_MAX, fixture_frame
from evals.fixtures.scripted_llm import ScriptedLLMClient
from evals.golden_set import load_labels, load_queries, resolve, select
from evals.judge import Judge, JudgeError, is_calibrated
from evals.metrics import QueryRun, blocking_failures, compute, format_value
from evals.report import git_commit, write_report

# Offline runs need a non-empty key so the recommender takes the LLM path to the scripted fake.
OFFLINE_KEY = "offline-scripted-key"
# Groq's free tier allows 8,000 tokens a minute and one call uses about 3,500, so pace live runs at 2 calls a minute.
DEFAULT_THROTTLE_SECONDS = 30.0
# The judge model has its own per-model limit; one judge call uses about 2,000 tokens.
DEFAULT_JUDGE_THROTTLE_SECONDS = 20.0
# Several 429s in a row mean the daily token limit is spent, not a passing per-minute spike.
RATE_LIMIT_STOP_AFTER = 3
# Injection and forced-failure queries test robustness, not taste, so the judge skips them.
UNJUDGED_CATEGORIES = {"injection", "forced_fallback"}


class LiveSetupError(Exception):
    pass


class _LogCapture(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


def offline_environment() -> tuple[Settings, RestaurantStore]:
    settings = Settings(
        _env_file=None,
        groq_api_key=OFFLINE_KEY,
        budget_low_max=FIXTURE_LOW_MAX,
        budget_medium_max=FIXTURE_MEDIUM_MAX,
    )
    return settings, RestaurantStore(fixture_frame(), FIXTURE_LOW_MAX, FIXTURE_MEDIUM_MAX)


def live_environment() -> tuple[Settings, RestaurantStore, LLMClient]:
    settings = get_settings()
    if not settings.groq_api_key:
        raise LiveSetupError("GROQ_API_KEY is empty in backend/.env; live mode calls Groq and needs a key.")
    store = load_store(settings.data_path, settings.budget_low_max, settings.budget_medium_max)
    if store is None:
        raise LiveSetupError("Real dataset not loaded; run python -m scripts.ingest from backend/ first.")
    return settings, store, GroqLLMClient.from_settings(settings)


def known_secrets(settings: Settings) -> list[str]:
    secrets = [SYSTEM_PROMPT.splitlines()[0]]
    try:
        keys = {get_settings().groq_api_key, settings.groq_api_key}
    except ValidationError:
        keys = {settings.groq_api_key}
    return secrets + sorted(key for key in keys if key)


def _id_key(value: str) -> str:
    return " ".join(str(value).split()).casefold()


def _query_llm(query: dict, mode: str, settings: Settings, llm: LLMClient | None) -> tuple[Settings, LLMClient]:
    if mode == "live":
        return settings, llm
    script = query.get("script")
    if script == "no_api_key":
        return settings.model_copy(update={"groq_api_key": ""}), ScriptedLLMClient()
    return settings, ScriptedLLMClient(script)


def run_query(
    query: dict,
    mode: str,
    store: RestaurantStore,
    settings: Settings,
    secrets: list[str],
    llm: LLMClient | None = None,
    repeat: int = 0,
) -> QueryRun:
    request_body, expect = resolve(query, mode)
    run = QueryRun(query_id=query["id"], category=query.get("category", ""), repeat=repeat, request=request_body)
    settings, llm = _query_llm(query, mode, settings, llm)
    capture = _LogCapture()
    app_logger = logging.getLogger("app")
    app_logger.addHandler(capture)
    try:
        request = RecommendationRequest.model_validate(request_body)
        started = time.perf_counter()
        response, trace = recommend(request, store, settings, llm=llm, use_cache=False, trace=True)
        run.latency_ms = (time.perf_counter() - started) * 1000
    except ValidationError as exc:
        run.error = f"request rejected by schema: {exc.error_count()} errors"
        return run
    except InvalidPreferences as exc:
        run.error = "request rejected: " + "; ".join(error["msg"] for error in exc.errors)
        return run
    finally:
        app_logger.removeHandler(capture)
    run.source = response.source
    run.count = len(response.recommendations)
    run.ids = [item.restaurant.id for item in response.recommendations]
    run.results = [
        {"id": item.restaurant.id, "name": item.restaurant.name, "explanation": item.explanation}
        for item in response.recommendations
    ]
    run.summary = response.summary
    run.relaxations = list(response.relaxations)
    run.fallback_ids = [
        item.restaurant.id for item in recommend(request, store, settings, llm=None, use_cache=False).recommendations
    ]
    run.candidates = len(trace.candidate_ids)
    run.llm_called = trace.llm_called
    run.llm_error = trace.llm_error
    run.llm_ms = trace.timings_ms.get("llm_ms", 0.0)
    run.dropped_ids = list(trace.dropped_ids)
    candidate_keys = {_id_key(restaurant_id) for restaurant_id in trace.candidate_ids}
    run.raw_total = len(trace.raw_ids)
    run.raw_invented = sum(_id_key(raw_id) not in candidate_keys for raw_id in trace.raw_ids)
    if trace.stats is not None:
        run.attempts = trace.stats.attempts
        run.prompt_tokens = trace.stats.prompt_tokens
        run.completion_tokens = trace.stats.completion_tokens
        run.reasoning_tokens = trace.stats.reasoning_tokens
        run.finish_reason = trace.stats.finish_reason
    ctx = context_from(query, request, expect, response, trace, store, secrets, settings.llm_deadline_seconds,
                       log_text="\n".join(capture.lines))
    run.checks = {result.id: result for result in run_checks(ctx, expect["checks"])}
    return run


def judge_run(judge: Judge, run: QueryRun, store: RestaurantStore, pause) -> None:
    """Rubric-score the explanations (M-11, M-09), and compare against the fallback list on free-text queries (M-10)."""
    if run.error or run.source != "llm" or not run.results or run.category in UNJUDGED_CATEGORIES:
        return
    records = {rid: store.get(rid).model_dump(mode="json") for rid in set(run.ids) | set(run.fallback_ids)}
    items = [{"restaurant": records[item["id"]], "explanation": item["explanation"]} for item in run.results]
    try:
        pause()
        scores = judge.score(run.request, items, run.summary, run.relaxations)
        run.judge_scores = [
            {"restaurant_id": s.restaurant_id, "explanation": s.explanation, "scores": s.scores, "reasons": s.reasons}
            for s in scores
        ]
        if run.request.get("extra_preferences") and run.fallback_ids and run.fallback_ids != run.ids:
            pause()
            run.pairwise = judge.pairwise(
                run.request, [records[rid] for rid in run.ids], [records[rid] for rid in run.fallback_ids]
            )
    except JudgeError as exc:
        run.judge_error = str(exc)


def load_comparison(path: str) -> dict:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return {
        "path": path,
        "values": {metric["id"]: metric["value"] for metric in payload.get("metrics", [])},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the golden-set eval.")
    parser.add_argument("--mode", choices=["offline", "live"], default="offline",
                        help="offline: fixture data and a scripted fake LLM, no network. live: real data and Groq.")
    parser.add_argument("--queries", help="Comma-separated query ids, for example G-01,G-07")
    parser.add_argument("--repeat", type=int, default=1,
                        help="Run each query this many times; 2 or more adds run-to-run stability (M-18)")
    parser.add_argument("--repeat-queries",
                        help="Comma-separated query ids that get the extra repeats; the rest run once")
    parser.add_argument("--judge", action="store_true",
                        help="Live only: score explanations and compare with the fallback ranker using EVAL_JUDGE_MODEL")
    parser.add_argument("--compare-to", help="An earlier report's .json; adds a change column to the metrics")
    parser.add_argument("--throttle-seconds", type=float, default=DEFAULT_THROTTLE_SECONDS,
                        help="Pause between Groq calls in live mode (default 30, sized for the free tier)")
    parser.add_argument("--judge-throttle-seconds", type=float, default=DEFAULT_JUDGE_THROTTLE_SECONDS,
                        help="Pause before each judge call (default 20)")
    parser.add_argument("--no-fail", action="store_true", help="Always exit 0")
    args = parser.parse_args(argv)
    if args.repeat < 1:
        parser.error("--repeat must be at least 1")
    if args.judge and args.mode != "live":
        print("--judge needs --mode live: the judge grades real Groq output.", file=sys.stderr)
        return 2

    ids = [item.strip() for item in args.queries.split(",") if item.strip()] if args.queries else None
    queries = select(load_queries(), args.mode, ids)
    repeat_ids = None
    if args.repeat_queries:
        repeat_ids = {item.strip() for item in args.repeat_queries.split(",") if item.strip()}
        missing = repeat_ids - {query["id"] for query in queries}
        if missing:
            parser.error(f"--repeat-queries names queries not in this run: {', '.join(sorted(missing))}")
    planned = [(repeat, query) for repeat in range(args.repeat) for query in queries
               if repeat == 0 or repeat_ids is None or query["id"] in repeat_ids]
    comparison = load_comparison(args.compare_to) if args.compare_to else None
    judge = None
    if args.mode == "live":
        try:
            settings, store, llm = live_environment()
        except LiveSetupError as exc:
            print(exc, file=sys.stderr)
            return 2
        model = llm.model
        if args.judge:
            judge = Judge.from_settings(settings)
    else:
        settings, store = offline_environment()
        llm = None
        model = f"not used (offline: {ScriptedLLMClient.model})"
    secrets = known_secrets(settings)

    def pause_for_judge():
        if judge is not None and judge.calls:
            time.sleep(args.judge_throttle_seconds)

    runs: list[QueryRun] = []
    for repeat, query in planned:
        if args.mode == "live" and runs and runs[-1].llm_called:
            time.sleep(args.throttle_seconds)
        run = run_query(query, args.mode, store, settings, secrets, llm, repeat=repeat)
        if judge is not None and repeat == 0:
            judge_run(judge, run, store, pause_for_judge)
        runs.append(run)
        if args.mode == "live":
            print(f"[{len(runs)}/{len(planned)}] {run.query_id} run {repeat + 1}: "
                  f"{run.source or 'error'}{f' ({run.llm_error})' if run.llm_error else ''}",
                  file=sys.stderr, flush=True)
            recent = runs[-RATE_LIMIT_STOP_AFTER:]
            if len(recent) == RATE_LIMIT_STOP_AFTER and all(r.llm_error == "rate_limit" for r in recent):
                print(f"Stopped: Groq answered the last {RATE_LIMIT_STOP_AFTER} calls with a rate limit (429), "
                      "most likely the free-tier daily token limit. No report written; try again later.",
                      file=sys.stderr)
                return 2

    labels = load_labels()
    calibrated = judge is not None and is_calibrated(judge.model)
    metrics = compute(runs, mode=args.mode, deadline_seconds=settings.llm_deadline_seconds,
                      labels=labels, judged=judge is not None, calibrated=calibrated)
    header = {
        "date": datetime.now().isoformat(timespec="seconds"),
        "commit": git_commit(),
        "mode": args.mode,
        "GROQ_MODEL": model,
        "EVAL_JUDGE_MODEL": f"{judge.model} ({'calibrated' if calibrated else 'not calibrated'})" if judge else "not used",
        "LLM_TEMPERATURE": settings.llm_temperature,
        "LLM_REASONING_EFFORT": settings.llm_reasoning_effort,
        "LLM_MAX_COMPLETION_TOKENS": settings.llm_max_completion_tokens,
        "MAX_CANDIDATES": settings.max_candidates,
        "budget_thresholds": f"low <= {settings.budget_low_max}, medium <= {settings.budget_medium_max}",
        "dataset_rows": store.count,
        "queries_run": len(queries),
        "repeat": args.repeat,
        "repeat_queries": ",".join(sorted(repeat_ids)) if repeat_ids else "all",
        "compared_to": comparison["path"] if comparison else "none",
    }
    markdown_path, json_path = write_report(header, metrics, runs, labels if args.mode == "live" else None, comparison)

    for metric in metrics:
        print(f"{metric.id} {metric.name}: {format_value(metric)} (target {metric.target}, {metric.gate}) "
              f"{'PASS' if metric.passed else 'FAIL'}")
    errored = [run for run in runs if run.error]
    failed_checks = [(run.query_id, result) for run in runs for result in run.checks.values()
                     if result.status == "fail"]
    for run in errored:
        print(f"ERROR {run.query_id}: {run.error}")
    for query_id, result in failed_checks:
        print(f"FAIL {query_id} {result.id}: {result.detail}")
    for run in runs:
        if run.judge_error:
            print(f"JUDGE {run.query_id}: {run.judge_error}")
    print(f"Report: {markdown_path}")
    print(f"JSON:   {json_path}")

    failed = bool(blocking_failures(metrics) or errored)
    if failed and not args.no_fail:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
