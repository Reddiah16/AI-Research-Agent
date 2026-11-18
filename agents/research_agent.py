"""Research Agent — searches the web and assembles provenance-rich evidence.

The Researcher no longer builds summaries from search-result titles alone. For
each query it:

1. normalises, validates, and deduplicates DuckDuckGo results;
2. keeps the search snippet;
3. optionally fetches a bounded number of pages and extracts visible text;
4. filters empty/irrelevant content and caps the evidence budget;
5. records provenance (snippet vs fetched page, fetch status, relevance);
6. returns source URLs, titles, and evidence items for the Editor.

Page fetching is best-effort. Failures fall back to the snippet and never break
the LangGraph node.
"""

from __future__ import annotations

import logging
import time
from typing import Dict, List, Optional

from config.config import ResearchConfig
from schemas.models import ResearchPlan
from tools.page_fetcher import FetchCache, RobotsPolicy, fetch_page
from tools.research_evidence import (
    assemble_summary,
    build_query_evidence,
    normalize_results,
)
from tools.web_search import web_search

logger = logging.getLogger(__name__)


def _make_fetcher(config: ResearchConfig, cache: FetchCache, robots_policy: Optional[RobotsPolicy]):
    """Create a cached, bounded, robots-aware fetch callable."""
    timeout = float(getattr(config, "page_fetch_timeout", 6.0) or 6.0)
    max_bytes = int(getattr(config, "page_fetch_max_bytes", 500_000) or 500_000)
    respect_robots = bool(getattr(config, "respect_robots_txt", False))
    resolve_dns = bool(getattr(config, "enforce_dns_safety", False))

    def _fetch(url: str) -> Dict:
        return fetch_page(
            url,
            timeout=timeout,
            max_bytes=max_bytes,
            respect_robots=respect_robots,
            robots_policy=robots_policy,
            resolve_dns=resolve_dns,
        )

    return lambda url: cache.get(url, _fetch)


def run_research_agent(
    plan: ResearchPlan,
    config: ResearchConfig,
    on_progress=None,
) -> List[Dict]:
    """Execute web search plus evidence assembly for each query in the plan.

    Args:
        plan: The research plan from the triage agent.
        config: Application configuration.
        on_progress: Optional callback for progress updates.

    Returns:
        List of dicts, each with keys:
            - "query": the search query string
            - "summary": source-attributed evidence summary
            - "sources": normalized search result dicts
            - "source_urls": URLs retained as usable evidence
            - "all_retrieved_urls": every valid deduplicated search URL
            - "evidence_items": provenance-rich evidence records
            - "search_duration": measured DuckDuckGo latency
            - "usable_evidence_count", "filtered_results_count",
              "fetched_pages_count", "fetch_failed_count"
    """
    research_summaries: List[Dict] = []
    fetch_enabled = bool(getattr(config, "enable_page_fetch", False))
    max_items = int(getattr(config, "max_evidence_items_per_query", 3) or 3)
    fetch_limit = int(getattr(config, "max_pages_per_query", 2) or 0)
    max_excerpt_chars = int(getattr(config, "evidence_excerpt_chars", 900) or 900)
    min_relevance = float(getattr(config, "min_evidence_relevance", 0.05) or 0.0)
    total_fetch_budget = int(getattr(config, "max_total_pages_per_run", 12) or 0)

    cache = FetchCache(max_fetches=total_fetch_budget)
    robots_policy = None
    if fetch_enabled and bool(getattr(config, "respect_robots_txt", False)):
        robots_policy = RobotsPolicy(
            timeout=float(getattr(config, "robots_timeout", 3.0) or 3.0)
        )
    fetcher = _make_fetcher(config, cache, robots_policy) if fetch_enabled else None

    for i, query in enumerate(plan.search_queries):
        logger.info(f"Researching query {i+1}/{len(plan.search_queries)}: {query}")

        if on_progress:
            on_progress("researcher", "query_start", {
                "index": i,
                "total": len(plan.search_queries),
                "query": query,
            })

        # Step 1: search with bounded retries and instrumented network timing.
        t_search_start = time.perf_counter()
        search_diagnostics: Dict[str, object] = {}
        search_results = web_search(
            query,
            config=config,
            max_results=config.max_results,
            diagnostics=search_diagnostics,
        )
        search_duration = time.perf_counter() - t_search_start
        normalized = normalize_results(search_results)
        all_retrieved_urls = [record["url"] for record in normalized]

        logger.info(
            f"  Found {len(search_results)} results ({len(normalized)} valid) "
            f"for: {query} ({search_duration:.3f}s, "
            f"attempts={search_diagnostics.get('attempts')})"
        )

        if on_progress:
            on_progress("researcher", "query_search_done", {
                "index": i,
                "total": len(plan.search_queries),
                "query": query,
                "results_count": len(search_results),
                "valid_results_count": len(normalized),
            })

        # Step 2: build evidence from snippets and optional page text.
        cache_hits_before = cache.hits
        budget_exhausted_before = cache.budget_exhausted
        try:
            evidence = build_query_evidence(
                query=query,
                raw_results=search_results,
                topic=plan.topic,
                fetcher=fetcher,
                max_items=max_items,
                fetch_limit=fetch_limit,
                max_excerpt_chars=max_excerpt_chars,
                min_relevance=min_relevance,
            )
        except Exception as exc:
            # Evidence assembly must never break the LangGraph research node.
            logger.error(f"  Evidence assembly failed for '{query}': {exc}")
            evidence = {
                "items": [],
                "filtered_count": len(normalized),
                "fetched_count": 0,
                "fetch_failed_count": 0,
                "usable_count": 0,
            }
        items = evidence["items"]
        summary = assemble_summary(items, max_items=max_items)
        source_urls = [item["url"] for item in items]
        fetch_errors = [
            item.get("fetch_status", "")
            for item in items
            if str(item.get("fetch_status", "")).startswith("failed:")
        ]

        research_summaries.append({
            "query": query,
            "summary": summary,
            "sources": normalized,
            "source_urls": source_urls,
            "all_retrieved_urls": all_retrieved_urls,
            "evidence_items": items,
            "search_duration": round(search_duration, 4),
            "search_attempts": search_diagnostics.get("attempts"),
            "search_error": search_diagnostics.get("error"),
            "fetch_cache_hits": cache.hits - cache_hits_before,
            "fetch_budget_exhausted": cache.budget_exhausted - budget_exhausted_before,
            "fetch_errors": fetch_errors,
            "usable_evidence_count": evidence["usable_count"],
            "filtered_results_count": evidence["filtered_count"],
            "fetched_pages_count": evidence["fetched_count"],
            "fetch_failed_count": evidence["fetch_failed_count"],
        })

        if on_progress:
            on_progress("researcher", "query_summary_done", {
                "index": i,
                "total": len(plan.search_queries),
                "query": query,
                "summary": summary,
                "usable_evidence_count": evidence["usable_count"],
            })

        logger.info(
            f"  Evidence assembled: {evidence['usable_count']} usable, "
            f"{evidence['filtered_count']} filtered, "
            f"{evidence['fetched_count']} fetch attempts "
            f"({evidence['fetch_failed_count']} failed)"
        )

    return research_summaries
