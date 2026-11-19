# Benchmark Results

All values below were produced on this workstation on 2026-11-18, with the
retrieval and Phase 3 reruns added on 2026-11-19. Each row names the artifact
that contains the raw data. Mocked and live results are kept separate. Nothing
in this document is a simulated or hand-entered score.

- Machine: Windows 11 (`Windows-11-10.0.26300-SP0`), 8 logical CPUs
- Python: 3.13.13
- Model: `qwen3:4b` through Ollama at `http://localhost:11434`
- Search: DuckDuckGo; historical baseline used `max_results=3` and a 12-page
  budget, while the retrieval/Phase 3 reruns used `max_results=10` and a
  24-page budget
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

Two live artifacts are reported. The first is the historical baseline produced
before the retrieval-optimisation work. The second is the post-change rerun over
the same three cases with unchanged labels. Both are kept; the baseline artifact
was never modified.

### 2.1 Baseline (historical, pre-optimization)

Artifact: `benchmarks/results/golden_eval_live_20261118_091654.json`

Three verified-reference cases were selected deliberately:

| Case | Category | Question theme | Status | E2E seconds |
|---|---|---|---|---:|
| G001 | factual_research | RAG architecture | complete | 499.00 |
| G005 | comparison | Speculative decoding vs Medusa | complete | 484.36 |
| G014 | technical_research | Direct Preference Optimization | complete | 411.79 |

#### Retrieval (exact-URL labels, registry)

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

#### Baseline answer quality

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

#### Baseline latency and tokens

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

### 2.2 Post-optimization rerun (2026-11-19)

Artifact: `benchmarks/results/golden_eval_live_20261119_120333.json`

Same dataset (SHA-256 `edacf9ec…b8637e`), same labels, same model (`qwen3:4b`),
same host, and the same three cases. Configured depth was 6 queries × 10 search
results, 5 retained evidence items per query, 4 fetches per query, and a 24-fetch
run cap, with source ranking enabled.

| Case | Status | Sources | E2E seconds | Planner / research / editor seconds |
|---|---|---:|---:|---|
| G001 | complete | 30 | 1017.48 | 39.71 / 58.34 / 919.36 |
| G005 | complete | 30 | 1404.10 | 26.29 / 61.94 / 1315.85 |
| G014 | complete | 27 | 1220.74 | 29.64 / 58.70 / 1132.38 |

#### Registry retrieval (historical metric, unchanged definition)

| Metric | Baseline | Rerun | Delta |
|---|---:|---:|---:|
| Recall@1 | 0.0000 | 0.1667 | +0.1667 |
| Recall@3 | 0.1667 | 0.1667 | 0.0000 |
| Recall@5 | 0.1667 | 0.2500 | +0.0833 |
| Recall@10 | 0.1667 | 0.5833 | +0.4166 |
| Precision@5 | 0.0667 | 0.1333 | +0.0666 |
| Precision@10 | 0.0333 | 0.1000 | +0.0667 |
| NDCG@5 | 0.1290 | 0.2547 | +0.1257 |
| NDCG@10 | 0.1290 | 0.3599 | +0.2309 |
| MRR | 0.1667 | 0.4417 | +0.2750 |

Metric versions: registry `retrieval-registry-1.0`; candidate pools
`retrieval-candidate-pool-1.0`.

#### Candidate-pool retrieval (new diagnostic, post-change only)

| Pool | Recall@5 | Recall@10 | NDCG@5 | NDCG@10 | MRR |
|---|---:|---:|---:|---:|---:|
| Search order (`search_candidates`) | 0.0000 | 0.3333 | 0.0000 | 0.0964 | 0.0756 |
| Ranked order (`ranked_candidates`) | 0.2500 | 0.5833 | 0.2547 | 0.3550 | 0.4370 |
| Retained evidence (`retained_evidence`) | 0.3333 | 0.4167 | 0.1436 | 0.1846 | 0.1553 |

