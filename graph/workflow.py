"""LangGraph workflow for the Research Agent pipeline.

Graph: START → planner_node → research_node → editor_node → END

Each node reads from and writes to the shared GraphState (TypedDict),
delegating actual work to the agent functions in agents/.
"""

import logging
import time
from typing import Any, Callable, Dict, List, Optional, Union

from langgraph.graph import StateGraph, START, END
from typing_extensions import TypedDict

from config.config import ResearchConfig
from schemas.models import ResearchPlan, ResearchReport
from agents.triage_agent import run_triage_agent
from agents.research_agent import run_research_agent
from agents.editor_agent import run_editor_agent
from evaluation.engine import evaluate_open_state
from tools.grounding import analyze_source_grounding
from tools.research_evidence import build_source_registry

logger = logging.getLogger(__name__)


# --- LangGraph State (TypedDict) ---

class GraphState(TypedDict, total=False):
    """Shared state flowing through the LangGraph pipeline.

    Maps directly to ResearchState fields but uses TypedDict
    for LangGraph compatibility. Includes optional UI callbacks
    and Phase 2 telemetry instrumentation fields.
    """
    topic: str
    plan: Optional[ResearchPlan]
    facts: List[Dict]
    sources: List[str]
    report: Optional[Union[ResearchReport, str]]
    status: str
    config: ResearchConfig
    on_progress: Optional[Callable[[str, str, Any], None]]
    on_chunk: Optional[Callable[[str], None]]
    # Phase 2 Telemetry & Instrumentation
    planner_latency: Optional[float]
    research_latency: Optional[float]
    editor_latency: Optional[float]
    search_network_duration: Optional[float]
    grounded_sources: Optional[List[str]]
    grounded_sources_count: Optional[int]
    citation_grounding_ratio: Optional[float]
    editor_generation_mode: Optional[str]
    # Phase 2 research-quality provenance
    source_registry: List[Dict[str, Any]]
    claim_citation_coverage: Optional[float]
    unsupported_citations: List[str]
    research_metrics: Dict[str, Any]
    # Phase 3 evaluation / reliability
    evaluation: Dict[str, Any]
    pipeline_latency_seconds: Optional[float]
    pipeline_error: Optional[str]


# --- Node Functions ---

def planner_node(state: GraphState) -> Dict[str, Any]:
    """Triage Agent node — creates a research plan from the topic.

    Reads: topic, config
    Writes: plan, status, planner_latency
    """
    logger.info(f"[Planner Node] Planning research for: {state['topic']}")

    on_progress = state.get("on_progress")
    if on_progress:
        on_progress("planner", "running", "Analyzing topic and generating research plan...")

    t_start = time.perf_counter()
    config = state["config"]
    plan = run_triage_agent(state["topic"], config)
    planner_latency = time.perf_counter() - t_start

    logger.info(
        f"[Planner Node] Plan created in {planner_latency:.3f}s: {len(plan.search_queries)} queries, "
        f"{len(plan.focus_areas)} focus areas"
    )

    if on_progress:
        on_progress("planner", "complete", plan)

    return {
        "plan": plan,
        "status": "planning_complete",
        "planner_latency": round(planner_latency, 3),
    }


