"""Standalone tests for Phase 4 Streamlit analytics dashboard.

Verifies:
  1. Dashboard loads with existing database records (render path raises
     nothing when seeded data exists).
  2. KPI service values match the database exactly.
  3. History filters (status / date range / search) work in SQL.
  4. Chart series functions return actual stored records.
  5. Empty database state is handled gracefully.
  6. Database disconnection is handled gracefully (no crash).
  7. Daily activity aggregation is correct.

The dashboard is exercised in Streamlit "bare mode" (no script runner):
widgets return their defaults and output elements are no-ops, which lets
us verify the full data + render code path end to end.

Run:  python tests/test_analytics_dashboard.py
"""

import logging
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

# Fix Windows console encoding for emoji output
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database.database as db  # noqa: E402
from config.config import ResearchConfig  # noqa: E402
from schemas.models import ResearchReport  # noqa: E402


def _make_config() -> ResearchConfig:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return ResearchConfig(database_url=f"sqlite:///{path}")


def _reset_engine_globals() -> None:
    db._engine = None
    db._SessionFactory = None


def _report() -> ResearchReport:
    return ResearchReport(
        title="R", outline=[], report="# R\n\nbody", sources=["https://a.com"], word_count=2
    )


def _facts(*urls: str) -> list:
    return [{
        "query": "q",
        "summary": "s",
        "sources": [{"url": u, "title": u, "snippet": ""} for u in urls],
        "source_urls": list(urls),
    }]


def _seed(config: ResearchConfig) -> None:
    """Seed 4 runs: 3 SUCCESS (10/20/30s), 1 FAILED (40s), varied sources."""
    db.init_database(config)
    db.save_session(topic="espresso machines", plan=None,
                    facts=_facts("https://a.com", "https://b.com"), sources=[],
                    report=_report(), status="SUCCESS", config=config,
                    execution_time=10.0, run_id="d-1")
    db.save_session(topic="cruise lines USA", plan=None,
                    facts=_facts("https://c.com"), sources=[],
                    report=_report(), status="SUCCESS", config=config,
                    execution_time=20.0, run_id="d-2")
    db.save_session(topic="india solo travel", plan=None,
                    facts=[], sources=[], report=_report(),
                    status="SUCCESS", config=config, execution_time=30.0, run_id="d-3")
    db.save_session(topic="espresso broken run", plan=None, facts=[], sources=[],
                    report=None, status="FAILED", config=config,
                    execution_time=40.0, run_id="d-4", error_message="boom")


def test_kpi_values_match_database():
    """2: KPI service functions return values that match stored records."""
    print("=" * 60)
    print("TEST 1: KPI values match database records")
    print("=" * 60)

    _reset_engine_globals()
    config = _make_config()
    _seed(config)

    assert db.get_total_executions(config) == 4
    dist = db.get_status_distribution(config)
    assert dist == {"SUCCESS": 3, "FAILED": 1}
    assert db.get_average_execution_time(config) == 25.0  # (10+20+30+40)/4
    assert db.get_average_source_count(config) == 0.75   # (2+1+0+0)/4
    assert db.get_total_reports(config) == 3              # failed run: no report

    print(f"✅ totals={dist} avg_time=25.0 avg_sources=0.75 reports=3")
    print()


def test_history_filters():
    """3: status / search / date-range filters applied in SQL."""
    print("=" * 60)
    print("TEST 2: History table filters")
    print("=" * 60)

    _reset_engine_globals()
    config = _make_config()
    _seed(config)

    # Status filters
    only_success = db.get_execution_history_records(config, status="SUCCESS")
    only_failed = db.get_execution_history_records(config, status="FAILED")
    assert len(only_success) == 3 and len(only_failed) == 1

    # Search filter (case-insensitive substring)
    matches = db.get_execution_history_records(config, search_text="ESPRESSO")
    assert len(matches) == 2, f"Expected 2 'espresso' matches, got {len(matches)}"

    # Combined filters
    combo = db.get_execution_history_records(
        config, status="FAILED", search_text="espresso"
    )
    assert len(combo) == 1 and combo[0]["run_id"] == "d-4"

    # Date-range filter: future cutoff excludes everything, epoch includes all
    future = db.get_execution_history_records(
        config, since=datetime.now(timezone.utc) + timedelta(days=1)
    )
    assert future == []
    everything = db.get_execution_history_records(
        config, since=datetime(2000, 1, 1, tzinfo=timezone.utc)
    )
    assert len(everything) == 4

    print("✅ status / search / combined / date-range filters all correct")
    print()


