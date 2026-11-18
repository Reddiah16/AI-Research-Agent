# Phase 3 Stage 1 — Final Quality Review

**Review type:** independent, read-only quality audit  
**Dataset under review:** `benchmarks/datasets/benchmark_v2.json` (v2, SHA-256 `5f859b81be45b787b5a6108378a3b9abe0584e4697ad3d8def0f65a49a5b779f`)  
**Frozen baseline:** `benchmarks/datasets/benchmark_v1.json` (SHA-256 `4f1cb7f5ca19e1c58e10de714844fd042ffdd70ac4f8321e80046a6cd52573ec`)  
**Review date:** 2026-11-12  
**Reviewer:** OpenCode audit agent (DeepSeek V4.1 Flash Free)  
**Final status:** **NEEDS ATTENTION**

---

## 1. Verdict

The Phase 3 Stage 1 **implementation** is sound:

- the builder is deterministic and reproduces the on-disk v2 dataset exactly;
- the v1 SHA-256 guard is intact and correct;
- both datasets pass their validators (v1 15/15, v2 20/20);
- the 25-test suite passes;
- the headline totals (questions, claims, sources, status counts, verified questions) agree across the dataset and reports;

The Phase 3 Stage 1 **evidence quality** is not yet at the standard the dataset claims for itself. The dataset defines `verified` as *“the claim's exact meaning is supported by the cited source(s)”* and warns in the guide not to use it for “roughly right”. Applying that standard to the recorded evidence:

- **13 verified claims** contain a qualifier, number, example, mechanism, or comparison that the claim record itself does not support (Tier A);
- **13 further verified claims** contain weaker wording, examples, or rankings that need a second look (Tier B);
- one partial claim, **Q27-C3**, has an evidence summary that conflicts with both its own source inspection note and the verification report.

Tier A alone reduces fully verified questions from **23/40 to 16/40**; applying Tier B as well would reduce it to **13/40**. The dataset's headline “97 verified claims / 23 verified questions” is therefore overstated. Nothing found suggests fabricated URLs or fabricated datasets, and the unresolved claims are honestly recorded, so this is **NEEDS ATTENTION**, not FAIL.

---

## 2. Scope, method, and limitations

### 2.1 What was checked

1. all 19 `partially_supported` claims;
2. the contradicted claims Q12-C1, Q15-C3, Q34-C2;
3. Q17-C2 (`needs_more_evidence`);
4. Q27-C1/C2/C3 and source S086;
5. evidence-to-claim mapping for all 120 claims;
6. source title, publisher, URL, date, and type consistency across the 86-source registry;
7. whether every `verified` claim meets its exact wording;
8. builder reproducibility and v1 SHA-256 protection;
9. dataset ↔ verification report ↔ implementation report ↔ guide consistency;
10. the missing `sqlalchemy` environment issue.

### 2.2 Method and limits

The audit was repository-only and read-only:

- no network access, so no live page was re-fetched;
- no source snapshots exist in the repository, so source *content* could not be independently re-inspected;
- conclusions about factual support are therefore based on the recorded `evidence_summary`, `support_explanation`, source `inspection_notes`, and internal consistency between those layers;
- a claim is treated as **not meeting the exact-wording standard** when the record contradicts it, admits a gap in it, or its cited sources demonstrably do not describe it.

This means a claim could still be correct against the live source even when this audit flags it; conversely, a claim that appears internally consistent could still be wrong against the live page. The audit can prove overstatement, not truth.

### 2.3 Constraint compliance

Only the following were run: read-only shell inspection, `build()` imported without invoking `main()`, the validator, and the standard-library test suite. No research pipeline, PostgreSQL, Ollama, benchmark execution, dependency installation, staging, commit, push, reset, or clean was performed. No existing file was modified. `git status --short` was unchanged in composition before and after the audit.

---

## 3. Summary of findings

