"""Download the Zomato dataset, clean it, and write data/restaurants.parquet."""

from __future__ import annotations

import hashlib
import os
import re
import sys
from collections import Counter
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from app.services.budget import assign_budget_tier

DATASET_ID = "ManikaSaini/zomato-restaurant-recommendation"

REQUIRED_RAW_COLUMNS = [
    "address",
    "name",
    "online_order",
    "book_table",
    "rate",
    "votes",
    "location",
    "rest_type",
    "dish_liked",
    "cuisines",
    "approx_cost(for two people)",
    "listed_in(type)",
    "listed_in(city)",
]

OUTPUT_COLUMNS = [
    "id",
    "name",
    "location",
    "area",
    "address",
    "cuisines",
    "rating",
    "votes",
    "cost_for_two",
    "rest_type",
    "listing_types",
    "online_order",
    "book_table",
    "dish_liked",
]

_COST_PATTERN = re.compile(r"-?\d+(?:\.\d+)?")
_MISSING_RATING = {"new", "-", "nan", "none"}


class IngestError(Exception):
    """The dataset could not be loaded or cleaned into a usable file."""


def _is_missing(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def normalize_text(value) -> str | None:
    if _is_missing(value):
        return None
    text = " ".join(str(value).split())
    return text or None


def normalize_key(value) -> str:
    text = normalize_text(value)
    if text is None:
        return ""
    return text.casefold()


def parse_rating(value):
    if _is_missing(value):
        return None
    text = str(value).strip()
    if text.casefold() in _MISSING_RATING:
        return None
    if "/" in text:
        left, right = text.split("/", 1)
        if right.strip() != "5":
            return None
        text = left.strip()
    try:
        number = float(text)
    except ValueError:
        return None
    if number < 0 or number > 5:
        return None
    return number


def parse_cost(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if _is_missing(value) or value <= 0:
            return None
        return int(value)
    if _is_missing(value):
        return None
    match = _COST_PATTERN.search(str(value).replace(",", ""))
    if match is None:
        return None
    number = float(match.group())
    if number <= 0:
        return None
    return int(number)


def to_bool(value) -> bool:
    if _is_missing(value):
        return False
    return str(value).strip().casefold() == "yes"


def split_list(value) -> list[str]:
    if isinstance(value, (list, tuple)):
        parts = value
    elif _is_missing(value):
        return []
    else:
        parts = str(value).split(",")
    result: list[str] = []
    seen: set[str] = set()
    for part in parts:
        text = normalize_text(part)
        if text is None:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def make_id(name, address) -> str:
    key = f"{normalize_key(name)}\x1f{normalize_key(address)}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _missing_columns(frame: pd.DataFrame) -> list[str]:
    return [column for column in REQUIRED_RAW_COLUMNS if column not in frame.columns]


def _canonicalize_lists(lists: pd.Series) -> pd.Series:
    counts: Counter[str] = Counter()
    for items in lists:
        counts.update(items)
    best: dict[str, tuple[int, str]] = {}
    for spelling, count in counts.items():
        key = spelling.casefold()
        current = best.get(key)
        if current is None or count > current[0] or (count == current[0] and spelling < current[1]):
            best[key] = (count, spelling)
    mapping = {key: spelling for key, (_, spelling) in best.items()}

    def apply(items: list[str]) -> list[str]:
        chosen: list[str] = []
        seen: set[str] = set()
        for item in items:
            canonical = mapping[item.casefold()]
            folded = canonical.casefold()
            if folded in seen:
                continue
            seen.add(folded)
            chosen.append(canonical)
        return chosen

    return lists.map(apply)


def _collect_listing_types(values: pd.Series) -> list[str]:
    collected: list[str] = []
    seen: set[str] = set()
    for value in values:
        if _is_missing(value) or value in seen:
            continue
        seen.add(value)
        collected.append(value)
    return collected


def _python_none(value):
    if _is_missing(value):
        return None
    return value


def clean_dataframe(frame: pd.DataFrame) -> pd.DataFrame:
    missing = _missing_columns(frame)
    if missing:
        raise IngestError("Missing required columns: " + ", ".join(missing))

    working = pd.DataFrame(
        {
            "name": frame["name"].map(normalize_text),
            "address": frame["address"].map(normalize_text),
            "location": frame["location"].map(normalize_text),
            "area": frame["listed_in(city)"].map(normalize_text),
            "rest_type": frame["rest_type"].map(normalize_text),
            "listing_type": frame["listed_in(type)"].map(normalize_text),
            "rating": frame["rate"].map(parse_rating),
            "cost_for_two": frame["approx_cost(for two people)"].map(parse_cost),
            "cuisines": frame["cuisines"].map(split_list),
            "dish_liked": frame["dish_liked"].map(split_list),
            "online_order": frame["online_order"].map(to_bool),
            "book_table": frame["book_table"].map(to_bool),
            "votes": pd.to_numeric(frame["votes"], errors="coerce").fillna(0).astype(int),
        }
    )
    keep = (
        working["name"].notna()
        & working["location"].notna()
        & working["rating"].notna()
        & working["cost_for_two"].notna()
        & working["cuisines"].map(bool)
    )
    working = working.loc[keep].reset_index(drop=True)
    if working.empty:
        raise IngestError("Cleaning dropped every row; refusing to write an empty dataset")

    working["_name_key"] = working["name"].map(normalize_key)
    working["_address_key"] = working["address"].map(normalize_key)
    winner_index = working.groupby(["_name_key", "_address_key"], sort=False)["votes"].idxmax()
    winners = working.loc[winner_index].copy()
    listings = (
        working.groupby(["_name_key", "_address_key"], sort=False)["listing_type"]
        .agg(_collect_listing_types)
        .rename("listing_types")
        .reset_index()
    )
    winners = winners.merge(listings, on=["_name_key", "_address_key"], how="left")
    winners["id"] = [
        make_id(name, address) for name, address in zip(winners["name"], winners["address"], strict=True)
    ]
    winners["cuisines"] = _canonicalize_lists(winners["cuisines"])
    for column in ("area", "address", "rest_type"):
        winners[column] = [_python_none(value) for value in winners[column]]
    winners["rating"] = winners["rating"].astype(float)
    winners["cost_for_two"] = winners["cost_for_two"].astype(int)
    winners["votes"] = winners["votes"].astype(int)
    winners["online_order"] = pd.Series(
        (bool(value) for value in winners["online_order"]), index=winners.index, dtype=object
    )
    winners["book_table"] = pd.Series(
        (bool(value) for value in winners["book_table"]), index=winners.index, dtype=object
    )
    cleaned = winners.loc[:, OUTPUT_COLUMNS].reset_index(drop=True)
    if cleaned.empty:
        raise IngestError("Cleaning dropped every row; refusing to write an empty dataset")
    if cleaned["id"].duplicated().any():
        raise IngestError("Duplicate restaurant ids after hashing; refusing to write the dataset")
    return cleaned


def summarize(raw_count: int, cleaned: pd.DataFrame, low_max: int, medium_max: int) -> dict:
    tiers = cleaned["cost_for_two"].map(lambda cost: assign_budget_tier(int(cost), low_max, medium_max))
    counts = {
        "low": int((tiers == "low").sum()),
        "medium": int((tiers == "medium").sum()),
        "high": int((tiers == "high").sum()),
    }
    total = len(cleaned)
    warnings = []
    for tier, count in counts.items():
        share = count / total if total else 0
        if share < 0.10:
            warnings.append(
                f"budget tier '{tier}' holds {share:.1%} of rows, under 10%. "
                "Tune BUDGET_LOW_MAX and BUDGET_MEDIUM_MAX."
            )
    costs = cleaned["cost_for_two"]
    cuisines = {item for items in cleaned["cuisines"] for item in items}
    return {
        "rows_before": int(raw_count),
        "rows_after": int(total),
        "localities": int(cleaned["location"].nunique()),
        "cuisines": int(len(cuisines)),
        "budget_counts": counts,
        "cost_percentiles": {
            "p10": float(costs.quantile(0.10)),
            "p25": float(costs.quantile(0.25)),
            "p50": float(costs.quantile(0.50)),
            "p75": float(costs.quantile(0.75)),
            "p90": float(costs.quantile(0.90)),
        },
        "low_max": low_max,
        "medium_max": medium_max,
        "warnings": warnings,
    }


def format_summary(stats: dict) -> str:
    counts = stats["budget_counts"]
    lines = [
        f"Rows before cleaning: {stats['rows_before']}",
        f"Rows after cleaning: {stats['rows_after']}",
        f"Localities: {stats['localities']}",
        f"Distinct cuisines: {stats['cuisines']}",
        (
            f"Budget tiers (low <= {stats['low_max']}, medium <= {stats['medium_max']}): "
            f"low={counts['low']}, medium={counts['medium']}, high={counts['high']}"
        ),
        "Cost for two percentiles: "
        + ", ".join(f"{name}={value:.0f}" for name, value in stats["cost_percentiles"].items()),
    ]
    lines.extend(f"WARNING: {warning}" for warning in stats["warnings"])
    return "\n".join(lines)


def write_parquet(cleaned: pd.DataFrame, output_path: Path, rows_before: int | None = None) -> None:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    table = pa.Table.from_pandas(cleaned, preserve_index=False)
    metadata = dict(table.schema.metadata or {})
    if rows_before is not None:
        metadata[b"rows_before"] = str(int(rows_before)).encode("ascii")
    table = table.replace_schema_metadata(metadata)
    pq.write_table(table, temporary)
    os.replace(temporary, output_path)


def read_rows_before(path) -> int | None:
    metadata = pq.read_schema(path).metadata or {}
    raw = metadata.get(b"rows_before")
    if raw is None:
        return None
    return int(raw)


def run_ingest(*, loader, output_path, low_max: int = 500, medium_max: int = 1200) -> dict:
    try:
        raw = loader()
    except IngestError:
        raise
    except Exception as exc:
        raise IngestError(f"Failed to load dataset: {exc}") from exc
    if not isinstance(raw, pd.DataFrame):
        raw = pd.DataFrame(raw)
    cleaned = clean_dataframe(raw)
    stats = summarize(len(raw), cleaned, low_max, medium_max)
    write_parquet(cleaned, Path(output_path), rows_before=len(raw))
    return stats


def load_huggingface_dataset() -> pd.DataFrame:
    from datasets import load_dataset

    dataset = load_dataset(DATASET_ID, split="train")
    return dataset.to_pandas()


def read_thresholds(env_path: Path | None = None) -> tuple[int, int]:
    file_values: dict[str, str] = {}
    if env_path is not None and env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, _, raw_value = stripped.partition("=")
            file_values[key.strip()] = raw_value.strip()
    low = int(os.environ.get("BUDGET_LOW_MAX", file_values.get("BUDGET_LOW_MAX", "500")))
    medium = int(os.environ.get("BUDGET_MEDIUM_MAX", file_values.get("BUDGET_MEDIUM_MAX", "1200")))
    return low, medium


def default_output_path() -> Path:
    data_path = os.environ.get("DATA_PATH", "../data/restaurants.parquet")
    path = Path(data_path)
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[1] / path
    return path


def main() -> int:
    backend_dir = Path(__file__).resolve().parents[1]
    low_max, medium_max = read_thresholds(backend_dir / ".env")
    output_path = default_output_path()
    try:
        stats = run_ingest(
            loader=load_huggingface_dataset,
            output_path=output_path,
            low_max=low_max,
            medium_max=medium_max,
        )
    except IngestError as exc:
        print(f"Ingestion failed: {exc}", file=sys.stderr)
        return 1
    print(format_summary(stats), flush=True)
    print(f"Wrote {output_path}", flush=True)
    from evals.data_profile import write_report

    report_path = backend_dir / "evals" / "reports" / "phase1-data.md"
    loaded = pd.read_parquet(output_path)
    result = write_report(
        loaded,
        report_path,
        low_max=low_max,
        medium_max=medium_max,
        rows_before=stats["rows_before"],
    )
    print(f"Wrote {report_path}")
    if not result.passed:
        print("Data profile failed:", file=sys.stderr)
        for failure in result.failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
