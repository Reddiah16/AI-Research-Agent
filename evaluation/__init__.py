"""Reproducible research-quality evaluation (standard library only).

Public entry points:
    evaluation.engine       — scoring engine and record builder
    evaluation.metrics      — documented score definitions
    evaluation.store        — append-only JSONL record storage
"""

from evaluation.engine import (
    EVALUATION_RECORD_VERSION,
    dataset_readiness,
    evaluate_open_state,
    evaluate_report,
    evaluate_state,
    get_question,
    load_benchmark_dataset,
    record_digest,
)

__all__ = [
    "EVALUATION_RECORD_VERSION",
    "dataset_readiness",
    "evaluate_open_state",
    "evaluate_report",
    "evaluate_state",
    "get_question",
    "load_benchmark_dataset",
    "record_digest",
]
