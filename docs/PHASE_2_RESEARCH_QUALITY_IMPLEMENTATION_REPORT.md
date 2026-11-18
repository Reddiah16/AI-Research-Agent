# Phase 2 — Research Quality & Intelligent Agent Improvement Report

**Project:** AI Research Agent  
**Phase:** Phase 2 — research quality, source processing, citation reliability, report generation  
**Date:** 2026-11-13  
**Final status:** **NEEDS ATTENTION**

---

## 1. Executive summary

Phase 2 replaces the title-only research path with a provenance-rich evidence pipeline and hardens report generation and citation handling.

Implemented:

- normalised, deduplicated DuckDuckGo results with snippets preserved;
- bounded, best-effort page fetching with HTML text extraction;
- lexical relevance filtering so empty or unrelated content does not reach the Editor;
- stable source IDs (`SRC-001` …) shared by the Editor prompt, report findings, and grounding;
- structured `key_findings` with source IDs, plus citation sanitisation that separates retrieved from fabricated URLs;
- conservative truncated-JSON repair and a `json_repaired` generation mode;
- an explicit `no_evidence` report instead of forcing the model to invent content;
- claim-citation coverage metrics and unsupported-citation tracking;
- a 26-test standard-library suite covering extraction, evidence, parsing, citations, and failure handling.

Verification results:

- new Phase 2 suite: **26/26 passed**;
- existing Phase 3 dataset suite (regression): **44/44 passed**;
- v3 structural validator (regression): **29/29 passed**;
- all modified Python files compile (`py_compile` exit 0).

The final status is **NEEDS ATTENTION** because the full research pipeline, live search, page fetching, Ollama generation, database persistence, and UI were **not executed** in this environment: every third-party dependency is missing. Behavioural improvements are therefore unit-verified, not live-verified. The benchmark evidence audit also leaves 20 claims unresolved, and `benchmark_v4.json` was deliberately not created.

---

## 2. Architecture changes

### 2.1 Before

```text
DuckDuckGo results
  → top-3 titles concatenated
  → Editor (LLM parametric memory + titles)
  → report
```

Problems: no snippets, no page content, no source IDs, no claim-level citations, and the Editor could only cite URLs after the fact.

### 2.2 After

```text
DuckDuckGo results
  → normalise / validate / deduplicate
  → snippet extraction
  → optional bounded page fetch (HTML → visible text)
  → relevance filter + evidence budget
  → provenance record
      (title, URL, excerpt, snippet/page, fetch status, relevance)
  → global source registry (SRC-001 …)
  → Editor prompt with evidence + source IDs
  → JSON report + key_findings (claim → source_ids)
  → citation sanitisation
      (retrieved vs fabricated URLs)
  → grounding + claim-citation coverage
```

The LangGraph topology is unchanged:

```text
START → planner → researcher → editor → END
```

Only the `GraphState` payload and node internals were extended, so the existing workflow, callbacks, and persistence path remain compatible.

---

## 3. Files created and modified

### 3.1 Created

| File | Purpose |
|---|---|
| `tools/page_fetcher.py` | Bounded HTTP(S) fetching, redirect/host safety, HTML text extraction. |
| `tools/research_evidence.py` | Result normalisation, deduplication, relevance scoring, excerpt selection, provenance, source registry, evidence formatting. |
| `tools/report_parsing.py` | JSON extraction/repair, report-payload validation, citation splitting/sanitisation, markdown parsing. |
| `tests/test_phase2_research_quality.py` | 26 standard-library tests for the new behaviour. |
| `docs/PHASE_2_RESEARCH_QUALITY_IMPLEMENTATION_REPORT.md` | This report. |

### 3.2 Modified in this phase

