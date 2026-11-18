"""Append-only evaluation record storage.

Records are newline-delimited JSON so historical entries are never rewritten.
This module does not touch PostgreSQL and contains no network access.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from evaluation.engine import record_digest

__all__ = [
    "DEFAULT_RECORD_PATH",
    "append_evaluation_record",
    "load_evaluation_records",
    "summarise_records",
]

DEFAULT_RECORD_PATH = (
    Path(__file__).resolve().parent.parent
    / "benchmarks"
    / "results"
    / "evaluation_records.jsonl"
)


def append_evaluation_record(
    record: Dict[str, Any],
    path: str | Path = DEFAULT_RECORD_PATH,
) -> Path:
    """Append one record as a JSON line and return the file path.

    The file is opened in append mode. Existing lines are never modified.
    A deterministic ``record_digest`` is added when absent.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(record or {})
    payload.setdefault("record_digest", record_digest(payload))
    line = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    with target.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(line + "\n")
    return target


def load_evaluation_records(
    path: str | Path = DEFAULT_RECORD_PATH,
) -> List[Dict[str, Any]]:
    """Load all valid JSON records; malformed lines are skipped with a warning."""
    target = Path(path)
    if not target.exists():
        return []
    records: List[Dict[str, Any]] = []
    with target.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                value = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                records.append(value)
    return records


def summarise_records(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate evaluation records without hiding unmeasured components."""
    scores: List[float] = []
    per_metric: Dict[str, List[float]] = {}
    unresolved_claims = 0
    unsupported_citations = 0
    measured_counts: Dict[str, int] = {}
    for record in records or []:
        if not isinstance(record, dict):
            continue
        overall = record.get("overall_score")
        if isinstance(overall, (int, float)):
            scores.append(float(overall))
        metrics = record.get("metrics") or {}
        if isinstance(metrics, dict):
            for name, component in metrics.items():
                if not isinstance(component, dict):
                    continue
                value = component.get("score")
                if isinstance(value, (int, float)):
                    per_metric.setdefault(name, []).append(float(value))
                    measured_counts[name] = measured_counts.get(name, 0) + 1
        unresolved_claims += len(record.get("unresolved_claims") or [])
        citation_details = (metrics.get("citation_quality") or {}).get("details") or {}
        unsupported_citations += int(citation_details.get("unsupported_citations") or 0)

    def _mean(values: List[float]) -> Optional[float]:
        return round(sum(values) / len(values), 4) if values else None

    return {
        "records": len(records or []),
        "overall_score_mean": _mean(scores),
        "metric_means": {name: _mean(values) for name, values in per_metric.items()},
        "measured_counts": measured_counts,
        "unresolved_claims_reported": unresolved_claims,
        "unsupported_citations_reported": unsupported_citations,
    }
