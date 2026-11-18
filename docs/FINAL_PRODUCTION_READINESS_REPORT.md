# Final Production-Readiness Report

**Project:** AI Research Agent (`C:\Reddiah\Research_agent`)  
**Date:** 16 November 2026  
**Verified runtime:** Python 3.13.13 on Windows 11, local Ollama with `qwen3:4b`, local PostgreSQL 18.3  
**Scope:** Five production-readiness improvements plus final validation  
**Final status:** **PASS WITH LIMITATIONS**

> The status means the requested work is implemented and verified for the scopes
> listed here. It is **not** a claim of full production readiness. Section 8 lists
> the production risks that remain.

---

## 1. Executive summary

| Task | Outcome | Verification |
|---|---|---|
| 1. PostgreSQL persistence for `key_findings`, `unsupported_citations`, `source_details` | **Implemented** | Additive JSONB/JSON columns, save/retrieve wiring, history UI, migration and round-trip tests |
| 2. Word-count accuracy | **Implemented** | Single `count_words()` helper, model value no longer trusted, persistence and retrieval use the final body, regression tests |
| 3. Factual-support evaluation | **Implemented** | Deterministic `evidence_factual_support()` with supported / partially supported / unsupported / unverifiable classes, wired into open-topic evaluation and UI |
| 4. README and documentation | **Implemented** | Rewritten against the actual implementation; dependency versions match `requirements.txt`; limitations added |
| 5. Reproducible performance and reliability testing | **Implemented** | Nine mocked scenarios, SQLite persistence measurement, optional one-run real mode; actual results recorded |
| Final validation | **Completed** | 184 checks passed, 0 failed; validator 29/29; app health check OK; migration applied with row counts unchanged |

Key numbers:

- **184** test/verification checks passed (133 unittest, 7 instrumentation, 44 dataset checks), **0 failed**.
- **29/29** explicit `benchmark_v3.json` structural checks passed.
- Mocked harness: **13** pipeline runs, **11** successful DB writes, **1** simulated DB failure, **2** expected failure events, **0** unexpected failures.
- Existing database: **19** sessions, **19** reports, **167** sources before and after migration; max session ID **23** unchanged.
- Benchmark hashes unchanged for v1, v2, and v3.

No database rows were deleted, truncated, or rewritten. No benchmark dataset was modified. No package was installed or upgraded. Nothing was staged, committed, or pushed.

---

## 2. Changes implemented per task

### Task 1 — PostgreSQL persistence

**Problem.** `ResearchReport` already carried `key_findings`,
`unsupported_citations`, and `source_details`, but `ResearchReportRow` had no
matching columns. `save_session()` projected only five report fields and
`get_session_by_id()` read back only those five. Session 23 had three findings in
memory but none in the database.

**Implementation.**

- Added three nullable columns to `ResearchReportRow`:
  - `key_findings`
  - `unsupported_citations`
  - `source_details`
- Used a SQLAlchemy type variant: `JSONB` on PostgreSQL, plain `JSON` on SQLite.
- `save_session()` now persists all three lists for structured reports and
  writes empty lists for raw-string fallback reports.
- `get_session_by_id()` restores all three lists, normalising historical `NULL`
  to `[]`.
- The History view now renders key findings, an unsupported-citation warning,
  and a source-provenance expander.
- Existing duplicate protection is retained: one report row per session
  (`UNIQUE(session_id)`), one session per `run_id` (unique index), rollback on
  failure.

**Backward compatibility.** Historical rows keep `NULL`; readers return empty
lists. No row is updated by the migration.

### Task 2 — Word-count accuracy

**Problem.** `normalize_report_payload()` trusted any positive integer supplied
by the model. Session 23 stored **398** while the text contained **251**
whitespace tokens.

**Implementation.**

- Added `count_words()` in `tools/report_parsing.py`.
  - Counts whitespace-delimited tokens containing at least one alphanumeric
    character.
  - Ignores structural Markdown/JSON tokens such as `#`, `---`, `|`, `*`.
  - Returns `0` for empty, whitespace-only, or non-string input.
