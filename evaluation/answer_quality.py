"""Answer-quality evaluation layered on the existing deterministic metrics.

This module deliberately composes the project's existing lexical/structural
helpers instead of re-implementing them. It adds the specific RAG dimensions
requested by the evaluation framework:

* contextual precision (are cited sources relevant to the claim?),
* contextual recall (are reference findings covered by the report?),
* faithfulness (are report claims supported by cited evidence?),
* answer relevance,
* citation support (content support, not just URL format),
* unsupported-claim diagnostics,
* evidence coverage.

All scores are deterministic lexical proxies. They are not entailment, human
judgement, or ground truth. Unmeasured dimensions stay ``None``.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from evaluation.metrics import (
    MIN_EVIDENCE_TOKENS,
    PARTIAL_SUPPORT_THRESHOLD,
    build_component,
    evidence_coverage,
    evidence_factual_support,
    matching_tokens,
    relevance,
    text_coverage,
)
from tools.report_parsing import count_words

__all__ = [
    "ANSWER_QUALITY_WEIGHTS",
    "answer_relevance",
    "citation_support",
    "contextual_precision",
    "contextual_recall",
    "evaluate_answer_quality",
    "evaluate_case_answer_quality",
    "evaluate_expected_behavior",
    "evidence_coverage_component",
    "faithfulness",
    "unsupported_claims",
]

ANSWER_QUALITY_WEIGHTS: Dict[str, float] = {
    "faithfulness": 0.30,
    "answer_relevance": 0.20,
    "contextual_precision": 0.15,
    "contextual_recall": 0.15,
    "citation_support": 0.15,
    "evidence_coverage": 0.05,
}


def _finding_text(finding: Any) -> str:
    if not isinstance(finding, dict):
        return ""
    for key in ("claim", "text", "finding", "statement"):
        value = finding.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _source_text(record: Any) -> str:
    if not isinstance(record, dict):
        return ""
    return " ".join(
        value
        for value in (record.get("title"), record.get("excerpt"))
        if isinstance(value, str) and value.strip()
    )


def _registry_by_id(source_registry: Sequence[Any]) -> Dict[str, Dict[str, Any]]:
    return {
        str(record.get("source_id")): record
        for record in source_registry or []
        if isinstance(record, dict) and record.get("source_id")
    }


def _finding_sources(
    finding: Any, registry: Dict[str, Dict[str, Any]]
) -> Tuple[List[str], List[str]]:
    if not isinstance(finding, dict):
        return [], []
    raw_ids = finding.get("source_ids")
    source_ids = [
        str(source_id)
        for source_id in (raw_ids if isinstance(raw_ids, list) else [])
        if isinstance(source_id, str) and source_id.strip()
    ]
    source_ids = list(dict.fromkeys(source_ids))
    valid = [source_id for source_id in source_ids if source_id in registry]
    invalid = [source_id for source_id in source_ids if source_id not in registry]
    return valid, invalid


def _extract_report(report: Any) -> Dict[str, Any]:
    if report is None:
        return {"text": "", "sources": [], "key_findings": [], "unsupported": []}
    if isinstance(report, str):
        return {
            "text": report,
            "sources": [],
            "key_findings": [],
            "unsupported": [],
        }
    if isinstance(report, dict):
        text = report.get("report") or report.get("report_content") or ""
        return {
            "text": text if isinstance(text, str) else str(text),
            "sources": list(report.get("sources") or []),
            "key_findings": list(report.get("key_findings") or []),
            "unsupported": list(report.get("unsupported_citations") or []),
        }
    text = getattr(report, "report", "") or ""
    return {
        "text": text if isinstance(text, str) else str(text),
        "sources": list(getattr(report, "sources", None) or []),
        "key_findings": list(getattr(report, "key_findings", None) or []),
        "unsupported": list(getattr(report, "unsupported_citations", None) or []),
    }


def contextual_precision(
    key_findings: Sequence[Any],
    source_registry: Sequence[Any],
    relevance_threshold: float = PARTIAL_SUPPORT_THRESHOLD,
) -> Dict[str, Any]:
    """Share of cited sources whose excerpt is relevant to the cited claim.

    For each structured finding with at least one valid source ID, compute the
    lexical coverage of the claim by each cited source's title/excerpt. A source
    is counted as relevant when coverage reaches ``relevance_threshold``. The
    score is the mean of per-finding relevant/cited ratios.

    Findings without a valid citation are excluded here (they are reported by
    ``citation_support`` and ``unsupported_claims``).
    """
    registry = _registry_by_id(source_registry)
    details: List[Dict[str, Any]] = []
    ratios: List[float] = []

    for finding in key_findings or []:
        claim = _finding_text(finding)
        if not claim:
            continue
        valid_ids, invalid_ids = _finding_sources(finding, registry)
        if not valid_ids:
            continue
        source_results = []
        relevant_count = 0
        for source_id in valid_ids:
            coverage = text_coverage(_source_text(registry[source_id]), claim)
            is_relevant = coverage >= relevance_threshold
            relevant_count += 1 if is_relevant else 0
            source_results.append(
                {
                    "source_id": source_id,
                    "coverage": coverage,
                    "relevant": is_relevant,
                }
            )
        ratio = relevant_count / len(valid_ids)
        ratios.append(ratio)
        details.append(
            {
                "claim": claim,
                "valid_source_ids": valid_ids,
                "invalid_source_ids": invalid_ids,
                "relevant_sources": relevant_count,
                "cited_sources": len(valid_ids),
                "precision": round(ratio, 4),
                "sources": source_results,
            }
        )

    if not ratios:
        return build_component(
            None,
            "No finding carried a valid cited source; contextual precision is not measurable.",
            status="not_measurable",
            details={"claims_evaluated": 0, "relevance_threshold": relevance_threshold},
        )

    return build_component(
        sum(ratios) / len(ratios),
        "Mean per-claim share of cited sources whose title/excerpt lexically "
        "covers the claim at the relevance threshold. Citation relevance only; "
        "not factual correctness.",
        details={
            "claims_evaluated": len(ratios),
            "relevance_threshold": relevance_threshold,
            "claim_results": details,
        },
    )


def contextual_recall(
    report_text: str,
    reference_findings: Sequence[Any],
    threshold: float = 0.55,
) -> Dict[str, Any]:
    """Share of reference findings whose content is covered by the report.

    This is the deterministic counterpart of contextual recall: it uses the
    verified or expected findings supplied by the dataset and measures lexical
    coverage of each finding by the report. It is not semantic entailment.
    """
    expected = [
        _finding_text(finding)
        for finding in reference_findings or []
        if _finding_text(finding)
    ]
    if not expected:
        return build_component(
            None,
            "No reference findings were supplied; contextual recall is not measurable.",
            status="not_measurable",
            details={"reference_findings": 0, "threshold": threshold},
        )

    results = []
    supported = 0
    for text in expected:
        coverage = text_coverage(report_text, text)
        is_supported = coverage >= threshold
        supported += 1 if is_supported else 0
        results.append(
            {
                "reference_finding": text,
                "coverage": coverage,
                "supported": is_supported,
            }
        )

    return build_component(
        supported / len(expected),
        "Share of expected reference findings whose content tokens are covered "
        "by the report at the threshold. Lexical proxy, not entailment.",
        details={
            "reference_findings": len(expected),
            "supported_findings": supported,
            "threshold": threshold,
            "findings": results,
        },
    )


def faithfulness(
    report_text: str,
    key_findings: Sequence[Any],
    source_registry: Sequence[Any],
    threshold: float = 0.55,
) -> Dict[str, Any]:
    """Strict share of verifiable report claims supported by cited evidence.

    This reuses ``evidence_factual_support`` for classification, but reports a
    strict faithfulness score (supported / verifiable). Partial support is
    retained in details and never silently counted as full support.
    """
    result = evidence_factual_support(
        report_text,
        key_findings,
        source_registry,
        threshold=threshold,
    )
    details = dict(result.get("details") or {})
    verifiable = int(details.get("verifiable_claims") or 0)
    supported = int(details.get("claims_supported") or 0)
    if result.get("status") == "not_measurable" or verifiable == 0:
        return build_component(
            None,
            result.get("method")
            or "No verifiable claim had usable cited evidence; faithfulness is not measurable.",
            status="not_measurable",
            details=details,
        )
    details["partial_credit_score"] = result.get("score")
    details["strict_faithfulness"] = round(supported / verifiable, 4)
    return build_component(
        supported / verifiable,
        "Strict faithfulness: share of verifiable report claims whose cited "
        "source excerpts cover the claim at the threshold. Partial support is "
        "reported but not counted as full support. Lexical proxy, not entailment.",
        details=details,
    )


def answer_relevance(
    report_text: str,
    question: str,
    target_aspects: Sequence[str] = (),
) -> Dict[str, Any]:
    """Wrap the existing lexical relevance metric for answer quality."""
    component = relevance(report_text, question, target_aspects)
    component = dict(component)
    component["method"] = (
        "Lexical coverage: 0.7 mean(target-aspect token coverage) + "
        "0.3 question-token coverage. Not semantic or factual."
    )
    return component


def citation_support(
    key_findings: Sequence[Any],
    source_registry: Sequence[Any],
    threshold: float = 0.55,
) -> Dict[str, Any]:
    """Separate citation-format validity from factual citation support.

    * format-valid: finding has at least one source ID that exists in the
      retrieved source registry;
    * content-supported: at least one valid cited source excerpt covers the
      finding at ``threshold``.

    The score is ``content_supported / cited_findings``. A citation can be
    format-valid and still fail content support. This is still lexical overlap,
    not entailment or truth.
    """
    registry = _registry_by_id(source_registry)
    findings = [finding for finding in (key_findings or []) if isinstance(finding, dict)]
    cited = 0
    format_valid = 0
    content_supported = 0
    results: List[Dict[str, Any]] = []

    for finding in findings:
        claim = _finding_text(finding)
        if not claim:
            continue
        valid_ids, invalid_ids = _finding_sources(finding, registry)
        has_citation = bool(valid_ids or invalid_ids)
        if has_citation:
            cited += 1
        is_format_valid = bool(valid_ids)
        format_valid += 1 if is_format_valid else 0
        best_coverage = 0.0
        if valid_ids:
            best_coverage = max(
                text_coverage(_source_text(registry[source_id]), claim)
                for source_id in valid_ids
            )
        is_supported = is_format_valid and best_coverage >= threshold
        content_supported += 1 if is_supported else 0
        results.append(
            {
                "claim": claim,
                "valid_source_ids": valid_ids,
                "invalid_source_ids": invalid_ids,
                "has_citation": has_citation,
                "format_valid": is_format_valid,
                "best_coverage": round(best_coverage, 4),
                "content_supported": is_supported,
            }
        )

    if cited == 0:
        return build_component(
            0.0,
            "No finding carried a citation; citation support is zero with "
            "status no_citations. This is not a factual-correctness score.",
            status="no_citations",
            details={
                "findings": len(findings),
                "cited_findings": 0,
                "format_valid_citations": 0,
                "content_supported_citations": 0,
                "citation_format_validity": 0.0,
                "results": results,
            },
        )

    return build_component(
        content_supported / cited,
        "Share of cited findings whose cited source excerpt covers the claim at "
        "the threshold. Format validity (valid source ID) is reported separately. "
        "Content support is lexical overlap, not entailment.",
        details={
            "findings": len(findings),
            "cited_findings": cited,
            "format_valid_citations": format_valid,
            "content_supported_citations": content_supported,
            "citation_format_validity": round(format_valid / cited, 4),
            "threshold": threshold,
            "results": results,
        },
    )


def unsupported_claims(
    key_findings: Sequence[Any],
    source_registry: Sequence[Any],
    threshold: float = 0.55,
) -> Dict[str, Any]:
    """Diagnostic list and share of claims lacking valid content support.

    This is a lower-is-better diagnostic. It is not included in the overall
    answer-quality weighting because it would double-count citation support.
    """
    registry = _registry_by_id(source_registry)
    findings = [finding for finding in (key_findings or []) if isinstance(finding, dict)]
    unsupported: List[Dict[str, Any]] = []
    partially_supported: List[Dict[str, Any]] = []

    for finding in findings:
        claim = _finding_text(finding)
        if not claim:
            continue
        valid_ids, invalid_ids = _finding_sources(finding, registry)
        best = 0.0
        if valid_ids:
            best = max(
                text_coverage(_source_text(registry[source_id]), claim)
                for source_id in valid_ids
            )
        record = {
            "claim": claim,
            "valid_source_ids": valid_ids,
            "invalid_source_ids": invalid_ids,
            "best_coverage": round(best, 4),
        }
        if not valid_ids or best < PARTIAL_SUPPORT_THRESHOLD:
            unsupported.append(record)
        elif best < threshold:
            partially_supported.append(record)

    total = len([finding for finding in findings if _finding_text(finding)])
    if total == 0:
        return build_component(
            None,
            "No checkable findings were supplied; unsupported-claim rate is not measurable.",
            status="not_measurable",
            details={"claims": 0, "unsupported": 0, "partially_supported": 0},
        )

    return build_component(
        len(unsupported) / total,
        "Share of checkable findings with no valid content-supported citation. "
        "Lower is better. Diagnostic only; not part of the overall weighting.",
        status="measured",
        details={
            "claims": total,
            "unsupported": len(unsupported),
            "partially_supported": len(partially_supported),
            "unsupported_claims": unsupported,
            "partially_supported_claims": partially_supported,
            "partial_threshold": PARTIAL_SUPPORT_THRESHOLD,
            "support_threshold": threshold,
            "direction": "lower_is_better",
        },
    )


def evidence_coverage_component(
    research_metrics: Optional[Dict[str, Any]] = None,
    grounding: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Wrap the existing evidence-coverage metric with a stable name."""
    return evidence_coverage(research_metrics or {}, grounding or {})


