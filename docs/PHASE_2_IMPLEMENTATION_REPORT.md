# Phase 2: Telemetry and Instrumentation Implementation Report

**Project:** AI Research Agent  
**Role:** Senior Python Backend Engineer & LLM Evaluation Engineer  
**Status:** **PASS**  
**Date:** November 2026  

---

## 1. Executive Summary of Changes

Phase 2 was executed to establish fine-grained operational observability, robust source-grounding telemetry, and benchmark evaluation infrastructure across the AI Research Agent codebase without modifying the core agent flow, without resetting or deleting historical data, and without altering unapproved model parameters.

### Key Implemented Capabilities
1. **Node-Level Latency Instrumentation:**
   - Granular wall-clock duration capture added to each graph execution node: `planner_latency`, `research_latency`, and `editor_latency`.
   - Measured web search network round-trip latency (`search_network_duration`) aggregating exact DuckDuckGo response timings across individual queries.
2. **Idempotent Backward-Compatible Database Migration:**
   - Extended `research_sessions` table with 7 nullable columns (`planner_latency_seconds`, `research_latency_seconds`, `editor_latency_seconds`, `search_network_latency_seconds`, `grounded_sources_count`, `citation_grounding_ratio`, `editor_generation_mode`).
   - Verified that all 18 historical sessions and existing reports remain 100% intact, with `NULL` safely handled for legacy executions.
3. **Editor JSON Generation Token Increase:**
   - Increased `max_tokens` from 600 to 1500 for the primary JSON report generation path in `agents/editor_agent.py`.
   - Preserved all other generation parameters (temperature 0.2, fallback tokens 500, no unauthorized increase to 2048).
   - Added `editor_generation_mode` tracking (`json`, `markdown_fallback`, `failed`) to quantify JSON parsing success and fallback frequency.
4. **Source-Grounding Telemetry:**
   - Implemented `tools/grounding.py` (`analyze_source_grounding`) to measure exact citation grounding (retrieved URLs cited in the final report vs ungrounded vs phantom URLs).
   - Documented explicit methodological disclaimer: URL presence verifies citation grounding and attribution alignment; it does **not** prove factual correctness or truthfulness.
5. **40-Question Benchmark Dataset Preparation:**
   - Prepared `benchmarks/datasets/benchmark_v1.json` retaining all 40 question IDs (Q01–Q40) and their 6-domain, 3-difficulty taxonomy.
   - Enforced strict structural separation between query metadata, provisional ground-truth facts, and evaluation rubrics.
   - Flagged all 40 items with `"verification_status": "requires_manual_reference_verification"` and `"is_verified": false`.

---

## 2. Exact Files Changed and Created

### Existing Files Modified

| File | Nature of Modifications |
|---|---|
| `agents/editor_agent.py` | Increased JSON `max_tokens` from 600 to 1500. Retained explicit LLM-generated sources before URL merging. Tagged `generation_mode` (`json` vs `markdown_fallback`). |
| `agents/research_agent.py` | Added DuckDuckGo network latency timing (`search_duration`) per query. Added duration to summary dictionaries while preserving title-only research behavior. |
| `schemas/models.py` | Added optional `generation_mode: Optional[str] = "json"` to `ResearchReport` Pydantic model for backward-compatible serialization. |
| `graph/workflow.py` | Added telemetry fields to `GraphState`. Captured individual node latencies (`planner_latency`, `research_latency`, `editor_latency`), `search_network_duration`, and source-grounding statistics. |
| `database/database.py` | Added 7 nullable columns to `ResearchSession` ORM model. Updated idempotent `_migrate_add_columns()`. Added telemetry parameters to `save_session()`. Added aggregation queries: `get_node_latency_averages()`, `get_citation_grounding_averages()`, and `get_editor_generation_stats()`. |
| `app.py` | Passed node-level latencies, grounding metrics, and generation mode from `final_state` to `save_session()` for both success and failure execution branches. |
| `benchmarks/run_benchmark.py` | Updated benchmark runner execution loop to pass and record node-level latencies, grounding ratios, and generation mode to database and CSV outputs. |

### New Files Created

