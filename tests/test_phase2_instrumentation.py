"""Verification test suite for Phase 2 Telemetry & Instrumentation.

Verifies:
  1. Idempotent schema migration adds Phase 2 columns to research_sessions.
  2. Compatibility with legacy rows where timing and telemetry values are NULL.
  3. Node-level duration tracking (planner, research, editor, search network).
  4. Search network duration measurement.
  5. Editor JSON generation token limit is exactly 1200 (not 600, 1500, 2000 or 2048).
  6. Source-grounding instrumentation (exact, normalized, phantom, empty-safe).
  7. save_session persists all Phase 2 telemetry fields.
  8. 40-question benchmark dataset structure, taxonomy, and safety flags.

Uses temporary SQLite instances so no production database is affected.
Run: python tests/test_phase2_instrumentation.py
"""

import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from typing import Dict, List
from unittest.mock import MagicMock, patch

# Fix Windows console encoding for emoji output
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

# Add project root to sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from sqlalchemy import create_engine, inspect, text  # noqa: E402

import database.database as db  # noqa: E402
from config.config import ResearchConfig  # noqa: E402
from schemas.models import ResearchPlan, ResearchReport  # noqa: E402
from tools.grounding import analyze_source_grounding, normalize_url, extract_urls_from_text  # noqa: E402
from graph.workflow import planner_node, research_node, editor_node, GraphState  # noqa: E402


def _make_config() -> ResearchConfig:
    """Create a config pointing at a fresh temporary SQLite database."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return ResearchConfig(database_url=f"sqlite:///{path}")


def _reset_engine_globals() -> None:
    """Reset module-level engine/sessionmaker singletons between tests."""
    db._engine = None
    db._SessionFactory = None


def test_schema_migration_adds_phase2_columns():
    """Verify that _migrate_add_columns adds all Phase 2 columns idempotently."""
    print("=" * 60)
    print("TEST 1: Schema migration adds Phase 2 columns idempotently")
    print("=" * 60)

    _reset_engine_globals()
    config = _make_config()
    engine = db.get_engine(config)

    # Simulate a legacy pre-Phase-2 table
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE research_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic TEXT NOT NULL,
                search_queries TEXT,
                focus_areas TEXT,
                facts TEXT,
                status VARCHAR(50) DEFAULT 'started',
                model_name VARCHAR(100),
                execution_time_seconds FLOAT,
                error_message TEXT,
                run_id VARCHAR(36) UNIQUE,
                created_at TIMESTAMP,
                completed_at TIMESTAMP
            )
        """))
        # Seed an existing historical row
        conn.execute(text("""
            INSERT INTO research_sessions (topic, status, execution_time_seconds)
            VALUES ('Legacy Historic Run', 'SUCCESS', 127.85)
        """))

    # Run migration
    db._migrate_add_columns(engine)

    columns = {c["name"] for c in inspect(engine).get_columns("research_sessions")}
    expected_new_cols = {
        "planner_latency_seconds",
        "research_latency_seconds",
        "editor_latency_seconds",
        "search_network_latency_seconds",
        "grounded_sources_count",
        "citation_grounding_ratio",
        "editor_generation_mode",
    }

    for col in expected_new_cols:
        assert col in columns, f"Missing migrated column: {col}"
    print(f"  ✓ All Phase 2 columns present: {expected_new_cols}")

    # Verify existing historical row preserved with NULL for new columns
    with engine.connect() as conn:
        row = conn.execute(text("SELECT * FROM research_sessions WHERE topic='Legacy Historic Run'")).mappings().first()
        assert row["topic"] == "Legacy Historic Run"
        assert row["execution_time_seconds"] == 127.85
        assert row["planner_latency_seconds"] is None
        assert row["grounded_sources_count"] is None
    print("  ✓ Historic row preserved with NULL values for new telemetry columns")

    # Run migration a second time to verify idempotency (no errors, no duplicates)
    db._migrate_add_columns(engine)
    print("  ✓ Migration is strictly idempotent (safe to run repeatedly)")