The baseline artifact predates candidate-pool capture, so no baseline
candidate-pool comparison exists. Candidate-pool recall answers a different
question from registry recall: whether the search or ranking stage saw the label
before retention. It is diagnostic and additive. The retained-evidence pool is
listed in retention/query order, not score order, so it is not directly
interchangeable with the ranked pool.

Per-case stage at which the first label appeared:

| Case | Labels | First label: search | First label: ranked | First label: retained | First label: registry |
|---|---:|---:|---:|---:|---:|
| G001 | 4 | 13 | 5 | 8 | 5 |
| G005 | 2 | 20 | 1 | 11 | 1 |
| G014 | 1 | 10 | 9 | 4 | 8 |

G001 matched `https://arxiv.org/abs/2005.11401`; the other three labels were not
in the 45 unique search candidates. G005 matched `2401.10774`; `2211.17192` was
not retrieved. G014 matched `2305.18290` at search rank 10, which the old
3-result depth could not have seen.

#### Fetch and duplicate diagnostics

| Metric | Total (3 cases) |
|---|---:|
| Registered sources | 87 |
| Search candidates | 125 |
| Ranked candidates | 124 |
| Redirects rejected | 1 |
| Duplicate search candidates removed | 11 |
| Cross-query duplicates skipped | 32 |
| Duplicate sources removed (total) | 43 |
| Fetch attempts | 70 |
| Fetch successes / failures | 58 / 12 |
| Fetch success rate | 82.86% |
| Snippet-only retained sources | 30 |

Per-case fetch attempts were 24, 24, and 22, within the 24-fetch cap. The 24-cap
was reached by G001 and G005.

#### Answer quality (deterministic lexical proxies)

| Component | Baseline | Rerun |
|---|---:|---:|
| Faithfulness | 0.6667 | 0.3333 |
| Answer relevance | 0.6634 | 0.7284 |
| Contextual precision | 1.0000 | 0.6111 |
| Contextual recall | 0.7778 | 0.3333 |
| Citation support | 0.6667 | 0.2222 |
| Evidence coverage | 0.8175 | 0.3444 |
| Unsupported claims (lower is better) | 0.0000 | 0.3333 |
| **Overall (weighted, measured only)** | **0.7402** | **0.4379** |

Per-case overall: G001 `0.3678`, G005 `0.3385`, G014 `0.6074` (baseline
`0.7105`, `0.7005`, `0.8097`).

This is a real regression in the deterministic proxy, not a rounding artifact.
It is reported rather than hidden. The editor still writes under 400 words with
three findings, but it now receives ~27–30 sources instead of 7–9. Two effects
are visible:

- **Evidence-coverage denominator:** the component divides usable evidence by
  the number of search results and grounded sources by retrieved sources. A
  deeper pool mechanically lowers the ratio even when the retained evidence is
  better. The component has a 0.05 weight, so it explains only a small part of
  the overall change.
- **Lexical support:** claims are now often grounded in terse arXiv abstracts
  rather than Wikipedia/vendor prose. The lexical proxy measures shared tokens,
  so a correct but differently worded claim can score as unsupported. The
  report's three claims per case also make the score discrete and unstable
  (one unsupported claim moves faithfulness from 0.667 to 0.333).

The retrieval improvement and the answer-quality regression are both single-run
observational results. They do not establish causality, and no overall
answer-quality improvement is claimed.

#### Latency

| Metric | Baseline | Rerun | Delta |
|---|---:|---:|---:|
| Samples | 3 | 3 | — |
| Mean | 465.05 s | 1214.11 s | +749.06 s (+161.1%) |
| P50 | 484.36 s | 1220.74 s | +736.38 s |
| P95 | 497.54 s | 1385.77 s | +888.23 s |
| Min / max | 411.79 / 499.00 s | 1017.48 / 1404.10 s | — |

