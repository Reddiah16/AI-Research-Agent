"""LangGraph workflow for the Research Agent pipeline.

Graph: START → planner_node → research_node → editor_node → END

Each node reads from and writes to the shared GraphState (TypedDict),
delegating actual work to the agent functions in agents/.
"""

import logging
from typing import Any, Callable, Dict, List, Optional, Union

from langgraph.graph import StateGraph, START, END
from typing_extensions import TypedDict

from config.config import ResearchConfig
from schemas.models import ResearchPlan, ResearchReport
from agents.triage_agent import run_triage_agent
from agents.research_agent import run_research_agent
from agents.editor_agent import run_editor_agent

logger = logging.getLogger(__name__)


# --- LangGraph State (TypedDict) ---

class GraphState(TypedDict, total=False):
    """Shared state flowing through the LangGraph pipeline.

    Maps directly to ResearchState fields but uses TypedDict
    for LangGraph compatibility. Includes optional UI callbacks.
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


# --- Node Functions ---

def planner_node(state: GraphState) -> Dict[str, Any]:
    """Triage Agent node — creates a research plan from the topic.

    Reads: topic, config
    Writes: plan, status
    """
    logger.info(f"[Planner Node] Planning research for: {state['topic']}")

    on_progress = state.get("on_progress")
    if on_progress:
        on_progress("planner", "running", "Analyzing topic and generating research plan...")

    config = state["config"]
    plan = run_triage_agent(state["topic"], config)

    logger.info(
        f"[Planner Node] Plan created: {len(plan.search_queries)} queries, "
        f"{len(plan.focus_areas)} focus areas"
    )

    if on_progress:
        on_progress("planner", "complete", plan)

    return {
        "plan": plan,
        "status": "planning_complete",
    }


def research_node(state: GraphState) -> Dict[str, Any]:
    """Research Agent node — searches the web and summarizes findings.

    Reads: plan, config
    Writes: facts, sources, status
    """
    plan = state["plan"]
    config = state["config"]
    on_progress = state.get("on_progress")

    logger.info(f"[Research Node] Researching {len(plan.search_queries)} queries...")

    if on_progress:
        on_progress("researcher", "running", f"Initiating web research for {len(plan.search_queries)} queries...")

    summaries = run_research_agent(plan, config, on_progress=on_progress)

    # Collect all deduplicated source URLs
    all_sources = []
    for s in summaries:
        for url in s.get("source_urls", []):
            if url not in all_sources:
                all_sources.append(url)

    logger.info(
        f"[Research Node] Gathered {len(summaries)} summaries, "
        f"{len(all_sources)} unique sources"
    )

    if on_progress:
        on_progress("researcher", "complete", summaries)

    return {
        "facts": summaries,
        "sources": all_sources,
        "status": "research_complete",
    }


def editor_node(state: GraphState) -> Dict[str, Any]:
    """Editor Agent node — writes the final research report.

    Reads: plan, facts, config
    Writes: report, status
    """
    plan = state["plan"]
    facts = state["facts"]
    config = state["config"]
    on_progress = state.get("on_progress")
    on_chunk = state.get("on_chunk")

    logger.info(f"[Editor Node] Writing report from {len(facts)} research summaries...")

    if on_progress:
        on_progress("editor", "running", "Synthesizing research summaries into a comprehensive report...")

    report = run_editor_agent(plan, facts, config, on_chunk_callback=on_chunk)

    if isinstance(report, ResearchReport):
        logger.info(f"[Editor Node] Report complete: '{report.title}' ({report.word_count} words)")
        status = "complete"
    else:
        logger.warning("[Editor Node] Fell back to unstructured report")
        status = "complete_unstructured"

    if on_progress:
        on_progress("editor", "complete", report)

    return {
        "report": report,
        "status": status,
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
    }

    logger.info(f"[Pipeline] Starting research pipeline for: {topic}")
    final_state = graph.invoke(initial_state)
    logger.info(f"[Pipeline] Pipeline complete. Status: {final_state['status']}")

    return final_state