| Area | Result |
|---|---|
| Dataset structure | PASS — 40 questions, 120 claims, 86 sources, valid schema |
| Builder reproducibility | PASS — regenerated object is exactly equal to on-disk v2 |
| v1 integrity | PASS — actual SHA-256 equals the builder constant |
| Validator | PASS — v1 15/15, v2 20/20 |
| Test suite | PASS — 25/25 |
| Report consistency | PASS on counts and tables; **one evidence-text discrepancy** (Q27-C3) and **one unresolved-question count error** (implementation report risk #1) |
| Partially supported claims | 19/19 statuses appropriate |
| Contradicted / needs-evidence claims | 4/4 statuses appropriate |
| Verified-claim quality | **FAIL at exact-wording standard** — 13 Tier A overstatements, 13 Tier B risks |
| Source metadata | No duplicate or mis-mapped titles/URLs; one weakly evidenced source (S053) |
| Expected-domain metadata | **Inconsistent** — 39 claims across 20 questions cite sources outside the question's declared `expected_source_domains` |
| SQLAlchemy | Not installed; no virtualenv; Phase 2 regression cannot run; not a Phase 3 regression |

---

## 4. The 19 partially supported claims

All 19 statuses are defensible. Each has a genuine qualifier/number/generalisation gap, and none should be promoted to `verified` on the recorded evidence.

| Claim | Gap recorded | Assessment |
|---|---|---|
| Q07-C3 | DPO paper does not establish a universal stability/compute advantage or the residual role of RLHF | Appropriate |
| Q09-C3 | KubeEdge not inspected; operational overhead is deployment-specific | Appropriate |
| Q17-C1 | Big Sleep is agentic variant analysis, not grammar-aware fuzz-seed generation | Appropriate |
| Q17-C3 | The three named APR risks are not individually evidenced | Appropriate |
| Q20-C3 | “ClickHouse = clusters, DuckDB = single-node” is a product-boundary simplification | Appropriate |
| Q22-C2 | “Single-layer Vamana graph” is not on the inspected DiskANN page | Appropriate |
| Q24-C3 | Superconducting gate-speed/connectivity comparison is qualitative | Appropriate |
| Q25-C3 | Only CFS inspected; claim generalises to “private fusion companies” | Appropriate |
| Q27-C1 | “>100 Gbps” attenuation/signal-integrity/power thresholds are not in S086 | Appropriate |
| Q27-C3 | “Order of magnitude” and “energy per bit” exceed the vendor’s 5x figure | Appropriate status, but evidence summary is defective (see §7.3) |
| Q30-C2 | Per-PR preview environments are integration-dependent | Appropriate |
| Q30-C3 | Automated migration deployment is not a branching guarantee | Appropriate |
| Q31-C1 | Go FAQ does not assert p99 tail-latency spikes | Appropriate |
| Q31-C3 | Universal throughput ranking is benchmark-dependent | Appropriate |
| Q32-C1 | “<1 ms” cold start is not stated on the inspected pages | Appropriate |
| Q33-C2 | “Modern runtimes” universal quantifier is too strong | Appropriate |
| Q38-C2 | 4x/8x magnifications are not on the inspected ASML page | Appropriate |
| Q38-C3 | “Sub-micron pitch” is absent and inconsistent with current hybrid-bonding pitches | Appropriate |
| Q40-C2 | “Without coding custom plugins” conflicts with Port’s documented custom-plugin support | Appropriate |

No partial claim is marked too harshly. Q12-C1, Q15-C3, and Q34-C2 are counted separately because they are `contradicted`.

---

## 5. The three contradicted claims

| Claim | Recorded finding | Audit conclusion |
|---|---|---|
| **Q12-C1** | Claim says CPU **registers** are encrypted; sources describe guest **memory** encryption and CPU-state isolation | **Correctly contradicted.** The claim conflates memory encryption with register protection. |
| **Q15-C3** | Claim says “SLSA 1–4” and “guaranteeing … tamper-proof provenance”; SLSA v1.0 uses Build levels L0–L3, and L1 provenance is trivial to forge | **Correctly contradicted.** Both the version model and the absolute guarantee are wrong. |
| **Q34-C2** | Claim says DeepSeek-R1 showed reasoning “without supervised fine-tuning”; the final R1 used cold-start SFT and multi-stage training, and only R1-Zero was pure RL | **Correctly contradicted.** A charitable reading (“the R1 research programme”) does not rescue the sentence as written about the model. |

These are material defects in v1 wording, correctly preserved and correctly flagged. They should not be silently rewritten in v2.

---

## 6. Q17-C2 (`needs_more_evidence`)

**Correct status.** The only cited source (S036, Google Project Zero “From Naptime to Big Sleep”) documents vulnerability discovery and a crashing testcase for a SQLite memory-safety bug. It does not document patch synthesis or patch validation for memory-safety or logic vulnerabilities. No primary APR system or patch-validation result exists in the record. The suggested correction (cite a specific APR system or soften the claim) is appropriate.

One nuance: Q17-C2 is a general statement about the APR field, not a claim that Big Sleep performs APR. It could be supported by a different APR source, but on the inspected record `needs_more_evidence` is the honest status.

---

## 7. Q27 and source S086

### 7.1 Mapping correction

The earlier defect — Q27 claims citing S076 (ASML) while using NVIDIA evidence — is fixed:

- S086 is NVIDIA, “Silicon Photonics Networking for Agentic AI”, `https://www.nvidia.com/en-us/networking/products/silicon-photonics/`;
- Q27-C1, Q27-C2, and Q27-C3 all map to S086;
- Q38 correctly continues to use S075/S076 (ASML).

No remaining ASML/NVIDIA cross-mapping exists.

### 7.2 Q27-C1 and Q27-C3

Q27-C1 is correctly `partially_supported`: S086 supports “electrical signalling limits” generally but not the “>100 Gbps” threshold or the three named failure modes.

Q27-C3 is correctly `partially_supported`, but its **evidence record is internally inconsistent**:

| Layer | Statement |
|---|---|
| Claim | “cutting **latency** and energy consumption per bit” |
| Dataset `evidence_summary` | “5x better power efficiency and **lower latency**” |
| S086 `inspection_notes` | “5x better power efficiency, **higher network resiliency** and 5x sustained AI application runtime” |
| Verification report §7 | “5x better power efficiency and **higher network resiliency**” |

“Lower latency” appears in the dataset evidence but not in the source inspection note or the report. This is an evidence-to-claim mapping defect. It does not change the status (the claim was already partial), but it weakens confidence in the evidence summaries generally and must be corrected in v3.

### 7.3 Q27-C2

Q27-C2 is marked `verified`, but its wording exceeds the record in two places:

1. **“compute silicon”** — S086 describes co-packaged optics on the same package as the **ASIC** in a **networking** product, not integration with compute silicon (GPUs/CPUs);
2. **“or optical I/O chiplets”** — the disjunct is not present in S086’s inspection notes or evidence summary.

The CPO half is supported; the “compute silicon” framing and the unsupported disjunct mean Q27-C2 should be `partially_supported` or reworded. This contradicts the verification report’s list of unresolved Q27 claims (C1 and C3 only).

---

## 8. Verified claims that do not meet the exact-wording standard

The dataset’s own standard is exact meaning, explicitly not “roughly right” (guide, status table). The following claims fail that standard on the recorded evidence.

### 8.1 Tier A — recommend downgrade to `partially_supported`

| Claim | Current status | Why the record does not support the exact wording | Recommended |
|---|---|---|---|
| **Q01-C2** | verified | Claim says “**semantic** chunking”; source note says “chunking strategies”, and the claim’s own note admits the wording is “narrower than the general pipeline”. No record shows semantic chunking is one of the documented phases. | partially_supported |
| **Q08-C3** | verified | Claim lists four mitigations; AWS sources document only provisioned concurrency and SnapStart. Support says the others are “standard runtime-level mitigations” — i.e. not inspected. | partially_supported |
| **Q11-C3** | verified | Claim gives “**node-level ztunnel**” as an example of L7 proxy logic; support states ztunnel handles **L4** and waypoint proxies handle L7. The example contradicts the mechanism. | partially_supported |
| **Q14-C2** | verified | Claim says passkeys are “**immune**”; evidence says “**resistant**”; support says “phishing resistance”. Absolute quantifier vs documented resistance. | partially_supported |
| **Q15-C2** | verified | Claim groups “public transparency logs (**Rekor, Fulcio**)”; record says “Fulcio **certificates** and Rekor transparency logs”. Fulcio is a CA, not a log. | partially_supported |
| **Q16-C3** | verified | Claim says “**smaller signatures than Dilithium**”; Falcon source record says only “compact signatures”. No Dilithium comparison is in the record. | partially_supported |
| **Q25-C1** | verified | Claim names “(e.g. ITER, **Commonwealth Fusion Systems**)”, but only S054 (ITER) is cited and CFS is absent from its notes. | partially_supported |
| **Q27-C2** | verified | Claim says optical transceivers are integrated with **compute silicon** and offers “**optical I/O chiplets**” as an alternative. S086 describes CPO on the same package as a networking **ASIC**, not compute silicon, and does not mention optical I/O chiplets. | partially_supported |
| **Q29-C2** | verified | Claim asserts REST verbs, JSON/XML payloads, HTTP caching, and payload overhead. The cited sources are the gRPC introduction and GraphQL learn page; neither record documents REST mechanics. Support itself calls the compatibility part “qualitative”. | partially_supported (or needs_more_evidence until a REST primary source is added) |
| **Q33-C1** | verified | Claim says schemas are “injected into **system prompts or special tool-call token sequences**”. OpenAI record documents JSON-schema tool definitions and tool-call outputs, not either injection mechanism. | partially_supported |
| **Q35-C2** | verified | Claim says gVisor is “**preventing** direct kernel exploitation”. The record documents syscall interception via Sentry/Gofer; it does not state that exploitation is prevented, and gVisor is still software with its own attack surface. | partially_supported |
| **Q38-C1** | verified | Claim says “**sub-2nm** nodes”; S075 note says “**2 nm** Logic node” and support says “the 2 nm Logic node” — while the evidence summary says “sub-2 nm”. The record is internally inconsistent and the claim is stronger than the source note. | partially_supported |
| **Q40-C3** | verified | Claim says “**Both** platforms centralize … **scorecards**”. Backstage record lists catalog/templates/TechDocs only; support assigns scorecards to Port only. | partially_supported |

**Question-level effect of Tier A:** Q01, Q08, Q11, Q14, Q16, Q29, and Q35 lose fully verified status. Fully verified questions fall from **23/40 to 16/40**. At minimum, Q01-C2, Q11-C3, and Q14-C2 are compelling because the record expressly contradicts or self-downgrades the claim.

### 8.2 Tier B — inconsistent but lower-confidence; review before PASS

These are not necessarily false, but each contains a specific word, number, ranking, or example that the record does not establish. They should be reworded or given a supporting source in v3.

| Claim | Unsupported element |
|---|---|
| Q09-C1 | “well-suited for constrained **edge** nodes” — record supports single binary/low overhead, not edge suitability |
| Q10-C3 | “**foundational** cost-optimisation levers” — ranking not stated |
| Q12-C3 | “**CPU-attested** module” — Intel’s precise term is CPU-measured (self-admitted note) |
| Q14-C3 | “reverse-proxy phishing (e.g. **Evilginx**)” — example absent from S028 notes |
| Q20-C1 | “**petabyte-scale** datasets” — not in the ClickHouse record |
| Q21-C2 | “**thousands** of small files” — specific count absent |
| Q22-C1 | “**billion-scale**” — specific scale absent |
| Q23-C2 | “**significantly** reduces” thermal-runaway risk — sources say risk is to be reduced |
| Q25-C2 | “without an **internal** current” vs source “without a **net** plasma current” |
| Q31-C2 | “deterministic memory layout and **zero-cost abstractions**” — not in the ownership chapter record |
| Q32-C2 | “**strict standards compliance**” — not in the Wasmtime record |
| Q32-C3 | “**TensorFlow**” — source record lists TensorFlow-Lite |
| Q34-C3 | “accelerate inference on **commodity hardware**” — not in the recorded distillation evidence |

If Tier B is enforced strictly, Q10, Q21, and Q23 also lose fully verified status, taking the total to **13/40**.

### 8.3 Claims that should remain verified

The remaining verified claims are supported by records that match their wording closely: Q01-C1, Q02-C1/C2/C3, Q03-C1/C2/C3, Q04-C1/C2/C3, Q05-C1/C2/C3, Q06-C1/C2/C3, Q07-C1/C2, Q08-C1/C2, Q09-C2, Q10-C1/C2, Q11-C1/C2, Q12-C2, Q13-C1/C2/C3, Q14-C1, Q15-C1, Q16-C1/C2, Q18-C1/C2/C3, Q19-C1/C2/C3, Q20-C2, Q21-C1/C3, Q22-C3, Q23-C1/C3, Q24-C1/C2, Q26-C1/C2/C3, Q28-C1/C2/C3, Q29-C1/C3, Q30-C1, Q31-C2, Q32-C1, Q34-C1, Q35-C1/C3, Q36-C1/C2/C3, Q37-C1/C2/C3, Q39-C1/C2/C3, Q40-C1.

Two of these have self-admitted nuances that do not change status: Q01-C3 (cosine similarity usually implemented as normalised inner product) and Q13-C2 (NIST PE/PA/PEP), both of which remain fair summaries. One caveat: Q24-C2 is listed here on the strength of the S053 record, which is the weakest source record in the registry (§10.2); it should be re-confirmed when S053 is strengthened.

---

## 9. Evidence-to-claim mapping accuracy

### 9.1 Strong mapping

- Every claim has at least one resolvable `source_id`.
- Q12-C1, Q15-C3, and Q34-C2 map to sources that genuinely contradict them.
- Q17-C2 maps to a source that genuinely fails to support patch synthesis.
- The 19 partial claims map to sources that support a defensible core.
- Q27 is now correctly mapped to S086; Q38 remains correctly mapped to ASML.

### 9.2 Mapping defects found

1. **Q27-C3 latency drift** — dataset evidence claims “lower latency”, source note and report do not (§7.2).
2. **Q29-C2 source mismatch** — two sources about gRPC/GraphQL are used to establish REST mechanics; the record contains no REST source.
3. **Q27-C2 overreach** — “compute silicon” / “optical I/O chiplets” not in S086.
4. **Q38-C1 internal inconsistency** — evidence summary says “sub-2 nm”, support and source note say “2 nm”.
5. **Q25-C1 example drift** — CFS named in the claim but absent from the cited ITER record.
6. **Q40-C3 attribution drift** — scorecards attributed to “both” platforms; only Port’s record has them.

### 9.3 Expected-domain metadata is stale

Each question declares three `expected_source_domains` inherited from the Phase 1 search plan. Those are not the domains actually used as evidence:

- **39 claims across 20 questions** cite at least one source outside the declared domain set;
- examples: Q27 declares `ieee.org / tsmc.com / ayarlabs.com` but cites `nvidia.com`; Q15-C1 declares `slsa.dev / sigstore.dev / openssf.org` but cites `cisa.gov`; Q28 declares `martinfowler.com / microsoft.com / aws.amazon.com` but cites `microservices.io`; Q23 declares `nature.com / energy.gov / quantumscape.com` but cites `cas.org`;
- some are harmless aliases (`neon.tech` vs `neon.com`, `getport.io` vs `docs.port.io`, `ietf.org` vs `rfc-editor.org`, `katacontainers.io` vs `raw.githubusercontent.com`), but others are genuinely different authorities.

The validator checks that each question *has* three domains but never checks that citations belong to them. Consequently the benchmark's domain metadata no longer describes its evidence base. This is a design-consistency defect, not a factual error; it should be reconciled in v3 (either widen the declared domains or record the substitution policy).

---

## 10. Source title, publisher, and URL consistency

### 10.1 Mechanical checks (all passed)

- 86 sources, `S001`–`S086`, contiguous, unique;
- no duplicate URLs;
- no duplicate title + publisher pairs;
- every URL is an absolute HTTPS URL;
- every source has a non-empty title, publisher, publication date, source type, inspection note, and accessed date;
- every `accessed_date` is `2026-11-12`.

### 10.2 Semantic checks

- Publisher-to-domain mapping is plausible for all 86 records (arXiv, NIST, CISA, W3C, IETF, AWS, Intel, ASML, TSMC, NVIDIA, etc.).
- The earlier S086/Q27 publisher mismatch is fixed.
- No title/publisher/URL mismatch of that severity remains.
- Convention inconsistencies: some arXiv publishers include author names (`arXiv (Lewis et al.)`), others do not (`arXiv` for S053, `arXiv / Applied Physics Reviews ...` for S084). This is cosmetic but should be normalised.
- Permanence risks: S003 is a wiki and S022/S071/S072 point at mutable branches (`master`/`main`) on GitHub, so content can change without the URL changing; S031 is already marked retired in favour of SLSA v1.2; S084 uses an `/html/` version URL rather than the `/abs/` form used elsewhere.
- **S053 is the least confirmable record**: arXiv `2608.05010`, title “Neutral Atom Quantum Computing: Principles, Routes, Progress, and Challenges”, publisher “arXiv”, publication date “2026”, and **no authors**. It underpins Q24-C2 (`verified`) and Q24-C3 (`partially_supported`). It cannot be independently confirmed from the repository.

### 10.3 Claims whose evidence cannot be independently confirmed

Because no source snapshots were provided and browsing was prohibited, **none** of the source-content claims can be fully independently confirmed at audit time. The highest-risk dependencies are:

- **Q24-C2, Q24-C3** — S053 has generic metadata and no authors;
- **Q27-C1/C2/C3** — S086 is a living vendor page, and the record already shows drift;
- **Q15-C3** — S031 is a retired SLSA page (version-specific but now superseded);
- **Q08-C1/C2/C3** — AWS living documents;
- **Q40-C1/C2/C3** — Backstage/Port living documents;
- every vendor-reported figure (e.g. NVIDIA’s 5x power efficiency) remains vendor-reported, not independently measured.

---

## 11. Builder reproducibility and v1 protection

### 11.1 v1 hash protection — PASS

| Item | Value |
|---|---|
| Actual v1 SHA-256 | `4f1cb7f5ca19e1c58e10de714844fd042ffdd70ac4f8321e80046a6cd52573ec` |
| Builder constant `V1_SHA256` | `4f1cb7f5ca19e1c58e10de714844fd042ffdd70ac4f8321e80046a6cd52573ec` |
| Match | **Yes** |

`build()` recomputes the hash and raises `SystemExit` on mismatch before reading or writing anything.

### 11.2 Reproducibility — PASS

- imported `build_benchmark_v2.py` without invoking `main()`;
- called `build()`;
- compared the returned object to the parsed on-disk `benchmark_v2.json` with `==` — **exactly equal**;
- serialised the returned object with the same writer settings (`indent=2`, `ensure_ascii=False`, trailing newline) — **byte-identical** to the on-disk file (239,482 characters).

The builder is deterministic: fixed `VERIFICATION_DATE`, static source/claim registries, `sorted()` status counts, and a fixed `created_at`. `main()` is the only writer and uses UTF-8 with `\n` newlines.

### 11.3 Claim-wording preservation — PASS

`build()` copies `claim_text` from the v1 `provisional_reference_claims` list verbatim and asserts the claim-key set matches v1 exactly. Test 22 independently rejects wording drift.

---

## 12. Validator and test-suite results

| Command | Result |
|---|---|
| `python benchmarks\validate_dataset.py --dataset benchmarks\datasets\benchmark_v2.json` | 20/20 checks, exit 0 |
| `python benchmarks\validate_dataset.py --dataset benchmarks\datasets\benchmark_v1.json` | 15/15 checks, exit 0 |
| `python tests\test_phase3_dataset_verification.py` | 25/25 tests, exit 0 |

Observed distributions: 40 questions, 120 claims, 86 sources, 23 verified questions, claim statuses `{verified: 97, partially_supported: 19, contradicted: 3, needs_more_evidence: 1}`. Both validator runs leave the dataset hash unchanged.

### 12.1 Structural blind spots (why the overstatements pass)

The v2 checks verify *presence and shape*, not *semantic adequacy*:

- `v2_source_registry` checks URL syntax, dates, and non-empty metadata; it does not check that title/publisher match the URL;
- `v2_claim_evidence` checks that evidence is non-empty, source IDs resolve, and verified claims have no recorded gap; it does not check that evidence supports the claim wording;
- no check compares cited domains with `metadata.expected_source_domains`;
- no check detects the Q27-C3 evidence/report discrepancy.

These are appropriate limits for a standard-library structural validator, but they mean the 20/20 PASS cannot be presented as evidence-quality validation. The verification report should say so explicitly (it currently says claim statuses “describe inspected evidence”, which is honest but not a caveat about validator scope).

---

## 13. Consistency between dataset and reports

### 13.1 Counts — PASS

| Metric | Dataset | Verification report | Implementation report | Guide |
|---|---|---|---|---|
| Questions | 40 | 40 | 40 | 40 |
| Claims | 120 | 120 | 120 | 120 |
| Sources | 86 | 86 | 86 | 86 |
| Verified claims | 97 | 97 | 97 | 97 |
| Partial claims | 19 | 19 | 19 | 19 |
| Contradicted | 3 | 3 | 3 | 3 |
| Needs more evidence | 1 | 1 | 1 | 1 |
| Verified questions | 23 | 23 | 23/40 | 23 |
| Validator checks | 20 v2 / 15 v1 | 20/20, 15/15 | 20/20, 15/15 | 20 v2 |
| Tests | 25 | 25/25 | 25 | — |
| v2 SHA-256 | `5f859b…779f` | not printed | `5f859b…779f` | — |

### 13.2 Per-question table — PASS

All 40 rows in the verification report’s per-question table match the dataset exactly (status and unresolved-claim list). Q17 is correctly shown with three unresolved claims; Q27 with C1 and C3; Q38 with C2 and C3; Q40 with C2. The table is internally consistent, but §7.3 shows that Q27-C2 should also be unresolved, so the table faithfully reproduces a dataset-level error rather than introducing one.

### 13.3 Substantive discrepancy — Q27-C3

The verification report §7 says NVIDIA reports “5x better power efficiency and **higher network resiliency**” (matching S086’s inspection note). The dataset says “**lower latency**”. Both describe the same claim. One of them is wrong, and the dataset evidence is the one not traceable to the source note. Fix in v3.

### 13.4 Other document issues

- The verification report does not mention any claim ID from the verified set when discussing limitations; the overstatements in §8 are therefore invisible to a reader who trusts the report.
- The implementation report’s risk #1 says “**23 of 40 questions remain unresolved** at the question level”. That is wrong: 23 are verified and **17 are unresolved** (13 partial + 3 contradicted + 1 needs-more-evidence). The error appears to conflate the 23 verified questions with the 23 unresolved claims.
- The implementation report’s statement that “the audit included source-to-claim traceability” is true for the Q27 fix, but the Q27-C3 drift shows traceability was not re-checked after the fix.
- The implementation report’s final status (NEEDS ATTENTION) is consistent with this review’s verdict, though for a stronger reason than the report states.

---

## 14. SQLAlchemy environment issue

Findings, without installing anything:

| Check | Result |
|---|---|
| Python | 3.10.11, `C:\Users\DELL\AppData\Local\Programs\Python\Python310\python.exe` |
| `importlib.util.find_spec("sqlalchemy")` | `None` |
| `python -m pip show sqlalchemy` | `WARNING: Package(s) not found: sqlalchemy` (exit 1) |
| Installed packages | only `pip 23.0.1` and `setuptools 65.5.0` |
| `requirements.txt` | pins `sqlalchemy==2.0.43`, `psycopg2-binary`, LangChain/LangGraph, Streamlit, pandas, etc. |
| Virtual environment | none found (no `.venv`, no `pyvenv.cfg` in the repo) |
| Phase 2 test import | `from sqlalchemy import create_engine, inspect, text` at import time |

Conclusion: the global interpreter is effectively bare; **none** of the project's third-party dependencies are installed, not just SQLAlchemy. `tests/test_phase2_instrumentation.py` fails at import before any database code runs, so this is an environment provisioning gap, not a Phase 3 regression. The Phase 3 validator/tests run only because they are standard-library-only. Installing dependencies or running the Phase 2 suite was outside the permitted scope. Recommendation: provision a controlled environment from `requirements.txt` and run the Phase 2 regression there before any release decision that depends on it.

---

## 15. Required actions before a PASS

1. **Recategorise Tier A claims** — Q01-C2, Q08-C3, Q11-C3, Q14-C2, Q15-C2, Q16-C3, Q25-C1, Q27-C2, Q29-C2, Q33-C1, Q35-C2, Q38-C1, Q40-C3 — to `partially_supported` or reword them and add evidence. This changes the headline to at most 16/40 verified questions.
2. **Resolve Tier B wording** or attach sources for Q09-C1, Q10-C3, Q12-C3, Q14-C3, Q20-C1, Q21-C2, Q22-C1, Q23-C2, Q25-C2, Q31-C2, Q32-C2, Q32-C3, Q34-C3.
3. **Fix the Q27-C3 evidence record** so dataset, source note, and report agree; remove “lower latency” unless a source supports it.
4. **Fix Q29-C2 sourcing** with a REST primary source, or downgrade to `needs_more_evidence`.
5. **Reconcile `expected_source_domains`** with the actual evidence base (39 claims / 20 questions), or document the substitution policy.
6. **Tighten S053 metadata** (authors, full date, version) or replace it with a stronger neutral-atom review.
7. **Correct the verification and implementation reports** so they disclose verified-claim risks and fix the “23 unresolved questions” error (17 are unresolved).
8. **Preserve v2 immutability** and record every change in a new v3 dataset.

---

## Appendix A — Audit commands and key outputs

```
python benchmarks\validate_dataset.py --dataset benchmarks\datasets\benchmark_v2.json
  -> Checks: 20 passed, 0 failed; RESULT: PASS
python benchmarks\validate_dataset.py --dataset benchmarks\datasets\benchmark_v1.json
  -> Checks: 15 passed, 0 failed; RESULT: PASS
python tests\test_phase3_dataset_verification.py
  -> Tests: 25 passed, 0 failed; RESULT: PASS

build() == on-disk v2   -> True
actual v1 hash == V1_SHA256 -> True
claims=120 questions=40 sources=86
claims_by_status={'contradicted': 3, 'needs_more_evidence': 1, 'partially_supported': 19, 'verified': 97}
verified questions=23
duplicate URLs={} duplicate title+publisher={}
all accessed 2026-11-12=True
```

---

## Final status

### NEEDS ATTENTION

The implementation, reproducibility, validation, and reporting infrastructure are complete and trustworthy for a Stage 1 baseline. The evidence layer is not yet trustworthy as a *verified* answer key:

- 13 verified claims fail the dataset’s own exact-wording standard;
- a further 13 need wording/source review;
- one partial claim’s evidence summary is inconsistent with its source note and the report;
- the expected-source-domain metadata no longer matches the evidence base;
- 23/40 is an overstatement of fully verified questions; the defensible figure is 13–16/40 depending on how strictly Tier B is applied.

These are correctable in a v3 dataset. Until then, v2 should be described as an **audited, partially verified baseline**, not a fully verified reference answer key.

**Status: NEEDS ATTENTION.**
