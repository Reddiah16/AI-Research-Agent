# Retrieval Optimization Report

**Status:** implementation and live before/after measurement complete.
**Baseline artifact:** `benchmarks/results/golden_eval_live_20261118_091654.json`
(historical, unmodified)
**Post-change artifact:** `benchmarks/results/golden_eval_live_20261119_120333.json`
**Dataset:** `benchmarks/datasets/golden_eval_v1.jsonl`, SHA-256
`edacf9ec8225061ce4d47403301e73c4c06aad1fafb142c67243019cc8b8637e`
**Cases:** G001, G005, G014 — the same three cases as the baseline, with labels
unchanged.

---

## 1. Executive summary

The retrieval path was optimized without rebuilding the agent. Six changes were
implemented: a retrieval-oriented planner, deeper search/retention, deterministic
source ranking, scholarly URL canonicalisation, bounded 429 fetch retries, and
versioned candidate-pool metrics. At the time of this report, all 199 unittest
tests plus 73 standalone checks passed. Phase 3 later raised the reported total
to 305 checks (232 unittest + 73 standalone); see
`docs/PHASE_3_FINAL_OPTIMIZATION_REPORT.md`.

On the same three unchanged cases (G001, G005, G014), the historical registry
metric improved from:

| Metric | Baseline | Rerun | Delta |
|---|---:|---:|---:|
| Recall@5 | 0.1667 | 0.2500 | +0.0833 |
| Recall@10 | 0.1667 | 0.5833 | +0.4166 |
| NDCG@10 | 0.1290 | 0.3599 | +0.2309 |
| MRR | 0.1667 | 0.4417 | +0.2750 |

Candidate-pool recall (new; no baseline exists) was `0.3333` at search order
Recall@10, `0.5833` at ranked order, and `0.4167` at retained evidence. Ranking
moved a label from search rank 20 to rank 1 (G005) and from rank 13 to rank 5
(G001), while the deeper pool exposed G014's label at search rank 10.

The cost is material and is reported, not hidden: mean end-to-end latency rose
from `465.05 s` to `1214.11 s`, and the deterministic answer-quality proxy fell
from `0.7402` to `0.4379`. The latter is partly a depth-sensitive denominator
and a terse-academic-wording effect in a lexical proxy, but the drop is real in
the measurement. **No overall answer-quality or performance improvement is
claimed.** These are single-run observational results, not a controlled A/B.

---

## 2. Scope and constraints

The work improves the existing retrieval path without rebuilding the agent:

- LangGraph pipeline, Ollama `qwen3:4b`, DuckDuckGo, PostgreSQL, SQLAlchemy, and
  Streamlit remain in place.
- No benchmark label, dataset file, historical artifact, or PostgreSQL row was
  modified.
- Retrieval changes are modular: a planner rewrite, a new ranking module,
  canonicalisation helpers, fetch hardening, and additive metrics.
- Changes are configurable and can be disabled (`ENABLE_SOURCE_RANKING`,
  `SEARCH_MAX_RESULTS`, `MAX_TOTAL_PAGES_PER_RUN`, and related settings).
- Retrieval is still sequential. No concurrency or browser automation was added.

The goal is higher retrieval recall and better evidence ranking for the same
pipeline, not a claim of factual truth or production readiness.

---

## 3. Baseline (historical)

The baseline is the last live three-case artifact produced before this work. It
was copied to `tmp_baseline/baseline_20261118.json` and otherwise left untouched.

| Metric | Baseline (G001/G005/G014) |
|---|---:|
| Recall@1 / @3 / @5 / @10 | 0.0000 / 0.1667 / 0.1667 / 0.1667 |
| Precision@1 / @3 / @5 / @10 | 0.0000 / 0.1111 / 0.0667 / 0.0333 |
| NDCG@1 / @3 / @5 / @10 | 0.0000 / 0.1290 / 0.1290 / 0.1290 |
| MRR | 0.1667 |
| Cases completed / errors | 3 / 0 |
| Mean latency | 465.05 s |
| P50 / P95 latency | 484.36 s / 497.54 s |

