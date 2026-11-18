"""Regression tests for the production-hardening changes.

Covers:
  1. Structured report persistence (key_findings, unsupported_citations,
     source_details), legacy NULL rows, idempotent saves and failed writes.
  2. Word-count derivation from the final report body.
  3. Evidence-based factual-support evaluation.

All database tests use a throwaway SQLite file. No PostgreSQL, Ollama, or
network service is required. Production data is never touched.
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

from sqlalchemy import inspect, text

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from config.config import ResearchConfig
from database import database as db
from evaluation.engine import evaluate_open_state
from evaluation.metrics import evidence_factual_support
from schemas.models import ResearchPlan, ResearchReport
from tools.report_parsing import count_words, normalize_report_payload


def _reset_engine_globals():
    db._engine = None
    db._SessionFactory = None


class _SqliteCase(unittest.TestCase):
    def setUp(self):
        _reset_engine_globals()
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.remove(self.db_path)
        self.config = ResearchConfig(
            database_url="sqlite:///" + self.db_path.replace("\\", "/")
        )

    def tearDown(self):
        if db._engine is not None:
            db._engine.dispose()
        _reset_engine_globals()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)


# ---------------------------------------------------------------------------
# Task 2 — word count
# ---------------------------------------------------------------------------

class WordCountTests(unittest.TestCase):
    def test_short_report(self):
        self.assertEqual(count_words("Hello world"), 2)

    def test_long_report(self):
        body = " ".join(f"word{i}" for i in range(500))
        self.assertEqual(count_words(body), 500)

    def test_empty_and_whitespace(self):
        for value in ("", "   ", "\n\t\r\n", None, 123):
            self.assertEqual(count_words(value), 0)

    def test_punctuation(self):
        self.assertEqual(count_words("Hello, world! Why?"), 3)
        self.assertEqual(count_words("state-of-the-art design"), 2)

    def test_structural_tokens_are_not_words(self):
        self.assertEqual(count_words("# Title\n\n---\n\n| a | b |"), 3)
        self.assertEqual(count_words("* bullet\n- another"), 2)

    def test_normalization_ignores_model_word_count(self):
        body = " ".join(["actual"] * 251)
        normalized = normalize_report_payload(
            {
                "title": "Report",
                "report": body,
                "outline": [],
                "sources": [],
                "word_count": 398,
                "key_findings": [],
            },
            retrieved_urls=[],
        )
        self.assertEqual(normalized["word_count"], 251)


# ---------------------------------------------------------------------------
# Task 1 — structured persistence
# ---------------------------------------------------------------------------

class PersistenceTests(_SqliteCase):
    def _report(self):
        return ResearchReport(
            title="Structured Report",
            outline=["Introduction"],
            report="# Structured Report\n\nSolar panels convert sunlight into electricity.",
            sources=["https://example.com/solar"],
            word_count=999,
            generation_mode="json",
            key_findings=[
                {
                    "claim": "Solar panels convert sunlight into electricity.",
                    "source_ids": ["SRC-001"],
                    "citation_supported": True,
                }
            ],
            unsupported_citations=["https://never-retrieved.example/x"],
            source_details=[
                {
                    "source_id": "SRC-001",
                    "url": "https://example.com/solar",
                    "title": "Solar",
                    "excerpt": "Solar panels convert sunlight into electricity.",
                    "retrieval_method": "fetched_page",
                    "fetch_status": "ok",
                }
            ],
        )

    def _plan(self):
        return ResearchPlan(
            topic="solar", search_queries=["solar"], focus_areas=["energy"]
        )

    def test_new_report_round_trips_all_fields(self):
        db.init_database(self.config)
        sid = db.save_session(
            topic="solar",
            plan=self._plan(),
            facts=[],
            sources=["https://example.com/solar"],
            report=self._report(),
            status="SUCCESS",
            config=self.config,
            run_id="structured-roundtrip",
        )
        record = db.get_session_by_id(sid, self.config)
        report = record["report"]
        self.assertEqual(len(report["key_findings"]), 1)
        self.assertEqual(
            report["key_findings"][0]["source_ids"], ["SRC-001"]
        )
        self.assertEqual(
            report["unsupported_citations"], ["https://never-retrieved.example/x"]
        )
        self.assertEqual(report["source_details"][0]["source_id"], "SRC-001")
        self.assertEqual(
            report["word_count"],
            count_words("# Structured Report\n\nSolar panels convert sunlight into electricity."),
        )

    def test_legacy_null_columns_load_as_empty_lists(self):
        db.init_database(self.config)
        session = db.get_session(self.config)
        try:
            row = db.ResearchSession(topic="legacy", status="complete")
            session.add(row)
            session.flush()
            session.add(
                db.ResearchReportRow(
                    session_id=row.id,
                    title="Legacy",
                    outline=[],
                    report_content="# Legacy\n\nOld body text",
                    sources=[],
                    word_count=999,
                )
            )
            session.commit()
            sid = row.id
        finally:
            session.close()

        report = db.get_session_by_id(sid, self.config)["report"]
        self.assertEqual(report["key_findings"], [])
        self.assertEqual(report["unsupported_citations"], [])
        self.assertEqual(report["source_details"], [])
        self.assertEqual(report["stored_word_count"], 999)
        self.assertEqual(report["word_count"], 4)

    def test_duplicate_run_does_not_duplicate_report(self):
        db.init_database(self.config)
        kwargs = dict(
            topic="solar",
            plan=self._plan(),
            facts=[],
            sources=[],
            report=self._report(),
            status="SUCCESS",
            config=self.config,
            run_id="duplicate-run",
        )
        first = db.save_session(**kwargs)
        second = db.save_session(**kwargs)
        self.assertEqual(first, second)

        session = db.get_session(self.config)
        try:
            self.assertEqual(session.query(db.ResearchSession).count(), 1)
            self.assertEqual(session.query(db.ResearchReportRow).count(), 1)
        finally:
            session.close()

    def test_failed_write_rolls_back_without_partial_rows(self):
        db.init_database(self.config)

        class Unserializable:
            pass

        report = self._report()
        report.key_findings = [{"claim": "x", "bad": Unserializable()}]

        with self.assertRaises(Exception):
            db.save_session(
                topic="broken",
                plan=self._plan(),
                facts=[],
                sources=[],
                report=report,
                status="SUCCESS",
                config=self.config,
                run_id="failed-write",
            )

        session = db.get_session(self.config)
        try:
            self.assertEqual(session.query(db.ResearchSession).count(), 0)
            self.assertEqual(session.query(db.ResearchReportRow).count(), 0)
        finally:
            session.close()

    def test_migration_adds_columns_to_existing_table(self):
        engine = db.get_engine(self.config)
        db.Base.metadata.create_all(engine)
        with engine.begin() as conn:
            for column in ("key_findings", "unsupported_citations", "source_details"):
                conn.execute(text(f"ALTER TABLE research_reports DROP COLUMN {column}"))
        existing = {
            column["name"]
            for column in inspect(engine).get_columns("research_reports")
        }
        self.assertFalse(
            {"key_findings", "unsupported_citations", "source_details"} & existing
        )

        db.init_database(self.config)
        migrated = {
            column["name"]
            for column in inspect(engine).get_columns("research_reports")
        }
        self.assertTrue(
            {"key_findings", "unsupported_citations", "source_details"} <= migrated
        )
        # Idempotent: a second run must not fail or duplicate anything.
        db.init_database(self.config)


# ---------------------------------------------------------------------------
# Task 3 — evidence-based factual support
# ---------------------------------------------------------------------------

REGISTRY = [
    {
        "source_id": "SRC-001",
        "title": "Solar power",
        "excerpt": (
            "Solar panels convert sunlight into electricity using photovoltaic cells."
        ),
    },
    {
        "source_id": "SRC-002",
        "title": "Wind power",
        "excerpt": "Wind turbines generate electricity from moving air.",
    },
]


def _finding(claim, source_ids):
    return {"claim": claim, "source_ids": source_ids}


class FactualSupportTests(unittest.TestCase):
    def test_fully_supported_claim(self):
        component = evidence_factual_support(
            "",
            [
                _finding(
                    "Solar panels convert sunlight into electricity",
                    ["SRC-001"],
                )
            ],
            REGISTRY,
        )
        self.assertEqual(component["status"], "measured")
        self.assertEqual(component["details"]["claims_supported"], 1)
        self.assertEqual(component["score"], 1.0)

    def test_unsupported_claim(self):
        component = evidence_factual_support(
            "",
            [
                _finding(
                    "Hydropower dams flood valleys and displace communities",
                    ["SRC-001"],
                )
            ],
            REGISTRY,
        )
        self.assertEqual(component["details"]["claims_unsupported"], 1)
        self.assertEqual(component["score"], 0.0)

    def test_partially_supported_claim(self):
        component = evidence_factual_support(
            "",
            [
                _finding(
                    "Solar panels convert hydrogen fuel through electrolysis",
                    ["SRC-001"],
                )
            ],
            REGISTRY,
        )
        self.assertEqual(
            component["details"]["claims_partially_supported"], 1
        )
        self.assertEqual(component["score"], 0.5)

    def test_missing_sources_are_unverifiable_not_false(self):
        component = evidence_factual_support(
            "", [_finding("Solar panels convert sunlight", [])], REGISTRY
        )
        self.assertEqual(component["details"]["claims_unverifiable"], 1)
        self.assertIsNone(component["score"])
        self.assertEqual(component["details"]["reason"], "insufficient_evidence")
        self.assertEqual(
            component["details"]["claim_results"][0]["reason"],
            "no_source_reference",
        )

    def test_invalid_citation_is_unverifiable(self):
        component = evidence_factual_support(
            "", [_finding("Solar panels convert sunlight", ["SRC-999"])], REGISTRY
        )
        record = component["details"]["claim_results"][0]
        self.assertEqual(record["classification"], "unverifiable")
        self.assertEqual(record["reason"], "invalid_source_reference")
        self.assertEqual(record["invalid_source_ids"], ["SRC-999"])

    def test_empty_report_is_not_measurable(self):
        component = evidence_factual_support("", [], REGISTRY)
        self.assertIsNone(component["score"])
        self.assertEqual(component["details"]["reason"], "empty_report")

    def test_insufficient_evidence_is_unverifiable(self):
        thin_registry = [
            {"source_id": "SRC-001", "title": "Solar", "excerpt": "Solar"}
        ]
        component = evidence_factual_support(
            "",
            [_finding("Solar panels convert sunlight into electricity", ["SRC-001"])],
            thin_registry,
        )
        record = component["details"]["claim_results"][0]
        self.assertEqual(record["classification"], "unverifiable")
        self.assertEqual(record["reason"], "insufficient_source_evidence")
        self.assertIsNone(component["score"])

    def test_sentence_fallback_when_no_findings(self):
        component = evidence_factual_support(
            "Solar panels convert sunlight into electricity. "
            "Hydropower dams flood valleys and displace communities.",
            [],
            REGISTRY,
        )
        self.assertEqual(component["details"]["claims_total"], 2)
        self.assertEqual(component["details"]["claims_supported"], 1)
        # No retrieved source matches the second claim: it is unverifiable,
        # not counted as a false claim.
        self.assertEqual(component["details"]["claims_unverifiable"], 1)
        self.assertEqual(component["score"], 1.0)

    def test_open_topic_evaluation_measures_factual_support(self):
        report = ResearchReport(
            title="Solar",
            outline=["Introduction"],
            report=(
                "# Solar\n\n## Introduction\n"
                "Solar panels convert sunlight into electricity using photovoltaic cells."
            ),
            sources=["https://example.com/solar"],
            word_count=0,
            key_findings=[
                _finding(
                    "Solar panels convert sunlight into electricity using photovoltaic cells.",
                    ["SRC-001"],
                )
            ],
        )
        record = evaluate_open_state(
            "solar power",
            {
                "plan": ResearchPlan(
                    topic="solar power",
                    search_queries=["solar"],
                    focus_areas=["electricity"],
                ),
                "report": report,
                "sources": ["https://example.com/solar"],
                "source_registry": REGISTRY,
                "grounded_sources_count": 1,
                "citation_grounding_ratio": 1.0,
                "unsupported_citations": [],
            },
            plan=ResearchPlan(
                topic="solar power",
                search_queries=["solar"],
                focus_areas=["electricity"],
            ),
        )
        self.assertEqual(record["mode"], "open_topic")
        self.assertEqual(record["metrics"]["factual_support"]["status"], "measured")
        self.assertEqual(record["metrics"]["factual_support"]["score"], 1.0)
        self.assertIn("factual_support", record["measured_components"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
