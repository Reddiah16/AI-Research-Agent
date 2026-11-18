"""Phase 2 research-quality test suite (standard library only).

Covers:
- HTML extraction and URL safety;
- search-result normalisation, deduplication, relevance and provenance;
- snippet/page evidence assembly and fetch-failure fallback;
- source registry and evidence-context formatting;
- JSON parsing, truncated-JSON repair and citation sanitisation;
- citation-grounding and claim-citation coverage;
- an end-to-end pure evidence → report-payload → grounding flow.

No network, database, Ollama, DuckDuckGo, or application pipeline is used.

Run:
    python tests/test_phase2_research_quality.py
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.grounding import (
    analyze_claim_citations,
    analyze_source_grounding,
    normalize_url,
)
from tools.page_fetcher import fetch_page, html_to_text, is_safe_fetch_url
from tools.report_parsing import (
    extract_json_object,
    is_safe_citation_url,
    load_json_payload,
    normalize_report_payload,
    parse_markdown_report,
    repair_truncated_json,
    split_citations,
    strip_code_fences,
)
from tools.research_evidence import (
    assemble_summary,
    build_query_evidence,
    build_source_registry,
    canonical_url,
    extract_relevant_excerpt,
    format_evidence_context,
    normalize_results,
    relevance_score,
    source_fingerprint,
)


SAMPLE_HTML = """
<html><head><title>Vector Databases Explained</title>
<style>.hidden{display:none}</style></head>
<body>
<nav>Home | About</nav>
<h1>Vector databases</h1>
<p>Vector databases store embeddings and support similarity search.</p>
<script>alert('ignore me')</script>
<p>HNSW and IVF are common indexing strategies for approximate search.</p>
<footer>Copyright</footer>
</body></html>
"""

SAMPLE_RESULTS = [
    {
        "title": "Vector database guide",
        "url": "https://example.com/vector-db?utm_source=news",
        "snippet": "Vector databases store embeddings and support similarity search over dense vectors.",
    },
    {
        "title": "Duplicate result",
        "url": "https://example.com/vector-db#section",
        "snippet": "Duplicate URL with a fragment should be removed.",
    },
    {
        "title": "HNSW indexing",
        "url": "https://example.org/hnsw",
        "snippet": "HNSW is a graph-based approximate nearest neighbour index for vector search.",
    },
    {
        "title": "",
        "url": "",
        "snippet": "",
    },
    {"title": "Bad scheme", "url": "javascript:alert(1)", "snippet": "not a source"},
    "not-a-dictionary",
]


def _fetcher_ok(url: str):
    return {
        "ok": True,
        "title": "Fetched Vector Databases",
        "text": (
            "Vector databases store embeddings. HNSW provides approximate "
            "nearest neighbour search with bounded memory."
        ),
        "error": None,
    }


def _fetcher_fail(url: str):
    return {"ok": False, "text": "", "error": "network_error:URLError"}


class PageFetcherTests(unittest.TestCase):
    def test_html_extraction_skips_script_style_nav_footer(self):
        extracted = html_to_text(SAMPLE_HTML)
        self.assertEqual(extracted["title"], "Vector Databases Explained")
        self.assertIn("Vector databases store embeddings", extracted["text"])
        self.assertIn("HNSW and IVF", extracted["text"])
        self.assertNotIn("alert", extracted["text"])
        self.assertNotIn("Home | About", extracted["text"])
        self.assertNotIn("Copyright", extracted["text"])

    def test_malformed_html_is_tolerated(self):
        extracted = html_to_text("<html><body><p>Unclosed paragraph<div>More text")
        self.assertIn("Unclosed paragraph", extracted["text"])
        self.assertIn("More text", extracted["text"])

    def test_url_safety_rejects_local_and_non_http(self):
        self.assertTrue(is_safe_fetch_url("https://example.com/page"))
        self.assertTrue(is_safe_fetch_url("http://example.org"))
        self.assertFalse(is_safe_fetch_url("file:///etc/passwd"))
        self.assertFalse(is_safe_fetch_url("javascript:alert(1)"))
        self.assertFalse(is_safe_fetch_url("http://localhost:8000/admin"))
        self.assertFalse(is_safe_fetch_url("http://127.0.0.1/"))
        self.assertFalse(is_safe_fetch_url("http://10.0.0.5/"))
        self.assertFalse(is_safe_fetch_url("http://[::1]/"))

    def test_fetch_page_blocks_unsafe_url_without_network(self):
        result = fetch_page("file:///etc/passwd")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "blocked_or_invalid_url")

    def test_fetch_page_reports_network_failure_without_raising(self):
        with patch("tools.page_fetcher.build_opener", side_effect=URLError("offline")):
            result = fetch_page("https://example.com")
        self.assertFalse(result["ok"])
        self.assertIn("network_error", result["error"])

    def test_fetch_page_rejects_non_text_content_type(self):
        class FakeResponse:
            status = 200
            headers = {"Content-Type": "application/pdf"}

            def read(self, _n):
                return b"%PDF-1.7"

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        class FakeOpener:
            def open(self, request, timeout=0):
                return FakeResponse()

        with patch("tools.page_fetcher.build_opener", return_value=FakeOpener()):
            result = fetch_page("https://example.com/paper.pdf")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "unsupported_content_type:application/pdf")


class EvidenceTests(unittest.TestCase):
    def test_normalize_results_drops_malformed_and_duplicates(self):
        normalized = normalize_results(SAMPLE_RESULTS)
        self.assertEqual(len(normalized), 2)
        self.assertEqual(normalized[0]["url"], "https://example.com/vector-db?utm_source=news")
        self.assertEqual(normalized[1]["url"], "https://example.org/hnsw")

    def test_canonical_url_ignores_tracking_and_fragments(self):
        self.assertEqual(
            canonical_url("https://www.example.com/path/?utm_source=x&a=1#frag"),
            canonical_url("https://www.example.com/path?a=1"),
        )
        self.assertEqual(canonical_url("javascript:alert(1)"), "")
        self.assertTrue(source_fingerprint("https://example.com/a"))

    def test_relevance_score_separates_related_and_unrelated_text(self):
        related = relevance_score(
            "Vector databases store embeddings for similarity search",
            "vector database embeddings",
        )
        unrelated = relevance_score(
            "The history of medieval pottery",
            "vector database embeddings",
        )
        self.assertGreater(related, 0.5)
        self.assertEqual(unrelated, 0.0)

    def test_excerpt_prefers_query_relevant_sentence(self):
        text = (
            "Unrelated opening sentence about weather. "
            "Vector databases store embeddings for similarity search. "
            "Another unrelated sentence about cooking."
        )
        excerpt = extract_relevant_excerpt(text, "vector embeddings similarity", max_chars=120)
        self.assertIn("Vector databases", excerpt)
        self.assertLessEqual(len(excerpt), 120)

    def test_snippet_evidence_has_provenance_and_summary_url(self):
        bundle = build_query_evidence(
            "vector database embeddings",
            SAMPLE_RESULTS,
            topic="vector databases",
            fetcher=None,
        )
        self.assertEqual(bundle["usable_count"], 2)
        self.assertEqual(bundle["fetched_count"], 0)
        first = bundle["items"][0]
        self.assertEqual(first["retrieval_method"], "search_snippet")
        self.assertEqual(first["fetch_status"], "not_attempted")
        self.assertIn("example.com/vector-db", first["url"])
        summary = assemble_summary(bundle["items"])
        self.assertIn("URL: https://example.com/vector-db", summary)
        self.assertIn("search_snippet", summary)

    def test_fetched_page_is_preferred_and_failure_falls_back(self):
        ok_bundle = build_query_evidence(
            "vector database embeddings",
            SAMPLE_RESULTS,
            fetcher=_fetcher_ok,
            fetch_limit=1,
        )
        self.assertEqual(ok_bundle["fetched_count"], 1)
        self.assertEqual(ok_bundle["items"][0]["retrieval_method"], "fetched_page")
        self.assertEqual(ok_bundle["items"][0]["fetch_status"], "ok")

        fail_bundle = build_query_evidence(
            "vector database embeddings",
            SAMPLE_RESULTS,
            fetcher=_fetcher_fail,
            fetch_limit=2,
        )
        self.assertEqual(fail_bundle["fetch_failed_count"], 2)
        self.assertEqual(fail_bundle["items"][0]["retrieval_method"], "search_snippet")
        self.assertIn("failed:network_error", fail_bundle["items"][0]["fetch_status"])

    def test_fetch_budget_and_item_cap_are_respected(self):
        bundle = build_query_evidence(
            "vector database embeddings",
            SAMPLE_RESULTS,
            fetcher=_fetcher_ok,
            max_items=1,
            fetch_limit=2,
        )
        self.assertEqual(bundle["usable_count"], 1)
        self.assertEqual(bundle["fetched_count"], 1)

    def test_empty_and_irrelevant_results_do_not_reach_editor(self):
        bundle = build_query_evidence(
            "quantum error correction",
            [
                {"title": "", "url": "https://example.com/empty", "snippet": ""},
                {"title": "Cooking", "url": "https://example.com/food", "snippet": "Best pasta recipes."},
            ],
            min_relevance=0.1,
        )
        self.assertEqual(bundle["usable_count"], 0)
        self.assertEqual(bundle["items"], [])
        self.assertEqual(
            assemble_summary(bundle["items"]),
            "No usable evidence was retrieved for this query.",
        )

    def test_registry_deduplicates_across_queries_and_formats_ids(self):
        summaries = [
            {
                "query": "q1",
                "evidence_items": [
                    {
                        "title": "A",
                        "url": "https://example.com/a",
                        "canonical_url": "https://example.com/a",
                        "excerpt": "alpha",
                        "retrieval_method": "search_snippet",
                        "fetch_status": "not_attempted",
                        "relevance": 0.5,
                    }
                ],
            },
            {
                "query": "q2",
                "evidence_items": [
                    {
                        "title": "A again",
                        "url": "https://example.com/a#dup",
                        "canonical_url": "https://example.com/a",
                        "excerpt": "alpha",
                        "retrieval_method": "search_snippet",
                        "fetch_status": "not_attempted",
                        "relevance": 0.5,
                    }
                ],
            },
        ]
        registry = build_source_registry(summaries)
        self.assertEqual(len(registry), 1)
        self.assertEqual(registry[0]["source_id"], "SRC-001")
        self.assertEqual(registry[0]["queries"], ["q1", "q2"])
        context = format_evidence_context(summaries, registry)
        self.assertIn("[SRC-001]", context)
        self.assertIn("https://example.com/a", context)


class ReportParsingTests(unittest.TestCase):
    def test_strip_fences_and_extract_embedded_object(self):
        raw = 'prefix\n```json\n{"title": "T", "report": "Body"}\n```\nsuffix'
        self.assertEqual(strip_code_fences(raw).strip(), '{"title": "T", "report": "Body"}')
        self.assertEqual(
            extract_json_object('noise {"a": {"b": 1}} tail'),
            '{"a": {"b": 1}}',
        )

    def test_load_json_payload_plain_and_fenced(self):
        payload, repaired = load_json_payload('{"title": "T", "report": "Body"}')
        self.assertFalse(repaired)
        self.assertEqual(payload["title"], "T")

        payload, repaired = load_json_payload('```json\n{"title": "T2", "report": "B2"}\n```')
        self.assertFalse(repaired)
        self.assertEqual(payload["title"], "T2")

    def test_truncated_json_is_repaired(self):
        truncated = '{"title": "T", "report": "Body text", "outline": ["Intro", "Key'
        self.assertEqual(repair_truncated_json(truncated).count("{"), 1)
        payload, repaired = load_json_payload(truncated)
        self.assertTrue(repaired)
        self.assertEqual(payload["title"], "T")

    def test_normalize_payload_filters_unsupported_citations(self):
        payload = {
            "title": "T",
            "report": "Body",
            "outline": ["Intro"],
            "sources": [
                "https://example.com/retrieved",
                "https://evil.example/fabricated",
                "javascript:alert(1)",
            ],
            "key_findings": [
                {"claim": "Supported claim", "source_ids": ["SRC-001"], "support": "yes"},
                {"claim": "Bad claim", "source_ids": ["SRC-999"], "support": "no"},
            ],
            "word_count": 0,
        }
        normalized = normalize_report_payload(
            payload,
            retrieved_urls=["https://example.com/retrieved"],
            valid_source_ids=["SRC-001"],
        )
        self.assertEqual(normalized["sources"], ["https://example.com/retrieved"])
        self.assertEqual(
            normalized["unsupported_citations"],
            ["https://evil.example/fabricated", "javascript:alert(1)"],
        )
        self.assertEqual(len(normalized["key_findings"]), 2)
        self.assertTrue(normalized["key_findings"][0]["citation_supported"])
        self.assertFalse(normalized["key_findings"][1]["citation_supported"])
        self.assertEqual(normalized["key_findings"][1]["unsupported_source_ids"], ["SRC-999"])
        self.assertEqual(normalized["word_count"], 1)

    def test_normalize_payload_requires_title_and_body(self):
        with self.assertRaises(ValueError):
            normalize_report_payload({"title": "", "report": "Body"}, [])
        with self.assertRaises(ValueError):
            normalize_report_payload({"title": "T", "report": "   "}, [])

    def test_markdown_parser_extracts_and_filters_citations(self):
        markdown = (
            "# Title\n\n## Introduction\nText.\n\n## References\n"
            "1. Good – https://example.com/retrieved\n"
            "2. Bad – https://evil.example/fake\n"
        )
        parsed = parse_markdown_report(markdown, ["https://example.com/retrieved"])
        self.assertEqual(parsed["title"], "Title")
        self.assertIn("Introduction", parsed["outline"])
        self.assertEqual(parsed["sources"], ["https://example.com/retrieved"])
        self.assertEqual(parsed["unsupported_citations"], ["https://evil.example/fake"])

    def test_split_citations_and_url_safety(self):
        supported, unsupported = split_citations(
            ["https://example.com/a", "https://fake.example/b"],
            ["https://example.com/a"],
        )
        self.assertEqual(supported, ["https://example.com/a"])
        self.assertEqual(unsupported, ["https://fake.example/b"])
        self.assertTrue(is_safe_citation_url("https://example.com"))
        self.assertFalse(is_safe_citation_url("file:///etc/passwd"))


class GroundingTests(unittest.TestCase):
    def test_grounding_distinguishes_grounded_ungrounded_and_phantom(self):
        result = analyze_source_grounding(
            retrieved_urls=["https://example.com/a", "https://example.com/b"],
            report_content=(
                "Finding one https://example.com/a and a fabricated "
                "https://fake.example/x"
            ),
        )
        self.assertEqual(result["retrieved_sources_count"], 2)
        self.assertEqual(result["grounded_sources_count"], 1)
        self.assertEqual(result["ungrounded_retrieved_urls"], ["https://example.com/b"])
        self.assertEqual(result["phantom_urls"], ["https://fake.example/x"])
        self.assertEqual(result["citation_grounding_ratio"], 0.5)

    def test_claim_citation_coverage_and_unsupported_ids(self):
        registry = [{"source_id": "SRC-001", "url": "https://example.com/a"}]
        findings = [
            {"claim": "A", "source_ids": ["SRC-001"]},
            {"claim": "B", "source_ids": ["SRC-404"]},
            {"claim": "C", "source_ids": []},
        ]
        result = analyze_claim_citations(findings, registry)
        self.assertEqual(result["claim_findings_count"], 3)
        self.assertEqual(result["findings_with_supported_citations"], 1)
        self.assertEqual(result["unsupported_claim_source_ids"], ["SRC-404"])
        self.assertEqual(result["findings_without_citations"], ["C"])
        self.assertEqual(result["claim_citation_coverage"], round(1 / 3, 4))

    def test_normalize_url_is_stable(self):
        self.assertEqual(normalize_url("https://www.Example.com/a/"), "example.com/a")
        self.assertEqual(normalize_url(""), "")

    def test_end_to_end_evidence_to_citation_coverage(self):
        bundle = build_query_evidence(
            "vector database embeddings",
            SAMPLE_RESULTS,
            fetcher=_fetcher_ok,
            fetch_limit=1,
        )
        summaries = [
            {
                "query": "vector database embeddings",
                "evidence_items": bundle["items"],
                "source_urls": [item["url"] for item in bundle["items"]],
            }
        ]
        registry = build_source_registry(summaries)
        context = format_evidence_context(summaries, registry)
        self.assertIn("[SRC-001]", context)
        normalized = normalize_report_payload(
            {
                "title": "Report",
                "report": f"Body citing {registry[0]['url']}",
                "sources": [registry[0]["url"]],
                "key_findings": [{"claim": "X", "source_ids": ["SRC-001"]}],
            },
            retrieved_urls=[record["url"] for record in registry],
            valid_source_ids=[record["source_id"] for record in registry],
        )
        grounding = analyze_source_grounding(
            retrieved_urls=[record["url"] for record in registry],
            report_content=normalized["report"],
            explicit_sources=normalized["sources"],
            claim_findings=normalized["key_findings"],
            source_registry=registry,
        )
        self.assertEqual(grounding["grounded_sources_count"], 1)
        self.assertEqual(grounding["claim_citation_coverage"], 1.0)
        self.assertEqual(grounding["phantom_urls"], [])


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print(f"Tests run: {result.testsRun}")
    print(f"RESULT: {'PASS' if result.wasSuccessful() else 'FAIL'}")
    raise SystemExit(0 if result.wasSuccessful() else 1)