Per case: G001 Recall@5 `0.0` with 7 sources; G005 `0.5` with 9 sources; G014
`0.0` with 9 sources. The effective depth was 3 queries × 3 results, 2 fetches
per query, 3 retained items per query. The old artifact stores only the retained
registry, so **baseline candidate-pool recall cannot be reconstructed** from it.
Candidate-pool numbers exist only for the post-change run.

---

## 4. Implemented changes

### 4.1 Retrieval-oriented planner (`agents/triage_agent.py`)

- Requests six short, non-redundant queries covering distinct intents: direct
  topic, primary academic, constrained academic (`site:arxiv.org`), official
  documentation, comparison or implementation, and survey/review.
- Expands common acronyms in the deterministic fallback (for example RAG, DPO,
  RLHF, HNSW) without consulting the benchmark.
- Uses only the user topic. It never receives labels, expected URLs, or
  evaluation metadata.
- Strips benchmark-style language such as "verified facts", "key facts",
  "facts about", and "fact check".
- Rejects generated queries containing URLs, arXiv links, or arXiv identifiers.
- Validates structure, de-duplicates queries, tops up to the target, and falls
  back to a deterministic diverse plan when the model output is invalid,
  truncated, or fails.
- Raises the planner token budget to 512 so a six-query JSON plan is not
  truncated.

### 4.2 Retrieval depth (`config/config.py`)

| Setting | Before | After |
|---|---:|---:|
| Search results per query | 3 | 10 |
| Retained evidence items per query | 3 | 5 |
| Pages fetched per query | 2 | 4 |
| Run-wide fetch cap | 12 | 24 |
| Planned queries | 3 | 6 |

The run-wide cap is still a hard safety ceiling. Raising it was necessary:
6 × 4 = 24 per-query budget would otherwise be silently clipped to 12.

### 4.3 Scholarly canonicalisation (`tools/research_evidence.py`)

- arXiv `abs`, `html`, `pdf`, `format`, and `ar5iv` URLs collapse to one stable
  work key, ignoring version suffixes (`v1`, `v2`, …).
- The abstract URL is preferred for citation and fetching, because the bounded
  text fetcher cannot use PDFs.
- Different arXiv IDs are never merged.
- DOI URLs normalise to a lowercased `https://doi.org/<doi>` key.
- Tracking parameters, fragments, default ports, duplicate slashes, and trailing
  slashes are normalised for other URLs.
- The provider's original URL is preserved in `original_url` for audit.
- Advertisement and click-tracking redirectors are detected by host/path and
  rejected; legitimate independent blogs are never rejected by this check.
- Cross-query duplicates are skipped before fetch budget is spent.

### 4.4 Deterministic source ranking (`tools/source_ranking.py`)

A new module scores every eligible candidate with an additive, logged score:

| Component | Weight | Signal |
|---|---:|---|
| Lexical relevance | 0.35 | Topic/query token overlap |
| Query coverage | 0.20 | Fraction of unique query tokens present |
| Entity/title match | 0.15 | Title token/bigram overlap plus acronym match |
| Authority | 0.20 | Graded source-type score |
| Position prior | 0.05 | Provider rank, capped |

Authority is graded, not binary: academic repositories (1.0), official
documentation (0.88), `.edu`/`.gov` (0.86), relevant GitHub repositories (0.80),
encyclopedias (0.55), vendor engineering (0.45), independent blogs (0.32),
aggregator blogs (0.12), and social posts (0.06). Listicles, social posts,
aggregator blogs, and empty snippets receive small explicit penalties.

There is no hard allowlist. Every candidate keeps `rank_score`,
`rank_components`, `rank_penalties`, `authority_score`, `source_type`, and
`ranking_version`. Sorting is deterministic with explicit tie-breakers, and the
registry is sorted before stable `SRC-###` IDs are assigned.

### 4.5 Fetch hardening (`tools/page_fetcher.py`)

- HTTP 429 responses receive bounded retry with exponential backoff, honouring a
  bounded `Retry-After` header.
- Retries are opt-in (`max_retries=0` by default) and capped; non-429 errors are
  not retried.
- Fetch records now include attempt count, status, content type, extracted text
  length, and a classified error.
- Failures still fall back to the search snippet. Robots policy, DNS safety, and
  byte/time caps are unchanged; no robots bypass or browser automation was
  added.

