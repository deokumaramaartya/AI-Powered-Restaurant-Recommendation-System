import json

import pytest

from evals import run_eval
from evals.fixtures.scripted_llm import ScriptedLLMClient
from evals.golden_set import load_queries
from evals.judge import DIMENSIONS, RubricScore
from evals.metrics import QueryRun
from evals.report import render_markdown, write_report
from tests.conftest import make_settings, make_store


@pytest.fixture
def reports_in_tmp(monkeypatch, tmp_path):
    monkeypatch.setattr(run_eval, "write_report", lambda *args: write_report(*args, reports_dir=tmp_path))
    return tmp_path


def test_offline_golden_set_passes_and_writes_reports(reports_in_tmp):
    """The committed golden set is the Phase 2 gate; it must pass on the fixture data."""
    assert run_eval.main(["--mode", "offline"]) == 0
    assert len(list(reports_in_tmp.glob("*-offline.md"))) == 1
    assert len(list(reports_in_tmp.glob("*-offline.json"))) == 1


def test_wrong_frozen_relaxations_fail_the_run(monkeypatch, reports_in_tmp):
    """A changed relaxation order must fail M-02 and exit non-zero."""
    queries = load_queries()
    for query in queries:
        if query["id"] == "G-07":
            query["expect_by_mode"]["offline"]["relaxations"] = ["Dropped cuisine"]
    monkeypatch.setattr(run_eval, "load_queries", lambda: queries)
    assert run_eval.main(["--mode", "offline", "--queries", "G-07"]) == 1
    assert run_eval.main(["--mode", "offline", "--queries", "G-07", "--no-fail"]) == 0


def test_live_mode_without_a_key_stops_before_any_call(monkeypatch, capsys):
    """A live run with no key would silently measure the fallback ranker."""
    monkeypatch.setattr(run_eval, "get_settings", lambda: make_settings())
    assert run_eval.main(["--mode", "live"]) == 2
    assert "GROQ_API_KEY" in capsys.readouterr().err


def test_live_mode_pauses_between_llm_calls(monkeypatch, reports_in_tmp):
    """--throttle-seconds keeps a full run inside the Groq free-tier rate limit."""
    settings = make_settings(groq_api_key="test-key")
    monkeypatch.setattr(
        run_eval, "live_environment", lambda: (settings, make_store(settings), ScriptedLLMClient())
    )
    sleeps = []
    monkeypatch.setattr(run_eval.time, "sleep", sleeps.append)
    run_eval.main(["--mode", "live", "--queries", "G-01,G-02,G-13", "--throttle-seconds", "1.5", "--no-fail"])
    assert sleeps == [1.5, 1.5]
    report = next(reports_in_tmp.glob("*-live.md")).read_text(encoding="utf-8")
    assert "GROQ_MODEL: scripted-offline" in report


def test_repeat_runs_each_query_and_reports_stability(reports_in_tmp):
    """--repeat 3 runs every query three times and adds M-18 and a consistency section."""
    assert run_eval.main(["--mode", "offline", "--queries", "G-01,G-13", "--repeat", "3"]) == 0
    report = json.loads(next(reports_in_tmp.glob("*-offline.json")).read_text(encoding="utf-8"))
    assert [(run["query_id"], run["repeat"]) for run in report["queries"]] == [
        ("G-01", 0), ("G-13", 0), ("G-01", 1), ("G-13", 1), ("G-01", 2), ("G-13", 2),
    ]
    assert "M-18" in {metric["id"] for metric in report["metrics"]}
    assert "## Consistency" in next(reports_in_tmp.glob("*-offline.md")).read_text(encoding="utf-8")


def test_repeat_queries_limits_the_extra_runs(reports_in_tmp):
    """--repeat-queries keeps a repeated live run inside Groq's free-tier daily token limit."""
    run_eval.main(["--mode", "offline", "--queries", "G-01,G-13", "--repeat", "3", "--repeat-queries", "G-13"])
    report = json.loads(next(reports_in_tmp.glob("*-offline.json")).read_text(encoding="utf-8"))
    assert [(run["query_id"], run["repeat"]) for run in report["queries"]] == [
        ("G-01", 0), ("G-13", 0), ("G-13", 1), ("G-13", 2),
    ]
    assert report["header"]["repeat_queries"] == "G-13"


def test_repeat_queries_must_be_in_the_run(reports_in_tmp):
    with pytest.raises(SystemExit):
        run_eval.main(["--mode", "offline", "--queries", "G-01", "--repeat", "2", "--repeat-queries", "G-13"])


