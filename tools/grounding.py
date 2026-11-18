"""Source grounding instrumentation for AI Research Agent.

Measures citation grounding by cross-referencing retrieved source URLs
against URLs cited in the final generated report.

IMPORTANT METHODOLOGICAL DISCLAIMER:
Source grounding measures citation fidelity and source alignment (i.e.
whether retrieved URLs appear in the generated report). It does NOT prove
factual correctness, claim-level truthfulness, or source accuracy.
"""

import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_URL_PATTERN = re.compile(r"https?://[^\s)\]\"'>]+", re.IGNORECASE)


def normalize_url(url: str) -> str:
    """Normalize a URL for resilient comparison.

    Strips scheme (http/https), leading 'www.', trailing slashes,
    and converts to lowercase.

    Examples:
        'https://www.example.com/path/' -> 'example.com/path'
        'http://example.com/path' -> 'example.com/path'
    """
    if not url:
        return ""
    clean = url.strip().rstrip("/")
    clean = re.sub(r"^https?://", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"^www\.", "", clean, flags=re.IGNORECASE)
    return clean.lower()


def extract_urls_from_text(text: str) -> List[str]:
    """Extract all HTTP/HTTPS URLs from a block of text.

    Returns a deduplicated list of discovered URLs preserving appearance order.
    """
    if not text:
        return []
    matches = _URL_PATTERN.findall(text)
    # Deduplicate while preserving order, trimming trailing punctuation
    seen = set()
    deduped = []
    for raw in matches:
        cleaned = raw.rstrip(".,;:!?)")
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            deduped.append(cleaned)
    return deduped


def analyze_claim_citations(
    key_findings: Optional[List[Dict[str, Any]]],
    source_registry: Optional[List[Dict[str, Any]]] = None,
    retrieved_urls: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Measure whether generated findings point at retrieved sources.

    This checks identifier/URL alignment only. A supported reference still does
    not prove that the source supports the claim's factual content.
    """
    registry_ids = {
        record.get("source_id")
        for record in (source_registry or [])
        if isinstance(record, dict)
    }
    findings = key_findings if isinstance(key_findings, list) else []
    supported: List[str] = []
    unsupported: List[str] = []
    without_citations: List[str] = []

    for finding in findings:
        if not isinstance(finding, dict):
            continue
        claim = finding.get("claim") or finding.get("text") or ""
        source_ids = [
            sid for sid in finding.get("source_ids", []) if isinstance(sid, str)
        ]
        if not source_ids:
            without_citations.append(str(claim))
            continue
        if registry_ids:
            valid = [sid for sid in source_ids if sid in registry_ids]
            invalid = [sid for sid in source_ids if sid not in registry_ids]
        else:
            valid = source_ids
            invalid = []
        if valid:
            supported.append(str(claim))
        if invalid:
            unsupported.extend(invalid)

    total = len([f for f in findings if isinstance(f, dict)])
    covered = len(supported)
    return {
        "claim_findings_count": total,
        "findings_with_supported_citations": covered,
        "findings_without_citations": without_citations,
        "unsupported_claim_source_ids": sorted(set(unsupported)),
        "claim_citation_coverage": round(covered / total, 4) if total else 0.0,
    }


def analyze_source_grounding(
    retrieved_urls: List[str],
    report_content: str,
    explicit_sources: Optional[List[str]] = None,
    claim_findings: Optional[List[Dict[str, Any]]] = None,
    source_registry: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Analyze how many retrieved sources are referenced in the report.

    Args:
        retrieved_urls: List of source URLs collected during web search.
        report_content: Full text of the generated research report.
        explicit_sources: Optional list of sources explicitly cited by
            the LLM in its structured response (e.g. before post-processing).
        claim_findings: Optional structured findings with ``source_ids``.
        source_registry: Optional registry mapping source IDs to URLs.

    Returns:
        Dict with:
            - retrieved_sources_count: Total unique retrieved URLs
            - grounded_sources_count: Retrieved URLs referenced in report
            - citation_grounding_ratio: Ratio grounded / retrieved (0.0 to 1.0)
            - grounded_urls: List of referenced retrieved URLs
            - ungrounded_retrieved_urls: Retrieved URLs not referenced
            - report_urls: All URLs extracted from report text
            - phantom_urls: URLs in report that were not in retrieved sources
            - claim_citation_coverage: Share of findings with valid source IDs
            - findings_without_citations: Findings with no source IDs
            - unsupported_claim_source_ids: Finding source IDs not in registry
    """
    # Deduplicate retrieved URLs preserving order
    deduped_retrieved: List[str] = []
    seen_retrieved = set()
    for u in (retrieved_urls or []):
        stripped = u.strip() if isinstance(u, str) else ""
        if stripped and stripped not in seen_retrieved:
            seen_retrieved.add(stripped)
            deduped_retrieved.append(stripped)

    # Extract all URLs cited directly in the markdown report content
    report_urls = extract_urls_from_text(report_content or "")

    # Lookups for matching
    explicit_list = [s.strip() for s in (explicit_sources or []) if isinstance(s, str) and s.strip()]
    normalized_report_urls = {normalize_url(u) for u in report_urls if u}
    normalized_explicit = {normalize_url(u) for u in explicit_list if u}

    content_lower = (report_content or "").lower()

    grounded_urls: List[str] = []
    ungrounded_urls: List[str] = []

    for url in deduped_retrieved:
        norm = normalize_url(url)
        # Check matching conditions:
        # 1. Verbatim presence in report text
        # 2. Normalized URL matches any extracted report URL
        # 3. URL is in the explicit sources list returned by the LLM
        # 4. Normalized URL is in the normalized explicit sources
        # 5. Normalized domain + path is mentioned in report body
        if (
            url in (report_content or "")
            or norm in normalized_report_urls
            or url in explicit_list
            or norm in normalized_explicit
            or (norm and norm in content_lower)
        ):
            grounded_urls.append(url)
        else:
            ungrounded_urls.append(url)

    # Phantom URLs: URLs in the report that were never retrieved by the search agent
    normalized_retrieved = {normalize_url(u) for u in deduped_retrieved}
    phantom_urls = [
        u for u in report_urls
        if normalize_url(u) not in normalized_retrieved
    ]

    retrieved_count = len(deduped_retrieved)
    grounded_count = len(grounded_urls)
    ratio = round(grounded_count / retrieved_count, 4) if retrieved_count > 0 else 0.0

    logger.debug(
        f"Source grounding analysis: {grounded_count}/{retrieved_count} grounded "
        f"(ratio={ratio:.2f}, {len(phantom_urls)} phantom URLs)"
    )

    return {
        "retrieved_sources_count": retrieved_count,
        "grounded_sources_count": grounded_count,
        "citation_grounding_ratio": ratio,
        "grounded_urls": grounded_urls,
        "ungrounded_retrieved_urls": ungrounded_urls,
        "report_urls": report_urls,
        "phantom_urls": phantom_urls,
        **analyze_claim_citations(
            claim_findings,
            source_registry=source_registry,
            retrieved_urls=deduped_retrieved,
        ),
    }