- `normalize_report_payload()` now always computes from the final report body;
  the model's `word_count` is ignored.
- Markdown fallback, no-evidence report, persistence, evaluation, and retrieval
  use the same helper.
- Persistence recomputes from `report.report`; retrieval recomputes from stored
  text so historical rows display correctly without mutating them.
- Added `stored_word_count` to retrieval output for auditability.

**Session 23 reconciliation (verified read-only):**

| Measure | Value |
|---|---:|
| Stored value before fix | 398 |
| `len(text.split())` | 251 |
| Structural tokens (`#`, table separators, `---`, etc.) | 23 |
| `count_words()` actual word-bearing tokens | **228** |

The 23-token difference is Markdown structure, not prose. The new displayed value
is 228; the 251 figure is the raw whitespace-token count. Both are now stated
explicitly instead of presenting the inaccurate 398.

### Task 3 — Factual-support evaluation

**Problem.** Open-topic runs left `factual_support` as `not_measurable`. Benchmark
`claim_support` remained valid but only scored verified reference claims.

**Implementation.**

- Added `evidence_factual_support()` to `evaluation/metrics.py`.
- Claims come from `key_findings`; when none exist, bounded sentences are
  extracted from the report deterministically (maximum 40).
- Each claim's `source_ids` are resolved against the source registry. The check
  compares claim content tokens with the cited source titles/excerpts.
- Classification:
  - `supported` — coverage ≥ threshold (default 0.55)
  - `partially_supported` — coverage ≥ 0.30 and < threshold
  - `unsupported` — usable evidence exists but coverage < 0.30
  - `unverifiable` — no source reference, unknown source ID, excerpt too short
    (< 5 content tokens), or a claim with no checkable terms
- Score: `(supported + 0.5 × partially_supported) / verifiable_claims`.
- Unverifiable claims are excluded, never scored as false. If no claim has usable
  evidence, the component is `not_measurable` with reason
  `insufficient_evidence`.
- `evaluate_open_state()` now receives `source_registry` from the workflow and
  uses the evidence-based check. Benchmark mode still uses `claim_support()` over
  verified claims, so existing benchmark semantics are preserved.
- Claim-level details (`classification`, `evidence_coverage`, `valid_source_ids`,
  `invalid_source_ids`, `reason`) are stored inside `evaluation_metadata`.
- The Evaluation UI labels the metric as an evidence proxy for both modes and
  explains that unverifiable claims are excluded.

**Limitations.** This is lexical overlap, not entailment. It cannot detect
negation, contradiction, paraphrase with little shared vocabulary, or a source
that is itself wrong. No LLM judge is used.

### Task 4 — README and documentation

Rewrote `README.MD` against the implementation:

- Correct architecture: `START → planner → researcher → editor → END`.
- Separate responsibilities for planner, researcher, editor, and workflow nodes.
- Technology versions aligned with `requirements.txt`:
  - Python 3.13 verified (3.10+ supported by metadata)
  - Streamlit 1.58.0, Altair 6.2.1
  - LangGraph 0.6.5, SQLAlchemy 2.0.43, psycopg2-binary 2.9.10, Pydantic 2.11.7
- RAG/evidence format, grounding, citation validation, and word-count method.
- PostgreSQL tables, additive migrations, idempotency, and rollback behaviour.
- Evaluation metrics including both factual-support modes and the unverifiable
  rule.
- Installation, PostgreSQL setup, Ollama setup, environment variables, running
  the app, testing, benchmark execution, and the performance harness.
- Known limitations and an explicit "not production-ready" statement.
- Removed the stale claim that dependencies were missing and live verification
  had never happened; replaced with the actual verified scopes.

### Task 5 — Performance and reliability testing

Added `benchmarks/performance_harness.py`:

- Default mode is fully mocked and bounded. No Ollama, DuckDuckGo, or PostgreSQL
  call occurs. A temporary SQLite database measures real persistence latency and
  round-trip reads.
- Scenarios: small, medium, large, three sequential requests, three repeated
  identical queries, external search failure, LLM timeout, invalid LLM response,
  and database persistence failure.
- Measures pipeline total, planner/research/editor latency, evaluation latency
  (inside total), SQLite persistence duration, `tracemalloc` peak memory, error
  and timeout outcomes, word-count/round-trip correctness, and deterministic
  repeated results.
- Optional `--real --confirm-real` mode performs one bounded live run and requires
  `PERF_ALLOW_REAL=1` and `DATABASE_URL`. It was **not** run.
- Results are written to
  `benchmarks/results/performance_harness_latest.json`.

---

## 3. Files created and modified

### Created in this assignment

| File | Purpose |
|---|---|
| `tests/test_production_hardening.py` | 20 tests for persistence, word count, and factual support |
| `benchmarks/performance_harness.py` | Reproducible mocked/optional-real performance harness |
| `benchmarks/results/performance_harness_latest.json` | Actual harness output |
| `docs/FINAL_PRODUCTION_READINESS_REPORT.md` | This report |

### Modified in this assignment

| File | Change |
|---|---|
| `database/database.py` | JSONB/JSON report columns, report migration, save/retrieve fields, recomputed word count |
| `tools/report_parsing.py` | `count_words()`; model word count ignored; fallback uses helper |
| `agents/editor_agent.py` | No-evidence report uses `count_words()` |
| `evaluation/metrics.py` | `evidence_factual_support()` and thresholds; completeness uses the shared word helper |
| `evaluation/engine.py` | Open-topic factual support, source registry plumbing, methodology notes |
| `graph/workflow.py` | Passes `source_registry` into open-topic evaluation |
| `ui/components.py` | History rendering for findings, unsupported citations, source provenance |
| `ui/evaluation_view.py` | Factual-support labels/help text updated |
| `tests/test_phase3_evaluation.py` | Open-topic test now verifies the evidence-based not-measurable path |
| `README.MD` | Full documentation rewrite |

### Pre-existing changes preserved

The working tree already contained unrelated modifications from earlier phases
(for example `schemas/models.py`, `agents/research_agent.py`, `app.py`,
`config/config.py`, `generation/llm.py`, `tools/web_search.py`,
`tools/grounding.py`, `tools/page_fetcher.py`, `tools/research_evidence.py`,
`evaluation/`, `requirements.txt`). They were not reverted or rewritten. The
`requirements.txt` Streamlit 1.58.0 pin was already present before this task and
was verified, not changed again.

Git state after the work: **65 entries**, **0 staged**, branch `main`, HEAD
`6279c8f`. No commits, pushes, or deletions were performed.

---

## 4. Database migration details

### Columns added

Applied to the existing database by `init_database()`:

```sql
ALTER TABLE research_reports ADD COLUMN key_findings JSONB;
ALTER TABLE research_reports ADD COLUMN unsupported_citations JSONB;
ALTER TABLE research_reports ADD COLUMN source_details JSONB;
```

On SQLite the same statements use `JSON`. All columns are nullable.

### Migration properties

- Additive only: no `DROP`, `TRUNCATE`, `UPDATE`, or table recreation.
- Idempotent: absent columns are added, existing columns are skipped.
- Wrapped in a guarded try/except so a migration check cannot prevent startup.
- Uses `JSONB` on PostgreSQL and `JSON` on SQLite through a SQLAlchemy type
  variant.
- Existing telemetry/evaluation migration and the unique `run_id` index are
  unchanged.

### Applied migration evidence

| Check | Before | After |
|---|---:|---:|
| `research_reports` column count | 8 | 11 |
| `research_sessions` rows | 19 | 19 |
| `research_reports` rows | 19 | 19 |
| `research_sources` rows | 167 | 167 |
| Max session ID | 23 | 23 |