def _overall(components: Dict[str, Dict[str, Any]]) -> Tuple[Optional[float], Dict[str, float]]:
    available = {
        name: weight
        for name, weight in ANSWER_QUALITY_WEIGHTS.items()
        if isinstance(components.get(name), dict)
        and components[name].get("score") is not None
    }
    total = sum(available.values())
    if total <= 0:
        return None, {}
    weights = {name: round(weight / total, 4) for name, weight in available.items()}
    score = sum(float(components[name]["score"]) * weights[name] for name in weights)
    return round(score, 4), weights


def evaluate_answer_quality(
    *,
    question: str,
    report: Any,
    source_registry: Sequence[Any],
    reference_findings: Sequence[Any] = (),
    research_metrics: Optional[Dict[str, Any]] = None,
    grounding: Optional[Dict[str, Any]] = None,
    target_aspects: Sequence[str] = (),
    threshold: float = 0.55,
) -> Dict[str, Any]:
    """Evaluate answer quality with explicit measured/unmeasured components."""
    fields = _extract_report(report)
    text = fields["text"]
    components = {
        "faithfulness": faithfulness(
            text, fields["key_findings"], source_registry, threshold=threshold
        ),
        "answer_relevance": answer_relevance(text, question, target_aspects),
        "contextual_precision": contextual_precision(
            fields["key_findings"], source_registry
        ),
        "contextual_recall": contextual_recall(
            text, reference_findings, threshold=threshold
        ),
        "citation_support": citation_support(
            fields["key_findings"], source_registry, threshold=threshold
        ),
        "unsupported_claims": unsupported_claims(
            fields["key_findings"], source_registry, threshold=threshold
        ),
        "evidence_coverage": evidence_coverage_component(
            research_metrics, grounding
        ),
    }
    overall, weights = _overall(components)
    measured = [
        name for name, component in components.items() if component.get("score") is not None
    ]
    unmeasured = [
        name for name, component in components.items() if component.get("score") is None
    ]
    return {
        "evaluator_version": "answer-quality-1.0",
        "question": question,
        "report": {
            "word_count": count_words(text),
            "source_count": len(fields["sources"]),
            "finding_count": len(fields["key_findings"]),
        },
        "components": components,
        "overall_score": overall,
        "weights_used": weights,
        "measured_components": measured,
        "unmeasured_components": unmeasured,
        "limitations": [
            "All components are deterministic lexical/structural proxies.",
            "Citation support is not factual verification or entailment.",
            "Unmeasured components are excluded from the weighting, never imputed as zero.",
            "Contextual recall requires reference findings; it is not measurable for open-topic runs.",
        ],
    }


