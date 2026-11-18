"""Reproducible evaluation engine for benchmark questions and open research runs.

The engine produces an explicit, JSON-serialisable evaluation record. It never
changes benchmark datasets, never promotes unresolved claims, and never
reports citation alignment as factual correctness.

Two modes are supported:

``benchmark``
    A generated report is scored against one verified benchmark question:
    relevance, evidence coverage, citation quality, completeness, and a
    lexical factual-support proxy over the question's *verified* claims.
    Unresolved claims (``partially_supported`` / ``needs_more_evidence``)
    are recorded separately and excluded from the factual-support score.

``open_topic``
    A generated report from an arbitrary research run is scored with the
    measurable components (relevance to the plan, evidence coverage, citation
    quality, completeness) plus a deterministic evidence-based factual-support
    check: report claims are compared with the excerpts of the sources they
    cite. Claims without usable evidence are ``unverifiable`` and excluded
    rather than counted as false.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from evaluation.metrics import (
    CANONICAL_SECTIONS,
    DEFAULT_SUPPORT_THRESHOLD,
    build_component,
    claim_support,
    citation_quality,
    completeness,
    evidence_coverage,
    evidence_factual_support,
    overall_score,
    relevance,
    section_presence,
)
from tools.report_parsing import count_words

__all__ = [
    "EVALUATION_RECORD_VERSION",
    "dataset_readiness",
    "evaluate_open_state",
    "evaluate_report",
    "evaluate_state",
    "get_question",
    "load_benchmark_dataset",
    "record_digest",
]

EVALUATION_RECORD_VERSION = "phase3-evaluation-1.0"

_METHODOLOGY_NOTES = [
    "Scores are lexical/structural measurements with explicit definitions; they are not "
    "semantic understanding or factual verification.",
    "Citation quality measures whether emitted URLs were actually retrieved. A retrieved "
    "URL can still be cited for a claim it does not support.",
    "Factual support is a lexical proxy: in benchmark mode, coverage of verified "
    "reference claims; in open-topic mode, coverage of report claims by the "
    "excerpts of the sources they cite. It is not entailment, source accuracy, or truth.",
    "Open-topic claims without usable source evidence are unverifiable and are "
    "excluded from the factual-support denominator, never scored as false.",
    "Claims marked partially_supported or needs_more_evidence are excluded from the "
    "factual-support denominator and reported as unresolved. They are never promoted.",
    "Unmeasured components remain null and are removed from the overall weighting rather "
    "than being imputed as zero.",
]

_REQUIRED_QUESTION_FIELDS = ("id", "topic", "metadata", "ground_truth", "evaluation_rubric")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def load_benchmark_dataset(path: str | Path) -> Dict[str, Any]:
    """Load a benchmark dataset and attach its file hash.

    Raises:
        FileNotFoundError: if the dataset does not exist.
        ValueError: if the root object or question records are malformed.
    """
    dataset_path = Path(path)
    with dataset_path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("benchmark dataset root must be a JSON object")
    questions = data.get("benchmark_questions")
    if not isinstance(questions, list) or not questions:
        raise ValueError("benchmark dataset must contain a non-empty benchmark_questions list")
    for index, question in enumerate(questions):
        if not isinstance(question, dict):
            raise ValueError(f"benchmark question[{index}] is not an object")
        for field in _REQUIRED_QUESTION_FIELDS:
            if field not in question:
                raise ValueError(f"benchmark question[{index}] is missing {field!r}")
    return {
        "path": str(dataset_path),
        "filename": dataset_path.name,
        "sha256": _sha256_file(dataset_path),
        "version": data.get("version"),
        "created_at": data.get("created_at"),
        "questions": questions,
        "raw": data,
    }


def get_question(dataset: Dict[str, Any], question_id: str) -> Dict[str, Any]:
    """Return one question by ID, or raise ``KeyError`` with a clear message."""
    for question in dataset.get("questions", []):
        if question.get("id") == question_id:
            return question
    raise KeyError(f"question {question_id!r} not found in benchmark dataset")


def _rubric(question: Dict[str, Any]) -> Dict[str, Any]:
    rubric = question.get("evaluation_rubric")
    return rubric if isinstance(rubric, dict) else {}


def _sections(question: Dict[str, Any]) -> Sequence[str]:
    sections = _rubric(question).get("structural_requirements")
    if isinstance(sections, list) and all(isinstance(item, str) for item in sections):
        return sections
    return CANONICAL_SECTIONS


def _minimum_words(question: Dict[str, Any]) -> int:
    value = _rubric(question).get("minimum_word_count")
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return 250


def _aspects(question: Dict[str, Any]) -> List[str]:
    metadata = question.get("metadata")
    if not isinstance(metadata, dict):
        return []
    aspects = metadata.get("target_aspects")
    if not isinstance(aspects, list):
        return []
    return [item for item in aspects if isinstance(item, str) and item.strip()]


def _claims(question: Dict[str, Any]) -> List[Dict[str, Any]]:
    ground_truth = question.get("ground_truth")
    if not isinstance(ground_truth, dict):
        return []
    claims = ground_truth.get("claims")
    if isinstance(claims, list):
        return [claim for claim in claims if isinstance(claim, dict)]
    # Version 1 datasets only expose provisional_reference_claims (unverified).
    provisional = ground_truth.get("provisional_reference_claims")
    if isinstance(provisional, list):
        return [
            {"claim_id": "", "claim_text": text, "status": "unverified"}
            for text in provisional
            if isinstance(text, str)
        ]
    return []


def _extract_report_fields(report: Any) -> Dict[str, Any]:
    """Normalise a ResearchReport, dict, or plain string into report fields."""
    if report is None:
        return {
            "title": "",
            "text": "",
            "sources": [],
            "unsupported_citations": [],
            "key_findings": [],
            "generation_mode": None,
            "structured": False,
        }
    if isinstance(report, str):
        return {
            "title": "",
            "text": report,
            "sources": [],
            "unsupported_citations": [],
            "key_findings": [],
            "generation_mode": None,
            "structured": False,
        }
    if isinstance(report, dict):
        text = report.get("report") or report.get("report_content") or ""
        return {
            "title": report.get("title") or "",
            "text": text if isinstance(text, str) else str(text),
            "sources": list(report.get("sources") or []),
            "unsupported_citations": list(report.get("unsupported_citations") or []),
            "key_findings": list(report.get("key_findings") or []),
            "generation_mode": report.get("generation_mode"),
            "structured": True,
        }
    text = getattr(report, "report", "") or ""
    return {
        "title": getattr(report, "title", "") or "",
        "text": text if isinstance(text, str) else str(text),
        "sources": list(getattr(report, "sources", None) or []),
        "unsupported_citations": list(getattr(report, "unsupported_citations", None) or []),
        "key_findings": list(getattr(report, "key_findings", None) or []),
        "generation_mode": getattr(report, "generation_mode", None),
        "structured": True,
    }


def _unsupported_citations(
    fields: Dict[str, Any], grounding: Dict[str, Any]
) -> List[str]:
    """Merge report-level and state-level phantom URLs into one list.

    A URL can be flagged as unsupported by the grounding pass even when the
    report payload does not repeat the list. Both sources must reduce citation
    quality, otherwise a state-level phantom citation would score as aligned.
    """
    combined: List[str] = []
    for value in list(fields.get("unsupported_citations") or []):
        if isinstance(value, str) and value.strip():
            combined.append(value)
    for value in list(grounding.get("phantom_urls") or []):
        if isinstance(value, str) and value.strip():
            combined.append(value)
    return list(dict.fromkeys(combined))


def _claim_citation_coverage(
    key_findings: Sequence[Any], grounded: Optional[float]
) -> Optional[float]:
    if isinstance(grounded, (int, float)):
        return round(float(grounded), 4)
    findings = [f for f in (key_findings or []) if isinstance(f, dict)]
    if not findings:
        return None
    cited = sum(1 for finding in findings if finding.get("source_ids"))
    return round(cited / len(findings), 4)


def _build_record(
    *,
    mode: str,
    question: Optional[Dict[str, Any]],
    report: Any,
    dataset: Optional[Dict[str, Any]] = None,
    grounding: Optional[Dict[str, Any]] = None,
    research_metrics: Optional[Dict[str, Any]] = None,
    claim_citation_coverage: Optional[float] = None,
    support_threshold: float = DEFAULT_SUPPORT_THRESHOLD,
    source_registry: Optional[Sequence[Dict[str, Any]]] = None,
    evaluated_at: Optional[str] = None,
) -> Dict[str, Any]:
    fields = _extract_report_fields(report)
    report_text = fields["text"]

    topic = ""
    aspects: List[str] = []
    sections: Sequence[str] = CANONICAL_SECTIONS
    minimum_words = 250
    claims: List[Dict[str, Any]] = []
    question_id: Optional[str] = None
    domain = None

    if question is not None:
        question_id = question.get("id")
        topic = str(question.get("topic") or "")
        aspects = _aspects(question)
        sections = _sections(question)
        minimum_words = _minimum_words(question)
        claims = _claims(question)
        domain = question.get("domain")

    grounding_data = grounding or {}
    if grounding is None and question is not None:
        # No explicit grounding block: treat report citations as the only
        # available alignment evidence. No retrieved-source denominator.
        grounding_data = {}

    metrics: Dict[str, Dict[str, Any]] = {
        "relevance": relevance(report_text, topic, aspects),
        "evidence_coverage": evidence_coverage(research_metrics, grounding_data),
        "citation_quality": citation_quality(
            fields["sources"],
            _unsupported_citations(fields, grounding_data),
            _claim_citation_coverage(fields["key_findings"], claim_citation_coverage),
        ),
        "completeness": completeness(report_text, sections, minimum_words),
        "factual_support": (
            evidence_factual_support(
                report_text,
                fields["key_findings"],
                source_registry or [],
                threshold=support_threshold,
            )
            if mode == "open_topic"
            else claim_support(report_text, claims, threshold=support_threshold)
        ),
    }

    score, weights_used = overall_score(metrics)
    measured = [name for name, component in metrics.items() if component.get("score") is not None]
    unmeasured = [name for name, component in metrics.items() if component.get("score") is None]

    record: Dict[str, Any] = {
        "record_version": EVALUATION_RECORD_VERSION,
        "evaluated_at": evaluated_at
        or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": mode,
        "dataset": (
            {
                "filename": dataset.get("filename"),
                "version": dataset.get("version"),
                "sha256": dataset.get("sha256"),
            }
            if dataset
            else None
        ),
        "question": {
            "id": question_id,
            "domain": domain,
            "topic": topic,
        },
        "report": {
            "title": fields["title"],
            "word_count": count_words(report_text),
            "structured": fields["structured"],
            "generation_mode": fields["generation_mode"],
        },
        "metrics": metrics,
        "overall_score": score,
        "component_weights_used": weights_used,
        "measured_components": measured,
        "unmeasured_components": unmeasured,
        "claim_results": metrics["factual_support"].get("details", {}).get("claim_results", []),
        "unresolved_claims": metrics["factual_support"].get("details", {}).get(
            "unresolved_claims", []
        ),
        "methodology_notes": list(_METHODOLOGY_NOTES),
    }
    return record


def evaluate_report(
    question: Dict[str, Any],
    report: Any,
    *,
    dataset: Optional[Dict[str, Any]] = None,
    grounding: Optional[Dict[str, Any]] = None,
    research_metrics: Optional[Dict[str, Any]] = None,
    claim_citation_coverage: Optional[float] = None,
    support_threshold: float = DEFAULT_SUPPORT_THRESHOLD,
    evaluated_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Evaluate one generated report against one benchmark question."""
    if not isinstance(question, dict):
        raise ValueError("question must be a dictionary")
    return _build_record(
        mode="benchmark",
        question=question,
        report=report,
        dataset=dataset,
        grounding=grounding,
        research_metrics=research_metrics,
        claim_citation_coverage=claim_citation_coverage,
        support_threshold=support_threshold,
        evaluated_at=evaluated_at,
    )