| File | Change |
|---|---|
| `agents/research_agent.py` | Replaced title-only summaries with evidence assembly, optional page fetching, filtering, provenance, and metrics. |
| `agents/editor_agent.py` | Source-ID prompt, `key_findings`, JSON repair, citation sanitisation, `no_evidence` mode, fallback hardening. |
| `tools/grounding.py` | Added claim-citation coverage and unsupported source-ID analysis; existing API preserved. |
| `generation/llm.py` | Added `JSONGenerationError` carrying raw content for Editor-side repair; strict object parsing. |
| `graph/workflow.py` | Added `source_registry`, `research_metrics`, `claim_citation_coverage`, `unsupported_citations`; passes findings/registry to grounding. |
| `schemas/models.py` | Added optional `key_findings`, `unsupported_citations`, `source_details` to `ResearchReport`. |
| `config/config.py` | Added bounded fetch/evidence configuration fields. |
| `ui/components.py` | Report metadata now shows supported citations, evidence findings, generation mode, and warns about unsupported citations. |

No application pipeline file outside these paths was changed for this phase. `app.py`, `database/database.py`, and `benchmarks/run_benchmark.py` already carried modifications from earlier phases and were left untouched.

---

## 4. Researcher improvements

### 4.1 Result handling

Every DuckDuckGo result is passed through `normalize_results()`:

- rejects non-dictionaries and missing/blank URLs;
- accepts only `http`/`https` URLs;
- strips HTML tags from titles/snippets and collapses whitespace;
- canonicalises URLs (fragment removal, default-port removal, tracking-parameter removal, trailing-slash normalisation);
- deduplicates by canonical URL while preserving first-seen rank.

Malformed results are skipped rather than crashing the node, and the existing `web_search()` exception handling still returns an empty list on failure.

### 4.2 Snippets and page content

For each query:

1. the search snippet is cleaned and excerpted around the query terms;
2. up to `max_pages_per_query` pages are fetched (default 2);
3. fetched text replaces the snippet only when the fetch succeeds and yields usable text;
4. otherwise the snippet is retained with an explicit failure status.

Provenance is recorded per item:

```text
title, url, canonical_url, snippet, excerpt,
retrieval_method = fetched_page | search_snippet,
fetch_status = ok | not_attempted | failed:<reason>,
relevance, content_chars, query
```

### 4.3 Filtering and budget

- Relevance is lexical overlap with the query and topic; items below `min_evidence_relevance` are dropped.
- Items with no usable excerpt are dropped.
- At most `max_evidence_items_per_query` items (default 3) reach the Editor.
- Only `max_pages_per_query` fetch attempts are made per query.
- If nothing survives, the summary is the literal string `No usable evidence was retrieved for this query.` — not fabricated content.

### 4.4 Metrics

`research_metrics` in `GraphState` records:

- `search_results`
- `usable_evidence`
- `filtered_results`
- `fetch_attempts`
- `fetch_failures`
- `registered_sources`

These are in-memory/log metrics. They are **not persisted to PostgreSQL** because no schema migration was added in this phase.

---

## 5. Editor improvements

### 5.1 Evidence-aware prompt

The Editor now receives:

- the topic;
- an evidence block grouped by query;
- stable source IDs and URLs;
- retrieval provenance per item;
- explicit instructions to use only supplied evidence, cite source IDs, never invent URLs, and state insufficiency.

Evidence is labelled as untrusted data so page text is not treated as instructions.

### 5.2 Structured findings

The JSON schema now requests:

```json
"key_findings": [
  {"claim": "...", "source_ids": ["SRC-001"], "support": "..."}
]
```

Findings with unknown source IDs are retained for audit but marked `citation_supported=false` and listed in `unsupported_source_ids`; they are not treated as grounded.

### 5.3 JSON failure handling

`ollama_generate_json()` now raises `JSONGenerationError` with the raw content when strict parsing fails. The Editor then:

1. extracts a fenced or embedded object;
2. applies conservative repair (close an open string and unclosed containers, drop a trailing comma/colon);
3. validates required fields before accepting the result;
4. records `generation_mode="json_repaired"` when repair was used.

If repair or validation fails, the markdown fallback runs. If that also fails, the existing raw failure string is returned and the workflow records `generation_mode="failed"`.

### 5.4 Citation sanitisation

- Only cited URLs that match a retrieved source (canonical comparison) remain in `report.sources`.
- Unretrieved URLs are stored in `unsupported_citations` and are never presented as grounded.
- Markdown fallback citations are extracted and filtered the same way.
- The UI warns when unsupported citations exist.

### 5.5 Generation modes

