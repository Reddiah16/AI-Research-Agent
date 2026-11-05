"""Streamlit analytics dashboard for research performance metrics.

Renders KPI cards, charts and a filterable execution-history table from
the PostgreSQL metrics recorded in Phases 2–3. All data access goes
through the database service layer (database.database) — this module
contains no SQL and no sample data.

app.py wires render_analytics() into the Analytics tab.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional

import altair as alt
import pandas as pd
import streamlit as st

from config.config import ResearchConfig
from database.database import (
    FAILED_STATUSES,
    SUCCESS_STATUSES,
    get_average_execution_time,
    get_average_source_count,
    get_daily_activity,
    get_execution_history_records,
    get_execution_time_series,
    get_source_count_series,
    get_status_distribution,
    get_total_executions,
    get_total_reports,
)

logger = logging.getLogger(__name__)

# History-table time-window filter options → days (None = all time).
_WINDOW_OPTIONS: Dict[str, Optional[int]] = {
    "All time": None,
    "Last 7 days": 7,
    "Last 30 days": 30,
    "Last 90 days": 90,
}

# Status-distribution chart: explicit category→color mapping. The color
# scale domain is built from the categories actually present in the data
# (see _status_chart), so a fixed-length color range can never mismatch
# the number of categories.
_STATUS_COLORS: Dict[str, str] = {
    "SUCCESS": "#2ca25f",
    "FAILED": "#d7301f",
}


def _fmt_seconds(value: Optional[float]) -> str:
    """Format an execution duration consistently, with an empty-state dash."""
    return f"{value:.1f} s" if value is not None else "—"


def _display_status(status: Optional[str]) -> str:
    """Normalize legacy and current status vocabulary for display."""
    if status in SUCCESS_STATUSES:
        return "SUCCESS"
    if status in FAILED_STATUSES:
        return "FAILED"
    return (status or "UNKNOWN").upper()


def _safe_section(name: str, render_fn: Callable[[], None]) -> None:
    """Run one dashboard section with an isolated error boundary.

    Failures are logged at ERROR level (so automated tests can detect
    swallowed exceptions) and shown in place, without affecting the other
    sections of the dashboard. The message intentionally describes a
    *rendering* problem — it must never blame PostgreSQL, which is a
    separate failure class handled at data-load time.
    """
    try:
        render_fn()
    except Exception as e:
        logger.error(f"Analytics section '{name}' failed to render: {e}")
        st.error(
            f"⚠️ Chart-rendering problem (not a database error): the {name} "
            f"section could not be rendered: {e}"
        )


def render_analytics(config: ResearchConfig) -> None:
    """Render the full analytics dashboard (KPIs, charts, history table).

    Database connectivity problems are caught and surfaced as a friendly
    error instead of crashing the Streamlit app. Each dashboard section
    has its own error boundary so one broken section cannot hide the rest.

    Args:
        config: App configuration.
    """
    st.subheader("📊 Research Performance Analytics")

    col_refresh, col_spacer = st.columns([1, 3])
    with col_refresh:
        if st.button("🔄 Refresh metrics", key="analytics_refresh"):
            st.rerun()
    with col_spacer:
        st.caption(
            f"Live data from PostgreSQL · last refreshed "
            f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
        )

    # --- Data load (DB failure here is fatal to the dashboard) ---
    try:
        total = get_total_executions(config)
    except Exception as e:
        logger.error(f"Analytics database access failed: {e}")
        st.error(f"⚠️ Could not load analytics from the database: {e}")
        # A missing/empty DATABASE_URL is a configuration problem, not a
        # server-availability one — the guidance must not misreport it as
        # "PostgreSQL is down".
        if "DATABASE_URL" in str(e):
            st.warning(
                "**Configuration problem, not a server problem:** no database "
                "URL is configured. Copy `.env.example` to `.env`, set "
                "`DATABASE_URL`, restart the app, then press **Refresh "
                "metrics**."
            )
        else:
            st.info(
                "Check that PostgreSQL is running and reachable, then press "
                "**Refresh metrics**."
            )
        return

    if total == 0:
        st.info(
            "📭 No research executions recorded yet. Run a research query — "
            "the dashboard populates automatically after each execution."
        )
        return

    try:
        distribution = get_status_distribution(config)
        success = distribution["SUCCESS"]
        failed = distribution["FAILED"]
        success_rate = (success / total * 100) if total else 0.0
        avg_time = get_average_execution_time(config)
        avg_sources = get_average_source_count(config)
        reports = get_total_reports(config)
    except Exception as e:
        logger.error(f"Analytics KPI aggregation failed: {e}")
        st.error(f"⚠️ Could not compute KPIs from the database: {e}")
        return

    # --- KPI cards ---
    st.markdown("**Overview**")
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Total Executions", total)
    k2.metric("Successful", success)
    k3.metric("Failed", failed)
    k4.metric("Success Rate", f"{success_rate:.1f}%")

    k5, k6, k7 = st.columns(3)
    # avg_time is None only when no execution recorded timing (legacy rows).
    # Nothing is backfilled — the empty state is stated explicitly.
    k5.metric(
        "Avg Execution Time",
        _fmt_seconds(avg_time),
        help=(
            None if avg_time is not None
            else "No execution timing data available — existing records "
                 "pre-date performance tracking."
        ),
    )
    k6.metric("Avg Unique Sources", f"{avg_sources:.1f}" if avg_sources is not None else "—")
    k7.metric("Reports Generated", reports)

    st.divider()

    # --- Charts (each chart has its own error boundary, so one broken
    #     chart cannot hide the other charts or the history table) ---
    st.markdown("**Visual Analytics**")
    chart_a, chart_b = st.columns(2)
    with chart_a:
        st.caption("A. Execution status distribution")
        _safe_section(
            "execution status chart", lambda: _render_status_chart(success, failed)
        )
    with chart_b:
        st.caption("B. Execution time per run (seconds)")
        _safe_section(
            "execution time chart", lambda: _render_execution_time_chart(config)
        )
    st.caption("C. Research activity — executions per day (last 90 days)")
    _safe_section("research activity chart", lambda: _render_activity_chart(config))
    st.caption("D. Unique sources retrieved per execution (most recent 50)")
    _safe_section("source count chart", lambda: _render_source_count_chart(config))

    st.divider()

    # --- Filterable execution history (isolated error boundary) ---
    _safe_section("execution history table", lambda: _render_history_table(config, total))


def _render_status_chart(success: int, failed: int) -> None:
    """A. Success vs failed counts, with colors bound to actual statuses.

    Only categories that exist (count > 0) are plotted, and the color
    scale domain is derived from those categories — so the length
    mismatch Plotly raises when a fixed color range meets a single-category
    dataset cannot occur here. An all-empty dataset shows an info panel.
    """
    rows = []
    if success > 0:
        rows.append({"Status": "SUCCESS", "Executions": success})
    if failed > 0:
        rows.append({"Status": "FAILED", "Executions": failed})
    if not rows:
        st.info("No status data available to plot.")
        return

    df = pd.DataFrame(rows)
    colors = [_STATUS_COLORS[row] for row in df["Status"]]
    chart = (
        alt.Chart(df)
        .mark_bar()
        .encode(
            x=alt.X("Status:N", title="Status", sort=["SUCCESS", "FAILED"]),
            y=alt.Y("Executions:Q", title="Executions"),
            color=alt.Color(
                "Status:N",
                scale=alt.Scale(domain=list(df["Status"]), range=colors),
                legend=None,
            ),
            tooltip=["Status:N", "Executions:Q"],
        )
        .properties(height=220)
    )
    st.altair_chart(chart, use_container_width=True)


def _render_execution_time_chart(config: ResearchConfig) -> None:
    """B. Execution duration per timed run (empty state when untimed)."""
    time_series = get_execution_time_series(config)
    if not time_series:
        st.info(
            "No execution timing data available — the stored records "
            "pre-date performance tracking. Durations appear automatically "
            "once a new research task completes."
        )
        return
    df = pd.DataFrame(time_series)
    df = df.iloc[::-1]  # chronological left → right
    df["Run"] = [f"#{int(r.id)}" for r in df.itertuples()]
    st.bar_chart(df.set_index("Run")["execution_time_seconds"], height=220)


def _render_activity_chart(config: ResearchConfig) -> None:
    """C. Executions per calendar day over the last 90 days."""
    activity = get_daily_activity(config, days=90)
    if not activity:
        st.info("No executions in the last 90 days.")
        return
    act_df = pd.DataFrame(activity)
    st.bar_chart(act_df.set_index("date")["count"], height=220)


def _render_source_count_chart(config: ResearchConfig) -> None:
    """D. Unique sources retrieved per execution (most recent 50)."""
    sources = get_source_count_series(config)
    if not sources:
        st.info("No source data recorded yet.")
        return
    src_df = pd.DataFrame(sources)
    src_df = src_df.iloc[::-1]
    src_df["Run"] = [f"#{int(r.id)}" for r in src_df.itertuples()]
    st.bar_chart(src_df.set_index("Run")["source_count"], height=220)


def _render_history_table(config: ResearchConfig, total: int) -> None:
    """Render the filterable execution history table.

    Args:
        total: Pre-fetched total execution count (avoids a second query
            for the footer caption).
    """
    st.markdown("**Execution History**")

    f_status, f_window, f_search = st.columns([1, 1, 2])
    with f_status:
        status_label = st.selectbox(
            "Status", ["All", "Success", "Failed"], key="analytics_filter_status"
        )
    with f_window:
        window_label = st.selectbox(
            "Date range", list(_WINDOW_OPTIONS.keys()), index=0,
            key="analytics_filter_window",
        )
    with f_search:
        search_text = st.text_input(
            "Search research query", placeholder="e.g. espresso machines…",
            key="analytics_filter_search",
        )

    status_filter = {"All": None, "Success": "SUCCESS", "Failed": "FAILED"}[status_label]
    window_days = _WINDOW_OPTIONS[window_label]
    since = (
        datetime.now(timezone.utc) - timedelta(days=window_days)
        if window_days is not None
        else None
    )

    records: List[Dict] = get_execution_history_records(
        config,
        status=status_filter,
        since=since,
        search_text=search_text or None,
    )

    if not records:
        st.info(
            "No executions match the current filters. Adjust the filters "
            "or run a new research task."
        )
        return

    table = pd.DataFrame([
        {
            "Research Query": _shorten(r["topic"]),
            "Status": _display_status(r["status"]),
            "Execution Time": _fmt_seconds(r["execution_time_seconds"]),
            "Sources": r["source_count"],
            "Report": f"#{r['report_id']}" if r["report_id"] is not None else "—",
            "Executed At (UTC)": _fmt_timestamp(r["created_at"]),
        }
        for r in records
    ])
    st.dataframe(table, use_container_width=True, hide_index=True)
    st.caption(f"Showing {len(table)} of {total} recorded executions.")


def _shorten(text: str, max_len: int = 60) -> str:
    """Truncate long research topics for table display."""
    if not text:
        return "—"
    return text if len(text) <= max_len else text[: max_len - 1] + "…"


def _fmt_timestamp(iso: Optional[str]) -> str:
    """Format an ISO timestamp consistently as 'YYYY-MM-DD HH:MM UTC'."""
    if not iso:
        return "—"
    try:
        return iso[:16].replace("T", " ")
    except Exception:
        return iso
