"""Phase 1 data-quality summary for the cleaned restaurant Parquet file."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from app.services.budget import assign_budget_tier

REQUIRED_FIELDS = ("id", "name", "location", "cuisines", "rating", "cost_for_two")
ACRONYMS = ("BTM", "HSR")


@dataclass
class ProfileResult:
    passed: bool
    failures: list[str]
    markdown: str


def _is_blank(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, (list, tuple)):
        return len(value) == 0
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _cuisine_count(frame: pd.DataFrame) -> int:
    found: set[str] = set()
    for items in frame["cuisines"]:
        if _is_blank(items):
            continue
        found.update(str(item) for item in items)
    return len(found)


def evaluate_frame(
    frame: pd.DataFrame,
    low_max: int,
    medium_max: int,
    rows_before: int | None = None,
) -> ProfileResult:
    failures: list[str] = []
    if "budget_tier" in frame.columns:
        failures.append("column budget_tier must not be stored in the Parquet file")

    for column in REQUIRED_FIELDS:
        if column not in frame.columns:
            failures.append(f"required field {column} is missing")
            continue
        blank = frame[column].map(_is_blank)
        if bool(blank.any()):
            failures.append(f"nulls in required field {column}: {int(blank.sum())}")

    if "id" in frame.columns:
        duplicate_count = int(frame["id"].duplicated().sum())
        if duplicate_count:
            failures.append(f"duplicate id values: {duplicate_count}")

    counts = {"low": 0, "medium": 0, "high": 0}
    if "cost_for_two" in frame.columns and not frame.empty:
        for cost in frame["cost_for_two"]:
            if _is_blank(cost):
                continue
            counts[assign_budget_tier(int(cost), low_max, medium_max)] += 1
    total = len(frame)
    for tier, count in counts.items():
        share = count / total if total else 0
        if share < 0.10:
            failures.append(f"budget tier '{tier}' holds {share:.1%} of rows, under 10%")

    locations = set()
    if "location" in frame.columns:
        locations = {value for value in frame["location"] if isinstance(value, str)}
    for acronym in ACRONYMS:
        folded = acronym.casefold()
        found = sorted(value for value in locations if value.casefold() == folded)
        if acronym not in locations:
            detail = f" found {found}" if found else ""
            failures.append(f"locality {acronym} is missing or not stored as {acronym}{detail}")

    localities = int(frame["location"].nunique()) if "location" in frame.columns else 0
    cuisines = _cuisine_count(frame) if "cuisines" in frame.columns else 0
    before = "unknown" if rows_before is None else str(rows_before)
    shares = {tier: (count / total if total else 0) for tier, count in counts.items()}
    lines = [
        "# Phase 1 data profile",
        "",
        f"- Rows before cleaning: {before}",
        f"- Rows after cleaning: {total}",
        f"- Localities: {localities}",
        f"- Distinct cuisines: {cuisines}",
        (
            f"- Budget tiers (low <= {low_max}, medium <= {medium_max}): "
            f"low={counts['low']} ({shares['low']:.1%}), "
            f"medium={counts['medium']} ({shares['medium']:.1%}), "
            f"high={counts['high']} ({shares['high']:.1%})"
        ),
        f"- Localities include BTM: {'BTM' in locations}",
        f"- Localities include HSR: {'HSR' in locations}",
        "",
        "## Result",
        "",
        "PASS" if not failures else "FAIL",
    ]
    if failures:
        lines.append("")
        lines.extend(f"- {failure}" for failure in failures)
    markdown = "\n".join(lines) + "\n"
    return ProfileResult(passed=not failures, failures=failures, markdown=markdown)


def write_report(
    frame: pd.DataFrame,
    destination,
    low_max: int,
    medium_max: int,
    rows_before: int | None = None,
) -> ProfileResult:
    result = evaluate_frame(frame, low_max, medium_max, rows_before=rows_before)
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(result.markdown, encoding="utf-8")
    return result


def main() -> int:
    from scripts.ingest import default_output_path, read_rows_before, read_thresholds

    backend_dir = Path(__file__).resolve().parents[1]
    low_max, medium_max = read_thresholds(backend_dir / ".env")
    data_path = default_output_path()
    if not data_path.exists():
        print(f"Parquet file not found: {data_path}", file=sys.stderr)
        print("Run python -m scripts.ingest from backend/ first.", file=sys.stderr)
        return 1
    frame = pd.read_parquet(data_path)
    report_path = backend_dir / "evals" / "reports" / "phase1-data.md"
    result = write_report(
        frame,
        report_path,
        low_max=low_max,
        medium_max=medium_max,
        rows_before=read_rows_before(data_path),
    )
    print(result.markdown)
    print(f"Wrote {report_path}")
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
