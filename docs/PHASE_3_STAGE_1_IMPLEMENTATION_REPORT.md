# Phase 3 Stage 1 - Implementation Report

**Stage:** Phase 3, Stage 1 - Dataset verification  
**Date:** 2026-11-12  
**Verifier:** OpenCode verification agent (DeepSeek V4.1 Flash Free)  
**Final status:** **NEEDS ATTENTION**

---

## 1. Executive summary

Phase 3 Stage 1 is complete as an implementation and audit exercise:

- all 40 benchmark questions and all 120 claims in `benchmark_v1.json` were checked against directly inspected primary sources;
- a new evidence-backed dataset, `benchmarks/datasets/benchmark_v2.json`, was generated from a reproducible builder;
- v1 remains byte-for-byte unchanged (SHA-256 verified);
- the validator was extended to support both v1 and v2;
- the test suite was extended from 12 to 25 tests, including v2 positive and negative temporary-fixture tests;
- the required verification, guide, and implementation documents were written;
- both datasets validate cleanly (15/15 checks for v1, 20/20 for v2) and all 25 tests pass.

The stage does **not** earn a PASS for factual coverage. Only **23 of 40 questions** are fully verified. Three claims are contradicted, one needs more evidence, and 19 are only partially supported. Those unresolved claims remain unverified and are preserved with their original wording and suggested corrections.

**Recommendation:** retain v2 as the auditable verification baseline, but do not treat it as a fully verified answer key until the 23 unresolved claims are corrected or supported with additional primary evidence (a future v3).

---

## 2. Deliverables

| Deliverable | Path | Status |
|---|---|---|
| Evidence-backed dataset | `benchmarks/datasets/benchmark_v2.json` | Created |
| Reproducible builder | `benchmarks/build_benchmark_v2.py` | Created |
| Version-aware validator | `benchmarks/validate_dataset.py` | Updated |
| Extended test suite | `tests/test_phase3_dataset_verification.py` | Updated |
| Verification report | `docs/PHASE_3_STAGE_1_DATASET_VERIFICATION.md` | Created |
| Verification guide | `benchmarks/datasets/DATASET_VERIFICATION_GUIDE.md` | Created |
| Implementation report | `docs/PHASE_3_STAGE_1_IMPLEMENTATION_REPORT.md` | Created |

The frozen v1 dataset and the Phase 1/2 reports were not modified.

---

## 3. Dataset v2 design

### 3.1 Preservation

`benchmark_v2.json` preserves:

- all 40 question IDs in order (`Q01`-`Q40`);
- every `domain`, `difficulty`, `task_type`, `topic`, `metadata`, and `evaluation_rubric` value;
- the original `provisional_reference_claims` list verbatim;
- the v1 SHA-256 in `verification_metadata.v1_sha256`.

The builder aborts if the v1 file hash differs from:

```
4f1cb7f5ca19e1c58e10de714844fd042ffdd70ac4f8321e80046a6cd52573ec
```

### 3.2 Additions

Top-level:

- `verification_metadata` - date, verifier, methodology, source policy, five status definitions, aggregation rule, totals, v1 hash;
- `sources` - 86 source records (`S001`-`S086`) with URL, title, publisher, publication/version date, access date, source type, and inspection notes.

Per question:

- `ground_truth.claims` - one record per claim with `claim_id`, `claim_text`, `status`, `source_ids`, `evidence_summary`, `support_explanation`, `contradiction_or_gap`, `suggested_correction`, `verifier`, `verified_at`, and `notes`;
- `ground_truth.verification_status` - aggregated question status;
- `ground_truth.is_verified` - true only when all claims are verified;
- `ground_truth.verified_by` / `verified_at` - present only for fully verified questions;
- `ground_truth.reviewed_by` / `reviewed_at` - always present;
- `ground_truth.verification_notes` - claim-outcome summary.

### 3.3 Status outcomes

| Status | Claims | Questions |
|---|---:|---:|
| `verified` | 97 | 23 |
| `partially_supported` | 19 | 13 |
| `contradicted` | 3 | 3 |
| `needs_more_evidence` | 1 | 1 |
| `time_sensitive` | 0 | 0 |
| **Total** | **120** | **40** |

Contradicted claims: `Q12-C1`, `Q15-C3`, `Q34-C2`.  
Needs more evidence: `Q17-C2`.  
Question-level contradictions: Q12, Q15, Q34. Question-level needs-more-evidence: Q17.

### 3.4 Source-mapping correction during the audit

While preparing the verification report, a mapping defect was found: the three Q27 claims cited `S076` (ASML EUV lithography systems) although their evidence text came from NVIDIA's silicon-photonics page. The builder was corrected:

