# Final Stabilization Report

**Project:** AI Research Agent (`C:\Reddiah\Research_agent`)  
**Scope:** Dependency compatibility, test consistency, persistence-gap analysis, safe verification, portfolio freeze  
**Date:** 14 November 2026  
**Runtime:** Python 3.13.13 (`py -3.13`), Ollama 0.40.16, `qwen3:4b`, CPU-only  
**Final status:** **PASS** — stabilization scope complete; **not** a production-readiness claim

---

## 1. Summary

| Stabilization item | Outcome |
|---|---|
| Streamlit / Altair dependency conflict | **Fixed and verified** against package metadata and a no-install resolver dry run |
| Instrumentation token-cap test | **Updated to the approved 1,200 cap**, purpose preserved, test executed |
| `key_findings` persistence | Root cause identified; minimal fix recommended; **not applied** (schema change out of scope) |
| `word_count` persistence | Root cause identified; minimal fix recommended; **not applied** (no existing coverage for the new path) |
| Safe test suites | **91/91 unittest checks + 4/4 instrumentation checks + 15/15 dataset checks passed** |
| Database / datasets / Git | No DB writes or migrations; dataset hashes unchanged; nothing staged, committed, pushed or deleted |
| Packages | No install, upgrade or uninstall; environment unchanged |

No feature was added or redesigned. The only new working-tree entry is `M requirements.txt`;
the remaining edits are inside already-untracked `tests/` and `docs/` paths.

---

## 2. Exact files changed

| File | Change |
|---|---|
| `requirements.txt` | `streamlit==1.46.1` → `streamlit==1.58.0`; added a 2-line comment recording the Altair constraint and the reason. `altair==6.2.1` and all other pins untouched. |
| `tests/test_phase2_instrumentation.py` | Token-cap test now requires `max_tokens=1200`; guards against 600 / 1500 / 2000 / 2048; module docstring and test docstring updated. No other test logic changed. |
| `docs/FINAL_STABILIZATION_REPORT.md` | This report. |

Nothing else was modified. In particular, `agents/editor_agent.py`, `tools/report_parsing.py`,
`database/database.py`, `schemas/models.py` and all benchmark files are unchanged.

Git working tree after the work: **63 entries** (was 62), i.e. the 62 pre-existing
entries plus `M requirements.txt`. **0 staged**, branch `main`, no commits and no pushes.

---

## 3. Dependency compatibility evidence

### 3.1 The conflict

The old pin set could not resolve:

| Package | Declared constraint | Source |
|---|---|---|
| `streamlit==1.46.1` | `altair<6,>=4.0` | PyPI JSON metadata for 1.46.1 |
| `altair==6.2.1` | (is 6.2.1) | installed `altair-6.2.1.dist-info/METADATA` |

`altair==6.2.1` cannot satisfy `altair<6`, so any fresh
`pip install -r requirements.txt` would fail at resolution.

### 3.2 The chosen resolution

`streamlit==1.58.0` declares:

```
Requires-Python: >=3.10
Requires-Dist: altair!=5.4.0,!=5.4.1,<7,>=4.0
```

`altair==6.2.1` falls inside `>=4.0,<7` and is not one of the two excluded
versions. It requires Python `>=3.10`, which the verified interpreter (3.13.13)
satisfies. `streamlit==1.58.0` is also the version the application was actually
verified with (HTTP 200 health check, UI rendered, charts displayed), so the pin
now matches the tested runtime.

### 3.3 Resolver verification (no installation)

```powershell
py -3.13 -m pip install --dry-run --ignore-installed --no-cache-dir -r requirements.txt
```

The resolver completed without error and reported, among others:

```
Would install ... altair-6.2.1 ... streamlit-1.58.0 ...
```

This is a full resolution of the requirements file, not just the two edited
packages. `--dry-run --no-cache-dir` installs nothing and writes no package
cache; the environment was re-checked afterwards:

```
streamlit 1.58.0
altair    6.2.1
```

An alternative was considered and rejected: keeping `streamlit==1.46.1` and
downgrading Altair below 6. That would change a second dependency, move away
from the chart stack that was actually exercised, and leave the verified runtime
unrepresented by the lock file.

