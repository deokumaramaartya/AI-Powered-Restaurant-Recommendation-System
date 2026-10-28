import json
from types import SimpleNamespace

import groq
import httpx
import pytest

from evals.judge import DIMENSIONS, Judge, JudgeError, agreement, is_calibrated, pairwise_outcome
from tests.conftest import make_settings

REQUEST = {"location": "Jayanagar", "budget": "medium", "min_rating": 4.0,
           "extra_preferences": "family-friendly, casual dining with table booking"}


class FakeJudgeSDK:
    """Stands in for groq.Groq: records each call and replies with scripted JSON (or raises)."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        content = reply if isinstance(reply, str) else json.dumps(reply)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def rubric(*rows):
    return {"scores": [
        {"id": f"e{position}", **{d: {"score": s, "reason": f"{d} reason"} for d, s in zip(DIMENSIONS, scores)}}
        for position, scores in enumerate(rows, start=1)
    ]}


def items(store, *ids):
    return [{"restaurant": store.get(rid).model_dump(mode="json"), "explanation": f"Why {rid}."} for rid in ids]


def rate_limited():
    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    return groq.RateLimitError("slow down", response=httpx.Response(429, request=request), body=None)


def test_judge_calls_the_judge_model_at_temperature_zero_in_json_mode(store):
    sdk = FakeJudgeSDK(rubric((4, 5, 3, 5)))
    judge = Judge.from_settings(make_settings(), sdk=sdk)
    judge.score(REQUEST, items(store, "jay-1"), "A summary.")
    call = sdk.calls[0]
    assert call["model"] == "qwen/qwen3.8-27b"
    assert call["temperature"] == 0
    assert call["response_format"] == {"type": "json_object"}


def test_judge_sees_preferences_data_explanations_and_summary(store):
    sdk = FakeJudgeSDK(rubric((4, 5, 3, 5)))
    Judge(sdk, "judge").score(REQUEST, items(store, "jay-1"), "Family picks.", ["Lowered minimum rating"])
    user = sdk.calls[0]["messages"][1]["content"]
    assert "family-friendly, casual dining with table booking" in user
    assert store.get("jay-1").name in user
    assert "Why jay-1." in user
    assert "Family picks." in user
    assert "Lowered minimum rating" in user


def test_scores_map_back_to_restaurants_with_reasons(store):
    sdk = FakeJudgeSDK(rubric((4, 5, 3, 5), (2, 2, 1, 4)))
    scores = Judge(sdk, "judge").score(REQUEST, items(store, "jay-1", "jay-2"), None)
    assert [score.restaurant_id for score in scores] == ["jay-1", "jay-2"]
    assert scores[0].scores == {"specificity": 4, "faithfulness": 5, "preference_fit": 3, "clarity": 5}
    assert scores[1].reasons["preference_fit"] == "preference_fit reason"
    assert scores[1].average == 2.25


def test_out_of_range_or_missing_scores_drop_that_explanation(store):
    reply = rubric((4, 5, 3, 5), (4, 5, 3, 5))
    reply["scores"][1]["clarity"]["score"] = 7
    scores = Judge(FakeJudgeSDK(reply), "judge").score(REQUEST, items(store, "jay-1", "jay-2"), None)
    assert [score.restaurant_id for score in scores] == ["jay-1"]


@pytest.mark.parametrize("reply", ["not json", "[1, 2]", {"scores": []}])
def test_unusable_judge_replies_raise(store, reply):
    with pytest.raises(JudgeError):
        Judge(FakeJudgeSDK(reply), "judge").score(REQUEST, items(store, "jay-1"), None)


def test_rate_limit_is_retried_once_after_a_wait(store):
    waits = []
    sdk = FakeJudgeSDK(rate_limited(), rubric((4, 4, 4, 4)))
    scores = Judge(sdk, "judge", sleep=waits.append).score(REQUEST, items(store, "jay-1"), None)
    assert len(scores) == 1
    assert len(waits) == 1
    with pytest.raises(JudgeError):
        Judge(FakeJudgeSDK(rate_limited(), rate_limited()), "judge", sleep=waits.append).score(
            REQUEST, items(store, "jay-1"), None)


@pytest.mark.parametrize(
    ("first", "second", "expected"),
    [("A", "B", 1.0), ("B", "A", 0.0), ("tie", "tie", 0.5), ("A", "A", 0.5), ("B", "B", 0.5), ("A", "tie", 0.5)],
)
def test_pairwise_verdicts_from_both_orders_combine(first, second, expected):
    """eval.md 4.2: each pair is judged twice with A and B swapped; disagreement is a tie."""
    assert pairwise_outcome(first, second) == expected


def test_pairwise_shows_facts_only_and_swaps_the_lists(store):
    ai = [store.get("jay-1").model_dump(mode="json")]
    fallback = [store.get("jay-2").model_dump(mode="json")]
    sdk = FakeJudgeSDK({"winner": "A"}, {"winner": "B"})
    assert Judge(sdk, "judge").pairwise(REQUEST, ai, fallback) == 1.0
    first, second = (call["messages"][1]["content"] for call in sdk.calls)
    ai_name, fallback_name = ai[0]["name"], fallback[0]["name"]
    assert first.index(ai_name) < first.index(fallback_name)
    assert second.index(fallback_name) < second.index(ai_name)
    assert "explanation" not in first


def test_agreement_is_the_share_within_one_point_of_the_reviewer_average():
    rows = [
        {"human": {d: [4, 5] for d in DIMENSIONS}, "judge": {d: 5 for d in DIMENSIONS}},
        {"human": {d: [2, 2] for d in DIMENSIONS}, "judge": {d: 4 for d in DIMENSIONS}},
    ]
    assert agreement(rows) == {d: 0.5 for d in DIMENSIONS}


def test_calibration_must_match_the_judge_model(tmp_path):
    path = tmp_path / "calibration.json"
    assert not is_calibrated("judge-a", path)
    path.write_text(json.dumps({"judge_model": "judge-a", "passed": True}), encoding="utf-8")
    assert is_calibrated("judge-a", path)
    assert not is_calibrated("judge-b", path)
    path.write_text(json.dumps({"judge_model": "judge-a", "passed": False}), encoding="utf-8")
    assert not is_calibrated("judge-a", path)
