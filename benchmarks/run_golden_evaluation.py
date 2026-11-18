#!/usr/bin/env python3
"""Golden-dataset evaluation runner.

Modes
-----
``--mode mocked`` (default)
    Validates the dataset and exercises the retrieval-metric pipeline with
    deterministic perfect/degraded/unlabelled fixtures. This is a metric
    self-check, not a measurement of the live agent.

``--mode live``
    Runs the real LangGraph pipeline with Ollama and DuckDuckGo on a bounded
    set of golden cases, then measures retrieval and answer quality. Requires
    ``--confirm-live`` and reachable services. Results are host/model
    dependent and are never fabricated.

Outputs timestamped JSON plus ``golden_eval_latest.json`` and an append-only
JSONL record file. No PostgreSQL row is written or modified.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.config import ResearchConfig  # noqa: E402
from evaluation.answer_quality import (  # noqa: E402
    ANSWER_QUALITY_WEIGHTS,
    evaluate_case_answer_quality,
    evaluate_expected_behavior,
)
from evaluation.engine import evaluate_state  # noqa: E402
from evaluation.golden_dataset import (  # noqa: E402
    DEFAULT_GOLDEN_DATASET,
    DEFAULT_GOLDEN_MANIFEST,
    load_golden_dataset,
    to_engine_question,
)
from evaluation.performance import summarise_latencies  # noqa: E402
from evaluation.retrieval import (  # noqa: E402
    aggregate_retrieval_metrics,
    documents_from_state,
    evaluate_retrieval_case,
)
from generation.usage import get_usage, reset_usage  # noqa: E402
from graph.workflow import run_research_pipeline  # noqa: E402

RESULTS_DIR = PROJECT_ROOT / "benchmarks" / "results"
LATEST_SUMMARY = RESULTS_DIR / "golden_eval_latest.json"
LATEST_RECORDS = RESULTS_DIR / "golden_eval_records.jsonl"


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


def _append_jsonl(path: Path, payload: Dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    return path


def _environment(config: ResearchConfig) -> Dict[str, Any]:
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "cpu_count": os.cpu_count(),
        "llm_model": config.llm_model,
        "ollama_host": config.ollama_host,
        "search_provider": config.search_provider,
        "max_results": config.max_results,
        "enable_page_fetch": config.enable_page_fetch,
        "max_total_pages_per_run": config.max_total_pages_per_run,
    }


def _mock_retrieval_fixtures(dataset: Dict[str, Any]) -> Dict[str, Any]:
    """Exercise retrieval metrics with perfect/degraded/unlabelled fixtures."""
    cases = dataset["cases"]
    labelled = [
        case for case in cases
        if (case.get("retrieval_ground_truth") or {}).get("status") == "available"
    ]
    distractors = [
        "https://distractor.example/a",
        "https://distractor.example/b",
        "https://distractor.example/c",
        "https://distractor.example/d",
    ]

    perfect_results = []
    degraded_results = []
    unlabelled_results = []
    for index, case in enumerate(labelled[:20]):
        relevant = case["retrieval_ground_truth"]["relevant_urls"]
        case_id = case["id"]
        perfect = [{"url": url} for url in relevant] + [
            {"url": distractors[i % len(distractors)]} for i in range(3)
        ]
        degraded = (
            [{"url": distractors[index % len(distractors)]}, {"url": relevant[0]}]
            + [{"url": distractors[(index + 1) % len(distractors)]}]
            + [{"url": url} for url in relevant[1:2]]
            + [{"url": distractors[(index + 2) % len(distractors)]}]
        )
        perfect_results.append(evaluate_retrieval_case(case_id, perfect, relevant))
        degraded_results.append(evaluate_retrieval_case(case_id, degraded, relevant))

    for case in cases:
        if (case.get("retrieval_ground_truth") or {}).get("status") != "available":
            unlabelled_results.append(
                evaluate_retrieval_case(
                    case["id"],
                    [{"url": "https://example.com/unlabelled"}],
                    [],
                )
            )

    perfect_aggregate = aggregate_retrieval_metrics(perfect_results)
    degraded_aggregate = aggregate_retrieval_metrics(degraded_results)
    unlabelled_aggregate = aggregate_retrieval_metrics(unlabelled_results)

    checks = {
        "perfect_recall_at_5_is_1": (
            perfect_aggregate["metrics"].get("recall@5", {}).get("mean") == 1.0
        ),
        "perfect_mrr_is_1": perfect_aggregate["metrics"].get("mrr", {}).get("mean") == 1.0,
        "perfect_ndcg_at_5_is_1": (
            perfect_aggregate["metrics"].get("ndcg@5", {}).get("mean") == 1.0
        ),
        "degraded_mrr_below_perfect": (
            degraded_aggregate["metrics"].get("mrr", {}).get("mean") is not None
            and degraded_aggregate["metrics"]["mrr"]["mean"] < 1.0
        ),
        "unlabelled_cases_are_excluded": unlabelled_aggregate["measured_cases"] == 0,
        "unlabelled_values_are_none": all(
            item["value"] is None
            for item in unlabelled_results[0]["metrics"].values()
        ) if unlabelled_results else False,
    }

    return {
        "fixture_cases_labelled": len(perfect_results),
        "fixture_cases_unlabelled": len(unlabelled_results),
        "perfect": perfect_aggregate,
        "degraded": degraded_aggregate,
        "unlabelled": unlabelled_aggregate,
        "checks": checks,
        "all_checks_passed": all(checks.values()),
        "label": "mocked_metric_validation_not_agent_quality",
    }


def run_mocked(dataset_path: Path, manifest_path: Path) -> Dict[str, Any]:
    dataset = load_golden_dataset(dataset_path, manifest_path)
    fixtures = _mock_retrieval_fixtures(dataset)
    report = {
        "generated_at": _now(),
        "mode": "mocked",
        "dataset": {
            "filename": dataset["filename"],
            "sha256": dataset["sha256"],
            "version": (dataset.get("manifest") or {}).get("version"),
            "manifest": dataset.get("manifest"),
            "validation": dataset["validation"],
        },
        "retrieval": fixtures,
        "answer_quality": {
            "status": "not_measured",
            "reason": "Mocked mode validates metric plumbing only; it does not score agent answers.",
            "weights": ANSWER_QUALITY_WEIGHTS,
        },
        "limitations": [
            "Mocked fixtures test the metric implementation, not the live agent.",
            "No Ollama, DuckDuckGo, or answer-generation result is used.",
            "Do not present mocked numbers as benchmark quality.",
        ],
    }
    timestamped = RESULTS_DIR / f"golden_eval_mocked_{_stamp()}.json"
    _write_json(timestamped, report)
    _write_json(LATEST_SUMMARY, report)
    report["output_path"] = str(timestamped)
    return report


def _select_cases(
    cases: Sequence[Dict[str, Any]],
    ids: Optional[Sequence[str]],
    limit: int,
    include_synthetic: bool,
) -> List[Dict[str, Any]]:
    selected = list(cases)
    if not include_synthetic:
        selected = [
            case for case in selected
            if (case.get("provenance") or {}).get("type") == "verified_reference"
        ]
    if ids:
        wanted = {value.strip() for value in ids if value.strip()}
        selected = [case for case in selected if case.get("id") in wanted]
    return selected[: max(0, limit)]


def run_live(
    dataset_path: Path,
    manifest_path: Path,
    *,
    limit: int,
    ids: Optional[Sequence[str]],
    include_synthetic: bool,
) -> Dict[str, Any]:
    dataset = load_golden_dataset(dataset_path, manifest_path)
    config = ResearchConfig()
    selected = _select_cases(
        dataset["cases"], ids=ids, limit=limit, include_synthetic=include_synthetic
    )
    if not selected:
        raise ValueError("no cases selected for live evaluation")

    from generation.llm import check_ollama_connection

    if not check_ollama_connection(config):
        raise RuntimeError("Ollama is not reachable; refusing to fabricate live results")

    cases_out: List[Dict[str, Any]] = []
    retrieval_results: List[Dict[str, Any]] = []
    answer_records: List[Dict[str, Any]] = []
    behaviour_results: List[Dict[str, Any]] = []
    latencies: List[float] = []
    errors = 0
    reset_usage()

    for index, case in enumerate(selected, start=1):
        usage_before = get_usage()
        started = time.perf_counter()
        error = None
        state: Dict[str, Any] = {}
        try:
            state = run_research_pipeline(str(case["question"]), config)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            errors += 1
        elapsed = time.perf_counter() - started
        usage_after = get_usage()
        if error is None:
            latencies.append(elapsed)

        retrieval = evaluate_retrieval_case(
            case["id"],
            documents_from_state(state),
            (case.get("retrieval_ground_truth") or {}).get("relevant_urls") or [],
        )
        retrieval_results.append(retrieval)

        answer = evaluate_case_answer_quality(case, state)
        answer_records.append(answer)

        behaviour = None
        if (case.get("provenance") or {}).get("type") == "synthetic":
            behaviour = evaluate_expected_behavior(case, (state or {}).get("report"))
            behaviour_results.append({
                "case_id": case["id"],
                "category": case["category"],
                **behaviour,
            })

        engine_record = None
        if error is None:
            try:
                engine_record = evaluate_state(
                    to_engine_question(case),
                    state,
                    dataset=dataset,
                )
            except Exception as exc:
                engine_record = {"status": "evaluation_error", "error": str(exc)}

        cases_out.append(
            {
                "case_id": case["id"],
                "question": case["question"],
                "category": case["category"],
                "difficulty": case["difficulty"],
                "provenance": (case.get("provenance") or {}).get("type"),
                "status": state.get("status") if state else "error",
                "error": error,
                "elapsed_seconds": round(elapsed, 3),
                "pipeline_latency_seconds": (state or {}).get("pipeline_latency_seconds"),
                "planner_latency_seconds": (state or {}).get("planner_latency"),
                "research_latency_seconds": (state or {}).get("research_latency"),
                "editor_latency_seconds": (state or {}).get("editor_latency"),
                "source_count": len((state or {}).get("sources") or []),
                "retrieved_urls": [
                    document.get("url")
                    for document in documents_from_state(state)
                ],
                "relevant_urls": list(
                    (case.get("retrieval_ground_truth") or {}).get("relevant_urls")
                    or []
                ),
                "generation_mode": (state or {}).get("editor_generation_mode"),
                "token_usage_delta": {
                    "calls": usage_after["calls"] - usage_before["calls"],
                    "prompt_tokens": usage_after["prompt_tokens"] - usage_before["prompt_tokens"],
                    "completion_tokens": usage_after["completion_tokens"] - usage_before["completion_tokens"],
                    "total_tokens": usage_after["total_tokens"] - usage_before["total_tokens"],
                    "available": usage_after["calls_with_usage"] > usage_before["calls_with_usage"],
                },
                "retrieval": retrieval,
                "answer_quality": answer,
                "expected_behavior": behaviour,
                "engine_record": engine_record,
            }
        )
        print(
            f"[{index}/{len(selected)}] {case['id']} {state.get('status') if state else 'error'} "
            f"{elapsed:.1f}s sources={len((state or {}).get('sources') or [])} "
            f"error={error or 'none'}"
        )

    retrieval_aggregate = aggregate_retrieval_metrics(retrieval_results)
    component_names: List[str] = []
    for record in answer_records:
        for name in (record.get("components") or {}):
            if name not in component_names:
                component_names.append(name)
    answer_aggregate: Dict[str, Any] = {}
    for name in component_names:
        values = [
            float(record["components"][name]["score"])
            for record in answer_records
            if isinstance(record.get("components", {}).get(name), dict)
            and isinstance(record["components"][name].get("score"), (int, float))
        ]
        answer_aggregate[name] = {
            "mean": round(sum(values) / len(values), 4) if values else None,
            "measured_cases": len(values),
            "status": "measured" if values else "not_measurable",
        }
    overall_values = [
        float(record["overall_score"])
        for record in answer_records
        if isinstance(record.get("overall_score"), (int, float))
    ]
    behaviour_measured = [
        item for item in behaviour_results if item.get("score") is not None
    ]
    behaviour_pass = [
        item for item in behaviour_measured if float(item["score"]) >= 1.0
    ]

    total_usage = get_usage()
    report = {
        "generated_at": _now(),
        "mode": "live",
        "dataset": {
            "filename": dataset["filename"],
            "sha256": dataset["sha256"],
            "version": (dataset.get("manifest") or {}).get("version"),
            "validation": dataset["validation"],
        },
        "environment": _environment(config),
        "cases_requested": len(selected),
        "cases_completed": len(selected) - errors,
        "errors": errors,
        "retrieval": retrieval_aggregate,
        "answer_quality": {
            "overall_mean": (
                round(sum(overall_values) / len(overall_values), 4)
                if overall_values
                else None
            ),
            "components": answer_aggregate,
            "weights": ANSWER_QUALITY_WEIGHTS,
            "measured_cases": len(overall_values),
        },
        "expected_behavior": {
            "synthetic_cases": len(behaviour_results),
            "measured": len(behaviour_measured),
            "passed": len(behaviour_pass),
            "pass_rate": (
                round(len(behaviour_pass) / len(behaviour_measured), 4)
                if behaviour_measured
                else None
            ),
            "results": behaviour_results,
        },
        "latency": summarise_latencies(latencies),
        "token_usage": total_usage,
        "cases": cases_out,
        "limitations": [
            "Live results depend on DuckDuckGo availability, model version, and host load.",
            "Answer-quality components are deterministic lexical/structural proxies, not entailment or human judgement.",
            "Synthetic cases have no verified reference answer; expected-behaviour scores are heuristic.",
            "A small live sample is not a statistically representative benchmark.",
        ],
    }
    timestamped = RESULTS_DIR / f"golden_eval_live_{_stamp()}.json"
    _write_json(timestamped, report)
    _write_json(LATEST_SUMMARY, report)
    for case in cases_out:
        _append_jsonl(
            LATEST_RECORDS,
            {
                "generated_at": report["generated_at"],
                "dataset_sha256": report["dataset"]["sha256"],
                "case_id": case["case_id"],
                "category": case["category"],
                "status": case["status"],
                "error": case["error"],
                "elapsed_seconds": case["elapsed_seconds"],
                "retrieval": case["retrieval"],
                "answer_quality": case["answer_quality"],
                "engine_record": case["engine_record"],
            },
        )
    report["output_path"] = str(timestamped)
    return report


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default=str(DEFAULT_GOLDEN_DATASET))
    parser.add_argument("--manifest", default=str(DEFAULT_GOLDEN_MANIFEST))
    parser.add_argument("--mode", choices=("mocked", "live"), default="mocked")
    parser.add_argument("--confirm-live", action="store_true")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--ids", default="", help="Comma-separated golden case IDs")
    parser.add_argument("--include-synthetic", action="store_true")
    args = parser.parse_args(argv)

    if args.mode == "live" and not args.confirm_live:
        print("Refusing --mode live without --confirm-live", file=sys.stderr)
        return 2

    ids = [value for value in args.ids.split(",")] if args.ids else None
    if args.mode == "live":
        report = run_live(
            Path(args.dataset),
            Path(args.manifest),
            limit=args.limit,
            ids=ids,
            include_synthetic=args.include_synthetic,
        )
    else:
        report = run_mocked(Path(args.dataset), Path(args.manifest))

    summary = {
        "mode": report["mode"],
        "dataset": report["dataset"]["filename"],
        "dataset_sha256": report["dataset"]["sha256"],
        "cases_completed": report.get("cases_completed"),
        "retrieval": report.get("retrieval", {}).get("metrics"),
        "answer_quality": report.get("answer_quality", {}).get("components"),
        "latency": report.get("latency"),
        "output_path": report.get("output_path"),
    }
    if report["mode"] == "mocked":
        summary.update(
            {
                "fixture_checks": report["retrieval"]["checks"],
                "all_fixture_checks_passed": report["retrieval"]["all_checks_passed"],
                "label": report["retrieval"]["label"],
            }
        )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
