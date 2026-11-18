# Dataset Verification Guide

This guide explains how `benchmark_v2.json` is structured, how claim statuses are assigned, how the question-level verification flag is derived, and how to rebuild and validate the dataset without disturbing the frozen v1 dataset.

---

## 1. Files

| Path | Purpose |
|---|---|
| `benchmarks/datasets/benchmark_v1.json` | Frozen v1 dataset. Immutable. SHA-256 `4f1cb7f5ca19e1c58e10de714844fd042ffdd70ac4f8321e80046a6cd52573ec`. |
| `benchmarks/datasets/benchmark_v2.json` | Generated v2 dataset with per-claim evidence. Do not hand-edit. |
| `benchmarks/build_benchmark_v2.py` | Reproducible builder containing the source registry and all evidence records. |
| `benchmarks/validate_dataset.py` | Read-only validator for v1 and v2. |
| `tests/test_phase3_dataset_verification.py` | Positive and negative test suite. |
| `docs/PHASE_3_STAGE_1_DATASET_VERIFICATION.md` | Human-readable verification report. |

---

## 2. Versioning rules

- **v1 is frozen.** It must never be edited. The builder asserts its SHA-256 before doing anything.
- **v2 is generated.** Change `build_benchmark_v2.py`, then rebuild. Never edit `benchmark_v2.json` directly.
- **Do not renumber questions or claims.** `Q01`-`Q40` and `Qxx-C1`-`Qxx-C3` are stable identifiers.
- **Do not change original claim wording in v2.** `ground_truth.provisional_reference_claims` preserves the v1 wording verbatim; `ground_truth.claims[i].claim_text` must equal the corresponding original string. Suggested corrections belong in `suggested_correction`.
- A future dataset that adopts corrections should be written as **v3**, not by mutating v2.
- Adding or changing evidence in v2 is acceptable when it is backed by a directly inspected primary source. Rebuild and revalidate after every change.

---

## 3. Top-level schema (v2)

```jsonc
{
  "version": "2.0.0",
  "created_at": "2026-11-12T00:00:00Z",
  "description": "...",
  "methodology_disclaimer": "...",
  "supersedes": "benchmark_v1.json",
  "verification_metadata": {
    "verification_date": "2026-11-12",
    "verifier": "...",
    "methodology": "...",
    "source_inspection_policy": "...",
    "status_definitions": {
      "verified": "...",
      "partially_supported": "...",
      "contradicted": "...",
      "needs_more_evidence": "...",
      "time_sensitive": "..."
    },
    "aggregation_rule": "...",
    "claims_total": 120,
    "claims_by_status": { "verified": 97, "...": 0 },
    "questions_total": 40,
    "questions_verified": 23,
    "v1_sha256": "4f1cb7f5..."
  },
  "sources": [ /* source records */ ],
  "benchmark_questions": [ /* 40 question records */ ]
}
```

### 3.1 Source record

Every source must have all of these fields:

| Field | Requirement |
|---|---|
| `source_id` | `S001`-style unique identifier. |
| `url` | Absolute `http(s)://` URL. |
| `title` | Non-empty source title. |
| `publisher` | Non-empty publisher/author/organisation. |
| `publication_date` | Non-empty date, version, or living-document marker. |
| `accessed_date` | ISO-8601 date on which the source was inspected. |
| `source_type` | Non-empty classification (preprint, standard, official_documentation, etc.). |
| `inspection_notes` | What was actually read and how it bears on the claims. |

A URL alone is not evidence. `inspection_notes`, `evidence_summary`, and `support_explanation` must state what the source says.

### 3.2 Claim record

Each question has `ground_truth.claims`, a list with one record per original claim:

```jsonc
{
  "claim_id": "Q01-C1",
  "claim_text": "<exact original wording from v1>",
  "status": "verified",              // one of the five statuses
  "source_ids": ["S001"],            // must resolve to top-level sources
  "evidence_summary": "...",         // what the source says
  "support_explanation": "...",      // why that supports or fails to support the claim
  "contradiction_or_gap": null,      // required for non-verified statuses
  "suggested_correction": null,      // required for contradicted/needs_more_evidence/partial
  "verifier": "...",
  "verified_at": "2026-11-12",
  "notes": ""
}
```

### 3.3 Question verification state

Each `ground_truth` also contains:

- `verification_status`: one of the five statuses;
- `is_verified`: `true` only when all claims are `verified`;
- `verified_by`: verifier name when verified, otherwise `null`;
- `verified_at`: present only when verified;
- `reviewed_by`: always present;
- `reviewed_at`: always present;
- `verification_notes`: summary of claim outcomes;
- `claims`: the claim records.

---

## 4. Status definitions

| Status | Use when |
|---|---|
| `verified` | The exact claim meaning is supported by the cited source(s). Do not use for "roughly right" or "the URL exists". |
| `partially_supported` | The core is supported, but a number, qualifier, universal quantifier, or comparison is not. |
| `contradicted` | A material part of the claim is contradicted. Record the contradiction and a correction. |
| `needs_more_evidence` | No inspected primary source establishes the claim. Record what evidence is missing. |
| `time_sensitive` | Correct for a stated date/version, but expected to change. Record the time boundary. |

