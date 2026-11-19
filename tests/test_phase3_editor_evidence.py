"""Phase 3 tests for deterministic Editor evidence selection and budgeting.

These tests are standard-library only. They exercise source prioritisation,
subtopic diversity, near-duplicate suppression, budget enforcement, stable
source-ID preservation, and the character/token telemetry used by the Editor.

Run:
    python tests/test_phase3_editor_evidence.py
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.config import ResearchConfig
from tools.evidence_selection import (
    EVIDENCE_SELECTION_VERSION,
    EditorEvidenceBudget,
    build_compact_evidence_context,
    select_editor_evidence,
)


def source(
    source_id: str,
    title: str,
    excerpt: str,
    *,
    query: str,
    url: str | None = None,
    rank_score: float = 0.5,
    authority: float = 0.5,
    relevance: float = 0.5,
    retrieval_method: str = "fetched_page",
    fetch_status: str = "ok",
    source_type: str = "independent_blog",
) -> dict:
    return {
        "source_id": source_id,
        "url": url or f"https://example.com/{source_id.lower()}",
        "canonical_url": f"https://example.com/{source_id.lower()}",
        "original_url": url or f"https://example.com/{source_id.lower()}",
        "title": title,
        "queries": [query],
        "retrieval_method": retrieval_method,
        "fetch_status": fetch_status,
        "excerpt": excerpt,
        "relevance": relevance,
        "rank_score": rank_score,
        "authority_score": authority,
        "source_type": source_type,
    }


def summaries_for(records, query):
    by_query = {}
    for record in records:
        by_query.setdefault(query, []).append(record)
    return [
        {
            "query": query,
            "evidence_items": [
                {
                    "canonical_url": item["canonical_url"],
                    "url": item["url"],
                    "title": item["title"],
                    "excerpt": item["excerpt"],
                    "retrieval_method": item["retrieval_method"],
                    "fetch_status": item["fetch_status"],
                    "relevance": item["relevance"],
                    "rank_score": item["rank_score"],
                }
                for item in items
            ],
        }
        for query, items in by_query.items()
    ]


class BudgetTests(unittest.TestCase):
    def test_from_config_uses_defaults_and_clamps(self):
        budget = EditorEvidenceBudget.from_config(ResearchConfig())
        self.assertEqual(budget.max_sources, 12)
        self.assertEqual(budget.min_sources, 3)
        self.assertEqual(budget.max_context_chars, 14000)
        self.assertEqual(budget.excerpt_chars, 700)

        config = ResearchConfig()
        config.editor_max_sources = 2
        config.editor_min_sources = 9
        clamped = EditorEvidenceBudget.from_config(config)
        self.assertLessEqual(clamped.min_sources, clamped.max_sources)

    def test_invalid_numeric_config_falls_back_safely(self):
        config = ResearchConfig()
        config.editor_max_sources = 0
        config.editor_max_context_chars = "not-a-number"
        budget = EditorEvidenceBudget.from_config(config)
        self.assertGreaterEqual(budget.max_sources, 1)
        self.assertGreaterEqual(budget.max_context_chars, 1)


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.records = [
            source(
                "SRC-001",
                "Primary paper on retrieval",
                "Retrieval augmented generation combines a retriever with a generator.",
                query="rag architecture",
                rank_score=0.9,
                authority=1.0,
            ),
            source(
                "SRC-002",
                "Secondary blog on retrieval",
                "A blog introduction to retrieval augmented generation.",
                query="rag architecture",
                rank_score=0.3,
                authority=0.3,
            ),
            source(
                "SRC-003",
                "Evaluation study",
                "Evaluation of retrieval augmented generation measures faithfulness.",
                query="rag evaluation",
                rank_score=0.8,
                authority=0.9,
            ),
            source(
                "SRC-004",
                "Unrelated marketing page",
                "Buy our product today.",
                query="rag evaluation",
                rank_score=0.1,
                authority=0.2,
                relevance=0.0,
            ),
        ]
        self.summaries = [
            {
                "query": "rag architecture",
                "evidence_items": [self.records[0], self.records[1]],
            },
            {
                "query": "rag evaluation",
                "evidence_items": [self.records[2], self.records[3]],
            },
        ]

    def test_selects_diverse_sources_and_preserves_ids(self):
        result = select_editor_evidence(
            self.summaries,
            self.records,
            EditorEvidenceBudget(max_sources=2, min_sources=1),
        )
        selected = result["selected"]
        self.assertEqual(len(selected), 2)
        self.assertEqual(
            {record["source_id"] for record in selected}, {"SRC-001", "SRC-003"}
        )
        # Stable IDs from the registry are copied unchanged.
        self.assertIn("[SRC-001]", result["context"])
        self.assertIn("[SRC-003]", result["context"])

    def test_high_quality_source_is_selected_first(self):
        result = select_editor_evidence(
            self.summaries,
            self.records,
            EditorEvidenceBudget(max_sources=1, min_sources=1),
        )
        self.assertEqual(result["selected"][0]["source_id"], "SRC-001")

    def test_near_duplicate_content_is_dropped(self):
        duplicate = source(
            "SRC-005",
            "Duplicate copy",
            "Retrieval augmented generation combines a retriever with a generator.",
            query="rag architecture",
            url="https://mirror.example.com/copy",
            rank_score=0.85,
            authority=0.9,
        )
        records = self.records + [duplicate]
        summaries = self.summaries + [
            {"query": "rag architecture", "evidence_items": [duplicate]}
        ]
        result = select_editor_evidence(
            summaries,
            records,
            EditorEvidenceBudget(max_sources=10, min_sources=1),
        )
        self.assertEqual(result["stats"]["near_duplicates_dropped"], 1)
        selected_ids = {record["source_id"] for record in result["selected"]}
        self.assertFalse({"SRC-001", "SRC-005"} <= selected_ids)

    def test_character_budget_keeps_minimum_sources(self):
        result = select_editor_evidence(
            self.summaries,
            self.records,
            EditorEvidenceBudget(
                max_sources=4,
                min_sources=2,
                max_context_chars=10,  # deliberately impossible
                excerpt_chars=700,
            ),
        )
        self.assertGreaterEqual(len(result["selected"]), 2)
        # The budget is reported honestly even when the floor cannot be met.
        self.assertGreater(result["stats"]["chars_after"], 10)
        self.assertIn("budget", result["stats"])

    def test_telemetry_reports_before_and_after(self):
        result = select_editor_evidence(
            self.summaries,
            self.records,
            EditorEvidenceBudget(max_sources=3, min_sources=1),
        )
        stats = result["stats"]
        self.assertEqual(stats["version"], EVIDENCE_SELECTION_VERSION)
        self.assertEqual(stats["sources_before"], 4)
        self.assertEqual(stats["sources_after"], 3)
        self.assertGreater(stats["chars_before"], stats["chars_after"])
        self.assertGreaterEqual(stats["approx_tokens_after"], 0)
        self.assertIn("characters/4", stats["token_estimate_method"])
        self.assertEqual(len(stats["selected_source_ids"]), 3)

    def test_fetched_page_outranks_equivalent_snippet(self):
        fetched = source(
            "SRC-010",
            "Fetched source",
            "Full page text about speculative decoding.",
            query="speculative decoding",
            rank_score=0.5,
            authority=0.5,
            relevance=0.5,
            retrieval_method="fetched_page",
            fetch_status="ok",
        )
        snippet = source(
            "SRC-011",
            "Snippet source",
            "Snippet text about speculative decoding.",
            query="speculative decoding",
            rank_score=0.5,
            authority=0.5,
            relevance=0.5,
            retrieval_method="search_snippet",
            fetch_status="not_attempted",
        )
        result = select_editor_evidence(
            [{"query": "speculative decoding", "evidence_items": [snippet, fetched]}],
            [snippet, fetched],
            EditorEvidenceBudget(max_sources=1, min_sources=1),
        )
        self.assertEqual(result["selected"][0]["source_id"], "SRC-010")
        self.assertEqual(result["stats"]["after"]["fetched"], 1)
        self.assertEqual(result["stats"]["after"]["snippet_only"], 0)

    def test_compact_context_does_not_truncate_mid_word(self):
        long_excerpt = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu"
        record = source(
            "SRC-020",
            "Long excerpt",
            long_excerpt,
            query="truncation",
        )
        context = build_compact_evidence_context(
            [record], [{"query": "truncation", "evidence_items": [record]}], 25
        )
        evidence_line = [
            line for line in context.splitlines() if line.startswith("Evidence excerpt:")
        ][0]
        evidence = evidence_line.split(":", 1)[1].strip()
        self.assertTrue(evidence.endswith("…"))
        self.assertNotIn("lambd…", evidence)
        self.assertTrue(evidence.rstrip(" …").endswith(("alpha", "beta", "gamma", "delta")))

    def test_empty_registry_returns_honest_empty_context(self):
        result = select_editor_evidence([], [], EditorEvidenceBudget())
        self.assertEqual(result["selected"], [])
        self.assertIn("No usable evidence", result["context"])
        self.assertEqual(result["stats"]["sources_before"], 0)
        self.assertEqual(result["stats"]["sources_after"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
