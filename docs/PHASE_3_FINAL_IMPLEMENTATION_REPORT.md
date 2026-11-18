# Phase 3 — Final Evaluation, Optimization and Production Readiness

**Date:** 2026-11-13  
**Project:** AI Research Agent (`C:\Reddiah\Research_agent`)  
**Scope:** Environment/live verification, evaluation engine, performance and
reliability hardening, persistence design, Streamlit evaluation view, security
review, documentation and final status.

---

## 1. Executive Summary

Phase 3 adds a reproducible, standard-library evaluation engine; bounded search
retries and fetch controls (deduplication, total budget, robots.txt, DNS-rebinding
mitigation); pipeline/node failure and latency reporting; additive evaluation
persistence; an additive Streamlit Evaluation tab; 48 new unit tests; and updated
documentation.

All executable Phase 3 checks passed. However, the **live application stack could
not be verified**: the default Python 3.10 environment has none of the
application dependencies installed, the DuckDuckGo HTML endpoint served a
bot-challenge page, and no `ddgs` client, Streamlit, SQLAlchemy or PostgreSQL
connection was available. No live end-to-end report, live evaluation score, live
dashboard render, or database migration was produced. The project therefore
cannot be declared production-ready on the basis of this phase alone.

No packages were installed, no database was contacted or reset, no datasets or
historical results were deleted, and nothing was staged, committed or pushed.
Benchmark v1/v2/v3 remain byte-for-byte unchanged and no `benchmark_v4.json` was
created.

---

## 2. Environment Inspection

| Item | Observed value |
|---|---|
| Default interpreter | Python `3.10.11` (`C:\Users\DELL\AppData\Local\Programs\Python\Python310\python.exe`) |
| Other interpreters | Python 3.13 and 3.14.4 installed via `py` |
| Virtual environment | None present in the repository |
| OS | Windows (`win32`) |
| Repository state | Existing uncommitted Phase 1/2 work preserved; nothing staged/committed |

Dependency availability in the default 3.10 interpreter:

| Package | State |
|---|---|
| `sqlalchemy`, `psycopg2` | missing |
| `streamlit`, `pandas`, `altair` | missing |
| `ddgs`, `ollama`, `langgraph` | missing |
| `pydantic`, `dotenv` | missing |

Consequences: the planner/researcher/editor pipeline, real search client,
Streamlit UI, and database persistence could not be executed. The Phase 2/3
standard-library suites were run instead.

---

## 3. Live Verification Results

Live probes were run without installing anything. They are reported separately
from synthetic/unit results.