The increase is dominated by Editor generation (919–1316 s), which receives a
larger evidence prompt, and by six searches instead of three. This is a
throughput trade-off, not a speed improvement.

### 2.3 Phase 3 Editor evidence/citation optimization (two repeats, 2026-11-19)

Artifacts:

- run 1: `benchmarks/results/golden_eval_live_20261119_142306.json`
- run 2: `benchmarks/results/golden_eval_live_20261119_160446.json`

Same dataset (SHA-256 `edacf9ec…b8637e`), labels, model (`qwen3:4b`), host, and
three cases as section 2.2. The Phase 3 configuration adds a deterministic
Editor evidence budget (12 sources maximum, 3 minimum, 14,000 context
characters, 700-character excerpts), output ceilings (1,000 JSON / 700 markdown
tokens), evidence-based citation binding, a measured limitations note, and an
1,800 s per-request guard.

Per-case stage timings:

| Case | Run | Sources | E2E s | Planner | Research | Editor | Generation |
|---|---|---:|---:|---:|---:|---:|---|
| G001 | 1 | 28 | 481.5 | 28.7 | 49.8 | 403.0 | json_repaired |
| G005 | 1 | 30 | 3106.3 | 25.4 | 67.0 | 3013.8 | json |
| G014 | 1 | 28 | 502.8 | 26.0 | 59.0 | 417.8 | json |
| G001 | 2 | 21 | 4894.2 | 11.4 | 4595.5 | 287.3 | json |
| G005 | 2 | 30 | 526.5 | 31.1 | 58.8 | 436.6 | json |
| G014 | 2 | 29 | 567.1 | 25.2 | 78.7 | 463.1 | json_repaired |

Context budget actually applied (mean over the three cases):

| Metric | Run 1 | Run 2 |
|---|---:|---:|
| Sources before → after | 28.67 → 12.0 | 26.67 → 12.0 |
| Characters before → after | 24,697 → 10,253 | 22,539 → 9,667 |
| Approx. tokens before → after (`characters/4`) | 6,174 → 2,563 | 5,634 → 2,416 |
| Near-duplicate sources dropped | 0.0 | 0.33 |
| Sources dropped only by the hard budget | 0.0 | 0.0 |
| Prompt characters | 10,702 | 10,116 |
| LLM calls | 1.0 | 1.0 |

The selector removed roughly 55–59% of the evidence prompt while keeping 12
sources, stable `SRC-*` IDs, and at least the configured minimum. No case needed
character-budget truncation; the 14,000-character cap was not the limiting
factor after source reduction.

Registry retrieval (historical definition, unchanged):

| Metric | Baseline | Retrieval rerun | Phase 3 run 1 | Phase 3 run 2 |
|---|---:|---:|---:|---:|
| Recall@5 | 0.1667 | 0.2500 | 0.1667 | 0.1667 |
| Recall@10 | 0.1667 | 0.5833 | 0.5000 | 0.5000 |
| NDCG@10 | 0.1290 | 0.3599 | 0.3155 | 0.1986 |
| MRR | 0.1667 | 0.4417 | 0.3810 | 0.1444 |

All Phase 3 runs remain above the historical baseline at Recall@10 and are
close to the retrieval rerun. Differences between Phase 3 runs are search-result
variation, not a metric-definition change.

Answer quality (deterministic lexical proxies):

| Component | Baseline | Retrieval rerun | Phase 3 run 1 | Phase 3 run 2 |
|---|---:|---:|---:|---:|
| Faithfulness | 0.6667 | 0.3333 | 0.5778 | 0.5000 |
| Answer relevance | 0.6634 | 0.7284 | 0.7586 | 0.6984 |
| Contextual precision | 1.0000 | 0.6111 | 0.9333 | 0.8056 |
| Contextual recall | 0.7778 | 0.3333 | 0.4444 | 0.4444 |
| Citation support | 0.6667 | 0.2222 | 0.5778 | 0.5000 |
| Evidence coverage | 0.8175 | 0.3444 | 0.3340 | 0.3439 |
| Unsupported claims (lower is better) | 0.0000 | 0.3333 | 0.0667 | 0.1944 |
| **Overall (weighted, measured only)** | **0.7402** | **0.4379** | **0.6351** | **0.5694** |

