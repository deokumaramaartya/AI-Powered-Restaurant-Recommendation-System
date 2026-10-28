"""AI judge: rubric scoring of explanations and pairwise list comparison (eval.md section 4.2).

The judge runs on EVAL_JUDGE_MODEL at temperature 0. It never sees which ranker produced a list: pairwise
comparisons show restaurant facts only, because the fallback ranker's template explanations would give it away.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import groq

from app.core.config import Settings

DIMENSIONS = ("specificity", "faithfulness", "preference_fit", "clarity")
CALIBRATION_PATH = Path(__file__).resolve().parent / "golden" / "calibration.json"
# Agreement means the judge is within this many points of the reviewers' average (eval.md section 4.3).
AGREEMENT_TOLERANCE = 1.0
AGREEMENT_TARGET = 0.8
JUDGE_TIMEOUT_SECONDS = 30.0
RATE_LIMIT_WAIT_SECONDS = 10.0

FACT_FIELDS = (
    "name", "cuisines", "rating", "votes", "cost_for_two", "rest_type", "listing_types",
    "online_order", "book_table", "dish_liked",
)

RUBRIC_PROMPT = """You grade explanations written by a restaurant recommendation assistant for Bangalore.
You receive the user's request, the assistant's summary, and for each recommended restaurant its data record and the explanation shown to the user.

Score every explanation from 1 to 5 on each dimension:
- specificity: 1 = generic text that could describe any restaurant; 3 = mentions one concrete fact (rating, cost, cuisine, dish, or type); 5 = uses two or more concrete facts that distinguish this restaurant.
- faithfulness: 1 = states something the data contradicts, or claims facts not in the data (such as parking, ambience, or opening hours); 3 = accurate but vague; 5 = every claim is supported by the data shown.
- preference_fit: 1 = ignores the user's stated preferences; 3 = addresses some preferences; 5 = clearly connects the restaurant to each relevant preference, including the free text.
- clarity: 1 = hard to read, over 2 sentences, or repetitive; 3 = readable but wordy; 5 = clear, 1 to 2 sentences, no filler.

