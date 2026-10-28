"""Write Markdown and JSON eval reports to evals/reports/ (eval.md section 7.5)."""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from evals.metrics import Metric, QueryRun, consistency_rows, format_value, label_coverage, percentile

REPORTS_DIR = Path(__file__).resolve().parent / "reports"
COVERAGE_TARGET = 0.9
JUDGE_NOTES = 5


def git_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True, timeout=5
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown (no commits yet)"
    return result.stdout.strip() or "unknown"


def _change(metric: Metric, previous: dict) -> str:
    before = previous.get(metric.id)
    if before is None or metric.value is None:
        return "n/a"
    delta = metric.value - before
    if metric.unit == "ms":
        return f"{delta:+.0f} ms"
    if metric.unit == "tokens":
        return f"{delta:+.0f}"
    if metric.unit == "score":
        return f"{delta:+.2f}"
    return f"{delta * 100:+.1f} pts"


def _metrics_table(metrics: list[Metric], comparison: dict | None) -> list[str]:
    change = comparison is not None
    lines = ["| Metric | Name | Value | Target | Gate | Result |" + (" Change |" if change else "") + " Detail |",
             "|--------|------|-------|--------|------|--------|" + ("--------|" if change else "") + "--------|"]
    for metric in metrics:
        cells = [metric.id, metric.name, format_value(metric), metric.target, metric.gate,
                 "PASS" if metric.passed else "FAIL"]
        if change:
            cells.append(_change(metric, comparison["values"]))
        cells.append(metric.detail)
        lines.append("| " + " | ".join(str(cell) for cell in cells) + " |")
    return lines


def _judge_notes(runs: list[QueryRun]) -> list[str]:
    scored = [(run.query_id, score) for run in runs for score in run.judge_scores]
    errors = [f"- {run.query_id}: judge error: {run.judge_error}" for run in runs if run.judge_error]
    if not scored and not errors:
        return []
    lines = ["", "## Judge notes", ""]
    lowest = sorted(scored, key=lambda pair: sum(pair[1]["scores"].values()))[:JUDGE_NOTES]
    for query_id, score in lowest:
        weakest = min(score["scores"], key=score["scores"].get)
        scores = ", ".join(f"{key} {value}" for key, value in score["scores"].items())
        lines.append(f"- {query_id} / {score['restaurant_id']}: {scores}. "
                     f"Weakest ({weakest}): {score['reasons'].get(weakest, '')} "
                     f"Explanation: \"{score['explanation']}\"")
    pairwise = [f"{run.query_id} {run.pairwise:g}" for run in runs if run.pairwise is not None]
    if pairwise:
        lines.append("- Pairwise against the fallback ranker (1 win, 0.5 tie, 0 loss): " + ", ".join(pairwise))
    return lines + errors


def _consistency(runs: list[QueryRun]) -> list[str]:
    rows = consistency_rows(runs)
    if not rows:
        return []
    lines = ["", "## Consistency", "", "| Query | Runs | Jaccard | Same rank-1 pick |", "|-------|------|---------|------------------|"]
    for row in rows:
        lines.append(f"| {row['query_id']} | {row['runs']} | {row['jaccard']:.2f} | {'yes' if row['same_rank_1'] else 'no'} |")
    return lines


def _coverage(runs: list[QueryRun], labels: dict | None) -> list[str]:
    if labels is None:
        return []
    lines = ["", "## Label coverage", ""]
    if not labels:
        return lines + ["No human grades yet (golden/labels.jsonl). Build the rating sheet with "
                        "`python -m evals.review relevance-sheet --report <this report>.json`."]
    rows = [row for row in label_coverage(runs, labels) if row["coverage"] < COVERAGE_TARGET]
    if not rows:
        return lines + ["Every query has at least 90% of its returned restaurants graded."]
    for row in rows:
        missing = ", ".join(f"{rid} ({name})" for rid, name in row["missing"].items())
        lines.append(f"- {row['query_id']}: {row['coverage']:.0%} graded; needs grades for {missing}")
    return lines


def render_markdown(header: dict, metrics: list[Metric], runs: list[QueryRun], labels: dict | None = None,
                    comparison: dict | None = None) -> str:
    lines = [f"# Eval report: {header['mode']}", ""]
    lines += [f"- {key}: {value}" for key, value in header.items()]
    lines += ["", "## Metrics", ""] + _metrics_table(metrics, comparison)

    lines += ["", "## Failures", ""]
    failures = []
    for run in runs:
        label = run.query_id if run.repeat == 0 else f"{run.query_id} (repeat {run.repeat + 1})"
        if run.error:
            failures.append(f"- {label}: error: {run.error}")
        for result in run.checks.values():
            if result.status == "fail":
                failures.append(f"- {label}: {result.id}: {result.detail}")
    lines += failures or ["None."]

    skipped = sorted({result.id for run in runs for result in run.checks.values() if result.status == "skip"})
    if skipped:
        lines += ["", f"Skipped checks: {', '.join(skipped)}"]

    lines += _judge_notes(runs)

    latencies = [run.latency_ms for run in runs if run.error is None]
    slowest = sorted(runs, key=lambda run: run.latency_ms, reverse=True)[:5]
    lines += [
        "",
        "## Latency",
        "",
        f"- p50: {percentile(latencies, 0.5):.1f} ms",
        f"- p95: {percentile(latencies, 0.95):.1f} ms",
        f"- max: {max(latencies, default=0.0):.1f} ms",
        "- slowest: " + ", ".join(f"{run.query_id} ({run.latency_ms:.1f} ms)" for run in slowest),
    ]
    lines += _consistency(runs)
    lines += _coverage(runs, labels)
    lines += [
        "",
        "## Queries",
        "",
        "| Query | Run | Category | Source | Count | LLM ms | LLM error | Attempts | Tokens in/out/reasoning | Dropped ids | Checks |",
        "|-------|-----|----------|--------|-------|--------|-----------|----------|-------------------------|-------------|--------|",
    ]
    for run in runs:
        summary = " ".join(f"{result.id}:{result.status}" for result in run.checks.values())
        llm_ms = f"{run.llm_ms:.0f}" if run.llm_called else "-"
        tokens = "/".join("-" if value is None else str(value)
                          for value in (run.prompt_tokens, run.completion_tokens, run.reasoning_tokens))
        lines.append(
            f"| {run.query_id} | {run.repeat + 1} | {run.category} | {run.source or '-'} | {run.count} | {llm_ms} | "
            f"{run.llm_error or '-'} | {run.attempts or '-'} | {tokens} | {', '.join(run.dropped_ids) or '-'} | {summary} |"
        )
    return "\n".join(lines) + "\n"


def write_report(
    header: dict,
    metrics: list[Metric],
    runs: list[QueryRun],
    labels: dict | None = None,
    comparison: dict | None = None,
    reports_dir: Path = REPORTS_DIR,
):
    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    base = reports_dir / f"{stamp}-{header['mode']}"
    markdown_path = base.with_suffix(".md")
    json_path = base.with_suffix(".json")
    markdown_path.write_text(render_markdown(header, metrics, runs, labels, comparison), encoding="utf-8")
    payload = {
        "header": header,
        "metrics": [asdict(metric) for metric in metrics],
        "queries": [
            {**asdict(run), "checks": {key: asdict(value) for key, value in run.checks.items()}} for run in runs
        ],
    }
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return markdown_path, json_path
