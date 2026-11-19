"""Independent retrieval metrics for the AI Research Agent.

The metrics operate on a ranked list of retrieved documents and an explicit
set of relevant URLs. They are intentionally independent of report generation:
a good answer cannot compensate for bad retrieval, and a bad answer does not
change the retrieval score.

Missing ground-truth labels are a first-class state. A case without labels
returns ``value=None`` and ``status="not_measurable"``; it is excluded from
aggregate means rather than counted as zero.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from tools.research_evidence import canonical_url

__all__ = [
    "CANDIDATE_POOL_METRIC_VERSION",
    "DEFAULT_K_VALUES",
    "REGISTRY_METRIC_VERSION",
    "aggregate_candidate_pool_metrics",
    "aggregate_retrieval_metrics",
    "documents_from_candidate_pool",
    "documents_from_search_candidates",
    "documents_from_state",
    "documents_from_summaries",
    "evaluate_candidate_pool_case",
    "evaluate_retrieval_case",
    "ndcg_at_k",
    "precision_at_k",
    "recall_at_k",
    "reciprocal_rank",
]

DEFAULT_K_VALUES: Tuple[int, ...] = (1, 3, 5, 10)

# Versioned metric families. The registry family is the historical metric and
# its implementation is intentionally unchanged. Candidate-pool metrics are
# additive diagnostics over a wider retrieval stage.
REGISTRY_METRIC_VERSION = "retrieval-registry-1.0"
CANDIDATE_POOL_METRIC_VERSION = "retrieval-candidate-pool-1.0"


def _key(url: Any) -> str:
    if not isinstance(url, str) or not url.strip():
        return ""
    canonical = canonical_url(url)
    return canonical or url.strip().lower().rstrip("/")


def _document_url(document: Any) -> str:
    if isinstance(document, str):
        return document
    if isinstance(document, dict):
        for key in ("url", "canonical_url", "link", "href"):
            value = document.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return ""


def _ranked_keys(documents: Sequence[Any]) -> List[str]:
    keys: List[str] = []
    seen: set[str] = set()
    for document in documents or []:
        key = _key(_document_url(document))
        if key and key not in seen:
            seen.add(key)
            keys.append(key)
    return keys


def _relevant_keys(relevant_urls: Sequence[Any]) -> List[str]:
    keys: List[str] = []
    seen: set[str] = set()
    for url in relevant_urls or []:
        key = _key(url)
        if key and key not in seen:
            seen.add(key)
            keys.append(key)
    return keys


def _as_int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    return default


def recall_at_k(
    ranked_documents: Sequence[Any],
    relevant_urls: Sequence[Any],
    k: int,
) -> Optional[float]:
    """Fraction of labelled relevant URLs present in the top ``k`` results.

    Returns ``None`` when no relevant URLs are labelled.
    """
    relevant = set(_relevant_keys(relevant_urls))
    if not relevant:
        return None
    ranked = _ranked_keys(ranked_documents)
    cutoff = max(0, _as_int(k))
    retrieved = set(ranked[:cutoff])
    return round(len(retrieved & relevant) / len(relevant), 4)


def precision_at_k(
    ranked_documents: Sequence[Any],
    relevant_urls: Sequence[Any],
    k: int,
) -> Optional[float]:
    """Fraction of the top ``k`` slots that are labelled relevant.

    The denominator is ``k`` (the requested cutoff), not the number of returned
    documents. This makes a retriever that returns fewer than ``k`` results
    receive the appropriate precision penalty.
    """
    relevant = set(_relevant_keys(relevant_urls))
    if not relevant:
        return None
    cutoff = max(0, _as_int(k))
    if cutoff == 0:
        return 0.0
    ranked = _ranked_keys(ranked_documents)[:cutoff]
    return round(sum(1 for key in ranked if key in relevant) / cutoff, 4)


def reciprocal_rank(
    ranked_documents: Sequence[Any],
    relevant_urls: Sequence[Any],
) -> Optional[float]:
    """Reciprocal rank of the first relevant document (0.0 when none found)."""
    relevant = set(_relevant_keys(relevant_urls))
    if not relevant:
        return None
    for index, key in enumerate(_ranked_keys(ranked_documents), start=1):
        if key in relevant:
            return round(1.0 / index, 4)
    return 0.0


def _graded_values(
    ranked_documents: Sequence[Any],
    relevant_urls: Sequence[Any],
    graded_relevance: Optional[Dict[str, Any]],
) -> Tuple[List[float], List[float]]:
    ranked = _ranked_keys(ranked_documents)
    if graded_relevance:
        grade_by_key = {
            _key(url): float(value)
            for url, value in graded_relevance.items()
            if _key(url) and isinstance(value, (int, float)) and not isinstance(value, bool)
        }
        gains = [grade_by_key.get(key, 0.0) for key in ranked]
        ideal = sorted(
            [float(value) for value in graded_relevance.values()
             if isinstance(value, (int, float)) and not isinstance(value, bool)],
            reverse=True,
        )
        return gains, ideal

    relevant = set(_relevant_keys(relevant_urls))
    gains = [1.0 if key in relevant else 0.0 for key in ranked]
    ideal = [1.0] * len(relevant)
    return gains, ideal


def ndcg_at_k(
    ranked_documents: Sequence[Any],
    relevant_urls: Sequence[Any],
    k: int,
    graded_relevance: Optional[Dict[str, Any]] = None,
) -> Optional[float]:
    """Normalized Discounted Cumulative Gain at ``k``.

    Binary relevance is used unless ``graded_relevance`` maps URLs to numeric
    grades. The ideal DCG is computed from the available labels, so a retriever
    that returns no relevant document scores 0.0 when labels exist.
    """
    relevant = set(_relevant_keys(relevant_urls))
    if not relevant and not graded_relevance:
        return None
    cutoff = max(0, _as_int(k))
    if cutoff == 0:
        return 0.0
    gains, ideal = _graded_values(ranked_documents, relevant_urls, graded_relevance)
    gains = gains[:cutoff]
    ideal = sorted(ideal, reverse=True)[:cutoff]
    if not ideal:
        return None

    def _dcg(values: Sequence[float]) -> float:
        return sum(
            ((2 ** value) - 1) / math.log2(index + 2)
            for index, value in enumerate(values)
        )

    ideal_dcg = _dcg(ideal)
    if ideal_dcg <= 0:
        return None
    return round(min(1.0, max(0.0, _dcg(gains) / ideal_dcg)), 4)


def evaluate_retrieval_case(
    case_id: str,
    ranked_documents: Sequence[Any],
    relevant_urls: Sequence[Any],
    k_values: Sequence[int] = DEFAULT_K_VALUES,
    graded_relevance: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Evaluate one retrieval case with explicit missing-label handling."""
    relevant = _relevant_keys(relevant_urls)
    ranked = _ranked_keys(ranked_documents)
    relevant_set = set(relevant)
    matched = [key for key in ranked if key in relevant_set]
    first_rank = next(
        (index for index, key in enumerate(ranked, start=1) if key in relevant_set),
        None,
    )

    if not relevant:
        metrics = {
            f"recall@{k}": {
                "value": None,
                "status": "not_measurable",
                "reason": "no_relevant_labels",
            }
            for k in k_values
        }
        metrics.update(
            {
                f"precision@{k}": {
                    "value": None,
                    "status": "not_measurable",
                    "reason": "no_relevant_labels",
                }
                for k in k_values
            }
        )
        metrics.update(
            {
                f"ndcg@{k}": {
                    "value": None,
                    "status": "not_measurable",
                    "reason": "no_relevant_labels",
                }
                for k in k_values
            }
        )
        metrics["mrr"] = {
            "value": None,
            "status": "not_measurable",
            "reason": "no_relevant_labels",
        }
        label_status = "unavailable"
    else:
        metrics = {}
        for k in k_values:
            metrics[f"recall@{k}"] = {
                "value": recall_at_k(ranked_documents, relevant_urls, k),
                "status": "measured",
            }
            metrics[f"precision@{k}"] = {
                "value": precision_at_k(ranked_documents, relevant_urls, k),
                "status": "measured",
            }
            metrics[f"ndcg@{k}"] = {
                "value": ndcg_at_k(
                    ranked_documents,
                    relevant_urls,
                    k,
                    graded_relevance=graded_relevance,
                ),
                "status": "measured",
            }
        metrics["mrr"] = {
            "value": reciprocal_rank(ranked_documents, relevant_urls),
            "status": "measured",
        }
        label_status = "available"

    return {
        "case_id": case_id,
        "metric_version": REGISTRY_METRIC_VERSION,
        "label_status": label_status,
        "ranked_count": len(ranked),
        "relevant_count": len(relevant),
        "matched_count": len(matched),
        "matched_urls": matched,
        "first_relevant_rank": first_rank,
        "k_values": list(k_values),
        "metrics": metrics,
    }