def evaluate_state(
    question: Dict[str, Any],
    state: Dict[str, Any],
    *,
    dataset: Optional[Dict[str, Any]] = None,
    support_threshold: float = DEFAULT_SUPPORT_THRESHOLD,
) -> Dict[str, Any]:
    """Evaluate a LangGraph final state against a benchmark question."""
    state = state or {}
    report = state.get("report")
    retrieved = state.get("sources") or []
    grounding = {
        "retrieved_sources_count": len(retrieved),
        "grounded_sources_count": state.get("grounded_sources_count"),
        "citation_grounding_ratio": state.get("citation_grounding_ratio"),
        "phantom_urls": list(state.get("unsupported_citations") or []),
    }
    return evaluate_report(
        question,
        report,
        dataset=dataset,
        grounding=grounding,
        research_metrics=state.get("research_metrics") or {},
        claim_citation_coverage=state.get("claim_citation_coverage"),
        support_threshold=support_threshold,
    )


def evaluate_open_state(
    topic: str,
    state: Dict[str, Any],
    *,
    plan: Optional[Any] = None,
    support_threshold: float = DEFAULT_SUPPORT_THRESHOLD,
) -> Dict[str, Any]:
    """Evaluate an open research run where no verified reference claims exist.

    Relevance uses the plan's focus areas as target aspects when available.
    Factual support is ``not_measurable`` and is excluded from the weighting.
    """
    state = state or {}
    plan = plan if plan is not None else state.get("plan")

    aspects: List[str] = []
    plan_topic = topic
    if plan is not None:
        plan_topic = getattr(plan, "topic", None) or (
            plan.get("topic") if isinstance(plan, dict) else topic
        ) or topic
        focus = getattr(plan, "focus_areas", None) or (
            plan.get("focus_areas") if isinstance(plan, dict) else None
        )
        if isinstance(focus, list):
            aspects = [item for item in focus if isinstance(item, str) and item.strip()]

    synthetic_question = {
        "id": None,
        "domain": None,
        "topic": plan_topic,
        "metadata": {"target_aspects": aspects},
        "ground_truth": {"claims": []},
        "evaluation_rubric": {
            "structural_requirements": list(CANONICAL_SECTIONS),
            "minimum_word_count": 250,
        },
    }

    report = state.get("report")
    retrieved = state.get("sources") or []
    grounding = {
        "retrieved_sources_count": len(retrieved),
        "grounded_sources_count": state.get("grounded_sources_count"),
        "citation_grounding_ratio": state.get("citation_grounding_ratio"),
        "phantom_urls": list(state.get("unsupported_citations") or []),
    }
    record = _build_record(
        mode="open_topic",
        question=synthetic_question,
        report=report,
        dataset=None,
        grounding=grounding,
        research_metrics=state.get("research_metrics") or {},
        claim_citation_coverage=state.get("claim_citation_coverage"),
        support_threshold=support_threshold,
        source_registry=state.get("source_registry") or [],
    )
    record["methodology_notes"].append(
        "Open-topic mode: factual_support is a deterministic check of report "
        "claims against cited source excerpts. Unverifiable claims are excluded, "
        "not scored as false; the metric is lexical overlap, not entailment."
    )
    return record