def research_node(state: GraphState) -> Dict[str, Any]:
    """Research Agent node — searches the web and summarizes findings.

    Reads: plan, config
    Writes: facts, sources, status, research_latency, search_network_duration
    """
    plan = state["plan"]
    config = state["config"]
    on_progress = state.get("on_progress")

    logger.info(f"[Research Node] Researching {len(plan.search_queries)} queries...")

    if on_progress:
        on_progress("researcher", "running", f"Initiating web research for {len(plan.search_queries)} queries...")

    t_start = time.perf_counter()
    summaries = run_research_agent(plan, config, on_progress=on_progress)
    research_latency = time.perf_counter() - t_start

    # Collect deduplicated evidence URLs and provenance registry
    all_sources = []
    for s in summaries:
        for url in s.get("source_urls", []):
            if url not in all_sources:
                all_sources.append(url)

    source_registry = build_source_registry(summaries)
    research_metrics = {
        "search_results": sum(len(s.get("sources", [])) for s in summaries),
        "usable_evidence": sum(s.get("usable_evidence_count", 0) for s in summaries),
        "filtered_results": sum(s.get("filtered_results_count", 0) for s in summaries),
        "fetch_attempts": sum(s.get("fetched_pages_count", 0) for s in summaries),
        "fetch_failures": sum(s.get("fetch_failed_count", 0) for s in summaries),
        "fetch_cache_hits": sum(s.get("fetch_cache_hits", 0) for s in summaries),
        "fetch_budget_exhausted": sum(s.get("fetch_budget_exhausted", 0) for s in summaries),
        "robots_disallowed": sum(
            1
            for s in summaries
            for error in s.get("fetch_errors", [])
            if "robots_disallowed" in str(error)
        ),
        "search_attempts": sum(s.get("search_attempts") or 0 for s in summaries),
        "search_failures": sum(1 for s in summaries if s.get("search_error")),
        "registered_sources": len(source_registry),
    }

    # Compute search network duration from individual query searches
    search_network_duration = sum(s.get("search_duration", 0.0) for s in summaries)

    logger.info(
        f"[Research Node] Completed in {research_latency:.3f}s (search network: {search_network_duration:.3f}s): "
        f"gathered {len(summaries)} evidence bundles, {len(all_sources)} usable sources, "
        f"{research_metrics['fetch_attempts']} page fetches"
    )

    if on_progress:
        on_progress("researcher", "complete", summaries)

    return {
        "facts": summaries,
        "sources": all_sources,
        "status": "research_complete",
        "research_latency": round(research_latency, 3),
        "search_network_duration": round(search_network_duration, 3),
        "source_registry": source_registry,
        "research_metrics": research_metrics,
    }


def editor_node(state: GraphState) -> Dict[str, Any]:
    """Editor Agent node — writes the final research report.

    Reads: plan, facts, sources, config
    Writes: report, status, editor_latency, editor_generation_mode,
            grounded_sources, grounded_sources_count, citation_grounding_ratio
    """
    plan = state["plan"]
    facts = state["facts"]
    config = state["config"]
    on_progress = state.get("on_progress")
    on_chunk = state.get("on_chunk")
    all_sources = state.get("sources", [])

    logger.info(f"[Editor Node] Writing report from {len(facts)} research summaries...")

    if on_progress:
        on_progress("editor", "running", "Synthesizing research summaries into a comprehensive report...")

    t_start = time.perf_counter()
    report = run_editor_agent(plan, facts, config, on_chunk_callback=on_chunk)
    editor_latency = time.perf_counter() - t_start

    # Determine generation mode & report status
    if isinstance(report, ResearchReport):
        logger.info(f"[Editor Node] Report complete in {editor_latency:.3f}s: '{report.title}' ({report.word_count} words)")
        status = "complete"
        gen_mode = getattr(report, "generation_mode", "json") or "json"
        report_text = report.report
        raw_llm_sources = getattr(report, "_raw_llm_sources", None)
        claim_findings = getattr(report, "key_findings", []) or []
        unsupported_citations = getattr(report, "unsupported_citations", []) or []
    else:
        logger.warning(f"[Editor Node] Fell back to unstructured report in {editor_latency:.3f}s")
        status = "complete_unstructured"
        gen_mode = "failed"
        report_text = str(report) if report else ""
        raw_llm_sources = None
        claim_findings = []
        unsupported_citations = []

    # Source grounding analysis (citation alignment, not factual verification)
    grounding = analyze_source_grounding(
        retrieved_urls=all_sources,
        report_content=report_text,
        explicit_sources=raw_llm_sources,
        claim_findings=claim_findings,
        source_registry=state.get("source_registry", []),
    )

    if on_progress:
        on_progress("editor", "complete", report)

    evaluation = evaluate_open_state(
        plan.topic,
        {
            "plan": plan,
            "report": report,
            "sources": all_sources,
            "source_registry": state.get("source_registry", []),
            "grounded_sources_count": grounding["grounded_sources_count"],
            "citation_grounding_ratio": grounding["citation_grounding_ratio"],
            "claim_citation_coverage": grounding["claim_citation_coverage"],
            "unsupported_citations": sorted(
                set(unsupported_citations) | set(grounding["phantom_urls"])
            ),
            "research_metrics": state.get("research_metrics", {}),
        },
        plan=plan,
        support_threshold=float(
            getattr(config, "evaluation_support_threshold", 0.55) or 0.55
        ),
    )

    return {
        "report": report,
        "status": status,
        "editor_latency": round(editor_latency, 3),
        "editor_generation_mode": gen_mode,
        "grounded_sources": grounding["grounded_urls"],
        "grounded_sources_count": grounding["grounded_sources_count"],
        "citation_grounding_ratio": grounding["citation_grounding_ratio"],
        "claim_citation_coverage": grounding["claim_citation_coverage"],
        "unsupported_citations": sorted(
            set(unsupported_citations) | set(grounding["phantom_urls"])
        ),
        "evaluation": evaluation,
    }


