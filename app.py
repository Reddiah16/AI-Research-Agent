"""AI Research Agent — Streamlit entry point.

Thin shell: configure → sidebar → run_workflow → save → display.
All logic lives in agents/, graph/, ui/, and database/.
"""

import logging
import time
import uuid

import streamlit as st
from dotenv import load_dotenv

from config.config import ResearchConfig
from generation.llm import check_ollama_connection
from graph.workflow import run_research_pipeline
from database.database import init_database, save_session, get_all_sessions, get_session_by_id
from schemas.models import ResearchReport
from ui.components import (
    render_sidebar,
    render_process,
    render_report,
    render_connection_error,
    render_history,
    render_history_report,
    render_history_tab,
)
from ui.analytics import render_analytics
from typing import Any

logger = logging.getLogger(__name__)

# --- Bootstrap ---
load_dotenv()
config = ResearchConfig()

st.set_page_config(
    page_title="AI Research Agent",
    page_icon="📰",
    layout="wide",
    initial_sidebar_state="expanded",
)

if not check_ollama_connection(config):
    render_connection_error(config)

# Initialize database tables
init_database(config)

# --- Header ---
st.title("📰 AI Research Agent")
st.subheader(f"Powered by Ollama — {config.llm_model}")
st.markdown(
    "Local multi-agent research pipeline using **LangGraph** + **Ollama**. "
    "No cloud API keys required."
)

# --- Session State ---
if "research_done" not in st.session_state:
    st.session_state.research_done = False
if "final_state" not in st.session_state:
    st.session_state.final_state = None
if "selected_session_id" not in st.session_state:
    st.session_state.selected_session_id = None

# --- Sidebar ---
user_topic, start_button = render_sidebar(config)

# --- History Sidebar ---
past_sessions = get_all_sessions(config)
render_history(past_sessions, config)

# --- Tabs ---
tab_process, tab_report, tab_history, tab_analytics = st.tabs(
    ["Research Process", "Report", "History", "📊 Analytics"]
)

