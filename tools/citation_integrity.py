"""Citation-to-source integrity checks for generated research reports.

The existing grounding module answers a narrow question: did an emitted URL
appear in the retrieved set? That is useful, but a URL can exist and still be
the wrong citation for a claim. This module adds evidence-text checks while
keeping the historical grounding metric unchanged.

Definitions used here:

``format_valid``
    The finding cites at least one ``SRC-*`` identifier that exists in the
    source registry.

``content_supported``
    The cited source title/excerpt covers the finding claim at the configured
    lexical threshold. This is still a lexical proxy, not entailment.

``displayed sources``
    Unique canonical URLs in the report's ``sources`` list.

``cited sources``
    Unique canonical URLs belonging to source IDs cited by structured
    findings. The Editor keeps every cited source in the displayed list, so
    ``cited <= displayed`` and no cited source is hidden.

Citation binding is optional and evidence-based: if a model cites a source
that does not support a claim, another *already retrieved* source whose excerpt
does support it may be added. No source content, URL, or identifier is ever
invented. The model's original identifiers are retained for audit.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from evaluation.metrics import (
    MIN_EVIDENCE_TOKENS,
    PARTIAL_SUPPORT_THRESHOLD,
    matching_tokens,
    text_coverage,
)
from tools.research_evidence import canonical_url

__all__ = [
    "CITATION_INTEGRITY_VERSION",
    "add_evidence_limitations",
    "bind_and_verify_findings",
    "build_report_sources",
    "evaluate_citation_integrity",
    "verify_findings",
]

CITATION_INTEGRITY_VERSION = "citation-integrity-1.0"

_LIMITATIONS_HEADING = re.compile(r"^##\s+Limitations\s*$", re.IGNORECASE | re.MULTILINE)


def _number(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    return default


def _finding_text(finding: Any) -> str:
    if not isinstance(finding, dict):
        return ""
    for key in ("claim", "text", "finding", "statement"):
        value = finding.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _unique_ids(values: Any) -> List[str]:
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return []
    result: List[str] = []
    for value in values:
        if isinstance(value, str) and value.strip() and value.strip() not in result:
            result.append(value.strip())
    return result


def _registry_by_id(
    registry: Sequence[Any],
) -> Dict[str, Dict[str, Any]]:
    return {
        str(record.get("source_id")): record
        for record in registry or []
        if isinstance(record, dict) and record.get("source_id")
    }


def _source_text(record: Dict[str, Any]) -> str:
    return " ".join(
        value
        for value in (record.get("title"), record.get("excerpt"))
        if isinstance(value, str) and value.strip()
    )


def _source_url(record: Dict[str, Any]) -> str:
    url = record.get("url")
    return url.strip() if isinstance(url, str) else ""


def _extract_findings(report: Any) -> List[Dict[str, Any]]:
    if report is None:
        return []
    if isinstance(report, dict):
        findings = report.get("key_findings")
    else:
        findings = getattr(report, "key_findings", None)
    return [finding for finding in (findings or []) if isinstance(finding, dict)]


def _extract_sources(report: Any) -> List[str]:
    if report is None:
        return []
    raw = report.get("sources") if isinstance(report, dict) else getattr(
        report, "sources", None
    )
    return [value.strip() for value in (raw or []) if isinstance(value, str) and value.strip()]


def _classify(best_coverage: float, valid_ids: Sequence[str]) -> str:
    if not valid_ids:
        return "invalid_citation"
    if best_coverage >= PARTIAL_SUPPORT_THRESHOLD:
        return "partially_supported"
    return "insufficient_evidence"


def verify_findings(
    findings: Sequence[Any],
    registry: Sequence[Any],
    *,
    threshold: float = 0.55,
    bind: bool = False,
    binding_registry: Optional[Sequence[Any]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Annotate findings with citation/evidence support status.

    Args:
        findings: Structured findings with ``claim`` and ``source_ids``.
        registry: The ranked source registry (with stable IDs).
        threshold: Lexical coverage required for full support.
        bind: When True, add an existing retrieved source whose excerpt does
            cover an otherwise unsupported claim.
        binding_registry: Optional subset of sources the Editor actually saw.
            When supplied, only these sources may be bound, so a citation is
            never attached to evidence that was hidden from generation.

    Returns:
        ``(annotated_findings, summary)``
    """
    by_id = _registry_by_id(registry)
    bind_by_id = _registry_by_id(
        binding_registry if binding_registry is not None else registry
    )
    annotated: List[Dict[str, Any]] = []
    invalid_references = 0
    valid_references = 0
    bound_findings = 0
    status_counts = {
        "supported": 0,
        "partially_supported": 0,
        "insufficient_evidence": 0,
        "invalid_citation": 0,
        "no_citation": 0,
    }
    bound_ids: List[str] = []

    for finding in findings or []:
        if not isinstance(finding, dict):
            continue
        claim = _finding_text(finding)
        if not claim:
            continue
        original_ids = _unique_ids(finding.get("source_ids"))
        valid_ids = [source_id for source_id in original_ids if source_id in by_id]
        invalid_ids = [
            source_id for source_id in original_ids if source_id not in by_id
        ]
        invalid_references += len(invalid_ids)
        valid_references += len(valid_ids)

        def _best(ids: Sequence[str]) -> float:
            if not ids:
                return 0.0
            return max(
                text_coverage(_source_text(by_id[source_id]), claim)
                for source_id in ids
                if source_id in by_id
            )

        best_coverage = _best(valid_ids)
        finding_bound: List[str] = []

        if bind and claim and best_coverage < threshold:
            candidates: List[Tuple[float, float, int, str]] = []
            for source_id, record in bind_by_id.items():
                if source_id in valid_ids:
                    continue
                evidence = _source_text(record)
                if len(matching_tokens(evidence)) < MIN_EVIDENCE_TOKENS:
                    continue
                coverage = text_coverage(evidence, claim)
                if coverage >= threshold:
                    source_number = "".join(
                        char for char in source_id if char.isdigit()
                    )
                    candidates.append(
                        (
                            coverage,
                            _number(record.get("rank_score")),
                            int(source_number) if source_number else 0,
                            source_id,
                        )
                    )
            if candidates:
                # Highest coverage first, then deterministic ranking/ID order.
                candidates.sort(key=lambda item: (-item[0], -item[1], item[2], item[3]))
                chosen = candidates[0][3]
                valid_ids.append(chosen)
                finding_bound.append(chosen)
                bound_ids.append(chosen)
                bound_findings += 1
                best_coverage = max(best_coverage, candidates[0][0])

        if not original_ids:
            status = "no_citation"
            note = "No source was cited; this claim is not presented as source-backed."
        elif not valid_ids:
            status = "invalid_citation"
            note = (
                "Every cited source ID was missing from the retrieved registry; "
                "the invalid identifiers were removed."
            )
        elif best_coverage >= threshold:
            status = "supported"
            note = "The cited source excerpt covers the claim at the support threshold."
        elif best_coverage >= PARTIAL_SUPPORT_THRESHOLD:
            status = "partially_supported"
            note = (
                "The cited source excerpt only partially covers the claim; treat "
                "the statement as an interpretation."
            )
        else:
            status = "insufficient_evidence"
            note = (
                "No retrieved excerpt fully supports this claim; treat it as an "
                "interpretation or qualification, not a directly verified fact."
            )
        status_counts[status] += 1

        record = dict(finding)
        record["claim"] = claim
        record["source_ids"] = valid_ids
        record["original_source_ids"] = original_ids
        record["invalid_source_ids"] = invalid_ids
        record["bound_source_ids"] = finding_bound
        record["best_evidence_coverage"] = round(best_coverage, 4)
        record["support_status"] = status
        record["support_note"] = note
        record["citation_supported"] = bool(valid_ids)
        record["content_supported"] = status == "supported"
        # Preserve the model's own explanation without letting it override the
        # deterministic evidence assessment.
        record["support"] = (
            finding.get("support")
            if isinstance(finding.get("support"), str)
            else ""
        )
        annotated.append(record)

    total = len(annotated)
    cited = total - status_counts["no_citation"]
    summary = {
        "version": CITATION_INTEGRITY_VERSION,
        "threshold": threshold,
        "partial_threshold": PARTIAL_SUPPORT_THRESHOLD,
        "claims_total": total,
        "claims_cited": cited,
        "claims_supported": status_counts["supported"],
        "claims_partially_supported": status_counts["partially_supported"],
        "claims_insufficient_evidence": status_counts["insufficient_evidence"],
        "claims_invalid_citation": status_counts["invalid_citation"],
        "claims_without_citation": status_counts["no_citation"],
        "format_valid_references": valid_references,
        "invalid_references": invalid_references,
        "citation_precision": (
            round(valid_references / (valid_references + invalid_references), 4)
            if (valid_references + invalid_references)
            else None
        ),
        "citation_coverage": round(cited / total, 4) if total else None,
        "evidence_support_rate": (
            round(status_counts["supported"] / cited, 4) if cited else None
        ),
        "unsupported_claim_rate": (
            round(
                (
                    status_counts["insufficient_evidence"]
                    + status_counts["invalid_citation"]
                )
                / total,
                4,
            )
            if total
            else None
        ),
        "partially_supported_claim_rate": (
            round(status_counts["partially_supported"] / total, 4) if total else None
        ),
        "bindings_added": bound_findings,
        "bound_source_ids": sorted(set(bound_ids)),
        "binding_enabled": bool(bind),
    }
    return annotated, summary


