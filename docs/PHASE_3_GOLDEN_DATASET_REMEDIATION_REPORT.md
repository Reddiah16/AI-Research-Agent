# Phase 3 Stage 1 — Golden Dataset Remediation Report

**Project:** AI Research Agent  
**Stage:** Phase 3 Stage 1 closeout  
**Dataset under remediation:** `benchmarks/datasets/benchmark_v3.json`  
**Frozen baselines:** `benchmark_v1.json`, `benchmark_v2.json`  
**Report date:** 2026-11-13  
**Final status:** **NEEDS ATTENTION**

---

## 1. Executive summary

Phase 3 Stage 1 closeout is complete at the implementation and structural-validation level.

- `benchmark_v3.json` validates cleanly: **29/29 checks passed**.
- The frozen baselines still validate: **v1 15/15**, **v2 20/20**.
- The dataset test suite passes: **44/44 tests**.
- The v3 builder reproduces the on-disk dataset exactly (object-equal and byte-identical).
- v1 and v2 are byte-for-byte unchanged.

The final status is **NEEDS ATTENTION**, not PASS, because the dataset still contains **20 unresolved claims** (18 `partially_supported`, 2 `needs_more_evidence`), **13 partially verified questions**, and **one insufficiently verified source (S053)**. Structural validation proves internal consistency and traceability; it does **not** prove factual correctness against live sources.

No application pipeline code was modified. No database was contacted. No package was installed. Nothing was staged, committed, or pushed.

---

## 2. Scope and constraints

### 2.1 In scope

- v3-specific structural and evidence-metadata validation;
- v3 test coverage;
- verification that v1/v2 are unchanged;
- builder reproducibility;
- documentation of unresolved claims, domain mismatches, and weak sources;
- safe execution of structural validation and dependency-free tests.

### 2.2 Constraints observed

| Constraint | Status |
|---|---|
| Never run `tests/test_db_postgres.py` | Complied — file was not executed (and is not present in the current tests directory). |
| No database connections or resets | Complied — no database command was run. |
| Do not modify application pipeline code | Complied — no file under `agents/`, `graph/`, `database/`, `ui/`, or `tools/` was edited. |
| Preserve every existing modified and untracked file | Complied — only the validator and the Phase 3 test file were edited in place; the new report was added. |
| Do not delete or overwrite datasets, reports, screenshots or results | Complied — dataset hashes are unchanged. |
| Do not stage, commit or push | Complied — `git status` shows the same tracked/untracked composition. |
| Do not fabricate evidence or equate structural validation with factual correctness | Complied — limitations are stated explicitly in §11. |

---

## 3. Deliverables and changed files

| File | Change | Notes |
|---|---|---|
| `benchmarks/validate_dataset.py` | Extended in place | Added `v3_claim_evidence_integrity`; registered it in `_validate_v3_extensions()`. |
| `tests/test_phase3_dataset_verification.py` | Extended in place | Added `_validate_mutated_v3()` plus 8 tests (frozen v3 hash and 7 negative mutation tests). |
| `docs/PHASE_3_GOLDEN_DATASET_REMEDIATION_REPORT.md` | Created | This report. |
| `benchmarks/datasets/benchmark_v1.json` | Unchanged | Byte-for-byte preserved. |
| `benchmarks/datasets/benchmark_v2.json` | Unchanged | Byte-for-byte preserved. |
| `benchmarks/datasets/benchmark_v3.json` | Unchanged | Byte-for-byte preserved; not rebuilt by this closeout. |

No application code, configuration, screenshot, or result file was modified.

---

## 4. Dataset hashes and preservation

Recomputed with SHA-256 after all edits:

| Dataset | SHA-256 | Expected | Match |
|---|---|---|---|
| `benchmark_v1.json` | `4f1cb7f5ca19e1c58e10de714844fd042ffdd70ac4f8321e80046a6cd52573ec` | `4f1cb7f5…73ec` | Yes |
| `benchmark_v2.json` | `5f859b81be45b787b5a6108378a3b9abe0584e4697ad3d8def0f65a49a5b779f` | `5f859b81…779f` | Yes |
| `benchmark_v3.json` | `10a5ba16c400a1cac0040e4357df49d97fc09e258f006999df96c93df2ff2527` | `10a5ba16…2527` | Yes |

Additional integrity results:

- The validator’s `dataset_unchanged` check passed for all three datasets (hash before == hash after).
- The v3 builder `build()` was imported and called directly (without invoking `main()` and without writing):
  - returned object equals the parsed on-disk v3 object: **True**;
  - serialized output is byte-identical to the on-disk file: **True**;
  - v3 hash before and after the call: unchanged.