# --- Run Pipeline ---
if start_button:
    st.session_state.research_done = False
    st.session_state.final_state = None
    # Idempotency key: exactly one metrics record per execution, enforced
    # by the database even if this block is somehow re-entered.
    st.session_state.current_run_id = str(uuid.uuid4())

    # Placeholders for progress feedback
    progress_bar = st.progress(0.0)
    status_msg = st.empty()

    with tab_process:
        st.subheader("🔄 Research Steps")
        step_status = st.status("Initializing pipeline...", expanded=True)

    with tab_report:
        st.subheader("📝 Live Report Generation")
        report_stream = st.empty()

    # Progress callback function
    def on_progress(node: str, status: str, details: Any):
        if node == "planner":
            if status == "running":
                progress_bar.progress(0.1)
                status_msg.info("Step 1/3: Creating research plan...")
                step_status.write("🔍 **Planner Node**: Analyzing topic and generating research plan...")
            elif status == "complete":
                progress_bar.progress(0.3)
                plan = details
                step_status.write("✓ **Plan Created**:")
                step_status.json({
                    "topic": plan.topic,
                    "search_queries": plan.search_queries,
                    "focus_areas": plan.focus_areas
                })
        elif node == "researcher":
            if status == "running":
                progress_bar.progress(0.4)
                status_msg.info("Step 2/3: Gathering facts from the web...")
                step_status.write("🔬 **Research Node**: Initiating web search and summarization...")
            elif status == "query_start":
                idx = details["index"]
                total = details["total"]
                query = details["query"]
                step_status.write(f"  🌐 *Searching ({idx+1}/{total})*: `{query}`...")
            elif status == "query_search_done":
                idx = details["index"]
                query = details["query"]
                count = details["results_count"]
                step_status.write(f"  ✓ Found {count} web results for `{query}`. Summarizing...")
            elif status == "query_summary_done":
                idx = details["index"]
                total = details["total"]
                query = details["query"]
                step_status.write(f"  ✓ **Summary gathered** for `{query}`.")
                progress_bar.progress(0.4 + 0.4 * ((idx + 1) / total))
            elif status == "complete":
                progress_bar.progress(0.8)
                status_msg.info("Step 3/3: Synthesizing final report...")
                step_status.write(f"✓ **Research Complete**: Gathered {len(details)} summaries.")
        elif node == "editor":
            if status == "running":
                progress_bar.progress(0.85)
                step_status.write("📝 **Editor Node**: Generating comprehensive research report...")
            elif status == "complete":
                progress_bar.progress(1.0)
                status_msg.success("Research pipeline complete!")
                step_status.update(label="✓ All Agent steps completed!", state="complete")

    # Chunk callback function
    def on_chunk(chunk: str):
        if "report_stream_text" not in st.session_state:
            st.session_state.report_stream_text = ""
        st.session_state.report_stream_text += chunk
        report_stream.markdown(st.session_state.report_stream_text)

    # Initialize stream text in session state
    st.session_state.report_stream_text = ""

    pipeline_start = time.perf_counter()
    pipeline_succeeded = False

    try:
        final_state = run_research_pipeline(
            user_topic,
            config,
            on_progress=on_progress,
            on_chunk=on_chunk
        )
        execution_time = time.perf_counter() - pipeline_start

        # SUCCESS only when the editor produced a valid structured report;
        # partially completed / unstructured runs are recorded as FAILED.
        report = final_state.get("report")
        is_success = (
            final_state.get("status") == "complete"
            and isinstance(report, ResearchReport)
        )
        execution_status = "SUCCESS" if is_success else "FAILED"

        st.session_state.final_state = final_state
        st.session_state.research_done = True

        # Save to database.
        # The report is only persisted for successful runs, so failed
        # executions never inflate the generated-report count.
        session_id = save_session(
            topic=user_topic,
            plan=final_state.get("plan"),
            facts=final_state.get("facts", []),
            sources=final_state.get("sources", []),
            report=report if is_success else None,
            status=execution_status,
            config=config,
            execution_time=execution_time,
            run_id=st.session_state.current_run_id,
        )
        # Set this as the selected session so the sidebar/tab defaults to it
        st.session_state.selected_session_id = session_id
        pipeline_succeeded = True

    except Exception as e:
        # st.rerun() raises a control-flow exception (RerunException) — it is
        # NOT a pipeline failure. Re-raise so it reaches Streamlit's runner.
        if pipeline_succeeded:
            raise
        execution_time = time.perf_counter() - pipeline_start
        status_msg.error(f"Pipeline error: {e}")
        step_status.update(label="Pipeline failed", state="error")
        st.session_state.final_state = {
            "report": f"# Research on {user_topic}\n\nError: {e}",
            "status": "error",
        }
        st.session_state.research_done = True

        # Persist the failed run so failure metrics and timing are captured.
        # A DB error here must not mask the original pipeline error.
        # Credentials are stripped from the message by the database layer.
        try:
            save_session(
                topic=user_topic,
                plan=None,
                facts=[],
                sources=[],
                report=None,
                status="FAILED",
                config=config,
                execution_time=execution_time,
                run_id=st.session_state.current_run_id,
                error_message=f"{type(e).__name__}: {e}",
            )
        except Exception as db_error:
            logger.error(f"Failed to persist failed run: {db_error}")
            st.warning(f"Could not save failed session to history: {db_error}")

    # Rerun OUTSIDE the try/except so the control-flow exception raised by
    # st.rerun() is never mistaken for a pipeline error (which previously
    # risked a duplicate FAILED record after a successful save).
    if pipeline_succeeded:
        # Clean up progress bar and render final static report
        st.rerun()

# --- Display Current Results ---
if st.session_state.research_done and st.session_state.final_state:
    state = st.session_state.final_state

    with tab_process:
        render_process(state)

    with tab_report:
        report = state.get("report")
        if report:
            render_report(
                report,
                fallback_title=user_topic.title() if user_topic else "Report",
                model_name=config.llm_model,
            )

# --- Display History ---
with tab_history:
    # Refresh sessions list to include newly added or deleted sessions
    refreshed_sessions = get_all_sessions(config)
    render_history_tab(refreshed_sessions, config)

# --- Display Analytics Dashboard ---
with tab_analytics:
    render_analytics(config)