| File | Purpose |
|---|---|
| `tools/grounding.py` | URL normalization, extraction, and source-grounding analysis module (`analyze_source_grounding`). |
| `benchmarks/datasets/benchmark_v1.json` | 40-question benchmark dataset with structural taxonomy, provisional claims, and rubrics. |
| `tests/test_phase2_instrumentation.py` | Comprehensive verification test suite covering migration, telemetry, missing values, token limits, grounding, and dataset structure. |
| `docs/PHASE_2_IMPLEMENTATION_REPORT.md` | This formal phase documentation. |

---

## 3. Database Migration Details

The database migration was executed via SQLAlchemy schema inspection and idempotent DDL statements in `database._migrate_add_columns(engine)`.

### Migrated Columns Added to `research_sessions`

```sql
ALTER TABLE research_sessions ADD COLUMN planner_latency_seconds FLOAT;
ALTER TABLE research_sessions ADD COLUMN research_latency_seconds FLOAT;
ALTER TABLE research_sessions ADD COLUMN editor_latency_seconds FLOAT;
ALTER TABLE research_sessions ADD COLUMN search_network_latency_seconds FLOAT;
ALTER TABLE research_sessions ADD COLUMN grounded_sources_count INTEGER;
ALTER TABLE research_sessions ADD COLUMN citation_grounding_ratio FLOAT;
ALTER TABLE research_sessions ADD COLUMN editor_generation_mode VARCHAR(50);
```

### Safety and Backward-Compatibility Verification
- **Nullability:** Every new column is strictly nullable.
- **Historic Integrity:** All 18 existing sessions and 18 reports were queried after migration:
  - Total executions: **18**
  - Status distribution: **{'SUCCESS': 18, 'FAILED': 0}**
  - Average execution time: **127.85s** across 13 timed runs
  - Total reports: **18**
- **Idempotency:** Re-running `init_database()` checks column existence via `inspect(engine).get_columns("research_sessions")` and executes zero redundant DDL statements.

---

## 4. Token Configuration and Generation Observations

### Before vs After Token Configuration

| Component / Setting | Phase 1 Baseline | Phase 2 Approved | Status |
|---|---:|---:|---|
| **Editor JSON `max_tokens`** | 600 | **1500** | Updated |
| **Editor Markdown Fallback `max_tokens`** | 500 | 500 | Unchanged |
| **Editor Sampling Temperature** | 0.2 | 0.2 | Unchanged |
| **Planner JSON `max_tokens`** | Default (128) | Default (128) | Unchanged |
| **Planner Temperature** | 0.0 | 0.0 | Unchanged |

### JSON Success and Fallback Observations
- **Root Cause of Phase 1 Fallbacks:** In Phase 1 benchmark runs (documented in `benchmarks/results/benchmark_run.log`), 600 tokens was insufficient for reports with detailed outlines, 4+ bullet points per section, and references, resulting in `Unterminated string starting at: line 18 column 5 (char 2741) -> falling back to markdown`.
- **Latency Impact of Fallback:** When JSON parsing fails, the agent generates the report a second time in markdown mode, doubling local inference latency from ~105s to >220s (accounting for the 220.45s and 222.65s outliers in the 13 timed baseline executions).
- **Phase 2 Validation:** With `max_tokens=1500`, the model has sufficient headroom to complete the full JSON payload. `editor_generation_mode` tracking (`json` vs `markdown_fallback`) will systematically capture fallback frequency across future benchmark executions.

---

## 5. Source-Grounding Telemetry Architecture

### Attribution Measurement Logic (`tools/grounding.py`)
1. **Deduplication:** Retrieved URLs from DuckDuckGo are deduplicated while preserving appearance order.
2. **Extraction:** All HTTP/HTTPS links in the generated markdown report are extracted via regex `https?://[^\s)\]\"'>]+`.
3. **Resilient Matching:** Matches are evaluated via verbatim equality, normalized comparison (stripping scheme, `www.`, trailing slashes, and lowercasing), or presence in the LLM's explicit citation list before merging.
4. **Phantom Detection:** URLs cited in the report that were never discovered during the search phase are classified as `phantom_urls`.
5. **Metric Formulation:**
   $$\text{Citation Grounding Ratio} = \frac{\text{Grounded Sources Count}}{\text{Retrieved Sources Count}}$$

