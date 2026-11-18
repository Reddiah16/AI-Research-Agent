"""Phase 3 Stage 1 dataset-verification test suite.

Verifies the read-only structural/evidence validator against the real version 1
and version 2 benchmark datasets and against temporary, mutated fixtures. The
real datasets are never modified.

Run:
    python tests/test_phase3_dataset_verification.py

The suite uses only the Python standard library and does not connect to
PostgreSQL, Ollama, or DuckDuckGo, and does not execute the research pipeline.
"""

from __future__ import annotations

import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any, Callable

PROJECT_ROOT = Path(__file__).resolve().parent.parent

VALIDATOR_PATH = PROJECT_ROOT / "benchmarks" / "validate_dataset.py"
DATASET_PATH = PROJECT_ROOT / "benchmarks" / "datasets" / "benchmark_v1.json"
V2_DATASET_PATH = PROJECT_ROOT / "benchmarks" / "datasets" / "benchmark_v2.json"
PHASE1_REPORT = PROJECT_ROOT / "docs" / "PHASE_1_RESEARCH_AGENT_AUDIT_REPORT.md"
PHASE2_REPORT = PROJECT_ROOT / "docs" / "PHASE_2_IMPLEMENTATION_REPORT.md"
V1_SHA256 = "4f1cb7f5ca19e1c58e10de714844fd042ffdd70ac4f8321e80046a6cd52573ec"
V2_SHA256 = "5f859b81be45b787b5a6108378a3b9abe0584e4697ad3d8def0f65a49a5b779f"
V3_DATASET_PATH = PROJECT_ROOT / "benchmarks" / "datasets" / "benchmark_v3.json"
V3_SHA256 = "10a5ba16c400a1cac0040e4357df49d97fc09e258f006999df96c93df2ff2527"


