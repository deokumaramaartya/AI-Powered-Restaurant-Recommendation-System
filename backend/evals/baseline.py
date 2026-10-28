"""Baseline data-dependent expectations for live golden queries on the real dataset.

Run from backend/:  python -m evals.baseline          (print what the real data produces)
                    python -m evals.baseline --write  (freeze relaxations and counts into queries.jsonl)

Re-run after re-ingesting the data or changing budget thresholds (eval.md sections 3.1 and 8.2),
and review the printed values before writing them.
"""

from __future__ import annotations

import argparse
import json
import sys

from app.core.config import get_settings
from app.models.recommendation import RecommendationRequest
from app.services.recommender import InvalidPreferences, recommend
from app.services.store import load_store
from evals.golden_set import QUERIES_PATH, load_queries, resolve, runs_in


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--write", action="store_true", help="Freeze the printed values into queries.jsonl")
    args = parser.parse_args(argv)

    settings = get_settings()
    store = load_store(settings.data_path, settings.budget_low_max, settings.budget_medium_max)
    if store is None:
        print("Real dataset not loaded; run python -m scripts.ingest first.", file=sys.stderr)
        return 1

    queries = load_queries()
    problems = 0
    for query in queries:
        if not runs_in(query, "live"):
            continue
        body, _ = resolve(query, "live")
        try:
            request = RecommendationRequest.model_validate(body)
            response, trace = recommend(request, store, settings, trace=True)
        except InvalidPreferences as exc:
            problems += 1
            print(f"{query['id']}: NAME NOT IN DATA: " + "; ".join(error["msg"] for error in exc.errors))
            continue
        frozen = {"relaxations": response.relaxations, "count": len(response.recommendations)}
        print(f"{query['id']}: candidates={len(trace.candidate_ids)} {json.dumps(frozen, ensure_ascii=False)}")
        if args.write:
            query.setdefault("expect_by_mode", {}).setdefault("live", {}).update(frozen)

    if args.write and not problems:
        lines = [json.dumps(query, ensure_ascii=False) for query in queries]
        QUERIES_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"Froze live expectations into {QUERIES_PATH}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
