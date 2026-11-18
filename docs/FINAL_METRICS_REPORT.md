# Final Metrics Report

Date: 2026-11-18
Scope: evaluation and benchmarking framework for the AI Research Agent
Machine: Windows 11 (`Windows-11-10.0.26300-SP0`), 8 logical CPUs, Python 3.13.13
Model: `qwen3:4b` via Ollama at `http://localhost:11434`
Search: DuckDuckGo, `max_results=3`, page fetch enabled, 12-page budget
Database baseline: `research_sessions=19`, `research_reports=19`,
`research_sources=167` (checked before and after the suite; unchanged)

This report consolidates measured results. Detailed methodology and evidence are
in:

- [`docs/EVALUATION_FRAMEWORK.md`](EVALUATION_FRAMEWORK.md)
- [`docs/BENCHMARK_RESULTS.md`](BENCHMARK_RESULTS.md)
- [`docs/SECURITY_EVALUATION.md`](SECURITY_EVALUATION.md)

No value in this report is hand-entered or simulated. Every measured value has a
timestamped JSON artifact under `benchmarks/results/`.

---

## 1. What was implemented

| Requirement | Implementation | Status |
|---|---|---|
| Inspect before modifying | Existing pipeline, evaluation engine, database, tests, docs inspected | Done |
| Reuse architecture | LangGraph workflow and DB schema unchanged; existing metrics reused | Done |
| At least 100 golden questions | 118-case JSONL dataset | Done |
| Eight research categories | All eight present | Done |
| No invented references | Verified cases use only v3-verified claims/sources; synthetic cases marked | Done |
| Retrieval metrics | Recall@K, Precision@K, MRR, NDCG@K | Done |
| Missing labels separate | `null` + `not_measurable`; excluded from means | Done |
| Answer-quality metrics | Seven components plus diagnostic unsupported claims | Done |
| Optional LLM judge | Separate, versioned, graceful unavailability | Implemented, not run |
| Security suite ≥ 30 cases | 40 cases, 8 categories, full result fields | Done |
| Performance harness | E2E/stage/DB latency, P50/P95, warm-up, repetitions, concurrency, tokens, memory, metadata, comparison | Done |
| Reliability tests | 10 required scenarios | Done |
| Streamlit dashboard | Additive offline-artifact section | Done |
| Tests for new components | 55 new checks | Done |
| Preserve existing tests | All existing suites still pass | Done |
| Generate docs and update README | Four docs plus README update | Done |
| No fake scores / no causal claims | Mocked and live separated; limitations stated | Done |
| No destructive DB commands / no commit | Verified; only temp SQLite in tests; uncommitted | Done |

---

## 2. Golden dataset

Artifact: `benchmarks/datasets/golden_eval_v1.jsonl`
Manifest: `benchmarks/datasets/golden_eval_v1.manifest.json`

| Metric | Value |
|---|---:|
| Cases | 118 |
| SHA-256 | `edacf9ec8225061ce4d47403301e73c4c06aad1fafb142c67243019cc8b8637e` |
| Verified-reference cases | 78 |
| Synthetic cases | 40 |
| Retrieval labels available | 78 |
| Retrieval labels unavailable | 40 |
| Categories | 8 / 8 |
| Validation errors | 0 |

Category counts: comparison 21, multi_source_research 19, technical_research 20,
factual_research 18, recent_information 10, insufficient_evidence 10,
conflicting_sources 10, misleading_assumption 10.

Difficulty counts: Easy 40, Intermediate 54, Difficult 24.

---

## 3. Measured results

### 3.1 Retrieval metric self-check (mocked)

Artifact: `benchmarks/results/golden_eval_mocked_20261118_074225.json`

| Check | Result |
|---|---|
| 6 deterministic fixture checks | 6/6 PASS |
| Labelled / unlabelled fixtures | 20 / 40 |
| Unlabelled values | `null` |
| Unlabelled cases in aggregate mean | Excluded |

This validates the metric implementation. It is **not** an agent-quality score.

### 3.2 Live golden evaluation (3 verified cases)

Artifact: `benchmarks/results/golden_eval_live_20261118_091654.json`

