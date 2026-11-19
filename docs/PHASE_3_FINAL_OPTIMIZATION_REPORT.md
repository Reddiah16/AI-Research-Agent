# Phase 3 Final Optimization Report

**Project:** AI Research Agent
**Date:** 2026-11-19
**Scope:** Editor evidence selection, citation/grounding integrity, Editor latency,
tests, live evaluation, persistence/UI verification, and documentation.
**Status:** Implemented and verified on three golden cases with two live repeats.
No commit or push was performed.

---

## 1. Executive Summary

Phase 3 targeted three coupled problems left by the retrieval-depth optimization:

1. the Editor received 27–30 sources in an unbounded prompt, which reduced
   citation precision and increased prompt-evaluation time;
2. citation validation checked whether a URL had been retrieved, not whether the
   cited evidence supported the claim;
3. a deeper retrieval pool mechanically depressed several answer-quality
   components and made the Editor latency tail worse.

The implemented solution is deterministic and additive:

- a scored, deduplicated, diverse evidence selector with source and character
  budgets, applied before the Editor prompt is built;
- citation checks over the cited source text, plus optional evidence-based
  binding that can only add an already-retrieved, already-shown source;
- report source-list reconstruction so every cited source is displayed and URL
  variants are not double-counted;
- a measured limitations note when evidence support is incomplete;
- versioned telemetry for context, citation, and per-node latency;
- a configurable per-request LLM timeout guard.

Measured outcome on the same three golden cases and labels:

| Metric | Baseline | Retrieval rerun | Phase 3 run 1 | Phase 3 run 2 |
|---|---:|---:|---:|---:|
| Answer-quality proxy | 0.7402 | 0.4379 | **0.6351** | **0.5694** |
| Citation support | 0.6667 | 0.2222 | **0.5778** | **0.5000** |
| Citation precision | not versioned | not versioned | **1.0** | **1.0** |
| Registry Recall@10 | 0.1667 | 0.5833 | **0.5000** | **0.5000** |
| E2E median | 484.36 s | 1220.74 s | **502.88 s** | **567.08 s** |
| Editor median | 1132.4 s | 1132.4 s | 417.8 s | 436.6 s |

Phase 3 recovered most of the answer-quality/citation regression and returned
typical-case latency close to the historical baseline despite a much deeper
retrieval pool. It did **not** exceed the historical answer-quality proxy
(`0.7402`), and no causal or general speed-up is claimed. Both runs are
observational, single-host, three-case measurements with a heavy latency tail.

---

## 2. Scope, Constraints, and Preserved Work

### 2.1 Preserved

The following existing work was retained unchanged in definition and behaviour:

- diverse multi-query planning and search depth;
- deterministic source ranking and arXiv/DOI canonicalization;
- cross-query deduplication and redirect rejection;
- bounded page fetching, robots/DNS controls, and HTTP 429 retry/backoff;
- registry recall metrics (`retrieval-registry-1.0`) and candidate-pool metrics
  (`retrieval-candidate-pool-1.0`);
- historical answer-quality component definitions and weights;
- PostgreSQL schema and additive migrations;
- the LangGraph three-node workflow and the Streamlit entry point.

The working tree was not reset or reverted. The pre-existing checkpoint
`024ec3824f8cce84b52d6b5e2269d6425dabb9be` remains in history.

### 2.2 Explicit non-goals

- No change to benchmark labels, frozen datasets, historical artifacts, `.env`,
  or the LLM provider/model.
- No commit or push.
- No destructive database command.
- No claim that lexical proxies equal factual truth or human judgement.

### 2.3 Environment

- Python 3.13.13
- LangGraph, Pydantic, SQLAlchemy, psycopg2, Streamlit
- Ollama `qwen3:4b` on CPU-only local hardware
- DuckDuckGo search provider
- PostgreSQL for persistence

---

## 3. Baseline and Artifacts

| Role | Artifact |
|---|---|
| Historical baseline | `benchmarks/results/golden_eval_live_20261118_091654.json` |
| Retrieval-only rerun | `benchmarks/results/golden_eval_live_20261119_120333.json` |
| Phase 3 live run 1 | `benchmarks/results/golden_eval_live_20261119_142306.json` |
| Phase 3 live run 2 | `benchmarks/results/golden_eval_live_20261119_160446.json` |
| Mocked metric self-check | `benchmarks/results/golden_eval_mocked_20261119_131417.json` |