**Caveat:** resolution success proves the pins are mutually consistent; it does
not prove bit-for-bit sameness with this host. The dry run resolves some
dependencies differently from the installed environment (e.g. the pins select
SQLAlchemy 2.0.43 / psycopg2-binary 2.9.10 / pydantic 2.11.7, while this host runs
2.0.49 / 2.9.12 / 2.13.4). Those pins were left alone as instructed; the
difference is recorded as a limitation in §7.

---

## 4. Test consistency change

### 4.1 What changed

`tests/test_phase2_instrumentation.py` previously asserted `max_tokens=1500`,
which was never the implemented value after the Editor optimization. The test now
locks the approved cap:

```python
assert "max_tokens=1200" in source
assert "max_tokens=600"  not in source   # too small
assert "max_tokens=1500" not in source   # superseded stale expectation
assert "max_tokens=2000" not in source   # pre-optimization cap
assert "max_tokens=2048" not in source   # rejected increase
```

The original purpose is preserved: pin one exact approved JSON generation cap and
fail if the Editor silently drifts to a rejected value. The assertions still read
`agents/editor_agent.py` directly, so they test configuration rather than a mock.

### 4.2 How it was run

The file contains seven standalone functions and touches a database in tests 1,
2 and 6 (temporary SQLite schema creation and inserts). To honour the
"no migrations or writes" and "database-dependent tests excluded" constraints,
this run executed only the four database-independent checks:

- `test_node_latency_and_search_duration_tracking`
- `test_editor_token_limit_configuration`
- `test_source_grounding_instrumentation`
- `test_benchmark_dataset_structure_and_safety`

They were invoked through a small harness that imports the module functions,
forces `DATABASE_URL` to a throwaway SQLite path, and calls no database API.
Result:

```
PASS node_latency_and_search_duration_tracking
PASS editor_token_limit_configuration
PASS source_grounding_instrumentation
PASS benchmark_dataset_structure_and_safety
instrumentation_safe_checks: 4/4 passed
python_exit=0
```

The token-cap check printed:
`✓ Confirmed editor_agent.py sets max_tokens=1200 strictly as approved`.

Tests 1, 2 and 6 were **not run** in this task because they create schema and
rows, even though only in temporary SQLite files. Their behaviour is unchanged by
this edit.

---

## 5. Verification results

### 5.1 Exact commands

```powershell
# dependency metadata (read-only)
py -3.13 -m pip show streamlit altair
# PyPI JSON metadata for streamlit 1.46.1 / 1.58.0 and altair 6.2.1
py -3.13 -m pip install --dry-run --ignore-installed --no-cache-dir -r requirements.txt

# instrumentation (4 database-independent checks only)
py -3.13 -W ignore "C:\Users\DELL\AppData\Local\Temp\opencode\run_instr_safe.py"

# safe suites
py -3.13 -W ignore -m unittest tests.test_editor_generation_optimization
py -3.13 -W ignore -m unittest tests.test_phase2_research_quality
py -3.13 -W ignore -m unittest tests.test_phase3_evaluation
py -3.13 -W ignore -m unittest tests.test_phase3_reliability

# dataset
py -3.13 -W ignore benchmarks/validate_dataset.py
Get-ChildItem benchmarks\datasets -Filter *.json | ForEach-Object { Get-FileHash -Algorithm SHA256 $_.FullName }

# freeze checks (read-only)
git status --porcelain=v1
py -3.13 -W ignore "C:\Users\DELL\AppData\Local\Temp\opencode\db_safety_check.py"
```

`tests/test_db_postgres.py` was **not run**. No benchmark batch was run.

### 5.2 Suite results

| Suite | Result | Count |
|---|---|---|
| `tests/test_editor_generation_optimization.py` | OK | **17/17** |
| `tests/test_phase2_research_quality.py` | OK | **26/26** |
| `tests/test_phase3_evaluation.py` | OK | **23/23** |
| `tests/test_phase3_reliability.py` | OK | **25/25** |
| **Unittest subtotal** | **OK** | **91/91** |
| Phase 2 instrumentation (database-independent checks) | 4/4 PASS | **4/4** |
| `benchmarks/validate_dataset.py` | RESULT: PASS | **15/15 checks** |

Failures: **0** in everything executed. Exit codes were verified as `0`.

The validator confirmed 40 questions, 120 claims and 120 expected source domains,
with all 40 questions still marked `requires_manual_reference_verification`.

### 5.3 Dataset hashes (unchanged)

