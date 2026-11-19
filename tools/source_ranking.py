"""Deterministic, explainable source ranking for research candidates.

The old pipeline kept candidates in raw DuckDuckGo order and built the source
registry in first-seen query order. That let vendor explainers, SEO listicles,
and social posts displace primary papers and official documentation even when
a better source was present in the result pool.

This module implements an *additive* ranking score. Every component is logged
on the candidate so an evaluation can attribute score changes. There is no
hard domain allowlist: academic repositories and official documentation get a
larger authority weight, while advertisement redirects are the only candidates
that are dropped outright. Legitimate independent technical blogs remain
eligible and simply score below primary sources.

The score is a retrieval heuristic, not a truth or quality judgement.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence
from urllib.parse import urlparse

from tools.research_evidence import clean_text, relevance_score, tokenize

RANKING_VERSION = "source-ranking-1.0"

# Authority is an additive signal in [0, 1]. It is deliberately graded rather
# than binary so an unknown independent blog still receives a usable score.
_ACADEMIC_HOSTS = {
    "arxiv.org",
    "www.arxiv.org",
    "export.arxiv.org",
    "openreview.net",
    "www.openreview.net",
    "aclanthology.org",
    "www.aclanthology.org",
    "neurips.cc",
    "www.neurips.cc",
    "proceedings.neurips.cc",
    "papers.nips.cc",
    "jmlr.org",
    "www.jmlr.org",
    "mlr.press",
    "proceedings.mlr.press",
    "openaccess.thecvf.com",
    "dl.acm.org",
    "ieeexplore.ieee.org",
    "link.springer.com",
    "www.sciencedirect.com",
    "www.nature.com",
    "pubmed.ncbi.nlm.nih.gov",
    "pmc.ncbi.nlm.nih.gov",
    "www.semanticscholar.org",
    "semanticscholar.org",
    "www.usenix.org",
    "www.ijcai.org",
    "aaai.org",
    "ojs.aaai.org",
}
_ENCYCLOPEDIA_HOSTS = {
    "wikipedia.org",
    "www.wikipedia.org",
    "en.wikipedia.org",
}
_OFFICIAL_DOC_HOSTS = {
    "python.org",
    "docs.python.org",
    "nodejs.org",
    "developer.mozilla.org",
    "pytorch.org",
    "docs.pytorch.org",
    "tensorflow.org",
    "www.tensorflow.org",
    "huggingface.co",
    "platform.openai.com",
    "docs.anthropic.com",
    "langchain.com",
    "docs.langchain.com",
    "js.langchain.com",
    "python.langchain.com",
    "fastapi.tiangolo.com",
    "docs.docker.com",
    "kubernetes.io",
    "docs.aws.amazon.com",
    "cloud.google.com",
    "learn.microsoft.com",
    "postgresql.org",
    "www.postgresql.org",
    "sqlalchemy.org",
    "docs.sqlalchemy.org",
    "streamlit.io",
    "docs.streamlit.io",
    "ollama.com",
    "docs.ollama.com",
}
_VENDOR_ENGINEERING_HOSTS = {
    "openai.com",
    "www.openai.com",
    "anthropic.com",
    "www.anthropic.com",
    "ai.meta.com",
    "engineering.fb.com",
    "research.google",
    "deepmind.google",
    "aws.amazon.com",
    "nvidia.com",
    "developer.nvidia.com",
    "www.nvidia.com",
    "microsoft.com",
    "www.microsoft.com",
    "pinecone.io",
    "www.pinecone.io",
    "together.ai",
    "www.together.ai",
    "cohere.com",
    "www.cohere.com",
    "databricks.com",
    "www.databricks.com",
    "snowflake.com",
    "www.snowflake.com",
}
_SOCIAL_HOSTS = {
    "reddit.com",
    "www.reddit.com",
    "old.reddit.com",
    "quora.com",
    "www.quora.com",
    "x.com",
    "www.x.com",
    "twitter.com",
    "www.twitter.com",
    "facebook.com",
    "www.facebook.com",
    "linkedin.com",
    "www.linkedin.com",
    "pinterest.com",
    "www.pinterest.com",
    "tiktok.com",
    "www.tiktok.com",
    "instagram.com",
    "www.instagram.com",
}
_LOW_QUALITY_HOSTS = {
    "medium.com",
    "www.medium.com",
    "readmedium.com",
    "www.readmedium.com",
    "dev.to",
    "www.dev.to",
    "hashnode.com",
    "www.hashnode.com",
}

_LISTICLE_PATTERNS = (
    re.compile(r"\btop\s+\d+\b", re.IGNORECASE),
    re.compile(r"\b\d+\s+(?:best|top)\b", re.IGNORECASE),
    re.compile(r"\bbest\s+[\w\-]+\s+(?:tools|frameworks|platforms|papers)\b", re.IGNORECASE),
    re.compile(r"\bultimate\s+guide\b", re.IGNORECASE),
    re.compile(r"\beverything\s+you\s+need\s+to\s+know\b", re.IGNORECASE),
    re.compile(r"\bbeginner'?s?\s+guide\b", re.IGNORECASE),
)


def _host(url: str) -> str:
    try:
        return (urlparse(url or "").hostname or "").lower()
    except ValueError:
        return ""


def _normalise_terms(text: str) -> str:
    """Split hyphens/underscores/slashes so ``retrieval-augmented`` matches
    both ``retrieval`` and ``augmented`` during deterministic matching."""
    return re.sub(r"[-_/]+", " ", text or "")


def _path_segments(url: str) -> List[str]:
    try:
        return [part for part in (urlparse(url or "").path or "").split("/") if part]
    except ValueError:
        return []


def _matches_host(host: str, candidates: set[str]) -> bool:
    return host in candidates or any(host.endswith("." + item) for item in candidates)


def source_authority(url: str) -> Dict[str, Any]:
    """Classify a URL into a source type and an authority bonus.

    Returns a dict with ``score`` (0..1), ``source_type`` and ``host``.
    """
    host = _host(url)
    if not host:
        return {"score": 0.0, "source_type": "unknown", "host": host}

    if _matches_host(host, _ACADEMIC_HOSTS):
        return {"score": 1.0, "source_type": "academic", "host": host}
    if host.endswith(".edu") or host.endswith(".gov"):
        return {"score": 0.86, "source_type": "academic_institution", "host": host}
    if _matches_host(host, _ENCYCLOPEDIA_HOSTS):
        return {"score": 0.55, "source_type": "encyclopedia", "host": host}
    path_segments = [part.lower() for part in _path_segments(url)]
    if (
        _matches_host(host, _OFFICIAL_DOC_HOSTS)
        or host.startswith("docs.")
        or ".docs." in host
        or host.endswith(".readthedocs.io")
        or (path_segments and path_segments[0] in {"docs", "documentation", "reference"})
    ):
        return {"score": 0.88, "source_type": "official_documentation", "host": host}
    if host in {"github.com", "www.github.com"}:
        if len(_path_segments(url)) >= 2:
            return {"score": 0.80, "source_type": "code_repository", "host": host}
        return {"score": 0.35, "source_type": "code_hosting", "host": host}
    if _matches_host(host, _VENDOR_ENGINEERING_HOSTS):
        return {"score": 0.45, "source_type": "vendor_engineering", "host": host}
    if _matches_host(host, _SOCIAL_HOSTS):
        return {"score": 0.06, "source_type": "social", "host": host}
    if _matches_host(host, _LOW_QUALITY_HOSTS):
        return {"score": 0.12, "source_type": "aggregator_blog", "host": host}
    return {"score": 0.32, "source_type": "independent_blog", "host": host}


def _query_coverage(text_tokens: set[str], query_tokens: Sequence[str]) -> float:
    unique_query = set(query_tokens)
    if not unique_query:
        return 0.0
    return len(unique_query & text_tokens) / len(unique_query)


def _entity_match(title: str, query: str) -> float:
    """Approximate exact entity/title matching without external knowledge.

    Signals: query-token coverage in the title, contiguous query bigrams in
    the title, and presence of an explicit acronym from the query.
    """
    query_tokens = list(dict.fromkeys(tokenize(query)))
    if not query_tokens:
        return 0.0
    title_tokens = set(tokenize(title))
    if not title_tokens:
        return 0.0

    title_overlap = len(set(query_tokens) & title_tokens) / len(set(query_tokens))

    query_bigrams = {
        (query_tokens[index], query_tokens[index + 1])
        for index in range(len(query_tokens) - 1)
    }
    if query_bigrams:
        title_tokens_list = tokenize(title)
        title_bigrams = {
            (title_tokens_list[index], title_tokens_list[index + 1])
            for index in range(len(title_tokens_list) - 1)
        }
        bigram_overlap = len(query_bigrams & title_bigrams) / len(query_bigrams)
    else:
        bigram_overlap = 0.0

    acronyms = {
        token
        for token in re.findall(r"\b[A-Z][A-Z0-9]{1,5}\b", query or "")
        if token.lower() in title_tokens
    }
    acronym_bonus = 0.15 if acronyms else 0.0

    return min(1.0, max(title_overlap, bigram_overlap) + acronym_bonus)


def _listicle_penalty(title: str) -> float:
    for pattern in _LISTICLE_PATTERNS:
        if pattern.search(title or ""):
            return -0.06
    return 0.0


def score_candidate(
    candidate: Dict[str, Any],
    query: str,
    topic: str = "",
    position: int = 0,
) -> Dict[str, Any]:
    """Score one normalized candidate and return an enriched copy."""
    title = clean_text(candidate.get("title", ""))
    snippet = clean_text(candidate.get("snippet", ""))
    combined = f"{title} {snippet}".strip()
    normalised_combined = _normalise_terms(combined)
    normalised_query = _normalise_terms(query)
    query_tokens = tokenize(normalised_query)
    text_tokens = set(tokenize(normalised_combined))

    lexical = relevance_score(normalised_combined, normalised_query, topic)
    coverage = _query_coverage(text_tokens, query_tokens)
    entity = _entity_match(_normalise_terms(title), normalised_query)
    authority = source_authority(candidate.get("url", ""))
    listicle = _listicle_penalty(title)

    components = {
        "lexical_relevance": round(0.35 * lexical, 4),
        "query_coverage": round(0.20 * coverage, 4),
        "entity_match": round(0.15 * entity, 4),
        "authority": round(0.20 * float(authority["score"]), 4),
        "position_prior": round(0.05 * (1.0 - min(max(0, position), 10) / 10.0), 4),
    }
    penalties = {}
    if listicle:
        penalties["listicle"] = listicle
    if authority["source_type"] == "social":
        penalties["social"] = -0.05
    if authority["source_type"] == "aggregator_blog":
        penalties["aggregator_blog"] = -0.04
    if not snippet:
        penalties["empty_snippet"] = -0.02

    score = round(sum(components.values()) + sum(penalties.values()), 4)
    enriched = dict(candidate)
    enriched.update(
        {
            "rank_score": score,
            "rank_components": components,
            "rank_penalties": penalties,
            "authority_score": round(float(authority["score"]), 4),
            "source_type": authority["source_type"],
            "ranking_version": RANKING_VERSION,
        }
    )
    return enriched


def rank_candidates(
    candidates: Sequence[Dict[str, Any]],
    query: str,
    topic: str = "",
) -> List[Dict[str, Any]]:
    """Score and sort normalized candidates deterministically.

    Ties break on authority, then original search rank, then canonical URL,
    so repeated runs over the same candidate pool produce the same order.
    """
    scored: List[Dict[str, Any]] = []
    for index, candidate in enumerate(candidates or []):
        if not isinstance(candidate, dict):
            continue
        position = candidate.get("rank_position", candidate.get("original_rank", index))
        try:
            position = int(position)
        except (TypeError, ValueError):
            position = index
        scored.append(score_candidate(candidate, query, topic, position=position))

    scored.sort(
        key=lambda item: (
            -float(item.get("rank_score") or 0.0),
            -float(item.get("authority_score") or 0.0),
            int(item.get("original_rank") or 0),
            str(item.get("canonical_url") or ""),
        )
    )
    return scored


def ranking_summary(candidates: Sequence[Dict[str, Any]], limit: int = 5) -> List[Dict[str, Any]]:
    """Compact, log-safe view of the top ranked candidates."""
    summary: List[Dict[str, Any]] = []
    for index, candidate in enumerate(list(candidates or [])[: max(0, limit)], start=1):
        summary.append(
            {
                "position": index,
                "url": candidate.get("url", ""),
                "source_type": candidate.get("source_type"),
                "rank_score": candidate.get("rank_score"),
                "authority_score": candidate.get("authority_score"),
                "rank_components": candidate.get("rank_components"),
            }
        )
    return summary