def test_live_run_stops_when_groq_keeps_rate_limiting(monkeypatch, reports_in_tmp, capsys):
    """Once the daily token limit is reached every call fails, so the rest of the run would measure nothing."""
    settings = make_settings(groq_api_key="test-key")
    monkeypatch.setattr(
        run_eval, "live_environment", lambda: (settings, make_store(settings), ScriptedLLMClient("rate_limit"))
    )
    monkeypatch.setattr(run_eval.time, "sleep", lambda seconds: None)
    assert run_eval.main(["--mode", "live", "--queries", "G-01,G-02,G-13,G-07,G-08", "--no-fail"]) == 2
    assert "rate limit" in capsys.readouterr().err
    assert not list(reports_in_tmp.glob("*-live.*"))


def test_report_has_latency_and_label_coverage_sections():
    """Phase 5: the report adds latency, consistency, and label-coverage sections (eval.md 5.3 and 7.4)."""
    def query_run(query_id, latency_ms, result_ids):
        return QueryRun(query_id=query_id, category="normal", source="llm", latency_ms=latency_ms, ids=result_ids,
                        results=[{"id": rid, "name": f"Name {rid}", "explanation": "x"} for rid in result_ids])

    runs = [query_run("G-01", 1000.0, ["a", "b"]), query_run("G-02", 2000.0, ["c", "d"])]
    header = {"mode": "live"}
    no_grades = render_markdown(header, [], runs, labels={})
    assert "## Latency" in no_grades
    assert "- p50: 1000.0 ms" in no_grades
    assert "- p95: 2000.0 ms" in no_grades
    assert "slowest: G-02 (2000.0 ms), G-01 (1000.0 ms)" in no_grades
    assert "## Label coverage" in no_grades
    assert "No human grades yet" in no_grades
    partial = render_markdown(header, [], runs, labels={"G-01": {"a": 3, "b": 2}, "G-02": {"c": 1}})
    assert "- G-02: 50% graded; needs grades for d (Name d)" in partial
    assert "G-01:" not in partial.split("## Label coverage")[1].split("## Queries")[0]
    assert "## Label coverage" not in render_markdown({"mode": "offline"}, [], runs, labels=None)


def test_judge_needs_live_mode(capsys):
    assert run_eval.main(["--mode", "offline", "--judge"]) == 2
    assert "--mode live" in capsys.readouterr().err


def test_runs_record_results_and_the_fallback_ranking():
    """The fallback list is kept for nDCG and the pairwise judge; results feed the review sheets."""
    settings, store = run_eval.offline_environment()
    query = next(q for q in load_queries() if q["id"] == "G-01")
    run = run_eval.run_query(query, "offline", store, settings, [])
    assert run.source == "llm"
    assert run.ids and run.fallback_ids
    assert run.results[0]["explanation"]
    assert run.request["location"] == "Indiranagar"


def test_live_judge_scores_explanations_and_compares_free_text_queries(monkeypatch, reports_in_tmp):
    settings = make_settings(groq_api_key="test-key")
    monkeypatch.setattr(
        run_eval, "live_environment", lambda: (settings, make_store(settings), ScriptedLLMClient())
    )
    monkeypatch.setattr(run_eval.time, "sleep", lambda seconds: None)

    class FakeJudge:
        model = "fake-judge"
        calls = 0

        def score(self, request, items, summary, relaxations=None):
            self.calls += 1
            return [RubricScore(item["restaurant"]["id"], item["explanation"],
                                {d: 4 for d in DIMENSIONS}, {d: "ok" for d in DIMENSIONS}) for item in items]

        def pairwise(self, request, ai_list, fallback_list):
            self.calls += 2
            return 1.0

    judge = FakeJudge()
    monkeypatch.setattr(run_eval.Judge, "from_settings", classmethod(lambda cls, s: judge))
    run_eval.main(["--mode", "live", "--queries", "G-01,G-13,G-20", "--judge", "--no-fail"])
    report = json.loads(next(reports_in_tmp.glob("*-live.json")).read_text(encoding="utf-8"))
    runs = {run["query_id"]: run for run in report["queries"]}
    assert runs["G-01"]["judge_scores"] and runs["G-01"]["pairwise"] is None
    assert runs["G-20"]["judge_scores"] == []
    metrics = {metric["id"]: metric for metric in report["metrics"]}
    assert metrics["M-11"]["value"] == 4.0
    assert metrics["M-11"]["gate"] == "tracking"
    assert "fake-judge (not calibrated)" in report["header"]["EVAL_JUDGE_MODEL"]


def test_compare_to_adds_a_change_column(reports_in_tmp, tmp_path):
    previous = tmp_path / "previous.json"
    previous.write_text(json.dumps({"metrics": [{"id": "M-01", "value": 0.5}]}), encoding="utf-8")
    run_eval.main(["--mode", "offline", "--queries", "G-01", "--compare-to", str(previous)])
    report = next(reports_in_tmp.glob("*-offline.md")).read_text(encoding="utf-8")
    assert "| Change |" in report
    assert "+50.0 pts" in report


def test_unknown_query_id_is_rejected():
    """A typo in --queries must not silently run nothing."""
    with pytest.raises(ValueError, match="G-99"):
        run_eval.main(["--mode", "offline", "--queries", "G-99"])
