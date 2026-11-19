# Final Metrics Report

Date: 2026-11-18; retrieval-optimization and Phase 3 reruns added 2026-11-19
Scope: evaluation and benchmarking framework for the AI Research Agent
Machine: Windows 11 (`Windows-11-10.0.26300-SP0`), 8 logical CPUs, Python 3.13.13
Model: `qwen3:4b` via Ollama at `http://localhost:11434`
Search: DuckDuckGo. Baseline `max_results=3`, 12-page budget; post-optimization
rerun `max_results=10`, 24-page budget.
Database baseline: `research_sessions=19`, `research_reports=19`,
`research_sources=167` (checked before and after the suite; unchanged)

This report consolidates measured results. Detailed methodology and evidence are
in:

- [`docs/EVALUATION_FRAMEWORK.md`](EVALUATION_FRAMEWORK.md)
- [`docs/BENCHMARK_RESULTS.md`](BENCHMARK_RESULTS.md)
- [`docs/SECURITY_EVALUATION.md`](SECURITY_EVALUATION.md)
- [`docs/RETRIEVAL_OPTIMIZATION_REPORT.md`](RETRIEVAL_OPTIMIZATION_REPORT.md)
- [`docs/PHASE_3_FINAL_OPTIMIZATION_REPORT.md`](PHASE_3_FINAL_OPTIMIZATION_REPORT.md)

Every measured value has a timestamped JSON artifact under
`benchmarks/results/`; the tables below are transcriptions of those artifacts.

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
| Tests for new components | 88 new checks (55 framework + 33 retrieval-optimisation) plus 33 Phase 3 editor/citation checks | Done |
| Retrieval optimization | Six-intent planner, 10-result depth, deterministic ranking, scholarly canonicalisation, fetch hardening, candidate-pool metrics | Done (observational live rerun) |
| Editor evidence budget | Deterministic scoring, near-duplicate suppression, diverse selection, source/character limits, before/after telemetry | Done |
| Citation integrity | Invalid-ID removal, evidence-text support, evidence-based binding, displayed/cited consistency, snippet/fetched shares | Done |
| Honest missing/conflicting evidence | Unsupported claims classified, not force-cited; measured limitations note; no invented URLs | Done |
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

Artifact: `benchmarks/results/golden_eval_mocked_20261119_105919.json`

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

### 3.2b Live rerun after retrieval optimization (2026-11-19)

Artifact: `benchmarks/results/golden_eval_live_20261119_120333.json`

Same dataset, labels, model, and host as 3.2; six queries, 10 search results per
query, 5 retained items per query, 4 fetches per query, 24-fetch cap, ranking
enabled.

| Metric | Baseline (3.2) | Rerun | Measured cases |
|---|---:|---:|---:|
| Registry Recall@5 / @10 | 0.1667 / 0.1667 | 0.2500 / 0.5833 | 3 |
| Registry NDCG@10 | 0.1290 | 0.3599 | 3 |
| Registry MRR | 0.1667 | 0.4417 | 3 |
| Candidate recall@10 (search order) | not captured | 0.3333 | 3 |
| Candidate recall@10 (ranked order) | not captured | 0.5833 | 3 |
| Candidate recall@10 (retained evidence) | not captured | 0.4167 | 3 |
| Fetch attempts / success rate | not captured | 70 / 82.86% | 3 |
| Duplicate sources removed | not captured | 43 | 3 |
| Faithfulness | 0.6667 | 0.3333 | 3 |
| Answer relevance | 0.6634 | 0.7284 | 3 |
| Contextual precision | 1.0000 | 0.6111 | 3 |
| Contextual recall | 0.7778 | 0.3333 | 3 |
| Citation support | 0.6667 | 0.2222 | 3 |
| Evidence coverage | 0.8175 | 0.3444 | 3 |
| Unsupported claims (lower is better) | 0.0000 | 0.3333 | 3 |
| **Answer-quality overall** | **0.7402** | **0.4379** | 3 |
| End-to-end mean / P50 / P95 | 465.05 / 484.36 / 497.54 s | 1214.11 / 1220.74 / 1385.77 s | 3 |

Candidate-pool metrics have no baseline because the historical artifact stored
only the retained registry. The retrieval gain and the answer-quality/latency
regression are both single-run observational results; no causal or general
improvement is claimed. The lexical answer metrics are depth-sensitive and
punish terse academic wording, and the report is still capped at three findings,
so one unsupported claim moves faithfulness by 0.333. Full detail is in
`BENCHMARK_RESULTS.md` and `RETRIEVAL_OPTIMIZATION_REPORT.md`.

