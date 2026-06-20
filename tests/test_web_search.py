"""Standalone test for DuckDuckGo web search tool.

Run:  python tests/test_web_search.py
"""

import sys
import os

# Fix Windows console encoding for emoji output
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.web_search import web_search, web_search_news, format_search_results
from config.config import ResearchConfig


def test_basic_search():
    """Test basic web search returns results."""
    print("=" * 60)
    print("TEST 1: Basic web search")
    print("=" * 60)

    results = web_search("Python programming language", max_results=5)

    if len(results) == 0:
        print("\u26a0\ufe0f  Got 0 results (likely rate-limited by DuckDuckGo). Skipping.")
        print()
        return

    assert "title" in results[0], "Result should have 'title' key"
    assert "url" in results[0], "Result should have 'url' key"
    assert "snippet" in results[0], "Result should have 'snippet' key"

    print(f"\u2705 Got {len(results)} results")
    for r in results:
        print(f"   \u2022 {r['title'][:60]}...")
        print(f"     {r['url']}")
    print()


def test_search_with_config():
    """Test search respects config max_results."""
    print("=" * 60)
    print("TEST 2: Search with config.max_results")
    print("=" * 60)

    config = ResearchConfig(max_results=3)
    results = web_search("artificial intelligence 2025", config=config)

    assert len(results) <= 3, f"Expected <=3 results, got {len(results)}"

    print(f"✅ Got {len(results)} results (max_results=3, may be 0 if rate-limited)")
    print()


def test_news_search():
    """Test news search returns results with date/source."""
    print("=" * 60)
    print("TEST 3: News search")
    print("=" * 60)

    results = web_search_news("technology news", max_results=5)

    if len(results) == 0:
        print("\u26a0\ufe0f  Got 0 news results (likely rate-limited by DuckDuckGo). Skipping.")
        print()
        return

    print(f"\u2705 Got {len(results)} news results")
    for r in results:
        print(f"   \u2022 {r['title'][:60]}...")
        print(f"     Source: {r.get('source', 'N/A')} | Date: {r.get('date', 'N/A')}")
    print()


def test_format_results():
    """Test formatter produces readable output."""
    print("=" * 60)
    print("TEST 4: Format search results")
    print("=" * 60)

    # Use synthetic data to test formatting independently of network
    mock_results = [
        {"title": "Test Title 1", "url": "https://example.com/1", "snippet": "First result snippet"},
        {"title": "Test Title 2", "url": "https://example.com/2", "snippet": "Second result snippet"},
    ]
    formatted = format_search_results(mock_results)

    assert len(formatted) > 0, "Formatted output should not be empty"
    assert "[1]" in formatted, "Should have numbered entries"
    assert "[2]" in formatted, "Should have second entry"
    assert "Test Title 1" in formatted, "Should contain title"

    print(f"\u2705 Formatted output ({len(formatted)} chars):")
    print("-" * 40)
    print(formatted)
    print("-" * 40)
    print()


def test_empty_results():
    """Test handling of no results."""
    print("=" * 60)
    print("TEST 5: Empty results formatting")
    print("=" * 60)

    formatted = format_search_results([])
    assert formatted == "No search results found.", f"Unexpected: {formatted}"

    print(f"✅ Empty results handled: '{formatted}'")
    print()


if __name__ == "__main__":
    print("\n🔍 DuckDuckGo Web Search Tool — Independent Tests\n")

    tests = [
        test_basic_search,
        test_search_with_config,
        test_news_search,
        test_format_results,
        test_empty_results,
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