- added `S086` for NVIDIA, "Silicon Photonics Networking for Agentic AI";
- remapped Q27-C1, Q27-C2, and Q27-C3 to `S086`;
- rebuilt the dataset (source count is now 86);
- updated the tests that assert the source count.

This is recorded as evidence that the audit included source-to-claim traceability, not just schema checks.

---

## 4. Validator changes

`benchmarks/validate_dataset.py` remains a read-only, standard-library-only validator. It is now version-aware.

### 4.1 v1 behaviour preserved

For `version` major `< 2` and no `verification_metadata`:

- 15 checks, identical to the previous validator;
- two-state verification model (`requires_manual_reference_verification` / `verified`);
- no source-registry or claim-record checks;
- CLI exit code `0` when all checks pass.

Verified: v1 still reports 15/15 checks and `RESULT: PASS`.

### 4.2 v2 additions

When the dataset is v2:

- `top_level_schema` additionally requires `verification_metadata` and `sources`;
- `verification_state` uses the five-state vocabulary and enforces:
  - `is_verified` true only when status is `verified`;
  - `verified_by` non-empty only for verified questions;
  - `verified_at` present only for verified questions;
  - `unresolved` questions must not carry a verifier or timestamp;
  - `reviewed_by` / `reviewed_at` present and valid;
- five new checks are appended:

| Check | Enforcement |
|---|---|
| `v2_source_registry` | Complete source records, unique `Sxxx` IDs, valid HTTP(S) URLs, valid access dates. |
| `v2_verification_metadata` | Methodology, five status definitions, valid totals, `claims_by_status`, `questions_verified`, and a 64-hex `v1_sha256`. |
| `v2_claim_records` | One record per original claim, unique `Qxx-Cn` IDs, verbatim `claim_text`, valid status, non-empty `source_ids`, verifier, and ISO `verified_at`. |
| `v2_claim_evidence` | Non-empty evidence/support text, every `source_id` resolves to the registry, status-specific gap/correction rules, and no gap for verified claims. |
| `v2_question_aggregation` | Question status and flag match the documented aggregation rule; records claim-status and verified-question counts. |

Result fields added: `dataset_version`, `sources_checked`, `claim_status_counts`, `v2_questions_verified`. Console and Markdown report output were extended conditionally, so the v1 report layout remains compatible.

### 4.3 Aggregation implementation

```
all verified               -> verified
any contradicted           -> contradicted
else any needs_more_evidence -> needs_more_evidence
else any time_sensitive    -> time_sensitive
else                       -> partially_supported
```

The validator recomputes this from the claim records and rejects mismatched question flags.

---

## 5. Test coverage

`tests/test_phase3_dataset_verification.py` now contains 25 tests.

v1 coverage (12 tests, preserved):

- real v1 dataset passes all 15 checks;
- missing dataset path returns exit code 1;
- duplicate question IDs;
- missing questions;
- duplicate topics;
- invalid taxonomy values;
- missing metadata;
- missing ground-truth claims;
- invalid verification flags;
- inconsistent verification metadata;
- missing expected source domains;
- invalid evaluation rubrics.

v2 coverage (13 new tests):

- real v2 dataset passes all 20 checks, 40 questions, 120 claims, 86 sources, 23 verified questions;
- v1 and v2 are detected distinctly (15 vs 20 checks, different metadata);
- v2 preserves v1 question content, claim wording, and v1 hash;
- every v2 claim has resolvable evidence and status-specific fields;
- missing `evidence_summary` is rejected;
- unknown `source_id` is rejected;
- partial claim without a gap is rejected;
- contradicted claim without a correction is rejected;
- unresolved question cannot be marked verified;
- claim-wording drift from v1 is rejected;
- metadata total mismatch is rejected;
- missing source-registry entry is rejected;
- v2 taxonomy violations are still rejected.

All negative tests write deep-copied fixtures to temporary directories; the real datasets are never written.

---

## 6. Command log

All commands were run from `C:\Reddiah\Research_agent` with Python 3.10.11.

| # | Command | Exit code | Result |
|---:|---|---:|---|
| 1 | `python benchmarks\build_benchmark_v2.py` | 0 | Wrote 40 questions, 120 claims, 86 sources; 23 verified questions. |
| 2 | `python benchmarks\validate_dataset.py` | 0 | 15/15 checks passed; `RESULT: PASS` (v1). |
| 3 | `python benchmarks\validate_dataset.py --dataset benchmarks\datasets\benchmark_v2.json` | 0 | 20/20 checks passed; `RESULT: PASS` (v2). |
| 4 | `python tests\test_phase3_dataset_verification.py` | 0 | 25 passed, 0 failed; `RESULT: PASS`. |
| 5 | `python -m py_compile benchmarks\validate_dataset.py benchmarks\build_benchmark_v2.py tests\test_phase3_dataset_verification.py` | 0 | All three modules compile. |
| 6 | `python tests\test_phase2_instrumentation.py` | 1 | **Blocked:** `ModuleNotFoundError: No module named 'sqlalchemy'`. No dependency was installed, per constraints. |