```
4f1cb7f5ca19e1c58e10de714844fd042ffdd70ac4f8321e80046a6cd52573ec  benchmark_v1.json
5f859b81be45b787b5a6108378a3b9abe0584e4697ad3d8def0f65a49a5b779f  benchmark_v2.json
10a5ba16c400a1cac0040e4357df49d97fc09e258f006999df96c93df2ff2527  benchmark_v3.json
```

All three match the values recorded in the repository.

### 5.4 Database unchanged

Read-only check against the configured local database:

```
database_name_matches_expected: True (ai_research_agent)
row_counts: research_reports=19, research_sessions=19, research_sources=167
schema_columns_present: True
max_session_id: 23
read_only_session: completed
```

No connection was opened by the test runs; no session, row or column changed in
this task. Session 23 remains the newest record from the prior verified run.

---

## 6. Persistence gap findings

### 6.1 `key_findings` are silently dropped

**Root cause — a lossy projection at two points:**

1. The Pydantic model carries the data:
   - `schemas/models.py:20` — `key_findings: list[dict] = Field(default_factory=list)`
   - `schemas/models.py:22` — `unsupported_citations`
   - `schemas/models.py:24` — `source_details`
2. The SQLAlchemy row has no such columns: `ResearchReportRow` in
   `database/database.py:122` stores only `title`, `outline`, `report_content`,
   `sources`, `word_count` and `created_at`.
3. `save_session` writes only the five report fields
   (`database/database.py:414–421`), so `key_findings`, `unsupported_citations`
   and `source_details` are discarded at insert time.
4. `get_session_by_id` reads back only those same five fields
   (`database/database.py:560–566`).

**Evidence from the verified run (session 23):**

| Layer | Observation |
|---|---|
| In-memory report | 3 structured key findings, all with valid source IDs |
| Grounding/evaluation | `claim_citation_coverage = 1.0` (3/3), `unsupported_citations = 0` |
| Persisted report | keys are only `title`, `outline`, `report_content`, `sources`, `word_count` |
| Retrieved `key_findings` | absent / `None` |

So the findings were used correctly during the run but cannot be audited from the
database afterwards. This is a pre-existing schema gap, not a regression from the
Editor optimization.

**Recommended minimal non-destructive fix (not applied):**

```python
# database/database.py — row model
key_findings = Column(JSON, nullable=True)

# database/database.py — _migrate_add_columns()
"key_findings": "JSON",

# save_session()
db_report = ResearchReportRow(..., key_findings=report.key_findings)

# get_session_by_id()
"key_findings": row.report.key_findings or [],
```

All steps are additive and nullable, so historical rows stay valid (`NULL` → `[]`).
`unsupported_citations` and `source_details` can use the same pattern if audit
requirements demand them.

**Why it was not implemented:** adding a column is a database schema change, and
this task explicitly forbids schema changes. It is also not covered by any
existing test, so it does not meet the "clearly safe and covered by tests" bar for
this freeze.

### 6.2 Stored `word_count` can disagree with the report text

**Root cause:** the JSON path trusts a positive model-supplied value.

```python
# tools/report_parsing.py:244-247
word_count = payload.get("word_count")
if not isinstance(word_count, int) or isinstance(word_count, bool) or word_count <= 0:
    word_count = len(report.split())
```

If the model reports a positive integer, it is stored verbatim. Session 23 stored
**398** while the report text contains **251** words (2,148 characters). The
Markdown fallback already computes the count correctly
(`tools/report_parsing.py:295`), so only the JSON path is affected.

Downstream impact is limited: evaluation's completeness metric recomputes the
word count from the text (`251` vs a 250 minimum) and the live report was
unaffected. The stored field is nevertheless misleading, and any analytics that
aggregate `research_reports.word_count` inherit the error.

**Recommended minimal non-destructive fix (not applied):**

```python
# tools/report_parsing.py — replace the trust-then-fallback block
word_count = len(report.split())
```

Add one regression test to `tests/test_phase2_research_quality.py`: a payload
claiming `word_count=398` with a body of 251 words must normalize to `251`. The
existing test only covers `word_count=0`
(`tests/test_phase2_research_quality.py:355,371`), so the positive-value override
has no coverage today.

**Why it was not implemented:** no existing test covers the changed path, and the
task permits implementation only when it is clearly safe **and covered by tests**.
The fix is therefore documented with a ready-to-apply patch for a follow-up task.

