"""Phase 3 evaluation-engine test suite (standard library only).

Covers:
- documented metric definitions and weighting;
- heading-based structural completeness;
- citation precision vs. claim-citation coverage;
- evidence coverage from retrieval/grounding counts;
- factual-support scoring that excludes and preserves unresolved claims;
- benchmark dataset loading, hashing and readiness counts;
- open-topic evaluation leaving factual support unmeasured;
- append-only JSONL persistence and deterministic digests.

No network, database, Ollama, or application pipeline is used.

Run:
    python tests/test_phase3_evaluation.py
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.engine import (
    dataset_readiness,
    evaluate_open_state,
    evaluate_report,
    evaluate_state,
    get_question,
    load_benchmark_dataset,
    record_digest,
)
from evaluation.metrics import (
    CANONICAL_SECTIONS,
    claim_support,
    citation_quality,
    completeness,
    evidence_coverage,
    overall_score,
    relevance,
    section_presence,
    text_coverage,
)
from evaluation.store import (
    append_evaluation_record,
    load_evaluation_records,
    summarise_records,
)

DATASET_PATH = PROJECT_ROOT / "benchmarks" / "datasets" / "benchmark_v3.json"
V3_SHA256 = "10a5ba16c400a1cac0040e4357df49d97fc09e258f006999df96c93df2ff2527"

COMPLETE_REPORT = """# Test Report

## Introduction
Retrieval augmented generation combines parametric memory with retrieval
so that external documents can be updated without retraining the model.

## Key Points
- Chunking splits source documents into retrievable units.
- Vector indexing supports dense retrieval over embedded chunks.
- Prompt augmentation inserts retrieved passages into the model context.

## Applications
Enterprise question answering and technical support over internal documents.

## Challenges
Retrieval quality bounds answer quality and chunking trades recall for latency.

## Conclusion
Careful chunking, indexing, retrieval and augmentation are all required.