def evaluate_case_answer_quality(
    case: Dict[str, Any],
    state: Dict[str, Any],
    *,
    threshold: float = 0.55,
) -> Dict[str, Any]:
    """Convenience adapter for a golden case and a LangGraph final state."""
    state = state or {}
    report = state.get("report")
    registry = state.get("source_registry") or []
    retrieved = state.get("sources") or []
    grounding = {
        "retrieved_sources_count": len(retrieved),
        "grounded_sources_count": state.get("grounded_sources_count"),
    }
    return evaluate_answer_quality(
        question=str((case or {}).get("question") or ""),
        report=report,
        source_registry=registry,
        reference_findings=(case or {}).get("expected_key_findings") or [],
        research_metrics=state.get("research_metrics") or {},
        grounding=grounding,
        target_aspects=((case or {}).get("metadata") or {}).get("target_aspects") or [],
        threshold=threshold,
    )


_BEHAVIOUR_CUES: Dict[str, Tuple[str, ...]] = {
    "insufficient_evidence": (
        "insufficient evidence",
        "insufficient information",
        "evidence is insufficient",
        "insufficient to answer",
        "not enough evidence",
        "no usable evidence",
        "unable to verify",
        "cannot verify",
        "not supported by the retrieved evidence",
        "no reliable",
        "no public",
        "not publicly measured",
        "cannot be determined",
        "could not be determined",
    ),
    "conflicting_sources": (
        "conflict",
        "disagree",
        "inconsistent",
        "competing",
        "whereas",
        "on the other hand",
        "differs",
        "mixed evidence",
        "no consensus",
    ),
    "misleading_assumption": (
        "false premise",
        "incorrect",
        "does not",
        "is not",
        "not the same",
        "misleading",
        "contrary",
        "cannot",
        "reject",
        "no evidence",
    ),
    "recent_information": (
        "as of",
        "retrieved",
        "cutoff",
        "publication date",
        "version",
        "2026",
        "latest available",
        "cannot verify recency",
        "not dated",
    ),
}


def evaluate_expected_behavior(
    case: Dict[str, Any],
    report: Any,
) -> Dict[str, Any]:
    """Heuristic compliance check for synthetic behavioural cases.

    This checks whether the report contains an appropriate disclosure/correction
    cue and does not cite unsupported URLs. It is a behaviour proxy, not a
    factual correctness judgement, and it has no verified reference answer.
    """
    category = str((case or {}).get("category") or "")
    cues = _BEHAVIOUR_CUES.get(category, ())
    fields = _extract_report(report)
    text = fields["text"].lower()
    matched = [cue for cue in cues if cue in text]
    unsupported = list(fields["unsupported"] or [])
    cited_ok = not unsupported
    cue_ok = bool(matched)
    score = 1.0 if cue_ok and cited_ok else 0.0
    return build_component(
        score,
        "Heuristic behaviour compliance: required disclosure/correction cue is "
        "present and no unsupported citation was emitted. Not factual correctness.",
        details={
            "category": category,
            "expected_behavior": (case or {}).get("expected_behavior"),
            "matched_cues": matched,
            "required_cue_present": cue_ok,
            "unsupported_citations": unsupported,
            "no_unsupported_citations": cited_ok,
            "heuristic": True,
        },
    )
