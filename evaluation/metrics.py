"""Pure, reproducible evaluation metrics for the AI Research Agent.

Design rules
------------
1. Every score has an explicit definition and method string.
2. Citation alignment is never reported as factual correctness.
3. Claims that the benchmark marks ``partially_supported`` or
   ``needs_more_evidence`` are excluded from the factual-support score and
   reported as unresolved. They are never promoted to ``verified``.
4. Unmeasured components stay ``None`` with a status; they are never
   imputed or replaced with a default.
5. This module depends only on the standard library and the project's own
   dependency-free helpers, so it can be tested and run in environments without
   the application's third-party packages.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from tools.research_evidence import tokenize
from tools.report_parsing import count_words

__all__ = [
    "CANONICAL_SECTIONS",
    "DEFAULT_SUPPORT_THRESHOLD",
    "build_component",
    "claim_support",
    "citation_quality",
    "completeness",
    "evidence_coverage",
    "evidence_factual_support",
    "matching_tokens",
    "overall_score",
    "relevance",
    "section_presence",
    "text_coverage",
]

CANONICAL_SECTIONS: Tuple[str, ...] = (
    "Introduction",
    "Key Points",
    "Applications",
    "Challenges",
    "Conclusion",
    "References",
)

DEFAULT_SUPPORT_THRESHOLD = 0.55
# A claim whose content coverage falls below this is only partially evidenced.
PARTIAL_SUPPORT_THRESHOLD = 0.30
# Excluding short excerpts avoids calling a near-empty page "contradicting".
MIN_EVIDENCE_TOKENS = 5

# Weights are re-normalised over measured components only.
COMPONENT_WEIGHTS: Dict[str, float] = {
    "relevance": 0.20,
    "evidence_coverage": 0.20,
    "citation_quality": 0.20,
    "completeness": 0.15,
    "factual_support": 0.25,
}

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")


def _clean(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return _WS_RE.sub(" ", _TAG_RE.sub(" ", value)).strip()


def _clean_lines(value: Any) -> str:
    """Strip HTML tags and horizontal whitespace but preserve newlines."""
    if not isinstance(value, str):
        return ""
    text = _TAG_RE.sub(" ", value).replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(_WS_RE.sub(" ", line).strip() for line in text.split("\n"))


def _stem(token: str) -> str:
    """Very small, deterministic suffix normaliser for lexical matching.

    This is not a linguistic stemmer. It only prevents trivial mismatches
    such as ``indexing``/``index`` or ``chunks``/``chunk``. It never changes
    the meaning-based limitations of lexical matching.
    """
    token = token.lower()
    for suffix in ("ing", "ed"):
        if len(token) > len(suffix) + 3 and token.endswith(suffix):
            return token[: -len(suffix)]
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def matching_tokens(value: Any) -> List[str]:
    """Return unique, order-preserving stemmed content tokens."""
    seen: set[str] = set()
    result: List[str] = []
    for token in tokenize(_clean(value)):
        stemmed = _stem(token)
        if stemmed and stemmed not in seen:
            seen.add(stemmed)
            result.append(stemmed)
    return result


def text_coverage(text: Any, reference: Any) -> float:
    """Fraction of reference tokens present in ``text`` (0.0–1.0)."""
    reference_tokens = set(matching_tokens(reference))
    if not reference_tokens:
        return 0.0
    text_tokens = set(matching_tokens(text))
    return round(len(reference_tokens & text_tokens) / len(reference_tokens), 4)


def section_presence(
    report_text: Any, sections: Sequence[str] = CANONICAL_SECTIONS
) -> Dict[str, bool]:
    """Return whether each required heading appears in the report.

    Headings are matched case-insensitively on lines that start with ``#``,
    so body prose mentioning a section name does not count as the section.
    """
    headings = []
    for line in _clean_lines(report_text).splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            heading = stripped.lstrip("#").strip()
            if heading:
                headings.append(heading.casefold())
    normalised_headings = headings
    presence: Dict[str, bool] = {}
    for section in sections:
        target = _clean(section).casefold()
        presence[section] = any(target in heading for heading in normalised_headings)
    return presence


def build_component(
    score: Optional[float],
    method: str,
    *,
    status: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a metric component with an explicit measured/unmeasured status."""
    if score is None:
        resolved_status = status or "not_measurable"
    else:
        score = round(max(0.0, min(1.0, float(score))), 4)
        resolved_status = status or "measured"
    return {
        "score": score,
        "status": resolved_status,
        "method": method,
        "details": details or {},
    }


