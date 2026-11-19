"""Retrieval-optimisation test suite.

Covers the additive retrieval work:

* arXiv / DOI scholarly URL canonicalisation and variant collapsing;
* redirect/ad filtering without rejecting legitimate blogs;
* deterministic source ranking and authority signals;
* retrieval-oriented planner validation, fallbacks, and label-leak guards;
* retrieval depth configuration;
* cross-query deduplication and fetch budgets;
* candidate-pool metrics and historical registry-metric compatibility;
* bounded HTTP 429 retry/backoff.

All tests are deterministic. No live search, Ollama, database, or network is
required; the fetch retry tests use an injected fake opener.

Run:
    py -3.13 tests/test_retrieval_optimization.py
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agents.triage_agent import (  # noqa: E402
    fallback_focus_areas,
    fallback_queries,
    run_triage_agent,
)
from config.config import ResearchConfig  # noqa: E402
from evaluation.retrieval import (  # noqa: E402
    CANDIDATE_POOL_METRIC_VERSION,
    REGISTRY_METRIC_VERSION,
    aggregate_candidate_pool_metrics,
    documents_from_candidate_pool,
    documents_from_search_candidates,
    documents_from_state,
    evaluate_candidate_pool_case,
    evaluate_retrieval_case,
)
from schemas.models import ResearchPlan  # noqa: E402
from tools.page_fetcher import fetch_page  # noqa: E402
from tools.research_evidence import (  # noqa: E402
    arxiv_abstract_url,
    arxiv_work_id,
    build_query_evidence,
    canonical_url,
    is_redirect_or_ad_url,
    normalize_results,
)
from tools.source_ranking import (  # noqa: E402
    RANKING_VERSION,
    rank_candidates,
    score_candidate,
    source_authority,
)


def _candidate(title, url, snippet, rank=0):
    return {
        "title": title,
        "url": url,
        "canonical_url": canonical_url(url),
        "snippet": snippet,
        "original_rank": rank,
    }


class CanonicalizationTests(unittest.TestCase):
    def test_arxiv_views_and_versions_collapse_to_one_work(self):
        variants = [
            "https://arxiv.org/abs/2005.11401",
            "https://arxiv.org/abs/2005.11401v2",
            "https://arxiv.org/pdf/2005.11401",
            "https://arxiv.org/pdf/2005.11401v3.pdf",
            "https://arxiv.org/html/2005.11401v1",
            "http://www.arxiv.org/format/2005.11401",
        ]
        self.assertEqual({canonical_url(url) for url in variants}, {"https://arxiv.org/abs/2005.11401"})
        self.assertEqual(arxiv_work_id(variants[3]), "2005.11401")
        self.assertEqual(arxiv_abstract_url(variants[3]), "https://arxiv.org/abs/2005.11401")

    def test_distinct_arxiv_ids_are_never_merged(self):
        self.assertNotEqual(
            canonical_url("https://arxiv.org/abs/2005.11401"),
            canonical_url("https://arxiv.org/abs/1603.09320"),
        )
        self.assertNotEqual(
            canonical_url("https://arxiv.org/pdf/2401.10774"),
            canonical_url("https://arxiv.org/pdf/2401.10775"),
        )

    def test_old_style_arxiv_ids_are_preserved(self):
        self.assertEqual(
            canonical_url("https://arxiv.org/pdf/hep-th/9901001v2"),
            "https://arxiv.org/abs/hep-th/9901001",
        )

    def test_doi_urls_are_stable(self):
        self.assertEqual(
            canonical_url("https://dx.doi.org/10.1000/ABC"),
            canonical_url("https://doi.org/10.1000/abc"),
        )

    def test_tracking_parameters_trailing_slash_and_fragments(self):
        self.assertEqual(
            canonical_url("https://www.example.com/path/?utm_source=x&a=1#frag"),
            canonical_url("https://www.example.com/path?a=1"),
        )
        self.assertEqual(canonical_url("javascript:alert(1)"), "")

    def test_redirect_and_ad_detection_keeps_legitimate_sites(self):
        self.assertTrue(is_redirect_or_ad_url("https://www.bing.com/aclick?ld=abc"))
        self.assertTrue(is_redirect_or_ad_url("https://duckduckgo.com/y.js?ad_domain=x"))
        self.assertFalse(is_redirect_or_ad_url("https://arxiv.org/abs/2005.11401"))
        self.assertFalse(is_redirect_or_ad_url("https://example.com/technical-blog"))

    def test_normalize_results_collapses_arxiv_variants_and_preserves_original(self):
        raw = [
            {"title": "RAG paper", "url": "https://arxiv.org/abs/2005.11401", "snippet": "x"},
            {"title": "RAG pdf", "url": "https://arxiv.org/pdf/2005.11401v2", "snippet": "x"},
            {"title": "RAG html", "url": "https://arxiv.org/html/2005.11401v1", "snippet": "x"},
        ]
        normalized = normalize_results(raw)
        self.assertEqual(len(normalized), 1)
        self.assertEqual(normalized[0]["url"], "https://arxiv.org/abs/2005.11401")
        self.assertEqual(normalized[0]["original_url"], "https://arxiv.org/abs/2005.11401")

    def test_non_arxiv_normalized_url_is_unchanged(self):
        raw = [{"title": "Blog", "url": "https://example.com/post?utm_source=news", "snippet": "x"}]
        normalized = normalize_results(raw)
        self.assertEqual(normalized[0]["url"], "https://example.com/post?utm_source=news")
        self.assertEqual(normalized[0]["original_url"], "https://example.com/post?utm_source=news")


class PlannerTests(unittest.TestCase):
    def test_fallback_is_diverse_and_avoids_benchmark_language(self):
        topic = (
            "What are the key verified facts about Retrieval Augmented Generation "
            "architecture and core components?"
        )
        queries = fallback_queries(topic, 6)
        joined = " ".join(queries).lower()
        self.assertGreaterEqual(len(queries), 5)
        self.assertEqual(len(queries), len({query.lower() for query in queries}))
        self.assertIn("site:arxiv.org", joined)
        self.assertIn("survey", joined)
        self.assertIn("documentation", joined)
        self.assertIn("github", joined)
        self.assertNotIn("verified facts", joined)
        self.assertNotIn("key facts", joined)

    def test_fallback_focus_areas_are_meaningful(self):
        focus = fallback_focus_areas("Direct Preference Optimization DPO")
        self.assertGreaterEqual(len(focus), 3)
        self.assertTrue(all(len(area) > 20 for area in focus))

    def test_invalid_planner_output_falls_back(self):
        with patch(
            "agents.triage_agent.ollama_generate_json",
            return_value={"search_queries": "not-a-list", "focus_areas": None},
        ):
            plan = run_triage_agent("RAG architecture", ResearchConfig())
        self.assertIsInstance(plan, ResearchPlan)
        self.assertGreaterEqual(len(plan.search_queries), 5)
        self.assertGreaterEqual(len(plan.focus_areas), 2)

    def test_planner_exception_falls_back(self):
        with patch(
            "agents.triage_agent.ollama_generate_json",
            side_effect=RuntimeError("truncated"),
        ):
            plan = run_triage_agent("RAG architecture", ResearchConfig())
        self.assertGreaterEqual(len(plan.search_queries), 5)

    def test_planner_rejects_label_like_output(self):
        polluted = {
            "search_queries": [
                "https://arxiv.org/abs/2005.11401",
                "arxiv 2005.11401",
                "RAG architecture",
                "RAG survey",
            ],
            "focus_areas": ["Core components"],
        }
        with patch("agents.triage_agent.ollama_generate_json", return_value=polluted):
            plan = run_triage_agent("RAG architecture", ResearchConfig())
        for query in plan.search_queries:
            self.assertNotIn("http", query.lower())
            self.assertNotIn("/abs/", query.lower())
            self.assertNotIn("2005.11401", query)

    def test_planner_uses_topic_only_and_planner_token_budget(self):
        config = ResearchConfig()
        captured = {}

        def fake(**kwargs):
            captured.update(kwargs)
            return {
                "search_queries": [
                    "RAG architecture",
                    "site:arxiv.org RAG",
                    "RAG official documentation",
                    "RAG survey",
                    "site:github.com RAG",
                    "RAG technical architecture",
                ],
                "focus_areas": ["Architecture", "Retrieval"],
            }

        with patch("agents.triage_agent.ollama_generate_json", side_effect=fake):
            plan = run_triage_agent("RAG architecture", config)
        self.assertEqual(captured["max_tokens"], config.planner_max_tokens)
        self.assertIn("RAG architecture", captured["prompt"])
        self.assertEqual(len(plan.search_queries), 6)

    def test_model_queries_are_top_up_to_target_without_duplicates(self):
        payload = {"search_queries": ["RAG architecture"], "focus_areas": ["A"]}
        with patch("agents.triage_agent.ollama_generate_json", return_value=payload):
            plan = run_triage_agent("RAG architecture", ResearchConfig())
        self.assertEqual(len(plan.search_queries), 6)
        self.assertEqual(
            len(plan.search_queries),
            len({query.lower() for query in plan.search_queries}),
        )


class RankingTests(unittest.TestCase):
    QUERY = "retrieval augmented generation architecture"

    def test_academic_source_outranks_independent_blog(self):
        candidates = [
            _candidate(
                "Retrieval Augmented Generation architecture overview",
                "https://example.com/blog",
                "retrieval augmented generation architecture",
                rank=0,
            ),
            _candidate(
                "Retrieval Augmented Generation architecture",
                "https://arxiv.org/abs/2005.11401",
                "retrieval augmented generation architecture",
                rank=5,
            ),
        ]
        ranked = rank_candidates(candidates, self.QUERY)
        self.assertEqual(ranked[0]["source_type"], "academic")
        self.assertGreater(ranked[0]["rank_score"], ranked[1]["rank_score"])

    def test_legitimate_blog_is_not_rejected(self):
        blog = _candidate(
            "A practical guide to RAG systems",
            "https://independent.example/rag-guide",
            "retrieval augmented generation architecture",
        )
        ranked = rank_candidates([blog], self.QUERY)
        self.assertEqual(len(ranked), 1)
        self.assertEqual(ranked[0]["source_type"], "independent_blog")
        self.assertGreater(ranked[0]["rank_score"], 0.0)

    def test_official_docs_and_github_repositories(self):
        self.assertEqual(source_authority("https://docs.python.org/3/")["source_type"], "official_documentation")
        self.assertEqual(
            source_authority("https://github.com/facebookresearch/faiss/wiki")["source_type"],
            "code_repository",
        )

    def test_listicle_penalty_reduces_score(self):
        plain = score_candidate(
            _candidate("RAG retrieval systems", "https://independent.example/a", "rag retrieval"),
            self.QUERY,
        )
        listicle = score_candidate(
            _candidate("Top 10 RAG retrieval systems", "https://independent.example/b", "rag retrieval"),
            self.QUERY,
        )
        self.assertLess(listicle["rank_score"], plain["rank_score"])
        self.assertIn("listicle", listicle["rank_penalties"])

    def test_components_and_version_are_logged(self):
        ranked = rank_candidates(
            [_candidate("RAG architecture", "https://arxiv.org/abs/2005.11401", "rag")],
            self.QUERY,
        )[0]
        self.assertEqual(ranked["ranking_version"], RANKING_VERSION)
        self.assertEqual(
            set(ranked["rank_components"]),
            {"lexical_relevance", "query_coverage", "entity_match", "authority", "position_prior"},
        )
        self.assertGreater(ranked["authority_score"], 0.0)

    def test_ranking_is_deterministic(self):
        candidates = [
            _candidate("RAG survey", "https://arxiv.org/abs/2005.11401", "rag", rank=1),
            _candidate("RAG blog", "https://example.com/a", "rag", rank=0),
            _candidate("RAG docs", "https://docs.python.org/3/", "rag", rank=2),
        ]
        first = [item["url"] for item in rank_candidates(candidates, self.QUERY)]
        second = [item["url"] for item in rank_candidates(list(reversed(candidates)), self.QUERY)]
        self.assertEqual(first, second)


class DepthConfigTests(unittest.TestCase):
    def test_configured_depth(self):
        config = ResearchConfig()
        self.assertEqual(config.max_results, 10)
        self.assertEqual(config.max_evidence_items_per_query, 5)
        self.assertEqual(config.max_pages_per_query, 4)
        self.assertGreaterEqual(config.max_total_pages_per_run, 24)
        self.assertLessEqual(config.max_total_pages_per_run, 50)
        self.assertEqual(config.planner_num_queries, 6)
        self.assertGreaterEqual(config.planner_max_tokens, 256)
        self.assertTrue(config.enable_source_ranking)

    def test_env_overrides_are_honoured(self):
        with patch.dict(
            "os.environ",
            {
                "SEARCH_MAX_RESULTS": "4",
                "MAX_PAGES_PER_QUERY": "1",
                "MAX_EVIDENCE_ITEMS_PER_QUERY": "2",
                "MAX_TOTAL_PAGES_PER_RUN": "6",
            },
        ):
            config = ResearchConfig()
        self.assertEqual(config.max_results, 4)
        self.assertEqual(config.max_pages_per_query, 1)
        self.assertEqual(config.max_evidence_items_per_query, 2)
        self.assertEqual(config.max_total_pages_per_run, 6)


class EvidenceDepthTests(unittest.TestCase):
    RESULTS = [
        {
            "title": f"RAG source {index}",
            "url": f"https://example.com/rag-{index}",
            "snippet": "retrieval augmented generation architecture and retrieval",
        }
        for index in range(6)
    ]

    def test_cross_query_duplicates_are_skipped_before_fetch(self):
        fetched = []

        def fetcher(url):
            fetched.append(url)
            return {"ok": True, "title": "T", "text": "retrieval augmented generation architecture", "fetched_chars": 58}

        first = build_query_evidence(
            "retrieval augmented generation architecture",
            self.RESULTS[:2],
            fetcher=fetcher,
            max_items=5,
            fetch_limit=4,
        )
        seen = {item["canonical_url"] for item in first["items"]}
        second = build_query_evidence(
            "retrieval augmented generation architecture",
            self.RESULTS[:2],
            fetcher=fetcher,
            max_items=5,
            fetch_limit=4,
            seen_urls=seen,
        )
        self.assertEqual(second["usable_count"], 0)
        self.assertEqual(second["duplicates_skipped"], 2)
        self.assertEqual(second["fetched_count"], 0)
        self.assertEqual(len(fetched), 2)

    def test_fetch_budget_and_retention_are_separate(self):
        bundle = build_query_evidence(
            "retrieval augmented generation architecture",
            self.RESULTS,
            fetcher=lambda url: {"ok": True, "text": "retrieval augmented generation architecture", "fetched_chars": 58},
            max_items=5,
            fetch_limit=2,
        )
        self.assertEqual(bundle["usable_count"], 5)
        self.assertEqual(bundle["fetched_count"], 2)
        self.assertEqual(
            sum(1 for item in bundle["items"] if item["retrieval_method"] == "fetched_page"),
            2,
        )
        self.assertEqual(
            sum(1 for item in bundle["items"] if item["retrieval_method"] == "search_snippet"),
            3,
        )

    def test_arxiv_pdf_candidate_fetches_abstract_page(self):
        fetched = []

        def fetcher(url):
            fetched.append(url)
            return {"ok": True, "text": "retrieval augmented generation", "fetched_chars": 30}

        bundle = build_query_evidence(
            "retrieval augmented generation",
            [
                {
                    "title": "RAG paper",
                    "url": "https://arxiv.org/pdf/2005.11401",
                    "snippet": "retrieval augmented generation",
                }
            ],
            fetcher=fetcher,
            max_items=1,
            fetch_limit=1,
        )
        self.assertEqual(fetched, ["https://arxiv.org/abs/2005.11401"])
        self.assertEqual(bundle["items"][0]["url"], "https://arxiv.org/abs/2005.11401")
        self.assertEqual(bundle["items"][0]["fetched_chars"], 30)

    def test_fetch_status_and_text_length_are_recorded(self):
        bundle = build_query_evidence(
            "retrieval augmented generation",
            [
                {
                    "title": "RAG source",
                    "url": "https://example.com/rag",
                    "snippet": "retrieval augmented generation",
                }
            ],
            fetcher=lambda url: {"ok": False, "text": "", "error": "http_error:429"},
            max_items=1,
            fetch_limit=1,
        )
        item = bundle["items"][0]
        self.assertTrue(item["fetch_status"].startswith("failed:http_error:429"))
        self.assertEqual(item["retrieval_method"], "search_snippet")
        self.assertGreater(item["content_chars"], 0)


class CandidateMetricTests(unittest.TestCase):
    def _state(self):
        return {
            "source_registry": [
                {"source_id": "SRC-001", "url": "https://arxiv.org/abs/2005.11401", "canonical_url": "https://arxiv.org/abs/2005.11401"},
                {"source_id": "SRC-002", "url": "https://example.com/blog", "canonical_url": "https://example.com/blog"},
            ],
            "search_candidates": [
                {"url": "https://example.com/blog", "canonical_url": "https://example.com/blog", "rank_position": 1},
                {"url": "https://example.com/d2", "canonical_url": "https://example.com/d2", "rank_position": 2},
                {"url": "https://example.com/d3", "canonical_url": "https://example.com/d3", "rank_position": 3},
                {"url": "https://example.com/d4", "canonical_url": "https://example.com/d4", "rank_position": 4},
                {"url": "https://example.com/d5", "canonical_url": "https://example.com/d5", "rank_position": 5},
                {"url": "https://arxiv.org/abs/2005.11401", "canonical_url": "https://arxiv.org/abs/2005.11401", "rank_position": 6},
            ],
            "candidate_pool": [
                {"url": "https://arxiv.org/abs/2005.11401", "canonical_url": "https://arxiv.org/abs/2005.11401", "rank_position": 1, "rank_score": 0.9},
                {"url": "https://example.com/blog", "canonical_url": "https://example.com/blog", "rank_position": 2, "rank_score": 0.3},
            ],
        }

    def test_pool_documents_follow_their_stage_order(self):
        state = self._state()
        self.assertEqual(documents_from_state(state)[0]["url"], "https://arxiv.org/abs/2005.11401")
        self.assertEqual(documents_from_search_candidates(state)[0]["url"], "https://example.com/blog")
        self.assertEqual(documents_from_candidate_pool(state)[0]["url"], "https://arxiv.org/abs/2005.11401")

    def test_candidate_pool_recall_is_versioned_and_separate(self):
        state = self._state()
        relevant = ["https://arxiv.org/abs/2005.11401"]
        retained = evaluate_retrieval_case("G001", documents_from_state(state), relevant)
        search = evaluate_candidate_pool_case(
            "G001", documents_from_search_candidates(state), relevant, pool="search_candidates"
        )
        ranked = evaluate_candidate_pool_case(
            "G001", documents_from_candidate_pool(state), relevant, pool="ranked_candidates"
        )
        self.assertEqual(retained["metric_version"], REGISTRY_METRIC_VERSION)
        self.assertEqual(search["metric_version"], CANDIDATE_POOL_METRIC_VERSION)
        self.assertEqual(ranked["metric_version"], CANDIDATE_POOL_METRIC_VERSION)
        self.assertEqual(retained["metrics"]["recall@5"]["value"], 1.0)
        self.assertEqual(search["metrics"]["recall@5"]["value"], 0.0)
        self.assertEqual(search["metrics"]["recall@10"]["value"], 1.0)
        self.assertEqual(ranked["metrics"]["recall@5"]["value"], 1.0)
        self.assertEqual(ranked["pool"], "ranked_candidates")

    def test_candidate_aggregate_excludes_unlabelled(self):
        labelled = evaluate_candidate_pool_case(
            "A", [{"url": "https://a.example"}], ["https://a.example"]
        )
        unlabelled = evaluate_candidate_pool_case("B", [{"url": "https://b.example"}], [])
        aggregate = aggregate_candidate_pool_metrics([labelled, unlabelled])
        self.assertEqual(aggregate["metric_version"], CANDIDATE_POOL_METRIC_VERSION)
        self.assertEqual(aggregate["measured_cases"], 1)
        self.assertEqual(aggregate["metrics"]["recall@5"]["mean"], 1.0)


class _FakeResponse:
    status = 200
    headers = {"Content-Type": "text/html"}

    def read(self, _n):
        return b"<html><body><p>retrieval augmented generation</p></body></html>"

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _FakeOpener:
    def __init__(self, failures):
        self.failures = failures
        self.calls = 0

    def open(self, request, timeout=0):
        self.calls += 1
        if self.calls <= self.failures:
            raise HTTPError(request.full_url, 429, "Too Many Requests", {"Retry-After": "0"}, None)
        return _FakeResponse()


class FetchRetryTests(unittest.TestCase):
    def test_429_is_retried_then_succeeds(self):
        opener = _FakeOpener(failures=1)
        with patch("tools.page_fetcher.build_opener", return_value=opener):
            result = fetch_page(
                "https://example.com/rag",
                max_retries=1,
                retry_backoff=0.0,
            )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(opener.calls, 2)
        self.assertIn("retrieval augmented generation", result["text"])

    def test_429_retry_budget_is_bounded(self):
        opener = _FakeOpener(failures=5)
        with patch("tools.page_fetcher.build_opener", return_value=opener):
            result = fetch_page(
                "https://example.com/rag",
                max_retries=2,
                retry_backoff=0.0,
            )
        self.assertFalse(result["ok"])
        self.assertEqual(result["attempts"], 3)
        self.assertEqual(result["error"], "http_error:429")

    def test_retry_disabled_by_default(self):
        opener = _FakeOpener(failures=1)
        with patch("tools.page_fetcher.build_opener", return_value=opener):
            result = fetch_page("https://example.com/rag")
        self.assertFalse(result["ok"])
        self.assertEqual(result["attempts"], 1)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    result = unittest.main(exit=False, verbosity=2).result
    print(f"\nTests run: {result.testsRun}")
    print(
        f"Failures: {len(result.failures)}  Errors: {len(result.errors)}"
    )
    print("RESULT:", "PASS" if result.wasSuccessful() else "FAIL")
    raise SystemExit(0 if result.wasSuccessful() else 1)