### 6.3 Related observations

- `save_session` also ignores `ResearchReport.generation_mode` when writing the
  report row; the mode is persisted separately on the session row, so no
  information is lost there.
- The report row is a projection of a richer Pydantic object. Any future field
  added to `ResearchReport` will be dropped unless the row, save and read paths
  are updated together. A small round-trip test would prevent recurrence.

---

## 7. Remaining production limitations

1. **No production-readiness claim.** The verified evidence is one live run plus
   mocked/unit suites. Reliability, cost control, multi-user behaviour,
   authentication, rate-limit handling and data governance are not demonstrated.
2. **Factual support is unmeasured.** Evaluation scores (overall 0.9379 on the
   verified run) are lexical and structural. `evaluation_factual_support` is
   `NULL` by design for open-topic runs, and the benchmark's claims are still
   marked `requires_manual_reference_verification`.
3. **Persistence is lossy.** `key_findings`, `unsupported_citations` and
   `source_details` are not stored; `word_count` can be wrong. See §6.
4. **Lock file still differs from the verified runtime.** The edited pin fixes
   the resolver conflict, but a fresh install resolves SQLAlchemy 2.0.43,
   psycopg2-binary 2.9.10 and pydantic 2.11.7, while this host ran 2.0.49,
   2.9.12 and 2.13.4. Only the Streamlit/Altair conflict was in scope; the other
   pins were deliberately left unchanged and remain a reproducibility caveat.
5. **README version drift.** `README.MD:11` and `README.MD:134` still advertise
   Streamlit 1.46.1, and `README.MD:371–372` recommends Python 3.10/3.11 because
   of that old pin. The requirements file now says 1.58.0 and was verified on
   Python 3.13. A short documentation pass is recommended before publishing.
6. **Single-run Editor evidence.** 734.054 s → 193.217 s is real but not a
   controlled A/B: prompt size also changed (2,355 → 1,383 tokens), and the
   baseline is not stored in the database.
7. **Search depends on third-party endpoints.** The verified run saw HTTP 429
   responses from some providers; results are availability-dependent.
8. **Upgrade warnings are outstanding.** Streamlit warns that
   `use_container_width` will be removed; LangGraph warns that the
   `allowed_objects` default will change. Both are warnings today and future
   upgrade risks.
9. **Unverified paths.** `tests/test_db_postgres.py` remains unrun, and three
   instrumentation checks (schema migration, legacy-row compatibility,
   `save_session` telemetry) were excluded from this task because they write to
   temporary SQLite databases.
10. **No CI pipeline.** Suites are standalone modules run manually; there is no
    automated regression gate for portfolio maintenance.

---

## 8. Freeze compliance

| Constraint | Status |
|---|---|
| Preserve existing modifications / untracked files | Yes — 62 pre-existing entries retained; only `requirements.txt` newly modified |
| Preserve benchmark datasets | Yes — all three hashes unchanged |
| No staging / commit / push / delete | Yes — 0 staged, no commits, no pushes, no deletions |
| No package installation or upgrade | Yes — only `pip show` metadata reads and a `--dry-run --no-cache-dir` resolution check |
| No database migrations or writes | Yes — no SQL writes; one read-only verification query |
| Never run `tests/test_db_postgres.py` | Yes |
| No full benchmark | Yes — only the structural validator |
| Secrets | `.env` values were never printed or copied |

---

## 9. Final status

**PASS** — stabilization scope complete.

What was actually achieved and verified:

- The Streamlit/Altair pin conflict is resolved by `streamlit==1.58.0` with
  `altair==6.2.1`, proven against package metadata and a full no-install
  resolution of `requirements.txt`.
- The stale `max_tokens=1500` expectation is corrected to the approved 1,200
  behavior, keeps its original guard purpose, and passes.
- All permitted suites pass: 91/91 unittest checks, 4/4 database-independent
  instrumentation checks, 15/15 dataset checks.
- Persistence gaps are root-caused with file/line evidence and concrete,
  non-destructive fixes ready for a follow-up task.
- The working tree, datasets, database and environment are preserved exactly.

**This is not a production-readiness statement.** Known limitations remain:
lossy report persistence, unmeasured factual support, single-run performance
evidence, lock-file drift for unrelated pins, README version drift, and the
excluded database-touching checks.
