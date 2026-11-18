"""Read timestamped evaluation artifacts for the Streamlit dashboard.

The dashboard deliberately reads JSON files from ``benchmarks/results/`` rather
than adding tables to PostgreSQL. Missing or malformed artifacts are reported
explicitly; no value is synthesised.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

__all__ = [
    "ARTIFACT_FILES",
    "DEFAULT_RESULTS_DIR",
    "load_latest_artifacts",
]

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RESULTS_DIR = PROJECT_ROOT / "benchmarks" / "results"

ARTIFACT_FILES: Dict[str, str] = {
    "golden": "golden_eval_latest.json",
    "security": "security_eval_latest.json",
    "reliability": "reliability_eval_latest.json",
    "performance": "performance_benchmark_latest.json",
}

ARTIFACT_LABELS: Dict[str, str] = {
    "golden": "Golden evaluation",
    "security": "Security suite",
    "reliability": "Reliability suite",
    "performance": "Performance benchmark",
}


def _load_one(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {
            "status": "missing",
            "path": str(path),
            "filename": path.name,
            "data": None,
        }
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "status": "unreadable",
            "path": str(path),
            "filename": path.name,
            "error": f"{type(exc).__name__}: {exc}",
            "data": None,
        }
    return {
        "status": "loaded",
        "path": str(path),
        "filename": path.name,
        "data": data if isinstance(data, dict) else None,
    }


def load_latest_artifacts(
    directory: Optional[Path | str] = None,
) -> Dict[str, Dict[str, Any]]:
    """Load each known latest artifact, preserving per-file status.

    Returns a mapping keyed by ``golden``, ``security``, ``reliability`` and
    ``performance``. Every entry has ``status`` (``loaded``, ``missing`` or
    ``unreadable``) and the parsed ``data`` when available.
    """
    base = Path(directory) if directory is not None else DEFAULT_RESULTS_DIR
    return {
        name: _load_one(base / filename)
        for name, filename in ARTIFACT_FILES.items()
    }