Per-case overall was `0.4748 / 0.6348 / 0.7957` in run 1 and
`0.4939 / 0.5688 / 0.6455` in run 2 (G001/G005/G014).

Citation and source integrity (mean over three cases):

| Diagnostic | Run 1 | Run 2 |
|---|---:|---:|
| Citation precision (valid references / all references) | 1.0000 | 1.0000 |
| Citation coverage (findings with a valid ID) | 1.0000 | 1.0000 |
| Evidence support rate | 0.5778 | 0.5000 |
| Partially supported claim rate | 0.3555 | 0.3056 |
| Unsupported claim rate | 0.0667 | 0.1944 |
| Displayed = cited consistency | 1.0000 | 1.0000 |
| Snippet-only cited share | 0.0000 | 0.0000 |
| Evidence-based bindings added | 0 | 0 |
| Invalid source references | 0 | 0 |
| Source-type diversity | 3.0 | 2.0 |

Every finding carried a real source ID; no invalid ID, phantom URL, or duplicate
URL variant was counted. All cited sources in these runs were fetched pages; no
snippet-only source was cited. No binding was required: the model's own IDs
already covered the claims it made at the configured threshold. The limitations
note was added only where support was measurably incomplete (G001 in run 1;
G001 and G005 in run 2).

Latency:

| Metric | Baseline | Retrieval rerun | Phase 3 run 1 | Phase 3 run 2 |
|---|---:|---:|---:|---:|
| Mean | 465.05 s | 1214.11 s | 1363.57 s | 1995.97 s |
| Median | 484.36 s | 1220.74 s | 502.88 s | 567.08 s |
| P95 | 497.54 s | 1385.77 s | 2845.98 s | 4461.57 s |
| Min / max | 411.79 / 499.00 s | 1017.48 / 1404.10 s | 481.52 / 3106.32 s | 526.54 / 4894.29 s |

Editor stage only: baseline `919 / 1316 / 1132 s`; run 1
`403 / 3014 / 418 s`; run 2 `287 / 437 / 463 s`. Across the six Phase 3 Editor
measurements the median is `427.2 s`, compared with `1132 s` in the
retrieval-only rerun. Typical Editor time fell substantially, but the mean and
P95 are unstable because each three-case run contains one infrastructure
outlier in a different stage:

- run 1, G005: Editor `3013.8 s` despite a 10,147-character prompt;
- run 2, G001: Research `4595.5 s` (network/fetch stall), while its Editor took
  only `287.3 s`.

The research outlier is unrelated to the Editor change; the Editor outlier is
not explained by prompt size. With three cases and a heavy tail, the median is
more informative than the mean. No causal speed-up is claimed, and the
retrieval-only rerun's mean latency is not used as a stable baseline.

Answer-quality interpretation:

- The Phase 3 proxy (`0.6351` and `0.5694`) recovered most of the loss seen in
  the retrieval-only rerun (`0.4379`) but remains below the historical baseline
  (`0.7402`). No overall improvement over the historical baseline is claimed.
- Citation support recovered from `0.2222` to `0.5000–0.5778` with zero
  bindings, zero invalid IDs, and perfect displayed/cited consistency.
- Contextual recall (`0.4444`) and evidence coverage (`0.334–0.344`) remain the
  main gaps versus the historical baseline. Both are depth-sensitive ratios:
  the baseline had 7–9 sources and three findings, while the current pipeline
  searches six queries and retains ~27–30 sources. They are not direct
  like-for-like quality measures.