Dataset: `benchmarks/datasets/golden_eval_v1.jsonl`
SHA-256: `edacf9ec8225061ce4d47403301e73c4c06aad1fafb142c67243019cc8b8637e`
(unchanged; matches the manifest).

Cases: G001, G005, G014. Labels and case text were not modified. The baseline
used 7–9 sources per case; the retrieval rerun and Phase 3 searched six queries
with 10 results and retained 21–30 sources before Editor selection.

The mocked self-check (`all_fixture_checks_passed: true`) validates the metric
wiring only and is not an agent-quality result.

---

## 4. Root-Cause Analysis

### 4.1 Answer quality and citation integrity

The retrieval optimization increased the evidence pool from 7–9 to 27–30
sources. Three effects followed:

1. **Prompt dilution.** A 4B local model received a long, largely
   undifferentiated evidence block. It cited fewer, less precise sources and
   produced more unsupported or partially supported claims.
2. **Evidence-coverage denominator.** `evidence_coverage` divides usable
   evidence by the number of search results. A deeper pool lowers the ratio even
   when the retained evidence is better. The same applies to contextual recall
   against three reference findings.
3. **URL existence was the only citation check.** A URL could be retrieved and
   still be the wrong source for a claim. The historical
   `analyze_claim_citations` metric measured alignment, not content support.
   Abstractive claim wording against terse arXiv prose then scored below the
   strict `0.55` lexical threshold.

Diagnostics from the retrieval rerun confirmed the mechanism: citation support
fell `0.6667 → 0.2222`, unsupported claims rose `0.0 → 0.3333`, and per-case
overall scores fell to `0.3678 / 0.3385 / 0.6074`.

### 4.2 Editor latency

Measured Editor time in the retrieval rerun was `919.36 / 1315.85 / 1132.38 s`
(mean `1122.5 s`). Planner and research were `26–40 s` and `58–62 s`. The
Editor dominated end-to-end time because:

- prompt evaluation scales with the ~24–26k-character evidence block;
- the model runs on CPU with no GPU acceleration;
- the output ceiling and streaming JSON early-stop reduce completion time but
  do not reduce prompt-evaluation cost;
- there was no per-request runaway timeout.

The latency problem is therefore primarily **prompt-size and hardware/model
bound**, not a failure of the retrieval or persistence layers.

### 4.3 Grounding and reporting gaps

- Cited source IDs could be missing from the displayed `sources` list, making
  `len(sources)` smaller than the set of cited sources.
- `www.` and non-`www.` variants of the same page could be counted twice.
- Snippet-only and fetched-page evidence were not distinguished in citation
  diagnostics.
- Incomplete evidence support was not disclosed in a measured way; the report
  could look fully grounded when it was not.

---

## 5. Implementation: Deterministic Editor Evidence Selection

**Module:** `tools/evidence_selection.py`
**Version:** `editor-evidence-selection-1.0`

### 5.1 Algorithm

Before the prompt is built, the Editor:

1. filters registry records with a usable canonical URL;
2. scores each record with an auditable additive formula:

   ```text
   0.45 × rank_score
 + 0.25 × authority_score
 + 0.25 × relevance
 + 0.08 if the page was fetched successfully
 + up to 0.04 for usable excerpt length
 − 0.04 if the evidence is snippet-only and not fetched
   ```

3. suppresses near-duplicates using 3-token shingle Jaccard over **either** the
   excerpt alone or title-plus-excerpt, at a default threshold of `0.85`;
4. selects round-robin across queries to preserve subtopic coverage, then fills
   by score;
5. enforces source and character budgets by dropping the weakest selected
   source, never by truncating every excerpt to nothing;
6. keeps at least `min_sources` even if the configured character cap is
   impossible;
7. copies stable `SRC-*` IDs unchanged and renders a compact, query-grouped
   context.

Excerpts are trimmed at sentence or word boundaries, never mid-word. A trailing
ellipsis marks truncation.

