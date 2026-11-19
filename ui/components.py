"""Streamlit UI components for the Research Agent.

All rendering logic lives here — sidebar, process display, report display.
app.py just wires these together.
"""

import streamlit as st
from typing import Any, Dict, List, Optional, Union

from schemas.models import ResearchReport
from config.config import ResearchConfig


# --- Sidebar ---

EXAMPLE_TOPICS = [
    "What are the best cruise lines in USA for first-time travelers who have never been on a cruise?",
    "What are the best affordable espresso machines for someone upgrading from a French press?",
    "What are the best off-the-beaten-path destinations in India for a first-time solo traveler?",
]


def render_sidebar(config: ResearchConfig) -> tuple[str, bool]:
    """Render the sidebar and return (topic, start_button).

    Args:
        config: App configuration for displaying model info.

    Returns:
        Tuple of (user_topic, should_start).
    """
    with st.sidebar:
        st.header("Research Topic")
        user_topic = st.text_input(
            "Enter a topic to research:",
            max_chars=500,
            help=(
                "Up to 500 characters. The topic is sent to the local LLM and "
                "stored in the research history."
            ),
        )

        start_button = st.button("Start Research", type="primary", disabled=not user_topic)

        st.divider()
        st.subheader("Example Topics")
        for topic in EXAMPLE_TOPICS:
            if st.button(topic):
                user_topic = topic
                start_button = True

        st.divider()
        st.caption(f"🤖 Model: `{config.llm_model}`")
        st.caption(f"🔗 Ollama: `{config.ollama_host}`")
        st.caption("🔀 Pipeline: LangGraph")

    # Normalise input once, after example-topic selection. An all-whitespace
    # topic is treated as empty and cannot start a run.
    user_topic = (user_topic or "").strip()
    if not user_topic:
        start_button = False
    return user_topic, start_button


# --- Research Process Tab ---

def render_process(final_state: Dict[str, Any]) -> None:
    """Render the Research Process tab from pipeline state.

    Args:
        final_state: The GraphState dict returned by LangGraph.
    """
    st.write("🚀 **LangGraph Pipeline Complete**")
    st.divider()

    # Plan
    plan = final_state.get("plan")
    if plan:
        st.write("🔍 **Planner Node** — Research Plan:")
        st.json({
            "topic": plan.topic,
            "search_queries": plan.search_queries,
            "focus_areas": plan.focus_areas,
        })

    # Research summaries
    facts = final_state.get("facts", [])
    if facts:
        st.write(f"🔬 **Research Node** — {len(facts)} summaries gathered")
        for s in facts:
            n_sources = len(s.get("sources", []))
            st.write(f"  🌐 *{s['query']}* — {n_sources} web results")

        st.write("📚 **Collected Research Summaries**:")
        for s in facts:
            summary = s["summary"]
            preview = summary[:200] + "..." if len(summary) > 200 else summary
            st.info(f"**Query**: {s['query']}\n\n{preview}")

    # Report status
    report = final_state.get("report")
    if isinstance(report, ResearchReport):
        st.write("📝 **Editor Node** — Report generated successfully!")
        st.write("✅ **Pipeline Complete!**")
        st.write("📄 **Report Preview**:")
        st.markdown(report.report[:300] + "...")
        st.write("*See the Report tab for the full document.*")
    elif report:
        st.write("⚠️ **Editor Node** — Report generated (unstructured)")
        st.write("Results are available in the Report tab.")

    # Pipeline summary
    st.divider()
    status = final_state.get("status", "unknown")
    sources = final_state.get("sources", [])
    st.success(f"Pipeline status: **{status}** | Sources: {len(sources)} | Facts: {len(facts)}")


# --- Report Tab ---

