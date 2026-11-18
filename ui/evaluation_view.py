"""Streamlit view for Phase 3 research-quality evaluation and latency.

This view is additive: the Phase 2 analytics dashboard in ``ui/analytics.py``
is unchanged and remains a separate tab. Every value is read from PostgreSQL
through ``database.database``; no score is synthesised in the UI.

Unavailable metrics are rendered as ``—`` with an explanation. Citation
quality is explicitly labelled as alignment, not factual correctness.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

import pandas as pd
import streamlit as st

from config.config import ResearchConfig
from database.database import (
    get_average_execution_time,
    get_evaluation_averages,
    get_evaluation_series,
    get_evaluation_summary,
    get_node_latency_averages,
)
from ui.evaluation_artifacts import ARTIFACT_LABELS, load_latest_artifacts

logger = logging.getLogger(__name__)

_SCORE_LABELS = {
    "overall": "Overall",
    "relevance": "Relevance",
    "evidence_coverage": "Evidence coverage",
    "citation_quality": "Citation quality",
    "completeness": "Completeness",
    "factual_support": "Factual support (evidence proxy)",
}


def _fmt_score(value: Optional[float]) -> str:
    """Format a 0–1 score as a percentage, or a dash when unmeasured."""
    return f"{value * 100:.1f}%" if isinstance(value, (int, float)) else "—"


def _fmt_seconds(value: Optional[float]) -> str:
    return f"{value:.2f} s" if isinstance(value, (int, float)) else "—"


def _safe_section(name: str, render_fn: Callable[[], None]) -> None:
    """Isolate one section so a rendering failure cannot hide the rest."""
    try:
        render_fn()
    except Exception as exc:
        logger.error(f"Evaluation section '{name}' failed to render: {exc}")
        st.error(
            f"⚠️ Chart-rendering problem (not a database error): the {name} "
            f"section could not be rendered: {exc}"
        )


def render_evaluation(config: ResearchConfig) -> None:
    """Render evaluation scores, citation/unsupported-claim counts and latency."""
    st.subheader("🔬 Research Quality & Evaluation")
    st.caption(
        "Reproducible lexical/structural scores persisted per run. Citation "
        "quality measures alignment with retrieved URLs — it is not proof of "
        "factual correctness."
    )

    col_refresh, col_note = st.columns([1, 3])
    with col_refresh:
        if st.button("🔄 Refresh evaluation", key="evaluation_refresh"):
            st.rerun()
    with col_note:
        st.caption(
            f"Live data from PostgreSQL · last refreshed "
            f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
        )

    try:
        averages = get_evaluation_averages(config)
        summary = get_evaluation_summary(config)
        latency = get_node_latency_averages(config)
        total_latency = get_average_execution_time(config)
    except Exception as exc:
        logger.error(f"Evaluation database access failed: {exc}")
        st.error(f"⚠️ Could not load evaluation data from the database: {exc}")
        if "DATABASE_URL" in str(exc):
            st.warning(
                "**Configuration problem, not a server problem:** no database "
                "URL is configured. Copy `.env.example` to `.env`, set "
                "`DATABASE_URL`, restart the app, then refresh."
            )
        else:
            st.info(
                "Check that PostgreSQL is running and reachable, then press "
                "**Refresh evaluation**."
            )
        st.divider()
        _safe_section("benchmark artifacts", _render_artifacts)
        return

    tracked = int(averages.get("tracked_runs") or 0)

    st.markdown("**Measured quality scores**")
    if tracked == 0:
        st.info(
            "📭 No evaluation records yet. New research runs persist an "
            "evaluation record automatically; historical rows are shown as "
            "unmeasured rather than backfilled."
        )
    else:
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Evaluated Runs", tracked)
        k2.metric("Avg Overall", _fmt_score(averages.get("overall")))
        k3.metric("Avg Relevance", _fmt_score(averages.get("relevance")))
        k4.metric("Avg Completeness", _fmt_score(averages.get("completeness")))

        k5, k6, k7, k8 = st.columns(4)
        k5.metric(
            "Avg Evidence Coverage",
            _fmt_score(averages.get("evidence_coverage")),
            help="Retrieval usability plus citation grounding. Not source authority.",
        )
        k6.metric(
            "Avg Citation Quality",
            _fmt_score(averages.get("citation_quality")),
            help="Alignment with retrieved URLs only; not factual correctness.",
        )
        k7.metric(
            "Avg Factual Support",
            _fmt_score(averages.get("factual_support")),
            help=(
                "Benchmark runs check verified reference claims. Open-topic runs "
                "check report claims against the excerpts of the sources they cite. "
                "Lexical overlap only; unverifiable claims are excluded."
            ),
        )
        k8.metric("Unsupported Citations", summary.get("unsupported_citations", 0))

        k9, k10 = st.columns(2)
        k9.metric(
            "Avg Claim Citation Coverage",
            _fmt_score(summary.get("claim_citation_coverage_mean")),
            help=(
                "Share of report findings that carry at least one source ID. "
                "Alignment only; it does not prove the source supports the claim."
            ),
        )
        k10.metric(
            "Unresolved Benchmark Claims",
            summary.get("unresolved_claims", 0),
            help=(
                "Claims recorded as partially supported or needing more evidence. "
                "They remain unresolved and are excluded from factual-support scores."
            ),
        )

        if summary.get("unresolved_claims"):
            st.warning(
                f"{summary['unresolved_claims']} unresolved benchmark claim(s) "
                "were reported. They remain unresolved and are never counted as "
                "verified."
            )

    st.divider()

    st.markdown("**Historical evaluation trend**")
    _safe_section("evaluation trend", lambda: _render_trend_chart(config))

    st.markdown("**Node latency (average across timed runs)**")
    _safe_section("node latency", lambda: _render_latency(latency, total_latency))

    st.divider()
    _safe_section("recent evaluation records", lambda: _render_table(config))

    st.caption(
        "Definitions: relevance = lexical aspect/topic coverage; evidence coverage "
        "= retrieval usability + citation grounding; citation quality = precision "
        "against retrieved URLs + claim-citation coverage; completeness = required "
        "sections + word-count floor; factual support = benchmark: lexical coverage "
        "of verified claims; open-topic: coverage of report claims by cited source "
        "excerpts. Unverifiable claims and unmeasured components are excluded from "
        "weighting, never imputed as zero."
    )

    st.divider()
    _safe_section("benchmark artifacts", _render_artifacts)


def _fmt_artifact_score(value) -> str:
    return f"{value:.4f}" if isinstance(value, (int, float)) else "NOT MEASURED"


def _render_artifacts() -> None:
    """Render the latest offline benchmark artifacts, if present.

    These files are produced by the ``benchmarks/run_*`` CLIs. Missing files are
    labelled, not replaced with guessed values.
    """
    st.markdown("**Offline benchmark artifacts**")
    st.caption(
        "Latest JSON files from `benchmarks/results/`. A missing artifact means "
        "the corresponding suite has not been run on this machine."
    )
    artifacts = load_latest_artifacts()

    for name in ("golden", "security", "reliability", "performance"):
        artifact = artifacts.get(name, {})
        label = ARTIFACT_LABELS.get(name, name)
        if artifact.get("status") != "loaded" or not artifact.get("data"):
            reason = artifact.get("error") or artifact.get("status", "missing")
            st.info(f"{label}: no usable artifact ({reason}).")
            continue

        data = artifact["data"]
        generated = data.get("generated_at") or "unknown time"
        mode = data.get("mode") or "unknown"
        st.markdown(
            f"*{label}* · mode `{mode}` · generated {generated} · "
            f"`{artifact.get('filename')}`"
        )

        if name == "golden":
            dataset = data.get("dataset") or {}
            validation = dataset.get("validation") or {}
            counts = validation.get("counts") or {}
            retrieval = (data.get("retrieval") or {}).get("metrics") or {}
            if not retrieval and (data.get("retrieval") or {}).get("perfect"):
                retrieval = (
                    data["retrieval"]["perfect"].get("metrics") or {}
                )
            answer = data.get("answer_quality") or {}
            columns = st.columns(4)
            columns[0].metric("Cases", counts.get("cases", "—"))
            columns[1].metric(
                "Recall@5",
                _fmt_artifact_score((retrieval.get("recall@5") or {}).get("mean")),
            )
            columns[2].metric(
                "MRR",
                _fmt_artifact_score((retrieval.get("mrr") or {}).get("mean")),
            )
            columns[3].metric(
                "Answer overall",
                _fmt_artifact_score(answer.get("overall_mean")),
            )
            if mode == "mocked":
                st.caption(
                    "Mocked mode validates metric plumbing only; these are not "
                    "live agent scores."
                )

        elif name == "security":
            deterministic = data.get("deterministic") or {}
            live = data.get("live") or {}
            columns = st.columns(4)
            columns[0].metric("Deterministic cases", deterministic.get("total_cases", "—"))
            columns[1].metric("Passed", deterministic.get("passed", "—"))
            columns[2].metric("Failed", deterministic.get("failed", "—"))
            columns[3].metric(
                "Live status", (live.get("status") or "not_run").replace("_", " ")
            )
            if live.get("status") == "measured":
                st.caption(
                    "Live probes: "
                    f"{live.get('measured', 0)} measured, "
                    f"{live.get('passed', 0)} passed, "
                    f"{live.get('failed', 0)} failed, "
                    f"{live.get('inconclusive', 0)} inconclusive. "
                    "Inconclusive probes produced no report and are excluded "
                    "from the live pass rate."
                )
            st.caption(
                "A finite deterministic suite is evidence about those probes, "
                "not a guarantee that the system is secure."
            )

        elif name == "reliability":
            summary = data.get("summary") or {}
            columns = st.columns(3)
            columns[0].metric("Scenarios", summary.get("scenarios_total", "—"))
            columns[1].metric("Passed", summary.get("scenarios_passed", "—"))
            columns[2].metric("Failed", summary.get("scenarios_failed", "—"))

        elif name == "performance":
            summary = data.get("summary") or {}
            end_to_end = summary.get("end_to_end") or {}
            database = (summary.get("database") or {}).get("db_roundtrip") or {}
            token_usage = summary.get("token_usage") or {}
            columns = st.columns(5)
            columns[0].metric("Runs", summary.get("runs", "—"))
            columns[1].metric(
                "P50", _fmt_seconds(end_to_end.get("p50"))
            )
            columns[2].metric(
                "P95", _fmt_seconds(end_to_end.get("p95"))
            )
            columns[3].metric(
                "Success rate",
                _fmt_artifact_score(summary.get("success_rate")),
            )
            columns[4].metric("DB P95", _fmt_seconds(database.get("p95")))
            st.caption(
                f"Token usage: {token_usage.get('status', 'NOT MEASURED')}. "
                "P95 from small samples is unstable; sample size is in the artifact."
            )

    st.caption(
        "Artifacts are separated by mode. Mocked runs exercise orchestration and "
        "metrics, not live model quality. Compare runs only with identical inputs "
        "and environment."
    )


def _render_trend_chart(config: ResearchConfig) -> None:
    series = get_evaluation_series(config, limit=100)
    if not series:
        st.info("No evaluation history yet — this chart populates after a scored run.")
        return
    df = pd.DataFrame(series).iloc[::-1]
    df["Run"] = [f"#{int(run_id)}" for run_id in df["id"]]
    columns = [
        column
        for column in ("overall", "relevance", "citation_quality", "completeness")
        if column in df.columns
    ]
    if not columns or df[columns].dropna(how="all").empty:
        st.info("Evaluation records exist, but no numeric score was measured.")
        return
    st.line_chart(df.set_index("Run")[columns], height=260)
    st.caption(
        "Line breaks indicate an unmeasured value for that run. "
        "Scores are 0–1; hover values are shown to two decimals by Streamlit."
    )


def _render_latency(
    latency: Dict[str, Optional[float]], total_latency: Optional[float]
) -> None:
    st.metric(
        "Average total execution time",
        _fmt_seconds(total_latency),
        help="Mean execution_time_seconds across runs that recorded timing.",
    )
    labels = {
        "planner_latency": "Planner",
        "research_latency": "Researcher",
        "editor_latency": "Editor",
        "search_network_latency": "Search network",
    }
    rows = [
        {"Stage": label, "Average seconds": latency.get(key)}
        for key, label in labels.items()
        if isinstance(latency.get(key), (int, float))
    ]
    if not rows:
        st.info(
            "No node-level latency recorded. Legacy rows pre-date performance "
            "tracking; new runs populate this chart automatically."
        )
        return
    df = pd.DataFrame(rows).set_index("Stage")
    st.bar_chart(df["Average seconds"], height=240)
    st.caption(
        "Node latencies are averages over runs that recorded them. The search "
        "network value is the summed DuckDuckGo round-trip time per run."
    )


def _render_table(config: ResearchConfig) -> None:
    st.markdown("**Recent evaluation records**")
    series = get_evaluation_series(config, limit=50)
    if not series:
        st.info("No evaluation records to display.")
        return

    def _dash(value):
        return round(value, 4) if isinstance(value, (int, float)) else "—"

    table = pd.DataFrame([
        {
            "Run": f"#{record['id']}",
            "Research Query": _shorten(record.get("topic")),
            "Overall": _dash(record.get("overall")),
            "Relevance": _dash(record.get("relevance")),
            "Evidence": _dash(record.get("evidence_coverage")),
            "Citation": _dash(record.get("citation_quality")),
            "Completeness": _dash(record.get("completeness")),
            "Factual support": _dash(record.get("factual_support")),
            "Generation": record.get("generation_mode") or "—",
            "Executed At (UTC)": (record.get("created_at") or "—")[:16].replace("T", " "),
        }
        for record in series
    ])
    st.dataframe(table, use_container_width=True, hide_index=True)
    st.caption(
        "“—” means the metric was not measured for that run, not that it scored zero."
    )


def _shorten(text: Optional[str], max_len: int = 55) -> str:
    if not text:
        return "—"
    return text if len(text) <= max_len else text[: max_len - 1] + "…"