def test_missing_historical_timing_compatibility():
    """Verify system handles rows where timing and telemetry values are NULL."""
    print("=" * 60)
    print("TEST 2: Legacy rows with missing historical timing values")
    print("=" * 60)

    _reset_engine_globals()
    config = _make_config()
    db.init_database(config)

    # Insert a session with all timing values omitted (legacy pre-timing row)
    session_id = db.save_session(
        topic="Pre-telemetry session",
        plan=None,
        facts=[],
        sources=[],
        report=None,
        status="SUCCESS",
        config=config,
        execution_time=None,
        planner_latency=None,
        research_latency=None,
        editor_latency=None,
        search_network_duration=None,
        grounded_sources_count=None,
        citation_grounding_ratio=None,
        editor_generation_mode=None,
    )

    retrieved = db.get_session_by_id(session_id, config)
    assert retrieved is not None
    assert retrieved["execution_time_seconds"] is None
    assert retrieved["planner_latency_seconds"] is None
    assert retrieved["research_latency_seconds"] is None
    assert retrieved["editor_latency_seconds"] is None
    assert retrieved["search_network_latency_seconds"] is None
    assert retrieved["grounded_sources_count"] is None
    assert retrieved["citation_grounding_ratio"] is None
    assert retrieved["editor_generation_mode"] is None
    print("  ✓ Retrieved legacy session safely without schema/type errors")

    # Verify aggregation queries gracefully ignore NULLs
    node_avgs = db.get_node_latency_averages(config)
    assert node_avgs["planner_latency"] is None
    assert node_avgs["research_latency"] is None
    assert node_avgs["editor_latency"] is None
    assert node_avgs["search_network_latency"] is None

    grounding_avgs = db.get_citation_grounding_averages(config)
    assert grounding_avgs["average_grounded_sources"] is None
    assert grounding_avgs["average_grounding_ratio"] is None

    gen_stats = db.get_editor_generation_stats(config)
    assert gen_stats["total_tracked_runs"] == 0
    print("  ✓ Telemetry aggregations gracefully handle empty/NULL historical states")


def test_node_latency_and_search_duration_tracking():
    """Verify that graph nodes capture individual durations in GraphState."""
    print("=" * 60)
    print("TEST 3: Node-level duration and search network tracking")
    print("=" * 60)

    config = ResearchConfig()

    # 1. Planner node timing
    mock_plan = ResearchPlan(topic="Quantum AI", search_queries=["q1"], focus_areas=["f1"])
    with patch("graph.workflow.run_triage_agent", return_value=mock_plan) as mock_triage:
        state: GraphState = {"topic": "Quantum AI", "config": config}
        out = planner_node(state)
        assert "planner_latency" in out
        assert isinstance(out["planner_latency"], float)
        assert out["planner_latency"] >= 0.0
    print(f"  ✓ Planner node captures planner_latency ({out['planner_latency']}s)")

    # 2. Research node timing and search_network_duration
    mock_summaries = [
        {
            "query": "q1",
            "summary": "sum1",
            "sources": [{"url": "https://example.com/1", "title": "T1", "snippet": "S1"}],
            "source_urls": ["https://example.com/1"],
            "search_duration": 0.452,
        },
        {
            "query": "q2",
            "summary": "sum2",
            "sources": [{"url": "https://example.com/2", "title": "T2", "snippet": "S2"}],
            "source_urls": ["https://example.com/2"],
            "search_duration": 0.318,
        },
    ]
    with patch("graph.workflow.run_research_agent", return_value=mock_summaries):
        state = {"plan": mock_plan, "config": config}
        out = research_node(state)
        assert "research_latency" in out
        assert "search_network_duration" in out
        assert out["search_network_duration"] == round(0.452 + 0.318, 3)
    print(f"  ✓ Research node captures research_latency ({out['research_latency']}s) "
          f"and search_network_duration ({out['search_network_duration']}s)")

    # 3. Editor node timing and grounding
    mock_report = ResearchReport(
        title="Report Title",
        outline=["Introduction"],
        report="# Report Title\n\n## References\n1. Source - https://example.com/1",
        sources=["https://example.com/1"],
        word_count=50,
        generation_mode="json",
    )
    with patch("graph.workflow.run_editor_agent", return_value=mock_report):
        state = {
            "plan": mock_plan,
            "facts": mock_summaries,
            "sources": ["https://example.com/1", "https://example.com/2"],
            "config": config,
        }
        out = editor_node(state)
        assert "editor_latency" in out
        assert out["editor_generation_mode"] == "json"
        assert out["grounded_sources_count"] == 1
        assert out["citation_grounding_ratio"] == 0.5
        assert out["grounded_sources"] == ["https://example.com/1"]
    print(f"  ✓ Editor node captures editor_latency ({out['editor_latency']}s), "
          f"generation_mode ('{out['editor_generation_mode']}'), "
          f"and citation grounding (ratio={out['citation_grounding_ratio']})")


