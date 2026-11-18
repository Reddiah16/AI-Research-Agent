#!/usr/bin/env python3
"""Read-only structural validator for the Phase 3 benchmark dataset.

Usage:
    python benchmarks/validate_dataset.py
    python benchmarks/validate_dataset.py --report benchmarks/datasets/STRUCTURAL_VALIDATION_REPORT.md

The validator uses only the Python standard library. It reads the benchmark
dataset and the Phase 1 catalog, performs structural checks, and optionally
writes a Markdown report. It never writes the dataset, connects to PostgreSQL,
calls Ollama or DuckDuckGo, or executes the research pipeline.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_DATASET = PROJECT_ROOT / "benchmarks" / "datasets" / "benchmark_v1.json"
DEFAULT_PHASE1_REPORT = PROJECT_ROOT / "docs" / "PHASE_1_RESEARCH_AGENT_AUDIT_REPORT.md"
DEFAULT_PHASE2_REPORT = PROJECT_ROOT / "docs" / "PHASE_2_IMPLEMENTATION_REPORT.md"
DEFAULT_REPORT = PROJECT_ROOT / "benchmarks" / "datasets" / "STRUCTURAL_VALIDATION_REPORT.md"

EXPECTED_IDS = tuple(f"Q{index:02d}" for index in range(1, 41))
EXPECTED_ID_SET = set(EXPECTED_IDS)

DOMAIN_ORDER = ("AI", "CC", "CS", "DE", "ET", "SE")
DIFFICULTY_ORDER = ("Easy", "Intermediate", "Difficult")
TASK_TYPE_ORDER = ("Explanation", "Comparison", "Multi-Source", "SOTA")

CANONICAL_SECTIONS = (
    "Introduction",
    "Key Points",
    "Applications",
    "Challenges",
    "Conclusion",
    "References",
)
CANONICAL_SCORING_CRITERIA = ("relevance", "completeness", "source_quality")

MIN_CLAIMS = 3
MAX_CLAIMS = 5
EXPECTED_SOURCE_DOMAINS = 3
MINIMUM_WORD_COUNT = 250

# Two-state verification model used by version 1 datasets:
# provisional items must be false; verified items must be true and attributed.
VALID_VERIFICATION_STATUSES = {
    "requires_manual_reference_verification": False,
    "verified": True,
}

# Five-state claim vocabulary introduced by version 2.
VALID_CLAIM_STATUSES = {
    "verified",
    "partially_supported",
    "contradicted",
    "needs_more_evidence",
    "time_sensitive",
}

# Resolution vocabulary introduced by version 3.
V3_RESOLUTIONS = {
    "confirmed_verified",
    "retained_partially_supported",
    "retained_needs_more_evidence",
    "corrected_and_verified",
    "downgraded_needs_more_evidence",
}

V3_SOURCE_VERIFICATION_STATUSES = {
    "verified_in_repository_record",
    "insufficiently_verified",
}

CLAIM_REQUIRED_FIELDS = (
    "claim_id",
    "claim_text",
    "status",
    "source_ids",
    "evidence_summary",
    "support_explanation",
    "contradiction_or_gap",
    "suggested_correction",
    "verifier",
    "verified_at",
    "notes",
)

SOURCE_REQUIRED_FIELDS = (
    "source_id",
    "url",
    "title",
    "publisher",
    "publication_date",
    "accessed_date",
    "source_type",
    "inspection_notes",
)

SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
QUESTION_ID_RE = re.compile(r"^Q\d{2}$")
CLAIM_ID_RE = re.compile(r"^Q\d{2}-C\d+$")
HTTP_URL_RE = re.compile(r"^https?://\S+$")
DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
)
CATALOG_ROW_RE = re.compile(
    r"^\|\s*(Q\d{2})\s*\|\s*([A-Z]{2})\s*\|\s*"
    r"(Easy|Intermediate|Difficult)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|"
)


class DuplicateKeyError(ValueError):
    """Raised when a JSON object contains the same key more than once."""


def _object_pairs_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Build an object while rejecting duplicate JSON keys."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKeyError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle, object_pairs_hook=_object_pairs_without_duplicates)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _normalise_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value).strip()


def _parse_iso_timestamp(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text)


def _format_counter(counter: Counter[Any]) -> str:
    if not counter:
        return "{}"
    return "{" + ", ".join(f"{key!r}: {counter[key]}" for key in sorted(counter, key=str)) + "}"


def _major_version(value: Any) -> int | None:
    """Return the integer major version from a MAJOR.MINOR.PATCH string."""
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"(\d+)\.\d+\.\d+", value.strip())
    if not match:
        return None
    return int(match.group(1))


def _aggregate_claim_statuses(statuses: list[str]) -> str:
    """Aggregate claim outcomes into the documented question-level status."""
    if all(status == "verified" for status in statuses):
        return "verified"
    if "contradicted" in statuses:
        return "contradicted"
    if "needs_more_evidence" in statuses:
        return "needs_more_evidence"
    if "time_sensitive" in statuses:
        return "time_sensitive"
    return "partially_supported"


@dataclass
class CheckResult:
    name: str
    description: str
    passed: bool = True
    details: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)


@dataclass
class ValidationResult:
    dataset_path: Path
    phase1_path: Path
    phase2_path: Path
    checks: list[CheckResult] = field(default_factory=list)
    issues: list[tuple[str, str]] = field(default_factory=list)
    observations: list[str] = field(default_factory=list)
    questions_checked: int = 0
    claims_checked: int = 0
    source_domains_checked: int = 0
    domain_counts: Counter[str] = field(default_factory=Counter)
    difficulty_counts: Counter[str] = field(default_factory=Counter)
    task_type_counts: Counter[str] = field(default_factory=Counter)
    catalog_domain_counts: Counter[str] = field(default_factory=Counter)
    catalog_difficulty_counts: Counter[str] = field(default_factory=Counter)
    catalog_task_type_counts: Counter[str] = field(default_factory=Counter)
    verification_distribution: Counter[tuple[bool, str]] = field(default_factory=Counter)
    claim_count_distribution: Counter[int] = field(default_factory=Counter)
    source_domain_count_distribution: Counter[int] = field(default_factory=Counter)
    claim_status_counts: Counter[str] = field(default_factory=Counter)
    sources_checked: int = 0
    v2_questions_verified: int = 0
    dataset_version: str = ""
    verified_by_populated: int = 0
    verified_at_present: int = 0
    domain_full_names: dict[str, set[str]] = field(default_factory=dict)
    dataset_sha256_before: str = ""
    dataset_sha256_after: str = ""
    fatal_error: str | None = None

    @property
    def passed(self) -> bool:
        return self.fatal_error is None and all(check.passed for check in self.checks)


class DatasetValidator:
    def __init__(
        self,
        dataset_path: Path,
        phase1_path: Path,
        phase2_path: Path,
    ) -> None:
        self.dataset_path = dataset_path
        self.phase1_path = phase1_path
        self.phase2_path = phase2_path
        self.data: Any = None
        self.questions: list[Any] = []
        self.catalog: dict[str, dict[str, str]] = {}
        self.is_v2 = False
        self.is_v3 = False
        self.v2_sources: dict[str, dict[str, Any]] = {}
        self.result = ValidationResult(
            dataset_path=dataset_path,
            phase1_path=phase1_path,
            phase2_path=phase2_path,
        )

    def _new_check(self, name: str, description: str) -> CheckResult:
        check = CheckResult(name=name, description=description)
        self.result.checks.append(check)
        return check

    def _fail(self, check: CheckResult, message: str) -> None:
        check.passed = False
        check.issues.append(message)
        self.result.issues.append((check.name, message))

    def _question_location(self, index: int, question: Any) -> str:
        if isinstance(question, dict):
            question_id = question.get("id")
            if _is_nonempty_string(question_id):
                return str(question_id)
        return f"question[{index}]"

    def _question_dicts(self) -> list[tuple[int, dict[str, Any]]]:
        return [
            (index, question)
            for index, question in enumerate(self.questions, 1)
            if isinstance(question, dict)
        ]

    def run(self) -> ValidationResult:
        self.result.dataset_sha256_before = (
            _sha256(self.dataset_path) if self.dataset_path.exists() else ""
        )
        self._load_dataset()
        self._load_phase1_catalog()
        self._validate_top_level()
        self._validate_question_count()
        self._validate_question_schema()
        self._validate_id_sequence()
        self._validate_topic_uniqueness()
        self._validate_taxonomy_values()
        self._validate_metadata()
        self._validate_ground_truth_claims()
        self._validate_expected_source_domains()
        self._validate_evaluation_rubric()
        self._validate_verification_state()
        if self.is_v2:
            self._validate_v2_extensions()
        if self.is_v3:
            self._validate_v3_extensions()
        self._compare_phase1_catalog()
        self._validate_dataset_unchanged()
        self._build_observations()
        return self.result

    def _load_dataset(self) -> None:
        check = self._new_check("json_parse", "Dataset exists and is valid JSON without duplicate keys")
        if not self.dataset_path.exists():
            self.result.fatal_error = f"Dataset not found: {self.dataset_path}"
            self._fail(check, self.result.fatal_error)
            return

        try:
            self.data = _load_json(self.dataset_path)
        except DuplicateKeyError as exc:
            self.result.fatal_error = f"Duplicate JSON key detected: {exc}"
            self._fail(check, self.result.fatal_error)
            return
        except json.JSONDecodeError as exc:
            self.result.fatal_error = f"Invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}"
            self._fail(check, self.result.fatal_error)
            return
        except OSError as exc:
            self.result.fatal_error = f"Could not read dataset: {exc}"
            self._fail(check, self.result.fatal_error)
            return

        check.details.append(f"Parsed {self.dataset_path.name}")

    def _load_phase1_catalog(self) -> None:
        check = self._new_check(
            "phase1_catalog_parse",
            "Phase 1 question catalog can be parsed into 40 authoritative rows",
        )
        if not self.phase1_path.exists():
            self._fail(check, f"Phase 1 report not found: {self.phase1_path}")
            return

        try:
            self.catalog = self._parse_phase1_catalog(self.phase1_path)
        except (OSError, ValueError) as exc:
            self._fail(check, f"Could not parse Phase 1 catalog: {exc}")
            return

        if len(self.catalog) != len(EXPECTED_IDS):
            self._fail(
                check,
                f"Phase 1 catalog has {len(self.catalog)} rows; expected {len(EXPECTED_IDS)}",
            )
        if set(self.catalog) != EXPECTED_ID_SET:
            missing = sorted(EXPECTED_ID_SET - set(self.catalog))
            extra = sorted(set(self.catalog) - EXPECTED_ID_SET)
            self._fail(
                check,
                f"Phase 1 catalog IDs differ from Q01-Q40 (missing={missing}, extra={extra})",
            )

        if check.passed:
            check.details.append("Parsed 40 catalog rows Q01-Q40")

    @staticmethod
    def _parse_phase1_catalog(path: Path) -> dict[str, dict[str, str]]:
        lines = path.read_text(encoding="utf-8").splitlines()
        section_start: int | None = None
        section_end: int | None = None

        for index, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("### 3.2"):
                section_start = index + 1
                continue
            if section_start is not None and stripped.startswith("### 3.3"):
                section_end = index
                break

        if section_start is None:
            raise ValueError("could not locate '### 3.2' catalog section")
        if section_end is None:
            section_end = len(lines)

        catalog: dict[str, dict[str, str]] = {}
        for line in lines[section_start:section_end]:
            match = CATALOG_ROW_RE.match(line)
            if not match:
                continue
            question_id = match.group(1)
            if question_id in catalog:
                raise ValueError(f"duplicate catalog row: {question_id}")
            catalog[question_id] = {
                "domain": match.group(2).strip(),
                "difficulty": match.group(3).strip(),
                "task_type": match.group(4).strip(),
                "topic": _normalise_text(match.group(5)),
            }
        return catalog

    def _validate_top_level(self) -> None:
        check = self._new_check(
            "top_level_schema",
            "Top-level dataset object contains the required fields with correct types",
        )
        if not isinstance(self.data, dict):
            self.result.fatal_error = "Dataset root must be a JSON object"
            self._fail(check, self.result.fatal_error)
            self.questions = []
            return

        required = [
            "version",
            "created_at",
            "description",
            "methodology_disclaimer",
            "benchmark_questions",
        ]
        version = self.data.get("version")
        major_version = _major_version(version)
        self.is_v2 = (
            major_version is not None and major_version >= 2
        ) or "verification_metadata" in self.data
        self.is_v3 = major_version is not None and major_version >= 3
        self.result.dataset_version = version if isinstance(version, str) else ""
        if self.is_v2:
            required.extend(("verification_metadata", "sources"))

        for key in required:
            if key not in self.data:
                self._fail(check, f"Missing top-level field: {key}")

        if not _is_nonempty_string(version):
            self._fail(check, "version must be a non-empty string")
        elif not SEMVER_RE.fullmatch(version):
            self._fail(check, f"version must use MAJOR.MINOR.PATCH format; got {version!r}")

        created_at = self.data.get("created_at")
        if not _is_nonempty_string(created_at):
            self._fail(check, "created_at must be a non-empty ISO-8601 string")
        else:
            try:
                _parse_iso_timestamp(created_at)
            except ValueError:
                self._fail(check, f"created_at is not a valid ISO-8601 timestamp: {created_at!r}")

        for key in ("description", "methodology_disclaimer"):
            if not _is_nonempty_string(self.data.get(key)):
                self._fail(check, f"{key} must be a non-empty string")

        questions = self.data.get("benchmark_questions")
        if not isinstance(questions, list):
            self._fail(check, "benchmark_questions must be a list")
            self.questions = []
        else:
            self.questions = questions

        if check.passed:
            check.details.append(
                f"version={version}; created_at={created_at}; questions={len(self.questions)}"
            )

    def _validate_question_count(self) -> None:
        check = self._new_check(
            "question_count",
            f"Dataset contains exactly {len(EXPECTED_IDS)} questions",
        )
        self.result.questions_checked = len(self.questions)
        if len(self.questions) != len(EXPECTED_IDS):
            self._fail(
                check,
                f"Expected {len(EXPECTED_IDS)} questions; found {len(self.questions)}",
            )
        else:
            check.details.append(f"Questions={len(self.questions)}")

    def _validate_question_schema(self) -> None:
        check = self._new_check(
            "question_schema",
            "Every question has the required fields and valid container types",
        )
        required = (
            "id",
            "domain",
            "difficulty",
            "task_type",
            "topic",
            "metadata",
            "ground_truth",
            "evaluation_rubric",
        )
        for index, question in enumerate(self.questions, 1):
            location = self._question_location(index, question)
            if not isinstance(question, dict):
                self._fail(check, f"{location}: question must be a JSON object")
                continue
            for key in required:
                if key not in question:
                    self._fail(check, f"{location}: missing required field {key!r}")
            for key in ("id", "domain", "difficulty", "task_type", "topic"):
                if not _is_nonempty_string(question.get(key)):
                    self._fail(check, f"{location}: {key} must be a non-empty string")
            for key in ("metadata", "ground_truth", "evaluation_rubric"):
                if not isinstance(question.get(key), dict):
                    self._fail(check, f"{location}: {key} must be a JSON object")

        if check.passed:
            check.details.append("All questions contain the required fields")

    def _validate_id_sequence(self) -> None:
        check = self._new_check(
            "id_sequence",
            "Question IDs are Q01-Q40 in order with no duplicates",
        )
        observed_ids: list[str] = []
        for index, question in self._question_dicts():
            location = self._question_location(index, question)
            question_id = question.get("id")
            if not isinstance(question_id, str) or not QUESTION_ID_RE.fullmatch(question_id):
                self._fail(check, f"{location}: invalid question ID {question_id!r}")
                continue
            observed_ids.append(question_id)

        duplicates = sorted(
            question_id
            for question_id, count in Counter(observed_ids).items()
            if count > 1
        )
        if duplicates:
            self._fail(check, f"Duplicate question IDs: {duplicates}")

        if list(observed_ids) != list(EXPECTED_IDS):
            self._fail(
                check,
                "Question IDs are not exactly the sequential Q01-Q40 series "
                f"(observed={observed_ids[:5]}...{observed_ids[-5:]})",
            )

        if check.passed:
            check.details.append("Q01-Q40 sequential and unique")

    def _validate_topic_uniqueness(self) -> None:
        check = self._new_check(
            "topic_uniqueness",
            "All question topics are non-empty and unique",
        )
        seen: dict[str, str] = {}
        for index, question in self._question_dicts():
            location = self._question_location(index, question)
            topic = question.get("topic")
            if not _is_nonempty_string(topic):
                self._fail(check, f"{location}: topic must be a non-empty string")
                continue
            normalised = _normalise_text(topic).casefold()
            if normalised in seen:
                self._fail(
                    check,
                    f"{location}: duplicate topic also used by {seen[normalised]}: {topic!r}",
                )
            else:
                seen[normalised] = location

        if check.passed:
            check.details.append(f"Unique topics={len(seen)}")

    def _validate_taxonomy_values(self) -> None:
        check = self._new_check(
            "taxonomy_values",
            "Domain, difficulty, and task type values are from the approved taxonomy",
        )
        for index, question in self._question_dicts():
            location = self._question_location(index, question)

            domain = question.get("domain")
            if domain not in DOMAIN_ORDER:
                self._fail(check, f"{location}: invalid domain {domain!r}")
            elif isinstance(domain, str):
                self.result.domain_counts[domain] += 1

            difficulty = question.get("difficulty")
            if difficulty not in DIFFICULTY_ORDER:
                self._fail(check, f"{location}: invalid difficulty {difficulty!r}")
            elif isinstance(difficulty, str):
                self.result.difficulty_counts[difficulty] += 1

            task_type = question.get("task_type")
            if task_type not in TASK_TYPE_ORDER:
                self._fail(check, f"{location}: invalid task_type {task_type!r}")
            elif isinstance(task_type, str):
                self.result.task_type_counts[task_type] += 1

        if check.passed:
            check.details.append(
                f"domains={_format_counter(self.result.domain_counts)}; "
                f"difficulty={_format_counter(self.result.difficulty_counts)}; "
                f"task_types={_format_counter(self.result.task_type_counts)}"
            )

    def _validate_metadata(self) -> None:
        check = self._new_check(
            "metadata_schema",
            "Metadata fields have valid types, target aspects, and consistent domain names",
        )
        required = ("domain_full_name", "target_aspects", "expected_source_domains")
        for index, question in self._question_dicts():
            location = self._question_location(index, question)
            metadata = question.get("metadata")
            if not isinstance(metadata, dict):
                self._fail(check, f"{location}: metadata must be an object")
                continue

            for key in required:
                if key not in metadata:
                    self._fail(check, f"{location}: missing metadata field {key!r}")

            domain_full_name = metadata.get("domain_full_name")
            if not _is_nonempty_string(domain_full_name):
                self._fail(check, f"{location}: metadata.domain_full_name must be a non-empty string")
            else:
                domain = question.get("domain")
                if isinstance(domain, str):
                    self.result.domain_full_names.setdefault(domain, set()).add(
                        domain_full_name.strip()
                    )

            target_aspects = metadata.get("target_aspects")
            if not isinstance(target_aspects, list):
                self._fail(check, f"{location}: metadata.target_aspects must be a list")
            elif not target_aspects:
                self._fail(check, f"{location}: metadata.target_aspects must not be empty")
            else:
                seen_aspects: set[str] = set()
                for aspect in target_aspects:
                    if not _is_nonempty_string(aspect):
                        self._fail(
                            check,
                            f"{location}: metadata.target_aspects contains a non-string or empty value",
                        )
                        continue
                    normalised = aspect.strip().casefold()
                    if normalised in seen_aspects:
                        self._fail(
                            check,
                            f"{location}: duplicate target aspect {aspect!r}",
                        )
                    seen_aspects.add(normalised)

        for domain, names in sorted(self.result.domain_full_names.items()):
            if len(names) > 1:
                self._fail(
                    check,
                    f"Domain {domain} maps to multiple full names: {sorted(names)}",
                )

        if check.passed:
            mapping = ", ".join(
                f"{domain}={sorted(names)[0]}"
                for domain, names in sorted(self.result.domain_full_names.items())
            )
            check.details.append(f"Consistent domain names: {mapping}")

    def _validate_ground_truth_claims(self) -> None:
        check = self._new_check(
            "ground_truth_claims",
            f"Each question has {MIN_CLAIMS}-{MAX_CLAIMS} non-empty unique provisional claims",
        )
        required = (
            "verification_status",
            "is_verified",
            "verified_by",
            "verification_notes",
            "provisional_reference_claims",
        )
        for index, question in self._question_dicts():
            location = self._question_location(index, question)
            ground_truth = question.get("ground_truth")
            if not isinstance(ground_truth, dict):
                self._fail(check, f"{location}: ground_truth must be an object")
                continue
            for key in required:
                if key not in ground_truth:
                    self._fail(check, f"{location}: missing ground_truth field {key!r}")

            claims = ground_truth.get("provisional_reference_claims")
            if not isinstance(claims, list):
                self._fail(
                    check,
                    f"{location}: ground_truth.provisional_reference_claims must be a list",
                )
                continue

            self.result.claims_checked += len(claims)
            self.result.claim_count_distribution[len(claims)] += 1

            if not MIN_CLAIMS <= len(claims) <= MAX_CLAIMS:
                self._fail(
                    check,
                    f"{location}: expected {MIN_CLAIMS}-{MAX_CLAIMS} claims; found {len(claims)}",
                )

            seen_claims: set[str] = set()
            for claim in claims:
                if not _is_nonempty_string(claim):
                    self._fail(
                        check,
                        f"{location}: claim must be a non-empty string",
                    )
                    continue
                normalised = claim.strip().casefold()
                if normalised in seen_claims:
                    self._fail(check, f"{location}: duplicate claim {claim!r}")
                seen_claims.add(normalised)

        if check.passed:
            check.details.append(
                f"Claims={self.result.claims_checked}; "
                f"distribution={_format_counter(self.result.claim_count_distribution)}"
            )

    def _validate_expected_source_domains(self) -> None:
        check = self._new_check(
            "expected_source_domains",
            f"Each question has exactly {EXPECTED_SOURCE_DOMAINS} valid unique source domains",
        )
        for index, question in self._question_dicts():
            location = self._question_location(index, question)
            metadata = question.get("metadata")
            if not isinstance(metadata, dict):
                self._fail(check, f"{location}: metadata must be an object")
                continue

            domains = metadata.get("expected_source_domains")
            if not isinstance(domains, list):
                self._fail(
                    check,
                    f"{location}: metadata.expected_source_domains must be a list",
                )
                continue

            self.result.source_domains_checked += len(domains)
            self.result.source_domain_count_distribution[len(domains)] += 1

            if len(domains) != EXPECTED_SOURCE_DOMAINS:
                self._fail(
                    check,
                    f"{location}: expected {EXPECTED_SOURCE_DOMAINS} source domains; found {len(domains)}",
                )

            seen_domains: set[str] = set()
            for domain in domains:
                if not _is_nonempty_string(domain):
                    self._fail(
                        check,
                        f"{location}: expected source domain must be a non-empty string",
                    )
                    continue
                normalised = domain.strip().casefold()
                if not DOMAIN_RE.fullmatch(normalised):
                    self._fail(
                        check,
                        f"{location}: malformed source domain {domain!r}",
                    )
                    continue
                if normalised in seen_domains:
                    self._fail(
                        check,
                        f"{location}: duplicate source domain {domain!r}",
                    )
                seen_domains.add(normalised)

        if check.passed:
            check.details.append(
                f"Source domains={self.result.source_domains_checked}; "
                f"distribution={_format_counter(self.result.source_domain_count_distribution)}"
            )

    def _validate_evaluation_rubric(self) -> None:
        check = self._new_check(
            "evaluation_rubric",
            "Rubric sections, minimum word count, and scoring criteria are consistent",
        )
        section_signatures: set[tuple[str, ...]] = set()
        minimum_word_counts: set[int] = set()

        for index, question in self._question_dicts():
            location = self._question_location(index, question)
            rubric = question.get("evaluation_rubric")
            if not isinstance(rubric, dict):
                self._fail(check, f"{location}: evaluation_rubric must be an object")
                continue

            for key in ("structural_requirements", "minimum_word_count", "scoring_criteria"):
                if key not in rubric:
                    self._fail(check, f"{location}: missing evaluation_rubric field {key!r}")

            sections = rubric.get("structural_requirements")
            if not isinstance(sections, list):
                self._fail(
                    check,
                    f"{location}: evaluation_rubric.structural_requirements must be a list",
                )
            elif not all(isinstance(section, str) for section in sections):
                self._fail(
                    check,
                    f"{location}: structural_requirements must contain only strings",
                )
            else:
                signature = tuple(sections)
                section_signatures.add(signature)
                if signature != CANONICAL_SECTIONS:
                    self._fail(
                        check,
                        f"{location}: structural_requirements differ from the canonical six sections",
                    )

            minimum_word_count = rubric.get("minimum_word_count")
            if type(minimum_word_count) is not int:
                self._fail(
                    check,
                    f"{location}: minimum_word_count must be an integer",
                )
            elif minimum_word_count < MINIMUM_WORD_COUNT:
                self._fail(
                    check,
                    f"{location}: minimum_word_count is {minimum_word_count}; "
                    f"expected at least {MINIMUM_WORD_COUNT}",
                )
            else:
                minimum_word_counts.add(minimum_word_count)

            scoring_criteria = rubric.get("scoring_criteria")
            if not isinstance(scoring_criteria, dict):
                self._fail(
                    check,
                    f"{location}: evaluation_rubric.scoring_criteria must be an object",
                )
            else:
                observed_keys = set(scoring_criteria)
                expected_keys = set(CANONICAL_SCORING_CRITERIA)
                if observed_keys != expected_keys:
                    self._fail(
                        check,
                        f"{location}: scoring_criteria keys differ from canonical "
                        f"(missing={sorted(expected_keys - observed_keys)}, "
                        f"extra={sorted(observed_keys - expected_keys)})",
                    )
                for key in CANONICAL_SCORING_CRITERIA:
                    if not _is_nonempty_string(scoring_criteria.get(key)):
                        self._fail(
                            check,
                            f"{location}: scoring_criteria.{key} must be a non-empty string",
                        )

        if len(section_signatures) > 1:
            self._fail(
                check,
                f"Rubric sections are not consistent across questions: {len(section_signatures)} variants",
            )
        if len(minimum_word_counts) > 1:
            self._fail(
                check,
                f"minimum_word_count is not consistent across questions: {sorted(minimum_word_counts)}",
            )

        if check.passed:
            check.details.append(
                f"sections={len(CANONICAL_SECTIONS)}; "
                f"minimum_word_count={sorted(minimum_word_counts)}; "
                f"scoring_keys={list(CANONICAL_SCORING_CRITERIA)}"
            )

    def _validate_verification_state(self) -> None:
        check = self._new_check(
            "verification_state",
            "is_verified, verification_status, verified_by, and verification metadata agree",
        )
        if self.is_v2:
            self._validate_verification_state_v2(check)
        else:
            self._validate_verification_state_v1(check)

        if check.passed:
            check.details.append(
                f"distribution={_format_counter(self.result.verification_distribution)}; "
                f"verified_by_populated={self.result.verified_by_populated}; "
                f"verified_at_present={self.result.verified_at_present}"
            )

    def _validate_verification_state_v1(self, check: CheckResult) -> None:
        for index, question in self._question_dicts():
            location = self._question_location(index, question)
            ground_truth = question.get("ground_truth")
            if not isinstance(ground_truth, dict):
                self._fail(check, f"{location}: ground_truth must be an object")
                continue

            status = ground_truth.get("verification_status")
            is_verified = ground_truth.get("is_verified")
            verified_by = ground_truth.get("verified_by")
            notes = ground_truth.get("verification_notes")

            if status not in VALID_VERIFICATION_STATUSES:
                self._fail(
                    check,
                    f"{location}: invalid verification_status {status!r}; "
                    f"expected one of {sorted(VALID_VERIFICATION_STATUSES)}",
                )
            if type(is_verified) is not bool:
                self._fail(check, f"{location}: is_verified must be a boolean")
            if not _is_nonempty_string(notes):
                self._fail(
                    check,
                    f"{location}: verification_notes must be a non-empty string",
                )
            if verified_by is not None and not _is_nonempty_string(verified_by):
                self._fail(
                    check,
                    f"{location}: verified_by must be null or a non-empty string",
                )

            if isinstance(verified_by, str) and verified_by.strip():
                self.result.verified_by_populated += 1

            has_verified_at = "verified_at" in ground_truth
            if has_verified_at:
                self.result.verified_at_present += 1
                verified_at = ground_truth.get("verified_at")
                if not _is_nonempty_string(verified_at):
                    self._fail(
                        check,
                        f"{location}: verified_at must be a non-empty ISO-8601 string when present",
                    )
                else:
                    try:
                        _parse_iso_timestamp(verified_at)
                    except ValueError:
                        self._fail(
                            check,
                            f"{location}: verified_at is not a valid ISO-8601 timestamp: {verified_at!r}",
                        )
                if is_verified is False:
                    self._fail(
                        check,
                        f"{location}: verified_at is present but is_verified is false",
                    )

            if type(is_verified) is bool and isinstance(status, str):
                self.result.verification_distribution[(is_verified, status)] += 1
                expected_flag = VALID_VERIFICATION_STATUSES.get(status)
                if expected_flag is not None and is_verified is not expected_flag:
                    self._fail(
                        check,
                        f"{location}: verification_status={status!r} requires "
                        f"is_verified={expected_flag}; found {is_verified}",
                    )

                if is_verified is True:
                    if status != "verified":
                        self._fail(
                            check,
                            f"{location}: is_verified is true but status is {status!r}",
                        )
                    if not _is_nonempty_string(verified_by):
                        self._fail(
                            check,
                            f"{location}: verified item must include a non-empty verified_by",
                        )
                else:
                    if status == "verified":
                        self._fail(
                            check,
                            f"{location}: status is 'verified' but is_verified is false",
                        )
                    if verified_by is not None:
                        self._fail(
                            check,
                            f"{location}: provisional item must have verified_by=null",
                        )

    def _validate_verification_state_v2(self, check: CheckResult) -> None:
        for index, question in self._question_dicts():
            location = self._question_location(index, question)
            ground_truth = question.get("ground_truth")
            if not isinstance(ground_truth, dict):
                self._fail(check, f"{location}: ground_truth must be an object")
                continue

            status = ground_truth.get("verification_status")
            is_verified = ground_truth.get("is_verified")
            verified_by = ground_truth.get("verified_by")
            notes = ground_truth.get("verification_notes")
            reviewed_by = ground_truth.get("reviewed_by")
            reviewed_at = ground_truth.get("reviewed_at")

            if status not in VALID_CLAIM_STATUSES:
                self._fail(
                    check,
                    f"{location}: invalid verification_status {status!r}; "
                    f"expected one of {sorted(VALID_CLAIM_STATUSES)}",
                )
            if type(is_verified) is not bool:
                self._fail(check, f"{location}: is_verified must be a boolean")
            if not _is_nonempty_string(notes):
                self._fail(
                    check,
                    f"{location}: verification_notes must be a non-empty string",
                )
            if not _is_nonempty_string(reviewed_by):
                self._fail(
                    check,
                    f"{location}: reviewed_by must be a non-empty string",
                )
            if not _is_nonempty_string(reviewed_at):
                self._fail(
                    check,
                    f"{location}: reviewed_at must be a non-empty ISO-8601 string",
                )
            else:
                try:
                    _parse_iso_timestamp(reviewed_at)
                except ValueError:
                    self._fail(
                        check,
                        f"{location}: reviewed_at is not a valid ISO-8601 timestamp: {reviewed_at!r}",
                    )
            if verified_by is not None and not _is_nonempty_string(verified_by):
                self._fail(
                    check,
                    f"{location}: verified_by must be null or a non-empty string",
                )

            if isinstance(verified_by, str) and verified_by.strip():
                self.result.verified_by_populated += 1

            has_verified_at = "verified_at" in ground_truth
            if has_verified_at:
                self.result.verified_at_present += 1
                verified_at = ground_truth.get("verified_at")
                if not _is_nonempty_string(verified_at):
                    self._fail(
                        check,
                        f"{location}: verified_at must be a non-empty ISO-8601 string when present",
                    )
                else:
                    try:
                        _parse_iso_timestamp(verified_at)
                    except ValueError:
                        self._fail(
                            check,
                            f"{location}: verified_at is not a valid ISO-8601 timestamp: {verified_at!r}",
                        )

            if type(is_verified) is bool and isinstance(status, str):
                self.result.verification_distribution[(is_verified, status)] += 1

            if is_verified is True:
                if status != "verified":
                    self._fail(
                        check,
                        f"{location}: is_verified is true but status is {status!r}",
                    )
                if not _is_nonempty_string(verified_by):
                    self._fail(
                        check,
                        f"{location}: verified item must include a non-empty verified_by",
                    )
                if not has_verified_at:
                    self._fail(
                        check,
                        f"{location}: verified item must include verified_at",
                    )
            elif is_verified is False:
                if status == "verified":
                    self._fail(
                        check,
                        f"{location}: status is 'verified' but is_verified is false",
                    )
                if verified_by is not None:
                    self._fail(
                        check,
                        f"{location}: unresolved item must have verified_by=null",
                    )
                if has_verified_at:
                    self._fail(
                        check,
                        f"{location}: unresolved item must not include verified_at",
                    )

    # ------------------------------------------------------------------
    # Version 2 evidence checks
    # ------------------------------------------------------------------

    def _validate_v2_extensions(self) -> None:
        self._validate_v2_source_registry()
        self._validate_v2_verification_metadata()
        self._validate_v2_claim_records()
        self._validate_v2_claim_evidence()
        self._validate_v2_question_aggregation()

    def _validate_v2_source_registry(self) -> None:
        check = self._new_check(
            "v2_source_registry",
            "The top-level source registry is complete, unique, and traceable",
        )
        sources = self.data.get("sources") if isinstance(self.data, dict) else None
        if not isinstance(sources, list):
            self._fail(check, "sources must be a list")
            return
        if not sources:
            self._fail(check, "sources must not be empty")
            return

        registry: dict[str, dict[str, Any]] = {}
        for index, source in enumerate(sources, 1):
            location = f"source[{index}]"
            if not isinstance(source, dict):
                self._fail(check, f"{location}: source must be a JSON object")
                continue
            for field_name in SOURCE_REQUIRED_FIELDS:
                if field_name not in source:
                    self._fail(check, f"{location}: missing source field {field_name!r}")

            source_id = source.get("source_id")
            if not isinstance(source_id, str) or not re.fullmatch(r"S\d{3,}", source_id):
                self._fail(check, f"{location}: invalid source_id {source_id!r}")
            elif source_id in registry:
                self._fail(check, f"{location}: duplicate source_id {source_id!r}")
            else:
                registry[source_id] = source

            url = source.get("url")
            if not isinstance(url, str) or not HTTP_URL_RE.fullmatch(url.strip()):
                self._fail(check, f"{location}: url must be an absolute http(s) URL")

            for field_name in ("title", "publisher", "publication_date", "source_type", "inspection_notes"):
                if not _is_nonempty_string(source.get(field_name)):
                    self._fail(check, f"{location}: {field_name} must be a non-empty string")

            accessed_date = source.get("accessed_date")
            if not _is_nonempty_string(accessed_date):
                self._fail(check, f"{location}: accessed_date must be a non-empty string")
            else:
                try:
                    _parse_iso_timestamp(accessed_date)
                except ValueError:
                    self._fail(
                        check,
                        f"{location}: accessed_date is not a valid ISO-8601 date: {accessed_date!r}",
                    )

        self.v2_sources = registry
        self.result.sources_checked = len(sources)
        if check.passed:
            check.details.append(f"Sources={len(sources)}; unique IDs={len(registry)}")

    def _validate_v2_verification_metadata(self) -> None:
        check = self._new_check(
            "v2_verification_metadata",
            "verification_metadata records methodology, statuses, and consistent totals",
        )
        metadata = self.data.get("verification_metadata") if isinstance(self.data, dict) else None
        if not isinstance(metadata, dict):
            self._fail(check, "verification_metadata must be a JSON object")
            return

        required = (
            "verification_date",
            "verifier",
            "methodology",
            "status_definitions",
            "aggregation_rule",
            "claims_total",
            "questions_total",
        )
        for field_name in required:
            if field_name not in metadata:
                self._fail(check, f"verification_metadata: missing field {field_name!r}")

        verification_date = metadata.get("verification_date")
        if not _is_nonempty_string(verification_date):
            self._fail(check, "verification_metadata.verification_date must be a non-empty string")
        else:
            try:
                _parse_iso_timestamp(verification_date)
            except ValueError:
                self._fail(
                    check,
                    "verification_metadata.verification_date is not a valid ISO-8601 date: "
                    f"{verification_date!r}",
                )

        for field_name in ("verifier", "methodology", "aggregation_rule"):
            if not _is_nonempty_string(metadata.get(field_name)):
                self._fail(
                    check,
                    f"verification_metadata.{field_name} must be a non-empty string",
                )

        definitions = metadata.get("status_definitions")
        if not isinstance(definitions, dict):
            self._fail(check, "verification_metadata.status_definitions must be an object")
        else:
            observed = set(definitions)
            if observed != VALID_CLAIM_STATUSES:
                self._fail(
                    check,
                    "verification_metadata.status_definitions keys differ from the approved "
                    f"five-state vocabulary (missing={sorted(VALID_CLAIM_STATUSES - observed)}, "
                    f"extra={sorted(observed - VALID_CLAIM_STATUSES)})",
                )
            for status, definition in definitions.items():
                if not _is_nonempty_string(definition):
                    self._fail(
                        check,
                        f"verification_metadata.status_definitions.{status} must be non-empty",
                    )

        claim_statuses = self._collect_claim_statuses()
        claims_total = metadata.get("claims_total")
        if type(claims_total) is not int:
            self._fail(check, "verification_metadata.claims_total must be an integer")
        elif claims_total != self.result.claims_checked:
            self._fail(
                check,
                f"verification_metadata.claims_total={claims_total} but the dataset has "
                f"{self.result.claims_checked} claims",
            )

        questions_total = metadata.get("questions_total")
        if type(questions_total) is not int:
            self._fail(check, "verification_metadata.questions_total must be an integer")
        elif questions_total != len(self.questions):
            self._fail(
                check,
                f"verification_metadata.questions_total={questions_total} but the dataset has "
                f"{len(self.questions)} questions",
            )

        claims_by_status = metadata.get("claims_by_status")
        if not isinstance(claims_by_status, dict):
            self._fail(check, "verification_metadata.claims_by_status must be an object")
        else:
            observed = {str(key): value for key, value in claims_by_status.items()}
            for status, count in observed.items():
                if status not in VALID_CLAIM_STATUSES:
                    self._fail(
                        check,
                        f"verification_metadata.claims_by_status has unknown status {status!r}",
                    )
                if type(count) is not int:
                    self._fail(
                        check,
                        f"verification_metadata.claims_by_status.{status} must be an integer",
                    )
            if observed != dict(claim_statuses):
                self._fail(
                    check,
                    "verification_metadata.claims_by_status does not match the claim records: "
                    f"metadata={observed}, records={dict(claim_statuses)}",
                )

        questions_verified = metadata.get("questions_verified")
        if type(questions_verified) is not int:
            self._fail(check, "verification_metadata.questions_verified must be an integer")
        elif questions_verified != self._count_verified_questions():
            self._fail(
                check,
                f"verification_metadata.questions_verified={questions_verified} but the "
                "question records contain "
                f"{self._count_verified_questions()} verified questions",
            )

        v1_sha256 = metadata.get("v1_sha256")
        if v1_sha256 is not None:
            if not isinstance(v1_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", v1_sha256):
                self._fail(
                    check,
                    "verification_metadata.v1_sha256 must be a 64-character lowercase hex digest",
                )

        if check.passed:
            check.details.append(
                f"claims_total={claims_total}; questions_total={questions_total}; "
                f"claims_by_status={_format_counter(claim_statuses)}"
            )

    def _collect_claim_statuses(self) -> Counter[str]:
        counts: Counter[str] = Counter()
        for _, question in self._question_dicts():
            ground_truth = question.get("ground_truth")
            if not isinstance(ground_truth, dict):
                continue
            claims = ground_truth.get("claims")
            if not isinstance(claims, list):
                continue
            for claim in claims:
                if isinstance(claim, dict) and isinstance(claim.get("status"), str):
                    counts[claim["status"]] += 1
        return counts

    def _count_verified_questions(self) -> int:
        return sum(
            1
            for _, question in self._question_dicts()
            if isinstance(question.get("ground_truth"), dict)
            and question["ground_truth"].get("is_verified") is True
        )

    def _validate_v2_claim_records(self) -> None:
        check = self._new_check(
            "v2_claim_records",
            "Every original claim has a complete, uniquely identified verification record",
        )
        seen_claim_ids: set[str] = set()
        for index, question in self._question_dicts():
            location = self._question_location(index, question)
            question_id = question.get("id")
            ground_truth = question.get("ground_truth")
            if not isinstance(ground_truth, dict):
                self._fail(check, f"{location}: ground_truth must be an object")
                continue

            originals = ground_truth.get("provisional_reference_claims")
            if not isinstance(originals, list):
                self._fail(
                    check,
                    f"{location}: ground_truth.provisional_reference_claims must be a list",
                )
                continue

            claims = ground_truth.get("claims")
            if not isinstance(claims, list):
                self._fail(check, f"{location}: ground_truth.claims must be a list")
                continue

            if len(claims) != len(originals):
                self._fail(
                    check,
                    f"{location}: ground_truth.claims has {len(claims)} records but "
                    f"{len(originals)} original claims were preserved",
                )

            for claim_index, record in enumerate(claims, 1):
                claim_location = f"{location}-C{claim_index}"
                if not isinstance(record, dict):
                    self._fail(check, f"{claim_location}: claim record must be an object")
                    continue
                for field_name in CLAIM_REQUIRED_FIELDS:
                    if field_name not in record:
                        self._fail(
                            check,
                            f"{claim_location}: missing claim field {field_name!r}",
                        )

                claim_id = record.get("claim_id")
                if not isinstance(claim_id, str) or not CLAIM_ID_RE.fullmatch(claim_id):
                    self._fail(
                        check,
                        f"{claim_location}: invalid claim_id {claim_id!r}",
                    )
                else:
                    expected_prefix = f"{question_id}-C"
                    if not claim_id.startswith(expected_prefix):
                        self._fail(
                            check,
                            f"{claim_location}: claim_id {claim_id!r} does not belong to "
                            f"question {question_id!r}",
                        )
                    if claim_id in seen_claim_ids:
                        self._fail(check, f"{claim_location}: duplicate claim_id {claim_id!r}")
                    seen_claim_ids.add(claim_id)

                if claim_index <= len(originals):
                    original = originals[claim_index - 1]
                    if record.get("claim_text") != original:
                        self._fail(
                            check,
                            f"{claim_location}: claim_text does not preserve the original "
                            "wording verbatim",
                        )

                status = record.get("status")
                if status not in VALID_CLAIM_STATUSES:
                    self._fail(
                        check,
                        f"{claim_location}: invalid claim status {status!r}; "
                        f"expected one of {sorted(VALID_CLAIM_STATUSES)}",
                    )

                source_ids = record.get("source_ids")
                if not isinstance(source_ids, list) or not source_ids:
                    self._fail(
                        check,
                        f"{claim_location}: source_ids must be a non-empty list",
                    )
                elif len(set(source_ids)) != len(source_ids):
                    self._fail(
                        check,
                        f"{claim_location}: source_ids contains duplicates",
                    )
                elif not all(isinstance(source_id, str) and source_id for source_id in source_ids):
                    self._fail(
                        check,
                        f"{claim_location}: source_ids must contain non-empty strings",
                    )

                if not _is_nonempty_string(record.get("verifier")):
                    self._fail(
                        check,
                        f"{claim_location}: verifier must be a non-empty string",
                    )

                verified_at = record.get("verified_at")
                if not _is_nonempty_string(verified_at):
                    self._fail(
                        check,
                        f"{claim_location}: verified_at must be a non-empty ISO-8601 date",
                    )
                else:
                    try:
                        _parse_iso_timestamp(verified_at)
                    except ValueError:
                        self._fail(
                            check,
                            f"{claim_location}: verified_at is not a valid ISO-8601 date: "
                            f"{verified_at!r}",
                        )

                if not isinstance(record.get("notes"), str):
                    self._fail(check, f"{claim_location}: notes must be a string")

        if check.passed:
            check.details.append(f"Claim records={len(seen_claim_ids)}")

    def _validate_v2_claim_evidence(self) -> None:
        check = self._new_check(
            "v2_claim_evidence",
            "Claim evidence is substantive and every source_id resolves to the source registry",
        )
        for index, question in self._question_dicts():
            location = self._question_location(index, question)
            ground_truth = question.get("ground_truth")
            if not isinstance(ground_truth, dict):
                continue
            claims = ground_truth.get("claims")
            if not isinstance(claims, list):
                continue

            for claim_index, record in enumerate(claims, 1):
                claim_location = f"{location}-C{claim_index}"
                if not isinstance(record, dict):
                    continue

                status = record.get("status")
                source_ids = record.get("source_ids")
                if isinstance(source_ids, list):
                    for source_id in source_ids:
                        if not isinstance(source_id, str):
                            continue
                        if source_id not in self.v2_sources:
                            self._fail(
                                check,
                                f"{claim_location}: source_id {source_id!r} is not present "
                                "in the top-level source registry",
                            )

                for field_name in ("evidence_summary", "support_explanation"):
                    if not _is_nonempty_string(record.get(field_name)):
                        self._fail(
                            check,
                            f"{claim_location}: {field_name} must be a non-empty string",
                        )

                gap = record.get("contradiction_or_gap")
                correction = record.get("suggested_correction")
                for field_name, value in (
                    ("contradiction_or_gap", gap),
                    ("suggested_correction", correction),
                ):
                    if value is not None and not _is_nonempty_string(value):
                        self._fail(
                            check,
                            f"{claim_location}: {field_name} must be null or a non-empty string",
                        )

                if status == "verified":
                    if gap is not None:
                        self._fail(
                            check,
                            f"{claim_location}: verified claim must not record a contradiction or gap",
                        )
                    if correction is not None:
                        self._fail(
                            check,
                            f"{claim_location}: verified claim must not suggest a correction",
                        )
                elif status == "partially_supported":
                    if not _is_nonempty_string(gap):
                        self._fail(
                            check,
                            f"{claim_location}: partially_supported claim must explain the gap",
                        )
                    if not _is_nonempty_string(correction):
                        self._fail(
                            check,
                            f"{claim_location}: partially_supported claim must suggest a correction",
                        )
                elif status in ("contradicted", "needs_more_evidence"):
                    if not _is_nonempty_string(gap):
                        self._fail(
                            check,
                            f"{claim_location}: {status} claim must explain the contradiction or gap",
                        )
                    if not _is_nonempty_string(correction):
                        self._fail(
                            check,
                            f"{claim_location}: {status} claim must suggest a correction or next step",
                        )
                elif status == "time_sensitive":
                    if not _is_nonempty_string(gap):
                        self._fail(
                            check,
                            f"{claim_location}: time_sensitive claim must state the time sensitivity",
                        )

        if check.passed:
            check.details.append(f"Evidence checked for {self.result.claims_checked} claims")

    def _validate_v2_question_aggregation(self) -> None:
        check = self._new_check(
            "v2_question_aggregation",
            "Question verification flags aggregate consistently from their claim statuses",
        )
        claim_status_counts: Counter[str] = Counter()
        verified_questions = 0
        for index, question in self._question_dicts():
            location = self._question_location(index, question)
            ground_truth = question.get("ground_truth")
            if not isinstance(ground_truth, dict):
                continue
            claims = ground_truth.get("claims")
            if not isinstance(claims, list) or not claims:
                self._fail(
                    check,
                    f"{location}: ground_truth.claims must be a non-empty list",
                )
                continue

            statuses: list[str] = []
            for claim_index, record in enumerate(claims, 1):
                if not isinstance(record, dict):
                    continue
                status = record.get("status")
                if status not in VALID_CLAIM_STATUSES:
                    self._fail(
                        check,
                        f"{location}-C{claim_index}: cannot aggregate invalid status {status!r}",
                    )
                    continue
                statuses.append(status)
                claim_status_counts[status] += 1

            if len(statuses) != len(claims):
                continue

            expected = _aggregate_claim_statuses(statuses)
            observed = ground_truth.get("verification_status")
            if observed != expected:
                self._fail(
                    check,
                    f"{location}: verification_status={observed!r} but claim aggregation "
                    f"requires {expected!r} (claims={statuses})",
                )

            expected_flag = expected == "verified"
            observed_flag = ground_truth.get("is_verified")
            if observed_flag is not expected_flag:
                self._fail(
                    check,
                    f"{location}: is_verified={observed_flag!r} but claim aggregation "
                    f"requires {expected_flag!r}",
                )
            if expected_flag:
                verified_questions += 1

        self.result.claim_status_counts = claim_status_counts
        self.result.v2_questions_verified = verified_questions
        if check.passed:
            check.details.append(
                f"claims={_format_counter(claim_status_counts)}; "
                f"verified_questions={verified_questions}/{len(self.questions)}"
            )

    # ------------------------------------------------------------------
    # Version 3 remediation checks
    # ------------------------------------------------------------------

    def _validate_v3_extensions(self) -> None:
        self._validate_v3_claim_wording()
        self._validate_v3_claim_evidence_integrity()
        self._validate_v3_exact_wording_evidence()
        self._validate_v3_unresolved_claim_rules()
        self._validate_v3_review_history()
        self._validate_v3_source_verification()
        self._validate_v3_source_domain_reconciliation()
        self._validate_v3_metadata_consistency()
        self._validate_v3_version_integrity()

    def _v3_claims(self) -> list[tuple[str, dict[str, Any]]]:
        records: list[tuple[str, dict[str, Any]]] = []
        for index, question in self._question_dicts():
            location = self._question_location(index, question)
            ground_truth = question.get("ground_truth")
            if not isinstance(ground_truth, dict):
                continue
            claims = ground_truth.get("claims")
            if not isinstance(claims, list):
                continue
            for claim_index, record in enumerate(claims, 1):
                if isinstance(record, dict):
                    records.append((f"{location}-C{claim_index}", record))
        return records

    def _validate_v3_claim_wording(self) -> None:
        check = self._new_check(
            "v3_claim_wording_audit",
            "Every claim records original and corrected wording with an explicit resolution",
        )
        required = (
            "claim_text",
            "corrected_claim_text",
            "effective_claim_text",
            "status",
            "original_status",
            "resolution",
            "status_change_reason",
            "correction_evidence",
            "exact_wording_met",
            "exact_wording_assessment",
            "review_reference",
            "finding",
            "source_verification_note",
            "original_contradiction_or_gap",
            "original_suggested_correction",
            "review_history",
        )
        for location, claim in self._v3_claims():
            for field_name in required:
                if field_name not in claim:
                    self._fail(check, f"{location}: missing v3 field {field_name!r}")

            original = claim.get("claim_text")
            corrected = claim.get("corrected_claim_text")
            effective = claim.get("effective_claim_text")
            if not _is_nonempty_string(original):
                self._fail(check, f"{location}: claim_text must be non-empty")
            if corrected is not None:
                if not _is_nonempty_string(corrected):
                    self._fail(
                        check,
                        f"{location}: corrected_claim_text must be null or non-empty",
                    )
                elif corrected == original:
                    self._fail(
                        check,
                        f"{location}: corrected_claim_text must differ from claim_text",
                    )
            expected_effective = corrected if corrected is not None else original
            if effective != expected_effective:
                self._fail(
                    check,
                    f"{location}: effective_claim_text must equal corrected_claim_text "
                    "when present, otherwise claim_text",
                )

            original_status = claim.get("original_status")
            if original_status not in VALID_CLAIM_STATUSES:
                self._fail(
                    check,
                    f"{location}: invalid original_status {original_status!r}",
                )
            resolution = claim.get("resolution")
            if resolution not in V3_RESOLUTIONS:
                self._fail(check, f"{location}: invalid resolution {resolution!r}")
            if not _is_nonempty_string(claim.get("status_change_reason")):
                self._fail(
                    check,
                    f"{location}: status_change_reason must be non-empty",
                )
            if not isinstance(claim.get("exact_wording_met"), bool):
                self._fail(check, f"{location}: exact_wording_met must be a boolean")
            if not _is_nonempty_string(claim.get("exact_wording_assessment")):
                self._fail(
                    check,
                    f"{location}: exact_wording_assessment must be non-empty",
                )
            for field_name in ("review_reference", "finding", "source_verification_note"):
                value = claim.get(field_name)
                if value is not None and not _is_nonempty_string(value):
                    self._fail(
                        check,
                        f"{location}: {field_name} must be null or a non-empty string",
                    )

            evidence = claim.get("correction_evidence")
            if evidence is not None:
                if not isinstance(evidence, list) or not evidence:
                    self._fail(
                        check,
                        f"{location}: correction_evidence must be null or a non-empty list",
                    )
                elif not all(_is_nonempty_string(item) for item in evidence):
                    self._fail(
                        check,
                        f"{location}: correction_evidence must contain non-empty strings",
                    )
                elif any(item not in self.v2_sources for item in evidence):
                    self._fail(
                        check,
                        f"{location}: correction_evidence contains an unknown source_id",
                    )
            if corrected is not None and not evidence:
                self._fail(
                    check,
                    f"{location}: a corrected claim must record correction_evidence",
                )
            if corrected is None and evidence:
                self._fail(
                    check,
                    f"{location}: correction_evidence requires corrected_claim_text",
                )

        if check.passed:
            check.details.append(f"Wording audited for {len(self._v3_claims())} claims")

    def _validate_v3_claim_evidence_integrity(self) -> None:
        check = self._new_check(
            "v3_claim_evidence_integrity",
            "Every v3 claim cites resolvable sources and corrections stay traceable to them",
        )
        for location, claim in self._v3_claims():
            source_ids = claim.get("source_ids")
            if not isinstance(source_ids, list) or not source_ids:
                self._fail(check, f"{location}: source_ids must be a non-empty list")
                continue
            if len(set(source_ids)) != len(source_ids):
                self._fail(check, f"{location}: source_ids must be unique")
            unknown = sorted(set(source_ids) - set(self.v2_sources))
            if unknown:
                self._fail(check, f"{location}: unknown source_ids {unknown}")
            correction_evidence = claim.get("correction_evidence")
            if correction_evidence:
                untraceable = sorted(set(correction_evidence) - set(source_ids))
                if untraceable:
                    self._fail(
                        check,
                        f"{location}: correction_evidence {untraceable} is not among the "
                        "claim's cited source_ids",
                    )

        if check.passed:
            check.details.append(
                f"Evidence references checked for {len(self._v3_claims())} claims"
            )

    def _validate_v3_exact_wording_evidence(self) -> None:
        check = self._new_check(
            "v3_exact_wording_evidence",
            "A verified claim's exact effective wording is supported and unresolved claims cannot be verified",
        )
        for location, claim in self._v3_claims():
            status = claim.get("status")
            original_status = claim.get("original_status")
            corrected = claim.get("corrected_claim_text")
            evidence = claim.get("correction_evidence")
            exact = claim.get("exact_wording_met")
            gap = claim.get("contradiction_or_gap")
            correction = claim.get("suggested_correction")
            resolution = claim.get("resolution")

            if status == "verified":
                if exact is not True:
                    self._fail(
                        check,
                        f"{location}: verified claim must set exact_wording_met=true",
                    )
                if gap is not None or correction is not None:
                    self._fail(
                        check,
                        f"{location}: verified claim must not carry a gap or correction",
                    )
                if corrected is None:
                    if original_status != "verified":
                        self._fail(
                            check,
                            f"{location}: claim promoted to verified without corrected "
                            "wording or prior verified status",
                        )
                    if resolution != "confirmed_verified":
                        self._fail(
                            check,
                            f"{location}: unchanged verified claim must use "
                            "resolution='confirmed_verified'",
                        )
                else:
                    if resolution != "corrected_and_verified":
                        self._fail(
                            check,
                            f"{location}: corrected verified claim must use "
                            "resolution='corrected_and_verified'",
                        )
                    if not evidence:
                        self._fail(
                            check,
                            f"{location}: corrected verified claim has no supporting "
                            "correction_evidence",
                        )
            else:
                if exact is not False:
                    self._fail(
                        check,
                        f"{location}: non-verified claim must set exact_wording_met=false",
                    )
                if not _is_nonempty_string(gap):
                    self._fail(
                        check,
                        f"{location}: unresolved claim must explain its gap",
                    )
                if not _is_nonempty_string(correction):
                    self._fail(
                        check,
                        f"{location}: unresolved claim must record a correction or next step",
                    )
                if resolution == "corrected_and_verified":
                    self._fail(
                        check,
                        f"{location}: corrected_and_verified requires status='verified'",
                    )

        # The two deliberately unresolved v3 claims must not silently pass.
        q17_c2 = next(
            (claim for location, claim in self._v3_claims() if location.endswith("Q17-C2")),
            None,
        )
        q29_c2 = next(
            (claim for location, claim in self._v3_claims() if location.endswith("Q29-C2")),
            None,
        )
        if q17_c2 is None or q17_c2.get("status") != "needs_more_evidence":
            self._fail(check, "Q17-C2 must remain needs_more_evidence")
        if q29_c2 is None or q29_c2.get("status") != "needs_more_evidence":
            self._fail(check, "Q29-C2 must be needs_more_evidence in version 3")

        if check.passed:
            verified = sum(
                1 for _, claim in self._v3_claims() if claim.get("status") == "verified"
            )
            check.details.append(
                f"Verified claims with exact wording={verified}; "
                "Q17-C2 and Q29-C2 remain unresolved"
            )

    def _validate_v3_unresolved_claim_rules(self) -> None:
        check = self._new_check(
            "v3_unresolved_claim_rules",
            "Contradicted or unsupported claims are not promoted by wording alone",
        )
        for location, claim in self._v3_claims():
            original_status = claim.get("original_status")
            status = claim.get("status")
            resolution = claim.get("resolution")
            corrected = claim.get("corrected_claim_text")
            evidence = claim.get("correction_evidence")

            if status == "verified" and original_status != "verified":
                if corrected is None or not evidence:
                    self._fail(
                        check,
                        f"{location}: unresolved claim promoted to verified without a "
                        "corrected wording and cited evidence",
                    )
                if resolution != "corrected_and_verified":
                    self._fail(
                        check,
                        f"{location}: promoted claim must use corrected_and_verified",
                    )
            if original_status == "contradicted" and resolution == "confirmed_verified":
                self._fail(
                    check,
                    f"{location}: contradicted claim cannot be confirmed without a correction",
                )
            if status == "contradicted":
                self._fail(
                    check,
                    f"{location}: contradicted status must be resolved or retained with an "
                    "explicit unresolved resolution",
                )
            if resolution == "downgraded_needs_more_evidence":
                if status != "needs_more_evidence":
                    self._fail(
                        check,
                        f"{location}: downgraded_needs_more_evidence requires "
                        "status='needs_more_evidence'",
                    )
                if corrected is not None:
                    self._fail(
                        check,
                        f"{location}: a downgraded claim must not carry corrected wording",
                    )

        if check.passed:
            check.details.append("No unsupported claim was promoted by rewriting")

    def _validate_v3_review_history(self) -> None:
        check = self._new_check(
            "v3_review_history",
            "Every claim carries a review history consistent with its status transition",
        )
        for location, claim in self._v3_claims():
            history = claim.get("review_history")
            if not isinstance(history, list) or not history:
                self._fail(check, f"{location}: review_history must be a non-empty list")
                continue
            for entry_index, entry in enumerate(history, 1):
                entry_location = f"{location}:review_history[{entry_index}]"
                if not isinstance(entry, dict):
                    self._fail(check, f"{entry_location} must be an object")
                    continue
                for field_name in (
                    "date",
                    "reviewer",
                    "action",
                    "status_before",
                    "status_after",
                    "reason",
                    "evidence_refs",
                ):
                    if field_name not in entry:
                        self._fail(
                            check, f"{entry_location}: missing field {field_name!r}"
                        )
                date = entry.get("date")
                if not _is_nonempty_string(date):
                    self._fail(check, f"{entry_location}: date must be non-empty")
                else:
                    try:
                        _parse_iso_timestamp(date)
                    except ValueError:
                        self._fail(
                            check,
                            f"{entry_location}: date is not a valid ISO-8601 value",
                        )
                if not _is_nonempty_string(entry.get("reviewer")):
                    self._fail(check, f"{entry_location}: reviewer must be non-empty")
                if not _is_nonempty_string(entry.get("action")):
                    self._fail(check, f"{entry_location}: action must be non-empty")
                if not _is_nonempty_string(entry.get("reason")):
                    self._fail(check, f"{entry_location}: reason must be non-empty")
                before = entry.get("status_before")
                after = entry.get("status_after")
                if before is not None and before not in VALID_CLAIM_STATUSES:
                    self._fail(
                        check, f"{entry_location}: invalid status_before {before!r}"
                    )
                if after not in VALID_CLAIM_STATUSES:
                    self._fail(check, f"{entry_location}: invalid status_after {after!r}")
                refs = entry.get("evidence_refs")
                if not isinstance(refs, list) or not refs:
                    self._fail(
                        check, f"{entry_location}: evidence_refs must be a non-empty list"
                    )
                elif any(ref not in self.v2_sources for ref in refs):
                    self._fail(
                        check, f"{entry_location}: evidence_refs has an unknown source_id"
                    )

            if history[0].get("status_after") != claim.get("original_status"):
                self._fail(
                    check,
                    f"{location}: first review status does not match original_status",
                )
            if history[-1].get("status_after") != claim.get("status"):
                self._fail(
                    check,
                    f"{location}: last review status does not match status",
                )
            if history[-1].get("reason") != claim.get("status_change_reason"):
                self._fail(
                    check,
                    f"{location}: last review reason does not match status_change_reason",
                )
            if (
                claim.get("original_status") != claim.get("status")
                and len(history) < 2
            ):
                self._fail(
                    check,
                    f"{location}: status change requires at least two review entries",
                )

        if check.passed:
            check.details.append("Review histories are consistent with claim statuses")

    def _validate_v3_source_verification(self) -> None:
        check = self._new_check(
            "v3_source_verification",
            "Source verification is recorded separately and weak sources are not load-bearing",
        )
        sources = self.data.get("sources") if isinstance(self.data, dict) else None
        if not isinstance(sources, list):
            self._fail(check, "sources must be a list")
            return

        insufficient: set[str] = set()
        status_counts: Counter[str] = Counter()
        for source in sources:
            if not isinstance(source, dict):
                continue
            source_id = source.get("source_id")
            status = source.get("verification_status")
            if status not in V3_SOURCE_VERIFICATION_STATUSES:
                self._fail(
                    check,
                    f"{source_id}: verification_status must be one of "
                    f"{sorted(V3_SOURCE_VERIFICATION_STATUSES)}; got {status!r}",
                )
                continue
            status_counts[status] += 1
            if status == "insufficiently_verified":
                insufficient.add(str(source_id))
                gaps = source.get("bibliographic_gaps")
                if not isinstance(gaps, list) or not gaps:
                    self._fail(
                        check,
                        f"{source_id}: insufficient source must record bibliographic_gaps",
                    )
                if not _is_nonempty_string(source.get("v3_review_note")):
                    self._fail(
                        check,
                        f"{source_id}: insufficient source must record a v3_review_note",
                    )
            elif not _is_nonempty_string(source.get("v3_review_note")):
                self._fail(
                    check,
                    f"{source_id}: verified source must record a v3_review_note",
                )

        if insufficient != {"S053"}:
            self._fail(
                check,
                "The only insufficiently verified source in this release must be S053; "
                f"got {sorted(insufficient)}",
            )

        for location, claim in self._v3_claims():
            if claim.get("status") != "verified":
                continue
            cited = [
                source_id
                for source_id in claim.get("source_ids", [])
                if source_id in insufficient
            ]
            if not cited:
                continue
            if not _is_nonempty_string(claim.get("source_verification_note")):
                self._fail(
                    check,
                    f"{location}: verified claim cites {cited} without a "
                    "source_verification_note",
                )
            if len(claim.get("source_ids", [])) < 2:
                self._fail(
                    check,
                    f"{location}: verified claim depends solely on an insufficient source",
                )

        metadata = self.data.get("verification_metadata", {})
        if metadata.get("sources_total") != len(sources):
            self._fail(
                check,
                "verification_metadata.sources_total does not match the source registry",
            )
        if metadata.get("sources_insufficiently_verified") != status_counts[
            "insufficiently_verified"
        ]:
            self._fail(
                check,
                "verification_metadata.sources_insufficiently_verified is inconsistent",
            )
        if metadata.get("sources_verified_in_repository_record") != status_counts[
            "verified_in_repository_record"
        ]:
            self._fail(
                check,
                "verification_metadata.sources_verified_in_repository_record is "
                "inconsistent",
            )
        if metadata.get("insufficiently_verified_source_ids") != sorted(insufficient):
            self._fail(
                check,
                "verification_metadata.insufficiently_verified_source_ids is inconsistent",
            )

        if check.passed:
            check.details.append(
                f"Sources={len(sources)}; insufficient={sorted(insufficient)}"
            )

    def _validate_v3_source_domain_reconciliation(self) -> None:
        check = self._new_check(
            "v3_source_domain_reconciliation",
            "Frozen expected domains are reconciled with actual citations by documented substitution",
        )
        mismatch_questions = 0
        mismatch_claims: set[str] = set()
        outside_domains: set[str] = set()
        expected_question_count = len(self.questions)

        for index, question in self._question_dicts():
            location = self._question_location(index, question)
            metadata = question.get("metadata")
            if not isinstance(metadata, dict):
                self._fail(check, f"{location}: metadata must be an object")
                continue
            audit = metadata.get("source_domain_audit")
            if not isinstance(audit, dict):
                self._fail(check, f"{location}: missing source_domain_audit object")
                continue
            expected = metadata.get("expected_source_domains")
            if not isinstance(expected, list) or len(expected) != EXPECTED_SOURCE_DOMAINS:
                self._fail(
                    check,
                    f"{location}: expected_source_domains must remain a three-item list",
                )
                continue
            if audit.get("expected_source_domains") != expected:
                self._fail(
                    check,
                    f"{location}: source_domain_audit.expected_source_domains must equal "
                    "the frozen metadata list",
                )

            actual: set[str] = set()
            per_domain: dict[str, dict[str, set[str]]] = {}
            ground_truth = question.get("ground_truth")
            claims = ground_truth.get("claims") if isinstance(ground_truth, dict) else None
            if not isinstance(claims, list):
                self._fail(check, f"{location}: ground_truth.claims must be a list")
                continue
            for claim in claims:
                if not isinstance(claim, dict):
                    continue
                for source_id in claim.get("source_ids", []):
                    source = self.v2_sources.get(source_id)
                    if not isinstance(source, dict):
                        continue
                    host = urlparse(str(source.get("url", ""))).netloc.lower()
                    if not host:
                        continue
                    actual.add(host)
                    bucket = per_domain.setdefault(
                        host, {"source_ids": set(), "claim_ids": set()}
                    )
                    bucket["source_ids"].add(source_id)
                    bucket["claim_ids"].add(claim.get("claim_id"))

            actual_list = sorted(actual)
            if audit.get("actual_source_domains") != actual_list:
                self._fail(
                    check,
                    f"{location}: actual_source_domains does not match cited source URLs",
                )
            outside = sorted(
                host
                for host in actual
                if not any(
                    host == domain or host.endswith("." + domain)
                    for domain in expected
                )
            )
            if audit.get("outside_expected_source_domains") != outside:
                self._fail(
                    check,
                    f"{location}: outside_expected_source_domains is inconsistent",
                )
            expected_status = (
                "reconciled_with_documented_substitutions"
                if outside
                else "within_expected_domains"
            )
            if audit.get("status") != expected_status:
                self._fail(
                    check,
                    f"{location}: source_domain_audit.status must be {expected_status!r}",
                )

            claim_ids = sorted(
                {
                    claim_id
                    for host in outside
                    for claim_id in per_domain[host]["claim_ids"]
                }
            )
            if audit.get("mismatch_claim_ids") != claim_ids:
                self._fail(
                    check,
                    f"{location}: mismatch_claim_ids is inconsistent with cited domains",
                )
            if audit.get("mismatch_claim_count") != len(claim_ids):
                self._fail(
                    check,
                    f"{location}: mismatch_claim_count is inconsistent",
                )

            substitutions = audit.get("substitutions")
            if not isinstance(substitutions, list):
                self._fail(check, f"{location}: substitutions must be a list")
                continue
            if len(substitutions) != len(outside):
                self._fail(
                    check,
                    f"{location}: every outside domain must have exactly one substitution record",
                )
            observed = []
            for substitution in substitutions:
                if not isinstance(substitution, dict):
                    self._fail(check, f"{location}: substitution entry must be an object")
                    continue
                domain = substitution.get("observed_domain")
                observed.append(domain)
                if domain not in outside:
                    self._fail(
                        check,
                        f"{location}: substitution for unexpected domain {domain!r}",
                    )
                    continue
                if not _is_nonempty_string(substitution.get("classification")):
                    self._fail(
                        check,
                        f"{location}: substitution {domain!r} lacks a classification",
                    )
                if not _is_nonempty_string(substitution.get("rationale")):
                    self._fail(
                        check,
                        f"{location}: substitution {domain!r} lacks a rationale",
                    )
                expected_sources = sorted(per_domain[domain]["source_ids"])
                expected_claims = sorted(per_domain[domain]["claim_ids"])
                if substitution.get("source_ids") != expected_sources:
                    self._fail(
                        check,
                        f"{location}: substitution {domain!r} source_ids are inconsistent",
                    )
                if substitution.get("claim_ids") != expected_claims:
                    self._fail(
                        check,
                        f"{location}: substitution {domain!r} claim_ids are inconsistent",
                    )
            if sorted(observed) != outside:
                self._fail(
                    check,
                    f"{location}: substitution domains do not match outside domains",
                )

            if outside:
                mismatch_questions += 1
                outside_domains.update(outside)
                mismatch_claims.update(claim_ids)

        if mismatch_questions > expected_question_count:
            self._fail(check, "impossible mismatch-question count")

        metadata = self.data.get("verification_metadata", {})
        if metadata.get("source_domain_mismatch_questions") != mismatch_questions:
            self._fail(
                check,
                "verification_metadata.source_domain_mismatch_questions is inconsistent",
            )
        if metadata.get("source_domain_mismatch_claims") != len(mismatch_claims):
            self._fail(
                check,
                "verification_metadata.source_domain_mismatch_claims is inconsistent",
            )
        if metadata.get("source_domain_distinct_outside") != len(outside_domains):
            self._fail(
                check,
                "verification_metadata.source_domain_distinct_outside is inconsistent",
            )

        if check.passed:
            check.details.append(
                f"Questions with substitutions={mismatch_questions}; "
                f"claims={len(mismatch_claims)}; distinct outside domains={len(outside_domains)}"
            )

    def _validate_v3_metadata_consistency(self) -> None:
        check = self._new_check(
            "v3_metadata_consistency",
            "Version 3 remediation totals agree with the claim, source and question records",
        )
        metadata = self.data.get("verification_metadata") if isinstance(self.data, dict) else None
        if not isinstance(metadata, dict):
            self._fail(check, "verification_metadata must be an object")
            return

        required = (
            "previous_version",
            "v1_sha256",
            "v2_sha256",
            "source_verification_methodology",
            "claim_verification_methodology",
            "v3_status_scope",
            "claims_corrected",
            "claims_downgraded",
            "claims_unchanged",
            "claims_exact_wording_met",
            "claims_exact_wording_not_met",
            "claims_by_resolution",
            "questions_by_status",
            "sources_total",
            "sources_verified_in_repository_record",
            "sources_insufficiently_verified",
            "insufficiently_verified_source_ids",
            "source_domain_mismatch_questions",
            "source_domain_mismatch_claims",
            "source_domain_distinct_outside",
        )
        for field_name in required:
            if field_name not in metadata:
                self._fail(
                    check, f"verification_metadata: missing v3 field {field_name!r}"
                )

        claims = [claim for _, claim in self._v3_claims()]
        status_counts = Counter(claim.get("status") for claim in claims)
        resolution_counts = Counter(claim.get("resolution") for claim in claims)
        exact_count = sum(1 for claim in claims if claim.get("exact_wording_met") is True)
        question_statuses = Counter(
            question["ground_truth"].get("verification_status")
            for _, question in self._question_dicts()
            if isinstance(question.get("ground_truth"), dict)
        )

        if metadata.get("claims_total") != len(claims):
            self._fail(check, "claims_total does not match claim records")
        if metadata.get("claims_by_status") != dict(sorted(status_counts.items())):
            self._fail(check, "claims_by_status does not match claim records")
        if metadata.get("claims_by_resolution") != dict(sorted(resolution_counts.items())):
            self._fail(check, "claims_by_resolution does not match claim records")
        if metadata.get("claims_corrected") != resolution_counts[
            "corrected_and_verified"
        ]:
            self._fail(check, "claims_corrected does not match corrected resolutions")
        if metadata.get("claims_downgraded") != resolution_counts[
            "downgraded_needs_more_evidence"
        ]:
            self._fail(check, "claims_downgraded does not match downgraded resolutions")
        unchanged = sum(
            resolution_counts[key]
            for key in (
                "confirmed_verified",
                "retained_partially_supported",
                "retained_needs_more_evidence",
            )
        )
        if metadata.get("claims_unchanged") != unchanged:
            self._fail(check, "claims_unchanged does not match retained resolutions")
        if metadata.get("claims_exact_wording_met") != exact_count:
            self._fail(check, "claims_exact_wording_met does not match claim records")
        if metadata.get("claims_exact_wording_not_met") != len(claims) - exact_count:
            self._fail(
                check, "claims_exact_wording_not_met does not match claim records"
            )
        if exact_count != status_counts["verified"]:
            self._fail(
                check,
                "every verified claim must meet the exact-wording standard and only "
                "verified claims may meet it",
            )
        if metadata.get("questions_total") != len(self.questions):
            self._fail(check, "questions_total does not match question records")
        if metadata.get("questions_by_status") != dict(
            sorted(question_statuses.items())
        ):
            self._fail(check, "questions_by_status does not match question records")
        if metadata.get("questions_verified") != question_statuses["verified"]:
            self._fail(check, "questions_verified does not match question records")

        sources = self.data.get("sources")
        if isinstance(sources, list):
            source_statuses = Counter(
                source.get("verification_status")
                for source in sources
                if isinstance(source, dict)
            )
            if metadata.get("sources_total") != len(sources):
                self._fail(check, "sources_total does not match the source registry")
            if metadata.get("sources_verified_in_repository_record") != source_statuses[
                "verified_in_repository_record"
            ]:
                self._fail(
                    check,
                    "sources_verified_in_repository_record is inconsistent",
                )
            if metadata.get("sources_insufficiently_verified") != source_statuses[
                "insufficiently_verified"
            ]:
                self._fail(check, "sources_insufficiently_verified is inconsistent")

        if check.passed:
            check.details.append(
                f"claims={dict(sorted(status_counts.items()))}; "
                f"questions={dict(sorted(question_statuses.items()))}; "
                f"corrected={resolution_counts['corrected_and_verified']}; "
                f"exact={exact_count}"
            )

    def _validate_v3_version_integrity(self) -> None:
        check = self._new_check(
            "v3_version_integrity",
            "Version 3 records its lineage, hash guards and separated verification methods",
        )
        version = self.data.get("version") if isinstance(self.data, dict) else None
        if version != "3.0.0":
            self._fail(check, f"version must be '3.0.0'; got {version!r}")
        if self.data.get("supersedes") != "benchmark_v2.json":
            self._fail(check, "version 3 must declare supersedes='benchmark_v2.json'")

        metadata = self.data.get("verification_metadata")
        if not isinstance(metadata, dict):
            self._fail(check, "verification_metadata must be an object")
            return
        if metadata.get("previous_version") != "benchmark_v2.json":
            self._fail(check, "previous_version must be 'benchmark_v2.json'")
        for field_name in ("v1_sha256", "v2_sha256"):
            value = metadata.get(field_name)
            if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
                self._fail(
                    check,
                    f"verification_metadata.{field_name} must be a 64-character "
                    "lowercase hex digest",
                )
        source_method = metadata.get("source_verification_methodology")
        claim_method = metadata.get("claim_verification_methodology")
        if not _is_nonempty_string(source_method):
            self._fail(check, "source_verification_methodology must be non-empty")
        if not _is_nonempty_string(claim_method):
            self._fail(check, "claim_verification_methodology must be non-empty")
        if source_method == claim_method:
            self._fail(
                check,
                "source and claim verification methodologies must be recorded separately",
            )
        if not _is_nonempty_string(metadata.get("v3_status_scope")):
            self._fail(check, "v3_status_scope must be non-empty")

        if check.passed:
            check.details.append(
                f"version={version}; previous_version={metadata.get('previous_version')}"
            )

    def _compare_phase1_catalog(self) -> None:
        check = self._new_check(
            "phase1_catalog_comparison",
            "Dataset records and taxonomy counts match the Phase 1 catalog exactly",
        )
        if not self.catalog:
            self._fail(check, "Phase 1 catalog is unavailable; comparison cannot run")
            return

        question_by_id: dict[str, dict[str, Any]] = {}
        for _, question in self._question_dicts():
            question_id = question.get("id")
            if isinstance(question_id, str):
                question_by_id.setdefault(question_id, question)

        mismatches: list[str] = []
        for question_id in EXPECTED_IDS:
            expected = self.catalog.get(question_id)
            actual = question_by_id.get(question_id)
            if expected is None:
                mismatches.append(f"{question_id}: missing from Phase 1 catalog")
                continue
            if actual is None:
                mismatches.append(f"{question_id}: missing from dataset")
                continue
            for field in ("domain", "difficulty", "task_type", "topic"):
                actual_value = _normalise_text(actual.get(field))
                expected_value = _normalise_text(expected.get(field))
                if actual_value != expected_value:
                    mismatches.append(
                        f"{question_id}: {field} dataset={actual_value!r} "
                        f"catalog={expected_value!r}"
                    )

        for mismatch in mismatches:
            self._fail(check, mismatch)

        self.result.catalog_domain_counts = Counter(
            entry["domain"] for entry in self.catalog.values()
        )
        self.result.catalog_difficulty_counts = Counter(
            entry["difficulty"] for entry in self.catalog.values()
        )
        self.result.catalog_task_type_counts = Counter(
            entry["task_type"] for entry in self.catalog.values()
        )

        actual_counts = {
            "domain": self.result.domain_counts,
            "difficulty": self.result.difficulty_counts,
            "task_type": self.result.task_type_counts,
        }
        expected_counts = {
            "domain": self.result.catalog_domain_counts,
            "difficulty": self.result.catalog_difficulty_counts,
            "task_type": self.result.catalog_task_type_counts,
        }
        for field in ("domain", "difficulty", "task_type"):
            if actual_counts[field] != expected_counts[field]:
                self._fail(
                    check,
                    f"{field} counts differ: dataset={_format_counter(actual_counts[field])} "
                    f"catalog={_format_counter(expected_counts[field])}",
                )

        if check.passed:
            check.details.append(
                "All 40 records and all taxonomy counts match the Phase 1 catalog"
            )

    def _validate_dataset_unchanged(self) -> None:
        check = self._new_check(
            "dataset_unchanged",
            "Dataset SHA-256 is unchanged before and after validation",
        )
        if not self.dataset_path.exists():
            self._fail(check, f"Dataset not found: {self.dataset_path}")
            return
        self.result.dataset_sha256_after = _sha256(self.dataset_path)
        if self.result.dataset_sha256_after != self.result.dataset_sha256_before:
            self._fail(
                check,
                "Dataset hash changed during validation: "
                f"{self.result.dataset_sha256_before} -> {self.result.dataset_sha256_after}",
            )
        else:
            check.details.append(f"SHA-256={self.result.dataset_sha256_after}")

    def _build_observations(self) -> None:
        result = self.result
        if not result.questions_checked:
            return

        if self.is_v2:
            result.observations.append(
                f"All {result.questions_checked} questions and {result.claims_checked} claims "
                "were checked structurally. Claim statuses describe inspected evidence as of "
                f"{self._verification_date()}; they are not benchmark execution results."
            )
        else:
            result.observations.append(
                f"All {result.questions_checked} questions were checked structurally; "
                "this report does not assert factual accuracy."
            )

        if result.claim_count_distribution:
            result.observations.append(
                "Claim count distribution: "
                f"{_format_counter(result.claim_count_distribution)} "
                f"(approved range {MIN_CLAIMS}-{MAX_CLAIMS})."
            )

        if result.source_domain_count_distribution:
            result.observations.append(
                "Expected source-domain count distribution: "
                f"{_format_counter(result.source_domain_count_distribution)} "
                f"(required {EXPECTED_SOURCE_DOMAINS} per question)."
            )

        if result.verification_distribution:
            result.observations.append(
                "Verification-state distribution: "
                f"{_format_counter(result.verification_distribution)}. "
                f"verified_by populated={result.verified_by_populated}; "
                f"verified_at present={result.verified_at_present}."
            )

        if self.is_v2:
            result.observations.append(
                "Claim-status distribution: "
                f"{_format_counter(result.claim_status_counts)}; "
                f"fully verified questions={result.v2_questions_verified}/"
                f"{result.questions_checked}; sources in registry={result.sources_checked}."
            )
            result.observations.append(
                "Unresolved claims are preserved with their original wording and mapped to "
                "suggested corrections; they were not marked verified."
            )
        elif self._has_claim_reference_urls():
            result.observations.append(
                "The dataset contains at least one URL in its ground-truth metadata; "
                "confirm that each claim has traceable evidence during factual verification."
            )
        else:
            result.observations.append(
                "No per-claim reference URLs are present in the current dataset; "
                "factual verification will require external evidence."
            )

        result.observations.extend(self._phase2_count_observations())

    def _verification_date(self) -> str:
        if isinstance(self.data, dict):
            metadata = self.data.get("verification_metadata")
            if isinstance(metadata, dict) and _is_nonempty_string(
                metadata.get("verification_date")
            ):
                return str(metadata["verification_date"])
        return "the recorded verification date"

    def _has_claim_reference_urls(self) -> bool:
        url_re = re.compile(r"https?://", re.IGNORECASE)
        for _, question in self._question_dicts():
            ground_truth = question.get("ground_truth")
            if not isinstance(ground_truth, dict):
                continue
            for key, value in ground_truth.items():
                values = value if isinstance(value, list) else [value]
                if any(
                    isinstance(item, str) and url_re.search(item)
                    for item in values
                ):
                    return True
        return False

    def _phase2_count_observations(self) -> list[str]:
        if not self.phase2_path.exists():
            return [f"Phase 2 report not found at {self.phase2_path}; counts were not cross-checked."]

        try:
            lines = self.phase2_path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            return [f"Phase 2 report could not be read: {exc}"]

        def parse_line(marker: str) -> dict[str, int] | None:
            for line in lines:
                if marker in line:
                    matches = re.findall(r"`([^`]+)`\s*\((\d+)\)", line)
                    if matches:
                        return {name: int(count) for name, count in matches}
            return None

        observations: list[str] = []
        checks = (
            ("domain", "6 Domains:", self.result.domain_counts),
            ("difficulty", "3 Difficulty Tiers:", self.result.difficulty_counts),
            ("task type", "4 Task Archetypes:", self.result.task_type_counts),
        )
        for label, marker, actual in checks:
            reported = parse_line(marker)
            if reported is None:
                continue
            keys = sorted(set(reported) | set(actual), key=str)
            mismatches = [
                f"{key}: report={reported.get(key, 0)}, dataset={actual.get(key, 0)}"
                for key in keys
                if reported.get(key, 0) != actual.get(key, 0)
            ]
            if mismatches:
                observations.append(
                    f"Phase 2 report {label} counts differ from the dataset "
                    f"({'; '.join(mismatches)}). The Phase 1 catalog is authoritative; "
                    "the Phase 2 counts were not used by this validator."
                )
        return observations


def _ordered_counter(counter: Counter[Any], order: tuple[str, ...]) -> list[tuple[str, int]]:
    rows: list[tuple[str, int]] = [(key, counter.get(key, 0)) for key in order]
    extras = sorted(set(counter) - set(order), key=str)
    rows.extend((str(key), counter[key]) for key in extras)
    return rows


def _md_cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_markdown(result: ValidationResult) -> str:
    passed_checks = sum(1 for check in result.checks if check.passed)
    failed_checks = len(result.checks) - passed_checks
    status = "PASS" if result.passed else "FAIL"
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    lines: list[str] = []
    lines.append("# Phase 3 Stage 1 - Dataset Validation and Evidence Verification Report")
    lines.append("")
    lines.append(f"**Generated (UTC):** {generated_at}  ")
    lines.append("**Validator:** `benchmarks/validate_dataset.py`  ")
    lines.append(f"**Dataset:** `{result.dataset_path}`  ")
    lines.append(f"**Phase 1 catalog:** `{result.phase1_path}`  ")
    lines.append(f"**Dataset SHA-256 (before):** `{result.dataset_sha256_before}`  ")
    lines.append(f"**Dataset SHA-256 (after):** `{result.dataset_sha256_after}`  ")
    lines.append(f"**Result:** **{status}**")
    lines.append("")

    lines.append("## 1. Summary")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---|")
    lines.append(f"| Dataset version | {_md_cell(result.dataset_version or 'unknown')} |")
    lines.append(f"| Total questions checked | {result.questions_checked} |")
    lines.append(f"| Total claims checked | {result.claims_checked} |")
    lines.append(f"| Total expected source domains checked | {result.source_domains_checked} |")
    if result.sources_checked:
        lines.append(f"| Sources in evidence registry | {result.sources_checked} |")
    lines.append(f"| Structural checks passed | {passed_checks} |")
    lines.append(f"| Structural checks failed | {failed_checks} |")
    if result.claim_status_counts:
        lines.append(
            "| Claim statuses | " + _md_cell(_format_counter(result.claim_status_counts)) + " |"
        )
        lines.append(
            f"| Fully verified questions | {result.v2_questions_verified} / {result.questions_checked} |"
        )
    else:
        lines.append(
            f"| Verification state | {result.verification_distribution.get((False, 'requires_manual_reference_verification'), 0)} provisional / "
            f"{result.verification_distribution.get((True, 'verified'), 0)} verified |"
        )
    dataset_unchanged = result.dataset_sha256_before == result.dataset_sha256_after
    lines.append(
        "| Dataset modified | "
        + ("No - SHA-256 unchanged |" if dataset_unchanged else "YES - SHA-256 CHANGED |")
    )
    lines.append("")

    lines.append("## 2. Actual taxonomy counts")
    lines.append("")
    lines.append("Counts below are calculated directly from the dataset. The Phase 1 catalog is the comparison source of truth; the Phase 2 report counts are not used as authority.")
    lines.append("")
    lines.append("### 2.1 Domains")
    lines.append("")
    lines.append("| Domain | Dataset | Phase 1 catalog | Match |")
    lines.append("|---|---:|---:|---|")
    for key, actual in _ordered_counter(result.domain_counts, DOMAIN_ORDER):
        expected = result.catalog_domain_counts.get(key, 0)
        lines.append(f"| {_md_cell(key)} | {actual} | {expected} | {'YES' if actual == expected else 'NO'} |")
    lines.append("")
    lines.append("### 2.2 Difficulty")
    lines.append("")
    lines.append("| Difficulty | Dataset | Phase 1 catalog | Match |")
    lines.append("|---|---:|---:|---|")
    for key, actual in _ordered_counter(result.difficulty_counts, DIFFICULTY_ORDER):
        expected = result.catalog_difficulty_counts.get(key, 0)
        lines.append(f"| {_md_cell(key)} | {actual} | {expected} | {'YES' if actual == expected else 'NO'} |")
    lines.append("")
    lines.append("### 2.3 Task types")
    lines.append("")
    lines.append("| Task type | Dataset | Phase 1 catalog | Match |")
    lines.append("|---|---:|---:|---|")
    for key, actual in _ordered_counter(result.task_type_counts, TASK_TYPE_ORDER):
        expected = result.catalog_task_type_counts.get(key, 0)
        lines.append(f"| {_md_cell(key)} | {actual} | {expected} | {'YES' if actual == expected else 'NO'} |")
    lines.append("")

    lines.append("## 3. Verification-state distribution")
    lines.append("")
    lines.append("| `is_verified` | `verification_status` | Count |")
    lines.append("|---|---|---:|")
    if result.verification_distribution:
        for (is_verified, verification_status), count in sorted(
            result.verification_distribution.items(),
            key=lambda item: (str(item[0][0]), item[0][1]),
        ):
            lines.append(f"| {is_verified} | {_md_cell(verification_status)} | {count} |")
    else:
        lines.append("| - | - | 0 |")
    lines.append("")
    lines.append(
        f"- `verified_by` populated: **{result.verified_by_populated}**  "
    )
    lines.append(
        f"- `verified_at` present: **{result.verified_at_present}**"
    )
    lines.append("")

    lines.append("## 4. Validation checks")
    lines.append("")
    lines.append("| # | Check | Result | Details |")
    lines.append("|---:|---|---|---|")
    for index, check in enumerate(result.checks, 1):
        detail = "; ".join(check.details) if check.details else "; ".join(check.issues)
        lines.append(
            f"| {index} | `{_md_cell(check.name)}` | "
            f"{'PASS' if check.passed else 'FAIL'} | {_md_cell(detail)} |"
        )
    lines.append("")

    lines.append("## 5. Issues discovered")
    lines.append("")
    lines.append("### 5.1 Structural failures")
    lines.append("")
    if result.issues:
        for check_name, message in result.issues:
            lines.append(f"- **`{check_name}`:** {_md_cell(message)}")
    else:
        lines.append("- None.")
    lines.append("")
    lines.append("### 5.2 Observations and non-blocking notes")
    lines.append("")
    if result.observations:
        for observation in result.observations:
            lines.append(f"- {_md_cell(observation)}")
    else:
        lines.append("- None.")
    lines.append("")

    lines.append("## 6. Exact files created")
    lines.append("")
    lines.append("- `benchmarks/validate_dataset.py` - reusable read-only structural validator.")
    lines.append(
        "- `benchmarks/datasets/STRUCTURAL_VALIDATION_REPORT.md` - this validation report."
    )
    lines.append("")

    lines.append("## 7. Preservation confirmation")
    lines.append("")
    lines.append(
        f"- The dataset was read only. SHA-256 before: `{result.dataset_sha256_before}`; "
        f"after: `{result.dataset_sha256_after}`. Unchanged: "
        f"**{'YES' if result.dataset_sha256_before == result.dataset_sha256_after else 'NO'}**."
    )
    lines.append("- No PostgreSQL connection was opened.")
    lines.append("- No Ollama or DuckDuckGo call was made.")
    lines.append("- The research pipeline was not executed.")
    lines.append("- No existing tests, Phase 1 report, Phase 2 report, or other project file was modified.")
    lines.append("- No Git staging or commit action was performed.")
    lines.append("")

    lines.append("## 8. Scope boundary")
    lines.append("")
    if result.claim_status_counts:
        lines.append(
            "This report validates dataset structure and claim-level evidence coverage. "
            "Claim statuses reflect directly inspected sources as of the verification date; "
            "benchmark execution and downstream scoring remain out of scope."
        )
    else:
        lines.append(
            "This is structural validation only. Factual verification of the 120 provisional "
            "claims, benchmark execution, and downstream evaluation are out of scope for Step 1."
        )
    lines.append("")
    lines.append(f"**{status}**")
    lines.append("")
    return "\n".join(lines)


def print_summary(result: ValidationResult) -> None:
    passed_checks = sum(1 for check in result.checks if check.passed)
    failed_checks = len(result.checks) - passed_checks
    status = "PASS" if result.passed else "FAIL"

    print("=" * 72)
    print("Phase 3 Stage 1 - Structural Dataset Validation")
    print("=" * 72)
    print(f"Dataset        : {result.dataset_path}")
    print(f"Version        : {result.dataset_version or 'unknown'}")
    print(f"Phase 1 catalog: {result.phase1_path}")
    print(f"Questions      : {result.questions_checked}")
    print(f"Claims         : {result.claims_checked}")
    print(f"Source domains : {result.source_domains_checked}")
    if result.sources_checked:
        print(f"Sources        : {result.sources_checked}")
    print(f"Domains        : {_format_counter(result.domain_counts)}")
    print(f"Difficulty     : {_format_counter(result.difficulty_counts)}")
    print(f"Task types     : {_format_counter(result.task_type_counts)}")
    print(f"Verification   : {_format_counter(result.verification_distribution)}")
    if result.claim_status_counts:
        print(f"Claim statuses : {_format_counter(result.claim_status_counts)}")
        print(
            f"Verified Qs    : {result.v2_questions_verified}/{result.questions_checked}"
        )
    print("-" * 72)

    for check in result.checks:
        print(f"[{'PASS' if check.passed else 'FAIL'}] {check.name}: {check.description}")
        for issue in check.issues:
            print(f"       - {issue}")

    print("-" * 72)
    print(f"Checks: {passed_checks} passed, {failed_checks} failed")
    print(f"RESULT: {status}")
    if result.observations:
        print("Notes:")
        for observation in result.observations:
            print(f"  - {observation}")
    print("=" * 72)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        description="Read-only structural validator for the Phase 3 benchmark dataset."
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=DEFAULT_DATASET,
        help=f"Path to benchmark JSON (default: {DEFAULT_DATASET})",
    )
    parser.add_argument(
        "--phase1-report",
        type=Path,
        default=DEFAULT_PHASE1_REPORT,
        help=f"Path to Phase 1 report (default: {DEFAULT_PHASE1_REPORT})",
    )
    parser.add_argument(
        "--phase2-report",
        type=Path,
        default=DEFAULT_PHASE2_REPORT,
        help=f"Path to Phase 2 report for informational count comparison (default: {DEFAULT_PHASE2_REPORT})",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Optional Markdown report output path.",
    )
    args = parser.parse_args(argv)

    validator = DatasetValidator(
        dataset_path=args.dataset.resolve(),
        phase1_path=args.phase1_report.resolve(),
        phase2_path=args.phase2_report.resolve(),
    )
    result = validator.run()
    print_summary(result)

    if args.report is not None:
        report_path = args.report.resolve()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with report_path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(render_markdown(result))
        print(f"Report written: {report_path}")

    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