def dataset_readiness(dataset: Dict[str, Any]) -> Dict[str, Any]:
    """Summarise benchmark verification readiness without changing the dataset.

    This is an audit of recorded verification state, not an execution result.
    """
    questions = dataset.get("questions", [])
    claims = [
        claim
        for question in questions
        for claim in _claims(question)
    ]
    status_counts: Dict[str, int] = {}
    for claim in claims:
        status = str(claim.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1

    unresolved = [claim for claim in claims if claim.get("status") != "verified"]
    verified_questions = 0
    unresolved_questions: List[str] = []
    domain_mismatch_questions = 0
    substitution_records = 0
    for question in questions:
        ground_truth = question.get("ground_truth")
        if isinstance(ground_truth, dict) and ground_truth.get("verification_status") == "verified":
            verified_questions += 1
        else:
            unresolved_questions.append(str(question.get("id")))
        metadata = question.get("metadata")
        audit = metadata.get("source_domain_audit") if isinstance(metadata, dict) else None
        if isinstance(audit, dict):
            mismatches = audit.get("outside_expected_source_domains") or []
            if mismatches:
                domain_mismatch_questions += 1
            substitutions = audit.get("substitutions") or []
            substitution_records += len(substitutions) if isinstance(substitutions, list) else 0

    sources = dataset.get("raw", {}).get("sources") or []
    insufficient_sources = [
        source.get("source_id")
        for source in sources
        if isinstance(source, dict)
        and source.get("verification_status") == "insufficiently_verified"
    ]

    return {
        "record_version": EVALUATION_RECORD_VERSION,
        "dataset": {
            "filename": dataset.get("filename"),
            "version": dataset.get("version"),
            "sha256": dataset.get("sha256"),
            "created_at": dataset.get("created_at"),
        },
        "questions_total": len(questions),
        "claims_total": len(claims),
        "claim_status_counts": status_counts,
        "verified_claims": status_counts.get("verified", 0),
        "unresolved_claims": len(unresolved),
        "unresolved_claim_ids": [claim.get("claim_id") for claim in unresolved],
        "verified_questions": verified_questions,
        "unresolved_question_ids": unresolved_questions,
        "sources_total": len(sources),
        "insufficient_sources": insufficient_sources,
        "domain_mismatch_questions": domain_mismatch_questions,
        "domain_substitution_records": substitution_records,
        "methodology_notes": [
            "Readiness counts describe the recorded verification state of the dataset.",
            "They are not benchmark execution results and do not imply the agent answers correctly.",
            "Unresolved claims remain unresolved; this audit does not modify the dataset.",
        ],
    }


def record_digest(record: Dict[str, Any]) -> str:
    """Stable SHA-256 of a record's deterministic content.

    ``evaluated_at`` and any existing ``record_digest`` are excluded so the
    same inputs always produce the same digest.
    """
    canonical = {
        key: value
        for key, value in (record or {}).items()
        if key not in {"evaluated_at", "record_digest"}
    }
    return hashlib.sha256(_canonical_json(canonical).encode("utf-8")).hexdigest()