| Mode | Meaning |
|---|---|
| `json` | Structured JSON parsed on the first pass. |
| `json_repaired` | Truncated/invalid JSON repaired and validated. |
| `markdown_fallback` | Structured path failed; markdown parsed. |
| `no_evidence` | No usable evidence; honest insufficiency report. |
| `failed` | All generation paths failed (workflow-level). |

`generation_mode` continues to flow into the existing `editor_generation_mode` database column through the unchanged workflow/app path.

---

## 6. Source-grounding improvements

`analyze_source_grounding()` retains its existing outputs and adds:

- `claim_citation_coverage` — share of findings with at least one valid registry source ID;
- `findings_without_citations` — findings with no source IDs;
- `unsupported_claim_source_ids` — IDs not present in the registry.

The workflow now passes `key_findings` and `source_registry` into grounding and returns:

- `claim_citation_coverage`;
- `unsupported_citations` — union of model-reported unsupported URLs and phantom URLs found in the report text.

Existing retrieval metrics remain:

- `grounded_sources_count`
- `citation_grounding_ratio`
- `ungrounded_retrieved_urls`
- `phantom_urls`

**Methodological boundary:** URL presence proves citation alignment only. It does not prove factual correctness, claim truth, or source authority. The code and report keep that distinction explicit.

---

## 7. Benchmark evidence audit

### 7.1 Decision: no `benchmark_v4.json`

`benchmark_v3.json` was audited read-only. No new source was fetched and no source snapshot exists in the repository. The 20 unresolved claims already carry gap statements and suggested corrections in v3. Applying those corrections without new independent evidence would only re-assert the same record and risk forcing unresolved claims into `verified`.

Therefore:

- **`benchmark_v4.json` was not created.**
- v1, v2, and v3 remain byte-for-byte unchanged.
- Historical hashes remain:
  - v1 `4f1cb7f5ca19e1c58e10de714844fd042ffdd70ac4f8321e80046a6cd52573ec`
  - v2 `5f859b81be45b787b5a6108378a3b9abe0584e4697ad3d8def0f65a49a5b779f`
  - v3 `10a5ba16c400a1cac0040e4357df49d97fc09e258f006999df96c93df2ff2527`

### 7.2 Unresolved claims (20)

| Category | Claims | Why still unresolved |
|---|---|---|
| Numeric/threshold overreach | Q27-C1, Q32-C1, Q38-C2 | The specific `>100 Gbps`, `<1 ms`, and `4x/8x` figures are not in the inspected records. |
| Absolute/universal wording | Q25-C3, Q33-C2, Q40-C2 | “Private fusion companies”, “modern runtimes”, and “without coding custom plugins” exceed the inspected sources. |
| Comparative/performance breadth | Q07-C3, Q20-C3, Q31-C1, Q31-C3 | Stability, product boundaries, GC tail latency, and proxy throughput are context- or benchmark-dependent. |
| Source-scope generalisation | Q09-C3, Q17-C1, Q17-C3, Q24-C3, Q30-C2, Q30-C3, Q25-C3 | Claims generalise beyond the single inspected source or integration. |
| Missing technical evidence | Q22-C2, Q38-C3 | Vamana/single-layer and sub-micron pitch details are absent. |
| Needs a new primary source | **Q17-C2**, **Q29-C2** | APR patch synthesis and REST mechanics are not documented by the cited sources. |

None was promoted. All retain `exact_wording_met=false`, a gap, and a suggested correction.

### 7.3 Q17-C2

- Status: `needs_more_evidence`.
- Only cited source: S036 (Google Project Zero, Big Sleep).
- The source documents vulnerability discovery and a crashing testcase, not automated patch synthesis or validation for memory-safety/logic vulnerabilities.
- Required next step: cite a specific APR system/paper or soften the claim; neither was possible offline.

### 7.4 Q29-C2

- Status: `needs_more_evidence` (downgraded during v3).
- Cited sources: S059, S060 (gRPC/GraphQL material).
- Neither documents REST verbs, JSON/XML payloads, HTTP caching, or payload overhead.
- Required next step: add an authoritative REST/HTTP primary source or narrow the claim to the gRPC/GraphQL comparison.

### 7.5 Weak source S053

