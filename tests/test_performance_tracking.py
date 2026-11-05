"""Standalone tests for Phase 2 performance tracking.

Verifies:
  - execution_time_seconds is persisted on save_session()
  - SUCCESS / FAILED status recording (failed runs are saved too)
  - source_count uses unique URLs (deduplicated across queries)
  - report rows only exist for runs that produced a valid report
  - schema migration adds the new column to an old-schema table
  - existing history reads (get_all_sessions / get_session_by_id) still work

Uses a temporary SQLite file so no PostgreSQL instance is required.

Run:  python tests/test_performance_tracking.py
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


def test_save_successful_session():
    """SUCCESS run: execution time, status, sources and report all stored."""
    print("=" * 60)
    print("TEST 1: Successful session records metrics")
    print("=" * 60)

    _reset_engine_globals()
    config = _make_config()
    db.init_database(config)

    plan = ResearchPlan(topic="quantum computing", search_queries=["q1"], focus_areas=["f1"])
    facts = [
        {
            "query": "q1",
            "summary": "s1",
            "sources": [
                {"url": "https://a.com", "title": "A", "snippet": "aa"},
                {"url": "https://b.com", "title": "B", "snippet": "bb"},
            ],
            "source_urls": ["https://a.com", "https://b.com"],
        }
    ]
    report = ResearchReport(
        title="Quantum Report",
        outline=["Introduction"],
        report="# Quantum Report\n\ncontent here",
        sources=["https://a.com", "https://b.com"],
        word_count=3,
    )

    session_id = db.save_session(
        topic="quantum computing",
        plan=plan,
        facts=facts,
        sources=["https://a.com", "https://b.com"],
        report=report,
        status="SUCCESS",
        config=config,
        execution_time=12.3456,
    )

    full = db.get_session_by_id(session_id, config)
    assert full is not None, "Session should be retrievable"
    assert full["status"] == "SUCCESS", f"Expected SUCCESS, got {full['status']}"
    assert full["execution_time_seconds"] == 12.35, (
        f"Expected rounded 12.35, got {full['execution_time_seconds']}"
    )
    assert full["completed_at"] is not None, "completed_at should be set"
    assert full["report"] is not None, "Successful run should have a report row"
    assert len(full["sources"]) == 2, f"Expected 2 sources, got {len(full['sources'])}"

    sessions = db.get_all_sessions(config)
    assert sessions[0]["has_report"] is True
    assert sessions[0]["source_count"] == 2

    print(f"✅ Session {session_id}: status=SUCCESS, time={full['execution_time_seconds']}s")
    print()


def test_save_failed_session():
    """FAILED run: persisted with timing but WITHOUT a report row."""
    print("=" * 60)
    print("TEST 2: Failed session recorded, no report counted")
    print("=" * 60)

    _reset_engine_globals()
    config = _make_config()
    db.init_database(config)

    session_id = db.save_session(
        topic="broken topic",
        plan=None,
        facts=[],
        sources=[],
        report=None,
        status="FAILED",
        config=config,
        execution_time=3.21,
    )

    full = db.get_session_by_id(session_id, config)
    assert full["status"] == "FAILED"
    assert full["execution_time_seconds"] == 3.21
    assert full["report"] is None, "Failed run must not create a report row"

    sessions = db.get_all_sessions(config)
    assert sessions[0]["has_report"] is False, "Failed run must not count as report"
    assert sessions[0]["source_count"] == 0

    print(f"✅ Failed session {session_id} persisted with time and FAILED status")
    print()


def test_source_dedup():
    """The same URL found by two queries must be stored/counted once."""
    print("=" * 60)
    print("TEST 3: Duplicate URLs deduplicated across queries")
    print("=" * 60)

    _reset_engine_globals()
    config = _make_config()
    db.init_database(config)

    shared = {"url": "https://shared.com", "title": "Shared", "snippet": "ss"}
    facts = [
        {
            "query": "q1",
            "summary": "s1",
            "sources": [shared, {"url": "https://c1.com", "title": "C1", "snippet": ""}],
            "source_urls": ["https://shared.com", "https://c1.com"],
        },
        {
            "query": "q2",
            "summary": "s2",
            "sources": [shared, {"url": "https://c2.com", "title": "C2", "snippet": ""}],
            "source_urls": ["https://shared.com", "https://c2.com"],
        },
    ]

    session_id = db.save_session(
        topic="dedup test",
        plan=None,
        facts=facts,
        sources=["https://shared.com", "https://c1.com", "https://c2.com"],
        report=None,
        status="SUCCESS",
        config=config,
        execution_time=1.0,
    )

    full = db.get_session_by_id(session_id, config)
    urls = [s["url"] for s in full["sources"]]
    assert len(urls) == 3, f"Expected 3 unique source rows, got {len(urls)}: {urls}"

    sessions = db.get_all_sessions(config)
    assert sessions[0]["source_count"] == 3, (
        f"Expected unique source_count=3, got {sessions[0]['source_count']}"
    )

    print(f"✅ Stored {len(urls)} unique sources from 4 raw hits (1 duplicate URL)")
    print()


def test_no_execution_time_defaults_to_none():
    """Omitting execution_time keeps the column NULL (backward compatible)."""
    print("=" * 60)
    print("TEST 4: Missing execution_time stays NULL, no crash")
    print("=" * 60)

    _reset_engine_globals()
    config = _make_config()
    db.init_database(config)

    session_id = db.save_session(
        topic="legacy call",
        plan=None,
        facts=[],
        sources=[],
        report=None,
        status="SUCCESS",
        config=config,
    )

    full = db.get_session_by_id(session_id, config)
    assert full["execution_time_seconds"] is None

    print("✅ execution_time is optional; legacy call signature still works")
    print()


def test_migration_adds_column_to_old_schema():
    """_migrate_add_columns adds execution_time_seconds to a pre-Phase-2 table."""
    print("=" * 60)
    print("TEST 5: Schema migration on existing old-schema table")
    print("=" * 60)

    _reset_engine_globals()
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    # Build an OLD-schema database manually (no execution_time_seconds column)
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

    cols_before = {c["name"] for c in inspect(old_engine).get_columns("research_sessions")}
    assert "execution_time_seconds" not in cols_before

    # Run the migration
    db._migrate_add_columns(old_engine)

    cols_after = {c["name"] for c in inspect(old_engine).get_columns("research_sessions")}
    assert "execution_time_seconds" in cols_after, "Migration should add the column"

    # Existing history must be untouched
    with old_engine.connect() as conn:
        row = conn.execute(text("SELECT topic, status FROM research_sessions")).fetchone()
    assert row is not None and row[0] == "historic run", "Old rows must survive migration"

    # Migration must be idempotent
    db._migrate_add_columns(old_engine)
    print("✅ Column added, existing rows preserved, re-run is a no-op")
    print()


def test_status_semantics_match_pipeline_rules():
    """Verify the SUCCESS-mapping rule used by app.py against workflow outputs."""
    print("=" * 60)
    print("TEST 6: Status normalization rules")
    print("=" * 60)

    def classify(status_from_workflow: str, report) -> str:
        """Mirror of the rule in app.py."""
        return (
            "SUCCESS"
            if status_from_workflow == "complete" and isinstance(report, ResearchReport)
            else "FAILED"
        )

    good = ResearchReport(title="t", outline=[], report="r", sources=[], word_count=1)

    assert classify("complete", good) == "SUCCESS"
    assert classify("complete_unstructured", "# topic\n\nReport generation failed") == "FAILED"
    assert classify("complete", "raw string fallback") == "FAILED"
    assert classify("research_complete", None) == "FAILED"

    print("✅ Only structured-report completions map to SUCCESS")
    print()


if __name__ == "__main__":
    print("\n📊 Phase 2 Performance Tracking — Verification Tests\n")

    tests = [
        test_save_successful_session,
        test_save_failed_session,
        test_source_dedup,
        test_no_execution_time_defaults_to_none,
        test_migration_adds_column_to_old_schema,
        test_status_semantics_match_pipeline_rules,
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
