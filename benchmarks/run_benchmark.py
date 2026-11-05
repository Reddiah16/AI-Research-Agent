"""Repeatable performance benchmark for the AI Research Agent workflow.

Runs real research queries through the actual LangGraph pipeline
(planner → researcher → editor), with Ollama generation and live
DuckDuckGo search, and persists every execution through the same
database functions the Streamlit app uses (save_session with run_id
idempotency, SUCCESS/FAILED normalization and execution timing).

Nothing is fabricated: the CSV is written from actual run results and
the matching rows already exist in PostgreSQL because the real
application workflow created them.

Usage:
    python benchmarks/run_benchmark.py            # all 20 queries
    python benchmarks/run_benchmark.py --limit 3  # quick smoke run
    python benchmarks/run_benchmark.py --skip-existing

Output:
    benchmarks/results/benchmark_YYYYMMDD_HHMMSS.csv
    A console summary of aggregate metrics.
"""

import argparse
import csv
import logging
import os
import platform
import sys
import time
import uuid
from datetime import datetime, timezone

# Project root on sys.path (benchmarks/ lives one level below it)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

# Windows console safety for emoji output
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

from config.config import ResearchConfig  # noqa: E402
from database.database import init_database, save_session  # noqa: E402
from generation.llm import check_ollama_connection  # noqa: E402
from graph.workflow import run_research_pipeline  # noqa: E402
from schemas.models import ResearchReport  # noqa: E402

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("benchmark")

# 20 research queries across diverse topic domains.
BENCHMARK_QUERIES = [
    "Artificial intelligence trends in 2026",
    "Cloud computing market outlook",
    "Electric vehicle industry growth",
    "Cybersecurity developments for enterprises",
    "Generative AI applications in software development",
    "Startup ecosystem funding trends",
    "Data engineering trends and modern pipelines",
    "Enterprise automation with AI agents",
    "Quantum computing hardware progress",
    "Renewable energy storage technologies",
    "Semiconductor supply chain challenges",
    "Remote work impact on urban real estate",
    "AI regulation in Europe and the United States",
    "Healthcare applications of large language models",
    "Autonomous driving technology adoption",
    "Open source AI models versus proprietary models",
    "Edge computing for Internet of Things",
    "Digital payments growth in emerging markets",
    "Robotics in warehouse logistics",
    "Climate tech investment opportunities",
]

# Pause between runs to reduce DuckDuckGo rate-limiting pressure.
INTER_RUN_DELAY_SECONDS = 2.0


def _env_info(config: ResearchConfig) -> dict:
    """Collect environment metadata (never includes credentials)."""
    return {
        "python_version": platform.python_version(),
        "platform": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "cpu_count": os.cpu_count(),
        "llm_model": config.llm_model,
        "ollama_host": config.ollama_host,
        "database": "PostgreSQL @ localhost:5432/ai_research_agent",
        "search_provider": config.search_provider,
        "max_results_per_query": config.max_results,
    }