### 4.1 Evidence rules by status

- `verified`: `contradiction_or_gap` and `suggested_correction` must be `null`.
- `partially_supported`: `contradiction_or_gap` and `suggested_correction` must both be non-empty.
- `contradicted`: both fields must be non-empty.
- `needs_more_evidence`: both fields must be non-empty (state the gap and the next step).
- `time_sensitive`: `contradiction_or_gap` must be non-empty and describe the time boundary.

### 4.2 Aggregation rule

```
if all claims verified            -> verified
elif any claim contradicted       -> contradicted
elif any claim needs_more_evidence-> needs_more_evidence
elif any claim time_sensitive     -> time_sensitive
else                              -> partially_supported
```

`is_verified` is `true` only for the first case. Unresolved questions must not carry `verified_by` or `verified_at`.

---

## 5. Reviewer checklist

Before adding or changing a verification record:

1. Open the source yourself; do not rely on the existing URL or a search snippet.
2. Confirm the source is primary (paper, standard, official docs, government, vendor technical docs).
3. Quote or paraphrase the exact passage that bears on the claim in `evidence_summary`.
4. Compare the **exact** claim wording, including quantifiers ("all", "guarantees", "without") and numbers.
5. Choose the weakest status the evidence honestly supports.
6. If not `verified`, state the gap and a concrete suggested correction.
7. Record the access date and verifier.
8. Rebuild and run both validators plus the test suite.
9. If a correction changes the reference answer, propose a v3 dataset rather than editing the original claim text.

---

## 6. Rebuilding v2

```powershell
python benchmarks\build_benchmark_v2.py
```

The builder:

1. computes the SHA-256 of `benchmark_v1.json` and aborts on mismatch;
2. loads v1 and asserts 40 questions;
3. checks that evidence exists for exactly the expected `Qxx-Cn` claim IDs;
4. preserves every question, taxonomy value, rubric, and original claim string;
5. writes `benchmarks/datasets/benchmark_v2.json` with deterministic formatting.

Expected output:

```
Wrote ...\benchmarks\datasets\benchmark_v2.json
Questions: 40
Claims: 120
Claim statuses: {'contradicted': 3, 'needs_more_evidence': 1, 'partially_supported': 19, 'verified': 97}
Verified questions: 23
```

---

## 7. Validating

### 7.1 Version 1

```powershell
python benchmarks\validate_dataset.py
```

Expected: 15 checks, all pass, `RESULT: PASS`.

### 7.2 Version 2

```powershell
python benchmarks\validate_dataset.py --dataset benchmarks\datasets\benchmark_v2.json
```

Expected: 20 checks, all pass, `RESULT: PASS`. The five v2-only checks are:

| Check | What it enforces |
|---|---|
| `v2_source_registry` | 86 unique, complete, traceable sources with valid URLs and dates. |
| `v2_verification_metadata` | Methodology, five status definitions, and totals consistent with the records. |
| `v2_claim_records` | Every original claim has one uniquely identified record with verbatim wording. |
| `v2_claim_evidence` | Evidence text is substantive, source IDs resolve, and status-specific gap/correction rules hold. |
| `v2_question_aggregation` | Question status and `is_verified` match the documented aggregation rule. |

### 7.3 Optional Markdown report

```powershell
python benchmarks\validate_dataset.py --dataset benchmarks\datasets\benchmark_v2.json --report benchmarks\datasets\VERIFICATION_REPORT.md
```

The report path is optional; validating without `--report` leaves all files untouched except stdout.

---

## 8. Running the tests

```powershell
python tests\test_phase3_dataset_verification.py
```

The suite contains 25 tests:

- v1 structural tests (12);
- v2 positive tests for schema, evidence, versioning, preservation, and taxonomy;
- v2 negative tests using temporary fixtures for missing evidence, unknown source IDs, missing gap/correction, unlawful verification, claim-wording drift, metadata totals, and missing registry entries.

The real datasets are never modified. Every negative fixture is written to a temporary directory.

Expected: `Tests: 25 passed, 0 failed`, exit code `0`.

---

## 9. Adding a new source or updating evidence

1. Add a new `Sxxx` record to `SOURCES` in `build_benchmark_v2.py`.
2. Reference it from the relevant claim(s) via `source_ids`.
3. Update `evidence_summary`, `support_explanation`, and, if necessary, `status`, `contradiction_or_gap`, and `suggested_correction`.
4. Run `python benchmarks\build_benchmark_v2.py`.
5. Run both validator commands and the test suite.
6. Update `docs/PHASE_3_STAGE_1_DATASET_VERIFICATION.md` if the outcome distribution changes.

Never reuse a `source_id` for a different URL. Never delete a source that is still referenced.

---

## 10. What the validator does not prove

- It does not prove that a claim is true; it proves that the dataset records are structurally consistent and that every claim has a resolvable evidence record.
- It does not execute the research agent or the benchmark.
- It does not connect to PostgreSQL, Ollama, or DuckDuckGo.
- It does not replace human review of the source meaning. The evidence summaries and statuses should be re-read when sources change.