### 4.6 Measurement integrity (`evaluation/retrieval.py`, `graph/workflow.py`)

- The historical registry metric is unchanged and explicitly versioned as
  `retrieval-registry-1.0`.
- New candidate-pool metrics (`retrieval-candidate-pool-1.0`) are computed for
  three stages: provider search order (`search_candidates`), ranked order
  (`ranked_candidates`), and retained evidence (`retained_evidence`).
- The new metrics are additive and reported separately; they do not replace the
  registry numbers used in earlier reports.
- Workflow metrics now include candidate counts, redirects rejected, duplicates
  removed, cross-query duplicates skipped, snippet-only sources, fetch successes,
  and fetch failures.
- The editor receives the workflow's sorted registry so `SRC-###` IDs stay
  consistent between the report and grounding.

### 4.7 Editor context

Retaining five items across six queries increases prompt size. The JSON
generation context window was raised from 8192 to 16384 tokens to keep the
prompt plus the 1200-token JSON output inside the model window.

---

## 5. Test coverage

New suite: `tests/test_retrieval_optimization.py` (33 tests).

It covers:

- arXiv view/version collapsing, distinct-ID safety, old-style IDs, DOI
  normalisation, tracking parameters, and fragment handling;
- redirect/ad detection with legitimate-blog preservation;
- query diversity, invalid planner output, planner exceptions, fallback
  behaviour, token budget, and label-leak guards;
- deterministic ranking, authority classification, official docs, GitHub
  repositories, listicle penalties, component logging, and tie stability;
- configured depth and environment overrides;
- cross-query deduplication before fetch, separate fetch/retention budgets,
  arXiv abstract fetching, and fetch status/length recording;
- candidate-pool document ordering, versioned metrics, and unlabelled-case
  exclusion;
- bounded 429 retry success, retry exhaustion, and default-disabled behaviour.

Existing tests were preserved. Two tests that encoded the old intentional values
(`max_pages_per_query == 2`, `num_ctx == 8192`) were updated to the new intended
values; reliability editor stubs were updated to accept the new
`source_registry` keyword. No assertion was deleted or weakened.

---

## 6. Live evaluation setup

- Command:
  `py -3.13 benchmarks/run_golden_evaluation.py --mode live --confirm-live --ids G001,G005,G014 --limit 3`
- Same dataset, labels, model (`qwen3:4b`), and host as the baseline.
- Mocked and live results are stored in separate artifacts.
- The baseline artifact was not overwritten.

---

## 7. Results

Artifact: `benchmarks/results/golden_eval_live_20261119_120333.json`
Generated: `2026-11-19T12:03:33+00:00`. Cases completed: 3. Errors: 0.

### 7.1 Configuration actually used

| Setting | Value |
|---|---:|
| Search results per query | 10 |
| Queries | 6 |
| Retained evidence per query | 5 |
| Fetches per query | 4 |
| Run-wide fetch cap | 24 |
| Source ranking | enabled |
| 429 retries | 2 |
| Python / model | 3.13.13 / `qwen3:4b` |

### 7.2 Registry retrieval (historical definition)

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

Both runs use `retrieval-registry-1.0`. The definition and ordering were not
changed to manufacture a gain.

### 7.3 Candidate versus retained recall

| Pool | Recall@5 | Recall@10 | NDCG@10 | MRR |
|---|---:|---:|---:|---:|
| Search order | 0.0000 | 0.3333 | 0.0964 | 0.0756 |
| Ranked order | 0.2500 | 0.5833 | 0.3550 | 0.4370 |
| Retained evidence | 0.3333 | 0.4167 | 0.1846 | 0.1553 |
| Registry (reference) | 0.2500 | 0.5833 | 0.3599 | 0.4417 |

The gap between search order and ranked order is the ranking effect: labels were
present in the pool but below the merge point of the provider-ordered list. The
retained-evidence pool is reported in retention/query order, not rank order, so
its numbers are not directly interchangeable with the sorted registry.

### 7.4 Per case

