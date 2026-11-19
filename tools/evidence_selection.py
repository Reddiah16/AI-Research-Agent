"""Deterministic evidence selection and context budgeting for the Editor.

The retrieval phase intentionally keeps more evidence than the Editor can use
well. A long, undifferentiated evidence block increases prompt-evaluation time
and makes it harder for a small local model to keep each claim attached to the
source that actually supports it.

This module performs a *deterministic* two-stage reduction before generation:

1. score and deduplicate the source registry;
2. select a diverse, budget-bounded subset and build a compact structured
   context that preserves stable ``SRC-*`` identifiers.

Nothing here judges factual truth. The score is a retrieval/readability
heuristic, and lexical near-duplicate detection is not semantic deduplication.
Source IDs are copied from the registry and are never renumbered, so citations
keep the same meaning downstream.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from tools.research_evidence import (
    canonical_url,
    clean_text,
    format_evidence_context,
    tokenize,
)

__all__ = [
    "EVIDENCE_SELECTION_VERSION",
    "EditorEvidenceBudget",
    "build_compact_evidence_context",
    "select_editor_evidence",
]

EVIDENCE_SELECTION_VERSION = "editor-evidence-selection-1.0"

# Approximate provider tokenisation for telemetry only. The character/4
# heuristic is explicitly labelled so it is never mistaken for a real token
# count returned by Ollama.
_CHARS_PER_TOKEN = 4


@dataclass(frozen=True)
class EditorEvidenceBudget:
    """Configurable limits for the evidence block sent to the Editor.

    Defaults are deliberately conservative for a 4B local model. They keep a
    representative set of sources while preventing a 27-30 source prompt from
    dominating prompt-evaluation time.
    """

    max_sources: int = 12
    min_sources: int = 3
    max_context_chars: int = 14_000
    excerpt_chars: int = 700
    near_duplicate_threshold: float = 0.85

    @classmethod
    def from_config(cls, config: Any) -> "EditorEvidenceBudget":
        """Build a budget from ``ResearchConfig`` with safe clamping."""
        max_sources = _positive_int(
            getattr(config, "editor_max_sources", cls.max_sources),
            cls.max_sources,
        )
        min_sources = _positive_int(
            getattr(config, "editor_min_sources", cls.min_sources),
            cls.min_sources,
        )
        min_sources = min(min_sources, max_sources)
        max_context_chars = _positive_int(
            getattr(config, "editor_max_context_chars", cls.max_context_chars),
            cls.max_context_chars,
        )
        excerpt_chars = _positive_int(
            getattr(config, "editor_excerpt_chars", cls.excerpt_chars),
            cls.excerpt_chars,
        )
        threshold = _number(
            getattr(
                config,
                "editor_near_duplicate_threshold",
                cls.near_duplicate_threshold,
            ),
            cls.near_duplicate_threshold,
        )
        return cls(
            max_sources=max_sources,
            min_sources=min_sources,
            max_context_chars=max_context_chars,
            excerpt_chars=excerpt_chars,
            near_duplicate_threshold=max(0.5, min(1.0, threshold)),
        )


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _number(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    return default


def _source_number(record: Dict[str, Any]) -> int:
    source_id = str(record.get("source_id") or "")
    digits = "".join(char for char in source_id if char.isdigit())
    try:
        return int(digits)
    except ValueError:
        return 10**9


def _quality(record: Dict[str, Any]) -> float:
    """Rank a source for Editor inclusion (higher is better).

    Components are intentionally simple and auditable:

    * 0.45 deterministic source-ranking score;
    * 0.25 authority score;
    * 0.25 query/topic lexical relevance;
    * small bonus for a successfully fetched page and usable content;
    * small penalty for snippet-only evidence.

    A snippet-only source is not removed: it can still be the only evidence for
    a subtopic. The penalty only orders otherwise comparable sources.
    """
    rank = max(0.0, min(1.0, _number(record.get("rank_score"))))
    authority = max(0.0, min(1.0, _number(record.get("authority_score"))))
    relevance = max(0.0, min(1.0, _number(record.get("relevance"))))
    excerpt = clean_text(record.get("excerpt") or "")
    content = min(1.0, len(excerpt) / 700.0) * 0.04

    fetch_status = str(record.get("fetch_status") or "")
    retrieval_method = str(record.get("retrieval_method") or "")
    fetch_bonus = 0.08 if fetch_status == "ok" else 0.0
    snippet_penalty = (
        0.04 if retrieval_method == "search_snippet" and fetch_status != "ok" else 0.0
    )

    return round(
        (0.45 * rank)
        + (0.25 * authority)
        + (0.25 * relevance)
        + fetch_bonus
        + content
        - snippet_penalty,
        6,
    )


def _shingles(text: str, size: int = 3) -> set:
    tokens = tokenize(text)
    if not tokens:
        return set()
    if len(tokens) <= size:
        return {tuple(tokens)}
    return {tuple(tokens[index : index + size]) for index in range(len(tokens) - size + 1)}


def _jaccard(left_text: str, right_text: str) -> Optional[float]:
    left_shingles = _shingles(left_text)
    right_shingles = _shingles(right_text)
    if not left_shingles or not right_shingles:
        return None
    return len(left_shingles & right_shingles) / len(left_shingles | right_shingles)


def _near_duplicate(
    left: Dict[str, Any], right: Dict[str, Any], threshold: float
) -> bool:
    """Detect pages that repeat the same content under different URLs.

    Two pages are near-duplicates when *either* their excerpts or their
    title-plus-excerpt shingles overlap at the threshold. Comparing excerpts
    separately catches mirrors that reuse the same body text but change the
    page title, which a title+excerpt comparison would miss.
    """
    left_full = clean_text(f"{left.get('title', '')} {left.get('excerpt', '')}")
    right_full = clean_text(f"{right.get('title', '')} {right.get('excerpt', '')}")
    scores = [
        _jaccard(left_full, right_full),
        _jaccard(
            clean_text(left.get("excerpt") or ""),
            clean_text(right.get("excerpt") or ""),
        ),
    ]
    return any(score is not None and score >= threshold for score in scores)


def _trim_excerpt(text: str, max_chars: int) -> str:
    """Trim at a sentence/word boundary, never in the middle of a word."""
    cleaned = clean_text(text)
    if len(cleaned) <= max_chars:
        return cleaned
    window = cleaned[:max_chars]
    # Prefer a sentence boundary in the latter half of the window so the model
    # sees complete claims rather than a fragment.
    best = -1
    for marker in (". ", "! ", "? ", "\n"):
        index = window.rfind(marker)
        if index > best:
            best = index
    if best >= max(20, len(window) // 2):
        return window[: best + 1].strip()
    if " " in window:
        return window.rsplit(" ", 1)[0].rstrip(" ,;:-") + " …"
    return window.rstrip() + " …"


def _format_group(
    query: str,
    records: Sequence[Dict[str, Any]],
    excerpt_chars: int,
) -> str:
    lines = [f"### Query: {query}"]
    for record in records:
        lines.append(
            f"[{record.get('source_id') or 'UNREGISTERED'}] "
            f"{record.get('title') or 'Untitled'}\n"
            f"URL: {record.get('url', '')}\n"
            f"Retrieved as: {record.get('retrieval_method', 'search_snippet')} "
            f"({record.get('fetch_status', 'not_attempted')})\n"
            f"Evidence excerpt: "
            f"{_trim_excerpt(record.get('excerpt') or '', excerpt_chars)}"
        )
    return "\n".join(lines)


def _group_queries(
    records: Sequence[Dict[str, Any]],
    research_summaries: Sequence[Dict[str, Any]],
) -> List[Tuple[str, List[Dict[str, Any]]]]:
    """Group selected records by query, preserving evidence-to-query mapping.

    A record may be returned by several queries. It is assigned to the first
    query that actually retained it so the context never repeats the same
    excerpt and source ID.
    """
    summaries = list(research_summaries or [])
    if summaries:
        selected_by_canonical = {
            record.get("canonical_url"): record
            for record in records
            if record.get("canonical_url")
        }
        assigned: set[str] = set()
        groups: List[Tuple[str, List[Dict[str, Any]]]] = []
        for summary in summaries:
            query = str(summary.get("query") or "Research query")
            chosen: List[Dict[str, Any]] = []
            for item in summary.get("evidence_items", []) or []:
                canonical = item.get("canonical_url") or canonical_url(
                    item.get("url", "")
                )
                record = selected_by_canonical.get(canonical)
                if record is not None and canonical not in assigned:
                    assigned.add(canonical)
                    chosen.append(record)
            if chosen:
                chosen.sort(key=lambda record: (-_quality(record), _source_number(record)))
                groups.append((query, chosen))
        leftovers = [
            record
            for record in records
            if record.get("canonical_url") not in assigned
        ]
        if leftovers:
            leftovers.sort(
                key=lambda record: (-_quality(record), _source_number(record))
            )
            groups.append(("Additional selected evidence", leftovers))
        return groups

    query_map: Dict[str, List[Dict[str, Any]]] = {}
    for record in records:
        queries = record.get("queries") or ["Research query"]
        for query in queries:
            query_map.setdefault(str(query), []).append(record)
    groups = list(query_map.items())
    for _, group in groups:
        group.sort(key=lambda record: (-_quality(record), _source_number(record)))
    return groups


def build_compact_evidence_context(
    records: Sequence[Dict[str, Any]],
    research_summaries: Sequence[Dict[str, Any]],
    excerpt_chars: int,
) -> str:
    """Render selected records as compact, query-grouped evidence text."""
    groups = _group_queries(records, research_summaries)
    blocks = [
        _format_group(query, group, excerpt_chars) for query, group in groups
    ]
    return "\n\n---\n\n".join(blocks) if blocks else "No usable evidence was retrieved."


def _source_stats(records: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    fetched = sum(
        1 for record in records if str(record.get("fetch_status")) == "ok"
    )
    snippet_only = sum(
        1
        for record in records
        if str(record.get("retrieval_method")) == "search_snippet"
        and str(record.get("fetch_status")) != "ok"
    )
    return {
        "fetched": fetched,
        "snippet_only": snippet_only,
        "fetched_share": round(fetched / len(records), 4) if records else None,
        "snippet_only_share": (
            round(snippet_only / len(records), 4) if records else None
        ),
    }


def select_editor_evidence(
    research_summaries: Sequence[Dict[str, Any]],
    registry: Sequence[Dict[str, Any]],
    budget: Optional[EditorEvidenceBudget] = None,
) -> Dict[str, Any]:
    """Select a strong, diverse, budget-bounded evidence subset.

    Returns:
        ``{"selected": [...], "context": str, "stats": {...}}`` where
        ``selected`` records keep their original ``source_id`` values.
    """
    budget = budget or EditorEvidenceBudget()
    records = [
        record
        for record in (registry or [])
        if isinstance(record, dict) and record.get("canonical_url")
    ]
    # Stable, quality-descending order for every later decision.
    records.sort(key=lambda record: (-_quality(record), _source_number(record)))

    # 1) Deterministic near-duplicate suppression. Exact canonical duplicates
    # are already removed by build_source_registry; this catches pages that
    # repeat the same text under different URLs.
    kept: List[Dict[str, Any]] = []
    duplicates_dropped = 0
    for record in records:
        if any(
            _near_duplicate(record, other, budget.near_duplicate_threshold)
            for other in kept
        ):
            duplicates_dropped += 1
            continue
        kept.append(record)

    # 2) Round-robin across queries for subtopic coverage, then fill by score.
    query_map: Dict[str, List[Dict[str, Any]]] = {}
    for record in kept:
        for query in record.get("queries") or ["Research query"]:
            query_map.setdefault(str(query), []).append(record)
    for group in query_map.values():
        group.sort(key=lambda record: (-_quality(record), _source_number(record)))
    query_order = sorted(
        query_map,
        key=lambda query: (
            -_quality(query_map[query][0]) if query_map[query] else 0.0,
            query,
        ),
    )

    selected: List[Dict[str, Any]] = []
    selected_canonicals: set[str] = set()
    while len(selected) < budget.max_sources:
        progressed = False
        for query in query_order:
            for record in query_map[query]:
                canonical = record.get("canonical_url")
                if canonical in selected_canonicals:
                    continue
                selected.append(record)
                selected_canonicals.add(canonical)
                progressed = True
                break
            if len(selected) >= budget.max_sources:
                break
        if not progressed:
            break
    if len(selected) < budget.max_sources:
        for record in kept:
            canonical = record.get("canonical_url")
            if canonical in selected_canonicals:
                continue
            selected.append(record)
            selected_canonicals.add(canonical)
            if len(selected) >= budget.max_sources:
                break

    # 3) Enforce the character budget by dropping the weakest selected sources,
    # never by truncating every excerpt to nothing. At least ``min_sources``
    # sources are always retained, even if the configured hard cap is smaller.
    working_excerpt_chars = budget.excerpt_chars
    context = build_compact_evidence_context(
        selected, research_summaries, working_excerpt_chars
    )
    budget_dropped = 0
    while len(selected) > budget.min_sources and len(context) > budget.max_context_chars:
        weakest = min(
            selected,
            key=lambda record: (_quality(record), str(record.get("canonical_url") or "")),
        )
        selected.remove(weakest)
        budget_dropped += 1
        context = build_compact_evidence_context(
            selected, research_summaries, working_excerpt_chars
        )
    while len(context) > budget.max_context_chars and working_excerpt_chars > 200:
        working_excerpt_chars = max(200, int(working_excerpt_chars * 0.8))
        context = build_compact_evidence_context(
            selected, research_summaries, working_excerpt_chars
        )

    full_context = format_evidence_context(research_summaries, records)
    stats = {
        "version": EVIDENCE_SELECTION_VERSION,
        "budget": asdict(budget),
        "effective_excerpt_chars": working_excerpt_chars,
        "sources_before": len(records),
        "sources_after": len(selected),
        "sources_dropped": max(0, len(records) - len(selected)),
        "near_duplicates_dropped": duplicates_dropped,
        "budget_dropped": budget_dropped,
        "chars_before": len(full_context),
        "chars_after": len(context),
        "approx_tokens_before": len(full_context) // _CHARS_PER_TOKEN,
        "approx_tokens_after": len(context) // _CHARS_PER_TOKEN,
        "token_estimate_method": "characters/4 heuristic; not a provider tokenizer",
        "queries_after": len(_group_queries(selected, research_summaries)),
        "selected_source_ids": [record.get("source_id") for record in selected],
        "selected_source_types": sorted(
            {
                str(record.get("source_type"))
                for record in selected
                if record.get("source_type")
            }
        ),
        "before": _source_stats(records),
        "after": _source_stats(selected),
    }
    return {"selected": selected, "context": context, "stats": stats}