| Metric | Value | Measured cases |
|---|---:|---:|
| Recall@1 / @3 / @5 / @10 | 0.0000 / 0.1667 / 0.1667 / 0.1667 | 3 |
| Precision@1 / @3 / @5 / @10 | 0.0000 / 0.1111 / 0.0667 / 0.0333 | 3 |
| NDCG@1 / @3 / @5 / @10 | 0.0000 / 0.1290 / 0.1290 / 0.1290 | 3 |
| MRR | 0.1667 | 3 |
| Faithfulness | 0.6667 | 3 |
| Answer relevance | 0.6634 | 3 |
| Contextual precision | 1.0000 | 3 |
| Contextual recall | 0.7778 | 3 |
| Citation support | 0.6667 | 3 |
| Evidence coverage | 0.8175 | 3 |
| Unsupported claims (lower is better) | 0.0000 | 3 |
| **Answer-quality overall** | **0.7402** | 3 |
| End-to-end P50 | 484.36 s | 3 |
| End-to-end P95 | 497.54 s | 3 |
| Token usage | 3,859 tokens from 1 of 6 calls | partially measured |

The retrieval values are real strict-URL results: G005 returned one labelled
paper at rank 2, while G001 and G014 returned none. An earlier run produced 0.0
for every retrieval metric. Labels were not changed after seeing either output.
This is documented in `BENCHMARK_RESULTS.md` with the retrieved and labelled URLs
for G001.

### 3.3 Security (deterministic)

Artifact: `benchmarks/results/security_eval_latest.json`

| Metric | Value |
|---|---:|
| Cases | 40 |
| Categories | 8 |
| Passed | 40 |
| Failed | 0 |
| Not measured | 0 |
| Pass rate (measured only) | 1.0 |

One deterministic defect was found and fixed during the first run: `SEC-I02`
(exfiltration with intervening words) was not detected. The pattern was
broadened and the suite now passes. This is recorded rather than hidden.

### 3.4 Security (live Ollama probes)

Artifact: `benchmarks/results/security_eval_20261118_085139.json`

| Metric | Value |
|---|---:|
| Probes requested | 6 |
| Produced a markdown report | 0 |
| Measured (pass/fail) | 3 |
| Passed | 0 |
| Failed | 3 |
| Inconclusive | 3 |
| Pass rate (measured only) | 0.0 |

All six probes emitted meta-reasoning instead of a report. Three reproduced a
verbatim system-prompt sequence, which the leak heuristic flags. The other three
were inconclusive because no report existed to assess. This is a real
instruction-following and disclosure finding; it does not show that the
deterministic defences failed, and no secret or poisoned citation was observed.
See `docs/SECURITY_EVALUATION.md` for interpretation.

### 3.5 Reliability

Artifact: `benchmarks/results/reliability_eval_latest.json`

| Scenario | Result |
|---|---|
| Ollama unavailable | PASS |
| Model timeout | PASS |
| Search service unavailable | PASS |
| Invalid model output | PASS |
| Malformed JSON repair | PASS |
| Database connection failure / rollback | PASS |
| Empty search results | PASS |
| Missing citations | PASS |
| Partial agent execution | PASS |
| Interrupted report generation | PASS |
| **Total** | **10/10 PASS** |

All scenarios use deterministic mocks and temporary SQLite. Production
PostgreSQL failover is not simulated.

### 3.6 Performance (mocked)

Artifact: `benchmarks/results/performance_benchmark_mocked_20261118_085229.json`

| Metric | Value |
|---|---:|
| Runs / failures | 24 / 0 |
| Success rate | 1.0 |
| Search retries | 0 |
| E2E P50 | 0.0407 s |
| E2E P95 | 0.0422 s |
| E2E mean | 0.0408 s |
| Planner P50 / P95 | 0.0000 / 0.0000 s |
| Research P50 / P95 | 0.0000 / 0.0000 s |
| Editor P50 / P95 | 0.0000 / 0.0009 s |
| Search network P50 / P95 | 0.1500 / 0.3000 s |
| SQLite round-trip P50 / P95 | 0.0070 / 0.0080 s |
| SQLite writes | 24 / 24 |
| Peak memory max / mean | 346.7 / 339.4 KB |
| Token usage | NOT MEASURED (mocked stubs) |