---

## 5. Validator extensions

### 5.1 Existing v3 checks retained

The validator already carried these v3 checks before this closeout:

1. `v3_claim_wording_audit`
2. `v3_exact_wording_evidence`
3. `v3_unresolved_claim_rules`
4. `v3_review_history`
5. `v3_source_verification`
6. `v3_source_domain_reconciliation`
7. `v3_metadata_consistency`
8. `v3_version_integrity`

### 5.2 New v3 check added

**`v3_claim_evidence_integrity`** — verifies, for all 120 claims:

- `source_ids` is a non-empty list;
- `source_ids` contains no duplicates;
- every `source_id` resolves to the 86-source registry;
- every `correction_evidence` entry is also present in that claim’s `source_ids`, so a correction cannot cite evidence outside the claim’s own citation set.

This closes a traceability gap: previously, correction evidence was checked for resolvability but not for consistency with the claim’s citations.

### 5.3 v3 check count

`benchmark_v3.json` now runs **29 checks** (15 base + 5 v2 + 9 v3). v1 remains at 15 checks and v2 at 20 checks.

---

## 6. Test-suite extensions

The Phase 3 suite previously had 36 tests. It now has **44**:

### 6.1 Added tests

| Test | Purpose |
|---|---|
| `test_v3_hash_is_frozen` | Asserts the on-disk v3 SHA-256 equals the frozen constant. |
| `test_v3_effective_wording_must_match_correction` | Mutating `effective_claim_text` must fail `v3_claim_wording_audit`. |
| `test_v3_correction_evidence_must_be_cited` | An unknown or uncited correction source must fail `v3_claim_evidence_integrity`. |
| `test_v3_review_history_must_match_status` | A review-history status that disagrees with the claim must fail `v3_review_history`. |
| `test_v3_domain_audit_must_match_citations` | Tampering with `outside_expected_source_domains` must fail `v3_source_domain_reconciliation`. |
| `test_v3_metadata_totals_must_match_records` | Inflating `claims_corrected` must fail `v3_metadata_consistency`. |
| `test_v3_insufficient_source_cannot_be_sole_evidence` | A verified claim backed only by S053 must fail `v3_source_verification`. |
| `test_v3_lineage_is_required` | Removing `supersedes` must fail `v3_version_integrity`. |

### 6.2 Test-helper addition

`_validate_mutated_v3()` deep-copies `benchmark_v3.json`, applies a mutation, writes the fixture to a temporary directory, and runs the real validator against it. The real dataset is never modified.

---

## 7. Validation and test results

### 7.1 Structural validation

| Dataset | Command | Result |
|---|---|---|
| v1 | `python benchmarks\validate_dataset.py` | **15/15 passed, 0 failed — PASS** |
| v2 | `python benchmarks\validate_dataset.py --dataset benchmarks\datasets\benchmark_v2.json` | **20/20 passed, 0 failed — PASS** |
| v3 | `python benchmarks\validate_dataset.py --dataset benchmarks\datasets\benchmark_v3.json` | **29/29 passed, 0 failed — PASS** |

v3 observed values:

- questions: 40
- claims: 120
- sources: 86
- claim statuses: `{verified: 100, partially_supported: 18, needs_more_evidence: 2}`
- fully verified questions: 25/40
- expected-source-domain count: 3 per question for all 40
- claim count: 3 per question for all 40

### 7.2 Test suite

```text
python tests\test_phase3_dataset_verification.py
Tests: 44 passed, 0 failed
RESULT: PASS
```

This suite uses only the Python standard library and does not touch PostgreSQL, Ollama, DuckDuckGo, or the research pipeline.

### 7.3 Builder reproducibility

```text
build_equals_on_disk_object: True
build_equals_on_disk_bytes:  True
hash_before: 10a5ba16c400a1cac0040e4357df49d97fc09e258f006999df96c93df2ff2527
hash_after : 10a5ba16c400a1cac0040e4357df49d97fc09e258f006999df96c93df2ff2527
hash_unchanged: True
```

---

## 8. Dataset remediation outcomes

### 8.1 Status distribution

| Metric | v2 | v3 |
|---|---:|---:|
| Verified claims | 97 | 100 |
| Partially supported claims | 19 | 18 |
| Contradicted claims | 3 | 0 |
| Needs more evidence | 1 | 2 |
| Verified questions | 23/40 | 25/40 |
| Corrected claims | — | 30 |
| Downgraded claims | — | 1 |
| Unchanged claims | — | 89 |

### 8.2 Corrected claims (30)