## References
1. https://arxiv.org/abs/2005.11401
"""


class MetricDefinitionTests(unittest.TestCase):
    def test_text_coverage_is_bounded_and_symmetric_for_identical_text(self):
        self.assertEqual(text_coverage("alpha beta gamma", "alpha beta"), 1.0)
        self.assertEqual(text_coverage("alpha beta", "gamma delta"), 0.0)
        self.assertGreater(text_coverage("alpha beta gamma", "alpha delta"), 0.0)

    def test_relevance_reports_aspect_and_topic_details(self):
        component = relevance(
            "Retrieval augmented generation uses chunking and vector indexing.",
            "retrieval augmented generation",
            ["Chunking", "Vector Indexing"],
        )
        self.assertEqual(component["status"], "measured")
        self.assertLessEqual(component["score"], 1.0)
        self.assertEqual(
            set(component["details"]["aspect_coverage"]), {"Chunking", "Vector Indexing"}
        )

    def test_section_presence_requires_markdown_headings(self):
        prose_only = "This text discusses Introduction, Key Points and Conclusion."
        self.assertFalse(any(section_presence(prose_only).values()))
        presence = section_presence(COMPLETE_REPORT)
        self.assertTrue(all(presence[name] for name in CANONICAL_SECTIONS))

    def test_completeness_full_fixture_scores_one(self):
        component = completeness(COMPLETE_REPORT, CANONICAL_SECTIONS, 50)
        self.assertEqual(component["score"], 1.0)
        self.assertEqual(component["details"]["section_ratio"], 1.0)

    def test_completeness_missing_headings_reduces_score(self):
        without_sections = "word " * 300
        component = completeness(without_sections, CANONICAL_SECTIONS, 250)
        self.assertEqual(component["details"]["section_ratio"], 0.0)
        self.assertEqual(component["score"], 0.25)

    def test_citation_quality_penalises_unsupported_urls(self):
        good = citation_quality(["https://a.example"], [], 1.0)
        bad = citation_quality(["https://a.example"], ["https://fake.invalid"], 1.0)
        self.assertEqual(good["score"], 1.0)
        self.assertLess(bad["score"], good["score"])
        self.assertEqual(bad["details"]["citation_precision"], 0.5)

    def test_citation_quality_without_citations_is_explicit(self):
        component = citation_quality([], [])
        self.assertEqual(component["status"], "no_citations")
        self.assertEqual(component["score"], 0.0)

    def test_evidence_coverage_uses_supplied_counts(self):
        component = evidence_coverage(
            {"search_results": 10, "usable_evidence": 5},
            {"retrieved_sources_count": 4, "grounded_sources_count": 2},
        )
        self.assertEqual(component["score"], 0.5)
        self.assertIn("retrieval_usability", component["details"])

    def test_evidence_coverage_not_measurable_without_counts(self):
        component = evidence_coverage({}, {})
        self.assertIsNone(component["score"])
        self.assertEqual(component["status"], "not_measurable")

    def test_claim_support_excludes_and_preserves_unresolved_claims(self):
        claims = [
            {"claim_id": "C1", "status": "verified", "effective_claim_text": "alpha beta gamma"},
            {"claim_id": "C2", "status": "partially_supported", "effective_claim_text": "delta epsilon"},
            {"claim_id": "C3", "status": "needs_more_evidence", "effective_claim_text": "zeta eta"},
        ]
        component = claim_support("alpha beta gamma only", claims, threshold=0.55)
        self.assertEqual(component["details"]["verified_claims"], 1)
        self.assertEqual(component["details"]["supported_verified_claims"], 1)
        self.assertEqual(component["score"], 1.0)
        ids = {item["claim_id"] for item in component["details"]["unresolved_claims"]}
        self.assertEqual(ids, {"C2", "C3"})

    def test_claim_support_without_verified_claims_is_not_measurable(self):
        component = claim_support(
            "anything",
            [{"claim_id": "C1", "status": "partially_supported", "effective_claim_text": "x"}],
        )
        self.assertIsNone(component["score"])
        self.assertEqual(component["status"], "not_measurable")

    def test_overall_score_renormalises_measured_weights(self):
        score, weights = overall_score(
            {
                "relevance": {"score": 1.0},
                "completeness": {"score": 1.0},
                "factual_support": {"score": None},
            }
        )
        self.assertEqual(score, 1.0)
        self.assertNotIn("factual_support", weights)
        self.assertAlmostEqual(sum(weights.values()), 1.0, places=4)

    def test_overall_score_is_none_when_nothing_measured(self):
        score, weights = overall_score({"factual_support": {"score": None}})
        self.assertIsNone(score)
        self.assertEqual(weights, {})


class EngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dataset = load_benchmark_dataset(DATASET_PATH)

    def test_dataset_loads_with_frozen_hash(self):
        self.assertEqual(self.dataset["sha256"], V3_SHA256)
        self.assertEqual(self.dataset["filename"], "benchmark_v3.json")
        self.assertEqual(len(self.dataset["questions"]), 40)

    def test_get_question_returns_record_and_unknown_id_raises(self):
        question = get_question(self.dataset, "Q01")
        self.assertEqual(question["id"], "Q01")
        with self.assertRaises(KeyError):
            get_question(self.dataset, "Q99")

    def test_readiness_reports_unresolved_claims_without_promoting_them(self):
        readiness = dataset_readiness(self.dataset)
        self.assertEqual(readiness["questions_total"], 40)
        self.assertEqual(readiness["claims_total"], 120)
        self.assertEqual(readiness["verified_claims"], 100)
        self.assertEqual(readiness["unresolved_claims"], 20)
        self.assertEqual(readiness["verified_questions"], 25)
        self.assertIn("Q17-C2", readiness["unresolved_claim_ids"])

    def test_evaluate_report_produces_all_components(self):
        question = get_question(self.dataset, "Q01")
        record = evaluate_report(
            question,
            {
                "title": "Test Report",
                "report": COMPLETE_REPORT,
                "sources": ["https://arxiv.org/abs/2005.11401"],
                "unsupported_citations": [],
                "key_findings": [{"claim": "x", "source_ids": ["S1"]}],
            },
            dataset=self.dataset,
            grounding={"retrieved_sources_count": 2, "grounded_sources_count": 1},
            research_metrics={"search_results": 4, "usable_evidence": 2},
            claim_citation_coverage=1.0,
            evaluated_at="2026-11-13T00:00:00+00:00",
        )
        self.assertEqual(record["mode"], "benchmark")
        self.assertEqual(record["dataset"]["sha256"], V3_SHA256)
        self.assertIsNotNone(record["overall_score"])
        for name in (
            "relevance",
            "evidence_coverage",
            "citation_quality",
            "completeness",
            "factual_support",
        ):
            self.assertIn(name, record["metrics"])
        self.assertEqual(record["metrics"]["completeness"]["details"]["section_ratio"], 1.0)
        self.assertGreater(record["metrics"]["completeness"]["score"], 0.75)
        self.assertIn("not", record["metrics"]["factual_support"]["method"].lower())

    def test_evaluate_state_uses_grounding_counts(self):
        question = get_question(self.dataset, "Q01")
        state = {
            "report": {"report": COMPLETE_REPORT, "sources": [], "unsupported_citations": []},
            "sources": ["https://a.example", "https://b.example"],
            "grounded_sources_count": 1,
            "citation_grounding_ratio": 0.5,
            "claim_citation_coverage": 0.5,
            "research_metrics": {"search_results": 4, "usable_evidence": 3},
        }
        record = evaluate_state(question, state, dataset=self.dataset)
        self.assertEqual(
            record["metrics"]["evidence_coverage"]["details"]["citation_grounding"][
                "retrieved_sources"
            ],
            2,
        )

    def test_state_phantom_urls_reduce_citation_quality(self):
        question = get_question(self.dataset, "Q01")
        state = {
            "report": {
                "report": COMPLETE_REPORT,
                "sources": ["https://a.example"],
                "unsupported_citations": [],
            },
            "sources": ["https://a.example"],
            "grounded_sources_count": 1,
            "claim_citation_coverage": 1.0,
            "unsupported_citations": ["https://fabricated.invalid/source"],
            "research_metrics": {"search_results": 1, "usable_evidence": 1},
        }
        record = evaluate_state(question, state, dataset=self.dataset)
        details = record["metrics"]["citation_quality"]["details"]
        self.assertEqual(details["unsupported_citations"], 1)
        self.assertEqual(details["citation_precision"], 0.5)
        self.assertLess(record["metrics"]["citation_quality"]["score"], 1.0)

    def test_open_topic_without_source_evidence_is_not_measurable(self):
        state = {
            "plan": {
                "topic": "Vector databases",
                "focus_areas": ["Indexing", "Retrieval"],
            },
            "report": {
                "report": COMPLETE_REPORT,
                "sources": ["https://a.example"],
                "unsupported_citations": [],
            },
            "sources": ["https://a.example"],
            "source_registry": [],
            "grounded_sources_count": 1,
            "research_metrics": {"search_results": 1, "usable_evidence": 1},
        }
        record = evaluate_open_state("Vector databases", state)
        self.assertEqual(record["mode"], "open_topic")
        self.assertIsNone(record["metrics"]["factual_support"]["score"])
        self.assertEqual(
            record["metrics"]["factual_support"]["details"]["reason"],
            "insufficient_evidence",
        )
        self.assertIn("factual_support", record["unmeasured_components"])
        self.assertIsNotNone(record["overall_score"])
        self.assertTrue(
            any("unverifiable" in note for note in record["methodology_notes"])
        )

    def test_record_digest_is_deterministic_and_time_independent(self):
        question = get_question(self.dataset, "Q01")
        first = evaluate_report(
            question,
            {"report": COMPLETE_REPORT},
            dataset=self.dataset,
            evaluated_at="2026-11-13T00:00:00+00:00",
        )
        second = evaluate_report(
            question,
            {"report": COMPLETE_REPORT},
            dataset=self.dataset,
            evaluated_at="2026-11-14T12:34:56+00:00",
        )
        self.assertEqual(record_digest(first), record_digest(second))


class StoreTests(unittest.TestCase):
    def test_append_is_additive_and_summary_keeps_unmeasured_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "records.jsonl"
            measured = {
                "overall_score": 0.5,
                "metrics": {
                    "relevance": {"score": 0.5},
                    "factual_support": {"score": None},
                    "citation_quality": {"score": 0.25, "details": {"unsupported_citations": 2}},
                },
                "unresolved_claims": [{"claim_id": "C1"}],
            }
            unmeasured = {
                "overall_score": None,
                "metrics": {"factual_support": {"score": None}},
                "unresolved_claims": [],
            }
            append_evaluation_record(measured, path)
            append_evaluation_record(unmeasured, path)
            records = load_evaluation_records(path)
            self.assertEqual(len(records), 2)
            self.assertIn("record_digest", records[0])

            summary = summarise_records(records)
            self.assertEqual(summary["records"], 2)
            self.assertEqual(summary["overall_score_mean"], 0.5)
            self.assertNotIn("factual_support", summary["metric_means"])
            self.assertEqual(summary["measured_counts"]["relevance"], 1)
            self.assertEqual(summary["unresolved_claims_reported"], 1)
            self.assertEqual(summary["unsupported_citations_reported"], 2)

    def test_missing_file_loads_empty_and_malformed_lines_are_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "records.jsonl"
            self.assertEqual(load_evaluation_records(path), [])
            path.write_text('{"overall_score": 0.1}\nnot json\n', encoding="utf-8")
            records = load_evaluation_records(path)
            self.assertEqual(len(records), 1)


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    result = unittest.main(exit=False, verbosity=2).result
    total = result.testsRun
    failures = len(result.failures) + len(result.errors)
    print(f"\nTests run: {total}")
    print("RESULT:", "PASS" if failures == 0 else "FAIL")
    sys.exit(0 if failures == 0 else 1)