- `insufficiently_verified`: no authors, year-only date (`2026`), no version/DOI.
- It is corroborating context only and is not load-bearing for any verified claim.
- Q24-C2 now relies on S052; Q24-C3 remains `partially_supported` and still lists S053 as secondary.
- Full independent confirmation requires the live paper or a snapshot, neither available here.

### 7.6 Source-domain mismatches

The frozen expected-domain lists were not rewritten. v3 records:

- **39 claims** across **20 questions** cite at least one outside domain;
- **18 distinct outside domains**;
- **21 documented substitution records**.

Classification counts:

| Classification | Count |
|---|---:|
| canonical alias | 4 |
| official project repository | 3 |
| primary technical source | 3 |
| official project domain | 2 |
| vendor primary authority | 2 |
| government authority substitution | 1 |
| official specification site | 1 |
| scientific authority substitution | 1 |
| official operator domain | 1 |
| specialist corroborating source | 1 |
| canonical pattern catalogue | 1 |
| official project documentation | 1 |

The audit is internally consistent, but it documents substitutions rather than proving each substitute is the best authority. No new domains were added in this phase.

---

## 8. Tests executed and exact results

### 8.1 New Phase 2 suite

```text
python tests\test_phase2_research_quality.py
Ran 26 tests
OK
RESULT: PASS
exit code 0
```

Coverage:

| Area | Tests |
|---|---:|
| HTML extraction, malformed HTML, URL safety, content-type rejection, network failure | 6 |
| Normalisation, deduplication, canonicalisation, relevance, excerpts, provenance, fetch fallback, budgets, empty filtering, registry, context formatting | 10 |
| JSON fences/embedded objects, truncated repair, citation filtering, required fields, markdown parsing, URL safety | 7 |
| Grounding counts, claim coverage, unsupported IDs, end-to-end evidence → payload → grounding | 3 |

### 8.2 Regression suites

```text
python tests\test_phase3_dataset_verification.py
Tests: 44 passed, 0 failed
RESULT: PASS

python benchmarks\validate_dataset.py --dataset benchmarks\datasets\benchmark_v3.json
Checks: 29 passed, 0 failed
RESULT: PASS
```

### 8.3 Syntax check

```text
python -m py_compile <all modified/new Python files>
exit 0
```

### 8.4 Tests not executed

The environment has none of the third-party packages installed:

```text
sqlalchemy MISSING
streamlit MISSING
pandas MISSING
altair MISSING
ddgs MISSING
ollama MISSING
langgraph MISSING
pydantic MISSING
dotenv MISSING
psycopg2 MISSING
```

Consequently these could not run:

| Test | Blocking error |
|---|---|
| `tests/test_web_search.py` | `ModuleNotFoundError: No module named 'ddgs'` |
| `tests/test_analytics_dashboard.py` | `ModuleNotFoundError: No module named 'sqlalchemy'` |
| `tests/test_metrics_persistence.py` | `ModuleNotFoundError: No module named 'sqlalchemy'` |
| `tests/test_performance_tracking.py` | `ModuleNotFoundError: No module named 'sqlalchemy'` |
| `tests/test_phase2_instrumentation.py` | `ModuleNotFoundError: No module named 'sqlalchemy'` |

No package was installed. `tests/test_db_postgres.py` was not run and no database was contacted.

### 8.5 Regression risk

The new modules are standard-library only. The wired application paths (`research_agent`, `editor_agent`, `workflow`, `llm`, `models`) compile but could not be integration-tested without dependencies. Existing tests that mock those paths were not run.

---

## 9. Performance comparisons

### 9.1 Historical baseline (measured previously, not re-run)

From `docs/PHASE_1_RESEARCH_AGENT_AUDIT_REPORT.md`:

- 18 runs, 13 timed;
- mean latency 127.85 s;
- min 97.53 s, max 222.65 s, P95 221.72 s;
- 159 unique URLs across 18 sessions;
- mean 8.83 unique sources/session.

### 9.2 Synthetic evidence-processing measurement (measured now)

Fixture: 20 synthetic results, 20 repeated 56-character sentences per snippet, 3 retained items, 200 iterations.

