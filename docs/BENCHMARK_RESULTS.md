# Benchmark Results

All values below were produced on this workstation on 2026-11-18. Each row
names the artifact that contains the raw data. Mocked and live results are kept
separate. Nothing in this document is a simulated or hand-entered score.

- Machine: Windows 11 (`Windows-11-10.0.26300-SP0`), 8 logical CPUs
- Python: 3.13.13
- Model: `qwen3:4b` through Ollama at `http://localhost:11434`
- Search: DuckDuckGo (`max_results=3`, page fetch enabled, 12-page budget)
- Results directory: `benchmarks/results/`

---

## 1. Metric self-check (mocked)

Artifact: `benchmarks/results/golden_eval_mocked_20261118_074225.json`

This run does **not** score the agent. It validates the retrieval metric
implementation with deterministic perfect, degraded, and unlabelled fixtures.

| Check | Result |
|---|---|
| Perfect Recall@5 = 1.0 | PASS |
| Perfect MRR = 1.0 | PASS |
| Perfect NDCG@5 = 1.0 | PASS |
| Degraded MRR < perfect | PASS |
| Unlabelled cases excluded from aggregate | PASS |
| Unlabelled values are `null` | PASS |
| Fixture cases (labelled / unlabelled) | 20 / 40 |
| Overall | **6/6 checks passed** |

Dataset validation in the same artifact: 118 cases, all eight categories
present, 78 verified-reference, 40 synthetic, 78 retrieval labels available,
40 unavailable.

---

## 2. Live golden evaluation

Artifact: `benchmarks/results/golden_eval_live_20261118_091654.json`

Three verified-reference cases were selected deliberately:

| Case | Category | Question theme | Status | E2E seconds |
|---|---|---|---|---:|
| G001 | factual_research | RAG architecture | complete | 499.00 |
| G005 | comparison | Speculative decoding vs Medusa | complete | 484.36 |
| G014 | technical_research | Direct Preference Optimization | complete | 411.79 |

### Retrieval (exact-URL labels)

| Metric | Value | Measured cases |
|---|---:|---:|
| Recall@1 / @3 / @5 / @10 | 0.0000 / 0.1667 / 0.1667 / 0.1667 | 3 |
| Precision@1 / @3 / @5 / @10 | 0.0000 / 0.1111 / 0.0667 / 0.0333 | 3 |
| NDCG@1 / @3 / @5 / @10 | 0.0000 / 0.1290 / 0.1290 / 0.1290 | 3 |
| MRR | 0.1667 | 3 |

These are real measurements, not missing values. The labels are exact URLs from
the verified v3 dataset. In this run G005 returned the labelled Medusa paper at
rank 2, so one of two relevant URLs was found within the top 10. G001 and G014
returned no labelled URL. For G001, the labels were:

- `https://arxiv.org/abs/2005.11401`
- `https://docs.langchain.com/oss/python/langchain/rag`
- `https://github.com/facebookresearch/faiss/wiki`
- `https://arxiv.org/abs/1603.09320`

The live run retrieved Wikipedia, IBM, DeepLearning.AI, and vendor blog pages
instead. Those pages are topically related, but they are not the labelled URLs.
Under strict exact-URL scoring they count as misses. An earlier three-case run
(`golden_eval_live_20261118_080207.json`) produced 0.0 for every retrieval
metric; the latest run found one label. Search results vary between runs.

**Interpretation:** this measures agreement with a fixed verified URL set. It
does not prove the retrieved pages were useless. The strict result is reported
unchanged; the labels were not edited after seeing the output.

### Answer quality

| Component | Mean | Measured cases |
|---|---:|---:|
| Faithfulness | 0.6667 | 3 |
| Answer relevance | 0.6634 | 3 |
| Contextual precision | 1.0000 | 3 |
| Contextual recall | 0.7778 | 3 |
| Citation support | 0.6667 | 3 |
| Evidence coverage | 0.8175 | 3 |
| Unsupported claims (lower is better) | 0.0000 | 3 |
| **Overall (weighted, measured only)** | **0.7402** | 3 |

Per case:

| Case | Overall | Faithfulness | Relevance | Contextual precision | Contextual recall | Citation support |
|---|---:|---:|---:|---:|---:|---:|
| G001 | 0.7105 | 0.6667 | 0.6200 | 1.0000 | 0.6667 | 0.6667 |
| G005 | 0.7005 | 0.6667 | 0.5577 | 1.0000 | 0.6667 | 0.6667 |
| G014 | 0.8097 | 0.6667 | 0.8125 | 1.0000 | 1.0000 | 0.6667 |

All components were measured for these three cases. The lower citation-support
scores in all three cases are lexical coverage outcomes, not proof that the cited
pages are false.

### Latency and tokens

| Metric | Value |
|---|---:|
| Samples | 3 |
| Mean | 465.05 s |
| P50 | 484.36 s |
| P95 | 497.54 s |
| Min / max | 411.79 / 499.00 s |
| Ollama calls | 6 |
| Calls exposing token counts | 1 of 6 |
| Prompt tokens | 2,659 |
| Completion tokens | 1,200 |
| Total tokens | 3,859 |

Token usage is **partially measured**: only one of six Ollama responses exposed
`prompt_eval_count` / `eval_count`. The counts are not estimated for the other
calls.

One G005 editor response was truncated and required conservative JSON repair
before falling back to a valid structured report. It still completed. This is
recorded in the live log and as `generation_mode="json_repaired"`, not hidden.

---

