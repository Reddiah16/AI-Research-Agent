"""Deterministic research-evidence processing.

The Researcher uses this module instead of concatenating search-result titles.
It normalises and deduplicates search results, keeps snippets, optionally
enriches them with bounded page text, scores relevance, and records provenance
for every retained item.

All functions are standard-library only and have no network side effects except
through an injected ``fetcher`` callable. This keeps the core logic unit-testable
without DuckDuckGo, Ollama, or network access.

Methodological note: relevance scoring is lexical overlap, not semantic truth.
Retaining an item means "this text is topically related and usable", not "this
text is factually correct".
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

Fetcher = Callable[[str], Dict[str, Any]]

_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "how",
    "in", "into", "is", "it", "of", "on", "or", "that", "the", "their",
    "this", "to", "vs", "versus", "what", "when", "where", "which", "who",
    "why", "with", "within", "using", "use", "used",
}

_TRACKING_PARAM_PREFIXES = ("utm_", "ref_", "mc_")
_TRACKING_PARAMS = {"fbclid", "gclid", "igshid", "ref", "source"}

_TAG_PATTERN = re.compile(r"<[^>]+>")
_WS_PATTERN = re.compile(r"\s+")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


def clean_text(value: Any) -> str:
    """Collapse whitespace and strip simple HTML tags from a text value."""
    if not isinstance(value, str):
        return ""
    text = _TAG_PATTERN.sub(" ", value)
    text = text.replace("\xa0", " ")
    return _WS_PATTERN.sub(" ", text).strip()


def tokenize(value: str) -> List[str]:
    """Lowercase alphanumeric tokens with stopwords removed."""
    tokens = re.findall(r"[a-zA-Z0-9][a-zA-Z0-9+#.\-]*", (value or "").lower())
    return [token for token in tokens if token not in _STOPWORDS and len(token) > 1]


def canonical_url(url: str) -> str:
    """Return a stable comparison key for a URL.

    Strips fragments, trailing slashes, default ports, and common tracking
    parameters. Returns an empty string for non-HTTP(S) or malformed URLs.
    """
    if not isinstance(url, str) or not url.strip():
        return ""
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return ""
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return ""
    host = parsed.hostname.lower()
    if parsed.port and not (
        (parsed.scheme.lower() == "http" and parsed.port == 80)
        or (parsed.scheme.lower() == "https" and parsed.port == 443)
    ):
        host = f"{host}:{parsed.port}"
    path = re.sub(r"/+", "/", parsed.path or "/").rstrip("/") or "/"
    query_pairs = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        lowered = key.lower()
        if lowered in _TRACKING_PARAMS or lowered.startswith(_TRACKING_PARAM_PREFIXES):
            continue
        query_pairs.append((key, value))
    query = urlencode(sorted(query_pairs))
    return urlunparse(("https", host, path, "", query, ""))


def source_fingerprint(url: str) -> str:
    """Short deterministic identifier derived from the canonical URL."""
    key = canonical_url(url) or (url or "").strip().lower()
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:8] if key else ""


def normalize_search_result(result: Any, index: int = 0) -> Optional[Dict[str, str]]:
    """Convert a raw search result into a safe record, or None if unusable."""
    if not isinstance(result, dict):
        return None
    url = result.get("url") or result.get("href") or ""
    if not isinstance(url, str):
        return None
    canonical = canonical_url(url)
    if not canonical:
        return None
    title = clean_text(result.get("title", ""))
    snippet = clean_text(result.get("snippet", "") or result.get("body", ""))
    if not title:
        host = urlparse(url).hostname or f"result-{index + 1}"
        title = host
    return {
        "title": title,
        "url": url.strip(),
        "canonical_url": canonical,
        "snippet": snippet,
        "original_rank": index,
    }


def normalize_results(raw_results: Iterable[Any]) -> List[Dict[str, str]]:
    """Normalize, validate, and deduplicate search results in rank order."""
    normalized: List[Dict[str, str]] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_results or []):
        record = normalize_search_result(raw, index)
        if record is None or record["canonical_url"] in seen:
            continue
        seen.add(record["canonical_url"])
        normalized.append(record)
    return normalized


def relevance_score(text: str, query: str, topic: str = "") -> float:
    """Lexical overlap between evidence text and the query/topic.

    Returns a value in [0, 1]. It is a filter signal, not a truth score.
    """
    query_tokens = set(tokenize(query))
    if not query_tokens:
        return 0.0
    text_tokens = set(tokenize(text))
    if not text_tokens:
        return 0.0
    query_overlap = len(query_tokens & text_tokens) / len(query_tokens)
    topic_tokens = set(tokenize(topic))
    topic_overlap = (
        len(topic_tokens & text_tokens) / len(topic_tokens) if topic_tokens else 0.0
    )
    return round(min(1.0, (query_overlap * 0.8) + (topic_overlap * 0.2)), 4)


def extract_relevant_excerpt(
    text: str, query: str, max_chars: int = 700
) -> str:
    """Select the most query-relevant sentences, preserving document order."""
    cleaned = clean_text(text)
    if not cleaned:
        return ""
    if len(cleaned) <= max_chars:
        return cleaned

    sentences = [s.strip() for s in _SENTENCE_SPLIT.split(cleaned) if s.strip()]
    if not sentences:
        return cleaned[:max_chars].rstrip()

    query_tokens = set(tokenize(query))
    scored = []
    for position, sentence in enumerate(sentences):
        sentence_tokens = set(tokenize(sentence))
        overlap = len(query_tokens & sentence_tokens)
        scored.append((overlap, position, sentence))

    selected = sorted(scored, key=lambda item: (-item[0], item[1]))[:4]
    selected.sort(key=lambda item: item[1])
    excerpt = " ".join(sentence for _, _, sentence in selected)
    if len(excerpt) > max_chars:
        excerpt = excerpt[:max_chars].rsplit(" ", 1)[0].rstrip()
    return excerpt or cleaned[:max_chars].rstrip()


def build_query_evidence(
    query: str,
    raw_results: Sequence[Any],
    topic: str = "",
    fetcher: Optional[Fetcher] = None,
    max_items: int = 3,
    fetch_limit: int = 2,
    max_excerpt_chars: int = 700,
    min_relevance: float = 0.05,
) -> Dict[str, Any]:
    """Build a provenance-rich evidence bundle for one query.

    Args:
        query: The search query.
        raw_results: Raw search-result dictionaries (may be malformed).
        topic: Overall research topic, used as a small relevance tie-breaker.
        fetcher: Optional callable ``url -> fetch_page result``. When omitted,
            only search snippets are used.
        max_items: Maximum retained evidence items.
        fetch_limit: Maximum pages fetched per query (budget).
        max_excerpt_chars: Character cap for each excerpt.
        min_relevance: Minimum lexical relevance for retention.

    Returns:
        Dict with ``items`` (retained evidence), ``filtered_count``,
        ``fetched_count``, ``fetch_failed_count`` and ``usable_count``.
    """
    normalized = normalize_results(raw_results)
    items: List[Dict[str, Any]] = []
    filtered_count = 0
    fetched_count = 0
    fetch_failed_count = 0
    fetch_budget = max(0, int(fetch_limit))

    for result in normalized:
        if len(items) >= max_items:
            break

        snippet_excerpt = extract_relevant_excerpt(
            result["snippet"], query, max_excerpt_chars
        )
        fetch_status = "not_attempted"
        page_excerpt = ""
        page_title = ""

        should_fetch = fetcher is not None and fetch_budget > 0
        if should_fetch:
            fetch_budget -= 1
            fetched_count += 1
            try:
                page = fetcher(result["url"])
            except Exception as exc:  # defensive: fetchers must not break research
                page = {"ok": False, "error": f"fetcher_exception:{type(exc).__name__}"}
            if isinstance(page, dict) and page.get("ok") and page.get("text"):
                fetch_status = "ok"
                page_excerpt = extract_relevant_excerpt(
                    str(page.get("text", "")), query, max_excerpt_chars
                )
                page_title = clean_text(page.get("title", ""))
            else:
                fetch_status = "failed"
                fetch_failed_count += 1
                error = page.get("error") if isinstance(page, dict) else "invalid_fetch_result"
                fetch_status = f"failed:{error or 'unknown'}"

        excerpt = page_excerpt or snippet_excerpt
        retrieval_method = "fetched_page" if page_excerpt else "search_snippet"
        if not excerpt:
            filtered_count += 1
            continue

        title = page_title or result["title"]
        combined = f"{title} {excerpt}"
        score = relevance_score(combined, query, topic)
        if score < min_relevance:
            filtered_count += 1
            continue

        items.append(
            {
                "title": title,
                "url": result["url"],
                "canonical_url": result["canonical_url"],
                "snippet": result["snippet"],
                "excerpt": excerpt,
                "retrieval_method": retrieval_method,
                "fetch_status": fetch_status,
                "relevance": score,
                "content_chars": len(excerpt),
                "query": query,
                "source_fingerprint": source_fingerprint(result["url"]),
            }
        )

    return {
        "items": items,
        "filtered_count": filtered_count,
        "fetched_count": fetched_count,
        "fetch_failed_count": fetch_failed_count,
        "usable_count": len(items),
    }


def assemble_summary(items: Sequence[Dict[str, Any]], max_items: int = 3) -> str:
    """Create a compact, source-attributed summary for the Editor context."""
    if not items:
        return "No usable evidence was retrieved for this query."
    lines = []
    for item in list(items)[:max_items]:
        provenance = item.get("retrieval_method", "search_snippet")
        lines.append(
            f"- {item.get('title', 'Untitled')} [{provenance}]\n"
            f"  {item.get('excerpt', '')}\n"
            f"  URL: {item.get('url', '')}"
        )
    return "\n".join(lines)


def build_source_registry(
    research_summaries: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Build a global, deduplicated source registry with stable IDs."""
    registry: List[Dict[str, Any]] = []
    by_url: Dict[str, Dict[str, Any]] = {}
    for summary in research_summaries or []:
        query = summary.get("query", "")
        for item in summary.get("evidence_items", []) or []:
            canonical = item.get("canonical_url") or canonical_url(item.get("url", ""))
            if not canonical:
                continue
            if canonical in by_url:
                existing = by_url[canonical]
                if query and query not in existing["queries"]:
                    existing["queries"].append(query)
                continue
            record = {
                "source_id": f"SRC-{len(registry) + 1:03d}",
                "url": item.get("url", ""),
                "canonical_url": canonical,
                "title": item.get("title", ""),
                "queries": [query] if query else [],
                "retrieval_method": item.get("retrieval_method", "search_snippet"),
                "fetch_status": item.get("fetch_status", "not_attempted"),
                "excerpt": item.get("excerpt", ""),
                "relevance": item.get("relevance", 0.0),
            }
            registry.append(record)
            by_url[canonical] = record
    return registry


