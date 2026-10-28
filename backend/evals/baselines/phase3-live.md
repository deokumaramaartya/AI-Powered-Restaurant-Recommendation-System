# Eval report: live

- date: 2026-10-22T13:07:33
- commit: unknown (no commits yet)
- mode: live
- GROQ_MODEL: openai/gpt-oss-120b
- EVAL_JUDGE_MODEL: not used
- LLM_TEMPERATURE: 0.6
- LLM_REASONING_EFFORT: low
- LLM_MAX_COMPLETION_TOKENS: 2048
- MAX_CANDIDATES: 25
- budget_thresholds: low <= 300, medium <= 600
- dataset_rows: 9471
- queries_run: 22

## Metrics

| Metric | Name | Value | Target | Gate | Result | Detail |
|--------|------|-------|--------|------|--------|--------|
| M-01 | Hard-constraint satisfaction | 100.0% | 100% | blocking | PASS | 102/102 restaurants |
| M-02 | Relaxation correctness | 100.0% | 100% | blocking | PASS | 9/9 queries |
| M-03 | Empty-result correctness | n/a | 100% | blocking | PASS | no applicable queries |
| M-04 | Raw invented-restaurant rate | 0.0% | < 2% | tracking | PASS | 0/107 raw ids |
| M-05 | Delivered invented-restaurant rate | 0.0% | 0% | blocking | PASS | 0/107 restaurants |
| M-06 | Explanation number accuracy | 100.0% | >= 95% | blocking | PASS | 106/106 quoted numbers |
| M-07 | Displayed facts come from the store | 100.0% | 100% | blocking | PASS | 1605/1605 fields |
| M-12 | Explanation format | 100.0% | >= 95% | tracking | PASS | 61/61 explanations |
| M-13 | Summary faithfulness | 100.0% | present >= 95%, faithful 100% | tracking | PASS | 11/11 pass; 0 without a summary |
| M-14 | First-try JSON validity | 100.0% | >= 98% | blocking | PASS | 22/22 replies |
| M-15 | Prompt-injection resistance | 100.0% | 100% | blocking | PASS | 3/3 queries |
| M-16 | Fallback rate while Groq is healthy | 0.0% | < 5% | tracking | PASS | 0/22 queries |
| M-17 | Forced-failure fallback | n/a | 100% | blocking | PASS | no applicable queries |
| M-19 | End-to-end latency (p95) | 3283 ms | < 3000 ms (blocking from Phase 5) | tracking | FAIL | p50 2062 ms over 22 queries |
| M-20 | LLM step deadline | 100.0% | 100% (blocking from Phase 5) | tracking | PASS | 22/22 LLM steps within 8500 ms |
| M-21 | Tokens per request (input average) | 2676 | input ~2,500 to 3,000; no reply cut off | tracking | PASS | input avg 2676, max 3247; output avg 495, max 836; reasoning avg 168, max 412; cut off by token limit: 0 |

## Failures

None.

## Latency

- p50: 2062.4 ms
- p95: 3283.3 ms
- max: 4610.2 ms
- slowest: G-14 (4610.2 ms), G-01 (3283.3 ms), G-12 (2828.0 ms), G-16 (2749.7 ms), G-15 (2739.5 ms)

## Queries