Columns confirmed present: `key_findings`, `unsupported_citations`,
`source_details`.

Session 23 remained readable; its new fields deserialize as empty lists and its
recomputed word count is 228 (stored 398).

### Rollback and failure behaviour

- `save_session()` rolls back the transaction on any exception and re-raises.
- A failed JSON serialisation test left **0** session and report rows.
- Duplicate `run_id` saves return the existing session ID; the test confirmed one
  session and one report row.
- Duplicate report insertion is prevented by `UNIQUE(session_id)`.

---

## 5. Test results

Command environment: `py -3.13 -W ignore`, project root. No live services were
required by any suite below.

| Suite | Result | Count |
|---|---|---:|
| `tests/test_editor_generation_optimization.py` | PASS | 17 |
| `tests/test_phase2_research_quality.py` | PASS | 26 |
| `tests/test_phase2_instrumentation.py` | PASS | 7 |
| `tests/test_phase3_evaluation.py` | PASS | 23 |
| `tests/test_phase3_reliability.py` | PASS | 25 |
| `tests/test_phase3_dataset_verification.py` | PASS | 44 |
| `tests/test_production_hardening.py` | PASS | 20 |
| `tests/test_analytics_dashboard.py` | PASS | 9 |
| `tests/test_metrics_persistence.py` | PASS | 7 |
| `tests/test_performance_tracking.py` | PASS | 6 |
| **Total** | **PASS** | **184** |

Additional verification:

| Check | Result |
|---|---|
| `validate_dataset.py --dataset benchmarks/datasets/benchmark_v3.json` | 29/29 PASS |
| Default `validate_dataset.py` (benchmark_v1) | 15/15 PASS |
| `run_evaluation.py --readiness` | PASS; 40 questions, 120 claims, 100 verified, 20 unresolved |
| `run_evaluation.py --self-check` | SELF-CHECK PASS |
| Python compile of all changed modules | PASS |
| Streamlit headless health check | HTTP 200, `ok` |

Not run, deliberately:

- `tests/test_web_search.py` — performs live DuckDuckGo calls; excluded to avoid
  uncontrolled external requests. The file is not a unit test.
- Full 40-question benchmark (`run_benchmark.py`) — expensive live model/search
  run; not required for this validation and not executed.
- `tests/test_db_postgres.py` — not present in this checkout; no such test was
  run.
- Real performance mode — requires explicit opt-in and live services.

### New test coverage

`tests/test_production_hardening.py` covers:

- short, long, empty, whitespace-only, punctuation, and Markdown-structure word
  counts;
- model-supplied `word_count=398` with 251 actual tokens normalising to the
  computed value;
- full structured round-trip;
- legacy `NULL` rows loading as empty lists;
- duplicate-save idempotency;
- failed-write rollback;
- additive migration on an existing table and idempotent re-run;
- fully supported, unsupported, partially supported, missing-source, invalid-
  citation, empty-report, and insufficient-evidence factual-support cases;
- open-topic evaluation producing a measured factual-support component.

---

## 6. Performance measurements

### 6.1 Mocked harness — actual results

Conditions: Python 3.13.13, Windows 11, deterministic stubs for planner,
researcher, and editor, temporary SQLite persistence, no network, no Ollama, no
PostgreSQL. Times measure orchestration, local evaluation, and SQLite only.

Generated at `2026-11-16T16:44:49+00:00`.