| Probe | Result | Interpretation |
|---|---|---|
| Ollama API (`GET /api/tags`) | HTTP 200; models `qwen2.5:1.5b`, `qwen3:4b`, `qwen3:8b` | Service reachable |
| Ollama generation (`qwen3:4b`, bounded 24 tokens) | `done=true`, `eval_count=24`, `total_duration_ms=11293.5`; response was reasoning prose, not the requested JSON | Connectivity verified; JSON reliability **not** verified (output was truncated by the probe's token cap) |
| DuckDuckGo HTML endpoint | HTTP 202, 14,296 characters, `0` `result__a` markers | Bot-challenge page; result extraction not verified |
| `ddgs` client search | Not run — package missing | Unverified |
| Page fetch `https://www.iana.org/domains/example` | HTTP 200, 813 extracted characters, title “Example Domains” | Fetcher works against a TLS-trusted site |
| Page fetch `https://example.com` | `network_error:URLError`; underlying `CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate` | Local trust-store issue, not an application logic error |
| Full `Planner → Researcher → Editor` pipeline | Not run — dependencies missing | Unverified |
| Report generation / saved report | Not run | Unverified |
| Streamlit UI (including the Evaluation tab) | Not run — `streamlit` missing; module compiles | Unverified live |
| PostgreSQL persistence / migration | Not run — `sqlalchemy`/`psycopg2` missing; no DB connection attempted | Unverified live |

---

## 4. Architecture Changes

### 4.1 Evaluation package (`evaluation/`)

- `evaluation/metrics.py` — pure scoring functions with explicit, documented
  definitions:
  - relevance: `0.7 × mean(aspect token coverage) + 0.3 × topic token coverage`;
  - completeness: `0.75 × required-heading coverage + 0.25 × min(words / rubric minimum, 1)`;
  - citation quality: `0.6 × citation precision + 0.4 × claim-citation coverage`;
  - evidence coverage: mean of retrieval usability and citation grounding;
  - factual support: share of **verified** benchmark claims whose lexical token
    coverage meets the threshold;
  - overall: weighted mean over measured components only.
- `evaluation/engine.py` — dataset loading with SHA-256, question lookup,
  benchmark/open-topic record builders, readiness audit, deterministic record
  digest, and explicit `measured` / `not_measurable` / `no_citations` statuses.
- `evaluation/store.py` — append-only JSONL records and summary aggregation.
- `benchmarks/run_evaluation.py` — readiness CLI, report evaluation CLI, and a
  deterministic synthetic self-check.

Design guarantees:

1. Citation alignment is never described as factual correctness.
2. `partially_supported` and `needs_more_evidence` claims are excluded from the
   factual-support numerator/denominator and preserved as unresolved.
3. Open-topic runs set factual support to `not_measurable`; no benchmark score is
   invented for free-form research.
4. Missing components are `None` and removed from the overall weighting rather
   than imputed as zero.
5. The engine is standard-library only and never writes benchmark datasets.
6. Phantom URLs reported by the grounding pass are merged into citation quality,
   so a state-level unsupported citation cannot be scored as aligned.

### 4.2 Reliability and fetch safety (`tools/page_fetcher.py`)

- Optional DNS resolution; a hostname resolving to any private/loopback/
  link-local/reserved address is rejected.
- Redirects are re-validated against the same host/DNS policy.
- Optional robots.txt policy with per-origin caching; retrieval failures are
  fail-open and counted.
- `FetchCache` deduplicates canonical URLs across queries and enforces a hard
  per-run fetch budget; exceptions and malformed results are contained.
- Stable, non-secret error codes (`timeout`, `tls_certificate_error`,
  `network_error:<Type>`, `robots_disallowed`, `fetch_budget_exhausted`, etc.).

### 4.3 Search resilience (`tools/web_search.py`)

- Lazy `ddgs` import so the module can be imported without the dependency.
- Bounded retries with linear backoff, only on raised exceptions.
- Diagnostics (`attempts`, `error`) available to the Researcher.
- Malformed provider records are filtered instead of crashing formatting.

### 4.4 Pipeline instrumentation (`graph/workflow.py`)

- `GraphState` now carries `evaluation`, `pipeline_latency_seconds` and
  `pipeline_error`.
- The research node reports search attempts/failures, cache hits, budget
  exhaustion, robots blocks, fetch failures and registered-source counts.
- The editor node computes an open-topic evaluation record.
- `run_research_pipeline` times the whole invocation and re-raises failures after
  attaching latency/error diagnostics.

### 4.5 Persistence (`database/database.py`)

Seven nullable columns were added to `research_sessions`:

`evaluation_overall_score`, `evaluation_relevance_score`,
`evaluation_evidence_coverage`, `evaluation_citation_quality`,
`evaluation_completeness`, `evaluation_factual_support`, `evaluation_metadata`.

`save_session(..., evaluation=...)` stores numeric component scores (or `NULL`)
and a JSON copy of the record. New read helpers:

- `get_evaluation_averages`
- `get_evaluation_series`
- `get_evaluation_summary`

Migration is additive (`ALTER TABLE ... ADD COLUMN`, nullable), idempotent, and
never drops/recreates tables or rewrites rows. The migration was **not executed**
against any database in this phase; no production database was contacted.

### 4.6 Streamlit (`ui/evaluation_view.py`, `app.py`)

A fifth tab, **🔬 Evaluation**, was added while preserving the existing four tabs
and `ui/analytics.py`. It shows measured quality averages, claim-citation
coverage, unresolved-claim and unsupported-citation counts, total and per-node
latency, a historical score trend, and recent records. Unmeasured values render
as `—` with an explanation; citation quality is labelled as alignment, not
factual correctness.

---

## 5. Files Modified / Created

### Phase 3 modified (tracked)

- `agents/research_agent.py` — shared fetch cache/budget, robots/DNS options,
  search diagnostics and expanded metrics.
- `app.py` — Evaluation tab, evaluation persistence wiring, failure record.
- `config/config.py` — fetch budget, robots, DNS, retry, evaluation settings.
- `database/database.py` — evaluation columns, migration entries, save/read
  helpers, docstring repair.
- `graph/workflow.py` — state fields, node metrics, evaluation, pipeline timing
  and failure handling.
- `tools/web_search.py` — lazy import, retries, diagnostics, malformed-result
  filtering.
- `ui/components.py` — bounded, whitespace-normalised topic input (input
  validation).
- `README.MD` — Phase 3 setup, evaluation methodology, live verification status,
  test results, limitations and production blockers.
- `.env.example` — documented optional Phase 3 settings.

### Phase 3 created

- `evaluation/__init__.py`
- `evaluation/metrics.py`
- `evaluation/engine.py`
- `evaluation/store.py`
- `benchmarks/run_evaluation.py`
- `ui/evaluation_view.py`
- `tests/test_phase3_evaluation.py` (23 tests)
- `tests/test_phase3_reliability.py` (25 tests)
- `docs/PHASE_3_FINAL_IMPLEMENTATION_REPORT.md` (this report)

### Phase 3 extended (untracked from Phase 2)

- `tools/page_fetcher.py` — DNS safety, robots policy, fetch cache, error
  classification, new fetch options.

All pre-existing modified/untracked files, datasets, reports and screenshots were
preserved.

---

## 6. Evaluation Methodology

| Metric | Formula | Explicit limitation |
|---|---|---|
| Relevance | `0.7 × mean(aspect coverage) + 0.3 × topic coverage` | Lexical only; not semantic relevance or correctness |
| Evidence coverage | Mean of `usable_evidence / search_results` and `grounded_sources / retrieved_sources` | Does not measure source authority or truth |
| Citation quality | `0.6 × (supported / all citations) + 0.4 × claim-citation coverage` | A retrieved URL may still be cited for a claim it does not support |
| Completeness | `0.75 × heading coverage + 0.25 × min(words / rubric minimum, 1)` | Structural only; not writing quality |
| Factual support | Verified claims with token coverage ≥ threshold / verified claims | Lexical proxy; not entailment or factual verification |
| Overall | Weighted mean over measured components (`0.20/0.20/0.20/0.15/0.25`), re-normalised | Unmeasured components are excluded, never treated as zero |

Unresolved-claim policy (audited from `benchmark_v3.json`, unchanged):

- 40 questions, 120 claims;
- 100 `verified`, 18 `partially_supported`, 2 `needs_more_evidence`;
- 25/40 questions fully verified;
- 20 unresolved claim IDs: `Q07-C3, Q09-C3, Q17-C1, Q17-C2, Q17-C3, Q20-C3,
  Q22-C2, Q24-C3, Q25-C3, Q27-C1, Q29-C2, Q30-C2, Q30-C3, Q31-C1, Q31-C3,
  Q32-C1, Q33-C2, Q38-C2, Q38-C3, Q40-C2`.

These claims remain unresolved and are never promoted by the engine. The
readiness audit is a statement about recorded dataset verification, not about
agent performance.

Reproducibility: records include dataset filename/version/SHA-256, metric
definitions, measured/unmeasured lists, weights used, and a deterministic
SHA-256 digest that excludes `evaluated_at`. `--record` appends JSONL lines and
never rewrites existing entries.

---

## 7. Exact Executed Test Results

| Command | Result |
|---|---|
| `python -m py_compile` on all changed/new modules | exit 0 (`PY_COMPILE_OK`) |
| `python tests/test_phase2_research_quality.py` | 26 tests, `RESULT: PASS` |
| `python tests/test_phase3_dataset_verification.py` | 44 tests, `RESULT: PASS` |
| `python benchmarks/validate_dataset.py --dataset benchmarks/datasets/benchmark_v3.json` | 29/29 checks, `RESULT: PASS`; dataset unchanged before/after |
| `python tests/test_phase3_evaluation.py` | 23 tests, `RESULT: PASS`; explicit Python exit code 0 |
| `python tests/test_phase3_reliability.py` | 25 tests, `RESULT: PASS`; explicit Python exit code 0 |
| `python benchmarks/run_evaluation.py --self-check` | `SELF-CHECK PASS`; explicit Python exit code 0 |
| `python benchmarks/run_evaluation.py --readiness` | counts: 40 questions, 120 claims, 100 verified, 20 unresolved, 25 verified questions |

Self-check output (synthetic fixture, **not** a live pipeline result):

```text
SELF-CHECK (synthetic fixture; not a live pipeline result)
  dataset: benchmark_v3.json sha256=10a5ba16c400a1ca…
  overall_score: 1.0
  factual_support: 1.0
  completeness: 1.0
  record_digest: 6802ea95aeef881a96095e3cbc1589b3661d73f107c9b365da061f1c2303877f
  unresolved claims preserved for Q17: ['Q17-C1', 'Q17-C2', 'Q17-C3']

SELF-CHECK PASS
```

New Phase 3 tests: 48 (23 evaluation + 25 reliability). Phase 2/3 standard-library
regression total: 26 + 44 + 48 = 118 tests, plus 29 structural validator checks.

A heading-detection defect was found and fixed during verification:
`completeness` previously flattened newlines before checking headings, which
could make every section appear present. The fixture now passes for the correct
reason, and `test_section_presence_requires_markdown_headings` guards the
behaviour.

**Not run (dependencies unavailable):** `test_analytics_dashboard.py`,
`test_metrics_persistence.py`, `test_performance_tracking.py`,
`test_web_search.py`, `test_phase2_instrumentation.py`. No result is claimed for
them in this phase.

---

## 8. Benchmark and Dataset Integrity

| File | SHA-256 |
|---|---|
| `benchmarks/datasets/benchmark_v1.json` | `4f1cb7f5ca19e1c58e10de714844fd042ffdd70ac4f8321e80046a6cd52573ec` |
| `benchmarks/datasets/benchmark_v2.json` | `5f859b81be45b787b5a6108378a3b9abe0584e4697ad3d8def0f65a49a5b779f` |
| `benchmarks/datasets/benchmark_v3.json` | `10a5ba16c400a1cac0040e4357df49d97fc09e258f006999df96c93df2ff2527` |

All three hashes match the Phase 2 baseline. `benchmarks/datasets/benchmark_v4.json`
does not exist. Historical results in `benchmarks/results/` were not modified.

---

## 9. Performance Measurements

Instrumentation added: node durations, summed search network time, total pipeline
duration, and failure diagnostics. Live measurement of these fields was not
possible without the installed stack.

Historical local baseline from earlier recorded runs (not re-measured in
Phase 3; retained for context):

| Metric | Value |
|---|---:|
| Recorded runs | 18 |
| Timed runs | 13 |
| Mean execution time | 127.85 s |
| Minimum | 97.53 s |
| Maximum | 222.65 s |
| P95 | 221.72 s |
| Unique source URLs | 159 (8.83 per run) |

Phase 2 synthetic evidence-processing measurement (not re-run in Phase 3):
evidence assembly grew from 32 to 833 characters and from 0.002 ms to 1.446 ms
per query after page fetching was enabled.

Live probe timings: one bounded `qwen3:4b` generation completed in 11,293.5 ms
(24 evaluated tokens) — connectivity only, not a pipeline latency measurement.

---

## 10. Security Review

### Findings

1. **Secrets handling** — no hardcoded credentials found. `DATABASE_URL` is read
   from environment/`.env`, and `.env` is git-ignored. Database engine logging
   strips the user-info portion before logging. `.env.example` contains
   placeholders only.
2. **Input validation** — the Streamlit topic input is bounded to 500
   characters and whitespace-normalised; an all-whitespace topic cannot start a
   run. SQL injection is not a practical risk because SQLAlchemy parameterises
   writes, but prompt injection and LLM context abuse remain possible.
3. **URL fetching (SSRF)** — scheme restriction, private/loopback literal
   rejection, optional DNS checks, redirect re-validation, byte/time caps and
   content-type filtering are implemented. Residual TOCTOU risk between DNS check
   and connection; no full IP pinning. robots.txt is fail-open by design.
4. **Untrusted content** — fetched page text and search snippets are inserted
   into LLM context. They are treated as data in prompts, but prompt injection
   cannot be fully eliminated at this layer.
5. **Error handling** — page/search failures are contained and classified with
   non-secret codes; failed runs are persisted as `FAILED`. The Streamlit error
   record includes raw exception text (`Error: {e}`), which can expose internal
   details to whoever can open the local UI. Stored `error_message` values are
   sanitized.
6. **Logging** — logs include topic prefixes and up to 200 characters of raw
   model output on JSON parse failure. No credentials or API keys were observed
   in logs, but generated content could contain sensitive material and should be
   treated accordingly.
7. **Dependency configuration** — versions are pinned in `requirements.txt`, but
   there is no lockfile/hash verification, no CI vulnerability scan, and the
   `ddgs` scraping path is inherently fragile (bot challenge observed live).
8. **Persistence safety** — migrations are additive, nullable and non-destructive.
   Migration exceptions are logged and swallowed; if a legacy schema fails to
   gain the new columns, later writes referencing them can fail. This is visible
   in logs but does not abort startup.
9. **Access control** — none. Any local user who can open the app can view and
   delete all stored research history.

### Production blockers identified

- No authentication/authorisation or per-user isolation.
- No rate limiting or concurrency/load testing.
- Live stack (search, pipeline, UI, DB migration) unverified in this environment.
- No CI, dependency lockfile/hashes, or vulnerability scanning.
- No retention policy for stored research or logs.

No secrets, credentials, or production data were accessed or modified during this
review.

---

## 11. Deployment Readiness

| Area | Readiness |
|---|---|
| Evaluation engine and metric definitions | Ready for staging use (unit-verified) |
| Fetch/search safety controls | Ready for staging use (unit-verified with injected dependencies) |
| Persistence schema | Designed and additive; **not exercised live** |
| Streamlit Evaluation tab | Implemented and compiles; **not rendered live** |
| Full pipeline | **Not verified live** |
| Production deployment | **Not ready** — blockers above |

Reproducible setup for the missing stack is documented in `README.MD`
(Python 3.10/3.11 virtual environment, `pip install -r requirements.txt`,
`ollama pull qwen3:4b`, `DATABASE_URL` in `.env`). Installation was not performed
here because authorization was not given.

---

## 12. Remaining Limitations

- Live search, full pipeline, UI and database verification remain outstanding.
- Evaluation metrics are lexical/structural proxies; factual support is not
  entailment or truth, and benchmark execution has not been run end-to-end.
- DNS-rebinding mitigation is check-time only (TOCTOU residual); robots.txt
  failures are allowed.
- DuckDuckGo HTML access can be challenged; the `ddgs` client path is untested.
- Some sites fail against the local TLS trust store.
- No hard wall-clock timeout is enforced around LLM generation; a hung Ollama
  request is reported only if it raises. Fetch and search operations are bounded.
- No authentication, concurrency testing, rate limiting, retention policy or CI.

---

## 13. Final Status

**NEEDS ATTENTION** — Phase 3 code and all executable unit/synthetic checks pass,
and benchmark integrity is preserved, but production readiness cannot be claimed
until the missing dependency stack is provisioned and live search, the full
pipeline, the Streamlit Evaluation tab and PostgreSQL persistence/migration are
verified end-to-end.