| Case | Labels | Sources | Registry R@5 / R@10 | MRR | First label: search → ranked → retained → registry |
|---|---:|---:|---:|---:|---|
| G001 | 4 | 30 | 0.25 / 0.25 | 0.20 | 13 → 5 → 8 → 5 |
| G005 | 2 | 30 | 0.50 / 0.50 | 1.00 | 20 → 1 → 11 → 1 |
| G014 | 1 | 27 | 0.00 / 1.00 | 0.125 | 10 → 9 → 4 → 8 |

G001 found `https://arxiv.org/abs/2005.11401`. The other three labels were not in
the 45 unique search candidates, so no ranking or retention change could recover
them in this run. G005 found `2401.10774` but not `2211.17192`. G014 found its
single label only because depth reached search rank 10.

### 7.5 Fetch and duplicate statistics

| Metric | Total |
|---|---:|
| Registered sources | 87 |
| Search candidates | 125 |
| Ranked candidates | 124 |
| Redirects rejected | 1 |
| Duplicate search candidates removed | 11 |
| Cross-query duplicates skipped | 32 |
| Total duplicates removed | 43 |
| Fetch attempts / successes / failures | 70 / 58 / 12 |
| Fetch success rate | 82.86% |
| Snippet-only retained sources | 30 |

Per-case fetch attempts were 24, 24, and 22 against the 24 cap. The cap was
reached by G001 and G005.

### 7.6 Latency

| Metric | Baseline | Rerun |
|---|---:|---:|
| Mean | 465.05 s | 1214.11 s |
| P50 | 484.36 s | 1220.74 s |
| P95 | 497.54 s | 1385.77 s |
| Min / max | 411.79 / 499.00 s | 1017.48 / 1404.10 s |

Per-case planner / research / editor: `39.71 / 58.34 / 919.36` (G001),
`26.29 / 61.94 / 1315.85` (G005), `29.64 / 58.70 / 1132.38` (G014). Editor
generation over the larger evidence prompt dominates.

### 7.7 Answer quality (deterministic lexical proxies)

| Component | Baseline | Rerun |
|---|---:|---:|
| Faithfulness | 0.6667 | 0.3333 |
| Answer relevance | 0.6634 | 0.7284 |
| Contextual precision | 1.0000 | 0.6111 |
| Contextual recall | 0.7778 | 0.3333 |
| Citation support | 0.6667 | 0.2222 |
| Evidence coverage | 0.8175 | 0.3444 |
| Unsupported claims (lower is better) | 0.0000 | 0.3333 |
| **Overall** | **0.7402** | **0.4379** |

Per case: G001 `0.3678`, G005 `0.3385`, G014 `0.6074` (baseline `0.7105`,
`0.7005`, `0.8097`).

---

## 8. Interpretation

### 8.1 What the retrieval numbers show

- **Search is still the main bottleneck.** Search-order Recall@10 was `0.3333`:
  only 4 of the 7 labels across the three cases appeared anywhere in the 125
  candidate pool. For G001, three of four labels never appeared. Planner and
  ranking changes cannot recover a URL the provider never returns.
- **Ranking materially improved the order of what search did return.** Ranked
  Recall@10 (`0.5833`) is much higher than provider-order Recall@10 (`0.3333`).
  G005's label moved from rank 20 to rank 1; G001's moved from 13 to 5.
- **Deeper depth exposed G014's label.** It sat at search rank 10, outside the
  old 3-result depth. The registry captured it at rank 8, giving Recall@10
  `1.0` for that case.
- **Retention preserved the ranked top-10 labels.** Registry Recall@10 equals
  ranked-pool Recall@10 (`0.5833`), so the 5-item retention budget did not drop a
  label that ranking had placed in the top 10.
- **Canonicalisation did not merge distinct papers.** arXiv views and versions
  collapse by ID; the regression tests assert that different IDs remain
  different. In this run canonicalisation mainly prevented duplicate fetches and
  registry entries rather than changing recall directly.

### 8.2 Why answer quality fell

The retrieval result is better, but the final answer score is worse in this run.
The mechanism is partly measurement and partly generation:

1. **Evidence coverage is depth-sensitive.** It divides usable evidence by
   search results and grounded sources by retrieved sources. Going from ~3
   results/query to 10 lowers the ratio mechanically. This component carries
   only 0.05 weight, so it explains only a small part of the overall drop.