def bind_and_verify_findings(
    findings: Sequence[Any],
    registry: Sequence[Any],
    *,
    threshold: float = 0.55,
    bind: bool = True,
    binding_registry: Optional[Sequence[Any]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Convenience wrapper used by the Editor before report construction."""
    return verify_findings(
        findings,
        registry,
        threshold=threshold,
        bind=bind,
        binding_registry=binding_registry,
    )


def build_report_sources(
    model_sources: Sequence[Any],
    findings: Sequence[Any],
    registry: Sequence[Any],
) -> Tuple[List[str], Dict[str, Any]]:
    """Build a consistent displayed-source list from findings and model URLs.

    Only URLs that exist in the retrieved registry are kept. Every valid cited
    source is displayed, so the report source count can never be smaller than
    the set of cited sources.
    """
    by_id = _registry_by_id(registry)
    by_canonical: Dict[str, str] = {}
    for record in registry or []:
        if not isinstance(record, dict):
            continue
        url = _source_url(record)
        canonical = record.get("canonical_url") or canonical_url(url)
        if canonical and canonical not in by_canonical:
            by_canonical[canonical] = url

    displayed: List[str] = []
    seen: set[str] = set()
    duplicates_collapsed = 0
    unknown: List[str] = []

    def _add(url: Any, *, track_duplicates: bool = True) -> bool:
        nonlocal duplicates_collapsed
        if not isinstance(url, str) or not url.strip():
            return False
        canonical = canonical_url(url.strip())
        if not canonical or canonical not in by_canonical:
            if url.strip() not in unknown:
                unknown.append(url.strip())
            return False
        if canonical in seen:
            # Only model-supplied URLs are audited as collapsed duplicates.
            # Re-adding a cited source from a finding is expected consistency,
            # not a duplicate the model emitted.
            if track_duplicates:
                duplicates_collapsed += 1
            return False
        seen.add(canonical)
        displayed.append(by_canonical[canonical])
        return True

    for url in model_sources or []:
        _add(url)

    added_from_findings = 0
    for finding in findings or []:
        if not isinstance(finding, dict):
            continue
        for source_id in _unique_ids(finding.get("source_ids")):
            record = by_id.get(source_id)
            if record is None:
                continue
            if _add(_source_url(record), track_duplicates=False):
                added_from_findings += 1

    return displayed, {
        "model_source_count": len(
            [url for url in (model_sources or []) if isinstance(url, str) and url.strip()]
        ),
        "displayed_source_count": len(displayed),
        "added_from_findings": added_from_findings,
        "duplicates_collapsed": duplicates_collapsed,
        "unknown_model_sources": unknown,
    }


def _cited_registry_records(
    findings: Sequence[Any], registry: Sequence[Any]
) -> List[Dict[str, Any]]:
    by_id = _registry_by_id(registry)
    cited: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for finding in findings or []:
        if not isinstance(finding, dict):
            continue
        for source_id in _unique_ids(finding.get("source_ids")):
            record = by_id.get(source_id)
            if record is None:
                continue
            canonical = record.get("canonical_url") or canonical_url(
                _source_url(record)
            )
            if canonical and canonical not in seen:
                seen.add(canonical)
                cited.append(record)
    return cited


def evaluate_citation_integrity(
    report: Any,
    source_registry: Sequence[Any],
    *,
    threshold: float = 0.55,
) -> Dict[str, Any]:
    """Compute report-level citation and evidence diagnostics.

    This is additive to the answer-quality score. Historical component
    definitions and weights are unchanged.
    """
    findings = _extract_findings(report)
    annotated, summary = verify_findings(
        findings, source_registry, threshold=threshold, bind=False
    )
    displayed = _extract_sources(report)
    cited_records = _cited_registry_records(annotated, source_registry)
    cited_canonicals = {
        record.get("canonical_url") or canonical_url(_source_url(record))
        for record in cited_records
    }
    displayed_canonicals = {
        canonical_url(url) for url in displayed if canonical_url(url)
    }
    missing_from_display = sorted(cited_canonicals - displayed_canonicals)

    fetched = sum(
        1 for record in cited_records if str(record.get("fetch_status")) == "ok"
    )
    snippet_only = sum(
        1
        for record in cited_records
        if str(record.get("retrieval_method")) == "search_snippet"
        and str(record.get("fetch_status")) != "ok"
    )
    source_types = sorted(
        {
            str(record.get("source_type"))
            for record in cited_records
            if record.get("source_type")
        }
    )
    queries = sorted(
        {
            str(query)
            for record in cited_records
            for query in (record.get("queries") or [])
            if query
        }
    )
    registry_count = len(
        [record for record in source_registry or [] if isinstance(record, dict)]
    )
    return {
        "version": CITATION_INTEGRITY_VERSION,
        "method": (
            "Citation/source diagnostics over structured findings. Content support "
            "is lexical coverage of a claim by the cited source title/excerpt, not "
            "entailment or factual correctness."
        ),
        **summary,
        "annotated_findings": annotated,
        "displayed_source_count": len(displayed_canonicals),
        "cited_source_count": len(cited_records),
        "registry_source_count": registry_count,
        "missing_cited_from_display": missing_from_display,
        "source_count_consistent": len(missing_from_display) == 0,
        "url_variants_collapsed": max(0, len(displayed) - len(displayed_canonicals)),
        "fetched_cited_sources": fetched,
        "snippet_only_cited_sources": snippet_only,
        "fetched_source_share": (
            round(fetched / len(cited_records), 4) if cited_records else None
        ),
        "snippet_only_share": (
            round(snippet_only / len(cited_records), 4) if cited_records else None
        ),
        "cited_source_types": source_types,
        "source_type_diversity": len(source_types),
        "queries_covered": queries,
        "query_coverage_count": len(queries),
    }


def add_evidence_limitations(
    report_text: str,
    integrity: Dict[str, Any],
    *,
    enabled: bool = True,
) -> Tuple[str, bool]:
    """Append a truthful limitations note when evidence support is incomplete.

    The note is derived only from measured diagnostics. It never invents a
    source or a fact, and it is skipped when the report already has a
    ``## Limitations`` heading.
    """
    if not enabled or not isinstance(report_text, str) or not report_text.strip():
        return report_text, False
    if _LIMITATIONS_HEADING.search(report_text):
        return report_text, False

    total = int(integrity.get("claims_total") or 0)
    insufficient = int(integrity.get("claims_insufficient_evidence") or 0)
    invalid_claims = int(integrity.get("claims_invalid_citation") or 0)
    snippet_share = integrity.get("snippet_only_share")
    invalid_refs = int(integrity.get("invalid_references") or 0)
    # Invalid-citation claims are reported by the reference bullet below, so
    # they are only folded into the unsupported-findings count when there is no
    # separate invalid-reference count to report.
    unsupported = insufficient + (invalid_claims if not invalid_refs else 0)

    bullets: List[str] = []
    if total and unsupported:
        bullets.append(
            f"{unsupported} of {total} key findings could not be fully matched to "
            "the excerpts of their cited sources; they should be read as "
            "interpretations rather than directly verified facts."
        )
    if invalid_refs:
        bullets.append(
            f"{invalid_refs} cited source reference(s) did not exist in the "
            "retrieved source registry and were excluded."
        )
    if isinstance(snippet_share, (int, float)) and snippet_share >= 0.5:
        bullets.append(
            f"{round(float(snippet_share) * 100)}% of cited sources were "
            "search-result snippets only; their full page text was not available, "
            "so claims based on them are limited to the snippet content."
        )
    if not bullets:
        return report_text, False

    section = "## Limitations\n" + "\n".join(f"- {bullet}" for bullet in bullets)
    references = re.search(r"^##\s+References\s*$", report_text, re.IGNORECASE | re.MULTILINE)
    if references:
        start = references.start()
        updated = report_text[:start].rstrip() + "\n\n" + section + "\n\n" + report_text[start:]
    else:
        updated = report_text.rstrip() + "\n\n" + section
    return updated, True