def run_benchmark(limit: int, skip_existing: bool) -> str:
    """Execute the benchmark and write the results CSV. Returns CSV path."""
    config = ResearchConfig()

    # --- Preflight: real infrastructure must be available ---
    if not check_ollama_connection(config):
        print("❌ Ollama is not reachable — benchmark cannot run against "
              "the real workflow. Start Ollama and retry.")
        sys.exit(2)

    init_database(config)

    queries = BENCHMARK_QUERIES[:limit]
    results = []
    t_start = time.time()

    print(f"🏁 Benchmark start: {len(queries)} queries, model={config.llm_model}, "
          f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n")

    for i, topic in enumerate(queries, 1):
        run_id = str(uuid.uuid4())

        if skip_existing:
            from database.database import get_session as _get_session
            from database.database import ResearchSession as _RS
            _s = _get_session(config)
            try:
                if _s.query(_RS).filter_by(topic=topic).count() > 0:
                    print(f"  [{i:02d}/{len(queries)}] SKIP (already benched): {topic[:60]}")
                    continue
            finally:
                _s.close()

        # --- Exact app.py execution path: time → pipeline → classify → save ---
        start = time.perf_counter()
        error_text = None
        try:
            final_state = run_research_pipeline(topic, config)
            elapsed = time.perf_counter() - start

            report = final_state.get("report")
            is_success = (
                final_state.get("status") == "complete"
                and isinstance(report, ResearchReport)
            )
            status = "SUCCESS" if is_success else "FAILED"
            unique_sources = len(final_state.get("sources", []))

            session_id = save_session(
                topic=topic,
                plan=final_state.get("plan"),
                facts=final_state.get("facts", []),
                sources=final_state.get("sources", []),
                report=report if is_success else None,
                status=status,
                config=config,
                execution_time=elapsed,
                run_id=run_id,
            )
            report_persisted = is_success
        except Exception as e:
            elapsed = time.perf_counter() - start
            status = "FAILED"
            unique_sources = 0
            report_persisted = False
            error_text = f"{type(e).__name__}: {e}"
            try:
                session_id = save_session(
                    topic=topic, plan=None, facts=[], sources=[],
                    report=None, status="FAILED", config=config,
                    execution_time=elapsed, run_id=run_id,
                    error_message=error_text,
                )
            except Exception as db_error:
                session_id = None
                error_text += f" | persistence also failed: {db_error}"

        results.append({
            "query": topic,
            "run_id": run_id,
            "session_id": session_id,
            "status": status,
            "execution_time_seconds": round(elapsed, 2),
            "unique_sources": unique_sources,
            "report_generated": status == "SUCCESS",
            "report_persisted": report_persisted,
            "error_message": (error_text or "")[:200],
            "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })
        marker = "✅" if status == "SUCCESS" else "❌"
        print(f"  [{i:02d}/{len(queries)}] {marker} {status} "
              f"{elapsed:7.2f}s  sources={unique_sources:2d}  {topic[:55]}")

        if i < len(queries):
            time.sleep(INTER_RUN_DELAY_SECONDS)

    wall = time.time() - t_start

    # --- Write CSV beside environment metadata ---
    out_dir = os.path.join(PROJECT_ROOT, "benchmarks", "results")
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    csv_path = os.path.join(out_dir, f"benchmark_{stamp}.csv")

    fields = list(results[0].keys()) if results else [
        "query", "run_id", "session_id", "status", "execution_time_seconds",
        "unique_sources", "report_generated", "report_persisted",
        "error_message", "timestamp_utc",
    ]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(results)

    # --- Aggregate summary (this run only) ---
    n = len(results)
    succ = [r for r in results if r["status"] == "SUCCESS"]
    fail = [r for r in results if r["status"] == "FAILED"]
    timed = [r for r in results if r["execution_time_seconds"] is not None]
    print("\n" + "=" * 60)
    print("BENCHMARK SUMMARY (this run)")
    print("=" * 60)
    print(f"Executions       : {n}")
    print(f"Successful       : {len(succ)}")
    print(f"Failed           : {len(fail)}")
    print(f"Success rate     : {len(succ) / n * 100 if n else 0:.1f}%")
    if timed:
        print(f"Avg execution time: {sum(r['execution_time_seconds'] for r in timed) / len(timed):.2f}s "
              f"(ALL runs incl. failed, n={len(timed)})")
    if succ:
        print(f"Avg sources (successful runs): {sum(r['unique_sources'] for r in succ) / len(succ):.2f}")
        print(f"Avg execution time (successful only): "
              f"{sum(r['execution_time_seconds'] for r in succ) / len(succ):.2f}s")
    print(f"Reports persisted: {sum(1 for r in results if r['report_persisted'])} / {n}")
    print(f"Wall-clock time  : {wall / 60:.1f} minutes")
    print(f"Environment      : {_env_info(config)}")
    print(f"\n📄 CSV written: {csv_path}")
    return csv_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI Research Agent benchmark")
    parser.add_argument("--limit", type=int, default=len(BENCHMARK_QUERIES),
                        help="Number of queries to run (default: all 20)")
    parser.add_argument("--skip-existing", action="store_true",
                        help="Skip topics already present in research_sessions")
    args = parser.parse_args()
    run_benchmark(limit=args.limit, skip_existing=args.skip_existing)
