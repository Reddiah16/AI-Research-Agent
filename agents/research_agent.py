"""Research Agent — Searches the web and summarizes findings for each query.

Simple function, no LangGraph. Takes a ResearchPlan, returns summaries.
"""

import logging
from typing import Dict, List

from config.config import ResearchConfig
from generation.llm import ollama_generate
from schemas.models import ResearchPlan
from tools.web_search import web_search, format_search_results
logger = logging.getLogger(__name__)

RESEARCH_SYSTEM_PROMPT = """You are a research assistant. Given a search query and topic context, 
produce a concise summary of what you know about this aspect of the topic. 
The summary must be 3-4 concise bullet points. 
Capture the main points. Write succinctly, no need to have complete sentences or good grammar. 
This will be consumed by someone synthesizing a report, so it's vital you capture the 
essence and ignore any fluff. Do not include any additional commentary other than the summary itself."""


def run_research_agent(
    plan: ResearchPlan,
    config: ResearchConfig,
    on_progress=None,
) -> List[Dict]:
    """Execute web search + LLM summarization for each query in the plan.

    Args:
        plan: The research plan from the triage agent.
        config: Application configuration.
        on_progress: Optional callback for progress updates.

    Returns:
        List of dicts, each with keys:
            - "query": the search query string
            - "summary": Ollama-generated summary of results
            - "sources": raw search result dicts from DuckDuckGo
            - "source_urls": list of URLs found
    """
    research_summaries = []

    for i, query in enumerate(plan.search_queries):
        logger.info(f"Researching query {i+1}/{len(plan.search_queries)}: {query}")

        if on_progress:
            on_progress("researcher", "query_start", {
                "index": i,
                "total": len(plan.search_queries),
                "query": query
            })

        # Step 1: Search the web with DuckDuckGo
        search_results = web_search(query, config=config, max_results=config.max_results)
        formatted_results = format_search_results(search_results)

        source_urls = [r["url"] for r in search_results if r.get("url")]

        logger.info(f"  Found {len(search_results)} results for: {query}")

        if on_progress:
            on_progress("researcher", "query_search_done", {
                "index": i,
                "total": len(plan.search_queries),
                "query": query,
                "results_count": len(search_results)
            })

        # Step 2: Summarize search results with Ollama
        # Per user request (optimization #6), the per-query LLM summarization step is bypassed entirely.
        # This reduces runtime from minutes to seconds.
        # The prompt below is updated for compliance with optimization #3:
        # prompt = (
        #     f"Topic: {plan.topic}\n\n"
        #     f"Search query: {query}\n\n"
        #     f"Focus areas to consider: {', '.join(plan.focus_areas)}\n\n"
        #     f"Web search results:\n{formatted_results}\n\n"
        #     f"Based on these search results, summarize in 3-4 concise bullet points."
        # )
        summary = "\n".join([
            f"- {r['title']}"
            for r in search_results[:3]
        ])

        research_summaries.append({
            "query": query,
            "summary": summary,
            "sources": search_results,
            "source_urls": source_urls,
        })

        if on_progress:
            on_progress("researcher", "query_summary_done", {
                "index": i,
                "total": len(plan.search_queries),
                "query": query,
                "summary": summary
            })

        logger.info(f"  Summary generated ({len(summary)} chars)")

    return research_summaries
