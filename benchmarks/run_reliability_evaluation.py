#!/usr/bin/env python3
"""Reliability evaluation runner.

Runs the deterministic graceful-failure suite against a temporary SQLite
database. No live Ollama, DuckDuckGo, or PostgreSQL connection is required and
no existing database row is modified.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.reliability import RELIABILITY_VERSION, run_reliability_suite  # noqa: E402

RESULTS_DIR = PROJECT_ROOT / "benchmarks" / "results"
LATEST = RESULTS_DIR / "reliability_eval_latest.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _write_json(path: Path, payload: Dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    return path


def run() -> Dict[str, Any]:
    suite = run_reliability_suite()
    report = {
        "generated_at": _now(),
        "mode": "deterministic_mocked",
        "suite_version": RELIABILITY_VERSION,
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
        "summary": {
            "scenarios_total": suite["scenarios_total"],
            "scenarios_passed": suite["scenarios_passed"],
            "scenarios_failed": suite["scenarios_failed"],
        },
        "scenarios": suite["results"],
        "database": suite["database"],
        "limitations": suite["limitations"],
    }
    timestamped = RESULTS_DIR / f"reliability_eval_{_stamp()}.json"
    _write_json(timestamped, report)
    _write_json(LATEST, report)
    report["output_path"] = str(timestamped)
    return report


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    args = parser.parse_args(argv)
    del args

    report = run()
    print(
        json.dumps(
            {
                **report["summary"],
                "scenarios": [
                    {
                        "scenario": item["scenario"],
                        "passed": item["passed"],
                        "actual_outcome": item["actual_outcome"],
                    }
                    for item in report["scenarios"]
                ],
                "output_path": report["output_path"],
            },
            indent=2,
        )
    )
    return 0 if report["summary"]["scenarios_failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