def relevance(
    report_text: str,
    topic: str,
    target_aspects: Sequence[str] = (),
) -> Dict[str, Any]:
    """Lexical coverage of the question's target aspects and topic.

    Method: 0.7 × mean aspect-token coverage + 0.3 × topic-token coverage.
    This measures on-topic vocabulary, not correctness or depth.
    """
    report_text = _clean(report_text)
    aspect_scores = [text_coverage(report_text, aspect) for aspect in target_aspects]
    aspect_score = sum(aspect_scores) / len(aspect_scores) if aspect_scores else None
    topic_score = text_coverage(report_text, topic) if topic else 0.0

    if aspect_score is None:
        score = topic_score
    else:
        score = (aspect_score * 0.7) + (topic_score * 0.3)

    return build_component(
        score,
        "Lexical coverage: 0.7 mean(target-aspect token coverage) + "
        "0.3 topic token coverage. Not a semantic or factual score.",
        details={
            "aspect_coverage": {
                aspect: value for aspect, value in zip(target_aspects, aspect_scores)
            },
            "topic_coverage": topic_score,
        },
    )


def completeness(
    report_text: str,
    sections: Sequence[str] = CANONICAL_SECTIONS,
    minimum_word_count: int = 250,
) -> Dict[str, Any]:
    """Structural rubric compliance: required headings plus word-count floor.

    Method: 0.75 × share of required headings present + 0.25 ×
    min(words / minimum_word_count, 1). Headings must appear as markdown
    headings, not merely as words in prose.
    """
    # Preserve line breaks for heading detection, then flatten only for the
    # word count. Collapsing first would turn the whole document into one
    # "heading" line and make every section appear present.
    line_preserving = _clean_lines(report_text)
    presence = section_presence(line_preserving, sections)
    denominator = len(sections) or 1
    section_ratio = sum(1 for present in presence.values() if present) / denominator
    word_count = count_words(_clean(line_preserving))
    floor = max(1, int(minimum_word_count or 1))
    word_ratio = min(1.0, word_count / floor)
    score = (section_ratio * 0.75) + (word_ratio * 0.25)
    return build_component(
        score,
        "Structural compliance: 0.75 × required-heading coverage + "
        "0.25 × min(word_count / rubric minimum, 1).",
        details={
            "sections_present": presence,
            "section_ratio": round(section_ratio, 4),
            "word_count": word_count,
            "minimum_word_count": floor,
            "word_ratio": round(word_ratio, 4),
        },
    )


def citation_quality(
    supported_sources: Sequence[str],
    unsupported_citations: Sequence[str],
    claim_citation_coverage: Optional[float] = None,
) -> Dict[str, Any]:
    """Citation precision against the retrieved-source registry.

    Method: ``cited = supported + unsupported``; precision =
    supported / cited. When claim-level coverage is available, the score is
    0.6 × precision + 0.4 × claim coverage.

    This is citation alignment only. A retrieved URL can still be cited for
    a statement it does not support, so this is never factual correctness.
    """
    supported = [s for s in (supported_sources or []) if isinstance(s, str) and s.strip()]
    unsupported = [s for s in (unsupported_citations or []) if isinstance(s, str) and s.strip()]
    unique_supported = list(dict.fromkeys(supported))
    unique_unsupported = list(dict.fromkeys(unsupported))
    cited_total = len(unique_supported) + len(unique_unsupported)
    if cited_total == 0:
        return build_component(
            0.0,
            "Citation precision against retrieved URLs. No citations were emitted.",
            status="no_citations",
            details={
                "supported_citations": 0,
                "unsupported_citations": 0,
                "citation_precision": 0.0,
                "claim_citation_coverage": claim_citation_coverage,
            },
        )

    precision = len(unique_supported) / cited_total
    if claim_citation_coverage is None:
        score = precision
        method = (
            "Citation precision against the retrieved-source registry "
            "(supported / all emitted citations). Alignment only; not factual support."
        )
    else:
        score = (precision * 0.6) + (float(claim_citation_coverage) * 0.4)
        method = (
            "0.6 × citation precision + 0.4 × claim-citation coverage. "
            "Alignment only; neither component proves a claim is true."
        )
    return build_component(
        score,
        method,
        details={
            "supported_citations": len(unique_supported),
            "unsupported_citations": len(unique_unsupported),
            "citation_precision": round(precision, 4),
            "claim_citation_coverage": claim_citation_coverage,
        },
    )


