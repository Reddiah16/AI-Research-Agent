#!/usr/bin/env python3
"""Extended performance benchmark harness with P50/P95 and concurrency.

Default mode is mocked: deterministic agent stubs plus a temporary SQLite
database. It measures real orchestration, evaluation, and persistence latency,
but not Ollama or DuckDuckGo. ``--live`` runs the actual pipeline with Ollama,
DuckDuckGo, and the configured database; it requires ``--confirm-live``.

No latency is simulated. Missing token usage remains NOT MEASURED.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import platform
import statistics
import sys
import tempfile
import time
import tracemalloc
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from benchmarks.performance_harness import (  # noqa: E402
    PLAN_TEMPLATES,
    _make_plan,
    _make_report,
    _make_summaries,
    _persist,
)
from config.config import ResearchConfig  # noqa: E402
from database import database as db  # noqa: E402
from evaluation.performance import (  # noqa: E402
    LatencyRecorder,
    compare_benchmark_reports,
    machine_metadata,
    success_rate,
    summarise_latencies,
)
from generation.usage import get_usage, reset_usage  # noqa: E402
from graph.workflow import run_research_pipeline  # noqa: E402

RESULTS_DIR = PROJECT_ROOT / "benchmarks" / "results"
LATEST = RESULTS_DIR / "performance_benchmark_latest.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _write_json(path: Path, payload: Dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    return path


def _run_once(
    size: str,
    topic: str,
    config: ResearchConfig,
    *,
    search_failure: bool = False,
    llm_timeout: bool = False,
    invalid_response: bool = False,
    measure_memory: bool = True,
) -> Dict[str, Any]:
    plan = _make_plan(topic, size)

    def planner(_topic, _config):
        if llm_timeout:
            raise TimeoutError("simulated_ollama_timeout")
        return plan

    def researcher(_plan, _config, on_progress=None):
        if search_failure:
            return [{
                "query": _plan.search_queries[0],
                "sources": [],
                "source_urls": [],
                "evidence_items": [],
                "usable_evidence_count": 0,
                "filtered_results_count": 0,
                "fetched_pages_count": 0,
                "fetch_failed_count": 0,
                "fetch_cache_hits": 0,
                "fetch_budget_exhausted": 0,
                "fetch_errors": [],
                "search_attempts": 3,
                "search_error": "provider_unavailable",
                "search_duration": 0.2,
            }]
        return _make_summaries(size)

    def editor(_plan, facts, _config, on_chunk_callback=None):
        if invalid_response:
            return "# Invalid JSON fallback\n\nReport generation fell back to plain text."
        return _make_report(_plan, facts, size)

    memory_peak_kb = None
    if measure_memory:
        tracemalloc.start()
    started = time.perf_counter()
    error = None
    state: Dict[str, Any] = {}
    try:
        with patch("graph.workflow.run_triage_agent", side_effect=planner), patch(
            "graph.workflow.run_research_agent", side_effect=researcher
        ), patch("graph.workflow.run_editor_agent", side_effect=editor):
            state = run_research_pipeline(topic, config)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    elapsed = time.perf_counter() - started
    if measure_memory:
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        memory_peak_kb = round(peak / 1024, 1)

    return {
        "size": size,
        "topic": topic,
        "elapsed_seconds": round(elapsed, 6),
        "error": error,
        "status": state.get("status") if state else "error",
        "planner_latency_seconds": (state or {}).get("planner_latency"),
        "research_latency_seconds": (state or {}).get("research_latency"),
        "editor_latency_seconds": (state or {}).get("editor_latency"),
        "search_latency_seconds": (state or {}).get("search_network_duration"),
        "retry_count": max(
            0,
            int(
                ((state or {}).get("research_metrics") or {}).get("search_attempts") or 0
            )
            - len(
                getattr((state or {}).get("plan"), "search_queries", None) or []
            ),
        ),
        "source_count": len((state or {}).get("sources") or []),
        "memory_peak_kb": memory_peak_kb,
        "state": state,
    }


def _summarise_runs(runs: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    elapsed = [run["elapsed_seconds"] for run in runs]
    successful = [run for run in runs if run.get("error") is None]
    failed = [run for run in runs if run.get("error") is not None]
    planner = [run["planner_latency_seconds"] for run in successful if run.get("planner_latency_seconds") is not None]
    research = [run["research_latency_seconds"] for run in successful if run.get("research_latency_seconds") is not None]
    editor = [run["editor_latency_seconds"] for run in successful if run.get("editor_latency_seconds") is not None]
    search = [run["search_latency_seconds"] for run in successful if run.get("search_latency_seconds") is not None]
    memory = [run["memory_peak_kb"] for run in runs if run.get("memory_peak_kb") is not None]
    return {
        "runs": len(runs),
        "successful_completions": len(successful),
        "failed_executions": len(failed),
        "success_rate": success_rate(len(successful), len(runs)),
        "retry_count": sum(int(run.get("retry_count") or 0) for run in runs),
        "end_to_end": summarise_latencies(elapsed),
        "successful_end_to_end": summarise_latencies([run["elapsed_seconds"] for run in successful]),
        "stages": {
            "planner": summarise_latencies(planner),
            "research": summarise_latencies(research),
            "editor": summarise_latencies(editor),
            "search_network": summarise_latencies(search),
        },
        "memory_peak_kb": {
            "max": max(memory) if memory else None,
            "mean": round(statistics.mean(memory), 1) if memory else None,
            "samples": len(memory),
        },
        "errors": [run["error"] for run in failed],
    }


def run_mocked(
    *,
    repetitions: int,
    warmup: int,
    concurrency: int,
    include_failure_scenarios: bool = True,
    questions: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    db._engine = None
    db._SessionFactory = None
    temp_dir = tempfile.TemporaryDirectory()
    try:
        db_path = os.path.join(temp_dir.name, "performance_benchmark.db")
        config = ResearchConfig(database_url="sqlite:///" + db_path.replace("\\", "/"))
        db.init_database(config)
        reset_usage()

        sizes = ("small", "medium", "large")
        topics = {
            "small": "solar panel efficiency",
            "medium": "residential solar systems",
            "large": "solar energy systems",
        }
        custom_questions = [value for value in (questions or []) if value.strip()]

        def topic_for(size: str, index: int) -> str:
            if not custom_questions:
                return topics[size]
            size_index = sizes.index(size)
            return custom_questions[(size_index + index) % len(custom_questions)]

        # Warm-up is deliberately excluded from recorded samples.
        for index in range(max(0, warmup)):
            for size in sizes:
                _run_once(
                    size,
                    topic_for(size, index) + " warmup",
                    config,
                    measure_memory=False,
                )

        all_runs: List[Dict[str, Any]] = []
        per_size: Dict[str, List[Dict[str, Any]]] = {size: [] for size in sizes}
        jobs = [(size, index) for size in sizes for index in range(max(1, repetitions))]

        if concurrency > 1:
            with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
                futures = [
                    pool.submit(
                        _run_once,
                        size,
                        topic_for(size, index),
                        config,
                        measure_memory=False,
                    )
                    for size, index in jobs
                ]
                for future in concurrent.futures.as_completed(futures):
                    run = future.result()
                    all_runs.append(run)
                    per_size[run["size"]].append(run)
        else:
            for size, index in jobs:
                run = _run_once(
                    size,
                    topic_for(size, index),
                    config,
                    measure_memory=True,
                )
                all_runs.append(run)
                per_size[size].append(run)

        # Measure real SQLite persistence and read-back latency for successful runs.
        db_recorder = LatencyRecorder()
        persisted = 0
        for index, run in enumerate(all_runs, start=1):
            if run.get("error") is not None or not run.get("state"):
                continue
            outcome = _persist(run, config, run_id=f"perf-{index}")
            db_recorder.record("db_roundtrip", outcome.get("duration_ms", 0) / 1000.0)
            if outcome.get("ok"):
                persisted += 1

        if include_failure_scenarios:
            failures = [
                _run_once("small", "search failure", config, search_failure=True, measure_memory=False),
                _run_once("small", "llm timeout", config, llm_timeout=True, measure_memory=False),
                _run_once("small", "invalid response", config, invalid_response=True, measure_memory=False),
            ]
        else:
            failures = []

        summary = _summarise_runs(all_runs)
        summary["by_size"] = {
            size: _summarise_runs(runs) for size, runs in per_size.items() if runs
        }
        summary["database"] = db_recorder.summary()
        summary["database"]["successful_writes"] = persisted
        summary["database"]["attempted_writes"] = len(all_runs)
        summary["failure_scenarios"] = [
            {
                "name": run["topic"],
                "error": run["error"],
                "status": run["status"],
                "elapsed_seconds": run["elapsed_seconds"],
            }
            for run in failures
        ]
        usage = get_usage()
        summary["token_usage"] = {
            **usage,
            "status": "not_available_mocked" if usage["calls"] == 0 else "measured",
            "note": (
                "Mocked agent stubs do not call Ollama, so token usage is not "
                "applicable. Live mode reports provider counts when available."
            ),
        }

        report = {
            "generated_at": _now(),
            "mode": "mocked",
            "environment": machine_metadata(
                llm_model=config.llm_model,
                ollama_host=config.ollama_host,
                search_provider=config.search_provider,
                extra={
                    "note": (
                        "Deterministic stubs replace planner/researcher/editor. "
                        "Latency covers orchestration, local evaluation, and "
                        "temporary SQLite persistence only."
                    ),
                    "concurrency": concurrency,
                    "repetitions": repetitions,
                    "warmup": warmup,
                    "questions": custom_questions
                    or [topics[size] for size in sizes],
                },
            ),
            "summary": summary,
            "limitations": [
                "Mocked mode does not measure Ollama generation or live web-search latency.",
                "P95 from small samples is unstable; sample size is reported with every metric.",
                "SQLite persistence is not PostgreSQL performance.",
                "No baseline-versus-optimized claim is made from mocked runs.",
            ],
        }
        timestamped = RESULTS_DIR / f"performance_benchmark_mocked_{_stamp()}.json"
        _write_json(timestamped, report)
        _write_json(LATEST, report)
        report["output_path"] = str(timestamped)
        return report
    finally:
        if db._engine is not None:
            db._engine.dispose()
        db._engine = None
        db._SessionFactory = None
        temp_dir.cleanup()


def run_live(
    *, repetitions: int, warmup: int, question: Optional[str] = None
) -> Dict[str, Any]:
    config = ResearchConfig()
    if not config.database_url:
        raise RuntimeError("DATABASE_URL is required for live performance mode")
    from generation.llm import check_ollama_connection

    if not check_ollama_connection(config):
        raise RuntimeError("Ollama is not reachable")

    db._engine = None
    db._SessionFactory = None
    db.init_database(config)
    reset_usage()

    question = question or "What are the main efficiency limits of silicon solar panels?"
    for _ in range(max(0, warmup)):
        run_research_pipeline(question, config)

    runs: List[Dict[str, Any]] = []
    for index in range(max(1, repetitions)):
        started = time.perf_counter()
        error = None
        state: Dict[str, Any] = {}
        try:
            state = run_research_pipeline(question, config)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        elapsed = time.perf_counter() - started
        runs.append({
            "size": "live",
            "topic": question,
            "elapsed_seconds": round(elapsed, 6),
            "error": error,
            "status": state.get("status") if state else "error",
            "planner_latency_seconds": (state or {}).get("planner_latency"),
            "research_latency_seconds": (state or {}).get("research_latency"),
            "editor_latency_seconds": (state or {}).get("editor_latency"),
            "search_latency_seconds": (state or {}).get("search_network_duration"),
            "retry_count": max(
                0,
                int(((state or {}).get("research_metrics") or {}).get("search_attempts") or 0)
                - len(getattr((state or {}).get("plan"), "search_queries", None) or []),
            ),
            "source_count": len((state or {}).get("sources") or []),
            "memory_peak_kb": None,
            "state": state,
        })
        print(
            f"[{index + 1}/{repetitions}] status={runs[-1]['status']} "
            f"elapsed={elapsed:.1f}s error={error or 'none'}"
        )

    summary = _summarise_runs(runs)
    summary["token_usage"] = {
        **get_usage(),
        "status": "measured" if get_usage()["calls_with_usage"] > 0 else "not_available",
    }
    report = {
        "generated_at": _now(),
        "mode": "live",
        "environment": machine_metadata(
            llm_model=config.llm_model,
            ollama_host=config.ollama_host,
            search_provider=config.search_provider,
            extra={
                "note": "Live Ollama + DuckDuckGo + PostgreSQL run.",
                "repetitions": repetitions,
                "warmup": warmup,
                "question": question,
            },
        ),
        "summary": summary,
        "limitations": [
            "Live latency depends on model, host load, and DuckDuckGo availability.",
            "A single-question sample is not a representative workload.",
            "PostgreSQL persistence latency is included in the end-to-end timing.",
        ],
    }
    timestamped = RESULTS_DIR / f"performance_benchmark_live_{_stamp()}.json"
    _write_json(timestamped, report)
    _write_json(LATEST, report)
    report["output_path"] = str(timestamped)
    return report


def compare_files(baseline_path: str, candidate_path: str) -> Dict[str, Any]:
    with open(baseline_path, "r", encoding="utf-8") as handle:
        baseline = json.load(handle)
    with open(candidate_path, "r", encoding="utf-8") as handle:
        candidate = json.load(handle)
    return compare_benchmark_reports(
        baseline,
        candidate,
        metric_paths=(
            ("summary", "end_to_end", "p50"),
            ("summary", "end_to_end", "p95"),
            ("summary", "success_rate"),
            ("summary", "stages", "editor", "p50"),
            ("summary", "stages", "research", "p50"),
        ),
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("mocked", "live"), default="mocked")
    parser.add_argument("--confirm-live", action="store_true")
    parser.add_argument("--repetitions", type=int, default=8)
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument(
        "--question",
        action="append",
        help="Question to benchmark; repeat for multiple questions",
    )
    parser.add_argument("--baseline", help="Baseline JSON artifact to compare")
    parser.add_argument("--candidate", help="Candidate JSON artifact to compare")
    args = parser.parse_args(argv)

    if args.baseline and args.candidate:
        comparison = compare_files(args.baseline, args.candidate)
        print(json.dumps(comparison, indent=2))
        return 0

    if args.mode == "live":
        if not args.confirm_live:
            print("Refusing --mode live without --confirm-live", file=sys.stderr)
            return 2
        report = run_live(
            repetitions=args.repetitions,
            warmup=args.warmup,
            question=(args.question[0] if args.question else None),
        )
    else:
        report = run_mocked(
            repetitions=args.repetitions,
            warmup=args.warmup,
            concurrency=args.concurrency,
            questions=args.question,
        )

    summary = report["summary"]
    print(json.dumps({
        "mode": report["mode"],
        "runs": summary["runs"],
        "success_rate": summary["success_rate"],
        "end_to_end": summary["end_to_end"],
        "stages": summary["stages"],
        "database": summary.get("database"),
        "token_usage": summary.get("token_usage"),
        "output_path": report.get("output_path"),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
