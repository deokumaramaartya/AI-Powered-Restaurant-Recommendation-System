from __future__ import annotations

import time
from typing import Callable, Protocol

import groq

from app.core.config import Settings
from app.models.llm import CallStats, FilterDecision, LLMError, LLMParseError, LLMResult, Preferences
from app.models.recommendation import RecommendationRequest
from app.models.restaurant import Restaurant
from app.services.interpret import build_interpret_messages, parse_filter_decision
from app.services.parser import parse_llm_output
from app.services.prompt import build_messages, restore_ids, result_count

RETRY_BACKOFF_SECONDS = 0.5
# A retry is only worth making if it leaves at least this much time for the call itself.
MIN_RETRY_CALL_SECONDS = 2.0


class LLMClient(Protocol):
    model: str

    def rank(
        self, preferences: Preferences, candidates: list[Restaurant], stats: CallStats | None = None
    ) -> LLMResult: ...

    def interpret(
        self,
        request: RecommendationRequest,
        vocab: dict,
        stats: CallStats | None = None,
    ) -> FilterDecision: ...


def _retry_after(error: groq.APIStatusError) -> float | None:
    value = error.response.headers.get("retry-after")
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


def _error_code(error: groq.APIStatusError) -> str | None:
    body = error.body if isinstance(error.body, dict) else {}
    details = body.get("error", body)
    return details.get("code") if isinstance(details, dict) else None


class GroqLLMClient:
    def __init__(
        self,
        sdk,
        *,
        model: str,
        temperature: float,
        reasoning_effort: str,
        max_completion_tokens: int,
        timeout_seconds: float,
        deadline_seconds: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.sdk = sdk
        self.model = model
        self._temperature = temperature
        self._reasoning_effort = reasoning_effort
        self._max_completion_tokens = max_completion_tokens
        self._timeout = timeout_seconds
        self._deadline = deadline_seconds
        self._clock = clock
        self._sleep = sleep

    @classmethod
    def from_settings(cls, settings: Settings, *, sdk=None, clock=time.monotonic, sleep=time.sleep) -> GroqLLMClient:
        if sdk is None:
            sdk = groq.Groq(api_key=settings.groq_api_key, max_retries=0, timeout=settings.llm_timeout_seconds)
        return cls(
            sdk,
            model=settings.groq_model,
            temperature=settings.llm_temperature,
            reasoning_effort=settings.llm_reasoning_effort,
            max_completion_tokens=settings.llm_max_completion_tokens,
            timeout_seconds=settings.llm_timeout_seconds,
            deadline_seconds=settings.llm_deadline_seconds,
            clock=clock,
            sleep=sleep,
        )

    def rank(self, preferences: Preferences, candidates: list[Restaurant], stats: CallStats | None = None) -> LLMResult:
        stats = stats if stats is not None else CallStats()
        stats.requested_count = result_count(preferences, candidates)
        completion = self._invoke(build_messages(preferences, candidates), stats)
        return restore_ids(self._read(completion, stats), candidates)

    def interpret(
        self,
        request: RecommendationRequest,
        vocab: dict,
        stats: CallStats | None = None,
    ) -> FilterDecision:
        stats = stats if stats is not None else CallStats()
        completion = self._invoke(build_interpret_messages(request, vocab), stats)
        self._record(completion, stats)
        return parse_filter_decision(stats.raw_output)

    def _invoke(self, messages: list[dict], stats: CallStats):
        deadline = self._clock() + self._deadline
        while True:
            stats.attempts += 1
            remaining = deadline - self._clock()
            if remaining <= 0:
                raise LLMError("timeout", "LLM deadline reached before the call")
            try:
                return self._create(messages, timeout=min(self._timeout, remaining))
            except (groq.RateLimitError, groq.InternalServerError) as error:
                kind = "rate_limit" if isinstance(error, groq.RateLimitError) else "server"
                backoff = max(RETRY_BACKOFF_SECONDS, _retry_after(error) or 0.0)
                if stats.attempts == 1 and backoff + MIN_RETRY_CALL_SECONDS <= deadline - self._clock():
                    self._sleep(backoff)
                    continue
                raise LLMError(kind, f"Groq returned {error.status_code}") from error

    def _create(self, messages: list[dict], *, timeout: float):
        try:
            return self.sdk.chat.completions.create(
                model=self.model,
                messages=messages,
                response_format={"type": "json_object"},
                temperature=self._temperature,
                reasoning_effort=self._reasoning_effort,
                include_reasoning=False,
                max_completion_tokens=self._max_completion_tokens,
                timeout=timeout,
            )
        except (groq.RateLimitError, groq.InternalServerError):
            raise
        except groq.APITimeoutError as error:
            raise LLMError("timeout", "Groq call timed out") from error
        except groq.APIConnectionError as error:
            raise LLMError("network", "could not reach Groq") from error
        except (groq.AuthenticationError, groq.PermissionDeniedError) as error:
            raise LLMError("auth", f"Groq rejected the API key ({error.status_code})") from error
        except groq.NotFoundError as error:
            raise LLMError("not_found", f"Groq model {self.model} was not found (404)") from error
        except groq.APIStatusError as error:
            if _error_code(error) == "json_validate_failed":
                raise LLMParseError("Groq JSON mode rejected the model output") from error
            raise LLMError("bad_request", f"Groq returned {error.status_code}") from error

    def _record(self, completion, stats: CallStats) -> None:
        usage = getattr(completion, "usage", None)
        details = getattr(usage, "completion_tokens_details", None)
        stats.prompt_tokens = getattr(usage, "prompt_tokens", None)
        stats.completion_tokens = getattr(usage, "completion_tokens", None)
        stats.reasoning_tokens = getattr(details, "reasoning_tokens", None)
        choice = completion.choices[0]
        stats.finish_reason = choice.finish_reason
        stats.raw_output = choice.message.content
        if choice.finish_reason == "length":
            raise LLMParseError("reply was cut off by LLM_MAX_COMPLETION_TOKENS")

    def _read(self, completion, stats: CallStats) -> LLMResult:
        self._record(completion, stats)
        return parse_llm_output(stats.raw_output)