```text
Q01-C2, Q08-C3, Q09-C1, Q10-C3, Q11-C3, Q12-C1, Q12-C3, Q14-C2,
Q14-C3, Q15-C2, Q15-C3, Q16-C3, Q20-C1, Q21-C2, Q22-C1, Q23-C2,
Q24-C2, Q25-C1, Q25-C2, Q27-C2, Q27-C3, Q31-C2, Q32-C2, Q32-C3,
Q33-C1, Q34-C2, Q34-C3, Q35-C2, Q38-C1, Q40-C3
```

Each corrected claim records:

- the original `claim_text`, preserved verbatim;
- `corrected_claim_text` and `effective_claim_text`;
- `original_status`, `status`, `resolution`;
- `status_change_reason`;
- `correction_evidence` (source IDs);
- `exact_wording_met` and `exact_wording_assessment`;
- at least two `review_history` entries whose first and last statuses match `original_status` and `status`.

The three formerly contradicted claims — **Q12-C1**, **Q15-C3**, **Q34-C2** — are now `verified` with corrected wording and cited evidence. Q29-C2 was downgraded from `verified` to `needs_more_evidence`; Q24-C2 was narrowed and now relies on S052 rather than the weakly evidenced S053.

---

## 9. Unresolved claims and questions

### 9.1 Unresolved claims (20)

| Claim | Status | Resolution |
|---|---|---|
| Q07-C3 | partially_supported | retained_partially_supported |
| Q09-C3 | partially_supported | retained_partially_supported |
| Q17-C1 | partially_supported | retained_partially_supported |
| Q17-C2 | needs_more_evidence | retained_needs_more_evidence |
| Q17-C3 | partially_supported | retained_partially_supported |
| Q20-C3 | partially_supported | retained_partially_supported |
| Q22-C2 | partially_supported | retained_partially_supported |
| Q24-C3 | partially_supported | retained_partially_supported |
| Q25-C3 | partially_supported | retained_partially_supported |
| Q27-C1 | partially_supported | retained_partially_supported |
| Q29-C2 | needs_more_evidence | downgraded_needs_more_evidence |
| Q30-C2 | partially_supported | retained_partially_supported |
| Q30-C3 | partially_supported | retained_partially_supported |
| Q31-C1 | partially_supported | retained_partially_supported |
| Q31-C3 | partially_supported | retained_partially_supported |
| Q32-C1 | partially_supported | retained_partially_supported |
| Q33-C2 | partially_supported | retained_partially_supported |
| Q38-C2 | partially_supported | retained_partially_supported |
| Q38-C3 | partially_supported | retained_partially_supported |
| Q40-C2 | partially_supported | retained_partially_supported |

### 9.2 Question-level status

- **25 verified questions**;
- **13 partially supported questions:** Q07, Q09, Q20, Q22, Q24, Q25, Q27, Q30, Q31, Q32, Q33, Q38, Q40;
- **2 needs-more-evidence questions:** Q17, Q29.

Every unresolved claim retains its gap statement and suggested correction. None was silently promoted to `verified`.

---

## 10. Domain mismatches and weak sources

### 10.1 Expected-domain reconciliation

The frozen `expected_source_domains` lists were **not** rewritten. Instead, each question records a `source_domain_audit`:

- expected domains;
- actual cited domains;
- outside domains;
- mismatched claim IDs and count;
- a status of `within_expected_domains` or `reconciled_with_documented_substitutions`;
- one substitution record per outside domain, with classification, rationale, source IDs, and claim IDs.

Observed totals:

| Metric | Value |
|---|---:|
| Claims citing at least one outside domain | 39 |
| Questions with at least one outside-domain claim | 20 |
| Distinct outside domains | 18 |
| Substitution records | 21 |

Questions with documented mismatches:

```text
Q01 (github.com), Q04 (arxiv.org), Q05 (docs.langchain.com), Q09 (docs.k3s.io),
Q10 (github.com), Q15 (www.cisa.gov), Q16 (falcon-sign.info), Q23 (www.cas.org),
Q24 (arxiv.org), Q25 (www.ipp.mpg.de), Q26 (apt.cs.manchester.ac.uk,
open-neuromorphic.org), Q27 (www.nvidia.com), Q28 (microservices.io),
Q30 (neon.com), Q32 (docs.wasmtime.dev), Q34 (arxiv.org),
Q35 (raw.githubusercontent.com), Q36 (www.rfc-editor.org),
Q38 (www.intel.com), Q40 (docs.port.io)
```

These mismatches are **documented, not eliminated**. The validator proves that each audit is internally consistent with the cited URLs; it does not prove that each substitution is the best available authority.