def test_editor_token_limit_configuration():
    """Verify Editor JSON generation max_tokens is exactly 1200 (approved cap).

    The approved value was reduced from 2000 to 1200 alongside the streaming
    early-stop optimization. This test still guards the original intent: the
    Editor JSON cap must be one exact approved value, never the rejected 600 or
    2048 values, and never a silent return to the old 1500/2000 expectations.
    """
    print("=" * 60)
    print("TEST 4: Editor JSON generation token limit configuration")
    print("=" * 60)

    # Read agents/editor_agent.py source code directly to check max_tokens setting
    editor_path = os.path.join(PROJECT_ROOT, "agents", "editor_agent.py")
    with open(editor_path, "r", encoding="utf-8") as f:
        source = f.read()

    assert "max_tokens=1200" in source, "editor_agent.py must configure max_tokens=1200"
    assert "max_tokens=600" not in source, "editor_agent.py should no longer contain max_tokens=600"
    assert "max_tokens=1500" not in source, "max_tokens=1500 is the superseded stale expectation"
    assert "max_tokens=2000" not in source, "max_tokens must not revert to the pre-optimization 2000 cap"
    assert "max_tokens=2048" not in source, "max_tokens must not be increased to 2048 without approval"
    print("  ✓ Confirmed editor_agent.py sets max_tokens=1200 strictly as approved")


def test_source_grounding_instrumentation():
    """Verify source-grounding algorithm across exact, normalized, and phantom cases."""
    print("=" * 60)
    print("TEST 5: Source grounding instrumentation")
    print("=" * 60)

    retrieved = [
        "https://www.example.com/doc1/",
        "https://example.org/doc2",
        "https://unused.com/doc3",
    ]

    report_text = """
    # Advanced Tech Report
    
    ## Introduction
    According to official specifications from https://example.com/doc1, the architecture works.
    Additionally, we found https://unretrieved-phantom.com/external-doc.
    
    ## References
    1. Example Org - https://example.org/doc2
    """

    res = analyze_source_grounding(
        retrieved_urls=retrieved,
        report_content=report_text,
        explicit_sources=["https://example.org/doc2"],
    )

    assert res["retrieved_sources_count"] == 3
    assert res["grounded_sources_count"] == 2  # doc1 (normalized match) and doc2 (exact match)
    assert res["citation_grounding_ratio"] == round(2 / 3, 4)
    assert "https://unused.com/doc3" in res["ungrounded_retrieved_urls"]
    assert "https://unretrieved-phantom.com/external-doc" in res["phantom_urls"]
    print(f"  ✓ Grounding correctly detected: {res['grounded_sources_count']}/{res['retrieved_sources_count']} grounded, "
          f"{len(res['phantom_urls'])} phantom URLs")

    # Edge case: empty retrieved sources list (zero division prevention)
    res_empty = analyze_source_grounding(retrieved_urls=[], report_content="Some report")
    assert res_empty["retrieved_sources_count"] == 0
    assert res_empty["grounded_sources_count"] == 0
    assert res_empty["citation_grounding_ratio"] == 0.0
    print("  ✓ Handled empty retrieved sources gracefully with ratio=0.0 (no ZeroDivisionError)")