2. **The lexical support proxy penalises terse academic wording.** The editor now
   cites arXiv abstracts more often. A correct claim phrased in the report's
   words may share few tokens with an abstract, so faithfulness and citation
   support fall even when the source is more authoritative than a vendor blog.
3. **The report is still capped at three findings.** One unsupported claim moves
   faithfulness from `0.667` to `0.333`. The metric is discrete and unstable at
   this sample size.
4. **The editor receives ~27–30 sources.** The prompt is much larger than before.
   The model still writes under 400 words and cites only 4–5 sources, suggesting
   context dilution rather than broader synthesis. This run cannot separate that
   from normal model variance.

A controlled next step would be a configurable editor-context cap (for example
the top 12 ranked sources) measured against the same cases. That is not
implemented here because it would change the code after the reported measurement
and would require another live run to evaluate.

### 8.3 Latency trade-off

The run is ~2.6× slower. Most of the increase is Editor generation over the
larger prompt; search time itself increased from ~16 s to ~60 s for six queries.
The 24-fetch budget was fully used in two cases. If latency matters more than
depth, lower `MAX_TOTAL_PAGES_PER_RUN`, `MAX_PAGES_PER_QUERY`, or
`PLANNER_NUM_QUERIES`.

### 8.4 What is not claimed

- No causal attribution: the two configurations were each run once, not in an
  alternating controlled A/B.
- No general benchmark improvement: three cases, one run, exact-URL labels.
- No answer-quality or latency improvement: both moved in the wrong direction in
  this run and are reported as such.
- No factual-truth claim: citation alignment and lexical overlap are not
  entailment or human judgement.

---

## 9. Limitations

- Three cases are not a statistically representative benchmark. Results are
  host-, model-, and search-provider-dependent.
- Exact-URL labels measure agreement with a fixed verified set, not whether a
  retrieved page is useful or true.
- Candidate-pool recall is diagnostic; it says whether a stage saw the label, not
  whether the final answer used it well.
- The baseline artifact predates candidate-pool capture, so no baseline
  candidate-pool comparison exists. Only post-change candidate pools are
  reported.
- Ranking is a deterministic lexical/authority heuristic. It can still prefer a
  topically similar page over the exact labelled page, and authority scores are
  hand-set, not learned. Content-farm and low-quality detection is heuristic
  (host lists plus listicle title patterns), not an exhaustive classifier.
- Fetching remains best-effort. Some labelled pages may be unreachable, paywalled,
  or block automated requests.
- Answer-quality scores are lexical/structural proxies, not human or entailment
  judgements. No human evaluation or LLM judge was run.
- No controlled A/B alternation was performed; the comparison is observational
  and single-run per configuration.

---

## 10. Reproduction

```powershell
# Unit and regression suites
py -3.13 -m unittest discover -s tests -p "test_*.py"

# Mocked metric self-check
py -3.13 benchmarks/run_golden_evaluation.py --mode mocked

# Live three-case evaluation (requires Ollama + DuckDuckGo)
py -3.13 benchmarks/run_golden_evaluation.py --mode live --confirm-live --ids G001,G005,G014 --limit 3
```

To reproduce the old depth, set `SEARCH_MAX_RESULTS=3`,
`MAX_EVIDENCE_ITEMS_PER_QUERY=3`, `MAX_PAGES_PER_QUERY=2`,
`MAX_TOTAL_PAGES_PER_RUN=12`, `PLANNER_NUM_QUERIES=3`, and
`ENABLE_SOURCE_RANKING=false`.

---

## 11. Artifacts

| Artifact | Purpose |
|---|---|
| `benchmarks/results/golden_eval_live_20261118_091654.json` | Historical baseline (unmodified) |
| `tmp_baseline/baseline_20261118.json` | Extracted baseline metrics |
| `benchmarks/results/golden_eval_live_20261119_120333.json` | Post-change live result |
| `benchmarks/results/golden_eval_mocked_20261119_105919.json` | Post-change mocked metric self-check |
| `docs/RETRIEVAL_DIAGNOSTIC_REPORT.md` | Pre-change diagnosis |
| `docs/RETRIEVAL_OPTIMIZATION_REPORT.md` | This report |