def render_report(
    report: Union[ResearchReport, str],
    fallback_title: str = "Research Report",
    model_name: str = "",
    config: Optional[ResearchConfig] = None,
) -> None:
    """Render the final report in the Report tab.

    Args:
        report: A ResearchReport object or raw markdown string.
        fallback_title: Title to use if report is a raw string.
        model_name: Name of the LLM model used (for metadata display).
        config: App config (unused but kept for future use).
    """
    if isinstance(report, ResearchReport):
        # --- Research Metadata panel ---
        with st.container(border=True):
            st.markdown("**📊 Research Metadata**")
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Word Count", report.word_count)
            col2.metric("Sources Cited", len(report.sources))
            col3.metric("Evidence Findings", len(getattr(report, "key_findings", []) or []))
            col4.metric("Generation Mode", getattr(report, "generation_mode", "json") or "json")
            if model_name:
                st.caption(f"Generated by {model_name}")

        unsupported = getattr(report, "unsupported_citations", []) or []
        if unsupported:
            st.warning(
                f"{len(unsupported)} citation(s) were generated but never retrieved "
                "during research. They are not treated as grounded sources."
            )

        findings = getattr(report, "key_findings", []) or []
        if findings:
            with st.expander(f"🎯 Key findings and evidence support ({len(findings)})"):
                for finding in findings:
                    if not isinstance(finding, dict):
                        continue
                    claim = finding.get("claim") or finding.get("text") or ""
                    if not claim:
                        continue
                    source_ids = [
                        str(sid) for sid in (finding.get("source_ids") or [])
                        if isinstance(sid, str) and sid
                    ]
                    status = finding.get("support_status") or (
                        "cited" if source_ids else "uncited"
                    )
                    suffix = f" — sources: {', '.join(source_ids)}" if source_ids else ""
                    st.markdown(f"- **[{status}]** {claim}{suffix}")
                    note = finding.get("support_note")
                    if note:
                        st.caption(note)

        details = getattr(report, "source_details", []) or []
        if details:
            with st.expander(f"📚 Source provenance ({len(details)} selected sources)"):
                for detail in details:
                    if not isinstance(detail, dict):
                        continue
                    label = detail.get("source_id") or "source"
                    title = detail.get("title") or "Untitled"
                    url = detail.get("url") or ""
                    st.markdown(f"**{label}** — {title}")
                    if url:
                        st.caption(url)
                    meta = " · ".join(
                        str(value) for value in (
                            detail.get("retrieval_method"),
                            detail.get("fetch_status"),
                            detail.get("source_type"),
                        ) if value
                    )
                    if meta:
                        st.caption(meta)
                context_stats = getattr(report, "_editor_context_stats", {}) or {}
                if context_stats:
                    st.caption(
                        "Editor context: "
                        f"{context_stats.get('sources_after', '?')} of "
                        f"{context_stats.get('sources_before', '?')} sources, "
                        f"~{context_stats.get('approx_tokens_after', '?')} tokens "
                        "(character/4 estimate)."
                    )

        st.markdown("")

        # --- Full markdown report (includes ## References from LLM) ---
        st.markdown(report.report)

        # --- Download ---
        st.download_button(
            label="⬇️ Download Report (.md)",
            data=report.report,
            file_name=f"{report.title.replace(' ', '_')}.md",
            mime="text/markdown",
        )
    else:
        # Raw string fallback
        report_content = str(report)
        title = fallback_title

        st.title(title)
        st.markdown(report_content)

        st.download_button(
            label="⬇️ Download Report (.md)",
            data=report_content,
            file_name=f"{title.replace(' ', '_')}.md",
            mime="text/markdown",
        )


# --- Connection Check ---

def render_connection_error(config: ResearchConfig) -> None:
    """Show Ollama connection error and stop the app.

    Args:
        config: App configuration for displaying connection details.
    """
    st.error(
        f"Cannot connect to Ollama at {config.ollama_host}. "
        "Please make sure Ollama is running and the model is available."
    )
    st.info(f"Expected model: **{config.llm_model}**")
    st.code(f"ollama pull {config.llm_model}", language="bash")
    st.stop()


# --- History ---

def render_history(sessions: list, config) -> None:
    """Render research history in the sidebar.

    Args:
        sessions: List of session dicts from get_all_sessions().
        config: App configuration.
    """
    with st.sidebar:
        st.divider()
        st.subheader("📜 Research History")

        if not sessions:
            st.caption("No past sessions yet.")
            return

        for s in sessions[:10]:  # Show last 10
            topic_short = s["topic"][:40] + "..." if len(s["topic"]) > 40 else s["topic"]
            date_str = s["created_at"][:10] if s.get("created_at") else "—"
            label = f"📄 {topic_short}"
            
            # Highlight if currently selected in session_state
            is_selected = st.session_state.get("selected_session_id") == s["id"]
            btn_type = "primary" if is_selected else "secondary"
            
            col1, col2 = st.columns([4, 1.5])
            with col1:
                if st.button(label, key=f"hist_{s['id']}", use_container_width=True, type=btn_type):
                    st.session_state.selected_session_id = s["id"]
                    st.rerun()
            with col2:
                st.caption(f"  {date_str}")


