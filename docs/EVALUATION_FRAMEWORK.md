# Evaluation and Benchmarking Framework

This document describes the evaluation layer added to the AI Research Agent. It
is additive: the LangGraph pipeline, database schema, and existing evaluation
engine are unchanged. The framework measures the system; it does not tune it.

**Status:** implemented and exercised on this workstation on 2026-11-18.
Measured values and artifact paths are recorded in
[`BENCHMARK_RESULTS.md`](BENCHMARK_RESULTS.md),
[`SECURITY_EVALUATION.md`](SECURITY_EVALUATION.md), and
[`FINAL_METRICS_REPORT.md`](FINAL_METRICS_REPORT.md).

---

## 1. Design rules

1. **Measure, do not invent.** Every number in the reports comes from a
   timestamped JSON artifact produced by a script in this repository.
2. **Keep mocked and live results separate.** Mocked runs exercise
   orchestration, metric plumbing, and persistence. They never stand in for
   model or search quality.
3. **Missing labels are a state, not zero.** A case without retrieval labels is
   excluded from aggregate means and reported as `not_measurable`.
4. **No post-hoc labels.** Reference evidence and retrieval labels come from the
   already verified `benchmark_v3.json`. Cases that describe expected behaviour
   without a verified answer are marked `synthetic` and carry no reference
   evidence or retrieval label.
5. **No causal claims without a controlled A/B.** The comparison helper reports
   observed deltas and explicitly warns that they are observational.
6. **Optional components degrade gracefully.** The LLM judge and live security
   probes return an explicit unavailable/inconclusive status instead of a score.

---

## 2. Components

| Area | Module | Purpose |
|---|---|---|
| Golden dataset | `evaluation/golden_dataset.py` | Load, hash, validate, and adapt the JSONL dataset |
| Dataset builder | `benchmarks/build_golden_dataset.py` | Build the dataset from verified v3 claims/sources |
| Retrieval metrics | `evaluation/retrieval.py` | Recall@K, Precision@K, MRR, NDCG@K, label handling |
| Answer quality | `evaluation/answer_quality.py` | Contextual precision/recall, faithfulness, relevance, citation support, unsupported claims, evidence coverage |
| Prompt safety | `tools/prompt_safety.py` | Injection detection, evidence delimiting, secret scanning/redaction, prompt-leak checks |
| Security suite | `evaluation/security.py` | 40 labelled cases across eight attack categories |
| Reliability suite | `evaluation/reliability.py` | Ten graceful-failure scenarios |
| Performance helpers | `evaluation/performance.py` | Percentiles, latency summaries, success rate, machine metadata, A/B comparison |
| Optional judge | `evaluation/judge.py` | LLM-as-a-judge with model/prompt version and unavailability handling |
| Token accounting | `generation/usage.py` | Process-wide provider token counters |
| Dashboard loader | `ui/evaluation_artifacts.py` | Read timestamped benchmark artifacts |
| Runners | `benchmarks/run_*.py` | CLI entry points for each suite |

The existing deterministic metrics in `evaluation/metrics.py`,
`tools/grounding.py`, and `tools/report_parsing.py` are reused rather than
duplicated.

---

## 3. Golden dataset

Dataset: `benchmarks/datasets/golden_eval_v1.jsonl`

| Property | Value |
|---|---|
| Cases | 118 |
| SHA-256 | `edacf9ec8225061ce4d47403301e73c4c06aad1fafb142c67243019cc8b8637e` |
| Manifest | `benchmarks/datasets/golden_eval_v1.manifest.json` |
| Verified-reference cases | 78 |
| Synthetic behavioural cases | 40 |
| Retrieval labels available | 78 |
| Retrieval labels unavailable | 40 |
| Source dataset | `benchmark_v3.json` (`10a5ba16…f2527`) |

### Category coverage

| Category | Cases |
|---|---:|
| `comparison` | 21 |
| `multi_source_research` | 19 |
| `technical_research` | 20 |
| `factual_research` | 18 |
| `recent_information` | 10 |
| `insufficient_evidence` | 10 |
| `conflicting_sources` | 10 |
| `misleading_assumption` | 10 |

Difficulty: 40 Easy, 54 Intermediate, 24 Difficult.

### Provenance rules

- **Verified reference cases** contain only findings whose v3 status is
  `verified`. Their `reference_evidence` records are copied from the v3 source
  registry. Every finding cites one or more evidence IDs that exist in the same
  case. Their retrieval labels list only repository-verified URLs.
- **Synthetic cases** cover insufficient-evidence, conflicting-source, recency,
  and misleading-premise behaviour. They have no reference answer, no reference
  evidence, no retrieval label, and an explicit `expected_behavior` string.