| Metric | Title-only baseline | Evidence pipeline |
|---|---:|---:|
| Summary characters | 32 | 833 |
| Includes URL | No | Yes |
| Includes snippet text | No | Yes |
| Includes provenance label | No | Yes |
| Mean processing time | 0.002 ms | 1.446 ms |

This measures deterministic local processing only. It shows the new path adds about 1.4 ms per query on this fixture while carrying far more evidence. It is **not** an end-to-end latency measurement and does not include network search, page fetching, or LLM generation.

### 9.3 Proposed targets (not measured)

| Target | Status |
|---|---|
| Page fetch adds no more than 2 requests/query | Implemented as a budget; live latency NOT MEASURED |
| Fetch timeout bounded at 6 s | Implemented; live behaviour NOT MEASURED |
| Editor receives snippets/page excerpts, not titles | Unit-verified |
| Unsupported URLs excluded from grounded sources | Unit-verified |
| Overall pipeline faster or slower than baseline | NOT MEASURED |

No claim of end-to-end speed-up is made. Page fetching is expected to add latency when enabled.

---

## 10. Remaining limitations

1. **No live execution.** Search, fetch, Ollama, database, and UI were not run. All behavioural claims beyond unit tests are unverified.
2. **Dependencies absent.** The full stack cannot be exercised in this environment without installing packages, which was not authorised.
3. **Page fetching is best-effort, not comprehensive.** JavaScript-rendered pages, paywalls, logins, and bot protection are not handled.
4. **`robots.txt` is not enforced.** The fetcher allows public HTTP(S) pages and blocks local/private hosts, but operators must ensure crawl permission for their use case.
5. **Prompt injection risk remains.** Evidence is labelled untrusted, but page text can still contain adversarial instructions; no classifier or sandbox guarantees immunity.
6. **Lexical relevance is not semantic relevance.** A topically matching passage can still be useless or misleading.
7. **Citation alignment is not factual verification.** A retrieved URL can be cited for a claim it does not support.
8. **New metrics are not persisted.** `research_metrics`, `claim_citation_coverage`, and `unsupported_citations` are in state/logs only; no database migration was added.
9. **Benchmark residual.** 20 claims remain unresolved and no v4 was created.
10. **No source snapshots.** Benchmark evidence could not be re-inspected against live pages.

---

## 11. Security and reliability considerations

### 11.1 Fetch safety

- only `http`/`https`;
- loopback, private, link-local, multicast, reserved, and unspecified literal IPs blocked;
- `localhost` and `.local` blocked;
- redirects revalidated by a custom redirect handler;
- 6 s default timeout and 500 KB byte cap;
- content-type allowlist (HTML/XHTML/text/markdown/JSON);
- scripts, styles, noscript, templates, SVG, canvas, iframes, forms, nav, and footer text discarded;
- no JavaScript execution, no file writes, no credential handling.

Known gap: DNS rebinding and hostnames that resolve to private addresses are not fully mitigated because no DNS check is performed (to avoid extra network calls).

### 11.2 Generation reliability

- strict JSON first, conservative repair second, markdown fallback third;
- required fields validated before acceptance;
- empty/failed generation never silently becomes a “successful” factual report;
- no-evidence topics produce an explicit insufficiency report;
- generation mode is recorded for observability.

### 11.3 Citation reliability

- fabricated URLs are separated from retrieved URLs;
- findings without valid source IDs are marked unsupported;
- phantom URLs are surfaced in telemetry;
- URL presence is never described as factual proof.

### 11.4 Data handling

- no database connections or resets;
- no benchmark dataset mutation;
- no staging, commit, or push;
- existing modified and untracked files preserved.

---

## 12. Final status

### NEEDS ATTENTION

**Implementation:** complete for the code and unit-test scope.  
**Unit verification:** PASS (26/26 new, 44/44 regression, 29/29 validator).  
**Live behavioural verification:** NOT PERFORMED — dependencies, database, network, Ollama, and UI were unavailable or prohibited.  
**Benchmark evidence:** 20 claims remain unresolved; no v4 was created because no new evidence was available.

Phase 2 materially improves what reaches the Editor, how sources are tracked, and how generation failures are handled. It should not be reported as a fully validated production improvement until the pipeline is run in a provisioned environment with live search, page fetching, Ollama, and database persistence.