| Scenario | Pipeline s | Planner s | Research s | Editor s | DB ms | Peak KiB | Overall eval | Factual |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Small query | 0.075 | 0.000 | 0.001 | 0.001 | 119.97 | 406.0 | 0.9221 | 1.0 |
| Medium query | 0.049 | 0.000 | 0.001 | 0.002 | 38.74 | 353.8 | 0.9257 | 1.0 |
| Large query | 0.055 | 0.000 | 0.003 | 0.002 | 36.65 | 347.8 | 0.9262 | 1.0 |
| Search failure | 0.044 | 0.000 | 0.000 | 0.001 | 23.74 | 346.2 | 0.2976 | not measurable |
| Invalid LLM response | 0.038 | 0.000 | 0.001 | 0.001 | 35.01 | 336.8 | 0.1620 | not measurable |
| DB persistence failure | 0.047 | 0.000 | 0.001 | 0.002 | 14.49 (failed) | 345.1 | 0.9221 | 1.0 |
| Sequential ×3 | 0.6859 total | — | — | — | 86.48 total | 346.4 | — | — |
| Repeated ×3 | 0.7630 total | — | — | — | 86.00 total | 348.9 | — | — |
| LLM timeout | 0.2411 elapsed | — | — | — | — | 344.3 | — | — |

Aggregate:

| Metric | Result |
|---|---:|
| Scenarios | 9 |
| Leaf pipeline runs | 13 |
| Completed runs | 12 |
| Expected failures observed | 2 (LLM timeout, DB commit failure) |
| Unexpected failures | 0 |
| Successful DB writes | 11 |
| Failed DB writes | 1 |
| Rows after run | 11 sessions / 11 reports (match writes) |
| Repeated-query determinism | True (identical digests) |

The first SQLite write is slower (≈120 ms) than subsequent writes (≈24–39 ms),
consistent with first-write connection/transaction warm-up.

### 6.2 Live end-to-end history

The most recent verified live run predates these changes; it used the same model,
prompts, and graph:

| Metric | Value |
|---|---:|
| Pipeline total | 231.175 s |
| Editor | 193.217 s |
| Sources | 6 |
| Grounding ratio | 1.0 |
| Evaluation overall | 0.9379 |
| Session | 23 |

The earlier Editor baseline was 734.054 s, but this was **not a controlled A/B**:
prompt size also changed (2,355 → 1,383 tokens), and the baseline is not stored in
the database. The improvement is therefore reported as a single-run observation,
not a benchmark result.

### 6.3 What is not measured

- Real Ollama latency and token usage: the generation client does not expose token
  counts; `--real` mode reports them as unavailable.
- Real PostgreSQL persistence latency: measured only on SQLite. The migration was
  applied to PostgreSQL, but write timing was not benchmarked.
- Concurrent or multi-user load.
- Long-duration memory growth.

---

## 7. Data-integrity verification

| Check | Result |
|---|---|
| Existing rows preserved | 19 sessions / 19 reports / 167 sources before and after |
| Schema change | Additive only; `research_reports` 8 → 11 columns |
| Historical records readable | Session 23 loaded successfully |
| Historical new fields | `key_findings`, `unsupported_citations`, `source_details` returned as lists |
| Word count on historical row | Recomputes to 228; stored 398 retained unchanged |
| Duplicate save | Same session ID; one report row |
| Failed write | Rolled back; zero partial rows |
| Migration idempotency | Second `init_database()` run succeeded |
| Benchmark datasets | All three SHA-256 hashes unchanged |
| Dataset writes | None |
| Production/user data used for tests | None; all DB tests used temporary SQLite |

Frozen benchmark hashes after the work:

```text
4f1cb7f5ca19e1c58e10de714844fd042ffdd70ac4f8321e80046a6cd52573ec  benchmark_v1.json
5f859b81be45b787b5a6108378a3b9abe0584e4697ad3d8def0f65a49a5b779f  benchmark_v2.json
10a5ba16c400a1cac0040e4357df49d97fc09e258f006999df96c93df2ff2527  benchmark_v3.json
```

---

## 8. Remaining technical limitations

1. **Not production-ready.** No authentication, authorisation, per-user isolation,
   rate limiting, concurrency control, or production load testing. The local UI
   exposes every stored session to anyone who can open it.
2. **Evaluation is lexical.** All metrics, including open-topic factual support,
   are token-overlap proxies. They cannot establish entailment, detect
   contradiction, or verify that a source is correct. No LLM judge or human review
   is implemented.
