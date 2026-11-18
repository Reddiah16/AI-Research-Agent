"""Golden evaluation dataset loading, validation, and engine adapters.

The dataset is newline-delimited JSON. Validation is read-only and never
rewrites the dataset. Missing retrieval labels are reported explicitly and are
never converted into zero performance.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence
from urllib.parse import urlparse

__all__ = [
    "DEFAULT_GOLDEN_DATASET",
    "DEFAULT_GOLDEN_MANIFEST",
    "VALID_CATEGORIES",
    "VALID_DIFFICULTIES",
    "load_golden_dataset",
    "summarise_golden_dataset",
    "to_engine_question",
    "validate_golden_dataset",
]

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_GOLDEN_DATASET = (
    PROJECT_ROOT / "benchmarks" / "datasets" / "golden_eval_v1.jsonl"
)
DEFAULT_GOLDEN_MANIFEST = (
    PROJECT_ROOT / "benchmarks" / "datasets" / "golden_eval_v1.manifest.json"
)

VALID_CATEGORIES = (
    "factual_research",
    "multi_source_research",
    "comparison",
    "technical_research",
    "recent_information",
    "insufficient_evidence",
    "conflicting_sources",
    "misleading_assumption",
)
VALID_DIFFICULTIES = ("Easy", "Intermediate", "Difficult")
VALID_PROVENANCE = ("verified_reference", "synthetic")

_REQUIRED_FIELDS = (
    "id",
    "question",
    "category",
    "difficulty",
    "provenance",
    "expected_key_findings",
    "reference_evidence",
    "expected_source_domains",
    "retrieval_ground_truth",
    "evaluation_rubric",
    "expected_behavior",
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalise_question(value: str) -> str:
    return " ".join(str(value or "").lower().split())


def _is_domain(value: str) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    if "://" in value or "/" in value:
        return False
    return "." in value and not value.startswith(".")


def load_golden_dataset(
    path: str | Path = DEFAULT_GOLDEN_DATASET,
    manifest_path: str | Path = DEFAULT_GOLDEN_MANIFEST,
) -> Dict[str, Any]:
    """Load and validate a JSONL golden dataset.

    Returns:
        ``{"path", "sha256", "manifest", "cases"}``.

    Raises:
        FileNotFoundError: when the dataset or manifest is missing.
        ValueError: when a line is not a JSON object or validation fails.
    """
    dataset_path = Path(path)
    manifest_file = Path(manifest_path)
    if not dataset_path.exists():
        raise FileNotFoundError(f"golden dataset not found: {dataset_path}")

    cases: List[Dict[str, Any]] = []
    with dataset_path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                case = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"invalid JSON on line {line_number}: {exc}"
                ) from exc
            if not isinstance(case, dict):
                raise ValueError(f"line {line_number} is not a JSON object")
            cases.append(case)

    manifest: Dict[str, Any] = {}
    if manifest_file.exists():
        with manifest_file.open("r", encoding="utf-8") as handle:
            manifest = json.load(handle)

    validation = validate_golden_dataset(cases)
    if not validation["ok"]:
        raise ValueError(
            "golden dataset validation failed: " + "; ".join(validation["errors"])
        )

    return {
        "path": str(dataset_path),
        "filename": dataset_path.name,
        "sha256": _sha256_file(dataset_path),
        "manifest": manifest,
        "cases": cases,
        "validation": validation,
    }


def _validate_verified_case(case: Dict[str, Any], errors: List[str], warnings: List[str]) -> None:
    case_id = case.get("id")
    findings = case.get("expected_key_findings")
    evidence = case.get("reference_evidence")
    labels = case.get("retrieval_ground_truth") or {}
    if not isinstance(findings, list) or not findings:
        errors.append(f"{case_id}: verified case has no expected_key_findings")
        return
    if not isinstance(evidence, list) or not evidence:
        errors.append(f"{case_id}: verified case has no reference_evidence")
        return

    evidence_ids = {
        str(record.get("source_id"))
        for record in evidence
        if isinstance(record, dict) and record.get("source_id")
    }
    for finding in findings:
        if not isinstance(finding, dict):
            errors.append(f"{case_id}: expected finding is not an object")
            continue
        if not str(finding.get("text") or "").strip():
            errors.append(f"{case_id}: expected finding has empty text")
        if finding.get("verification_status") != "verified":
            errors.append(f"{case_id}: verified case contains a non-verified finding")
        source_ids = finding.get("source_ids")
        if not isinstance(source_ids, list) or not source_ids:
            errors.append(f"{case_id}: expected finding has no source_ids")
        else:
            unknown = [sid for sid in source_ids if str(sid) not in evidence_ids]
            if unknown:
                errors.append(
                    f"{case_id}: expected finding references unknown evidence ids {unknown}"
                )

    if labels.get("status") != "available":
        errors.append(f"{case_id}: verified case must have retrieval labels")
    if not labels.get("relevant_urls"):
        errors.append(f"{case_id}: verified case has no relevant_urls")
    if labels.get("label_quality") == "mixed":
        warnings.append(f"{case_id}: retrieval labels include non-verified sources")


def _validate_synthetic_case(case: Dict[str, Any], errors: List[str]) -> None:
    case_id = case.get("id")
    if case.get("expected_key_findings"):
        errors.append(f"{case_id}: synthetic case must not contain reference findings")
    if case.get("reference_evidence"):
        errors.append(f"{case_id}: synthetic case must not contain reference evidence")
    if not str(case.get("expected_behavior") or "").strip():
        errors.append(f"{case_id}: synthetic case has no expected_behavior")
    labels = case.get("retrieval_ground_truth") or {}
    if labels.get("status") != "unavailable":
        errors.append(f"{case_id}: synthetic case must mark retrieval labels unavailable")


def validate_golden_dataset(cases: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Validate the golden dataset without modifying it.

    The validation is deliberately structural. It checks provenance integrity,
    required fields, category coverage, unique IDs/questions, and the rule that
    synthetic cases carry no reference answer, citation, or retrieval label.
    """
    errors: List[str] = []
    warnings: List[str] = []
    case_list = list(cases or [])
    if len(case_list) < 100:
        errors.append(f"dataset has {len(case_list)} cases; at least 100 are required")

    ids: set[str] = set()
    questions: set[str] = set()
    categories: Counter[str] = Counter()
    difficulties: Counter[str] = Counter()
    provenance: Counter[str] = Counter()
    labels: Counter[str] = Counter()

    for index, case in enumerate(case_list):
        prefix = f"case[{index}]"
        if not isinstance(case, dict):
            errors.append(f"{prefix}: not an object")
            continue
        missing = [field for field in _REQUIRED_FIELDS if field not in case]
        if missing:
            errors.append(f"{prefix}: missing fields {missing}")
            continue

        case_id = str(case.get("id") or "")
        if not case_id:
            errors.append(f"{prefix}: empty id")
        elif case_id in ids:
            errors.append(f"{case_id}: duplicate id")
        ids.add(case_id)

        question = str(case.get("question") or "").strip()
        if not question:
            errors.append(f"{case_id}: empty question")
        normalised = _normalise_question(question)
        if normalised in questions:
            errors.append(f"{case_id}: duplicate question text")
        questions.add(normalised)

        category = case.get("category")
        if category not in VALID_CATEGORIES:
            errors.append(f"{case_id}: invalid category {category!r}")
        categories[str(category)] += 1

        difficulty = case.get("difficulty")
        if difficulty not in VALID_DIFFICULTIES:
            errors.append(f"{case_id}: invalid difficulty {difficulty!r}")
        difficulties[str(difficulty)] += 1

        provenance_record = case.get("provenance")
        if not isinstance(provenance_record, dict):
            errors.append(f"{case_id}: provenance is not an object")
            continue
        provenance_type = provenance_record.get("type")
        if provenance_type not in VALID_PROVENANCE:
            errors.append(f"{case_id}: invalid provenance type {provenance_type!r}")
        provenance[str(provenance_type)] += 1
        is_synthetic = bool(provenance_record.get("synthetic")) or provenance_type == "synthetic"
        if is_synthetic and provenance_type != "synthetic":
            errors.append(f"{case_id}: synthetic flag conflicts with provenance type")
        if not is_synthetic and provenance_type != "verified_reference":
            errors.append(f"{case_id}: non-synthetic case must be verified_reference")

        labels_record = case.get("retrieval_ground_truth") or {}
        labels[str(labels_record.get("status"))] += 1

        domains = case.get("expected_source_domains")
        if not isinstance(domains, list):
            errors.append(f"{case_id}: expected_source_domains is not a list")
        else:
            invalid_domains = [
                domain for domain in domains if not _is_domain(domain)
            ]
            if invalid_domains:
                errors.append(
                    f"{case_id}: expected_source_domains contains invalid values "
                    f"{invalid_domains}"
                )

        rubric = case.get("evaluation_rubric")
        if not isinstance(rubric, dict):
            errors.append(f"{case_id}: evaluation_rubric is not an object")
        elif not isinstance(rubric.get("minimum_word_count"), int):
            errors.append(f"{case_id}: minimum_word_count must be an integer")

        if is_synthetic:
            _validate_synthetic_case(case, errors)
        else:
            _validate_verified_case(case, errors, warnings)

    required_categories = set(VALID_CATEGORIES)
    missing_categories = sorted(required_categories - set(categories))
    if missing_categories:
        errors.append(f"missing required categories: {missing_categories}")

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "counts": {
            "cases": len(case_list),
            "categories": dict(sorted(categories.items())),
            "difficulties": dict(sorted(difficulties.items())),
            "provenance": dict(sorted(provenance.items())),
            "retrieval_labels": dict(sorted(labels.items())),
        },
    }


