"""Reproducible performance and reliability harness.

Default mode is fully mocked: no Ollama, DuckDuckGo, or PostgreSQL connection is
made. A throwaway SQLite file is used to measure real persistence latency and to
verify round-trip reads. The LLM/search agents are replaced with deterministic
stubs so runs are bounded, repeatable, and safe for CI.

The optional ``--real`` mode executes exactly one bounded live pipeline against
the configured Ollama host and PostgreSQL database. It requires both
``--confirm-real`` and ``DATABASE_URL``, and it is never run by the default
suite. Its numbers are host/network dependent.

Usage:
    python benchmarks/performance_harness.py
    python benchmarks/performance_harness.py --output benchmarks/results/performance_harness_latest.json
    python benchmarks/performance_harness.py --real --confirm-real
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import platform
import statistics
import sys
import tempfile
import time
import tracemalloc
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from config.config import ResearchConfig
from database import database as db
from graph.workflow import run_research_pipeline
from schemas.models import ResearchPlan, ResearchReport
from tools.report_parsing import count_words
from tools.research_evidence import build_source_registry, canonical_url

logger = logging.getLogger("performance_harness")

PLAN_TEMPLATES = {
    "small": ["solar panel efficiency"],
    "medium": [
        "solar panel efficiency",
        "solar panel installation cost",
        "solar inverter reliability",
    ],
    "large": [
        "solar panel efficiency",
        "solar panel installation cost",
        "solar inverter reliability",
        "solar battery storage",
        "solar permitting requirements",
        "solar panel recycling",
    ],
}

SOURCE_POOL = [
    (
        "https://example.org/solar-efficiency",
        "Solar panel efficiency",
        "Photovoltaic panels convert sunlight into electricity; efficiency "
        "depends on cell material, temperature, and irradiance.",
    ),
    (
        "https://example.org/solar-cost",
        "Solar installation cost",
        "Installation cost includes panels, inverters, labour, permitting, and "
        "grid connection fees.",
    ),
    (
        "https://example.org/inverters",
        "Inverter reliability",
        "String inverters and microinverters differ in monitoring, redundancy, "
        "and replacement cost.",
    ),
    (
        "https://example.org/batteries",
        "Battery storage",
        "Lithium iron phosphate batteries are common in residential storage and "
        "retain capacity across many cycles.",
    ),
    (
        "https://example.org/permits",
        "Permitting",
        "Local permitting rules vary and can affect project timelines and cost.",
    ),
    (
        "https://example.org/recycling",
        "Panel recycling",
        "Recycling recovers glass, aluminium, copper, and silicon from retired "
        "modules.",
    ),
]


def _summary(query: str, sources: List[tuple], error: Optional[str] = None) -> Dict[str, Any]:
    items = []
    urls = []
    for url, title, excerpt in sources:
        urls.append(url)
        items.append({
            "url": url,
            "canonical_url": canonical_url(url),
            "title": title,
            "excerpt": excerpt,
            "retrieval_method": "fetched_page",
            "fetch_status": "ok",
            "relevance": 0.9,
        })
    return {
        "query": query,
        "source_urls": urls,
        "sources": [
            {"url": url, "title": title, "snippet": excerpt}
            for url, title, excerpt in sources
        ],
        "evidence_items": items,
        "usable_evidence_count": len(items),
        "filtered_results_count": 0,
        "fetched_pages_count": len(items),
        "fetch_failed_count": 0,
        "fetch_cache_hits": 0,
        "fetch_budget_exhausted": 0,
        "fetch_errors": [],
        "search_attempts": 0 if error else 1,
        "search_error": error,
        "search_duration": 0.05 if not error else 0.2,
    }


def _make_plan(topic: str, size: str) -> ResearchPlan:
    queries = PLAN_TEMPLATES[size]
    return ResearchPlan(
        topic=topic,
        search_queries=list(queries),
        focus_areas=["efficiency", "cost", "reliability"],
    )


def _make_summaries(size: str) -> List[Dict[str, Any]]:
    queries = PLAN_TEMPLATES[size]
    return [
        _summary(query, [SOURCE_POOL[index % len(SOURCE_POOL)]])
        for index, query in enumerate(queries)
    ]


def _make_report(plan: ResearchPlan, summaries: List[Dict[str, Any]], size: str) -> ResearchReport:
    registry = build_source_registry(summaries)
    findings = []
    for index in range(min(len(registry), 3 if size != "small" else 1)):
        record = registry[index]
        findings.append({
            "claim": record["excerpt"],
            "source_ids": [record["source_id"]],
            "citation_supported": True,
        })
    body = [f"# {plan.topic}", "", "## Introduction", "Solar research summary."]
    body += ["", "## Key Points"]
    for finding in findings:
        body.append(f"- {finding['claim']}")
    body += [
        "",
        "## Applications",
        "Residential and utility-scale generation.",
        "",
        "## Challenges",
        "Intermittency, cost, and permitting.",
        "",
        "## Conclusion",
        "The evidence supports a measured conclusion.",
        "",
        "## References",
    ]
    for record in registry:
        body.append(f"- {record['url']}")
    text = "\n".join(body)
    return ResearchReport(
        title=plan.topic,
        outline=["Introduction", "Key Points", "Applications", "Challenges", "Conclusion", "References"],
        report=text,
        sources=[record["url"] for record in registry],
        word_count=count_words(text),
        generation_mode="json",
        key_findings=findings,
        unsupported_citations=[],
        source_details=registry,
    )


def _run_mocked_pipeline(
    size: str,
    topic: str,
    config: ResearchConfig,
    *,
    search_failure: bool = False,
    llm_timeout: bool = False,
    invalid_response: bool = False,
) -> Dict[str, Any]:
    plan = _make_plan(topic, size)

    def planner(_topic, _config):
        if llm_timeout:
            raise TimeoutError("simulated_ollama_timeout")
        return plan

    def researcher(_plan, _config, on_progress=None):
        if search_failure:
            return [_summary(_plan.search_queries[0], [], error="provider_unavailable")]
        return _make_summaries(size)

    def editor(_plan, facts, _config, on_chunk_callback=None):
        if invalid_response:
            return "# Invalid JSON fallback\n\nReport generation fell back to plain text."
        return _make_report(_plan, facts, size)

    tracemalloc.start()
    started = time.perf_counter()
    error = None
    final_state: Dict[str, Any] = {}
    try:
        with patch("graph.workflow.run_triage_agent", side_effect=planner), patch(
            "graph.workflow.run_research_agent", side_effect=researcher
        ), patch("graph.workflow.run_editor_agent", side_effect=editor):
            final_state = run_research_pipeline(topic, config)
    except Exception as exc:  # expected for the LLM-timeout scenario
        error = f"{type(exc).__name__}: {exc}"
    elapsed = time.perf_counter() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    return {
        "elapsed_seconds": round(elapsed, 4),
        "memory_peak_kb": round(peak / 1024, 1),
        "error": error,
        "final_state": final_state,
    }


def _persist(
    result: Dict[str, Any],
    config: ResearchConfig,
    run_id: str,
    *,
    fail: bool = False,
) -> Dict[str, Any]:
    state = result.get("final_state") or {}
    report = state.get("report")
    started = time.perf_counter()
    outcome: Dict[str, Any] = {"expected_failure": fail}
    try:
        commit_patch = (
            patch.object(db.Session, "commit", side_effect=RuntimeError("simulated_db_unavailable"))
            if fail
            else None
        )
        if commit_patch:
            commit_patch.start()
        try:
            session_id = db.save_session(
                topic=state.get("topic") or "mocked topic",
                plan=state.get("plan"),
                facts=state.get("facts") or [],
                sources=state.get("sources") or [],
                report=report,
                status=state.get("status") or "unknown",
                config=config,
                execution_time=state.get("pipeline_latency_seconds"),
                run_id=run_id,
                planner_latency=state.get("planner_latency"),
                research_latency=state.get("research_latency"),
                editor_latency=state.get("editor_latency"),
                search_network_duration=state.get("search_network_duration"),
                grounded_sources_count=state.get("grounded_sources_count"),
                citation_grounding_ratio=state.get("citation_grounding_ratio"),
                editor_generation_mode=state.get("editor_generation_mode"),
                evaluation=state.get("evaluation"),
            )
        finally:
            if commit_patch:
                commit_patch.stop()
        outcome["ok"] = True
        outcome["session_id"] = session_id
        retrieved = db.get_session_by_id(session_id, config)
        outcome["retrieval_ok"] = retrieved is not None and retrieved.get("report") is not None
        if retrieved and retrieved.get("report"):
            stored = retrieved["report"]
            outcome["word_count_matches"] = stored["word_count"] == count_words(
                stored["report_content"]
            )
            outcome["key_findings_count"] = len(stored["key_findings"])
            outcome["source_details_count"] = len(stored["source_details"])
    except Exception as exc:
        outcome["ok"] = False
        outcome["error"] = f"{type(exc).__name__}: {exc}"
    outcome["duration_ms"] = round((time.perf_counter() - started) * 1000, 2)
    return outcome


def _result_digest(state: Dict[str, Any]) -> str:
    report = state.get("report")
    payload = {
        "topic": state.get("topic"),
        "status": state.get("status"),
        "report": getattr(report, "report", None),
        "sources": state.get("sources"),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _scenario(
    name: str,
    size: str,
    topic: str,
    config: ResearchConfig,
    *,
    run_id: str,
    search_failure: bool = False,
    llm_timeout: bool = False,
    invalid_response: bool = False,
    db_failure: bool = False,
) -> Dict[str, Any]:
    result = _run_mocked_pipeline(
        size,
        topic,
        config,
        search_failure=search_failure,
        llm_timeout=llm_timeout,
        invalid_response=invalid_response,
    )
    state = result.get("final_state") or {}
    evaluation = state.get("evaluation") or {}
    factual = (evaluation.get("metrics") or {}).get("factual_support") or {}
    record = {
        "name": name,
        "size": size,
        "status": state.get("status") or ("error" if result["error"] else "unknown"),
        "pipeline_latency_seconds": state.get("pipeline_latency_seconds"),
        "planner_latency_seconds": state.get("planner_latency"),
        "research_latency_seconds": state.get("research_latency"),
        "editor_latency_seconds": state.get("editor_latency"),
        "observed_elapsed_seconds": result["elapsed_seconds"],
        "memory_peak_kb": result["memory_peak_kb"],
        "source_count": len(state.get("sources") or []),
        "grounding_ratio": state.get("citation_grounding_ratio"),
        "evaluation_overall": evaluation.get("overall_score"),
        "factual_support_score": factual.get("score"),
        "factual_support_status": factual.get("status"),
        "pipeline_error": result["error"],
        "db": _persist(result, config, run_id, fail=db_failure) if not result["error"] else None,
    }
    if state:
        record["result_digest"] = _result_digest(state)
    return record


def run_mocked_harness(output: Optional[str] = None) -> Dict[str, Any]:
    db._engine = None
    db._SessionFactory = None
    temp_dir = tempfile.TemporaryDirectory()
    db_path = os.path.join(temp_dir.name, "performance_harness.db")
    config = ResearchConfig(database_url="sqlite:///" + db_path.replace("\\", "/"))
    db.init_database(config)

    scenarios: List[Dict[str, Any]] = []

    scenarios.append(_scenario(
        "small_query", "small", "solar panel efficiency", config,
        run_id="perf-small",
    ))
    scenarios.append(_scenario(
        "medium_query", "medium", "residential solar systems", config,
        run_id="perf-medium",
    ))
    scenarios.append(_scenario(
        "large_query", "large", "solar energy systems", config,
        run_id="perf-large",
    ))

    sequential = [
        _scenario(
            f"sequential_{index + 1}", "small", f"solar topic {index + 1}", config,
            run_id=f"perf-sequential-{index + 1}",
        )
        for index in range(3)
    ]
    scenarios.append({
        "name": "multiple_sequential_requests",
        "size": "small",
        "status": "completed",
        "observed_elapsed_seconds": round(
            sum(record["observed_elapsed_seconds"] for record in sequential), 4
        ),
        "memory_peak_kb": max(record["memory_peak_kb"] for record in sequential),
        "db_duration_ms": round(
            sum((record.get("db") or {}).get("duration_ms", 0.0) for record in sequential),
            2,
        ),
        "runs": sequential,
    })

    repeated = [
        _scenario(
            f"repeated_{index + 1}", "small", "identical solar query", config,
            run_id=f"perf-repeated-{index + 1}",
        )
        for index in range(3)
    ]
    scenarios.append({
        "name": "repeated_identical_query",
        "size": "small",
        "status": "completed",
        "observed_elapsed_seconds": round(
            sum(record["observed_elapsed_seconds"] for record in repeated), 4
        ),
        "memory_peak_kb": max(record["memory_peak_kb"] for record in repeated),
        "deterministic_result": len({record.get("result_digest") for record in repeated}) == 1,
        "db_duration_ms": round(
            sum((record.get("db") or {}).get("duration_ms", 0.0) for record in repeated),
            2,
        ),
        "runs": repeated,
    })

    scenarios.append(_scenario(
        "external_search_failure", "small", "solar search failure", config,
        run_id="perf-search-failure", search_failure=True,
    ))
    scenarios.append(_scenario(
        "llm_timeout", "small", "solar llm timeout", config,
        run_id="perf-llm-timeout", llm_timeout=True,
    ))
    scenarios.append(_scenario(
        "invalid_llm_response_fallback", "small", "solar invalid response", config,
        run_id="perf-invalid-response", invalid_response=True,
    ))
    scenarios.append(_scenario(
        "database_persistence_failure", "small", "solar db failure", config,
        run_id="perf-db-failure", db_failure=True,
    ))

    session = db.get_session(config)
    try:
        session_rows = session.query(db.ResearchSession).count()
        report_rows = session.query(db.ResearchReportRow).count()
    finally:
        session.close()
    if db._engine is not None:
        db._engine.dispose()
    db._engine = None
    db._SessionFactory = None
    temp_dir.cleanup()

    expected_failures = [
        record for record in scenarios
        if record["name"] in {"llm_timeout", "database_persistence_failure"}
    ]

    # Aggregate nested sequential/repeated runs so write counts match row counts.
    leaf_runs: List[Dict[str, Any]] = []
    for scenario in scenarios:
        leaf_runs.extend(scenario.get("runs") or [scenario])
    leaf_completed = [
        record for record in leaf_runs
        if record.get("status") in {"complete", "complete_unstructured"}
    ]
    leaf_db_ok = sum(1 for record in leaf_runs if (record.get("db") or {}).get("ok"))
    leaf_db_failed = sum(
        1 for record in leaf_runs if record.get("db") and record["db"].get("ok") is False
    )
    leaf_unexpected = sum(
        1 for record in leaf_runs
        if record.get("pipeline_error") and record.get("name") != "llm_timeout"
    )

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "mocked",
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "note": (
                "LLM and search agents are deterministic stubs; persistence uses a "
                "temporary SQLite database. Latencies measure orchestration, local "
                "evaluation, and SQLite persistence only, not Ollama or PostgreSQL."
            ),
        },
        "scenarios": scenarios,
        "summary": {
            "scenario_count": len(scenarios),
            "leaf_pipeline_runs": len(leaf_runs),
            "completed_pipeline_runs": len(leaf_completed),
            "expected_failures_observed": sum(
                1 for record in expected_failures
                if record.get("pipeline_error") or (record.get("db") or {}).get("error")
            ),
            "unexpected_failures": leaf_unexpected,
            "successful_db_writes": leaf_db_ok,
            "failed_db_writes": leaf_db_failed,
            "rows_after_run": {"research_sessions": session_rows, "research_reports": report_rows},
        },
    }

    if output:
        target = os.path.abspath(output)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2)
        report["output_path"] = target
    return report


def run_real_harness() -> Dict[str, Any]:
    """One bounded live run. Requires Ollama and PostgreSQL; opt-in only."""
    config = ResearchConfig()
    if not config.database_url:
        raise RuntimeError("DATABASE_URL is required for --real mode")
    if not os.environ.get("PERF_ALLOW_REAL"):
        raise RuntimeError("Set PERF_ALLOW_REAL=1 to confirm a live external run")

    from generation.llm import check_ollama_connection

    if not check_ollama_connection(config):
        raise RuntimeError("Ollama is not reachable")

    db._engine = None
    db._SessionFactory = None
    db.init_database(config)

    tracemalloc.start()
    started = time.perf_counter()
    error = None
    state: Dict[str, Any] = {}
    try:
        state = run_research_pipeline(
            "What are the main efficiency limits of silicon solar panels?", config
        )
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    elapsed = time.perf_counter() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    persisted = None
    if state and not error:
        persisted = _persist(
            {"final_state": state}, config,
            run_id="perf-real-" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S"),
        )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "real_single_run",
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "note": (
                "One bounded live pipeline run. Search, Ollama generation, and "
                "PostgreSQL latency depend on host, model, and network conditions."
            ),
        },
        "pipeline_latency_seconds": state.get("pipeline_latency_seconds"),
        "planner_latency_seconds": state.get("planner_latency"),
        "research_latency_seconds": state.get("research_latency"),
        "editor_latency_seconds": state.get("editor_latency"),
        "observed_elapsed_seconds": round(elapsed, 3),
        "memory_peak_kb": round(peak / 1024, 1),
        "source_count": len(state.get("sources") or []),
        "grounding_ratio": state.get("citation_grounding_ratio"),
        "evaluation_overall": (state.get("evaluation") or {}).get("overall_score"),
        "factual_support": ((state.get("evaluation") or {}).get("metrics") or {}).get(
            "factual_support"
        ),
        "error": error,
        "db": persisted,
        "token_usage": "not exposed by the current generation client",
    }


def _print_summary(report: Dict[str, Any]) -> None:
    print(json.dumps(report["summary"], indent=2))
    for scenario in report["scenarios"]:
        print(
            f"- {scenario['name']}: status={scenario.get('status')} "
            f"elapsed={scenario.get('observed_elapsed_seconds')}s "
            f"error={scenario.get('pipeline_error') or scenario.get('error') or 'none'}"
        )
    if report.get("output_path"):
        print(f"written: {report['output_path']}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="benchmarks/results/performance_harness_latest.json")
    parser.add_argument("--real", action="store_true", help="one bounded live run")
    parser.add_argument("--confirm-real", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)
    if args.real:
        if not args.confirm_real:
            print("Refusing --real without --confirm-real", file=sys.stderr)
            return 2
        report = run_real_harness()
    else:
        report = run_mocked_harness(output=args.output)
    _print_summary(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