def evidence_coverage(
    research_metrics: Optional[Dict[str, Any]] = None,
    grounding: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Share of retrieved material that was usable and cited.

    Method: mean of available components:
      - retrieval usability = usable_evidence / search_results;
      - citation grounding = grounded_sources / retrieved_sources.

    This is a retrieval/alignment measure. It does not measure whether the
    retrieved pages are authoritative or correct.
    """
    components: Dict[str, float] = {}
    details: Dict[str, Any] = {}

    metrics = research_metrics or {}
    search_results = metrics.get("search_results")
    usable = metrics.get("usable_evidence")
    if isinstance(search_results, (int, float)) and search_results > 0 and isinstance(usable, (int, float)):
        usability = min(1.0, max(0.0, float(usable) / float(search_results)))
        components["retrieval_usability"] = usability
        details["retrieval_usability"] = {
            "usable_evidence": int(usable),
            "search_results": int(search_results),
            "ratio": round(usability, 4),
        }
    elif isinstance(search_results, (int, float)) and isinstance(usable, (int, float)):
        components["retrieval_usability"] = 0.0
        details["retrieval_usability"] = {"usable_evidence": int(usable), "search_results": int(search_results), "ratio": 0.0}

    grounding_data = grounding or {}
    retrieved = grounding_data.get("retrieved_sources_count")
    grounded = grounding_data.get("grounded_sources_count")
    if isinstance(retrieved, (int, float)) and retrieved > 0 and isinstance(grounded, (int, float)):
        grounding_ratio = min(1.0, max(0.0, float(grounded) / float(retrieved)))
        components["citation_grounding"] = grounding_ratio
        details["citation_grounding"] = {
            "grounded_sources": int(grounded),
            "retrieved_sources": int(retrieved),
            "ratio": round(grounding_ratio, 4),
        }

    if not components:
        return build_component(
            None,
            "No retrieval or grounding counts were supplied.",
            status="not_measurable",
        )

    score = sum(components.values()) / len(components)
    return build_component(
        score,
        "Mean of available retrieval-usability and citation-grounding ratios. "
        "Retrieval/alignment only; not source authority or factual correctness.",
        details=details,
    )


def claim_support(
    report_text: str,
    claims: Sequence[Dict[str, Any]],
    threshold: float = DEFAULT_SUPPORT_THRESHOLD,
) -> Dict[str, Any]:
    """Lexical support of benchmark claims by the report.

    Only claims whose ``status`` is exactly ``verified`` contribute to the
    score. ``partially_supported`` and ``needs_more_evidence`` claims are
    returned as unresolved and excluded from numerator and denominator.
    They are never silently counted as supported.

    Method: for each verified claim, compute the fraction of the claim's
    content tokens present in the report. A claim counts as supported when
    coverage is at least ``threshold``. This is a lexical proxy, not factual
    verification or entailment.
    """
    report_text = _clean(report_text)
    claim_results: List[Dict[str, Any]] = []
    unresolved: List[Dict[str, Any]] = []
    verified_total = 0
    supported = 0

    for claim in claims or []:
        if not isinstance(claim, dict):
            continue
        claim_id = str(claim.get("claim_id") or "")
        text = claim.get("effective_claim_text") or claim.get("claim_text") or ""
        status = str(claim.get("status") or "unknown")
        coverage = text_coverage(report_text, text)
        record = {
            "claim_id": claim_id,
            "status": status,
            "lexical_coverage": coverage,
            "supported_proxy": coverage >= threshold,
        }
        if status == "verified":
            verified_total += 1
            if record["supported_proxy"]:
                supported += 1
            claim_results.append(record)
        else:
            unresolved.append(record)
            claim_results.append(record)

    if verified_total == 0:
        return build_component(
            None,
            "No verified benchmark claims available; unresolved claims are not scored.",
            status="not_measurable",
            details={
                "verified_claims": 0,
                "supported_verified_claims": 0,
                "support_threshold": threshold,
                "claim_results": claim_results,
                "unresolved_claims": unresolved,
            },
        )

    score = supported / verified_total
    return build_component(
        score,
        "Lexical claim-support proxy: share of verified benchmark claims whose "
        "content-token coverage is at least the threshold. Not entailment or "
        "factual verification. Unresolved claims are excluded, not promoted.",
        details={
            "verified_claims": verified_total,
            "supported_verified_claims": supported,
            "support_threshold": threshold,
            "claim_results": claim_results,
            "unresolved_claims": unresolved,
        },
    )


def _finding_claim_text(finding: Dict[str, Any]) -> str:
    for key in ("claim", "text", "finding", "statement"):
        value = finding.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _report_claims(report_text: str, max_claims: int = 40) -> List[str]:
    """Extract bounded, checkable sentences from prose when no findings exist.

    This is a deterministic heuristic: headings, list markers, link syntax and
    short fragments are discarded. It is not claim extraction by a language
    model and does not attempt to resolve anaphora.
    """
    claims: List[str] = []
    for sentence in _SENTENCE_SPLIT_RE.split(_clean_lines(report_text)):
        cleaned = sentence.strip().lstrip("#-*|>").strip()
        cleaned = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", cleaned)
        cleaned = _WS_RE.sub(" ", cleaned).strip()
        if not cleaned or cleaned.lower().startswith(("references", "sources")):
            continue
        if len(matching_tokens(cleaned)) < 3:
            continue
        claims.append(cleaned)
        if len(claims) >= max_claims:
            break
    return claims


def evidence_factual_support(
    report_text: str,
    key_findings: Sequence[Dict[str, Any]],
    source_registry: Sequence[Dict[str, Any]],
    threshold: float = DEFAULT_SUPPORT_THRESHOLD,
) -> Dict[str, Any]:
    """Check report claims against retrieved source excerpts.

    Method: for every structured finding (or, when no findings exist, bounded
    sentences from the report), resolve its ``source_ids`` against the source
    registry and measure the fraction of the claim's content tokens present in
    the cited sources' title/excerpt text.

    Classification:

    * ``supported`` — coverage at or above ``threshold``.
    * ``partially_supported`` — coverage between ``PARTIAL_SUPPORT_THRESHOLD``
      and ``threshold``.
    * ``unsupported`` — usable evidence exists but coverage is below the
      partial threshold.
    * ``unverifiable`` — no source reference, an unknown source ID, an
      excerpt too short to check, or a claim with no content tokens.

    Unverifiable claims are reported but excluded from the score; missing
    evidence is never treated as proof that a claim is false. The score is
    ``(supported + 0.5 × partially_supported) / verifiable_claims``.

    This is a lexical overlap proxy. It cannot detect negation, contradiction,
    paraphrase with little shared vocabulary, or a source that is itself wrong.
    """
    report_text = report_text if isinstance(report_text, str) else ""
    by_id = {
        str(record.get("source_id")): record
        for record in (source_registry or [])
        if isinstance(record, dict) and record.get("source_id")
    }

    raw_claims: List[Dict[str, Any]] = []
    for finding in key_findings or []:
        if not isinstance(finding, dict):
            continue
        text = _finding_claim_text(finding)
        if text:
            raw_claims.append({"text": text, "finding": finding})
    if not raw_claims:
        for text in _report_claims(report_text):
            raw_claims.append({"text": text, "finding": {}})

    if not raw_claims:
        return build_component(
            None,
            "No checkable claims were found in the report.",
            status="not_measurable",
            details={
                "claims_total": 0,
                "claims_supported": 0,
                "claims_partially_supported": 0,
                "claims_unsupported": 0,
                "claims_unverifiable": 0,
                "verifiable_claims": 0,
                "claim_results": [],
                "unresolved_claims": [],
                "support_threshold": threshold,
                "partial_threshold": PARTIAL_SUPPORT_THRESHOLD,
                "reason": "empty_report",
            },
        )

    results: List[Dict[str, Any]] = []
    counts = {"supported": 0, "partially_supported": 0, "unsupported": 0, "unverifiable": 0}

    for entry in raw_claims:
        finding = entry["finding"]
        claim_text = entry["text"]
        raw_ids = finding.get("source_ids")
        source_ids = [
            str(sid) for sid in (raw_ids if isinstance(raw_ids, list) else [])
            if isinstance(sid, str) and sid.strip()
        ]
        source_ids = list(dict.fromkeys(source_ids))
        explicit_citation = bool(source_ids)

        valid_ids = [sid for sid in source_ids if sid in by_id]
        invalid_ids = [sid for sid in source_ids if sid not in by_id]
        evidence_parts: List[str] = []
        for sid in valid_ids:
            record = by_id[sid]
            for key in ("title", "excerpt"):
                value = record.get(key)
                if isinstance(value, str) and value.strip():
                    evidence_parts.append(value)

        if not explicit_citation and not finding:
            # Sentence fallback: the claim carries no citation, so check it
            # against the whole retrieved corpus and record the closest sources.
            for sid, record in by_id.items():
                source_text = " ".join(
                    value for value in (record.get("title"), record.get("excerpt"))
                    if isinstance(value, str) and value.strip()
                )
                if text_coverage(source_text, claim_text) >= PARTIAL_SUPPORT_THRESHOLD:
                    valid_ids.append(sid)
                    evidence_parts.append(source_text)
        evidence_text = " ".join(evidence_parts)

        coverage = text_coverage(evidence_text, claim_text) if evidence_text else 0.0
        reason = ""

        if len(matching_tokens(claim_text)) == 0:
            classification = "unverifiable"
            reason = "claim_has_no_checkable_terms"
        elif not explicit_citation and not by_id:
            classification = "unverifiable"
            reason = "no_source_reference"
        elif not valid_ids:
            classification = "unverifiable"
            reason = "invalid_source_reference" if explicit_citation else "no_source_reference"
        elif len(matching_tokens(evidence_text)) < MIN_EVIDENCE_TOKENS:
            classification = "unverifiable"
            reason = "insufficient_source_evidence"
        elif coverage >= threshold:
            classification = "supported"
        elif coverage >= PARTIAL_SUPPORT_THRESHOLD:
            classification = "partially_supported"
        else:
            classification = "unsupported"

        counts[classification] += 1
        results.append({
            "claim": claim_text,
            "source_ids": source_ids,
            "valid_source_ids": valid_ids,
            "invalid_source_ids": invalid_ids,
            "cited": explicit_citation,
            "evidence_coverage": coverage,
            "classification": classification,
            "reason": reason,
        })

    verifiable = (
        counts["supported"] + counts["partially_supported"] + counts["unsupported"]
    )
    unresolved = [
        record for record in results
        if record["classification"] in {"partially_supported", "unverifiable"}
    ]
    details = {
        "claims_total": len(results),
        "claims_supported": counts["supported"],
        "claims_partially_supported": counts["partially_supported"],
        "claims_unsupported": counts["unsupported"],
        "claims_unverifiable": counts["unverifiable"],
        "verifiable_claims": verifiable,
        "support_threshold": threshold,
        "partial_threshold": PARTIAL_SUPPORT_THRESHOLD,
        "minimum_evidence_tokens": MIN_EVIDENCE_TOKENS,
        "claim_results": results,
        "unresolved_claims": unresolved,
    }

    if verifiable == 0:
        details["reason"] = "insufficient_evidence"
        return build_component(
            None,
            "No claim had checkable retrieved-source evidence; unverifiable "
            "claims are excluded rather than scored as false.",
            status="not_measurable",
            details=details,
        )

    score = (counts["supported"] + 0.5 * counts["partially_supported"]) / verifiable
    return build_component(
        score,
        "Deterministic lexical evidence check: claim content-token coverage "
        "against the excerpts of its cited retrieved sources. Full coverage "
        "counts 1, partial counts 0.5, unsupported counts 0, and unverifiable "
        "claims are excluded. Not entailment, contradiction detection, or "
        "ground truth.",
        details=details,
    )


def overall_score(metrics: Dict[str, Dict[str, Any]]) -> Tuple[Optional[float], Dict[str, float]]:
    """Weighted score over measured components only.

    Returns ``(score, weights_used)``. If no component is measured, the
    score is ``None``. Weights are re-normalised after removing unmeasured
    components so a missing factual-support score cannot be silently
    replaced with zero.
    """
    available = {
        name: COMPONENT_WEIGHTS[name]
        for name, component in metrics.items()
        if name in COMPONENT_WEIGHTS
        and isinstance(component, dict)
        and component.get("score") is not None
    }
    total_weight = sum(available.values())
    if total_weight <= 0:
        return None, {}
    used = {name: round(weight / total_weight, 4) for name, weight in available.items()}
    score = sum(
        float(metrics[name]["score"]) * used[name] for name in used
    )
    return round(score, 4), used