def test_chart_series_use_stored_records():
    """4: chart data functions return the actual seeded rows."""
    print("=" * 60)
    print("TEST 3: Chart series from stored records")
    print("=" * 60)

    _reset_engine_globals()
    config = _make_config()
    _seed(config)

    times = db.get_execution_time_series(config)
    assert len(times) == 4
    assert [t["execution_time_seconds"] for t in times] == [40.0, 30.0, 20.0, 10.0], (
        "Series must be newest-first"
    )

    sources = db.get_source_count_series(config)
    assert len(sources) == 4
    by_id = {s["id"]: s["source_count"] for s in sources}
    # Run ids 1..4 → 2, 1, 0, 0 unique sources
    assert sorted(by_id.values(), reverse=True) == [2, 1, 0, 0]

    activity = db.get_daily_activity(config, days=1)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    assert len(activity) == 1 and activity[0]["date"] == today
    assert activity[0]["count"] == 4

    print(f"✅ time series={len(times)} source series={len(sources)} "
          f"activity today={activity[0]['count']} runs")
    print()


def test_legacy_status_counted_in_dashboard():
    """Legacy 'complete'/'error' rows appear correctly in dashboard metrics."""
    print("=" * 60)
    print("TEST 4: Legacy rows normalized in analytics")
    print("=" * 60)

    _reset_engine_globals()
    config = _make_config()
    db.init_database(config)
    db.save_session(topic="legacy ok", plan=None, facts=[], sources=[],
                    report=_report(), status="complete", config=config)
    db.save_session(topic="legacy bad", plan=None, facts=[], sources=[],
                    report=None, status="error", config=config)

    dist = db.get_status_distribution(config)
    assert dist == {"SUCCESS": 1, "FAILED": 1}
    success_rows = db.get_execution_history_records(config, status="SUCCESS")
    assert len(success_rows) == 1 and success_rows[0]["status"] == "complete"

    print("✅ legacy 'complete'/'error' normalized to SUCCESS/FAILED buckets")
    print()


def test_dashboard_renders_with_records():
    """1: full render path completes without exceptions on seeded data.

    Also asserts NO ERROR-level log records from ui.analytics — section
    error boundaries must not silently swallow a broken chart/table
    (a real Streamlit UI check caught exactly this in Phase 5).
    """
    print("=" * 60)
    print("TEST 5: Dashboard render with existing records (bare mode)")
    print("=" * 60)

    _reset_engine_globals()
    config = _make_config()
    _seed(config)

    from ui.analytics import render_analytics

    class _ErrorCatcher(logging.Handler):
        def __init__(self):
            super().__init__()
            self.errors = []
        def emit(self, record):
            if record.levelno >= logging.ERROR:
                self.errors.append(record.getMessage())

    analytics_logger = logging.getLogger("ui.analytics")
    catcher = _ErrorCatcher()
    analytics_logger.addHandler(catcher)
    try:
        render_analytics(config)  # must not raise
    finally:
        analytics_logger.removeHandler(catcher)

    assert not catcher.errors, (
        f"Dashboard sections failed silently: {catcher.errors}"
    )

    print("✅ render_analytics() completed with zero section-level errors")
    print()


def test_dashboard_empty_database():
    """5: empty database shows the empty state without crashing."""
    print("=" * 60)
    print("TEST 6: Empty database state")
    print("=" * 60)

    _reset_engine_globals()
    config = _make_config()
    db.init_database(config)

    assert db.get_total_executions(config) == 0
    from ui.analytics import render_analytics
    render_analytics(config)  # empty state path — must not raise

    print("✅ empty database handled gracefully")
    print()


def test_dashboard_database_disconnected():
    """6: unreachable database is shown as an error, not a crash."""
    print("=" * 60)
    print("TEST 7: Database disconnection handling")
    print("=" * 60)

    _reset_engine_globals()
    dead_config = ResearchConfig(
        database_url="postgresql://nouser:nopass@127.0.0.1:59999/nodb"
    )
    _reset_engine_globals()

    from ui.analytics import render_analytics
    # Must NOT raise — the dashboard catches and renders an error panel.
    render_analytics(dead_config)

    _reset_engine_globals()
    print("✅ unreachable database handled without exception")
    print()


