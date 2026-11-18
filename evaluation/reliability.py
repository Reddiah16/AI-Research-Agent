"""Reliability and graceful-failure scenarios.

Every scenario uses a temporary SQLite database or pure functions. No live
Ollama, DuckDuckGo, or PostgreSQL connection is required, and no existing
database row is modified or deleted.
"""

from __future__ import annotations

import os
import tempfile
from typing import Any, Dict, List, Optional
from unittest.mock import patch

from config.config import ResearchConfig
from database import database as db
from evaluation.engine import evaluate_report
from graph.workflow import run_research_pipeline
from schemas.models import ResearchPlan, ResearchReport
from tools.report_parsing import count_words, load_json_payload

__all__ = ["RELIABILITY_SCENARIOS", "run_reliability_suite"]

RELIABILITY_VERSION = "reliability-1.0"

RELIABILITY_SCENARIOS = (
    "ollama_unavailable",
    "model_timeout",
    "search_service_unavailable",
    "invalid_model_output",
    "malformed_json",
    "database_connection_failure",
    "empty_search_results",
    "missing_citations",
    "partial_agent_execution",
    "interrupted_report_generation",
)


def _plan(topic: str = "reliability topic") -> ResearchPlan:
    return ResearchPlan(
        topic=topic,
        search_queries=[topic],
        focus_areas=["reliability"],
    )


def _empty_summary(query: str, error: Optional[str] = None) -> Dict[str, Any]:
    return {
        "query": query,
        "summary": "No usable evidence was retrieved for this query.",
        "sources": [],
        "source_urls": [],
        "all_retrieved_urls": [],
        "evidence_items": [],
        "search_duration": 0.01,
        "search_attempts": 3 if error else 1,
        "search_error": error,
        "fetch_cache_hits": 0,
        "fetch_budget_exhausted": 0,
        "fetch_errors": [],
        "usable_evidence_count": 0,
        "filtered_results_count": 0,
        "fetched_pages_count": 0,
        "fetch_failed_count": 0,
    }


def _report(topic: str = "reliability") -> ResearchReport:
    body = (
        f"# {topic}\n\n## Introduction\nA deterministic fixture report.\n\n"
        "## Key Points\n- One finding.\n\n## Applications\n- Testing.\n\n"
        "## Challenges\n- Failures happen.\n\n## Conclusion\nHandled.\n\n"
        "## References\n"
    )
    return ResearchReport(
        title=topic,
        outline=["Introduction", "Key Points", "Applications", "Challenges", "Conclusion", "References"],
        report=body,
        sources=[],
        word_count=count_words(body),
        generation_mode="json",
        key_findings=[],
        unsupported_citations=[],
        source_details=[],
    )


def _call_pipeline(config: ResearchConfig, *, planner=None, researcher=None, editor=None):
    patches = []
    if planner is not None:
        patches.append(patch("graph.workflow.run_triage_agent", side_effect=planner))
    if researcher is not None:
        patches.append(patch("graph.workflow.run_research_agent", side_effect=researcher))
    if editor is not None:
        patches.append(patch("graph.workflow.run_editor_agent", side_effect=editor))
    for item in patches:
        item.start()
    try:
        return run_research_pipeline("reliability topic", config), None
    except BaseException as exc:  # noqa: BLE001 - scenarios include interruption
        return None, f"{type(exc).__name__}: {exc}"
    finally:
        for item in reversed(patches):
            item.stop()


def _scenario_result(
    name: str,
    expected: str,
    passed: bool,
    actual: str,
    *,
    evidence: Optional[Dict[str, Any]] = None,
    failure: str = "",
) -> Dict[str, Any]:
    return {
        "scenario": name,
        "expected_behavior": expected,
        "actual_outcome": actual,
        "passed": bool(passed),
        "failure_explanation": failure,
        "evidence": evidence or {},
    }