## 3. Mocked performance

Artifact: `benchmarks/results/performance_benchmark_mocked_20261118_085229.json`

Configuration: 8 repetitions × 3 workload sizes (small/medium/large), 2 warm-up
rounds excluded from samples, concurrency 1.

| Metric | Value |
|---|---:|
| Recorded runs | 24 |
| Successful completions | 24 |
| Failed executions | 0 |
| Success rate | 1.0 |
| Search retries | 0 |
| End-to-end P50 | 0.0407 s |
| End-to-end P95 | 0.0422 s |
| End-to-end mean | 0.0408 s |
| Planner P50 / P95 | 0.0000 / 0.0000 s |
| Research P50 / P95 | 0.0000 / 0.0000 s |
| Editor P50 / P95 | 0.0000 / 0.0009 s |
| Search network P50 / P95 | 0.1500 / 0.3000 s |
| SQLite round-trip P50 / P95 | 0.0070 / 0.0080 s |
| SQLite writes | 24 / 24 |
| Peak memory (max) | 346.7 KB |
| Peak memory (mean) | 339.4 KB |
| Token usage | NOT MEASURED (mocked stubs make no Ollama calls) |

Per workload:

| Size | Runs | E2E P50 | E2E P95 |
|---|---:|---:|---:|
| Small | 8 | 0.0400 s | 0.0415 s |
| Medium | 8 | 0.0410 s | 0.0415 s |
| Large | 8 | 0.0409 s | 0.0441 s |

Failure scenarios exercised in the same run:

| Scenario | Outcome |
|---|---|
| Search provider failure | Completed with no-evidence report |
| Simulated Ollama timeout | Failed cleanly with `TimeoutError` |
| Invalid editor output | Completed with unstructured fallback |

### Concurrency smoke test

Artifact: `benchmarks/results/performance_benchmark_mocked_20261118_074225.json`

Configuration: 2 repetitions × 3 sizes, 1 warm-up, concurrency 3.

| Metric | Value |
|---|---:|
| Runs | 6 |
| Success rate | 1.0 |
| E2E P50 | 0.0338 s |
| E2E P95 | 0.0564 s |
| SQLite writes | 6 / 6 |

This confirms the harness accepts concurrent workers. It is not a load test and
does not establish production concurrency limits.

### Variance note

Four 24-run mocked samples on the same machine produced E2E P50 values of
0.2074 s, 0.0921 s, 0.0914 s, and 0.0407 s. Host load, filesystem cache state,
and process warm-up explain part of the spread, but the range is large. Treat
P50/P95 from this harness as relative indicators, not stable service-level
numbers. The sample count and raw artifact are always reported.

---

## 4. Live performance

**NOT MEASURED as a dedicated performance run.**

Live end-to-end latency is available from the three-case golden evaluation
above (P50 484.36 s, P95 497.54 s), but it is not the dedicated harness with
repetitions and stage isolation. The live performance command exists and
requires `--confirm-live`; no live performance artifact was produced for this
report.

---

## 5. Baseline versus optimized

**NOT MEASURED as a controlled comparison.**

The comparison helper is implemented and tested:

```powershell
py -3.13 benchmarks/run_performance_benchmark.py `
  --baseline benchmarks/results/<baseline>.json `
  --candidate benchmarks/results/<candidate>.json
```

It reports baseline, candidate, absolute delta, and percent change, and states
that the result is observational. No controlled A/B with identical inputs and
environment was run, so no performance improvement is claimed.

A demonstration comparison between two mocked artifacts exists at
`benchmarks/results/performance_comparison_observational.json`:

| Metric | Baseline | Candidate | Delta | Percent |
|---|---:|---:|---:|---:|
| E2E P50 | 0.091411 s | 0.040708 s | -0.050703 s | -55.47% |
| E2E P95 | 0.156216 s | 0.042184 s | -0.114032 s | -73.00% |
| Success rate | 1.0 | 1.0 | 0.0 | 0.0% |

The two runs had the same inputs and machine but were not a controlled
alternating A/B, and the mocked harness has shown large run-to-run variance.
The difference is therefore **observational**. It is not evidence that any code
change caused a speed-up, and it must not be reported as one.

---

## 6. What is not measured

| Item | Status |
|---|---|
| Full 118-case live golden evaluation | NOT MEASURED |
| Full 40-question v3 benchmark end-to-end | NOT MEASURED |
| Dedicated live performance repetitions | NOT MEASURED |
| PostgreSQL latency under live load | NOT MEASURED |
| Multi-user / production load | NOT MEASURED |
| Token usage for every Ollama call | PARTIALLY MEASURED (1 of 6 calls exposed counts) |
| Semantic retrieval relevance | NOT MEASURED (exact-URL labels only) |
| Human answer-quality judgement | NOT MEASURED |
| LLM-judge scores | NOT MEASURED (optional component, not run) |
| Controlled baseline-vs-optimized A/B | NOT MEASURED |

---

## 7. Reproduce

```powershell
py -3.13 benchmarks/run_golden_evaluation.py --mode mocked
py -3.13 benchmarks/run_golden_evaluation.py --mode live --confirm-live --ids G001,G005,G014
py -3.13 benchmarks/run_performance_benchmark.py --repetitions 8 --warmup 2 --concurrency 1
py -3.13 benchmarks/run_performance_benchmark.py --repetitions 2 --warmup 1 --concurrency 3
```

Results are host- and model-dependent. Re-running will produce different
latencies and, for live modes, different retrieved URLs.
