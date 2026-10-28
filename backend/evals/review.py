"""Human review helpers (eval.md sections 4.3 and 5).

Run from backend/ after a live eval:
    python -m evals.review relevance-sheet --report evals/reports/<run>.json
    python -m evals.review import-labels evals/reports/relevance-sheet-<stamp>.csv
    python -m evals.review calibration-sheet --report evals/reports/<run>.json
    python -m evals.review calibrate evals/reports/calibration-sheet-<stamp>.csv

Sheets are CSV files for a spreadsheet app; they are written to evals/reports/ (gitignored). Graded relevance
labels go to golden/labels.jsonl and calibration results to golden/calibration.json, both committed.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from datetime import datetime
from pathlib import Path

from app.core.config import get_settings
from app.services.store import RestaurantStore, load_store
from evals.golden_set import LABELS_PATH
from evals.judge import AGREEMENT_TARGET, CALIBRATION_PATH, DIMENSIONS, Judge, JudgeError, agreement
from evals.report import REPORTS_DIR

UNREVIEWED_CATEGORIES = {"injection", "forced_fallback"}
CALIBRATION_COUNT = 20
REVIEWERS = ("r1", "r2")
RELEVANCE_COLUMNS = [
    "query_id", "request", "restaurant_id", "name", "key_facts",
    "reviewer_1_grade", "reviewer_2_grade", "agreed_grade", "notes",
]


def describe_request(request: dict) -> str:
    parts = [f"location {request.get('location')}"]
    if request.get("budget"):
        parts.append(f"budget {request['budget']}")
    if request.get("cuisines"):
        parts.append("cuisines " + ", ".join(request["cuisines"]))
    parts.append(f"min rating {request.get('min_rating', 3.5)}")
    if request.get("extra_preferences"):
        parts.append(f"wants \"{request['extra_preferences']}\"")
    parts.append(f"top {request.get('top_k', 5)}")
    return "; ".join(parts)


def key_facts(record) -> str:
    badges = [label for label, on in (("online order", record.online_order), ("table booking", record.book_table)) if on]
    facts = [
        f"rating {record.rating:.1f} ({record.votes:,} votes)",
        f"₹{record.cost_for_two:,} for two",
        ", ".join(record.cuisines),
        record.rest_type or "type unknown",
    ]
    if badges:
        facts.append(" and ".join(badges))
    if record.dish_liked:
        facts.append("liked: " + ", ".join(record.dish_liked[:5]))
    return "; ".join(facts)


def load_report(path: str) -> dict:
    report = json.loads(Path(path).read_text(encoding="utf-8"))
    if report.get("header", {}).get("mode") != "live":
        raise SystemExit("Review sheets need a live report: offline runs use fixture data, not real restaurants.")
    return report


def real_store() -> RestaurantStore:
    settings = get_settings()
    store = load_store(settings.data_path, settings.budget_low_max, settings.budget_medium_max)
    if store is None:
        raise SystemExit("Real dataset not loaded; run python -m scripts.ingest from backend/ first.")
    return store


def reviewable(report: dict) -> list[dict]:
    return [
        run for run in report["queries"]
        if not run.get("error") and run.get("category") not in UNREVIEWED_CATEGORIES and run.get("ids")
    ]


def _sheet_path(kind: str) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    return REPORTS_DIR / f"{kind}-{datetime.now().strftime('%Y-%m-%d-%H%M%S')}.csv"


def _write_csv(path: Path, columns: list[str], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def _read_csv(path: str) -> list[dict]:
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def read_labels(path: Path = LABELS_PATH) -> dict[tuple[str, str], dict]:
    if not path.is_file():
        return {}
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return {(row["query_id"], row["restaurant_id"]): row for row in rows}


def relevance_rows(report: dict, store: RestaurantStore, labeled: set[tuple[str, str]]) -> list[dict]:
    """Union of AI and fallback picks per query, shuffled so reviewers cannot tell which ranker chose them."""
    picks: dict[str, set[str]] = {}
    requests: dict[str, dict] = {}
    for run in reviewable(report):
        picks.setdefault(run["query_id"], set()).update(run["ids"], run.get("fallback_ids", []))
        requests.setdefault(run["query_id"], run["request"])
    rows = []
    for query_id in sorted(picks):
        ungraded = sorted(rid for rid in picks[query_id] if (query_id, rid) not in labeled and store.get(rid))
        random.Random(query_id).shuffle(ungraded)
        for rid in ungraded:
            record = store.get(rid)
            rows.append({
                "query_id": query_id, "request": describe_request(requests[query_id]), "restaurant_id": rid,
                "name": record.name, "key_facts": key_facts(record),
                "reviewer_1_grade": "", "reviewer_2_grade": "", "agreed_grade": "", "notes": "",
            })
    return rows


def _grade(value: str, where: str) -> int | None:
    value = (value or "").strip()
    if not value:
        return None
    if value not in {"0", "1", "2", "3"}:
        raise ValueError(f"{where}: grade must be 0, 1, 2, or 3, not {value!r}")
    return int(value)


def settle_grade(row: dict, where: str) -> tuple[float, list[str]] | None:
    """Section 5.2: a 1-point gap is averaged; a 2-point gap needs an agreed grade."""
    first = _grade(row.get("reviewer_1_grade"), where)
    second = _grade(row.get("reviewer_2_grade"), where)
    agreed = _grade(row.get("agreed_grade"), where)
    reviewers = [name for name, grade in zip(REVIEWERS, (first, second)) if grade is not None]
    if agreed is not None:
        return float(agreed), reviewers
    if first is None and second is None:
        return None
    if first is not None and second is not None:
        if abs(first - second) >= 2:
            raise ValueError(f"{where}: grades {first} and {second} differ by 2 or more; fill in agreed_grade")
        return (first + second) / 2, reviewers
    return float(first if first is not None else second), reviewers


def import_labels(sheet: str, path: Path = LABELS_PATH) -> int:
    labels = read_labels(path)
    added = 0
    problems = []
    for number, row in enumerate(_read_csv(sheet), start=2):
        try:
            settled = settle_grade(row, f"row {number} ({row.get('query_id')}, {row.get('name')})")
        except ValueError as exc:
            problems.append(str(exc))
            continue
        if settled is None:
            continue
        grade, reviewers = settled
        labels[(row["query_id"], row["restaurant_id"])] = {
            "query_id": row["query_id"], "restaurant_id": row["restaurant_id"], "grade": grade,
            "reviewers": reviewers, "note": row.get("notes", "").strip(),
        }
        added += 1
    if problems:
        raise SystemExit("Nothing imported; fix these rows first:\n" + "\n".join(problems))
    ordered = sorted(labels.values(), key=lambda row: (row["query_id"], row["restaurant_id"]))
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in ordered), encoding="utf-8")
    return added


def calibration_columns() -> list[str]:
    scores = [f"{reviewer}_{dimension}" for reviewer in REVIEWERS for dimension in DIMENSIONS]
    return ["item", "query_id", "request", "restaurant_id", "name", "key_facts", "explanation", "summary",
            "request_json", *scores]


def calibration_rows(report: dict, store: RestaurantStore, count: int = CALIBRATION_COUNT) -> list[dict]:
    """Up to `count` explanations, taken round-robin across queries so every query type is represented."""
    queues = []
    seen = set()
    for run in reviewable(report):
        if run.get("source") != "llm" or run["query_id"] in seen:
            continue
        seen.add(run["query_id"])
        queues.append([(run, item) for item in run.get("results", [])])
    rows = []
    while len(rows) < count and any(queues):
        for queue in queues:
            if queue and len(rows) < count:
                run, item = queue.pop(0)
                record = store.get(item["id"])
                if record is None:
                    continue
                rows.append({
                    "item": len(rows) + 1, "query_id": run["query_id"], "request": describe_request(run["request"]),
                    "restaurant_id": item["id"], "name": record.name, "key_facts": key_facts(record),
                    "explanation": item["explanation"], "summary": run.get("summary") or "",
                    "request_json": json.dumps(run["request"], ensure_ascii=False),
                })
    return rows


def human_scores(row: dict) -> dict[str, list[int]]:
    scores = {}
    for dimension in DIMENSIONS:
        values = []
        for reviewer in REVIEWERS:
            value = (row.get(f"{reviewer}_{dimension}") or "").strip()
            if value:
                if value not in {"1", "2", "3", "4", "5"}:
                    raise ValueError(f"item {row.get('item')}: {reviewer}_{dimension} must be 1 to 5, not {value!r}")
                values.append(int(value))
        if values:
            scores[dimension] = values
    return scores


def calibrate(sheet: str, judge: Judge, store: RestaurantStore, path: Path = CALIBRATION_PATH) -> dict:
    rows = [row for row in _read_csv(sheet) if human_scores(row)]
    if not rows:
        raise SystemExit("The sheet has no human scores yet.")
    groups: dict[str, list[dict]] = {}
    for row in rows:
        groups.setdefault(row["query_id"], []).append(row)
    compared = []
    for group in groups.values():
        items = [{"restaurant": store.get(row["restaurant_id"]).model_dump(mode="json"),
                  "explanation": row["explanation"]} for row in group]
        try:
            judged = {score.restaurant_id: score.scores for score in
                      judge.score(json.loads(group[0]["request_json"]), items, group[0]["summary"] or None)}
        except JudgeError as exc:
            raise SystemExit(f"Judge call failed for {group[0]['query_id']}: {exc}")
        compared += [{"human": human_scores(row), "judge": judged[row["restaurant_id"]]}
                     for row in group if row["restaurant_id"] in judged]
    shares = agreement(compared)
    result = {
        "judge_model": judge.model,
        "date": datetime.now().isoformat(timespec="seconds"),
        "sheet": Path(sheet).name,
        "explanations": len(compared),
        "agreement": shares,
        "target": AGREEMENT_TARGET,
        "passed": all(share >= AGREEMENT_TARGET for share in shares.values()),
    }
    path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Human review sheets and judge calibration.")
    commands = parser.add_subparsers(dest="command", required=True)
    sheet = commands.add_parser("relevance-sheet", help="Blind rating sheet of AI and fallback picks (section 5.2)")
    sheet.add_argument("--report", required=True, help="A live eval report .json")
    labels = commands.add_parser("import-labels", help="Settle graded rows into golden/labels.jsonl")
    labels.add_argument("sheet")
    calibration = commands.add_parser("calibration-sheet", help="20 explanations for reviewers to score (section 4.3)")
    calibration.add_argument("--report", required=True, help="A live eval report .json")
    calibration.add_argument("--count", type=int, default=CALIBRATION_COUNT)
    run = commands.add_parser("calibrate", help="Score the sheet with the judge and record agreement")
    run.add_argument("sheet")
    args = parser.parse_args(argv)

    if args.command == "relevance-sheet":
        rows = relevance_rows(load_report(args.report), real_store(), set(read_labels()))
        path = _sheet_path("relevance-sheet")
        _write_csv(path, RELEVANCE_COLUMNS, rows)
        print(f"{len(rows)} restaurants to grade (0 to 3) across {len({r['query_id'] for r in rows})} queries: {path}")
    elif args.command == "import-labels":
        print(f"Imported {import_labels(args.sheet)} grades into {LABELS_PATH}")
    elif args.command == "calibration-sheet":
        rows = calibration_rows(load_report(args.report), real_store(), args.count)
        path = _sheet_path("calibration-sheet")
        _write_csv(path, calibration_columns(), rows)
        print(f"{len(rows)} explanations to score (1 to 5 per dimension): {path}")
    else:
        settings = get_settings()
        if not settings.groq_api_key:
            print("GROQ_API_KEY is empty in backend/.env; the judge calls Groq.", file=sys.stderr)
            return 2
        result = calibrate(args.sheet, Judge.from_settings(settings), real_store())
        for dimension, share in result["agreement"].items():
            print(f"{dimension}: {share:.0%} within 1 point (target {AGREEMENT_TARGET:.0%})")
        print(f"{'PASSED' if result['passed'] else 'NOT PASSED'}; saved to {CALIBRATION_PATH}")
        return 0 if result["passed"] else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