def aggregate_retrieval_metrics(
    case_results: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    """Aggregate measured retrieval cases; exclude unlabelled cases explicitly."""
    results = [result for result in case_results or [] if isinstance(result, dict)]
    measured = [result for result in results if result.get("label_status") == "available"]
    excluded = [result for result in results if result.get("label_status") != "available"]

    metric_names: List[str] = []
    for result in results:
        for name in (result.get("metrics") or {}):
            if name not in metric_names:
                metric_names.append(name)

    aggregates: Dict[str, Any] = {}
    for name in metric_names:
        values = [
            float(result["metrics"][name]["value"])
            for result in measured
            if isinstance(result.get("metrics", {}).get(name), dict)
            and isinstance(result["metrics"][name].get("value"), (int, float))
        ]
        aggregates[name] = {
            "mean": round(sum(values) / len(values), 4) if values else None,
            "measured_cases": len(values),
            "status": "measured" if values else "not_measurable",
        }

    return {
        "cases_total": len(results),
        "measured_cases": len(measured),
        "excluded_cases": len(excluded),
        "excluded_case_ids": [result.get("case_id") for result in excluded],
        "metrics": aggregates,
        "methodology": (
            "Means are computed over cases with ground-truth labels only. "
            "Cases without labels are excluded, not scored as zero."
        ),
    }


def documents_from_state(state: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return the pipeline's source registry in its final display order.

    The workflow may sort the registry by deterministic source score before
    stable IDs are assigned; when ranking is disabled the registry stays in
    first-seen retention order. In both cases this list is the retrieval
    ranking used here; the optional lexical ``relevance`` score is reported but
    does not reorder the list.
    """
    state = state or {}
    registry = state.get("source_registry") or []
    documents: List[Dict[str, Any]] = []
    for index, record in enumerate(registry, start=1):
        if not isinstance(record, dict):
            continue
        documents.append(
            {
                "rank": index,
                "source_id": record.get("source_id"),
                "url": record.get("url"),
                "canonical_url": record.get("canonical_url"),
                "title": record.get("title"),
                "retrieval_method": record.get("retrieval_method"),
                "relevance": record.get("relevance"),
            }
        )
    if documents:
        return documents
    return [
        {"rank": index, "url": url}
        for index, url in enumerate(state.get("sources") or [], start=1)
        if isinstance(url, str) and url.strip()
    ]


def documents_from_summaries(
    summaries: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Rank evidence items in the order the Researcher retained them."""
    documents: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for summary in summaries or []:
        for item in summary.get("evidence_items", []) or []:
            url = item.get("url")
            key = _key(url)
            if not key or key in seen:
                continue
            seen.add(key)
            documents.append(
                {
                    "rank": len(documents) + 1,
                    "url": url,
                    "title": item.get("title"),
                    "retrieval_method": item.get("retrieval_method"),
                    "relevance": item.get("relevance"),
                }
            )
    return documents


def _documents_from_records(records: Sequence[Any]) -> List[Dict[str, Any]]:
    """Convert candidate records (dict or URL string) to ranked documents."""
    documents: List[Dict[str, Any]] = []
    for index, record in enumerate(records or [], start=1):
        if isinstance(record, str):
            if record.strip():
                documents.append({"rank": index, "url": record})
            continue
        if not isinstance(record, dict):
            continue
        url = record.get("url") or record.get("canonical_url")
        if not isinstance(url, str) or not url.strip():
            continue
        documents.append(
            {
                "rank": record.get("rank_position", index),
                "url": url,
                "canonical_url": record.get("canonical_url"),
                "title": record.get("title"),
                "search_query": record.get("search_query"),
                "rank_score": record.get("rank_score"),
                "authority_score": record.get("authority_score"),
                "source_type": record.get("source_type"),
            }
        )
    return documents


def documents_from_candidate_pool(state: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return the ranked candidate pool (stage C) in ranking order.

    This pool is captured before evidence retention, so it measures whether
    search plus ranking surfaced the labelled source at all.
    """
    state = state or {}
    return _documents_from_records(state.get("candidate_pool") or [])


def documents_from_search_candidates(state: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return normalized search candidates (stage B) in provider order."""
    state = state or {}
    return _documents_from_records(state.get("search_candidates") or [])


def evaluate_candidate_pool_case(
    case_id: str,
    ranked_documents: Sequence[Any],
    relevant_urls: Sequence[Any],
    k_values: Sequence[int] = (5, 10),
    pool: str = "ranked_candidates",
) -> Dict[str, Any]:
    """Evaluate a candidate pool with an explicit metric version and pool name.

    This is a diagnostic metric, not a replacement for the historical
    registry-based recall. It answers a different question: did the search /
    ranking stage see the labelled source at all?
    """
    result = evaluate_retrieval_case(
        case_id,
        ranked_documents,
        relevant_urls,
        k_values=k_values,
    )
    result["metric_version"] = CANDIDATE_POOL_METRIC_VERSION
    result["pool"] = pool
    return result


def aggregate_candidate_pool_metrics(
    case_results: Sequence[Dict[str, Any]],
    pool: str = "ranked_candidates",
) -> Dict[str, Any]:
    """Aggregate candidate-pool cases without counting unlabelled cases as zero."""
    aggregate = aggregate_retrieval_metrics(case_results)
    aggregate["metric_version"] = CANDIDATE_POOL_METRIC_VERSION
    aggregate["pool"] = pool
    aggregate["interpretation"] = (
        "Candidate-pool recall measures whether the labelled URL appeared in "
        "the ranked search pool before evidence retention. It is diagnostic "
        "and additive; the historical registry metric is unchanged."
    )
    return aggregate