### 3.2c Phase 3 Editor/citation optimization (two repeats, 2026-11-19)

Artifacts:

- run 1: `benchmarks/results/golden_eval_live_20261119_142306.json`
- run 2: `benchmarks/results/golden_eval_live_20261119_160446.json`

Same dataset, labels, model, and host as 3.2. Phase 3 adds deterministic Editor
evidence selection (12 sources / 14,000 characters / 700-character excerpts),
1,000 JSON and 700 markdown output ceilings, evidence-based citation binding, a
measured limitations note, and an 1,800 s per-request guard.

| Metric | Baseline (3.2) | Retrieval rerun (3.2b) | Phase 3 run 1 | Phase 3 run 2 | Cases |
|---|---:|---:|---:|---:|---:|
| Registry Recall@5 | 0.1667 | 0.2500 | 0.1667 | 0.1667 | 3 |
| Registry Recall@10 | 0.1667 | 0.5833 | 0.5000 | 0.5000 | 3 |
| Registry NDCG@10 | 0.1290 | 0.3599 | 0.3155 | 0.1986 | 3 |
| Registry MRR | 0.1667 | 0.4417 | 0.3810 | 0.1444 | 3 |
| Faithfulness | 0.6667 | 0.3333 | 0.5778 | 0.5000 | 3 |
| Answer relevance | 0.6634 | 0.7284 | 0.7586 | 0.6984 | 3 |
| Contextual precision | 1.0000 | 0.6111 | 0.9333 | 0.8056 | 3 |
| Contextual recall | 0.7778 | 0.3333 | 0.4444 | 0.4444 | 3 |
| Citation support | 0.6667 | 0.2222 | 0.5778 | 0.5000 | 3 |
| Evidence coverage | 0.8175 | 0.3444 | 0.3340 | 0.3439 | 3 |
| Unsupported claims (lower is better) | 0.0000 | 0.3333 | 0.0667 | 0.1944 | 3 |
| **Answer-quality overall** | **0.7402** | **0.4379** | **0.6351** | **0.5694** | 3 |
| E2E mean | 465.05 s | 1214.11 s | 1363.57 s | 1995.97 s | 3 |
| E2E median | 484.36 s | 1220.74 s | 502.88 s | 567.08 s | 3 |
| E2E P95 | 497.54 s | 1385.77 s | 2845.98 s | 4461.57 s | 3 |

Editor context and citation integrity (mean over three cases):

| Diagnostic | Run 1 | Run 2 |
|---|---:|---:|
| Sources before → after | 28.67 → 12.0 | 26.67 → 12.0 |
| Characters before → after | 24,697 → 10,253 | 22,539 → 9,667 |
| Approx. tokens before → after (`characters/4`) | 6,174 → 2,563 | 5,634 → 2,416 |
| Citation precision / coverage | 1.0 / 1.0 | 1.0 / 1.0 |
| Evidence support rate | 0.5778 | 0.5000 |
| Unsupported claim rate | 0.0667 | 0.1944 |
| Displayed = cited consistency | 1.0 | 1.0 |
| Snippet-only cited share | 0.0 | 0.0 |
| Evidence-based bindings / invalid references | 0 / 0 | 0 / 0 |

All findings carried a valid source ID; no phantom URL, duplicate URL variant,
or invalid reference was counted. No binding was needed, which means the model's
own citations already covered its claims at the `0.55` threshold. The
limitations note was added only for measurably incomplete support.

Latency is reported with its full instability. The Editor stage took
`403 / 3014 / 418 s` in run 1 and `287 / 437 / 463 s` in run 2, versus
`919 / 1316 / 1132 s` in the retrieval rerun. The median across the six Phase 3
Editor measurements is `427.2 s`, versus `1132 s` before. However, run 1
contained a `3014 s` Editor outlier and run 2 contained a `4596 s` **research**
stall on G001 (a network/fetch delay, not generation). With three cases and a
heavy tail, the mean and P95 are not stable; the median and per-stage breakdown
are the defensible summaries. No causal speed-up is claimed.

Token usage remains partially measured: one of six calls exposed provider counts
(`3,320` prompt + `1,000` completion in run 2) because streamed JSON early-stop
closes before Ollama's final `done` chunk. The context token figures above are
explicitly `characters/4` estimates.

