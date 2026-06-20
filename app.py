"""AI Research Agent — Streamlit entry point.

Thin shell: configure → sidebar → run_workflow → save → display.
All logic lives in agents/, graph/, ui/, and database/.
"""

import streamlit as st
from dotenv import load_dotenv

from config.config import ResearchConfig
from generation.llm import check_ollama_connection
from graph.workflow import run_research_pipeline
from database.database import init_database, save_session, get_all_sessions, get_session_by_id
from ui.components import (
    render_sidebar,
    render_process,
    render_report,
    render_connection_error,
    render_history,
    render_history_report,
    render_history_tab,
)
from typing import Any

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
tab_process, tab_report, tab_history = st.tabs(["Research Process", "Report", "History"])

# --- Run Pipeline ---
if start_button:
    st.session_state.research_done = False
    st.session_state.final_state = None

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

    try:
        final_state = run_research_pipeline(
            user_topic,
            config,
            on_progress=on_progress,
            on_chunk=on_chunk
        )
        st.session_state.final_state = final_state
        st.session_state.research_done = True

        # Save to database
        session_id = save_session(
            topic=user_topic,
            plan=final_state.get("plan"),
            facts=final_state.get("facts", []),
            sources=final_state.get("sources", []),
            report=final_state.get("report"),
            status=final_state.get("status", "complete"),
            config=config,
        )
        # Set this as the selected session so the sidebar/tab defaults to it
        st.session_state.selected_session_id = session_id

        # Rerun to clean up progress bar and render final static report
        st.rerun()

    except Exception as e:
        status_msg.error(f"Pipeline error: {e}")
        step_status.update(label="Pipeline failed", state="error")
        st.session_state.final_state = {
            "report": f"# Research on {user_topic}\n\nError: {e}",
            "status": "error",
        }
        st.session_state.research_done = True

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