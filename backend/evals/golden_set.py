"""Load golden queries and resolve per-mode request and expectation overrides."""

from __future__ import annotations

import json
from pathlib import Path

QUERIES_PATH = Path(__file__).resolve().parent / "golden" / "queries.jsonl"
LABELS_PATH = Path(__file__).resolve().parent / "golden" / "labels.jsonl"
DEFAULT_CHECKS = ["C-01", "C-02", "C-03", "C-11", "C-13"]

# "fixture" queries run in offline mode only; both read the offline overrides.
_MODE_KEYS = {"offline": "offline", "fixture": "offline", "live": "live"}


def load_queries(path: Path = QUERIES_PATH) -> list[dict]:
    queries = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            queries.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path.name} line {number}: {exc}") from exc
    return queries


def load_labels(path: Path = LABELS_PATH) -> dict[str, dict[str, float]]:
    """Human relevance grades (eval.md section 5.2) as {query_id: {restaurant_id: grade}}."""
    if not path.is_file():
        return {}
    labels: dict[str, dict[str, float]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            labels.setdefault(row["query_id"], {})[row["restaurant_id"]] = float(row["grade"])
    return labels


def runs_in(query: dict, mode: str) -> bool:
    modes = set(query.get("modes", []))
    if mode == "offline":
        return bool(modes & {"offline", "fixture"})
    return mode in modes


def resolve(query: dict, mode: str) -> tuple[dict, dict]:
    key = _MODE_KEYS[mode]
    request = {**query["request"], **query.get("request_by_mode", {}).get(key, {})}
    expect = {**query.get("expect", {}), **query.get("expect_by_mode", {}).get(key, {})}
    expect.setdefault("checks", DEFAULT_CHECKS)
    return request, expect


def select(queries: list[dict], mode: str, ids: list[str] | None = None) -> list[dict]:
    chosen = [query for query in queries if runs_in(query, mode)]
    if ids:
        wanted = set(ids)
        unknown = wanted - {query["id"] for query in queries}
        if unknown:
            raise ValueError("Unknown query ids: " + ", ".join(sorted(unknown)))
        chosen = [query for query in chosen if query["id"] in wanted]
    return chosen