Use the whole scale. Give a one-sentence reason for each score.
Respond with a single JSON object, no other text:
{"scores": [{"id": "e1", "specificity": {"score": 4, "reason": "..."}, "faithfulness": {"score": 5, "reason": "..."}, "preference_fit": {"score": 3, "reason": "..."}, "clarity": {"score": 5, "reason": "..."}}]}
Include one entry for every explanation id."""

PAIRWISE_PROMPT = """You compare two ranked shortlists of Bangalore restaurants made for the same user request.
Each list shows the restaurants' data in rank order. Decide which list better serves this user: the best fits near the top, the free-text preferences honoured where the data allows, and no poor fits. If they are about equally good, call a tie.
Respond with a single JSON object, no other text: {"winner": "A" | "B" | "tie", "reason": "<one sentence>"}"""


class JudgeError(Exception):
    pass


@dataclass
class RubricScore:
    restaurant_id: str
    explanation: str
    scores: dict[str, int]
    reasons: dict[str, str] = field(default_factory=dict)

    @property
    def average(self) -> float:
        return sum(self.scores.values()) / len(self.scores)


def facts(record: dict) -> dict:
    return {key: record.get(key) for key in FACT_FIELDS}


def request_text(request: dict, relaxations: list[str] | None = None) -> str:
    lines = [
        f"- Location: {request.get('location')}",
        f"- Budget: {request.get('budget') or 'any'}",
        f"- Cuisines: {', '.join(request.get('cuisines') or []) or 'any'}",
        f"- Minimum rating: {request.get('min_rating', 3.5)}",
        f"- Free-text preferences: {request.get('extra_preferences') or 'none'}",
    ]
    if relaxations:
        lines.append(f"- Filters relaxed to find enough matches: {'; '.join(relaxations)}")
    return "User request:\n" + "\n".join(lines)


def _score(value) -> int | None:
    score = value.get("score") if isinstance(value, dict) else value
    if isinstance(score, bool):
        return None
    if isinstance(score, (int, float)) and float(score).is_integer() and 1 <= score <= 5:
        return int(score)
    return None


def pairwise_outcome(ai_first: str, ai_second: str) -> float:
    """Combine two verdicts, the AI list shown as A and then as B, into an AI win score (ties are half)."""
    first = {"A": "ai", "B": "fallback"}.get(ai_first, "tie")
    second = {"A": "fallback", "B": "ai"}.get(ai_second, "tie")
    if first != second:
        return 0.5
    return {"ai": 1.0, "fallback": 0.0, "tie": 0.5}[first]


class Judge:
    def __init__(
        self,
        sdk,
        model: str,
        *,
        timeout_seconds: float = JUDGE_TIMEOUT_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.sdk = sdk
        self.model = model
        self._timeout = timeout_seconds
        self._sleep = sleep
        self.calls = 0

    @classmethod
    def from_settings(cls, settings: Settings, *, sdk=None) -> Judge:
        if sdk is None:
            sdk = groq.Groq(api_key=settings.groq_api_key, max_retries=0, timeout=JUDGE_TIMEOUT_SECONDS)
        return cls(sdk, settings.eval_judge_model)

    def _ask(self, system: str, user: str) -> dict:
        for attempt in range(2):
            self.calls += 1
            try:
                completion = self.sdk.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                    response_format={"type": "json_object"},
                    temperature=0,
                    timeout=self._timeout,
                )
            except groq.RateLimitError as error:
                if attempt == 0:
                    self._sleep(RATE_LIMIT_WAIT_SECONDS)
                    continue
                raise JudgeError(f"judge rate limited ({error.status_code})") from error
            except groq.APIError as error:
                raise JudgeError(f"judge call failed ({type(error).__name__})") from error
            content = completion.choices[0].message.content or ""
            try:
                reply = json.loads(content)
            except json.JSONDecodeError as error:
                raise JudgeError("judge reply was not JSON") from error
            if not isinstance(reply, dict):
                raise JudgeError("judge reply was not a JSON object")
            return reply
        raise JudgeError("judge rate limited")

    def score(
        self, request: dict, items: list[dict], summary: str | None, relaxations: list[str] | None = None
    ) -> list[RubricScore]:
        """items: [{"restaurant": <store record dict>, "explanation": str}] in rank order."""
        entries = [
            {"id": f"e{position}", "restaurant": facts(item["restaurant"]), "explanation": item["explanation"]}
            for position, item in enumerate(items, start=1)
        ]
        user = "\n\n".join([
            request_text(request, relaxations),
            f"Assistant summary: {summary or '(none)'}",
            "Explanations to grade (JSON):\n" + json.dumps(entries, ensure_ascii=False),
        ])
        reply = self._ask(RUBRIC_PROMPT, user)
        by_id = {str(row.get("id")): row for row in reply.get("scores", []) if isinstance(row, dict)}
        results = []
        for entry, item in zip(entries, items):
            row = by_id.get(entry["id"])
            if row is None:
                continue
            scores = {dimension: _score(row.get(dimension)) for dimension in DIMENSIONS}
            if any(value is None for value in scores.values()):
                continue
            reasons = {
                dimension: str(row[dimension].get("reason", "")) if isinstance(row[dimension], dict) else ""
                for dimension in DIMENSIONS
            }
            results.append(RubricScore(item["restaurant"]["id"], item["explanation"], scores, reasons))
        if not results:
            raise JudgeError("judge reply had no usable scores")
        return results

    def compare_once(self, request: dict, list_a: list[dict], list_b: list[dict]) -> str:
        user = "\n\n".join([
            request_text(request),
            "List A (JSON, rank order):\n" + json.dumps([facts(record) for record in list_a], ensure_ascii=False),
            "List B (JSON, rank order):\n" + json.dumps([facts(record) for record in list_b], ensure_ascii=False),
        ])
        winner = str(self._ask(PAIRWISE_PROMPT, user).get("winner", "tie")).strip()
        return winner if winner in {"A", "B"} else "tie"

    def pairwise(self, request: dict, ai_list: list[dict], fallback_list: list[dict]) -> float:
        """M-10 for one query: judged twice with the lists swapped; disagreeing verdicts count as a tie."""
        return pairwise_outcome(
            self.compare_once(request, ai_list, fallback_list),
            self.compare_once(request, fallback_list, ai_list),
        )


def agreement(rows: list[dict]) -> dict[str, float]:
    """rows: [{"human": {dimension: [scores...]}, "judge": {dimension: score}}]; share within tolerance per dimension."""
    shares = {}
    for dimension in DIMENSIONS:
        pairs = [
            (sum(row["human"][dimension]) / len(row["human"][dimension]), row["judge"][dimension])
            for row in rows
            if row["human"].get(dimension) and dimension in row["judge"]
        ]
        within = [abs(human - judge) <= AGREEMENT_TOLERANCE for human, judge in pairs]
        shares[dimension] = sum(within) / len(within) if within else 0.0
    return shares


def load_calibration(path: Path = CALIBRATION_PATH) -> dict | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def is_calibrated(judge_model: str, path: Path = CALIBRATION_PATH) -> bool:
    """M-09 to M-11 become blocking only once this judge model has passed calibration."""
    calibration = load_calibration(path)
    return bool(calibration and calibration.get("passed") and calibration.get("judge_model") == judge_model)