- The two Phase 3 runs differ in faithfulness (`0.5778` vs `0.5000`) and
  unsupported claims (`0.0667` vs `0.1944`). With three claims per case, one
  changed classification moves these values by ~0.17–0.33.

Token usage remains unavailable for five of six calls because the streamed JSON
early-stop closes the stream before Ollama's final `done` chunk. The single
reported call exposed `3,320` prompt + `1,000` completion tokens in run 2. No
token count is estimated except the explicitly labelled context `characters/4`
figure.

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

Live end-to-end latency is available from the three-case golden evaluations
above (baseline P50 484.36 s, P95 497.54 s; post-change P50 1220.74 s, P95
1385.77 s), but it is not the dedicated harness with repetitions and stage
isolation. The live performance command exists and requires `--confirm-live`; no
live performance artifact was produced for this report.

---

## 5. Baseline versus optimized

**OBSERVATIONAL, NOT A CONTROLLED A/B.**

The three-case live comparison in section 2.2 uses the same dataset, labels,
model, and host, but the two runs were not alternated and each configuration was
measured once. Search results also vary between runs. The deltas are therefore
observational and are not attributed causally to individual code changes.

Observed retrieval deltas (registry metric, same definition in both runs):

| Metric | Baseline | Rerun | Delta |
|---|---:|---:|---:|
| Recall@5 | 0.1667 | 0.2500 | +0.0833 |
| Recall@10 | 0.1667 | 0.5833 | +0.4166 |
| NDCG@10 | 0.1290 | 0.3599 | +0.2309 |
| MRR | 0.1667 | 0.4417 | +0.2750 |

Observed answer-quality and latency changes were negative (overall `0.7402` →
`0.4379`; mean latency `465.05 s` → `1214.11 s`). They are reported in section
2.2 and must not be omitted when citing the retrieval gains.

Phase 3 added two further observational runs (section 2.3). The answer-quality
proxy was `0.6351` and `0.5694`, recovering most of the loss from `0.4379` but
remaining below the historical `0.7402`. Citation precision and displayed/cited
consistency were `1.0` in both runs, with no invalid IDs or bindings. The Editor
median across the six Phase 3 stage measurements was `427.2 s`, versus `1132 s`
in the retrieval-only rerun, but each three-case run contained one infrastructure
outlier in a different stage (Editor `3014 s` in run 1; research `4596 s` in run
2). The typical-case reduction is therefore observational; no causal speed-up is
claimed.

A combined Phase 3 mean over both runs is `0.6023` for answer quality
(`0.6351` / `0.5694`) and must be read with its full range. It is not a
replacement for the historical baseline and not a controlled A/B result.

The performance comparison helper is implemented and tested:

```powershell
py -3.13 benchmarks/run_performance_benchmark.py `
  --baseline benchmarks/results/<baseline>.json `
  --candidate benchmarks/results/<candidate>.json
```

It reports baseline, candidate, absolute delta, and percent change, and states
that the result is observational. A demonstration comparison between two mocked
artifacts exists at
`benchmarks/results/performance_comparison_observational.json`:

| Metric | Baseline | Candidate | Delta | Percent |
|---|---:|---:|---:|---:|
| E2E P50 | 0.091411 s | 0.040708 s | -0.050703 s | -55.47% |
| E2E P95 | 0.156216 s | 0.042184 s | -0.114032 s | -73.00% |
| Success rate | 1.0 | 1.0 | 0.0 | 0.0% |

The two mocked runs had the same inputs and machine but were not a controlled
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
| Controlled baseline-vs-optimized A/B | NOT MEASURED (single-run observational comparison only) |
| Baseline candidate-pool metrics | NOT AVAILABLE (historical artifact stored only the registry) |
| Editor-context cap / prompt compaction | IMPLEMENTED (deterministic selector + budgets); live effect reported per run, not assumed |
| Causal effect of context reduction on latency or answer quality | NOT MEASURED (single-run observational comparisons only) |

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
