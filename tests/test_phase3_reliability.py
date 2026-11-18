"""Phase 3 reliability and fetch-safety test suite (standard library only).

Covers:
- URL scheme/host validation and DNS-rebinding mitigation with an injected
  resolver (no real DNS);
- redirect re-validation against the host/DNS policy;
- robots.txt allow/disallow and fail-open behaviour with an injected fetcher;
- per-run fetch deduplication, budget exhaustion and failure accounting;
- fetch error classification without network access;
- DuckDuckGo retry/backoff, malformed-result filtering and lazy import;
- Phase 3 configuration defaults.

No network, database, Ollama, or application pipeline is used.

Run:
    python tests/test_phase3_reliability.py
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import Request

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.config import ResearchConfig
from tools.page_fetcher import (
    DEFAULT_USER_AGENT,
    FetchCache,
    RobotsPolicy,
    _SafeRedirectHandler,
    _classify_fetch_error,
    fetch_page,
    is_safe_fetch_url,
    resolve_host_addresses,
)
from tools.web_search import _coerce_results, _search_with_retries, web_search


def _resolver(*addresses):
    """Build a resolver returning getaddrinfo-shaped records for the addresses."""

    def _resolve(host, port=None):
        return [
            (2, 1, 6, "", (address, 0))
            for address in addresses
        ]

    return _resolve


def _raising_resolver(host, port=None):
    raise OSError("simulated DNS failure")


class UrlSafetyTests(unittest.TestCase):
    def test_rejects_non_http_private_and_local_hosts(self):
        for url in (
            "file:///etc/passwd",
            "ftp://example.com/file",
            "http://localhost/admin",
            "http://127.0.0.1/",
            "http://10.0.0.5/",
            "http://169.254.169.254/latest/meta-data",
            "http://[::1]/",
            "",
            None,
        ):
            self.assertFalse(is_safe_fetch_url(url), url)

    def test_accepts_public_hostname_without_dns_resolution(self):
        self.assertTrue(is_safe_fetch_url("https://example.com/page"))

    def test_dns_check_blocks_private_answers(self):
        self.assertFalse(
            is_safe_fetch_url(
                "https://rebind.example", resolve_dns=True, resolver=_resolver("127.0.0.1")
            )
        )
        self.assertFalse(
            is_safe_fetch_url(
                "https://rebind.example", resolve_dns=True, resolver=_resolver("192.168.1.10")
            )
        )

    def test_dns_check_allows_public_answers(self):
        self.assertTrue(
            is_safe_fetch_url(
                "https://example.com", resolve_dns=True, resolver=_resolver("93.184.216.34")
            )
        )

    def test_dns_failure_fails_closed(self):
        self.assertFalse(
            is_safe_fetch_url(
                "https://broken.example", resolve_dns=True, resolver=_raising_resolver
            )
        )

    def test_resolver_deduplicates_addresses(self):
        self.assertEqual(
            resolve_host_addresses("example.com", _resolver("1.1.1.1", "1.1.1.1")),
            ["1.1.1.1"],
        )

    def test_redirect_handler_blocks_unsafe_target(self):
        handler = _SafeRedirectHandler()
        with self.assertRaises(HTTPError):
            handler.redirect_request(
                Request("https://example.com/"),
                None,
                302,
                "Found",
                {},
                "http://127.0.0.1/secret",
            )


class RobotsTests(unittest.TestCase):
    def test_disallow_and_allow_are_honoured(self):
        policy = RobotsPolicy(
            fetch_text=lambda url: "User-agent: *\nDisallow: /private\n"
        )
        self.assertFalse(policy.is_allowed("https://example.com/private/page"))
        self.assertTrue(policy.is_allowed("https://example.com/public/page"))

    def test_robots_failure_is_fail_open_and_counted(self):
        def _boom(url):
            raise OSError("robots unreachable")

        policy = RobotsPolicy(fetch_text=_boom)
        self.assertTrue(policy.is_allowed("https://example.com/page"))
        self.assertEqual(policy.fetch_failures, 1)

    def test_origin_is_cached_once(self):
        calls = []

        def _fetch(url):
            calls.append(url)
            return "User-agent: *\nAllow: /\n"

        policy = RobotsPolicy(fetch_text=_fetch)
        policy.is_allowed("https://example.com/a")
        policy.is_allowed("https://example.com/b")
        self.assertEqual(len(calls), 1)

    def test_fetch_page_stops_before_network_when_disallowed(self):
        class _DenyAll:
            def is_allowed(self, url, respect_robots=True):
                return False

        result = fetch_page(
            "https://example.com/private",
            respect_robots=True,
            robots_policy=_DenyAll(),
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "robots_disallowed")
        self.assertEqual(result["robots_status"], "disallowed")

    def test_fetch_page_blocks_unsafe_url_without_network(self):
        result = fetch_page("http://localhost:11434/api/tags")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "blocked_or_invalid_url")

    def test_fetch_page_blocks_private_dns_without_network(self):
        result = fetch_page(
            "https://rebind.example/page",
            resolve_dns=True,
            resolver=_resolver("127.0.0.1"),
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "blocked_or_invalid_url")
        self.assertTrue(result["dns_checked"])


class FetchCacheTests(unittest.TestCase):
    def test_duplicate_urls_are_served_from_cache(self):
        calls = []
        cache = FetchCache(max_fetches=5)

        def _fetch(url):
            calls.append(url)
            return {"ok": True, "url": url, "text": "body", "error": None}

        first = cache.get("https://example.com/a?utm_source=x", _fetch)
        second = cache.get("https://example.com/a", _fetch)
        self.assertEqual(len(calls), 1)
        self.assertFalse(first.get("from_cache"))
        self.assertTrue(second.get("from_cache"))
        self.assertEqual(cache.hits, 1)

    def test_budget_exhaustion_is_reported_not_raised(self):
        cache = FetchCache(max_fetches=1)

        def _fetch(url):
            return {"ok": True, "url": url, "text": "body", "error": None}

        cache.get("https://example.com/1", _fetch)
        blocked = cache.get("https://example.com/2", _fetch)
        self.assertFalse(blocked["ok"])
        self.assertEqual(blocked["error"], "fetch_budget_exhausted")
        self.assertEqual(cache.budget_exhausted, 1)
        self.assertEqual(cache.attempts, 1)

    def test_fetcher_exception_is_contained(self):
        cache = FetchCache(max_fetches=3)

        def _fetch(url):
            raise RuntimeError("boom")

        result = cache.get("https://example.com/x", _fetch)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "fetcher_exception:RuntimeError")
        self.assertEqual(cache.failures, 1)

    def test_invalid_url_is_counted_as_failure(self):
        cache = FetchCache(max_fetches=3)
        result = cache.get("", lambda url: {"ok": True})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "invalid_url")


class ErrorClassificationTests(unittest.TestCase):
    def test_ssl_and_timeout_are_classified_without_secrets(self):
        import socket
        import ssl

        ssl_error = ssl.SSLCertVerificationError("certificate verify failed")
        self.assertEqual(_classify_fetch_error(ssl_error), "tls_certificate_error")
        self.assertEqual(
            _classify_fetch_error(URLError(ssl_error)), "tls_certificate_error"
        )
        self.assertEqual(_classify_fetch_error(socket.timeout()), "timeout")
        self.assertEqual(
            _classify_fetch_error(URLError(socket.timeout())), "timeout"
        )
        self.assertEqual(_classify_fetch_error(URLError("dns failure")), "network_error:str")

    def test_generic_exception_is_classified(self):
        self.assertEqual(
            _classify_fetch_error(ValueError("x")), "network_error:ValueError"
        )


class SearchReliabilityTests(unittest.TestCase):
    def test_coerce_results_drops_malformed_entries(self):
        raw = [
            {"title": "A", "href": "https://a.example", "body": "a"},
            {"title": "no url", "body": "x"},
            "not a dict",
            {"title": "B", "url": " https://b.example ", "snippet": "b"},
        ]
        results = _coerce_results(raw)
        self.assertEqual([r["url"] for r in results], ["https://a.example", "https://b.example"])
        self.assertEqual(results[1]["snippet"], "b")

    def test_retry_succeeds_after_transient_failures(self):
        attempts = {"count": 0}

        def _call():
            attempts["count"] += 1
            if attempts["count"] < 3:
                raise RuntimeError("temporary")
            return [{"title": "ok", "href": "https://a.example", "body": "b"}]

        diagnostics = {}
        results = _search_with_retries(
            _call, "q", max_retries=2, backoff=0.0, diagnostics=diagnostics
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(diagnostics["attempts"], 3)
        self.assertIsNone(diagnostics["error"])

    def test_retry_gives_up_and_records_error(self):
        def _call():
            raise RuntimeError("permanent")

        diagnostics = {}
        results = _search_with_retries(
            _call, "q", max_retries=1, backoff=0.0, diagnostics=diagnostics
        )
        self.assertEqual(results, [])
        self.assertEqual(diagnostics["attempts"], 2)
        self.assertIn("RuntimeError", diagnostics["error"])

    def test_empty_success_is_not_retried(self):
        calls = {"count": 0}

        def _call():
            calls["count"] += 1
            return []

        diagnostics = {}
        self.assertEqual(
            _search_with_retries(
                _call, "q", max_retries=3, backoff=0.0, diagnostics=diagnostics
            ),
            [],
        )
        self.assertEqual(calls["count"], 1)

    def test_web_search_is_importable_and_degrades_without_ddgs(self):
        diagnostics = {}
        # ddgs is an optional dependency. Simulate it being absent so this test
        # is deterministic regardless of whether it is installed on the host,
        # and never performs a real network search.
        with patch.dict(sys.modules, {"ddgs": None}):
            results = web_search("anything", max_retries=0, diagnostics=diagnostics)
        self.assertEqual(results, [])
        self.assertTrue(diagnostics.get("error"))
        self.assertEqual(diagnostics.get("attempts"), 1)


class ConfigTests(unittest.TestCase):
    def test_phase3_defaults_are_safe_and_bounded(self):
        config = ResearchConfig()
        self.assertGreater(config.max_total_pages_per_run, 0)
        self.assertTrue(config.respect_robots_txt)
        self.assertTrue(config.enforce_dns_safety)
        self.assertGreaterEqual(config.search_max_retries, 0)
        self.assertEqual(config.max_pages_per_query, 2)
        self.assertLessEqual(config.max_total_pages_per_run, 50)
        self.assertGreater(config.evaluation_support_threshold, 0.0)
        self.assertLessEqual(config.evaluation_support_threshold, 1.0)


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    result = unittest.main(exit=False, verbosity=2).result
    total = result.testsRun
    failures = len(result.failures) + len(result.errors)
    print(f"\nTests run: {total}")
    print("RESULT:", "PASS" if failures == 0 else "FAIL")
    sys.exit(0 if failures == 0 else 1)