The v1 SHA-256 was rechecked after all work:

```
4f1cb7f5ca19e1c58e10de714844fd042ffdd70ac4f8321e80046a6cd52573ec
```

The v2 SHA-256 (informational, will change if the builder is rerun with different evidence) is:

```
5f859b81be45b787b5a6108378a3b9abe0584e4697ad3d8def0f65a49a5b779f
```

### 6.1 Regression note

The Phase 2 instrumentation suite could not run because the active Python environment lacks `sqlalchemy`. This is a pre-existing environment limitation, not a failure introduced by the Phase 3 changes. The Phase 3 validator and tests use only the standard library and do not require database or model services. No package installation was performed.

---

## 7. Constraint compliance

| Constraint | Status |
|---|---|
| Do not modify app/agent logic | Satisfied. No changes to `agents/`, `app.py`, `graph/`, `database/`, or `schemas/`. |
| Do not modify database schemas/history | Satisfied. |
| Do not modify Phase 1/2 reports | Satisfied. |
| Do not modify existing Phase 2 tests | Satisfied. |
| Do not run the research agent or benchmark | Satisfied. |
| Do not connect to PostgreSQL or Ollama | Satisfied. |
| Do not install dependencies | Satisfied. |
| Do not stage, commit, push, reset, or clean Git | Satisfied. No staging or commit commands were run. |
| Preserve modified and untracked work | Satisfied. All pre-existing modified/untracked files were left in place. |
| Never invent URLs, facts, dates, or outcomes | Evidence was drawn from inspected sources; gaps and limitations are recorded. |
| Do not mark a claim verified because a URL exists | Enforced by review and by validator checks on evidence text; unresolved claims remain unverified. |

---

## 8. Risks and limitations

1. **Factual coverage is incomplete.** 23 of 40 questions remain unresolved at the question level. This is the reason for the final NEEDS ATTENTION status.
2. **Three answers are materially wrong.** Q12-C1, Q15-C3, and Q34-C2 require wording corrections before use as a reference key.
3. **Q17-C2 is unsupported.** No inspected source demonstrates automated patch synthesis for memory-safety or logic vulnerabilities.
4. **19 claims are over-broad rather than false.** They often contain a defensible core plus an unsupported number, universal quantifier, or comparison.
5. **Living documents can drift.** Several sources are vendor or project documentation that changes over time; the access date is 2026-11-12.
6. **Vendor-reported figures remain vendor-reported.** For example, NVIDIA's 5x CPO power-efficiency statement is not an independent measurement.
7. **Source coverage is finite.** Where a numerical or comparative detail was absent from the inspected page, the claim was downgraded rather than inferred.
8. **The full regression suite could not run** because `sqlalchemy` is missing from the environment.
9. **v2 is generated.** Manual edits to `benchmark_v2.json` would break reproducibility; all changes must go through `build_benchmark_v2.py`.

---

## 9. Recommended next steps

1. **Decide on the three corrections.** Adopt the suggested v3 wording for Q12-C1, Q15-C3, and Q34-C2, or gather evidence that supports the original wording.
2. **Resolve Q17-C2.** Either cite a specific APR system/paper or soften the claim.
3. **Tighten the 19 partial claims.** Remove unsupported numbers ("<1ms", "sub-micron pitch", "order of magnitude", "SLSA 1-4") or attach primary evidence.
4. **Re-verify living documents** before any public benchmark release.
5. **Install the missing Phase 2 dependencies in a controlled environment** and run `tests/test_phase2_instrumentation.py` as a regression.
6. **Keep v2 immutable once accepted.** Record corrections in a v3 dataset so the audit trail is preserved.

---

## 10. Final status

### NEEDS ATTENTION

Implementation is complete and reproducible. Structural validation passes for both datasets, and the test suite passes. The status is not PASS because factual coverage is incomplete:

- 23/40 questions fully verified;
- 3 contradicted claims;
- 1 claim with insufficient evidence;
- 19 partially supported claims.

Those gaps are documented, source-mapped, and paired with suggested corrections. They should be resolved before the benchmark is treated as a fully verified reference answer key.

---

**Prepared by:** OpenCode verification agent (DeepSeek V4.1 Flash Free)  
**Date:** 2026-11-12  
**Status:** NEEDS ATTENTION
