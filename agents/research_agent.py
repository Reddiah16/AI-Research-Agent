"""Research Agent — searches the web and assembles provenance-rich evidence.

For each query it:

1. normalises, validates, and deduplicates DuckDuckGo results;
2. rejects advertisement/click-tracking redirectors;
3. scores candidates with the deterministic source ranker (optional);
4. deduplicates across queries before spending fetch budget;
5. optionally fetches a bounded number of pages and extracts visible text;
6. filters empty/irrelevant content and caps the evidence budget;
7. records provenance (snippet vs fetched page, fetch status, relevance,
   ranking components);
8. returns source URLs, titles, candidate pools, and evidence items.

Search recall (the ranked candidate pool) is tracked separately from retention
so evaluation can distinguish "search never returned the source" from
"retention discarded it". Page fetching is best-effort; failures fall back to
the snippet and never break the LangGraph node.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Set

from config.config import ResearchConfig
from schemas.models import ResearchPlan
from tools.page_fetcher import FetchCache, RobotsPolicy, fetch_page
from tools.research_evidence import (
    assemble_summary,
    build_query_evidence,
    is_redirect_or_ad_url,
    normalize_results,
)
from tools.source_ranking import rank_candidates, ranking_summary
from tools.web_search import web_search

logger = logging.getLogger(__name__)


def _make_fetcher(config: ResearchConfig, cache: FetchCache, robots_policy: Optional[RobotsPolicy]):
    """Create a cached, bounded, robots-aware fetch callable."""
    timeout = float(getattr(config, "page_fetch_timeout", 6.0) or 6.0)
    max_bytes = int(getattr(config, "page_fetch_max_bytes", 500_000) or 500_000)
    respect_robots = bool(getattr(config, "respect_robots_txt", False))
    resolve_dns = bool(getattr(config, "enforce_dns_safety", False))
    retry_429 = int(getattr(config, "fetch_429_max_retries", 0) or 0)
    retry_429_backoff = float(getattr(config, "fetch_429_backoff", 0.0) or 0.0)

    def _fetch(url: str) -> Dict:
        return fetch_page(
            url,
            timeout=timeout,
            max_bytes=max_bytes,
            respect_robots=respect_robots,
            robots_policy=robots_policy,
            resolve_dns=resolve_dns,
            retry_statuses=(429,),
            max_retries=retry_429,
            retry_backoff=retry_429_backoff,
        )

    return lambda url: cache.get(url, _fetch)


def _candidate_record(
    record: Dict[str, Any],
    *,
    query: str,
    query_index: int,
    rank_position: int,
) -> Dict[str, Any]:
    return {
        "url": record.get("url", ""),
        "original_url": record.get("original_url", record.get("url", "")),
        "canonical_url": record.get("canonical_url", ""),
        "title": record.get("title", ""),
        "snippet": record.get("snippet", ""),
        "search_query": query,
        "search_query_index": query_index,
        "rank_position": rank_position,
        "original_rank": record.get("original_rank"),
        "rank_score": record.get("rank_score"),
        "rank_components": record.get("rank_components") or {},
        "authority_score": record.get("authority_score"),
        "source_type": record.get("source_type"),
    }


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
            - "candidate_pool": ranked candidates for this query
            - "search_candidates": normalized candidates in search order
            - "evidence_items": provenance-rich evidence records
            - "search_duration": measured DuckDuckGo latency
            - "usable_evidence_count", "filtered_results_count",
              "fetched_pages_count", "fetch_failed_count", ...
    """
    research_summaries: List[Dict] = []
    fetch_enabled = bool(getattr(config, "enable_page_fetch", False))
    max_items = int(getattr(config, "max_evidence_items_per_query", 5) or 5)
    fetch_limit = int(getattr(config, "max_pages_per_query", 4) or 0)
    max_excerpt_chars = int(getattr(config, "evidence_excerpt_chars", 900) or 900)
    min_relevance = float(getattr(config, "min_evidence_relevance", 0.05) or 0.0)
    total_fetch_budget = int(getattr(config, "max_total_pages_per_run", 24) or 0)
    max_results = int(getattr(config, "max_results", 10) or 10)
    ranking_enabled = bool(getattr(config, "enable_source_ranking", True))

    cache = FetchCache(max_fetches=total_fetch_budget)
    robots_policy = None
    if fetch_enabled and bool(getattr(config, "respect_robots_txt", False)):
        robots_policy = RobotsPolicy(
            timeout=float(getattr(config, "robots_timeout", 3.0) or 3.0)
        )
    fetcher = _make_fetcher(config, cache, robots_policy) if fetch_enabled else None

    # Canonical URLs already retained or fetched. Cross-query duplicate
    # candidates are skipped before any fetch budget is spent.
    seen_canonical: Set[str] = set()

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
            max_results=max_results,
            diagnostics=search_diagnostics,
        )
        search_duration = time.perf_counter() - t_search_start
        normalized = normalize_results(search_results)
        all_retrieved_urls = [record["url"] for record in normalized]

        # Step 2: reject redirectors, then rank candidates deterministically.
        rejected = [record for record in normalized if is_redirect_or_ad_url(record["url"])]
        eligible = [record for record in normalized if not is_redirect_or_ad_url(record["url"])]
        ranked = (
            rank_candidates(eligible, query, plan.topic)
            if ranking_enabled
            else eligible
        )
        search_candidates = [
            _candidate_record(
                record,
                query=query,
                query_index=i,
                rank_position=position,
            )
            for position, record in enumerate(normalized, start=1)
        ]
        candidate_pool = [
            _candidate_record(
                record,
                query=query,
                query_index=i,
                rank_position=position,
            )
            for position, record in enumerate(ranked, start=1)
        ]

        logger.info(
            f"  Found {len(search_results)} results ({len(normalized)} valid, "
            f"{len(rejected)} redirects rejected) for: {query} "
            f"({search_duration:.3f}s, attempts={search_diagnostics.get('attempts')})"
        )
        if ranking_enabled and ranked:
            logger.info(f"  Top ranked candidates: {ranking_summary(ranked, 3)}")

        if on_progress:
            on_progress("researcher", "query_search_done", {
                "index": i,
                "total": len(plan.search_queries),
                "query": query,
                "results_count": len(search_results),
                "valid_results_count": len(normalized),
            })

        # Step 3: build evidence from snippets and optional page text.
        cache_hits_before = cache.hits
        budget_exhausted_before = cache.budget_exhausted
        try:
            evidence = build_query_evidence(
                query=query,
                raw_results=ranked,
                topic=plan.topic,
                fetcher=fetcher,
                max_items=max_items,
                fetch_limit=fetch_limit,
                max_excerpt_chars=max_excerpt_chars,
                min_relevance=min_relevance,
                seen_urls=seen_canonical,
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
                "duplicates_skipped": 0,
                "attempted_canonical_urls": [],
            }
        items = evidence["items"]

        # Mark retained and fetched URLs so later queries do not duplicate
        # registry entries or re-spend the fetch budget.
        seen_canonical.update(item["canonical_url"] for item in items)
        seen_canonical.update(evidence.get("attempted_canonical_urls") or [])

        summary = assemble_summary(items, max_items=max_items)
        source_urls = [item["url"] for item in items]
        fetch_errors = [
            item.get("fetch_status", "")
            for item in items
            if str(item.get("fetch_status", "")).startswith("failed:")
        ]
        snippet_only = sum(
            1 for item in items if item.get("retrieval_method") == "search_snippet"
        )
        fetched_ok = sum(
            1 for item in items if item.get("fetch_status") == "ok"
        )

        research_summaries.append({
            "query": query,
            "summary": summary,
            "sources": normalized,
            "source_urls": source_urls,
            "all_retrieved_urls": all_retrieved_urls,
            "search_candidates": search_candidates,
            "candidate_pool": candidate_pool,
            "evidence_items": items,
            "search_duration": round(search_duration, 4),
            "search_attempts": search_diagnostics.get("attempts"),
            "search_error": search_diagnostics.get("error"),
            "raw_results_count": len(search_results),
            "normalized_candidates_count": len(normalized),
            "ranked_candidates_count": len(ranked),
            "redirects_rejected_count": len(rejected),
            "duplicate_candidates_removed": max(0, len(search_results) - len(normalized)),
            "cross_query_duplicates_skipped": evidence.get("duplicates_skipped", 0),
            "ranking_enabled": ranking_enabled,
            "fetch_cache_hits": cache.hits - cache_hits_before,
            "fetch_budget_exhausted": cache.budget_exhausted - budget_exhausted_before,
            "fetch_errors": fetch_errors,
            "snippet_only_count": snippet_only,
            "fetched_ok_count": fetched_ok,
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
            f"({evidence['fetch_failed_count']} failed), "
            f"{evidence.get('duplicates_skipped', 0)} cross-query duplicates skipped"
        )

    return research_summaries