3. **Full benchmark not executed.** Only dataset validation, readiness auditing,
   and a synthetic self-check were run. There is no measured 40-question
   end-to-end accuracy result.
4. **Performance evidence is partial.** The mocked harness cannot predict real
   Ollama/PostgreSQL latency; the live run is a single observation, and the Editor
   improvement was not a controlled A/B.
5. **Word-count definition changed.** `count_words()` excludes Markdown structural
   tokens, producing 228 for session 23 versus 251 raw whitespace tokens. This is
   consistent across storage, retrieval, and completeness, but differs from the
   earlier approximate 251 figure.
6. **Transitive dependency drift.** `requirements.txt` pins are mutually
   consistent, but a fresh install may resolve patch versions differently from the
   verified host (for example SQLAlchemy, psycopg2, Pydantic).
7. **PostgreSQL migration is local-only.** Additive migrations were applied to the
   local database. No production-scale migration/rollback drill, backup/restore
   test, or lock-contention test was performed.
8. **Untrusted web content.** Page text and source excerpts enter the LLM prompt.
   Bounded fetching, filtering, and citation checks reduce but do not eliminate
   prompt-injection risk.
9. **DNS TOCTOU and fail-open robots.** DNS safety is checked before connecting;
   a hostile resolver could change answers between check and connect. An
   unreachable `robots.txt` allows fetching.
10. **No timeout around LLM generation.** A hung Ollama request must be interrupted
    externally, though search and fetch calls are bounded.
11. **No CI, lockfile hashes, vulnerability scanning, or retention policy.**
12. **Upgrade warnings.** Streamlit warns that `use_container_width` will be
    removed; LangGraph warns that the `allowed_objects` default will change.
13. **Storage growth.** `source_details` duplicates registry metadata alongside
    `research_sources`; no retention or compaction policy exists.
14. **Live search tests excluded.** `tests/test_web_search.py` was not run because
    it makes uncontrolled DuckDuckGo requests.

---

## 9. Security considerations

- No API key, password, or connection string was printed in test output or this
  report. The migration script printed only schema and row information.
- `save_session()` sanitises persisted error messages: credential blocks are
  masked, the configured database URL is replaced, and messages are truncated.
- Structured report fields may contain public web excerpts and model-generated
  claims. They are not secrets, but they are untrusted content and are rendered
  through Streamlit's default Markdown path without `unsafe_allow_html`.
- The database and Streamlit UI must be protected at the host/network layer; the
  application provides no authentication.
- Benchmark datasets and `.env` were not modified. `.env` remains git-ignored.
- No package was installed, so the environment was not altered by this work.

---

## 10. Final assessment

**PASS WITH LIMITATIONS**

What was completed and verified:

- Structured report persistence is implemented with additive, backward-compatible
  JSONB/JSON columns; legacy rows load correctly; duplicate and failed writes are
  safe.
- Word count is derived from the final report body everywhere and no longer trusts
  the model.
- Factual support is measured deterministically for open-topic runs, distinguishes
  supported / partial / unsupported / unverifiable claims, and never treats missing
  evidence as falsity.
- README and documentation match the implementation and dependency pins.
- A reproducible mocked performance/reliability harness exists and was executed;
  failures and timeouts are recorded without invented numbers.
- 184 checks passed with 0 failures; the explicit v3 validator passed 29/29; the
  application started and returned a healthy status.
- Existing database rows and benchmark hashes are intact.

What prevents a full production-readiness claim:

- No authentication, authorisation, rate limiting, concurrency, or production load
  testing.
- Evaluation is lexical, not semantic or entailment-based.
- The 40-question benchmark has not been run end-to-end.
- Real Ollama/PostgreSQL performance and production migration behaviour are not
  benchmarked.
- The live performance evidence is a single run, not a controlled A/B.

These are documented limitations, not hidden failures.
