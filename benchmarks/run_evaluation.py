#!/usr/bin/env python3
"""Command-line evaluation engine for the AI Research Agent.

Modes
-----
Readiness audit (default):
    python benchmarks/run_evaluation.py --readiness

Evaluate a saved/generated report JSON against one benchmark question:
    python benchmarks/run_evaluation.py --report path/to/report.json --question Q01

Evaluate a markdown report file:
    python benchmarks/run_evaluation.py --report path/to/report.md --question Q01 --title "My report"

Deterministic self-check (synthetic fixture; not a real pipeline result):
    python benchmarks/run_evaluation.py --self-check

The engine never writes the benchmark dataset or the database. Records are
only appended when ``--record`` is explicitly supplied.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.engine import (  # noqa: E402
    dataset_readiness,
    evaluate_report,
    evaluate_state,
    get_question,
    load_benchmark_dataset,
    record_digest,
)
from evaluation.store import append_evaluation_record  # noqa: E402

DEFAULT_DATASET = PROJECT_ROOT / "benchmarks" / "datasets" / "benchmark_v3.json"


def _print(value) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False))


def _load_report(path: Path):
    if path.suffix.lower() == ".json":
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    return path.read_text(encoding="utf-8")


def _self_check(dataset_path: Path) -> int:
    """Deterministic sanity check of the scoring engine.

    This uses a synthetic report assembled from public benchmark wording. It
    proves the engine runs and that unresolved claims are excluded; it is not
    a live research result and must not be reported as one.
    """
    dataset = load_benchmark_dataset(dataset_path)
    question = get_question(dataset, "Q01")
    claims = question["ground_truth"]["claims"]
    verified = [claim for claim in claims if claim["status"] == "verified"]
    body = (
        "# Retrieval Augmented Generation Architecture\n\n"
        "## Introduction\n"
        f"{verified[0]['effective_claim_text']} The architecture separates the "
        "retriever from the generator so that external documents can be updated "
        "without retraining the language model. This separation is the core "
        "reason retrieval augmented generation improves factual grounding.\n\n"
        "## Key Points\n"
        f"- {verified[1]['effective_claim_text']}\n"
        f"- {verified[2]['effective_claim_text']}\n"
        "- Chunking, vector indexing, dense retrieval, and prompt augmentation "
        "must be evaluated together because each stage constrains the next. "
        "Chunk size affects retrieval precision; the vector index affects recall "
        "and latency; the prompt template determines how much retrieved context "
        "the generator can use; and augmentation quality determines whether the "
        "final answer stays grounded in the retrieved passages. A change in one "
        "stage can therefore change the measured quality of the whole pipeline, "
        "which is why end-to-end evaluation is necessary instead of isolated "
        "component checks.\n\n"
        "## Applications\n"
        "- Enterprise question answering over internal documents, where chunking "
        "and dense retrieval reduce hallucination and expose citations.\n"
        "- Technical support assistants that retrieve product documentation and "
        "augment a prompt with the most relevant passages.\n"
        "- Research literature review, where vector indexing over paper abstracts "
        "supports similarity search and prompt augmentation.\n\n"
        "## Challenges\n"
        "- Retrieval quality bounds answer quality: if the index misses the right "
        "passage, the generator cannot recover it reliably.\n"
        "- Chunking and vector indexing choices trade recall against latency and "
        "storage, so they must be measured rather than assumed.\n"
        "- Prompt augmentation can introduce irrelevant or adversarial text, so "
        "retrieved content must be treated as untrusted data.\n\n"
        "## Conclusion\n"
        "Retrieval augmented generation combines parametric memory with external "
        "retrieval. Careful chunking, vector indexing, dense retrieval and prompt "
        "augmentation are all required for a useful system.\n\n"
        "## References\n"
        "1. Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks – "
        "https://arxiv.org/abs/2005.11401\n"
    )
    report = {
        "title": "Retrieval Augmented Generation Architecture",
        "report": body,
        "sources": ["https://arxiv.org/abs/2005.11401"],
        "unsupported_citations": [],
        "key_findings": [
            {"claim": verified[0]["effective_claim_text"], "source_ids": ["SRC-001"]},
            {"claim": verified[1]["effective_claim_text"], "source_ids": ["SRC-001"]},
            {"claim": verified[2]["effective_claim_text"], "source_ids": ["SRC-001"]},
        ],
        "generation_mode": "json",
    }
    record = evaluate_report(
        question,
        report,
        dataset=dataset,
        grounding={
            "retrieved_sources_count": 1,
            "grounded_sources_count": 1,
        },
        research_metrics={"search_results": 3, "usable_evidence": 3},
        claim_citation_coverage=1.0,
        evaluated_at="2026-11-13T00:00:00+00:00",
    )

    failures = []
    for component in ("relevance", "evidence_coverage", "citation_quality", "completeness", "factual_support"):
        if record["metrics"][component]["score"] is None:
            failures.append(f"{component} was not measured")
    if record["metrics"]["completeness"]["score"] < 0.99:
        failures.append("completeness did not reach 1.0 on the complete fixture")
    if record["metrics"]["factual_support"]["score"] != 1.0:
        failures.append("verified-claim support was not 1.0 on the exact fixture")
    if record_digest(record) != record_digest(dict(record)):
        failures.append("record digest is not deterministic")

    # A question with unresolved claims must exclude them from the score.
    unresolved_question = get_question(dataset, "Q17")
    unresolved_record = evaluate_report(
        unresolved_question,
        {"report": body, "sources": [], "unsupported_citations": []},
        dataset=dataset,
        evaluated_at="2026-11-13T00:00:00+00:00",
    )
    unresolved_ids = {
        item["claim_id"] for item in unresolved_record["unresolved_claims"]
    }
    expected_unresolved = {
        claim["claim_id"]
        for claim in unresolved_question["ground_truth"]["claims"]
        if claim["status"] != "verified"
    }
    if expected_unresolved and unresolved_ids != expected_unresolved:
        failures.append(
            f"unresolved claims were not preserved: expected {sorted(expected_unresolved)}, "
            f"got {sorted(unresolved_ids)}"
        )

    # Unsupported citations must reduce the citation score, not become sources.
    bad_record = evaluate_report(
        question,
        {
            "report": body,
            "sources": ["https://arxiv.org/abs/2005.11401"],
            "unsupported_citations": ["https://fabricated.invalid/source"],
        },
        dataset=dataset,
        evaluated_at="2026-11-13T00:00:00+00:00",
    )
    if bad_record["metrics"]["citation_quality"]["score"] >= 1.0:
        failures.append("unsupported citation did not reduce citation quality")

    print("SELF-CHECK (synthetic fixture; not a live pipeline result)")
    print(f"  dataset: {dataset['filename']} sha256={dataset['sha256'][:16]}…")
    print(f"  overall_score: {record['overall_score']}")
    print(f"  factual_support: {record['metrics']['factual_support']['score']}")
    print(f"  completeness: {record['metrics']['completeness']['score']}")
    print(f"  record_digest: {record_digest(record)}")
    print(f"  unresolved claims preserved for Q17: {sorted(unresolved_ids)}")
    if failures:
        print("\nSELF-CHECK FAILED")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("\nSELF-CHECK PASS")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="AI Research Agent evaluation engine")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET), help="Benchmark JSON path")
    parser.add_argument("--readiness", action="store_true", help="Print dataset readiness audit")
    parser.add_argument("--report", help="Report JSON/Markdown file to evaluate")
    parser.add_argument("--question", help="Benchmark question ID (e.g. Q01)")
    parser.add_argument("--title", default="", help="Title for a Markdown report")
    parser.add_argument("--support-threshold", type=float, default=0.55)
    parser.add_argument("--record", help="Append the record to this JSONL path")
    parser.add_argument("--self-check", action="store_true", help="Run deterministic engine self-check")
    args = parser.parse_args(argv)

    dataset = load_benchmark_dataset(args.dataset)

    if args.self_check:
        return _self_check(Path(args.dataset))

    if args.report:
        if not args.question:
            parser.error("--report requires --question")
        question = get_question(dataset, args.question)
        report_value = _load_report(Path(args.report))
        if isinstance(report_value, dict):
            if "metrics" in report_value and "report" in report_value:
                # A saved LangGraph state-like object.
                record = evaluate_state(
                    question,
                    report_value,
                    dataset=dataset,
                    support_threshold=args.support_threshold,
                )
            else:
                if args.title and not report_value.get("title"):
                    report_value["title"] = args.title
                record = evaluate_report(
                    question,
                    report_value,
                    dataset=dataset,
                    support_threshold=args.support_threshold,
                )
        else:
            record = evaluate_report(
                question,
                {"title": args.title, "report": str(report_value)},
                dataset=dataset,
                support_threshold=args.support_threshold,
            )
        if args.record:
            path = append_evaluation_record(record, args.record)
            print(f"Record appended: {path}")
        _print(record)
        return 0

    # Default and explicit readiness mode.
    _print(dataset_readiness(dataset))
    return 0


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