def run_reliability_suite() -> Dict[str, Any]:
    """Run all reliability scenarios and return a machine-readable report."""
    db._engine = None
    db._SessionFactory = None
    temp_dir = tempfile.TemporaryDirectory()
    try:
        db_path = os.path.join(temp_dir.name, "reliability.db")
        config = ResearchConfig(database_url="sqlite:///" + db_path.replace("\\", "/"))
        db.init_database(config)
        results: List[Dict[str, Any]] = []

        # 1. Ollama unavailable at the planner.
        def unavailable(_topic, _config):
            raise ConnectionError("simulated_ollama_unavailable")

        state, error = _call_pipeline(config, planner=unavailable)
        results.append(
            _scenario_result(
                "ollama_unavailable",
                "Pipeline fails cleanly with a captured connection error and no report.",
                error is not None and "ConnectionError" in error and state is None,
                f"error={error}",
                evidence={"error": error},
                failure="" if error and "ConnectionError" in error else "error not surfaced",
            )
        )

        # 2. Model timeout.
        def timeout(_topic, _config):
            raise TimeoutError("simulated_model_timeout")

        state, error = _call_pipeline(config, planner=timeout)
        results.append(
            _scenario_result(
                "model_timeout",
                "Pipeline fails cleanly with a timeout error.",
                error is not None and "TimeoutError" in error,
                f"error={error}",
                evidence={"error": error},
                failure="" if error and "TimeoutError" in error else "timeout not surfaced",
            )
        )

        # 3. Search service unavailable: no evidence, honest report, no LLM call.
        def search_down(plan, _config, on_progress=None):
            return [_empty_summary(plan.search_queries[0], error="provider_unavailable")]

        state, error = _call_pipeline(config, planner=lambda _t, _c: _plan(), researcher=search_down)
        report = (state or {}).get("report")
        passed = (
            error is None
            and state is not None
            and state.get("status") == "complete"
            and getattr(report, "generation_mode", None) == "no_evidence"
            and not getattr(report, "sources", None)
        )
        results.append(
            _scenario_result(
                "search_service_unavailable",
                "Complete with an explicit no-evidence report and no fabricated sources.",
                passed,
                f"status={(state or {}).get('status')} mode={getattr(report, 'generation_mode', None)}",
                evidence={"status": (state or {}).get("status"), "error": error},
                failure="" if passed else "no-evidence fallback not observed",
            )
        )

        # 4. Invalid model output: plain string fallback.
        state, error = _call_pipeline(
            config,
            planner=lambda _t, _c: _plan(),
            researcher=lambda p, _c, on_progress=None: [_empty_summary(p.search_queries[0])],
            editor=lambda _p, _f, _c, on_chunk_callback=None: "# Plain fallback\n\nUnstructured output.",
        )
        passed = (
            error is None
            and state is not None
            and state.get("status") == "complete_unstructured"
        )
        results.append(
            _scenario_result(
                "invalid_model_output",
                "Record an unstructured fallback instead of aborting the run.",
                passed,
                f"status={(state or {}).get('status')} mode={(state or {}).get('editor_generation_mode')}",
                evidence={"status": (state or {}).get("status")},
                failure="" if passed else "unstructured fallback not recorded",
            )
        )

        # 5. Malformed JSON repair.
        repaired_payload, repaired = load_json_payload('{"title": "x", "report": "y"')
        passed = repaired and repaired_payload.get("title") == "x"
        results.append(
            _scenario_result(
                "malformed_json",
                "Conservative repair closes a truncated object without inventing fields.",
                passed,
                f"repaired={repaired} payload_keys={sorted(repaired_payload)}",
                evidence={"payload": repaired_payload},
                failure="" if passed else "truncated JSON was not repaired",
            )
        )

        # 6. Database connection failure: rollback, no rows written.
        row_count_before = db.get_total_executions(config)
        with patch.object(db.Session, "commit", side_effect=RuntimeError("simulated_db_unavailable")):
            try:
                db.save_session(
                    topic="db failure",
                    plan=None,
                    facts=[],
                    sources=[],
                    report=None,
                    status="FAILED",
                    config=config,
                    run_id="reliability-db-failure",
                )
                db_error = None
            except Exception as exc:
                db_error = f"{type(exc).__name__}: {exc}"
        row_count_after = db.get_total_executions(config)
        passed = db_error is not None and row_count_before == row_count_after
        results.append(
            _scenario_result(
                "database_connection_failure",
                "Raise the persistence error, roll back, and leave row counts unchanged.",
                passed,
                f"error={db_error} rows_before={row_count_before} rows_after={row_count_after}",
                evidence={"rows_before": row_count_before, "rows_after": row_count_after},
                failure="" if passed else "failed write changed row count",
            )
        )

        # 7. Empty search results: no-evidence report.
        def empty_search(plan, _config, on_progress=None):
            return [_empty_summary(plan.search_queries[0])]

        state, error = _call_pipeline(config, planner=lambda _t, _c: _plan(), researcher=empty_search)
        report = (state or {}).get("report")
        passed = (
            error is None
            and getattr(report, "generation_mode", None) == "no_evidence"
            and not getattr(report, "key_findings", None)
        )
        results.append(
            _scenario_result(
                "empty_search_results",
                "Return an explicit no-evidence report with no claims.",
                passed,
                f"mode={getattr(report, 'generation_mode', None)} findings={len(getattr(report, 'key_findings', []) or [])}",
                evidence={"error": error},
                failure="" if passed else "no-evidence report not produced",
            )
        )

        # 8. Missing citations: citation quality reports no_citations, not a fake pass.
        question = {
            "id": "REL-CITE",
            "topic": "missing citations",
            "metadata": {"target_aspects": []},
            "ground_truth": {"claims": []},
            "evaluation_rubric": {"structural_requirements": [], "minimum_word_count": 10},
        }
        record = evaluate_report(question, _report(), evaluated_at="2026-11-18T00:00:00Z")
        citation = record["metrics"]["citation_quality"]
        passed = citation.get("status") == "no_citations" and citation.get("score") == 0.0
        results.append(
            _scenario_result(
                "missing_citations",
                "Report no_citations explicitly and score zero without inventing support.",
                passed,
                f"status={citation.get('status')} score={citation.get('score')}",
                evidence={"citation": citation},
                failure="" if passed else "missing citations were misclassified",
            )
        )

        # 9. Partial agent execution: research-stage failure is surfaced.
        def research_crash(_plan, _config, on_progress=None):
            raise RuntimeError("simulated_partial_agent_execution")

        state, error = _call_pipeline(config, planner=lambda _t, _c: _plan(), researcher=research_crash)
        passed = error is not None and "RuntimeError" in error
        results.append(
            _scenario_result(
                "partial_agent_execution",
                "Surface the stage failure and do not return a fabricated complete report.",
                passed,
                f"error={error}",
                evidence={"error": error},
                failure="" if passed else "partial execution was not surfaced",
            )
        )

        # 10. Interrupted report generation: BaseException must not be swallowed.
        def interrupted(_plan, _facts, _config, on_chunk_callback=None):
            raise KeyboardInterrupt("simulated_interrupted_generation")

        state, error = _call_pipeline(
            config,
            planner=lambda _t, _c: _plan(),
            researcher=lambda p, _c, on_progress=None: [_empty_summary(p.search_queries[0])],
            editor=interrupted,
        )
        passed = error is not None and "KeyboardInterrupt" in error and state is None
        results.append(
            _scenario_result(
                "interrupted_report_generation",
                "Propagate interruption instead of persisting a partial report.",
                passed,
                f"error={error}",
                evidence={"error": error},
                failure="" if passed else "interruption was swallowed or partial state returned",
            )
        )

        passed_count = sum(1 for result in results if result["passed"])
        return {
            "suite_version": RELIABILITY_VERSION,
            "scenarios_total": len(results),
            "scenarios_passed": passed_count,
            "scenarios_failed": len(results) - passed_count,
            "results": results,
            "database": "temporary SQLite only",
            "limitations": [
                "Scenarios use deterministic mocks and a temporary SQLite database.",
                "They do not simulate production PostgreSQL failover or network partitions.",
                "A passing suite is not a production reliability guarantee.",
            ],
        }
    finally:
        if db._engine is not None:
            db._engine.dispose()
        db._engine = None
        db._SessionFactory = None
        temp_dir.cleanup()