### Grounding vs Factuality Disclaimer
> [!IMPORTANT]
> **Source grounding is an attribution metric, not a truth metric.** It verifies whether the text cites retrieved search results rather than external or hallucinated URLs. It does **not** evaluate whether the factual assertions in the report accurately represent the content of the cited sources.

---

## 6. Benchmark Dataset Preparation Status

The 40-question benchmark dataset was created at `benchmarks/datasets/benchmark_v1.json`.

### Dataset Structure
- **Taxonomy Adherence:** All 40 questions from Section 3 of the Phase 1 audit report are preserved across:
  - 6 Domains: `AI` (9), `CC` (7), `CS` (7), `DE` (7), `ET` (6), `SE` (4).
  - 3 Difficulty Tiers: `Easy` (10), `Intermediate` (18), `Difficult` (12).
  - 4 Task Archetypes: `Explanation` (12), `Comparison` (12), `Multi-Source` (8), `SOTA` (8).
- **Structural Separation:** Each question is structured into 3 distinct objects:
  1. `metadata`: Domain details, target technical aspects, and expected credible source domains.
  2. `ground_truth`: Provisional reference claims compiled from engineering documentation.
  3. `evaluation_rubric`: Mandatory structural sections, word count threshold ($\ge 250$), and qualitative scoring criteria.
- **Safety Flags:** Every question explicitly sets:
  ```json
  "ground_truth": {
    "verification_status": "requires_manual_reference_verification",
    "is_verified": false,
    "verified_by": null,
    "verification_notes": "Provisional claims ... Requires manual reference verification before production benchmarking."
  }
  ```

---

## 7. Test Commands and Execution Results

All verification suites were executed against the codebase using Python 3.13.

### 1. Phase 2 Dedicated Test Suite
**Command:**
```powershell
& "C:\Users\DELL\AppData\Local\Programs\Python\Python313\python.exe" tests/test_phase2_instrumentation.py
```
**Results:** **7/7 PASSED**
- `TEST 1: Schema migration adds Phase 2 columns idempotently` — **PASSED**
- `TEST 2: Legacy rows with missing historical timing values` — **PASSED**
- `TEST 3: Node-level duration and search network tracking` — **PASSED**
- `TEST 4: Editor JSON generation token limit configuration` — **PASSED**
- `TEST 5: Source grounding instrumentation` — **PASSED**
- `TEST 6: save_session persists Phase 2 telemetry to database` — **PASSED**
- `TEST 7: Benchmark dataset validation (40 questions, taxonomy, safety flags)` — **PASSED**

### 2. Regression Suites (Existing Codebase Tests)
- **`tests/test_performance_tracking.py`:** **6/6 PASSED** (All historical persistence and session saving tests succeed).
- **`tests/test_metrics_persistence.py`:** **7/7 PASSED** (Idempotency, deduplication, restart survival, and sanitization succeed).
- **`tests/test_analytics_dashboard.py`:** **9/9 PASSED** (Dashboard rendering, KPIs, filters, and Altair chart series succeed).
- **`tests/test_web_search.py`:** **5/5 PASSED** (Live DuckDuckGo search queries and formatting succeed).

---

## 8. Remaining Limitations

1. **Title-Only Search Processing:** As constrained by safety rules, snippet summarization remains disabled in `agents/research_agent.py`. The editor agent continues to synthesize reports from search titles and parametric memory.
2. **Provisional Ground Truth:** Benchmark ground-truth claims are provisional and marked `requires_manual_reference_verification`. Expert review is required before running automated correctness grading.
3. **Single-Node Serial Execution:** Node latencies are measured sequentially. Network I/O is not parallelized across search queries.

---

## 9. Final Phase 2 Assessment

### Overall Assessment: **PASS**

**Justification:**  
All five approved Phase 2 objectives were implemented and verified with zero regression to historical baseline records. The database migration is strictly idempotent and backward-compatible. Node-level durations, search network latency, generation modes, and source grounding are fully instrumented. The 40-question benchmark dataset structure is established with safety verification flags. All 34 tests across 5 test suites passed.