def _load_validator_module() -> Any:
    """Load benchmarks/validate_dataset.py without requiring package metadata."""
    spec = importlib.util.spec_from_file_location(
        "phase3_dataset_validator_under_test", VALIDATOR_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load validator module from {VALIDATOR_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


VALIDATOR = _load_validator_module()

with DATASET_PATH.open("r", encoding="utf-8") as _handle:
    BASE_DATASET: dict[str, Any] = json.load(_handle)

with V2_DATASET_PATH.open("r", encoding="utf-8") as _handle:
    BASE_V2_DATASET: dict[str, Any] = json.load(_handle)

with V3_DATASET_PATH.open("r", encoding="utf-8") as _handle:
    BASE_V3_DATASET: dict[str, Any] = json.load(_handle)


def _run_validator(dataset_path: Path) -> Any:
    """Run the validator directly against a dataset path."""
    return VALIDATOR.DatasetValidator(
        dataset_path=dataset_path.resolve(),
        phase1_path=PHASE1_REPORT,
        phase2_path=PHASE2_REPORT,
    ).run()


def _validate_mutated(mutator: Callable[[dict[str, Any]], None]) -> Any:
    """Write a deep-copied, mutated dataset to a temporary file and validate it."""
    fixture = copy.deepcopy(BASE_DATASET)
    mutator(fixture)

    with tempfile.TemporaryDirectory(prefix="phase3_dataset_test_") as temp_dir:
        fixture_path = Path(temp_dir) / "benchmark_fixture.json"
        fixture_path.write_text(
            json.dumps(fixture, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return _run_validator(fixture_path)


def _validate_mutated_v2(mutator: Callable[[dict[str, Any]], None]) -> Any:
    """Write a deep-copied, mutated version 2 dataset to a temporary file."""
    fixture = copy.deepcopy(BASE_V2_DATASET)
    mutator(fixture)

    with tempfile.TemporaryDirectory(prefix="phase3_dataset_v2_test_") as temp_dir:
        fixture_path = Path(temp_dir) / "benchmark_v2_fixture.json"
        fixture_path.write_text(
            json.dumps(fixture, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return _run_validator(fixture_path)


def _validate_mutated_v3(mutator: Callable[[dict[str, Any]], None]) -> Any:
    """Write a deep-copied, mutated version 3 dataset to a temporary file."""
    fixture = copy.deepcopy(BASE_V3_DATASET)
    mutator(fixture)

    with tempfile.TemporaryDirectory(prefix="phase3_dataset_v3_test_") as temp_dir:
        fixture_path = Path(temp_dir) / "benchmark_v3_fixture.json"
        fixture_path.write_text(
            json.dumps(fixture, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return _run_validator(fixture_path)


def _check(result: Any, name: str) -> Any:
    matches = [check for check in result.checks if check.name == name]
    assert len(matches) == 1, f"Expected exactly one check named {name!r}; found {len(matches)}"
    return matches[0]


def _failure_summary(result: Any) -> str:
    failed = [
        f"{check.name}: {'; '.join(check.issues)}"
        for check in result.checks
        if not check.passed
    ]
    return " | ".join(failed) if failed else "no failed checks reported"


def _assert_failed(result: Any, check_name: str) -> None:
    assert not result.passed, (
        f"Expected validation to fail for check {check_name!r}, but it passed"
    )
    check = _check(result, check_name)
    assert not check.passed, (
        f"Expected check {check_name!r} to fail; it passed. "
        f"Failed checks: {_failure_summary(result)}"
    )
    assert check.issues, f"Check {check_name!r} failed without an explanatory issue"


def test_actual_dataset_passes_all_structural_checks() -> None:
    result = _run_validator(DATASET_PATH)

    assert result.passed, f"Actual dataset failed validation: {_failure_summary(result)}"
    assert result.questions_checked == 40, (
        f"Expected 40 questions; got {result.questions_checked}"
    )
    assert result.claims_checked == 120, (
        f"Expected 120 claims; got {result.claims_checked}"
    )
    assert result.source_domains_checked == 120, (
        f"Expected 120 source domains; got {result.source_domains_checked}"
    )
    assert len(result.checks) == 15, (
        f"Expected 15 structural checks; got {len(result.checks)}"
    )
    assert all(check.passed for check in result.checks), _failure_summary(result)

    with contextlib.redirect_stdout(io.StringIO()):
        exit_code = VALIDATOR.main(["--dataset", str(DATASET_PATH)])
    assert exit_code == 0, f"Expected CLI exit code 0; got {exit_code}"


def test_missing_dataset_path_returns_failure() -> None:
    with tempfile.TemporaryDirectory(prefix="phase3_dataset_test_") as temp_dir:
        missing_path = Path(temp_dir) / "missing_benchmark.json"
        assert not missing_path.exists()

        result = _run_validator(missing_path)
        _assert_failed(result, "json_parse")

        with contextlib.redirect_stdout(io.StringIO()):
            exit_code = VALIDATOR.main(["--dataset", str(missing_path)])
        assert exit_code == 1, f"Expected CLI exit code 1 for missing dataset; got {exit_code}"


def test_duplicate_question_ids_detected() -> None:
    def mutate(data: dict[str, Any]) -> None:
        data["benchmark_questions"][1]["id"] = data["benchmark_questions"][0]["id"]

    result = _validate_mutated(mutate)
    _assert_failed(result, "id_sequence")


def test_missing_questions_detected() -> None:
    def mutate(data: dict[str, Any]) -> None:
        data["benchmark_questions"].pop()

    result = _validate_mutated(mutate)
    _assert_failed(result, "question_count")


def test_duplicate_topics_detected() -> None:
    def mutate(data: dict[str, Any]) -> None:
        data["benchmark_questions"][1]["topic"] = data["benchmark_questions"][0]["topic"]

    result = _validate_mutated(mutate)
    _assert_failed(result, "topic_uniqueness")


def test_invalid_taxonomy_values_detected() -> None:
    cases = (
        ("domain", "XX"),
        ("difficulty", "Expert"),
        ("task_type", "Survey"),
    )
    for field, invalid_value in cases:
        def mutate(
            data: dict[str, Any],
            field: str = field,
            invalid_value: str = invalid_value,
        ) -> None:
            data["benchmark_questions"][0][field] = invalid_value

        result = _validate_mutated(mutate)
        _assert_failed(result, "taxonomy_values")


def test_missing_required_metadata_detected() -> None:
    def mutate(data: dict[str, Any]) -> None:
        del data["benchmark_questions"][0]["metadata"]["target_aspects"]

    result = _validate_mutated(mutate)
    _assert_failed(result, "metadata_schema")


def test_missing_ground_truth_claims_detected() -> None:
    def mutate(data: dict[str, Any]) -> None:
        data["benchmark_questions"][0]["ground_truth"][
            "provisional_reference_claims"
        ] = []

    result = _validate_mutated(mutate)
    _assert_failed(result, "ground_truth_claims")


def test_invalid_verification_flags_detected() -> None:
    cases: tuple[Callable[[dict[str, Any]], None], ...] = (
        lambda data: data["benchmark_questions"][0]["ground_truth"].update(
            {"is_verified": True}
        ),
        lambda data: data["benchmark_questions"][0]["ground_truth"].update(
            {"verification_status": "verified"}
        ),
        lambda data: data["benchmark_questions"][0]["ground_truth"].update(
            {"is_verified": "yes"}
        ),
    )
    for mutate in cases:
        result = _validate_mutated(mutate)
        _assert_failed(result, "verification_state")


def test_inconsistent_verification_metadata_detected() -> None:
    cases: tuple[Callable[[dict[str, Any]], None], ...] = (
        # A provisional item must not carry a verifier identity.
        lambda data: data["benchmark_questions"][0]["ground_truth"].update(
            {"verified_by": "Reviewer"}
        ),
        # A verified item must carry a verifier identity.
        lambda data: data["benchmark_questions"][0]["ground_truth"].update(
            {
                "is_verified": True,
                "verification_status": "verified",
                "verified_by": None,
            }
        ),
        # A provisional item must not claim a verification timestamp.
        lambda data: data["benchmark_questions"][0]["ground_truth"].update(
            {"verified_at": "2026-11-12T00:00:00Z"}
        ),
    )
    for mutate in cases:
        result = _validate_mutated(mutate)
        _assert_failed(result, "verification_state")


def test_missing_expected_source_domains_detected() -> None:
    def mutate(data: dict[str, Any]) -> None:
        data["benchmark_questions"][0]["metadata"]["expected_source_domains"].pop()

    result = _validate_mutated(mutate)
    _assert_failed(result, "expected_source_domains")


def test_invalid_evaluation_rubrics_detected() -> None:
    cases: tuple[Callable[[dict[str, Any]], None], ...] = (
        # Missing mandatory report section.
        lambda data: data["benchmark_questions"][0]["evaluation_rubric"].update(
            {
                "structural_requirements": [
                    "Introduction",
                    "Key Points",
                    "Applications",
                    "Challenges",
                    "Conclusion",
                ]
            }
        ),
        # Below the approved minimum word count.
        lambda data: data["benchmark_questions"][0]["evaluation_rubric"].update(
            {"minimum_word_count": 100}
        ),
        # Missing scoring criterion.
        lambda data: data["benchmark_questions"][0]["evaluation_rubric"][
            "scoring_criteria"
        ].pop("source_quality"),
    )
    for mutate in cases:
        result = _validate_mutated(mutate)
        _assert_failed(result, "evaluation_rubric")


def _question_by_id(dataset: dict[str, Any], question_id: str) -> dict[str, Any]:
    for question in dataset["benchmark_questions"]:
        if question.get("id") == question_id:
            return question
    raise AssertionError(f"Fixture is missing question {question_id}")


def _claim_by_id(dataset: dict[str, Any], claim_id: str) -> dict[str, Any]:
    for question in dataset["benchmark_questions"]:
        for claim in question["ground_truth"]["claims"]:
            if claim.get("claim_id") == claim_id:
                return claim
    raise AssertionError(f"Fixture is missing claim {claim_id}")


def test_v2_dataset_passes_all_checks() -> None:
    result = _run_validator(V2_DATASET_PATH)

    assert result.passed, f"Version 2 dataset failed validation: {_failure_summary(result)}"
    assert result.dataset_version == "2.0.0", result.dataset_version
    assert result.questions_checked == 40
    assert result.claims_checked == 120
    assert result.sources_checked == 86
    assert result.v2_questions_verified == 23
    assert len(result.checks) == 20, (
        f"Expected 20 checks for version 2; got {len(result.checks)}"
    )
    assert result.claim_status_counts == {
        "verified": 97,
        "partially_supported": 19,
        "contradicted": 3,
        "needs_more_evidence": 1,
    }, result.claim_status_counts

    with contextlib.redirect_stdout(io.StringIO()):
        exit_code = VALIDATOR.main(["--dataset", str(V2_DATASET_PATH)])
    assert exit_code == 0, f"Expected version 2 CLI exit code 0; got {exit_code}"


def test_v1_and_v2_version_detection() -> None:
    v1_result = _run_validator(DATASET_PATH)
    v2_result = _run_validator(V2_DATASET_PATH)

    assert v1_result.dataset_version == "1.0.0"
    assert len(v1_result.checks) == 15
    assert v1_result.sources_checked == 0
    assert not v1_result.claim_status_counts
    assert v1_result.v2_questions_verified == 0

    assert v2_result.dataset_version == "2.0.0"
    assert len(v2_result.checks) == 20
    assert v2_result.sources_checked == 86
    assert v2_result.claim_status_counts


def test_v2_preserves_v1_content_and_hash() -> None:
    actual_hash = hashlib.sha256(DATASET_PATH.read_bytes()).hexdigest()
    assert actual_hash == V1_SHA256, "benchmark_v1.json has been modified"
    assert BASE_V2_DATASET["verification_metadata"]["v1_sha256"] == V1_SHA256

    v1_questions = {question["id"]: question for question in BASE_DATASET["benchmark_questions"]}
    v2_questions = {question["id"]: question for question in BASE_V2_DATASET["benchmark_questions"]}
    assert set(v1_questions) == set(v2_questions)

    for question_id, v1_question in v1_questions.items():
        v2_question = v2_questions[question_id]
        for field in ("domain", "difficulty", "task_type", "topic", "metadata", "evaluation_rubric"):
            assert v2_question[field] == v1_question[field], (
                f"{question_id}: field {field!r} changed between v1 and v2"
            )
        assert (
            v2_question["ground_truth"]["provisional_reference_claims"]
            == v1_question["ground_truth"]["provisional_reference_claims"]
        )
        assert [
            claim["claim_text"] for claim in v2_question["ground_truth"]["claims"]
        ] == v1_question["ground_truth"]["provisional_reference_claims"]




def test_v3_contradicted_claims_resolved() -> None:
    for claim_id in ("Q12-C1", "Q15-C3", "Q34-C2"):
        claim = _claim_by_id(BASE_V3_DATASET, claim_id)
        assert claim["status"] == "verified", f"{claim_id}: expected verified, got {claim['status']!r}"
        assert claim["resolution"] == "corrected_and_verified", f"{claim_id}: expected corrected_and_verified"
        assert claim["corrected_claim_text"] is not None, f"{claim_id}: missing corrected wording"
        assert claim["corrected_claim_text"] != claim["claim_text"], f"{claim_id}: corrected wording must differ from original"
        assert claim["correction_evidence"] is not None, f"{claim_id}: missing correction_evidence"
        assert claim["original_status"] == "contradicted", f"{claim_id}: original_status must be contradicted"


def test_v3_q17_c2_needs_more_evidence() -> None:
    claim = _claim_by_id(BASE_V3_DATASET, "Q17-C2")
    assert claim["status"] == "needs_more_evidence", f"Q17-C2: expected needs_more_evidence, got {claim['status']!r}"
    assert claim["resolution"] == "retained_needs_more_evidence", f"Q17-C2: expected retained_needs_more_evidence"
    assert claim["corrected_claim_text"] is None, f"Q17-C2: downgraded claim must not carry corrected wording"
    assert claim["exact_wording_met"] is False, f"Q17-C2: exact_wording_met must be False"


def test_v3_claim_wording_audit() -> None:
    for question in BASE_V3_DATASET["benchmark_questions"]:
        for claim in question["ground_truth"]["claims"]:
            cid = claim["claim_id"]
            assert "original_status" in claim, f"{cid}: missing original_status"
            assert "corrected_claim_text" in claim, f"{cid}: missing corrected_claim_text"
            assert "effective_claim_text" in claim, f"{cid}: missing effective_claim_text"
            assert "resolution" in claim, f"{cid}: missing resolution"
            assert claim["resolution"] in {
                "confirmed_verified",
                "retained_partially_supported",
                "retained_needs_more_evidence",
                "corrected_and_verified",
                "downgraded_needs_more_evidence",
            }, f"{cid}: invalid resolution {claim['resolution']!r}"
            assert "review_history" in claim, f"{cid}: missing review_history"
            assert isinstance(claim["review_history"], list), f"{cid}: review_history must be a list"
            assert len(claim["review_history"]) >= 2, f"{cid}: review_history must have >= 2 entries"




def test_v3_exact_wording_evidence() -> None:
    for question in BASE_V3_DATASET["benchmark_questions"]:
        for claim in question["ground_truth"]["claims"]:
            cid = claim["claim_id"]
            if claim["status"] == "verified":
                assert claim["exact_wording_met"] is True, f"{cid}: verified claim must meet exact wording"
                assert claim["exact_wording_assessment"], f"{cid}: missing exact_wording_assessment"
                gap = claim.get("contradiction_or_gap") or ""
                assert gap == "", f"{cid}: verified claim must not carry contradiction_or_gap"
            else:
                assert claim["exact_wording_met"] is False, f"{cid}: unresolved claim must not meet exact wording"


def test_v3_review_history_present() -> None:
    for question in BASE_V3_DATASET["benchmark_questions"]:
        for claim in question["ground_truth"]["claims"]:
            cid = claim["claim_id"]
            history = claim["review_history"]
            assert len(history) >= 2, f"{cid}: review_history must have at least 2 entries"
            v3_entry = history[-1]
            assert v3_entry["status_after"] == claim["status"], f"{cid}: v3 entry status_after mismatch"


def test_v3_source_verification_recorded() -> None:
    for source in BASE_V3_DATASET["sources"]:
        sid = source["source_id"]
        assert "verification_status" in source, f"{sid}: missing verification_status"
        assert source["verification_status"] in {
            "verified_in_repository_record",
            "insufficiently_verified",
        }, f"{sid}: invalid verification_status {source['verification_status']!r}"
        assert "v3_review_note" in source, f"{sid}: missing v3_review_note"
        assert source["v3_review_note"], f"{sid}: v3_review_note must be non-empty"
        if sid == "S053":
            assert source["verification_status"] == "insufficiently_verified", f"{sid}: S053 must be insufficiently_verified"
            assert "bibliographic_gaps" in source, f"{sid}: S053 must carry bibliographic_gaps"


def test_v3_source_domain_reconciliation() -> None:
    for question in BASE_V3_DATASET["benchmark_questions"]:
        qid = question["id"]
        assert "metadata" in question, f"{qid}: missing metadata"
        assert "source_domain_audit" in question["metadata"], f"{qid}: missing source_domain_audit"
        audit = question["metadata"]["source_domain_audit"]
        assert "expected_source_domains" in audit, f"{qid}: missing expected_source_domains"
        assert "actual_source_domains" in audit, f"{qid}: missing actual_source_domains"
        assert "outside_expected_source_domains" in audit, f"{qid}: missing outside_expected_source_domains"
        assert "status" in audit, f"{qid}: missing status"
        assert audit["status"] in {
            "within_expected_domains",
            "reconciled_with_documented_substitutions",
        }, f"{qid}: invalid domain audit status {audit['status']!r}"


def test_v3_metadata_consistency() -> None:
    meta = BASE_V3_DATASET["verification_metadata"]
    claims = [c for q in BASE_V3_DATASET["benchmark_questions"] for c in q["ground_truth"]["claims"]]
    resolution_counts = {}
    for c in claims:
        resolution_counts[c["resolution"]] = resolution_counts.get(c["resolution"], 0) + 1

    assert meta["claims_total"] == 120
    assert meta["claims_by_resolution"] == resolution_counts
    assert meta["claims_exact_wording_met"] + meta["claims_exact_wording_not_met"] == 120
    assert meta["questions_total"] == 40
    assert sum(meta["questions_by_status"].values()) == 40
    assert meta["sources_total"] == 86
    assert meta["source_domain_mismatch_questions"] >= 0
    assert meta["source_domain_mismatch_claims"] >= 0

def test_v2_all_claims_have_resolvable_evidence() -> None:
    source_ids = {source["source_id"] for source in BASE_V2_DATASET["sources"]}
    statuses = {
        "verified",
        "partially_supported",
        "contradicted",
        "needs_more_evidence",
        "time_sensitive",
    }
    total = 0
    for question in BASE_V2_DATASET["benchmark_questions"]:
        for claim in question["ground_truth"]["claims"]:
            total += 1
            assert claim["status"] in statuses, claim
            assert claim["source_ids"], claim["claim_id"]
            assert set(claim["source_ids"]) <= source_ids, claim["claim_id"]
            assert claim["evidence_summary"].strip(), claim["claim_id"]
            assert claim["support_explanation"].strip(), claim["claim_id"]
            assert claim["verifier"].strip(), claim["claim_id"]
            assert claim["verified_at"].strip(), claim["claim_id"]
            if claim["status"] == "verified":
                assert claim["contradiction_or_gap"] is None, claim["claim_id"]
                assert claim["suggested_correction"] is None, claim["claim_id"]
            else:
                assert claim["contradiction_or_gap"], claim["claim_id"]
    assert total == 120


def test_v2_missing_evidence_is_rejected() -> None:
    def mutate(data: dict[str, Any]) -> None:
        del _claim_by_id(data, "Q01-C1")["evidence_summary"]

    result = _validate_mutated_v2(mutate)
    _assert_failed(result, "v2_claim_evidence")


def test_v2_unknown_source_id_is_rejected() -> None:
    def mutate(data: dict[str, Any]) -> None:
        _claim_by_id(data, "Q01-C1")["source_ids"] = ["S999"]

    result = _validate_mutated_v2(mutate)
    _assert_failed(result, "v2_claim_evidence")


def test_v2_partial_claim_requires_gap_and_correction() -> None:
    def mutate(data: dict[str, Any]) -> None:
        claim = _claim_by_id(data, "Q07-C3")
        claim["contradiction_or_gap"] = None

    result = _validate_mutated_v2(mutate)
    _assert_failed(result, "v2_claim_evidence")


def test_v2_contradicted_claim_requires_correction() -> None:
    def mutate(data: dict[str, Any]) -> None:
        claim = _claim_by_id(data, "Q12-C1")
        claim["suggested_correction"] = None

    result = _validate_mutated_v2(mutate)
    _assert_failed(result, "v2_claim_evidence")


def test_v2_unresolved_question_cannot_be_verified() -> None:
    def mutate(data: dict[str, Any]) -> None:
        ground_truth = _question_by_id(data, "Q07")["ground_truth"]
        ground_truth["verification_status"] = "verified"
        ground_truth["is_verified"] = True
        ground_truth["verified_by"] = "fixture"
        ground_truth["verified_at"] = "2026-11-12T00:00:00Z"

    result = _validate_mutated_v2(mutate)
    _assert_failed(result, "v2_question_aggregation")


def test_v2_claim_wording_must_match_v1() -> None:
    def mutate(data: dict[str, Any]) -> None:
        _claim_by_id(data, "Q01-C1")["claim_text"] += " (edited)"

    result = _validate_mutated_v2(mutate)
    _assert_failed(result, "v2_claim_records")


def test_v2_metadata_totals_must_match() -> None:
    def mutate(data: dict[str, Any]) -> None:
        data["verification_metadata"]["claims_total"] = 119

    result = _validate_mutated_v2(mutate)
    _assert_failed(result, "v2_verification_metadata")


def test_v2_missing_source_registry_is_rejected() -> None:
    def mutate(data: dict[str, Any]) -> None:
        data["sources"].pop()

    result = _validate_mutated_v2(mutate)
    _assert_failed(result, "v2_claim_evidence")


def test_v2_taxonomy_still_enforced() -> None:
    def mutate(data: dict[str, Any]) -> None:
        data["benchmark_questions"][0]["domain"] = "XX"

    result = _validate_mutated_v2(mutate)
    _assert_failed(result, "taxonomy_values")



def test_v3_dataset_passes_all_checks() -> None:
    result = _run_validator(V3_DATASET_PATH)
    assert result.passed, f"Version 3 dataset failed validation: {_failure_summary(result)}"
    assert result.dataset_version == "3.0.0", result.dataset_version
    assert result.questions_checked == 40
    assert result.claims_checked == 120
    assert result.sources_checked == 86
    assert result.v2_questions_verified == 25
    assert len(result.checks) == 29, (
        f"Expected 29 checks for version 3; got {len(result.checks)}"
    )
    assert result.claim_status_counts == {
        "verified": 100,
        "partially_supported": 18,
        "needs_more_evidence": 2,
    }, result.claim_status_counts


def test_v3_distinct_from_version_2() -> None:
    assert BASE_V3_DATASET["version"] == "3.0.0"
    assert BASE_V3_DATASET["supersedes"] == "benchmark_v2.json"
    assert BASE_V3_DATASET["verification_metadata"]["v2_sha256"] == V2_SHA256
    assert BASE_V3_DATASET["verification_metadata"]["v1_sha256"] == V1_SHA256


def test_v3_preserves_v1_content_and_hash() -> None:
    actual_hash = hashlib.sha256(DATASET_PATH.read_bytes()).hexdigest()
    assert actual_hash == V1_SHA256, "benchmark_v1.json has been modified"
    v1_questions = {question["id"]: question for question in BASE_DATASET["benchmark_questions"]}
    v3_questions = {question["id"]: question for question in BASE_V3_DATASET["benchmark_questions"]}
    assert set(v1_questions) == set(v3_questions)
    for question_id, v1_question in v1_questions.items():
        v3_question = v3_questions[question_id]
        for field in ("domain", "difficulty", "task_type", "topic", "evaluation_rubric"):
            assert v3_question[field] == v1_question[field], (
                f"{question_id}: field {field!r} changed between v1 and v3"
            )
        # metadata may gain source_domain_audit in v3; check original sub-fields
        v1_meta = v1_question["metadata"]
        v3_meta = v3_question["metadata"]
        for mfield in ("domain_full_name", "target_aspects", "expected_source_domains"):
            assert v3_meta[mfield] == v1_meta[mfield], (
                f"{question_id}: metadata.{mfield} changed between v1 and v3"
            )
        assert (
            v3_question["ground_truth"]["provisional_reference_claims"]
            == v1_question["ground_truth"]["provisional_reference_claims"]
        )
        assert [
            claim["claim_text"] for claim in v3_question["ground_truth"]["claims"]
        ] == v1_question["ground_truth"]["provisional_reference_claims"]




def test_v3_hash_is_frozen() -> None:
    actual_hash = hashlib.sha256(V3_DATASET_PATH.read_bytes()).hexdigest()
    assert actual_hash == V3_SHA256, "benchmark_v3.json has changed since remediation"


def test_v3_effective_wording_must_match_correction() -> None:
    def mutate(dataset: dict[str, Any]) -> None:
        claim = _claim_by_id(dataset, "Q01-C1")
        claim["effective_claim_text"] = claim["claim_text"] + " Extra unsupported clause."

    result = _validate_mutated_v3(mutate)
    _assert_failed(result, "v3_claim_wording_audit")


def test_v3_correction_evidence_must_be_cited() -> None:
    def mutate(dataset: dict[str, Any]) -> None:
        claim = _claim_by_id(dataset, "Q01-C2")
        claim["correction_evidence"] = ["S999"]

    result = _validate_mutated_v3(mutate)
    _assert_failed(result, "v3_claim_evidence_integrity")


def test_v3_review_history_must_match_status() -> None:
    def mutate(dataset: dict[str, Any]) -> None:
        claim = _claim_by_id(dataset, "Q01-C1")
        claim["review_history"][-1]["status_after"] = "partially_supported"

    result = _validate_mutated_v3(mutate)
    _assert_failed(result, "v3_review_history")


def test_v3_domain_audit_must_match_citations() -> None:
    def mutate(dataset: dict[str, Any]) -> None:
        question = dataset["benchmark_questions"][0]
        question["metadata"]["source_domain_audit"]["outside_expected_source_domains"] = []

    result = _validate_mutated_v3(mutate)
    _assert_failed(result, "v3_source_domain_reconciliation")


def test_v3_metadata_totals_must_match_records() -> None:
    def mutate(dataset: dict[str, Any]) -> None:
        dataset["verification_metadata"]["claims_corrected"] += 1

    result = _validate_mutated_v3(mutate)
    _assert_failed(result, "v3_metadata_consistency")


def test_v3_insufficient_source_cannot_be_sole_evidence() -> None:
    def mutate(dataset: dict[str, Any]) -> None:
        claim = _claim_by_id(dataset, "Q01-C1")
        claim["source_ids"] = ["S053"]

    result = _validate_mutated_v3(mutate)
    _assert_failed(result, "v3_source_verification")


def test_v3_lineage_is_required() -> None:
    def mutate(dataset: dict[str, Any]) -> None:
        dataset.pop("supersedes")

    result = _validate_mutated_v3(mutate)
    _assert_failed(result, "v3_version_integrity")


TESTS: tuple[tuple[str, Callable[[], None]], ...] = (
    ("Actual dataset passes all structural checks", test_actual_dataset_passes_all_structural_checks),
    ("Missing dataset path returns failure", test_missing_dataset_path_returns_failure),
    ("Duplicate question IDs are detected", test_duplicate_question_ids_detected),
    ("Missing questions are detected", test_missing_questions_detected),
    ("Duplicate topics are detected", test_duplicate_topics_detected),
    ("Invalid taxonomy values are detected", test_invalid_taxonomy_values_detected),
    ("Missing required metadata is detected", test_missing_required_metadata_detected),
    ("Missing ground-truth claims are detected", test_missing_ground_truth_claims_detected),
    ("Invalid verification flags are detected", test_invalid_verification_flags_detected),
    ("Inconsistent verification metadata is detected", test_inconsistent_verification_metadata_detected),
    ("Missing expected source domains are detected", test_missing_expected_source_domains_detected),
    ("Invalid evaluation rubrics are detected", test_invalid_evaluation_rubrics_detected),
    ("Version 2 dataset passes all checks", test_v2_dataset_passes_all_checks),
    ("Version 1 and version 2 are detected distinctly", test_v1_and_v2_version_detection),
    ("Version 2 preserves version 1 content and hash", test_v2_preserves_v1_content_and_hash),
    ("Version 2 claims have resolvable evidence", test_v2_all_claims_have_resolvable_evidence),
    ("Version 2 missing evidence is rejected", test_v2_missing_evidence_is_rejected),
    ("Version 2 unknown source IDs are rejected", test_v2_unknown_source_id_is_rejected),
    ("Version 2 partial claims require a gap and correction", test_v2_partial_claim_requires_gap_and_correction),
    ("Version 2 contradicted claims require a correction", test_v2_contradicted_claim_requires_correction),
    ("Version 2 unresolved questions cannot be verified", test_v2_unresolved_question_cannot_be_verified),
    ("Version 2 claim wording must match version 1", test_v2_claim_wording_must_match_v1),
    ("Version 2 metadata totals must match", test_v2_metadata_totals_must_match),
    ("Version 2 missing source registry entries are rejected", test_v2_missing_source_registry_is_rejected),
    ("Version 2 taxonomy is still enforced", test_v2_taxonomy_still_enforced),
    # --- Version 3 tests ---
    ("Version 3 dataset passes all checks", test_v3_dataset_passes_all_checks),
    ("Version 3 is distinct from version 2", test_v3_distinct_from_version_2),
    ("Version 3 preserves v1 content and hash", test_v3_preserves_v1_content_and_hash),
    ("Version 3 contradicted claims are resolved", test_v3_contradicted_claims_resolved),
    ("Version 3 Q17-C2 remains needs_more_evidence", test_v3_q17_c2_needs_more_evidence),
    ("Version 3 claim wording audit", test_v3_claim_wording_audit),
    ("Version 3 exact wording evidence", test_v3_exact_wording_evidence),
    ("Version 3 review history present", test_v3_review_history_present),
    ("Version 3 source verification recorded", test_v3_source_verification_recorded),
    ("Version 3 source domain reconciliation", test_v3_source_domain_reconciliation),
    ("Version 3 metadata consistency", test_v3_metadata_consistency),
    ("Version 3 hash is frozen", test_v3_hash_is_frozen),
    ("Version 3 effective wording must match correction", test_v3_effective_wording_must_match_correction),
    ("Version 3 correction evidence must be cited", test_v3_correction_evidence_must_be_cited),
    ("Version 3 review history must match status", test_v3_review_history_must_match_status),
    ("Version 3 domain audit must match citations", test_v3_domain_audit_must_match_citations),
    ("Version 3 metadata totals must match records", test_v3_metadata_totals_must_match_records),
    ("Version 3 insufficient source cannot be sole evidence", test_v3_insufficient_source_cannot_be_sole_evidence),
    ("Version 3 lineage is required", test_v3_lineage_is_required),
)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    print("=" * 72)
    print("Phase 3 Stage 1 - Dataset Verification Test Suite")
    print("=" * 72)
    print(f"Validator: {VALIDATOR_PATH}")
    print(f"Dataset  : {DATASET_PATH}")
    print(f"Dataset  : {V2_DATASET_PATH}")
    print("-" * 72)

    passed = 0
    failed = 0
    failures: list[str] = []

    for index, (name, test) in enumerate(TESTS, 1):
        try:
            test()
        except Exception as exc:  # noqa: BLE001 - test harness must isolate failures
            failed += 1
            failures.append(f"{name}: {exc}")
            print(f"[FAIL] {index:02d}. {name}")
            print(traceback.format_exc())
        else:
            passed += 1
            print(f"[PASS] {index:02d}. {name}")

    print("-" * 72)
    print(f"Tests: {passed} passed, {failed} failed")
    if failures:
        print("Failures:")
        for failure in failures:
            print(f"  - {failure}")
    print(f"RESULT: {'PASS' if failed == 0 else 'FAIL'}")
    print("=" * 72)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
