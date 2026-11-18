"""Reusable latency, success-rate, and benchmark-comparison helpers.

All timings must be supplied by the caller from ``time.perf_counter()``. This
module never manufactures a latency. Missing measurements stay ``None``.
"""

from __future__ import annotations

import math
import os
import platform
import statistics
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

__all__ = [
    "LatencyRecorder",
    "compare_benchmark_reports",
    "machine_metadata",
    "percentile",
    "success_rate",
    "summarise_latencies",
]


def _numeric(values: Iterable[Any]) -> List[float]:
    result: List[float] = []
    for value in values or []:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if math.isnan(float(value)) or math.isinf(float(value)):
            continue
        result.append(float(value))
    return result


def percentile(values: Sequence[Any], p: float) -> Optional[float]:
    """Linear-interpolation percentile (the common ``numpy`` default).

    Returns ``None`` for an empty sample. Percentiles are not extrapolated.
    """
    sample = sorted(_numeric(values))
    if not sample:
        return None
    if len(sample) == 1:
        return round(sample[0], 6)
    rank = (len(sample) - 1) * (max(0.0, min(100.0, float(p))) / 100.0)
    lower = int(math.floor(rank))
    upper = int(math.ceil(rank))
    if lower == upper:
        return round(sample[lower], 6)
    weight = rank - lower
    return round(sample[lower] * (1 - weight) + sample[upper] * weight, 6)


def summarise_latencies(values: Sequence[Any]) -> Dict[str, Any]:
    """Summarise a latency sample with P50/P95 and explicit sample size."""
    sample = _numeric(values)
    if not sample:
        return {
            "samples": 0,
            "mean": None,
            "median": None,
            "p50": None,
            "p95": None,
            "min": None,
            "max": None,
            "stdev": None,
            "status": "not_measured",
        }
    return {
        "samples": len(sample),
        "mean": round(sum(sample) / len(sample), 6),
        "median": round(statistics.median(sample), 6),
        "p50": percentile(sample, 50),
        "p95": percentile(sample, 95),
        "min": round(min(sample), 6),
        "max": round(max(sample), 6),
        "stdev": round(statistics.stdev(sample), 6) if len(sample) > 1 else 0.0,
        "status": "measured",
    }


def success_rate(successes: int, total: int) -> Optional[float]:
    """Return successes/total, or ``None`` when no executions occurred."""
    if not isinstance(total, int) or total <= 0:
        return None
    return round(max(0, successes) / total, 4)


def machine_metadata(
    *,
    llm_model: Optional[str] = None,
    ollama_host: Optional[str] = None,
    search_provider: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Collect non-secret machine/model metadata for reproducible reports."""
    metadata = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "cpu_count": os.cpu_count(),
        "llm_model": llm_model,
        "ollama_host": ollama_host,
        "search_provider": search_provider,
    }
    if extra:
        metadata.update(extra)
    return metadata


class LatencyRecorder:
    """Collect named latency samples during a benchmark run."""

    def __init__(self) -> None:
        self._samples: Dict[str, List[float]] = {}

    def record(self, name: str, seconds: Any) -> None:
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
            return
        value = float(seconds)
        if value < 0 or math.isnan(value) or math.isinf(value):
            return
        self._samples.setdefault(str(name), []).append(value)

    def extend(self, name: str, values: Iterable[Any]) -> None:
        for value in values or []:
            self.record(name, value)

    def raw(self) -> Dict[str, List[float]]:
        return {name: list(values) for name, values in self._samples.items()}

    def summary(self) -> Dict[str, Dict[str, Any]]:
        return {
            name: summarise_latencies(values)
            for name, values in sorted(self._samples.items())
        }


def _get_path(report: Dict[str, Any], path: Sequence[str]) -> Any:
    current: Any = report
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def compare_benchmark_reports(
    baseline: Dict[str, Any],
    candidate: Dict[str, Any],
    metric_paths: Sequence[Sequence[str]],
) -> Dict[str, Any]:
    """Compare two benchmark artifacts without asserting causality.

    Each comparison reports the baseline value, candidate value, absolute
    delta, and percent change. Percent change is ``None`` when the baseline is
    missing or zero. A comparison is observational unless the caller ran a
    controlled A/B.
    """
    comparisons: Dict[str, Any] = {}
    for path in metric_paths:
        name = ".".join(path)
        before = _get_path(baseline, path)
        after = _get_path(candidate, path)
        before_value = before if isinstance(before, (int, float)) and not isinstance(before, bool) else None
        after_value = after if isinstance(after, (int, float)) and not isinstance(after, bool) else None
        delta = (
            round(float(after_value) - float(before_value), 6)
            if before_value is not None and after_value is not None
            else None
        )
        percent = (
            round(delta / float(before_value) * 100, 3)
            if delta is not None and before_value not in (None, 0)
            else None
        )
        comparisons[name] = {
            "baseline": before_value,
            "candidate": after_value,
            "delta": delta,
            "percent_change": percent,
            "status": "measured" if delta is not None else "not_measured",
        }
    return {
        "baseline_mode": baseline.get("mode"),
        "candidate_mode": candidate.get("mode"),
        "baseline_generated_at": baseline.get("generated_at"),
        "candidate_generated_at": candidate.get("generated_at"),
        "comparisons": comparisons,
        "causality_note": (
            "Observed differences only. Do not claim a causal improvement unless "
            "the runs were a controlled A/B with identical inputs and environment."
        ),
    }