### 10.2 Weak source

| Source | Issue | Effect |
|---|---|---|
| **S053** | Missing authors; year-only publication date (“2026”); no version/DOI; cannot be independently confirmed from the repository. | Marked `insufficiently_verified` with `bibliographic_gaps`. It is retained as corroborating context only. No verified claim may depend on it alone; Q24-C2 was narrowed to rely on S052. |

Source status totals: 85 `verified_in_repository_record`, 1 `insufficiently_verified` (S053).

The validator enforces that S053 is the only insufficient source, that it carries bibliographic gaps and a review note, and that no verified claim cites it as sole evidence.

---

## 11. Limitations

1. **Structural validation is not factual validation.** Passing 29/29 checks proves schema integrity, internal consistency, resolvability, and metadata traceability. It does not prove that a cited source actually supports a claim.
2. **No live source re-fetching.** No network request was made. No source snapshot exists in the repository. Claims were assessed from recorded `evidence_summary`, `support_explanation`, `inspection_notes`, and review history.
3. **No database verification.** Live PostgreSQL counts, latencies, and persisted reports were not re-checked.
4. **No pipeline execution.** The research pipeline, Ollama, DuckDuckGo, and the Streamlit app were not run.
5. **Residual unresolved coverage.** 20 claims and 15 questions remain unresolved at the evidence level.
6. **Vendor-reported and mutable sources.** Several sources are living vendor pages or GitHub branches; content can change without the URL changing.
7. **Domain metadata remains frozen.** The declared expected domains still differ from the actual evidence base; the difference is documented rather than redesigned.
8. **Frozen v3 hash.** The test suite now freezes the v3 SHA-256. Any future legitimate v3 edit requires an explicit re-baseline of the test constant.
9. **Environment not provisioned.** No third-party packages are installed, so database, UI, search, and pipeline tests cannot run in this environment.

---

## 12. Environment and dependency status

Read-only import probe results:

| Module | Status |
|---|---|
| `sqlalchemy` | MISSING |
| `streamlit` | MISSING |
| `pandas` | MISSING |
| `altair` | MISSING |
| `ddgs` | MISSING |
| `ollama` | MISSING |
| `langgraph` | MISSING |
| `pydantic` | MISSING |
| `dotenv` | MISSING |
| `psycopg2` | MISSING |

Consequences:

- `tests/test_web_search.py` cannot run: `ModuleNotFoundError: No module named 'ddgs'`.
- `tests/test_analytics_dashboard.py`, `tests/test_metrics_persistence.py`, `tests/test_performance_tracking.py`, and `tests/test_phase2_instrumentation.py` cannot run: `ModuleNotFoundError: No module named 'sqlalchemy'`.

No package was installed. No dependency version was changed. The Phase 3 dataset suite runs because it uses only the standard library.

---

## 13. Commands executed

| Command | Result |
|---|---|
| `python benchmarks\validate_dataset.py` | 15/15 checks, exit 0 |
| `python benchmarks\validate_dataset.py --dataset benchmarks\datasets\benchmark_v2.json` | 20/20 checks, exit 0 |
| `python benchmarks\validate_dataset.py --dataset benchmarks\datasets\benchmark_v3.json` | 29/29 checks, exit 0 |
| `python tests\test_phase3_dataset_verification.py` | 44/44 tests, exit 0 |
| Read-only import of `build_benchmark_v3.build()` and comparison to on-disk v3 | Object-equal, byte-identical, hash unchanged |
| SHA-256 recomputation for all three datasets | v1/v2/v3 all match expected hashes |
| `python -c "import importlib.util..."` dependency probe | All listed third-party modules missing |
| `git status --short` | Composition unchanged; nothing staged |

Not executed: `tests/test_db_postgres.py`, any database connection/reset, the research pipeline, Ollama, DuckDuckGo, Streamlit, benchmark runner.

---

## 14. Final status

### NEEDS ATTENTION

**Implementation closeout: COMPLETE.**  
**Structural validation: PASS.**  
**Dataset evidence status: NEEDS ATTENTION.**

Reason: 100 of 120 claims are verified and 25 of 40 questions are fully verified, but 18 claims remain partially supported, 2 need more evidence, 13 questions remain partially verified, and S053 remains insufficiently verified. Those are honest, documented residuals rather than structural defects.

The stage should not be reported as a factual PASS. The correct claim is narrower: **v3 is structurally valid, deterministic, internally traceable, and preserves v1/v2 byte-for-byte, while 20 claims remain unresolved and require additional evidence or source work before the dataset can be considered fully verified.**