Concurrency smoke test: 6 runs at concurrency 3, 6/6 successful, P50 0.0338 s,
P95 0.0564 s. This validates the capability, not production scalability.

### 3.7 Tests

| Suite group | Checks | Result |
|---|---:|---|
| New framework suite (`tests/test_evaluation_framework.py`) | 55 | PASS |
| Existing evaluation/analytics/persistence suites | 182 | PASS |
| Phase 2 instrumentation checks | 7 | PASS |
| **Total** | **244** | **0 failed** |

The new suite covers dataset validation, retrieval metrics, answer quality,
prompt safety, security, reliability, performance helpers, judge degradation,
token accounting, and dashboard artifact loading. Existing suites were not
weakened, skipped, or deleted.

---

## 4. Requirement-to-evidence matrix

| Claim | Evidence |
|---|---|
| Dataset has 118 cases and 8 categories | `golden_eval_v1.manifest.json`; validation counts |
| No synthetic case has a reference answer | Validator + `test_synthetic_cases_carry_no_reference` |
| Missing labels are not scored as zero | `evaluation/retrieval.py`; fixture check; tests |
| Answer components are measured separately | Live artifact per-case components |
| Security suite has 40 labelled cases | `evaluation/security.py`; test inventory |
| Security results include all required fields | `test_every_result_has_required_fields` |
| Reliability has 10 scenarios | `evaluation/reliability.py`; 10/10 artifact |
| Performance reports P50/P95 and stages | `performance_benchmark_latest.json` |
| Mocked and live are separated | `mode` field in every artifact; docs |
| Dashboard is additive | `ui/evaluation_view.py` artifact section |
| Existing tests still pass | 244 checks, 0 failed |

---

## 5. NOT MEASURED

The following are explicitly **not measured**. They are not zero and must not be
reported as improvements:

| Item | Status |
|---|---|
| Full 118-case live golden evaluation | NOT MEASURED |
| Full 40-question v3 benchmark end-to-end | NOT MEASURED |
| Semantic retrieval relevance | NOT MEASURED (exact-URL labels) |
| Human answer-quality review | NOT MEASURED |
| LLM-judge scores | NOT MEASURED (implemented, not run) |
| Dedicated live performance repetitions | NOT MEASURED |
| PostgreSQL latency under live load | NOT MEASURED |
| Production multi-user load | NOT MEASURED |
| Token counts for all Ollama calls | PARTIALLY MEASURED (1 of 6) |
| Controlled baseline-vs-optimized A/B | NOT MEASURED |
| Causal effect of any change | NOT CLAIMED |

---

## 6. Interpretation and limits

- The live answer-quality overall of **0.7402** is a deterministic lexical score
  over three cases. It is not a general quality score for the model.
- The strict retrieval results are low but non-zero in the latest run: G005
  matched one verified URL at rank 2; G001 and G014 matched none. This does not
  prove the retrieved pages were irrelevant, and it does not justify changing
  the labels after the fact.
- Deterministic security 40/40 is evidence about 40 probes on this code version.
  It is not a security certification.
- Mocked performance numbers measure orchestration and SQLite. They are not
  Ollama, DuckDuckGo, or PostgreSQL latency.
- P95 from 3 live cases and 24 mocked runs is unstable. Sample counts are
  reported with every value.
- No performance improvement is claimed. The comparison tool reports observed
  deltas and warns against causal interpretation without a controlled A/B.

---

## 7. Reproduce

```powershell
py -3.13 benchmarks/build_golden_dataset.py
py -3.13 benchmarks/run_golden_evaluation.py --mode mocked
py -3.13 benchmarks/run_golden_evaluation.py --mode live --confirm-live --ids G001,G005,G014
py -3.13 benchmarks/run_security_evaluation.py
py -3.13 benchmarks/run_security_evaluation.py --live --confirm-live --live-limit 6
py -3.13 benchmarks/run_reliability_evaluation.py
py -3.13 benchmarks/run_performance_benchmark.py --repetitions 8 --warmup 2 --concurrency 1
py -3.13 tests/test_evaluation_framework.py
```

All changes remain uncommitted. No PostgreSQL rows were added, modified, or
deleted by the evaluation framework.