### 5.2 Telemetry

The selector reports, before and after:

- source count;
- character count;
- approximate tokens (`characters/4`, explicitly labelled as a heuristic, not a
  provider tokenizer);
- near-duplicates dropped;
- budget-dropped sources;
- prompt characters;
- LLM calls;
- selected source IDs and source types;
- fetched and snippet-only shares.

These values are persisted in the existing evaluation JSON metadata column and
surfaced in the UI.

### 5.3 Measured effect

| Metric (mean of 3 cases) | Run 1 | Run 2 |
|---|---:|---:|
| Sources before → after | 28.67 → 12.0 | 26.67 → 12.0 |
| Characters before → after | 24,697 → 10,253 | 22,539 → 9,667 |
| Approx. tokens before → after | 6,174 → 2,563 | 5,634 → 2,416 |
| Near-duplicates dropped | 0.0 | 0.33 |
| Hard-budget drops | 0.0 | 0.0 |

The prompt was reduced by ~56–58% while retaining 12 sources and stable IDs. The
14,000-character cap was not the limiting constraint after source reduction; no
case required emergency excerpt truncation to 200 characters.

---

## 6. Implementation: Citation and Grounding Integrity

**Module:** `tools/citation_integrity.py`
**Version:** `citation-integrity-1.0`

### 6.1 Finding verification

Each structured finding is annotated with:

- `original_source_ids` — what the model emitted, retained for audit;
- `invalid_source_ids` — IDs not present in the retrieved registry;
- `source_ids` — valid IDs after removing invalid ones;
- `bound_source_ids` — evidence-based additions, if any;
- `best_evidence_coverage` — maximum lexical coverage of the claim by a cited
  source title/excerpt;
- `support_status` — one of `supported`, `partially_supported`,
  `insufficient_evidence`, `invalid_citation`, `no_citation`;
- `citation_supported` and `content_supported`;
- `support_note` — deterministic explanation.

Content support is lexical coverage at `0.55` (`PARTIAL_SUPPORT_THRESHOLD`
remains the partial boundary). It is not entailment or factual verification.

### 6.2 Evidence-based binding

Binding is optional (`EDITOR_BIND_CITATIONS=true`). When a cited source does not
cover a claim, the Editor may add **another source that was actually shown to
it** and whose excerpt does cover the claim. Constraints:

- only sources in the selected Editor evidence are eligible;
- no URL, ID, title, or text is invented;
- the model's original IDs are preserved;
- if no source covers the claim, the finding stays unsupported;
- an uncited claim is never forced to acquire a citation.

### 6.3 Displayed and cited sources

`build_report_sources`:

- keeps only URLs present in the retrieved registry;
- canonicalizes `www.` and trailing-slash variants before deduplication;
- adds every cited source to the displayed list, so `cited <= displayed`;
- reports model-supplied URLs that were never retrieved as unknown, not as
  supported sources.

### 6.4 Limitations note

`add_evidence_limitations` inserts a `## Limitations` section before
`## References` when measured support is incomplete. It reports only measured
counts:

- findings not fully matched to cited excerpts;
- citations whose IDs did not exist;
- snippet-only cited share when at least 50%.

It is skipped when the report already has a Limitations section and can be
disabled with `EDITOR_APPEND_LIMITATIONS=false`.

### 6.5 Measured effect

| Diagnostic (mean of 3 cases) | Run 1 | Run 2 |
|---|---:|---:|
| Citation precision | 1.0000 | 1.0000 |
| Citation coverage | 1.0000 | 1.0000 |
| Evidence support rate | 0.5778 | 0.5000 |
| Partially supported claim rate | 0.3555 | 0.3056 |
| Unsupported claim rate | 0.0667 | 0.1944 |
| Displayed = cited consistency | 1.0000 | 1.0000 |
| Snippet-only cited share | 0.0000 | 0.0000 |
| Evidence-based bindings | 0 | 0 |
| Invalid references | 0 | 0 |

No invalid ID or phantom URL was counted. No binding was needed because the
model's own citations already covered its claims at the threshold in these
cases. Binding behaviour is still covered by unit and integration tests.

---