def format_evidence_context(
    research_summaries: Sequence[Dict[str, Any]],
    registry: Sequence[Dict[str, Any]],
) -> str:
    """Format evidence for the Editor prompt, including source IDs and URLs."""
    by_url = {record["canonical_url"]: record for record in registry}
    blocks: List[str] = []
    for summary in research_summaries or []:
        query = summary.get("query", "")
        lines = [f"### Query: {query}"]
        items = summary.get("evidence_items", []) or []
        if not items:
            lines.append("No usable evidence was retrieved for this query.")
        for item in items:
            canonical = item.get("canonical_url") or canonical_url(item.get("url", ""))
            record = by_url.get(canonical)
            source_id = record["source_id"] if record else "UNREGISTERED"
            lines.append(
                f"[{source_id}] {item.get('title', 'Untitled')}\n"
                f"URL: {item.get('url', '')}\n"
                f"Retrieved as: {item.get('retrieval_method', 'search_snippet')} "
                f"({item.get('fetch_status', 'not_attempted')})\n"
                f"Evidence excerpt: {item.get('excerpt', '')}"
            )
        blocks.append("\n".join(lines))
    return "\n\n---\n\n".join(blocks) if blocks else "No usable evidence was retrieved."


def collect_source_urls(research_summaries: Sequence[Dict[str, Any]]) -> List[str]:
    """Collect unique retrieved URLs, preserving first-seen order."""
    urls: List[str] = []
    seen: set[str] = set()
    for summary in research_summaries or []:
        for url in summary.get("source_urls", []) or []:
            if isinstance(url, str) and url and url not in seen:
                seen.add(url)
                urls.append(url)
    return urls