def test_status_chart_edge_cases():
    """8: status chart handles SUCCESS-only, FAILED-only and empty data.

    The old Plotly bug came from a fixed two-color range meeting a
    one-category dataset. The Altair implementation builds the color
    domain from the actual categories, so these cases must render (or
    show an empty state) without raising or logging an error.
    """
    print("=" * 60)
    print("TEST 8: Status chart category/color edge cases")
    print("=" * 60)

    from ui.analytics import _render_status_chart

    class _ErrorCatcher(logging.Handler):
        def __init__(self):
            super().__init__()
            self.errors = []
        def emit(self, record):
            if record.levelno >= logging.ERROR:
                self.errors.append(record.getMessage())

    analytics_logger = logging.getLogger("ui.analytics")
    catcher = _ErrorCatcher()
    analytics_logger.addHandler(catcher)
    try:
        _render_status_chart(success=5, failed=3)  # both categories
        _render_status_chart(success=7, failed=0)  # SUCCESS only
        _render_status_chart(success=0, failed=2)  # FAILED only
        _render_status_chart(success=0, failed=0)  # empty dataset
    finally:
        analytics_logger.removeHandler(catcher)

    assert not catcher.errors, f"Status chart raised silently: {catcher.errors}"
    print("✅ both / SUCCESS-only / FAILED-only / empty all handled cleanly")
    print()


def test_chart_failures_are_isolated():
    """9: one broken chart must not hide the other charts or the table.

    Each chart renderer is patched to raise; the dashboard must still
    complete, log exactly the individual failures, and render the
    history table (its renderer must have been reached).
    """
    print("=" * 60)
    print("TEST 9: Per-chart error isolation")
    print("=" * 60)

    _reset_engine_globals()
    config = _make_config()
    _seed(config)

    import ui.analytics as ua

    reached = {"history": False}
    original = {
        "time": ua._render_execution_time_chart,
        "activity": ua._render_activity_chart,
        "sources": ua._render_source_count_chart,
        "history": ua._render_history_table,
    }

    def _boom(*args, **kwargs):
        raise RuntimeError("synthetic chart failure")

    def _ok_history(cfg, total):
        reached["history"] = True
        original["history"](cfg, total)

    ua._render_execution_time_chart = _boom
    ua._render_activity_chart = _boom
    ua._render_source_count_chart = _boom
    ua._render_history_table = _ok_history

    class _ErrorCatcher(logging.Handler):
        def __init__(self):
            super().__init__()
            self.errors = []
        def emit(self, record):
            if record.levelno >= logging.ERROR:
                self.errors.append(record.getMessage())

    analytics_logger = logging.getLogger("ui.analytics")
    catcher = _ErrorCatcher()
    analytics_logger.addHandler(catcher)
    try:
        ua.render_analytics(config)  # must not raise
    finally:
        analytics_logger.removeHandler(catcher)
        ua._render_execution_time_chart = original["time"]
        ua._render_activity_chart = original["activity"]
        ua._render_source_count_chart = original["sources"]
        ua._render_history_table = original["history"]

    # Each broken chart logged its own failure (3 sections, not one).
    chart_errors = [e for e in catcher.errors if "synthetic chart failure" in e]
    assert len(chart_errors) == 3, (
        f"Expected 3 isolated chart failures, got {chart_errors}"
    )
    # The history table still rendered despite every chart failing.
    assert reached["history"], "History table must render after chart failures"
    # Rendering errors must not be reported as database problems.
    assert all("database" not in e.lower() or "not a database" in e.lower()
               for e in catcher.errors), catcher.errors

    print("✅ 3 chart failures isolated; history table still rendered")
    print()


if __name__ == "__main__":
    print("\n📊 Phase 4 Analytics Dashboard — Verification Tests\n")

    tests = [
        test_kpi_values_match_database,
        test_history_filters,
        test_chart_series_use_stored_records,
        test_legacy_status_counted_in_dashboard,
        test_dashboard_renders_with_records,
        test_dashboard_empty_database,
        test_dashboard_database_disconnected,
        test_status_chart_edge_cases,
        test_chart_failures_are_isolated,
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
