import math

import pytest

from evals.metrics import QueryRun, blocking_failures, compute, label_coverage, ndcg


def by_id(metrics):
    return {metric.id: metric for metric in metrics}


def live_run(query_id, *, source="llm", llm_error=None, raw_total=5, raw_invented=0, llm_ms=1500.0,
             latency_ms=1600.0, prompt_tokens=2500, completion_tokens=400, reasoning_tokens=150,
             finish_reason="stop", category="normal", candidates=10):
    return QueryRun(
        query_id=query_id, category=category, source=source, count=5, latency_ms=latency_ms,
        llm_called=True, llm_error=llm_error, llm_ms=llm_ms, raw_total=raw_total, raw_invented=raw_invented,
        prompt_tokens=prompt_tokens, completion_tokens=completion_tokens, reasoning_tokens=reasoning_tokens,
        finish_reason=finish_reason, candidates=candidates,
    )


def test_first_try_json_validity_counts_only_replies_that_arrived():
    """A timeout is not a JSON failure; an invalid reply is (M-14)."""
    runs = [live_run("a"), live_run("b"), live_run("c", source="fallback", llm_error="invalid_json"),
            live_run("d", source="fallback", llm_error="timeout")]
    m14 = by_id(compute(runs, mode="live"))["M-14"]
    assert m14.value == 2 / 3
    assert not m14.passed
    assert m14 in blocking_failures(compute(runs, mode="live"))


def test_raw_invented_rate_uses_ids_before_the_parser():
    """M-04 measures what the model invented, even though the parser hid it from users."""
    runs = [live_run("a", raw_total=5, raw_invented=1), live_run("b", raw_total=5)]
    assert by_id(compute(runs, mode="live"))["M-04"].value == 0.1


def test_fallback_rate_ignores_forced_failures_and_empty_results():
    """M-16 is about healthy Groq calls only."""
    runs = [live_run("a"), live_run("b", source="fallback", llm_error="rate_limit"),
            live_run("c", source="fallback", category="forced_fallback"),
            live_run("d", source="fallback", candidates=0)]
    assert by_id(compute(runs, mode="live"))["M-16"].value == 0.5


def test_latency_and_tokens_are_reported_in_their_own_units():
    runs = [live_run("a", latency_ms=1000.0), live_run("b", latency_ms=3000.0, prompt_tokens=3500)]
    metrics = by_id(compute(runs, mode="live"))
    assert metrics["M-19"].unit == "ms"
    assert metrics["M-19"].value == 3000.0
    assert metrics["M-21"].unit == "tokens"
    assert metrics["M-21"].value == 3000
    assert "reasoning" in metrics["M-21"].detail


def test_reply_cut_off_by_the_token_limit_fails_the_token_metric():
    runs = [live_run("a", finish_reason="length", source="fallback", llm_error="invalid_json")]
    assert not by_id(compute(runs, mode="live"))["M-21"].passed


def test_live_only_metrics_are_left_out_offline():
    """Scripted replies say nothing about Groq's JSON validity or latency."""
    ids = set(by_id(compute([live_run("a")], mode="offline")))
    assert not ids & {"M-04", "M-14", "M-16", "M-19", "M-21"}


def test_latency_and_deadline_block_from_phase_5():
    """M-19 and M-20 are release gates now (eval.md section 8.1)."""
    runs = [live_run("a", latency_ms=1000.0), live_run("b", latency_ms=3500.0, llm_ms=9000.0)]
    metrics = compute(runs, mode="live")
    failing = {metric.id for metric in blocking_failures(metrics)}
    assert {"M-19", "M-20"} <= failing


def run_with(query_id, ids, **fields):
    fields.setdefault("source", "llm")
    return QueryRun(query_id=query_id, category="normal", ids=list(ids), **fields)