def summarise_golden_dataset(cases: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Return a compact, machine-readable dataset summary."""
    categories = Counter(str(case.get("category")) for case in cases or [])
    difficulties = Counter(str(case.get("difficulty")) for case in cases or [])
    provenance = Counter(
        str((case.get("provenance") or {}).get("type")) for case in cases or []
    )
    labels = Counter(
        str((case.get("retrieval_ground_truth") or {}).get("status"))
        for case in cases or []
    )
    return {
        "cases": len(cases or []),
        "categories": dict(sorted(categories.items())),
        "difficulties": dict(sorted(difficulties.items())),
        "provenance": dict(sorted(provenance.items())),
        "retrieval_labels": dict(sorted(labels.items())),
    }


def to_engine_question(case: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a golden case into the existing evaluation engine's question shape."""
    provenance = case.get("provenance") or {}
    synthetic = bool(provenance.get("synthetic"))
    claims: List[Dict[str, Any]] = []
    if not synthetic:
        for finding in case.get("expected_key_findings") or []:
            if not isinstance(finding, dict):
                continue
            claims.append(
                {
                    "claim_id": finding.get("claim_id") or "",
                    "claim_text": finding.get("text") or "",
                    "effective_claim_text": finding.get("text") or "",
                    "status": "verified",
                    "source_ids": list(finding.get("source_ids") or []),
                }
            )
    rubric = case.get("evaluation_rubric") or {}
    metadata = case.get("metadata") or {}
    return {
        "id": case.get("id"),
        "domain": metadata.get("domain"),
        "difficulty": case.get("difficulty"),
        "task_type": metadata.get("source_task_type"),
        "topic": case.get("question") or "",
        "metadata": {
            "target_aspects": metadata.get("target_aspects") or [],
            "expected_source_domains": case.get("expected_source_domains") or [],
        },
        "ground_truth": {
            "verification_status": "synthetic" if synthetic else "verified",
            "is_verified": not synthetic,
            "claims": claims,
            "provisional_reference_claims": [],
        },
        "evaluation_rubric": {
            "structural_requirements": rubric.get("structural_requirements")
            or [
                "Introduction",
                "Key Points",
                "Applications",
                "Challenges",
                "Conclusion",
                "References",
            ],
            "minimum_word_count": rubric.get("minimum_word_count") or 250,
            "scoring_criteria": rubric.get("scoring_criteria") or {},
        },
        "expected_behavior": case.get("expected_behavior") or "",
        "synthetic": synthetic,
        "retrieval_ground_truth": case.get("retrieval_ground_truth") or {},
    }
