"""Process-wide token-usage accounting for Ollama calls.

Ollama exposes ``prompt_eval_count`` and ``eval_count`` in its response. The
original client logged these values but did not expose them to callers, so the
performance harness had to report token usage as unavailable. This module keeps
a small, thread-safe counter that the client updates and benchmarks can read.

Counts are only recorded when the provider actually returns them. Missing
counts remain missing; nothing is estimated from text length.
"""

from __future__ import annotations

import threading
from typing import Any, Dict, Optional

__all__ = ["get_usage", "record_usage", "reset_usage", "usage_available"]

_LOCK = threading.Lock()
_USAGE: Dict[str, Any] = {
    "calls": 0,
    "calls_with_usage": 0,
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0,
    "model": None,
}


def _value(source: Any, name: str) -> Optional[int]:
    try:
        value = source[name]
    except (KeyError, TypeError, AttributeError, IndexError):
        value = getattr(source, name, None)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= 0 else None


def record_usage(
    response: Any = None,
    *,
    prompt_tokens: Optional[int] = None,
    completion_tokens: Optional[int] = None,
    model: Optional[str] = None,
) -> None:
    """Record one provider call. Missing counts are not estimated."""
    prompt = prompt_tokens
    completion = completion_tokens
    if response is not None:
        if prompt is None:
            prompt = _value(response, "prompt_eval_count")
        if completion is None:
            completion = _value(response, "eval_count")
        if model is None:
            try:
                candidate = response["model"]
            except (KeyError, TypeError, AttributeError, IndexError):
                candidate = getattr(response, "model", None)
            if isinstance(candidate, str) and candidate.strip():
                model = candidate
    with _LOCK:
        _USAGE["calls"] += 1
        if model:
            _USAGE["model"] = model
        if prompt is not None or completion is not None:
            _USAGE["calls_with_usage"] += 1
            _USAGE["prompt_tokens"] += int(prompt or 0)
            _USAGE["completion_tokens"] += int(completion or 0)
            _USAGE["total_tokens"] += int(prompt or 0) + int(completion or 0)


def reset_usage() -> None:
    """Reset counters to zero (used by benchmark harnesses)."""
    with _LOCK:
        for key in ("calls", "calls_with_usage", "prompt_tokens", "completion_tokens", "total_tokens"):
            _USAGE[key] = 0
        _USAGE["model"] = None


def get_usage() -> Dict[str, Any]:
    """Return a copy of the current usage counters."""
    with _LOCK:
        return dict(_USAGE)


def usage_available() -> bool:
    """True when at least one provider response exposed token counts."""
    with _LOCK:
        return _USAGE["calls_with_usage"] > 0
