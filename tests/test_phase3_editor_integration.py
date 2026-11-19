"""Phase 3 integration tests for the Editor evidence budget and citation flow.

Ollama is replaced with deterministic fakes. No network, database, or live
pipeline is used.

Run:
    python tests/test_phase3_editor_integration.py
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agents.editor_agent import run_editor_agent
from config.config import ResearchConfig
from schemas.models import ResearchPlan, ResearchReport
from tools.report_parsing import count_words
from tools.research_evidence import canonical_url


PLAN = ResearchPlan(
    topic="Retrieval Augmented Generation",
    search_queries=["rag architecture", "rag evaluation"],
    focus_areas=["retrieval", "generation"],
)


def source(
    source_id,
    url,
    title,
    excerpt,
    query,
    *,
    rank_score=0.8,
    authority_score=0.8,
    method="fetched_page",
    status="ok",
):
    return {
        "source_id": source_id,
        "url": url,
        "canonical_url": canonical_url(url),
        "original_url": url,
        "title": title,
        "excerpt": excerpt,
        "queries": [query],
        "retrieval_method": method,
        "fetch_status": status,
        "source_type": "academic",
        "relevance": 0.7,
        "rank_score": rank_score,
        "authority_score": authority_score,
    }


def summaries(records):
    grouped = {}
    for record in records:
        grouped.setdefault(record["queries"][0], []).append(record)
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
        for query, items in grouped.items()
    ]


REGISTRY = [
    source(
        "SRC-001",
        "https://example.com/rag",
        "Retrieval Augmented Generation",
        "Retrieval augmented generation combines information retrieval with text generation to improve factual accuracy.",
        "rag architecture",
    ),
    source(
        "SRC-002",
        "https://example.com/medusa",
        "Medusa speculative decoding",
        "Medusa adds parallel decoding heads that predict several subsequent tokens in a tree structured pass.",
        "rag evaluation",
    ),
    source(
        "SRC-003",
        "https://example.com/snippet",
        "Snippet source",
        "A short snippet about inference acceleration.",
        "rag evaluation",
        rank_score=0.4,
        authority_score=0.4,
        method="search_snippet",
        status="not_attempted",
    ),
]

PAYLOAD = {
    "title": "Retrieval Augmented Generation",
    "outline": [
        "Introduction",
        "Key Points",
        "Applications",
        "Challenges",
        "Conclusion",
        "References",
    ],
    "report": (
        "# Retrieval Augmented Generation\n\n"
        "## Introduction\nRetrieval augmented generation combines retrieval and generation.\n\n"
        "## Key Points\n- Retrieval finds evidence.\n- Generation writes the answer.\n\n"
        "## Applications\n- Question answering.\n\n"
        "## Challenges\n- Retrieval quality matters.\n\n"
        "## Conclusion\nRAG is a useful architecture.\n\n"
        "## References\n1. [SRC-001] RAG – https://example.com/rag"
    ),
    "key_findings": [
        {
            "claim": "Retrieval augmented generation combines information retrieval with text generation",
            "source_ids": ["SRC-001"],
            "support": "combines information retrieval with text generation",
        },
        {
            "claim": "Medusa adds parallel decoding heads that predict several subsequent tokens",
            "source_ids": ["SRC-999"],
            "support": "parallel decoding heads",
        },
    ],
    "sources": ["https://example.com/rag/", "https://fake.example/never-retrieved"],
    "word_count": 999,
}


def config(**overrides):
    cfg = ResearchConfig()
    cfg.editor_max_sources = 2
    cfg.editor_min_sources = 1
    cfg.editor_max_context_chars = 14000
    cfg.editor_excerpt_chars = 300
    cfg.editor_max_json_tokens = 777
    cfg.editor_bind_citations = True
    cfg.editor_append_limitations = True
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg


class EditorIntegrationTests(unittest.TestCase):
    def test_json_path_applies_budget_binding_and_telemetry(self):
        with patch(
            "agents.editor_agent.ollama_generate_json", return_value=dict(PAYLOAD)
        ) as mocked:
            report = run_editor_agent(
                PLAN, summaries(REGISTRY), config(), source_registry=REGISTRY
            )

        self.assertIsInstance(report, ResearchReport)
        self.assertEqual(report.generation_mode, "json")
        self.assertEqual(mocked.call_args.kwargs["max_tokens"], 777)

        stats = report._editor_context_stats
        self.assertLessEqual(stats["sources_after"], 2)
        self.assertEqual(stats["sources_before"], 3)
        self.assertLessEqual(len(report.source_details), 2)
        self.assertEqual(stats["llm_calls"], 1)
        self.assertIn("citation_integrity", stats)

        # Invalid citation is removed; the valid Medusa source is bound from
        # the selected evidence because it actually covers the claim.
        second = report.key_findings[1]
        self.assertEqual(second["invalid_source_ids"], ["SRC-999"])
        self.assertIn("SRC-002", second["source_ids"])
        self.assertEqual(second["bound_source_ids"], ["SRC-002"])
        self.assertEqual(second["support_status"], "supported")

        # Displayed sources are retrieved URLs only, URL variants collapse, and
        # every cited source is displayed.
        self.assertIn("https://example.com/medusa", report.sources)
        self.assertNotIn("https://fake.example/never-retrieved", report.sources)
        self.assertEqual(len(report.sources), len(set(report.sources)))

        # A truthful limitations note records the invalid reference.
        self.assertIn("## Limitations", report.report)
        self.assertIn("did not exist in the retrieved source registry", report.report)
        self.assertEqual(report.word_count, count_words(report.report))
        self.assertTrue(stats["limitations_section_added"])

    def test_binding_can_be_disabled(self):
        with patch(
            "agents.editor_agent.ollama_generate_json", return_value=dict(PAYLOAD)
        ):
            report = run_editor_agent(
                PLAN,
                summaries(REGISTRY),
                config(editor_bind_citations=False),
                source_registry=REGISTRY,
            )
        second = report.key_findings[1]
        self.assertEqual(second["bound_source_ids"], [])
        self.assertNotIn("SRC-002", second["source_ids"])
        self.assertNotEqual(second["support_status"], "supported")

    def test_context_budget_reduces_source_count(self):
        records = REGISTRY + [
            source(
                f"SRC-{index:03d}",
                f"https://example.com/extra-{index}",
                f"Extra source {index}",
                f"Additional retrieved evidence number {index} about retrieval.",
                "rag architecture",
                rank_score=0.2,
                authority_score=0.2,
            )
            for index in range(4, 8)
        ]
        cfg = config(editor_max_sources=3, editor_min_sources=2)
        with patch(
            "agents.editor_agent.ollama_generate_json", return_value=dict(PAYLOAD)
        ):
            report = run_editor_agent(
                PLAN, summaries(records), cfg, source_registry=records
            )
        stats = report._editor_context_stats
        self.assertEqual(stats["sources_before"], len(records))
        self.assertLessEqual(stats["sources_after"], 3)
        self.assertGreaterEqual(stats["sources_after"], 2)
        self.assertLess(stats["chars_after"], stats["chars_before"])
        self.assertLessEqual(len(report.source_details), 3)

    def test_no_evidence_returns_honest_report_and_stats(self):
        report = run_editor_agent(PLAN, [], config(), source_registry=[])
        self.assertIsInstance(report, ResearchReport)
        self.assertEqual(report.generation_mode, "no_evidence")
        self.assertEqual(report.sources, [])
        self.assertEqual(report.source_details, [])
        self.assertEqual(report._editor_context_stats["generation_mode"], "no_evidence")
        self.assertEqual(report._editor_context_stats["llm_calls"], 0)

    def test_model_timeout_falls_back_to_markdown(self):
        markdown = (
            "# Fallback Report\n\n## Introduction\nText.\n\n## Key Points\n- One.\n\n"
            "## Applications\n- Two.\n\n## Challenges\n- Three.\n\n"
            "## Conclusion\nDone.\n\n## References\n1. [SRC-001] RAG – "
            "https://example.com/rag"
        )
        with patch(
            "agents.editor_agent.ollama_generate_json",
            side_effect=TimeoutError("model timed out"),
        ), patch(
            "agents.editor_agent.ollama_generate", return_value=markdown
        ) as mocked_markdown:
            report = run_editor_agent(
                PLAN, summaries(REGISTRY), config(), source_registry=REGISTRY
            )
        self.assertIsInstance(report, ResearchReport)
        self.assertEqual(report.generation_mode, "markdown_fallback")
        self.assertTrue(report._editor_context_stats["json_path_failed"])
        self.assertEqual(report._editor_context_stats["llm_calls"], 2)
        self.assertEqual(mocked_markdown.call_args.kwargs["max_tokens"], 700)

    def test_completely_failed_generation_returns_raw_fallback(self):
        with patch(
            "agents.editor_agent.ollama_generate_json",
            side_effect=TimeoutError("json timed out"),
        ), patch(
            "agents.editor_agent.ollama_generate",
            side_effect=TimeoutError("markdown timed out"),
        ):
            report = run_editor_agent(
                PLAN, summaries(REGISTRY), config(), source_registry=REGISTRY
            )
        self.assertIsInstance(report, str)
        self.assertIn("Report generation failed", report)


if __name__ == "__main__":
    unittest.main(verbosity=2)