def test_stability_is_reported_only_for_repeated_queries():
    assert "M-18" not in by_id(compute([run_with("a", "xyz")]))
    runs = [run_with("a", "abcde", repeat=0), run_with("a", "abcde", repeat=1), run_with("a", "abcdf", repeat=2),
            run_with("b", "vwxyz", repeat=0), run_with("b", "wvxyz", repeat=1), run_with("b", "vwxyz", repeat=2)]
    m18 = by_id(compute(runs))["M-18"]
    assert m18.gate == "tracking"
    assert m18.value == pytest.approx((1 + 2 / 3 + 2 / 3) / 3 / 2 + 1 / 2)
    assert "same rank-1 pick in 50%" in m18.detail
    assert not m18.passed


def test_stability_compares_ai_rankings_only():
    """A run that fell back measures the fallback ranker, so it must not drag down the AI's stability."""
    runs = [run_with("a", "abcde", repeat=0), run_with("a", "abcde", repeat=1),
            run_with("a", "vwxyz", repeat=2, source="fallback")]
    m18 = by_id(compute(runs))["M-18"]
    assert m18.value == pytest.approx(1.0)
    assert m18.passed


def test_ndcg_matches_the_eval_formula():
    """Gain (2^grade - 1) / log2(i + 1); ideal order from every graded restaurant; ungraded ones left out."""
    grades = {"a": 3, "b": 2, "c": 0, "d": 1}
    assert ndcg(["a", "b", "d"], grades, 3) == pytest.approx(1.0)
    expected = (3 + 7 / math.log2(3)) / (7 + 3 / math.log2(3))
    assert ndcg(["b", "x", "a"], grades, 2) == pytest.approx(expected)
    assert ndcg(["c"], {"c": 0}, 1) is None


def test_ndcg_metric_compares_against_the_fallback_ranker():
    labels = {"a": {"r1": 3, "r2": 2, "r3": 0}}
    better = run_with("a", ["r1", "r2"], request={"top_k": 2}, fallback_ids=["r3", "r2"])
    worse = run_with("a", ["r3", "r2"], request={"top_k": 2}, fallback_ids=["r1", "r2"])
    assert by_id(compute([better], labels=labels))["M-08"].passed
    assert not by_id(compute([worse], labels=labels))["M-08"].passed
    assert "M-08" not in by_id(compute([better]))


def test_label_coverage_lists_ungraded_restaurants():
    runs = [run_with("a", ["r1", "r2"], results=[{"id": "r1", "name": "One"}, {"id": "r2", "name": "Two"}])]
    (row,) = label_coverage(runs, {"a": {"r1": 2}})
    assert row["coverage"] == 0.5
    assert row["missing"] == {"r2": "Two"}


def scored(preference_fit, others=5):
    return {"restaurant_id": "r", "explanation": "e", "reasons": {},
            "scores": {"specificity": others, "faithfulness": others, "preference_fit": preference_fit, "clarity": others}}


def test_judge_metrics_are_tracking_until_the_judge_is_calibrated():
    """eval.md 4.3: M-09 to M-11 only block once the judge agrees with people."""
    runs = [run_with("a", ["r"], request={"extra_preferences": "quiet"}, judge_scores=[scored(2)], pairwise=0.0)]
    tracking = by_id(compute(runs, judged=True))
    assert {tracking[m].gate for m in ("M-09", "M-10", "M-11")} == {"tracking"}
    assert "not calibrated" in tracking["M-11"].detail
    blocking = {metric.id for metric in blocking_failures(compute(runs, judged=True, calibrated=True))}
    assert {"M-09", "M-10", "M-11"} <= blocking


def test_judge_metric_values():
    runs = [
        run_with("a", ["r"], request={"extra_preferences": "quiet"}, judge_scores=[scored(4), scored(5)], pairwise=1.0),
        run_with("b", ["r"], request={}, judge_scores=[scored(1)], pairwise=None),
        run_with("c", ["r"], request={"extra_preferences": "cheap"}, judge_scores=[], pairwise=0.5),
    ]
    metrics = by_id(compute(runs, judged=True))
    assert metrics["M-09"].value == 4.5
    assert metrics["M-10"].value == 0.75
    assert metrics["M-11"].value == pytest.approx((4.75 + 5 + 4) / 3)
    assert not metrics["M-11"].passed
    assert "1 (33%) score 2 or lower" in metrics["M-11"].detail