- The validator rejects a synthetic case that carries reference findings or
  evidence, a verified case with a non-verified finding, unknown source IDs,
  duplicate IDs/questions, missing categories, or fewer than 100 cases.

### JSONL shape (abridged)

```json
{
  "id": "G001",
  "question": "…",
  "category": "factual_research",
  "difficulty": "Easy",
  "provenance": {"type": "verified_reference", "synthetic": false},
  "expected_key_findings": [
    {"claim_id": "C001", "text": "…", "verification_status": "verified", "source_ids": ["SRC-001"]}
  ],
  "reference_evidence": [
    {"source_id": "SRC-001", "url": "https://…", "title": "…"}
  ],
  "expected_source_domains": ["arxiv.org"],
  "retrieval_ground_truth": {"status": "available", "relevant_urls": ["https://…"]},
  "evaluation_rubric": {"minimum_word_count": 250},
  "expected_behavior": "…"
}
```

---

## 4. Retrieval metrics

Implemented in `evaluation/retrieval.py`.

| Metric | Definition | Notes |
|---|---|---|
| Recall@K | relevant URLs retrieved in the top K / all labelled relevant URLs | `None` when no labels exist |
| Precision@K | relevant URLs in the top K / K | The denominator is K, so returning fewer results is penalized |
| MRR | `1 / rank` of the first relevant URL | `0.0` when labels exist but nothing matched |
| NDCG@K | graded or binary DCG / ideal DCG | Binary relevance by default; optional graded map |

- URLs are canonicalized before comparison (`tools/research_evidence.canonical_url`).
- The ranking is the order evidence was retained in `source_registry`; the
  optional lexical `relevance` score is reported but does not reorder it.
- Unlabelled cases produce `value = null`, `status = "not_measurable"`, and
  `reason = "no_relevant_labels"`.
- Aggregates report `measured_cases`, `excluded_cases`, and
  `excluded_case_ids`. Excluded cases are not counted as zero.

**Known limitation:** relevance is exact-URL matching. A semantically relevant
page at a different URL is scored irrelevant. This is strict and reproducible,
but it understates retrieval quality when search returns an equivalent page at a
different address.

---

## 5. Answer-quality metrics

Implemented in `evaluation/answer_quality.py`, composed from the existing
deterministic metrics. Every component is a lexical/structural proxy, not
entailment or human judgement.

| Component | Weight | What it measures | What it does not measure |
|---|---:|---|---|
| Faithfulness | 0.30 | Share of verifiable report claims whose cited source excerpt covers the claim | Truth or entailment |
| Answer relevance | 0.20 | Lexical coverage of the question and target aspects | Semantic answering |
| Contextual precision | 0.15 | Share of cited sources whose excerpt is relevant to the cited claim | Source authority |
| Contextual recall | 0.15 | Share of reference findings covered by the report | Paraphrase or entailment |
| Citation support | 0.15 | Share of cited findings whose cited excerpt supports the claim | Factual correctness |
| Evidence coverage | 0.05 | Existing retrieval-usability + citation-grounding metric | Whether evidence is sufficient |
| Unsupported claims | diagnostic | Share of checkable findings without content-supported citation | Included in overall? No; it would double-count citation support |

The overall score is a weighted mean over **measured** components only, with the
weights re-normalised. Unmeasured components are excluded, never imputed as
zero.

`evaluate_expected_behavior()` applies a small, explicit cue list for synthetic
cases (for example an insufficiency disclosure or a conflict acknowledgement). It
is a behaviour heuristic, not factual correctness.

---

## 6. Optional LLM judge

`evaluation/judge.py` is disabled by default. It records:

- judge model name and `judge-1.0` prompt version;
- `is_ground_truth: false`;
- `status: measured | not_available | invalid_response`;
- per-dimension scores clamped to `[0, 1]`.

If Ollama is unreachable or the response is not a JSON object, the judge returns
`not_available` or `invalid_response` with a reason and no scores. It is never
merged into the deterministic metrics and is not presented as a reference answer.

---

## 7. Security suite

`evaluation/security.py` contains 40 labelled cases across eight categories:

1. direct prompt injection
2. indirect prompt injection
3. malicious web content
4. system-instruction override
5. hidden-prompt reveal
6. secret exposure
7. untrusted citation
8. sensitive-information leakage

Each case records an ID, category, severity, attack input, expected safe
behaviour, probe type, payload, actual outcome, pass/fail, and failure
explanation. Deterministic probes inspect the actual code paths (injection
scanner, evidence delimiters, citation validator, URL scheme filter, secret
redactor, system-prompt guard, HTML script stripping, private-URL rejection,
marker forgery). Live Ollama probes are opt-in and reported separately.