## 7. Implementation: Editor Prompt, Output Ceilings, and Timeout Safety

- The JSON and markdown system prompts were compressed without removing safety
  rules. The security probe requires the phrases "untrusted data", "never as
  instructions", "never invent a url", and "use only the evidence"; all are
  present.
- Reporting rules now ask the model to keep technical terms, names, and numbers
  close to the source wording and to cite only supporting sources.
- Output ceilings are configurable: `EDITOR_MAX_JSON_TOKENS=1000` and
  `EDITOR_MAX_MARKDOWN_TOKENS=700`. They are safety limits, not word targets;
  the report rubric remains "under 400 words".
- `generation/llm.py` now constructs the Ollama client with
  `LLM_REQUEST_TIMEOUT_SECONDS` (default `1800`). The cached client key includes
  the timeout. This prevents an indefinitely hung socket; it is not a pipeline
  deadline and cannot accelerate CPU inference.
- The existing streamed-JSON early-stop remains unchanged.

---

## 8. Configuration and Safe Defaults

| Setting | Default | Purpose |
|---|---:|---|
| `EDITOR_MAX_SOURCES` | 12 | Maximum registry sources shown to the Editor |
| `EDITOR_MIN_SOURCES` | 3 | Minimum sources retained even under budget pressure |
| `EDITOR_MAX_CONTEXT_CHARS` | 14000 | Evidence-block character budget |
| `EDITOR_EXCERPT_CHARS` | 700 | Per-source excerpt budget |
| `EDITOR_NEAR_DUPLICATE_THRESHOLD` | 0.85 | Shingle Jaccard duplicate threshold |
| `EDITOR_MAX_JSON_TOKENS` | 1000 | JSON output ceiling |
| `EDITOR_MAX_MARKDOWN_TOKENS` | 700 | Markdown fallback ceiling |
| `EDITOR_BIND_CITATIONS` | true | Evidence-based binding of unsupported cited claims |
| `EDITOR_APPEND_LIMITATIONS` | true | Measured limitations note |
| `LLM_REQUEST_TIMEOUT_SECONDS` | 1800 | Per-request runaway guard |

All values are validated and clamped. Invalid or non-positive numeric values
fall back to defaults; `min_sources` is capped at `max_sources`.

---

## 9. Versioned Metrics and Telemetry

Historical definitions were not changed.

| Metric family | Version | Notes |
|---|---|---|
| Answer-quality components/weights | `answer-quality-1.0` | Unchanged; existing test pins this version |
| Answer-quality additive diagnostics | `answer-quality-diagnostics-1.0` | New citation/context/latency fields |
| Citation integrity | `citation-integrity-1.0` | New, additive |
| Editor evidence selection | `editor-evidence-selection-1.0` | New, additive |
| Registry retrieval | `retrieval-registry-1.0` | Unchanged |
| Candidate-pool retrieval | `retrieval-candidate-pool-1.0` | Unchanged |

Missing measurements remain `null` / `not_measurable`, never zero. Aggregate
sections include `measured_cases` and `status`.

The live artifacts now include per case:

- `citation_integrity`;
- `editor_context`;
- `latency_breakdown`;
- `token_usage`.

Token usage is still only partially available: 1 of 6 calls exposed provider
counts because the streamed early-stop closes before Ollama's final `done`
chunk. The measured call in run 2 reported `3,320` prompt and `1,000`
completion tokens. Context token figures are explicitly `characters/4`
estimates.

---

## 10. Test Results

### 10.1 New Phase 3 tests (33)

| Module | Tests | Coverage |
|---|---:|---|
| `tests/test_phase3_editor_evidence.py` | 10 | Scoring, diversity, near-duplicates, budgets, stable IDs, telemetry, truncation |
| `tests/test_phase3_citation_integrity.py` | 17 | Invalid IDs, content support, binding, URL variants, displayed/cited consistency, limitations |
| `tests/test_phase3_editor_integration.py` | 6 | Budgeted black-box Editor, binding on/off, no-evidence path, fallback paths |

All 33 pass under `py -3.13`.

### 10.2 Existing and standalone suites

