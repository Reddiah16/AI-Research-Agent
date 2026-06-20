"""DuckDuckGo web search tool for the Research Agent.

Replaces OpenAI's WebSearchTool() with a local, free alternative.
No API key required.
"""

import logging
from typing import Dict, List, Optional

from ddgs import DDGS

from config.config import ResearchConfig

logger = logging.getLogger(__name__)


def web_search(
    query: str,
    config: Optional[ResearchConfig] = None,
    max_results: Optional[int] = None,
    region: str = "wt-wt",
    time_range: Optional[str] = None,
) -> List[Dict[str, str]]:
    """Search the web using DuckDuckGo.

    Args:
        query: The search query string.
        config: Optional ResearchConfig for max_results default.
        max_results: Maximum number of results to return.
            Falls back to config.max_results, then 10.
        region: DuckDuckGo region code (default: worldwide).
        time_range: Time filter — None, "d" (day), "w" (week),
            "m" (month), "y" (year).

    Returns:
        List of dicts, each with keys: "title", "url", "snippet".
    """
    if max_results is None:
        max_results = config.max_results if config else 10

    try:
        with DDGS() as ddgs:
            raw_results = list(
                ddgs.text(
                    query,
                    max_results=max_results
                )
            )

        results = []
        for r in raw_results:
            results.append({
                "title": r.get("title", ""),
                "url": r.get("href", ""),
                "snippet": r.get("body", ""),
            })

        logger.info(f"DuckDuckGo search '{query}' returned {len(results)} results")
        return results

    except Exception as e:
        import traceback

        logger.error(f"DuckDuckGo search error for '{query}': {e}")
        traceback.print_exc()

        return []


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

    try:
        with DDGS() as ddgs:
            raw_results = list(
                ddgs.news(
                    query,
                    max_results=max_results
                )
            )

        results = []
        for r in raw_results:
            results.append({
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "snippet": r.get("body", ""),
                "date": r.get("date", ""),
                "source": r.get("source", ""),
            })

        logger.info(f"DuckDuckGo news search '{query}' returned {len(results)} results")
        return results

    except Exception as e:
        logger.error(f"DuckDuckGo news search error for '{query}': {e}")
        return []


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
        entry = f"[{i}] {r['title']}\n    URL: {r['url']}\n    {r['snippet']}"
        if r.get("date"):
            entry += f"\n    Date: {r['date']}"
        if r.get("source"):
            entry += f"\n    Source: {r['source']}"
        formatted.append(entry)

    return "\n\n".join(formatted)