A finite passing suite is evidence about those probes only. It is not a claim
that the system is secure. The live probe run on 2026-11-18 produced 0 reports,
3 prompt-leak flags, and 3 inconclusive probes; it is reported separately.
Details are in [`SECURITY_EVALUATION.md`](SECURITY_EVALUATION.md).

---

## 8. Reliability suite

`evaluation/reliability.py` runs ten scenarios against deterministic mocks and a
temporary SQLite database:

1. Ollama unavailable
2. Model timeout
3. Search service unavailable
4. Invalid model output
5. Malformed JSON repair
6. Database connection failure / rollback
7. Empty search results
8. Missing citations
9. Partial agent execution
10. Interrupted report generation

Each result records expected behaviour, actual outcome, pass/fail, and a failure
explanation. No live service or production database is used.

---

## 9. Performance harness

`benchmarks/run_performance_benchmark.py` measures:

- end-to-end latency and per-stage latency (planner, research, editor, search);
- database round-trip latency;
- P50/P95, mean, min, max, standard deviation, and sample count;
- successful completions, failures, and search retries;
- peak memory via `tracemalloc` (mocked mode);
- token usage when the provider exposes it;
- machine, Python, model, and search-provider metadata;
- configurable questions through repeatable `--question` flags.

Supported options:

```powershell
py -3.13 benchmarks/run_performance_benchmark.py `
  --mode mocked --repetitions 8 --warmup 2 --concurrency 1

py -3.13 benchmarks/run_performance_benchmark.py `
  --mode live --confirm-live --repetitions 2 --warmup 1

# Configurable questions (repeat --question; mocked or live)
py -3.13 benchmarks/run_performance_benchmark.py `
  --repetitions 2 --question "What limits silicon solar efficiency?"

# Compare two artifacts without asserting causality
py -3.13 benchmarks/run_performance_benchmark.py `
  --baseline benchmarks/results/<baseline>.json `
  --candidate benchmarks/results/<candidate>.json
```

Mocked mode replaces planner, researcher, and editor with deterministic stubs
and uses a temporary SQLite database. Live mode uses Ollama, DuckDuckGo, and the
configured database. The two modes are never combined into one score.

Token accounting is populated only from Ollama's `prompt_eval_count` /
`eval_count`. If the provider does not return them, usage remains `NOT
MEASURED`; it is never estimated from text length.

---

## 10. Dashboard

`ui/evaluation_view.py` now includes an **Offline benchmark artifacts** section.
It reads the latest JSON files from `benchmarks/results/`:

- `golden_eval_latest.json`
- `security_eval_latest.json`
- `reliability_eval_latest.json`
- `performance_benchmark_latest.json`

Missing or malformed files are labelled; the UI does not substitute values. The
section is additive and isolated by `_safe_section()`, so a rendering failure
cannot hide the existing evaluation view.

---

## 11. Reproducing the results

```powershell
# Dataset only
py -3.13 benchmarks/build_golden_dataset.py
py -3.13 benchmarks/run_golden_evaluation.py --mode mocked

# Live golden evaluation (bounded)
py -3.13 benchmarks/run_golden_evaluation.py `
  --mode live --confirm-live --ids G001,G005,G014

# Security
py -3.13 benchmarks/run_security_evaluation.py
py -3.13 benchmarks/run_security_evaluation.py --live --confirm-live --live-limit 6

# Reliability
py -3.13 benchmarks/run_reliability_evaluation.py

# Performance
py -3.13 benchmarks/run_performance_benchmark.py --repetitions 8 --warmup 2
py -3.13 benchmarks/run_performance_benchmark.py --mode live --confirm-live --repetitions 2 --warmup 1

# Tests
py -3.13 tests/test_evaluation_framework.py
```

Live modes refuse to run without `--confirm-live`. Live golden and live
performance modes refuse to continue if Ollama is unreachable.

---

## 12. Limitations

- The full 118-case dataset has not been run end-to-end against live services.
- Retrieval labels are exact URLs from the verified v3 dataset. Live search can
  return different URLs for the same topic, producing a strict zero.
- Answer-quality scores are deterministic lexical proxies. They cannot detect
  negation, contradiction, or paraphrase.
- P95 from small samples is unstable; the sample count is reported with every
  metric.
- Mocked performance measures orchestration and SQLite persistence, not
  PostgreSQL or model latency.
- Security probes cover selected code paths, not infrastructure, authentication,
  dependencies, or every possible prompt.
- No causal improvement is claimed. The repository has comparison capability,
  but no controlled baseline-versus-optimized A/B was run.