def render_history_tab(sessions: list, config: ResearchConfig) -> None:
    """Render the History Tab containing a list of past sessions, selection, delete, and details.

    Args:
        sessions: List of session dicts from get_all_sessions().
        config: App configuration.
    """
    from database.database import get_session_by_id, delete_session

    st.subheader("📚 Saved Research Sessions")

    if not sessions:
        st.info("No research history yet. Run a research query to start building your history!")
        return

    # Sync selection between sidebar and selectbox using session state
    session_ids = [s["id"] for s in sessions]
    session_options = {s["id"]: f"{s['created_at'][:10]} — {s['topic'][:80]}..." for s in sessions}

    # Retrieve current selection from session state or default to the most recent session
    current_selected_id = st.session_state.get("selected_session_id")
    if current_selected_id not in session_ids:
        current_selected_id = session_ids[0]
        st.session_state.selected_session_id = current_selected_id

    default_index = session_ids.index(current_selected_id)

    col1, col2 = st.columns([4, 1])
    with col1:
        selected_id = st.selectbox(
            "Choose a past research report to view:",
            options=session_ids,
            index=default_index,
            format_func=lambda x: session_options[x],
            key="history_tab_selectbox",
        )
    with col2:
        st.write("")  # Spacer to align button
        st.write("")
        if st.button("🗑️ Delete Session", key=f"del_btn_{selected_id}", type="secondary", use_container_width=True):
            delete_session(selected_id, config)
            st.success("Session deleted successfully!")
            # Reset selection
            remaining_ids = [sid for sid in session_ids if sid != selected_id]
            st.session_state.selected_session_id = remaining_ids[0] if remaining_ids else None
            st.rerun()

    # If the user changed the selectbox value, update session_state and rerun to sync sidebar
    if selected_id != current_selected_id:
        st.session_state.selected_session_id = selected_id
        st.rerun()

    # Load and display the session report
    session_data = get_session_by_id(selected_id, config)
    if session_data:
        st.markdown("---")
        render_history_report(session_data)
    else:
        st.warning("Could not retrieve session details.")


def render_history_report(session_data: dict) -> None:
    """Render a past research session's report.

    Args:
        session_data: Full session dict from get_session_by_id().
    """
    st.subheader(f"📜 {session_data['topic']}")

    report = session_data.get("report")
    if report:
        # --- Research Metadata panel ---
        with st.container(border=True):
            st.markdown("**📊 Research Metadata**")
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Word Count", report.get("word_count", 0))
            col2.metric("Sources Used", len(report.get("sources", [])))
            col3.metric("Generated By", session_data.get("model_name", "Ollama"))
            col4.metric("Pipeline", "LangGraph + Ollama")

        st.markdown("")
        st.markdown(report.get("report_content", ""))

        findings = report.get("key_findings") or []
        if findings:
            st.markdown("**🎯 Key Findings**")
            for finding in findings:
                if not isinstance(finding, dict):
                    continue
                claim = finding.get("claim") or finding.get("text") or ""
                if not claim:
                    continue
                source_ids = [
                    str(sid) for sid in (finding.get("source_ids") or [])
                    if isinstance(sid, str) and sid
                ]
                status = finding.get("support_status") or (
                    "cited" if source_ids else "uncited"
                )
                suffix = f" — sources: {', '.join(source_ids)}" if source_ids else ""
                st.markdown(f"- **[{status}]** {claim}{suffix}")

        unsupported = report.get("unsupported_citations") or []
        if unsupported:
            st.warning(
                f"{len(unsupported)} citation(s) were generated but never retrieved "
                "during research and are excluded from supported sources."
            )

        details = report.get("source_details") or []
        if details:
            with st.expander(f"📚 Source provenance ({len(details)} sources)"):
                for detail in details:
                    if not isinstance(detail, dict):
                        continue
                    label = detail.get("source_id") or "source"
                    title = detail.get("title") or "Untitled"
                    url = detail.get("url") or ""
                    st.markdown(f"**{label}** — {title}")
                    if url:
                        st.caption(url)
                    meta = " · ".join(
                        str(value) for value in (
                            detail.get("retrieval_method"),
                            detail.get("fetch_status"),
                        ) if value
                    )
                    if meta:
                        st.caption(meta)

        st.download_button(
            label="⬇️ Download Report (.md)",
            data=report.get("report_content", ""),
            file_name=f"{report.get('title', 'report').replace(' ', '_')}.md",
            mime="text/markdown",
            key=f"dl_hist_{session_data['id']}",
        )
    else:
        st.warning("No report found for this session.")