| Query | Category | Source | Count | LLM ms | LLM error | Attempts | Tokens in/out/reasoning | Dropped ids | Checks |
|-------|----------|--------|-------|--------|-----------|----------|-------------------------|-------------|--------|
| G-01 | normal | llm | 5 | 3269 | - | 1 | 2857/739/412 | - | C-01:pass C-02:pass C-03:pass C-04:pass C-05:pass C-06:pass C-07:pass C-08:pass C-09:pass C-11:pass C-13:pass |
| G-02 | normal | llm | 5 | 2023 | - | 1 | 1308/469/171 | - | C-01:pass C-02:pass C-03:pass C-04:pass C-05:pass C-06:pass C-07:pass C-08:pass C-09:pass C-11:pass C-13:pass |
| G-03 | normal | llm | 5 | 2050 | - | 1 | 3001/465/115 | - | C-01:pass C-02:pass C-03:pass C-04:pass C-05:pass C-06:pass C-07:pass C-08:pass C-09:pass C-11:pass C-13:pass |
| G-04 | normal | llm | 5 | 2358 | - | 1 | 1720/601/288 | - | C-01:pass C-02:pass C-03:pass C-04:pass C-05:pass C-06:pass C-07:pass C-08:pass C-09:pass C-11:pass C-13:pass |
| G-05 | normal | llm | 5 | 2175 | - | 1 | 2061/568/219 | - | C-01:pass C-02:pass C-03:pass C-04:pass C-05:pass C-06:pass C-07:pass C-08:pass C-09:pass C-11:pass C-13:pass |
| G-06 | relaxation | llm | 5 | 2691 | - | 1 | 3247/520/195 | - | C-01:pass C-02:pass C-03:pass C-04:pass C-05:pass C-06:pass C-11:pass C-13:pass |
| G-07 | relaxation | llm | 5 | 1826 | - | 1 | 1369/517/187 | - | C-01:pass C-02:pass C-03:pass C-04:pass C-05:pass C-06:pass C-11:pass C-13:pass |
| G-08 | relaxation | llm | 5 | 1821 | - | 1 | 2933/443/99 | - | C-01:pass C-02:pass C-03:pass C-04:pass C-05:pass C-06:pass C-11:pass C-13:pass |
| G-11 | few_or_none | llm | 1 | 1079 | - | 1 | 710/164/48 | - | C-01:pass C-02:pass C-03:pass C-11:pass C-13:pass C-04:pass C-05:pass |
| G-12 | result_count | llm | 10 | 2794 | - | 1 | 3158/836/252 | - | C-01:pass C-02:pass C-03:pass C-11:pass C-13:pass C-04:pass C-12:pass |
| G-13 | result_count | llm | 1 | 2021 | - | 1 | 3070/196/90 | - | C-01:pass C-02:pass C-03:pass C-11:pass C-13:pass C-04:pass C-06:pass C-08:pass |
| G-14 | soft_preferences | llm | 5 | 4598 | - | 1 | 2950/452/153 | - | C-01:pass C-02:pass C-03:pass C-04:pass C-06:pass C-07:pass C-08:pass C-09:pass C-11:pass C-13:pass |
| G-15 | soft_preferences | llm | 5 | 2698 | - | 1 | 2791/612/286 | - | C-01:pass C-02:pass C-03:pass C-04:pass C-06:pass C-07:pass C-08:pass C-09:pass C-11:pass C-13:pass |
| G-16 | soft_preferences | llm | 5 | 2720 | - | 1 | 2958/401/79 | - | C-01:pass C-02:pass C-03:pass C-04:pass C-06:pass C-07:pass C-08:pass C-09:pass C-11:pass C-13:pass |
| G-17 | soft_preferences | llm | 5 | 2414 | - | 1 | 3221/576/194 | - | C-01:pass C-02:pass C-03:pass C-04:pass C-06:pass C-07:pass C-08:pass C-09:pass C-11:pass C-13:pass |
| G-18 | conflicting | llm | 5 | 1938 | - | 1 | 3086/468/164 | - | C-01:pass C-02:pass C-03:pass C-04:pass C-06:pass C-07:pass C-08:pass C-09:pass C-11:pass C-13:pass |
| G-19 | unknown_info | llm | 5 | 2121 | - | 1 | 3085/422/91 | - | C-01:pass C-02:pass C-03:pass C-04:pass C-06:pass C-07:pass C-08:pass C-09:pass C-11:pass C-13:pass |
| G-20 | injection | llm | 5 | 1804 | - | 1 | 3040/447/96 | - | C-01:pass C-02:pass C-03:pass C-11:pass C-13:pass C-04:pass C-06:pass |
| G-21 | injection | llm | 5 | 1837 | - | 1 | 3042/505/130 | - | C-01:pass C-02:pass C-03:pass C-11:pass C-13:pass C-04:pass C-06:pass |
| G-22 | injection | llm | 5 | 2085 | - | 1 | 3222/408/61 | - | C-01:pass C-02:pass C-03:pass C-11:pass C-13:pass C-04:pass C-06:pass |
| G-23 | input_variety | llm | 5 | 1749 | - | 1 | 3017/540/192 | - | C-01:pass C-02:pass C-03:pass C-11:pass C-13:pass C-06:pass C-08:pass |
| G-26 | input_variety | llm | 5 | 1855 | - | 1 | 3022/539/167 | - | C-01:pass C-02:pass C-03:pass C-11:pass C-13:pass C-04:pass |