# --- Graph Builder ---

def build_research_graph() -> StateGraph:
    """Build and compile the LangGraph research workflow.

    Flow:
        START → planner_node → research_node → editor_node → END

    Returns:
        A compiled LangGraph StateGraph ready to .invoke().
    """
    graph = StateGraph(GraphState)

    # Add nodes
    graph.add_node("planner", planner_node)
    graph.add_node("researcher", research_node)
    graph.add_node("editor", editor_node)

    # Define edges: linear pipeline
    graph.add_edge(START, "planner")
    graph.add_edge("planner", "researcher")
    graph.add_edge("researcher", "editor")
    graph.add_edge("editor", END)

    return graph.compile()


def run_research_pipeline(
    topic: str,
    config: ResearchConfig,
    on_progress: Optional[Callable[[str, str, Any], None]] = None,
    on_chunk: Optional[Callable[[str], None]] = None,
) -> GraphState:
    """Execute the full research pipeline via LangGraph.

    Convenience function that builds the graph, creates initial state,
    and invokes the pipeline.

    Args:
        topic: The user's research topic.
        config: Application configuration.
        on_progress: Optional callback for agent step progress updates.
        on_chunk: Optional callback for streaming report content chunks.

    Returns:
        The final GraphState with all fields populated.
    """
    graph = build_research_graph()

    initial_state: GraphState = {
        "topic": topic,
        "plan": None,
        "facts": [],
        "sources": [],
        "report": None,
        "status": "started",
        "config": config,
        "on_progress": on_progress,
        "on_chunk": on_chunk,
        "source_registry": [],
        "research_metrics": {},
        "evaluation": {},
        "pipeline_latency_seconds": None,
        "pipeline_error": None,
    }

    logger.info(f"[Pipeline] Starting research pipeline for: {topic}")
    t_start = time.perf_counter()
    try:
        final_state = graph.invoke(initial_state)
    except Exception as exc:
        pipeline_latency = time.perf_counter() - t_start
        logger.error(
            f"[Pipeline] Failed after {pipeline_latency:.3f}s: "
            f"{type(exc).__name__}: {exc}"
        )
        # Attach diagnostics to the initial state for callers that inspect it,
        # then re-raise so app.py/benchmarks still record the failure.
        initial_state["status"] = "error"
        initial_state["pipeline_error"] = f"{type(exc).__name__}: {exc}"
        initial_state["pipeline_latency_seconds"] = round(pipeline_latency, 3)
        raise
    pipeline_latency = time.perf_counter() - t_start
    final_state["pipeline_latency_seconds"] = round(pipeline_latency, 3)
    final_state.setdefault("pipeline_error", None)
    logger.info(
        f"[Pipeline] Pipeline complete in {pipeline_latency:.3f}s. "
        f"Status: {final_state['status']}"
    )

    return final_state