| Suite | Checks | Result |
|---|---:|---|
| `unittest discover -s tests -p "test_*.py"` | 232 | PASS |
| Standalone dataset verification | 44 | PASS |
| Standalone analytics / persistence / performance | 9 / 7 / 6 | PASS |
| Phase 2 instrumentation | 7 | PASS |
| **Total** | **305** | **0 failed** |

### 10.3 Regressions found and fixed during Phase 3

1. **Near-duplicate detection missed mirrors with a different title.** Fixed by
   comparing excerpt shingles in addition to title+excerpt.
2. **Limitations note double-counted invalid-citation claims** in the
   unsupported-findings bullet. Fixed so invalid references get their own
   bullet.
3. **URL-variant audit counted finding re-additions as collapsed duplicates.**
   Fixed to count only model-supplied duplicates.
4. **Prompt rewrite dropped required safety phrases.** Restored; the security
   suite now passes again.
5. **Bumping `evaluator_version` to 1.1 broke a historical assertion.** Reverted
   to `answer-quality-1.0`; the additive diagnostics carry
   `answer-quality-diagnostics-1.0`.
6. **The obsolete hardcoded `max_tokens=1200` instrumentation assertion** was
   updated to verify the new configurable cap and its approved defaults. The
   assertion's intent was preserved, not deleted.

No existing assertion was weakened or removed. No test was skipped.

---

## 11. Live Evaluation Results (Baseline vs Final)

Same dataset, labels, model, host, and three cases. Both Phase 3 runs are
observational repeats, not a controlled A/B.

### 11.1 Answer quality

| Component | Baseline | Retrieval rerun | Phase 3 run 1 | Phase 3 run 2 |
|---|---:|---:|---:|---:|
| Faithfulness | 0.6667 | 0.3333 | 0.5778 | 0.5000 |
| Answer relevance | 0.6634 | 0.7284 | 0.7586 | 0.6984 |
| Contextual precision | 1.0000 | 0.6111 | 0.9333 | 0.8056 |
| Contextual recall | 0.7778 | 0.3333 | 0.4444 | 0.4444 |
| Citation support | 0.6667 | 0.2222 | 0.5778 | 0.5000 |
| Evidence coverage | 0.8175 | 0.3444 | 0.3340 | 0.3439 |
| Unsupported claims (lower is better) | 0.0000 | 0.3333 | 0.0667 | 0.1944 |
| **Overall** | **0.7402** | **0.4379** | **0.6351** | **0.5694** |

Per-case overall (G001/G005/G014):

- baseline `0.7105 / 0.7005 / 0.8097`;
- retrieval rerun `0.3678 / 0.3385 / 0.6074`;
- Phase 3 run 1 `0.4748 / 0.6348 / 0.7957`;
- Phase 3 run 2 `0.4939 / 0.5688 / 0.6455`.

Combined Phase 3 mean over the two runs: **0.6023** (range `0.5694–0.6351`).
This is a recovery from the retrieval rerun, not an improvement over the
historical baseline.

### 11.2 Retrieval (historical registry definition)

| Metric | Baseline | Retrieval rerun | Phase 3 run 1 | Phase 3 run 2 |
|---|---:|---:|---:|---:|
| Recall@5 | 0.1667 | 0.2500 | 0.1667 | 0.1667 |
| Recall@10 | 0.1667 | 0.5833 | 0.5000 | 0.5000 |
| NDCG@10 | 0.1290 | 0.3599 | 0.3155 | 0.1986 |
| MRR | 0.1667 | 0.4417 | 0.3810 | 0.1444 |

Registry Recall@10 remains `3×` the historical baseline in both Phase 3 runs.
Run-to-run differences reflect search-result variation, not a metric change.

Candidate-pool diagnostics are reported in the artifacts. The search-order pool
concatenates queries in provider order and is expected to be low; the ranked
pool is the meaningful stage diagnostic. It is not a replacement for the
registry metric.

### 11.3 Latency

| Metric | Baseline | Retrieval rerun | Phase 3 run 1 | Phase 3 run 2 |
|---|---:|---:|---:|---:|
| Mean | 465.05 s | 1214.11 s | 1363.57 s | 1995.97 s |
| Median | 484.36 s | 1220.74 s | 502.88 s | 567.08 s |
| P95 | 497.54 s | 1385.77 s | 2845.98 s | 4461.57 s |
| Min / max | 411.79 / 499.00 s | 1017.48 / 1404.10 s | 481.52 / 3106.32 s | 526.54 / 4894.29 s |