def test_save_session_persists_telemetry():
    """Verify save_session persists all Phase 2 telemetry fields."""
    print("=" * 60)
    print("TEST 6: save_session persists Phase 2 telemetry to database")
    print("=" * 60)

    _reset_engine_globals()
    config = _make_config()
    db.init_database(config)

    plan = ResearchPlan(topic="Telemetry Test", search_queries=["q1"], focus_areas=["f1"])
    report = ResearchReport(
        title="Test Report",
        outline=["Intro"],
        report="# Test Report\n\nContent",
        sources=["https://test.org"],
        word_count=20,
        generation_mode="json",
    )

    session_id = db.save_session(
        topic="Telemetry Test",
        plan=plan,
        facts=[],
        sources=["https://test.org"],
        report=report,
        status="SUCCESS",
        config=config,
        execution_time=45.12,
        run_id="test-run-telemetry-001",
        planner_latency=3.456,
        research_latency=12.789,
        editor_latency=28.875,
        search_network_duration=1.234,
        grounded_sources_count=1,
        citation_grounding_ratio=1.0,
        editor_generation_mode="json",
    )

    rec = db.get_session_by_id(session_id, config)
    assert rec["planner_latency_seconds"] == 3.456
    assert rec["research_latency_seconds"] == 12.789
    assert rec["editor_latency_seconds"] == 28.875
    assert rec["search_network_latency_seconds"] == 1.234
    assert rec["grounded_sources_count"] == 1
    assert rec["citation_grounding_ratio"] == 1.0
    assert rec["editor_generation_mode"] == "json"
    print("  ✓ Full telemetry successfully persisted and re-queried from database")

    # Verify aggregation queries
    node_avgs = db.get_node_latency_averages(config)
    assert node_avgs["planner_latency"] == 3.456
    assert node_avgs["editor_latency"] == 28.875

    gen_stats = db.get_editor_generation_stats(config)
    assert gen_stats["total_tracked_runs"] == 1
    assert gen_stats["json_success_count"] == 1
    assert gen_stats["markdown_fallback_count"] == 0
    assert gen_stats["json_success_rate"] == 1.0
    print("  ✓ Telemetry aggregation APIs return accurate summary metrics")


def test_benchmark_dataset_structure_and_safety():
    """Verify 40-question benchmark dataset structure, taxonomy, and safety flags."""
    print("=" * 60)
    print("TEST 7: Benchmark dataset validation (40 questions, taxonomy, safety flags)")
    print("=" * 60)

    dataset_path = os.path.join(PROJECT_ROOT, "benchmarks", "datasets", "benchmark_v1.json")
    assert os.path.exists(dataset_path), f"Benchmark dataset missing at: {dataset_path}"

    with open(dataset_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert "benchmark_questions" in data
    questions = data["benchmark_questions"]
    assert len(questions) == 40, f"Expected 40 benchmark questions, found {len(questions)}"

    expected_domains = {"AI", "CC", "CS", "DE", "ET", "SE"}
    expected_difficulties = {"Easy", "Intermediate", "Difficult"}
    expected_task_types = {"Explanation", "Comparison", "Multi-Source", "SOTA"}

    observed_ids = set()
    for idx, q in enumerate(questions, 1):
        expected_id = f"Q{idx:02d}"
        assert q["id"] == expected_id, f"Question ID mismatch: expected {expected_id}, got {q['id']}"
        assert q["id"] not in observed_ids, f"Duplicate ID: {q['id']}"
        observed_ids.add(q["id"])

        assert q["domain"] in expected_domains, f"Invalid domain in {q['id']}: {q['domain']}"
        assert q["difficulty"] in expected_difficulties, f"Invalid difficulty in {q['id']}: {q['difficulty']}"
        assert q["task_type"] in expected_task_types, f"Invalid task_type in {q['id']}: {q['task_type']}"

        # Safety & structural separation checks
        assert "metadata" in q, f"Missing metadata in {q['id']}"
        assert "ground_truth" in q, f"Missing ground_truth in {q['id']}"
        assert "evaluation_rubric" in q, f"Missing evaluation_rubric in {q['id']}"

        gt = q["ground_truth"]
        assert gt["verification_status"] == "requires_manual_reference_verification", (
            f"Unapproved ground truth verification status in {q['id']}: {gt['verification_status']}"
        )
        assert gt["is_verified"] is False, f"is_verified must be False in provisional dataset: {q['id']}"
        assert len(gt.get("provisional_reference_claims", [])) >= 2, f"Missing claims in {q['id']}"

    print("  ✓ All 40 benchmark questions verified: strict Q01-Q40 sequence")
    print(f"  ✓ Preserved taxonomy across all 6 domains: {expected_domains}")
    print("  ✓ Strict safety flags verified: all 40 marked 'requires_manual_reference_verification'")


if __name__ == "__main__":
    print("\n🚀 Running Phase 2 Telemetry & Instrumentation Test Suite\n")
    test_schema_migration_adds_phase2_columns()
    test_missing_historical_timing_compatibility()
    test_node_latency_and_search_duration_tracking()
    test_editor_token_limit_configuration()
    test_source_grounding_instrumentation()
    test_save_session_persists_telemetry()
    test_benchmark_dataset_structure_and_safety()
    print("\n" + "=" * 60)
    print("🎉 ALL PHASE 2 TESTS PASSED PERFECTLY!")
    print("=" * 60 + "\n")