The Phase 3 proxy recovered most of the retrieval-rerun regression
(`0.4379` → `0.6351` / `0.5694`) but remains below the historical `0.7402`.
Contextual recall and evidence coverage are still the main gaps and are
depth-sensitive ratios, not like-for-like quality measures. Both Phase 3 runs
and the retrieval rerun are single-run observational comparisons.

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
| Unittest discovery (`unittest discover -s tests`) | 232 | PASS |
| — of which retrieval-optimisation suite | 33 | PASS |
| — of which Phase 3 editor/citation suite | 33 | PASS |
| — of which evaluation framework suite | 55 | PASS |
| Standalone dataset verification | 44 | PASS |
| Standalone analytics / persistence / performance | 9 / 7 / 6 | PASS |
| Phase 2 instrumentation checks | 7 | PASS |
| **Total** | **305** | **0 failed** |

The unittest group covers the existing suites plus the retrieval-optimisation
suite (canonicalisation, ranking, planner validation and label-leak guards,
depth, cross-query deduplication, fetch budgets, candidate metrics, 429 retry)
and the Phase 3 editor/citation suite (evidence budgeting, near-duplicate
suppression, stable IDs, invalid-citation removal, evidence-text support,
evidence-based binding, URL-variant collapse, displayed/cited consistency,
limitations note, and black-box editor integration). The new framework suite
covers dataset validation, retrieval metrics, answer quality, prompt safety,
security, reliability, performance helpers, judge degradation, token
accounting, and dashboard artifact loading. Existing suites were not weakened,
skipped, or deleted; the one obsolete hardcoded token-limit assertion was
updated to check the new configurable cap with its approved default.

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
| Editor evidence is budgeted deterministically | `tools/evidence_selection.py`; `test_phase3_editor_*`; before/after telemetry in both live artifacts |
| Cited sources are content-checked, not just URL-checked | `tools/citation_integrity.py`; `test_phase3_citation_integrity`; precision/coverage/consistency `1.0` in both runs |
| Unsupported claims are not force-cited | Binding disabled/invalid-ID tests; zero bindings and zero invalid references in both runs |
| Missing/conflicting evidence is disclosed | Measured `## Limitations` note; tests for truthful counts and no duplication |
| Existing tests still pass | 305 checks, 0 failed |

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
| Token counts for all Ollama calls | PARTIALLY MEASURED (1 of 6; streaming early-stop) |
| Controlled baseline-vs-optimized A/B | NOT MEASURED |
| Causal effect of any change | NOT CLAIMED |
| Stable latency distribution at n=3 | NOT MEASURED (one outlier per run) |

---

## 6. Interpretation and limits

- The live answer-quality overall was **0.7402** before retrieval optimization,
  **0.4379** in the post-change rerun, and **0.6351 / 0.5694** in the two Phase 3
  runs. All are deterministic lexical scores over three cases, not general
  quality scores for the model. The retrieval gain and the answer-quality
  changes are reported together; no overall improvement over the historical
  baseline is claimed.
- The strict registry retrieval results improved from Recall@5 `0.1667` /
  Recall@10 `0.1667` / MRR `0.1667` to `0.2500` / `0.5833` / `0.4417` in the
  retrieval rerun, and remained at Recall@10 `0.5000` in both Phase 3 runs.
  This does not prove the retrieved pages were relevant, and it does not
  justify changing the labels after the fact.
- Candidate-pool recall shows where labels were available: in the retrieval
  rerun, search order Recall@10 `0.3333`, ranked order `0.5833`, retained
  evidence `0.4167`. The baseline artifact cannot produce these diagnostics.
  Search-order recall is low because that pool concatenates queries in provider
  order; the ranked pool is the meaningful stage diagnostic.
- The retrieval rerun was slower (mean `1214.11 s` versus `465.05 s`), dominated
  by Editor generation over a larger evidence prompt. Phase 3 reduced the
  evidence prompt by ~55–59% and the median end-to-end time fell to
  `502.88 s` / `567.08 s`, but each run had one large outlier in a different
  stage (Editor `3014 s`; research `4596 s`). These are observational
  trade-offs, not a controlled speed-up.
- Citation precision, citation coverage, and displayed/cited consistency were
  `1.0` in both Phase 3 runs, with zero invalid IDs, zero phantom URLs, and zero
  snippet-only cited sources. No evidence-based binding was needed.
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

These results were recorded before the portfolio-release commit. No PostgreSQL
rows were added, modified, or deleted by the evaluation framework.