Editor stage only:

- baseline `919.36 / 1315.85 / 1132.38 s` (median `1132.38 s`);
- Phase 3 run 1 `402.99 / 3013.84 / 417.79 s`;
- Phase 3 run 2 `287.30 / 436.59 / 463.09 s`;
- median across the six Phase 3 Editor measurements: **427.19 s**.

Combined Phase 3 end-to-end median across six runs: **546.8 s**.

Interpretation of the tail:

- run 1, G005: Editor `3013.84 s` despite a 10,147-character prompt;
- run 2, G001: research `4595.48 s` (network/fetch stall), Editor only `287.30 s`.

Each run contains one large outlier in a **different stage**. The research stall
is unrelated to the Editor change; the Editor outlier is not explained by prompt
size. With three samples and a heavy tail, the mean and P95 are unstable; the
median and per-stage breakdown are the defensible summaries. No causal speed-up
is claimed.

---

## 12. Answer, Citation, and Latency Outcomes

### 12.1 Answer quality

- Recovered from `0.4379` to `0.6351` / `0.5694`.
- Still below the historical `0.7402`.
- Main remaining gaps: contextual recall (`0.4444` vs `0.7778`) and evidence
  coverage (`0.334–0.344` vs `0.8175`). Both are depth-sensitive ratios tied to
  a 6-query, 27–30-source pipeline and three findings per case; they are not
  like-for-like quality measures against the 7–9-source baseline.
- Answer relevance improved over the baseline (`0.6984–0.7586` vs `0.6634`).
- Contextual precision rose from `0.6111` to `0.8056–0.9333`.

### 12.2 Citation and grounding

- Citation precision and coverage: `1.0` in both runs.
- Displayed/cited source consistency: `1.0` in both runs.
- Invalid references: `0`.
- Phantom URLs counted as supported: `0`.
- Snippet-only cited sources: `0`.
- Evidence-based bindings: `0` (none needed).
- Unsupported claim rate: `0.0667` / `0.1944`, down from `0.3333`.
- The limitations note was added only where measured support was incomplete.

This satisfies the Phase 3 requirements: every presented citation references an
existing retrieved source, URL variants are not double-counted, support requires
evidence text rather than URL existence, and snippet-only versus fetched
evidence is distinguished.

### 12.3 Latency

- Editor median across six measurements: `427.19 s`, vs `1132.38 s` in the
  retrieval rerun.
- End-to-end median across six measurements: `546.8 s`, vs `1220.74 s` in the
  retrieval rerun.
- The improvement is consistent for typical cases but not universal: one run
  had a `3013.84 s` Editor case and the other a `4595.48 s` research stall.
- The work is partly hardware/model-bound: CPU-only 4B inference remains the
  dominant cost. Context reduction lowers prompt-evaluation work; it cannot
  eliminate long-tail stalls.

---

## 13. PostgreSQL and Streamlit Verification

### 13.1 PostgreSQL (read-only)

| Table | Rows |
|---|---:|
| `research_sessions` | 19 |
| `research_reports` | 19 |
| `research_sources` | 167 |

`research_reports` includes `key_findings`, `unsupported_citations`, and
`source_details`. Additive migration columns are present. Counts are consistent
across the three tables; no destructive command was run.

### 13.2 Streamlit

- Imported `ui.components`, `ui.analytics`, `ui.evaluation_view`, and
  `ui.evaluation_artifacts` successfully.
- Started the app headlessly on port 8502.
- `GET /_stcore/health` returned `ok`.
- No startup exception was logged.
- The Report tab now shows key-finding support status, selected-source
  provenance, and Editor context statistics.

### 13.3 Frozen inputs

- Golden dataset SHA-256 matches the manifest.
- `git status` shows no change under `benchmarks/datasets`.
- `.env` was not modified.

---

## 14. Unresolved Issues, Limitations, and Next Steps

### 14.1 Unresolved

