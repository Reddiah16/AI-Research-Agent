"""Standalone tests for Phase 3 PostgreSQL metrics persistence.

Verifies:
  1. Successful research metrics are persisted (incl. report_id, run_id)
  2. Failed research metrics are persisted with a sanitized error message
  3. Duplicate execution records are prevented (run_id idempotency key)
  4. Metrics are retrievable after application restart (fresh engine)
  5. Average execution time is calculated correctly
  6. Successful report counts are accurate (failed runs never count)
  7. Existing research history remains accessible (legacy rows preserved)

Uses a temporary SQLite file so no PostgreSQL instance is required —
the same SQLAlchemy code path runs against both backends.

Run:  python tests/test_metrics_persistence.py
"""

import os
import sys
import tempfile

# Fix Windows console encoding for emoji output
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, inspect, text  # noqa: E402

import database.database as db  # noqa: E402
from config.config import ResearchConfig  # noqa: E402
from schemas.models import ResearchPlan, ResearchReport  # noqa: E402


def _make_config() -> ResearchConfig:
    """Create a config pointing at a fresh temporary SQLite database."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return ResearchConfig(database_url=f"sqlite:///{path}")


def _reset_engine_globals() -> None:
    """Reset the module-level engine/sessionmaker singletons between tests."""
    db._engine = None
    db._SessionFactory = None


def _report() -> ResearchReport:
    return ResearchReport(
        title="Good Report",
        outline=["Introduction"],
        report="# Good Report\n\nbody text here",
        sources=["https://one.com"],
        word_count=4,
    )


def _facts_with_sources(*urls: str) -> list:
    return [{
        "query": "q1",
        "summary": "s",
        "sources": [{"url": u, "title": u, "snippet": ""} for u in urls],
        "source_urls": list(urls),
    }]


def test_success_metrics_persisted():
    """1: SUCCESS run stores timing, status, unique sources and report ref."""
    print("=" * 60)
    print("TEST 1: Successful execution metrics persisted")
    print("=" * 60)

    _reset_engine_globals()
    config = _make_config()
    db.init_database(config)

    sid = db.save_session(
        topic="quantum computing",
        plan=ResearchPlan(topic="t", search_queries=["q"], focus_areas=["f"]),
        facts=_facts_with_sources("https://one.com", "https://two.com"),
        sources=["https://one.com", "https://two.com"],
        report=_report(),
        status="SUCCESS",
        config=config,
        execution_time=30.5,
        run_id="run-success-1",
    )

    metrics = db.get_execution_metrics(config)
    assert len(metrics) == 1
    m = metrics[0]
    assert m["id"] == sid
    assert m["run_id"] == "run-success-1"
    assert m["topic"] == "quantum computing"
    assert m["status"] == "SUCCESS"
    assert m["execution_time_seconds"] == 30.5
    assert m["source_count"] == 2, "Unique source count must be 2"
    assert m["report_id"] is not None, "Report reference must be exposed"
    assert m["created_at"] is not None
    assert m["error_message"] is None

    print(f"✅ id={m['id']} status={m['status']} time={m['execution_time_seconds']}s "
          f"sources={m['source_count']} report_id={m['report_id']}")
    print()


def test_failed_metrics_persisted_with_sanitized_error():
    """2: FAILED run stores timing + error message with credentials masked."""
    print("=" * 60)
    print("TEST 2: Failed execution metrics persisted, error sanitized")
    print("=" * 60)

    _reset_engine_globals()
    config = _make_config()
    db.init_database(config)

    # Simulate an exception whose text embeds a full DSN (common with
    # psycopg2/SQLAlchemy errors) — must never reach storage verbatim.
    # Placeholder credentials only; never real ones in test fixtures.
    raw_error = (
        "OperationalError: could not connect to "
        "postgresql://dbuser:dbpass@localhost:5432/evil_db "
        + "x" * 600  # overlong message must be truncated
    )

    db.save_session(
        topic="doomed topic",
        plan=None,
        facts=[],
        sources=[],
        report=None,
        status="FAILED",
        config=config,
        execution_time=4.2,
        run_id="run-fail-1",
        error_message=raw_error,
    )

    m = db.get_execution_metrics(config)[0]
    assert m["status"] == "FAILED"
    assert m["execution_time_seconds"] == 4.2
    assert m["report_id"] is None, "Failed run must not reference a report"
    assert m["error_message"] is not None
    assert "dbuser:dbpass@" not in m["error_message"], (
        "Credentials leaked into stored error message!"
    )
    assert "***" in m["error_message"], "Credential block should be masked"
    assert len(m["error_message"]) <= 515, "Message should be truncated"

    print(f"✅ FAILED run stored; error masked ({len(m['error_message'])} chars) "
          f"and report_id=None")
    print()


def test_duplicate_run_id_prevented():
    """3: Saving the same run_id twice creates exactly one record."""
    print("=" * 60)
    print("TEST 3: Duplicate execution records prevented")
    print("=" * 60)

    _reset_engine_globals()
    config = _make_config()
    db.init_database(config)

    kwargs = dict(
        topic="once only",
        plan=None,
        facts=_facts_with_sources("https://a.com"),
        sources=["https://a.com"],
        report=None,
        status="SUCCESS",
        config=config,
        execution_time=1.0,
        run_id="run-dup-1",
    )
    first_id = db.save_session(**kwargs)
    second_id = db.save_session(**kwargs)  # accidental re-record attempt

    assert first_id == second_id, "Idempotent save must return the existing ID"
    metrics = db.get_execution_metrics(config)
    assert len(metrics) == 1, f"Expected 1 record, got {len(metrics)}"
    assert db.get_total_reports(config) == 0

    print(f"✅ Duplicate ignored: both calls returned id={first_id}, 1 row stored")
    print()


def test_metrics_retrievable_after_restart():
    """4: After resetting engine/session singletons, metrics still readable."""
    print("=" * 60)
    print("TEST 4: Metrics survive application restart")
    print("=" * 60)

    _reset_engine_globals()
    config = _make_config()
    db.init_database(config)

    db.save_session(
        topic="persisted run",
        plan=None,
        facts=[],
        sources=[],
        report=_report(),
        status="SUCCESS",
        config=config,
        execution_time=12.0,
        run_id="run-restart-1",
    )

    # Simulate app restart: drop engine + sessionmaker, reopen same DB file
    _reset_engine_globals()
    metrics = db.get_execution_metrics(config)
    assert len(metrics) == 1
    assert metrics[0]["run_id"] == "run-restart-1"
    assert metrics[0]["execution_time_seconds"] == 12.0

    # A duplicate save after "restart" is still blocked by the DB constraint
    again = db.save_session(
        topic="persisted run",
        plan=None,
        facts=[],
        sources=[],
        report=None,
        status="SUCCESS",
        config=config,
        execution_time=99.0,
        run_id="run-restart-1",
    )
    assert again == metrics[0]["id"], "Idempotency must hold across restarts"
    assert len(db.get_execution_metrics(config)) == 1

    print("✅ Metrics readable and dedup still enforced after restart")
    print()


def test_averages_and_counts_correct():
    """5+6: average execution time, source avg, success/fail counts exact."""
    print("=" * 60)
    print("TEST 5: Aggregation math (averages, counts, report total)")
    print("=" * 60)

    _reset_engine_globals()
    config = _make_config()
    db.init_database(config)

    # Seed: 2 SUCCESS (10.0s / 20.0s), 1 FAILED (30.0s)
    db.save_session(topic="a", plan=None, facts=_facts_with_sources(
        "https://x1.com", "https://x2.com"), sources=[], report=_report(),
        status="SUCCESS", config=config, execution_time=10.0, run_id="agg-1")
    db.save_session(topic="b", plan=None, facts=_facts_with_sources(
        "https://x3.com"), sources=[], report=_report(),
        status="SUCCESS", config=config, execution_time=20.0, run_id="agg-2")
    db.save_session(topic="c", plan=None, facts=[], sources=[], report=None,
                    status="FAILED", config=config, execution_time=30.0,
                    run_id="agg-3", error_message="boom")

    avg = db.get_average_execution_time(config)
    assert avg == 20.0, f"Expected (10+20+30)/3=20.0, got {avg}"

    avg_src = db.get_average_source_count(config)
    # unique sources: 2 + 1 + 0 across 3 sessions
    assert avg_src == 1.0, f"Expected 3/3=1.0, got {avg_src}"

    assert db.get_success_count(config) == 2
    assert db.get_failed_count(config) == 1
    assert db.get_total_reports(config) == 2, "Only successful runs persisted reports"

    print(f"✅ avg_time={avg}s avg_sources={avg_src} "
          f"success={db.get_success_count(config)} failed={db.get_failed_count(config)} "
          f"reports={db.get_total_reports(config)}")
    print()


def test_legacy_status_and_history_compatibility():
    """7: Pre-Phase-2 rows ('complete'/'error') count correctly and stay
    accessible through both the history and metrics APIs."""
    print("=" * 60)
    print("TEST 6: Legacy history preserved and normalized")
    print("=" * 60)

    _reset_engine_globals()
    config = _make_config()
    db.init_database(config)

    # Legacy-style rows: old vocabulary, no run_id / no timing
    db.save_session(topic="legacy ok", plan=None, facts=[], sources=[],
                    report=_report(), status="complete", config=config)
    db.save_session(topic="legacy bad", plan=None, facts=[], sources=[],
                    report=None, status="error", config=config)

    assert db.get_success_count(config) == 1, "Legacy 'complete' counts as SUCCESS"
    assert db.get_failed_count(config) == 1, "Legacy 'error' counts as FAILED"
    assert db.get_average_execution_time(config) is None, (
        "No timed runs yet -> None, not a divide-by-zero crash"
    )

    # Existing history APIs keep working exactly as before
    sessions = db.get_all_sessions(config)
    assert len(sessions) == 2
    assert sessions[0]["status"] in ("complete", "error")
    assert sessions[0]["execution_time_seconds"] is None

    full = db.get_session_by_id(sessions[0]["id"], config)
    assert full is not None and full["topic"] in ("legacy ok", "legacy bad")

    print("✅ Legacy rows readable via get_all_sessions/get_session_by_id")
    print()


def test_migration_upgrades_old_schema_idempotently():
    """Migration adds error_message + run_id (+ unique index) to a
    pre-Phase-3 table without touching existing rows; re-run is a no-op."""
    print("=" * 60)
    print("TEST 7: Old-schema migration (no data loss, idempotent)")
    print("=" * 60)

    _reset_engine_globals()
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    old_engine = create_engine(f"sqlite:///{path}")
    with old_engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE research_sessions ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " topic TEXT NOT NULL,"
            " status VARCHAR(50),"
            " created_at DATETIME,"
            " completed_at DATETIME)"
        ))
        conn.execute(text(
            "INSERT INTO research_sessions (topic, status, created_at) "
            "VALUES ('historic run', 'complete', '2026-01-01 00:00:00')"
        ))

    db._migrate_add_columns(old_engine)

    cols = {c["name"] for c in inspect(old_engine).get_columns("research_sessions")}
    for expected in ("execution_time_seconds", "error_message", "run_id"):
        assert expected in cols, f"Migration missing column: {expected}"

    # Unique index exists and tolerates the legacy NULL run_id
    with old_engine.connect() as conn:
        idx = conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND tbl_name='research_sessions' AND name='uq_research_sessions_run_id'"
        )).fetchone()
    assert idx is not None, "Unique run_id index missing"

    # Existing record untouched
    with old_engine.connect() as conn:
        row = conn.execute(text("SELECT topic, status FROM research_sessions")).fetchone()
    assert row[0] == "historic run", "Legacy row must survive migration"

    # Idempotent second run
    db._migrate_add_columns(old_engine)
    print("✅ Columns + unique index added, legacy row intact, re-run safe")
    print()


if __name__ == "__main__":
    print("\n🗄 Phase 3 PostgreSQL Metrics Persistence — Verification Tests\n")

    tests = [
        test_success_metrics_persisted,
        test_failed_metrics_persisted_with_sanitized_error,
        test_duplicate_run_id_prevented,
        test_metrics_retrievable_after_restart,
        test_averages_and_counts_correct,
        test_legacy_status_and_history_compatibility,
        test_migration_upgrades_old_schema_idempotently,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"❌ {test.__name__} FAILED: {e}\n")
            failed += 1

    print("=" * 60)
    print(f"Results: {passed} passed, {failed} failed out of {len(tests)} tests")
    print("=" * 60)
    sys.exit(1 if failed else 0)
