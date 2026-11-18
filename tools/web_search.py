"""DuckDuckGo web search tool for the Research Agent.

Uses the ``ddgs`` package lazily so this module can be imported (and its
retry/formatting helpers tested) without the optional dependency installed.
No API key required.
"""

import logging
import time
from typing import Any, Dict, List, Optional

from config.config import ResearchConfig

logger = logging.getLogger(__name__)


def _coerce_results(raw_results: Any) -> List[Dict[str, str]]:
    """Validate raw provider records and drop malformed entries."""
    results: List[Dict[str, str]] = []
    for item in raw_results or []:
        if not isinstance(item, dict):
            continue
        title = item.get("title", "")
        url = item.get("href") or item.get("url") or ""
        snippet = item.get("body") or item.get("snippet") or ""
        if not isinstance(url, str) or not url.strip():
            continue
        results.append({
            "title": title if isinstance(title, str) else str(title),
            "url": url.strip(),
            "snippet": snippet if isinstance(snippet, str) else str(snippet),
        })
    return results


def _search_with_retries(
    provider_call,
    query: str,
    *,
    max_retries: int,
    backoff: float,
    diagnostics: Optional[Dict[str, Any]],
) -> List[Dict[str, str]]:
    """Run a provider call with bounded exponential backoff.

    Retries only on raised exceptions. An empty successful response is not
    retried because it can be a legitimate no-result answer.
    """
    attempts = max(1, int(max_retries) + 1)
    last_error: Optional[str] = None
    for attempt in range(1, attempts + 1):
        try:
            raw = provider_call()
            results = _coerce_results(raw)
            if diagnostics is not None:
                diagnostics.update({"attempts": attempt, "error": None})
            return results
        except Exception as exc:  # network/provider failures
            last_error = f"{type(exc).__name__}: {exc}"
            logger.warning(
                f"DuckDuckGo search attempt {attempt}/{attempts} failed for "
                f"'{query}': {last_error}"
            )
            if attempt < attempts:
                time.sleep(max(0.0, float(backoff)) * attempt)
    if diagnostics is not None:
        diagnostics.update({"attempts": attempts, "error": last_error})
    logger.error(f"DuckDuckGo search failed after {attempts} attempts for '{query}'")
    return []


def web_search(
    query: str,
    config: Optional[ResearchConfig] = None,
    max_results: Optional[int] = None,
    region: str = "wt-wt",
    time_range: Optional[str] = None,
    *,
    diagnostics: Optional[Dict[str, Any]] = None,
    max_retries: Optional[int] = None,
    retry_backoff: Optional[float] = None,
) -> List[Dict[str, str]]:
    """Search the web using DuckDuckGo.

    Args:
        query: The search query string.
        config: Optional ResearchConfig for max_results/retry defaults.
        max_results: Maximum number of results to return.
            Falls back to config.max_results, then 10.
        region: DuckDuckGo region code (default: worldwide).
        time_range: Time filter — None, "d" (day), "w" (week),
            "m" (month), "y" (year).
        diagnostics: Optional dict populated with ``attempts`` and ``error``.
        max_retries: Override the configured retry count (0 = one attempt).
        retry_backoff: Override the configured backoff seconds.

    Returns:
        List of dicts, each with keys: "title", "url", "snippet".
    """
    if max_results is None:
        max_results = config.max_results if config else 10
    retries = (
        max_retries
        if max_retries is not None
        else getattr(config, "search_max_retries", 0) if config else 0
    )
    backoff = (
        retry_backoff
        if retry_backoff is not None
        else getattr(config, "search_retry_backoff", 0.0) if config else 0.0
    )

    def _call():
        from ddgs import DDGS  # lazy: optional dependency

        with DDGS() as ddgs:
            return list(ddgs.text(query, max_results=max_results))

    return _search_with_retries(
        _call,
        query,
        max_retries=int(retries or 0),
        backoff=float(backoff or 0.0),
        diagnostics=diagnostics,
    )


def web_search_news(
    query: str,
    config: Optional[ResearchConfig] = None,
    max_results: Optional[int] = None,
    region: str = "wt-wt",
    time_range: Optional[str] = None,
) -> List[Dict[str, str]]:
    """Search DuckDuckGo News for recent articles.

    Args:
        query: The search query string.
        config: Optional ResearchConfig for max_results default.
        max_results: Maximum number of results to return.
        region: DuckDuckGo region code.
        time_range: Time filter — None, "d", "w", "m".

    Returns:
        List of dicts with keys: "title", "url", "snippet", "date", "source".
    """
    if max_results is None:
        max_results = config.max_results if config else 10
    retries = getattr(config, "search_max_retries", 0) if config else 0
    backoff = getattr(config, "search_retry_backoff", 0.0) if config else 0.0

    def _call():
        from ddgs import DDGS  # lazy: optional dependency

        with DDGS() as ddgs:
            raw_results = list(ddgs.news(query, max_results=max_results))
        results = []
        for item in raw_results or []:
            if not isinstance(item, dict):
                continue
            url = item.get("url") or item.get("href") or ""
            if not isinstance(url, str) or not url.strip():
                continue
            results.append({
                "title": str(item.get("title") or ""),
                "url": url.strip(),
                "snippet": str(item.get("body") or item.get("snippet") or ""),
                "date": str(item.get("date") or ""),
                "source": str(item.get("source") or ""),
            })
        return results

    return _search_with_retries(
        _call,
        query,
        max_retries=int(retries or 0),
        backoff=float(backoff or 0.0),
        diagnostics=None,
    )


def format_search_results(results: List[Dict[str, str]]) -> str:
    """Format search results into a readable text block for LLM consumption.

    Args:
        results: List of search result dicts.

    Returns:
        Formatted string with numbered results.
    """
    if not results:
        return "No search results found."

    formatted = []
    for i, r in enumerate(results, 1):
        if not isinstance(r, dict):
            continue
        entry = f"[{i}] {r.get('title', '')}\n    URL: {r.get('url', '')}\n    {r.get('snippet', '')}"
        if r.get("date"):
            entry += f"\n    Date: {r['date']}"
        if r.get("source"):
            entry += f"\n    Source: {r['source']}"
        formatted.append(entry)

    return "\n\n".join(formatted) if formatted else "No search results found."