1. **Answer quality remains below the historical baseline** (`0.6023` combined
   vs `0.7402`). The gap is dominated by depth-sensitive contextual recall and
   evidence coverage. This is reported, not hidden.
2. **Latency is unstable.** Each three-case run contained one outlier in a
   different stage (Editor `3014 s`; research `4596 s`). P95 at n=3 is not a
   stable estimate. The G005 Editor outlier is unexplained by prompt size.
3. **Token counts are mostly unavailable.** Only 1 of 6 calls exposed provider
   counts because streamed JSON early-stop closes before the final `done` chunk.
   Context figures are `characters/4` estimates.
4. **Evidence-based binding was not exercised live.** Both runs had zero
   bindings because the model's own citations were sufficient. Binding is
   covered by deterministic tests but not observed in a live case.
5. **No controlled A/B.** Retrieval depth, prompt size, and code changed
   together across the baseline/rerun/Phase 3 sequence. No causal attribution is
   made.
6. **Full benchmarks were not run.** The 118-case golden live evaluation and the
   40-question v3 end-to-end benchmark remain NOT MEASURED.
7. **Lexical proxies are not truth.** No human review or LLM-judge score was
   produced. Citation content support cannot detect negation or subtle
   misattribution.
8. **Operational warnings remain.** Streamlit warns about `use_container_width`
   removal; LangGraph warns about the `allowed_objects` default.

### 14.2 Recommended next steps

- Run a controlled alternating A/B on the same three cases (context budget on
  vs off) with repeated trials and report median/P95 with confidence intervals.
- Profile the Ollama server during the G005-style Editor outlier and the
  research-stall outlier (CPU, memory, network) to separate model latency from
  infrastructure latency.
- Run the full 118-case live evaluation when time permits, or a stratified
  sample large enough to estimate variance.
- Consider exposing `token_usage` per node by disabling streamed early-stop for a
  measurement-only run; do not change production defaults solely to obtain
  counts.
- Add a live case that forces evidence-based binding (a deliberately mismatched
  model citation) to observe the binding path end-to-end.
- Revisit contextual recall/evidence coverage denominators with a versioned
  depth-normalized diagnostic; do not alter the historical definitions.

---

## Appendix A: Files Changed

Key code:

- `tools/evidence_selection.py` (new)
- `tools/citation_integrity.py` (new)
- `config/config.py`
- `agents/editor_agent.py`
- `generation/llm.py`
- `graph/workflow.py`
- `evaluation/answer_quality.py`
- `benchmarks/run_golden_evaluation.py`
- `tools/research_evidence.py` (`canonical_url` strips `www.`)
- `ui/components.py`

Tests:

- `tests/test_phase3_editor_evidence.py` (new)
- `tests/test_phase3_citation_integrity.py` (new)
- `tests/test_phase3_editor_integration.py` (new)
- `tests/test_phase2_instrumentation.py` (obsolete hardcoded assertion updated)

Docs:

- `README.MD`
- `docs/BENCHMARK_RESULTS.md`
- `docs/FINAL_METRICS_REPORT.md`
- this report

## Appendix B: Reproduce

```powershell
# Deterministic suites
py -3.13 -m unittest discover -s tests -p "test_*.py"

# Mocked metric self-check
py -3.13 benchmarks/run_golden_evaluation.py --mode mocked

# Live three-case repeat (unchanged labels)
py -3.13 benchmarks/run_golden_evaluation.py --mode live --confirm-live `
  --ids G001,G005,G014 --limit 3
```

The live runner writes a timestamped JSON, updates `golden_eval_latest.json`,
and appends a record to `golden_eval_records.jsonl`. It writes no PostgreSQL row.
A nonzero PowerShell exit code can be produced by a LangGraph stderr warning even
when `main()` returns 0 and the artifact is written; verify the artifact and the
per-case `error=none` lines rather than the wrapper exit code alone.

## Appendix C: Git Status

- The working tree is **uncommitted**.
- No commit and no push were performed.
- New Phase 3 source, test, and report files are untracked or modified in place.
- Pre-existing untracked screenshots, CSVs, `tmp_baseline/`, and retrieval
  reports were left untouched.
- `.env` remains untracked and unmodified.
