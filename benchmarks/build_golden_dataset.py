#!/usr/bin/env python3
"""Build the reproducible golden evaluation dataset (JSONL).

The builder never invents reference answers or citations. Verified cases are
derived from the already-verified ``benchmark_v3.json`` claims and source
records. Behavioural cases (recent information, insufficient evidence,
conflicting sources, misleading assumptions) are explicitly marked
``synthetic`` and contain no reference evidence or retrieval labels.

The generated JSONL is deterministic: the same input dataset produces the same
bytes and SHA-256. Run:

    python benchmarks/build_golden_dataset.py

Outputs:
    benchmarks/datasets/golden_eval_v1.jsonl
    benchmarks/datasets/golden_eval_v1.manifest.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = PROJECT_ROOT / "benchmarks" / "datasets" / "benchmark_v3.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "benchmarks" / "datasets" / "golden_eval_v1.jsonl"
DEFAULT_MANIFEST = PROJECT_ROOT / "benchmarks" / "datasets" / "golden_eval_v1.manifest.json"

DATASET_VERSION = "1.0.0"
CREATED_AT = "2026-11-18T00:00:00Z"

CATEGORIES = (
    "factual_research",
    "multi_source_research",
    "comparison",
    "technical_research",
    "recent_information",
    "insufficient_evidence",
    "conflicting_sources",
    "misleading_assumption",
)

STRUCTURAL_REQUIREMENTS = [
    "Introduction",
    "Key Points",
    "Applications",
    "Challenges",
    "Conclusion",
    "References",
]

_QUESTION_TEMPLATES = {
    "factual_research": "What are the key verified facts about {topic}?",
    "multi_source_research": (
        "Using multiple independent sources, explain {topic} and identify its "
        "core components or trade-offs."
    ),
    "comparison": (
        "Compare the main approaches or options in {topic}. What are the key "
        "differences and trade-offs?"
    ),
    "technical_research": (
        "Explain the technical mechanisms and implementation details behind {topic}."
    ),
}

_SYNTHETIC_CASES: Dict[str, List[Dict[str, str]]] = {
    "recent_information": [
        {
            "question": "What are the latest stable versions of LangGraph, Streamlit, and PostgreSQL as of November 2026?",
            "expected_behavior": "Report only versions supported by retrieved, dated evidence; state the evidence cutoff and avoid asserting recency without a dated source.",
        },
        {
            "question": "Which open-weight LLM had the highest score on the most recent public benchmark this month?",
            "expected_behavior": "Name the benchmark and retrieval date; if no dated evidence is available, say the claim cannot be verified rather than guessing.",
        },
        {
            "question": "What are the newest EU AI Act compliance deadlines announced in 2026?",
            "expected_behavior": "Cite dated official sources and distinguish enacted deadlines from proposals; state when evidence is missing.",
        },
        {
            "question": "What are the current DuckDuckGo rate limits as of November 2026?",
            "expected_behavior": "Prefer official documentation and state the access date; do not present an undated blog value as current.",
        },
        {
            "question": "Which quantum computing company announced the largest qubit count in 2026?",
            "expected_behavior": "Distinguish announced, demonstrated, and independently verified qubit counts; state the retrieval cutoff.",
        },
        {
            "question": "What are the latest CVEs affecting LangChain in November 2026?",
            "expected_behavior": "Use dated advisory sources and avoid inventing CVE identifiers; if no advisory is retrieved, state that none were found in the retrieved evidence.",
        },
        {
            "question": "What is the current retail price of the RTX 5090 in India this week?",
            "expected_behavior": "Report dated retailer evidence and note that prices change; do not invent a current price.",
        },
        {
            "question": "Which cloud provider offers the lowest per-token LLM inference price as of November 2026?",
            "expected_behavior": "Compare dated published prices and state model/region assumptions; avoid an unsupported global claim.",
        },
        {
            "question": "What are the newest PostgreSQL 18 features released this year?",
            "expected_behavior": "Use official release notes with a version/date and separate shipped features from proposals.",
        },
        {
            "question": "What changed in the latest Python 3.14 patch release this month?",
            "expected_behavior": "Use official release notes and identify the exact patch version; state the evidence cutoff.",
        },
    ],
    "insufficient_evidence": [
        {
            "question": "What is the exact number of enterprises running Qwen3:4b in production in 2026?",
            "expected_behavior": "State that no reliable public count was retrieved; do not fabricate a number or citation.",
        },
        {
            "question": "What percentage of AI research papers in 2026 were written primarily by AI agents?",
            "expected_behavior": "State that the requested population statistic is not supported by the retrieved evidence; avoid a fabricated percentage.",
        },
        {
            "question": "How many hours per week do remote data engineers in Antarctica work?",
            "expected_behavior": "Identify the population as too small or unmeasured; state insufficient evidence rather than inventing a statistic.",
        },
        {
            "question": "What is the exact failure rate of the DuckDuckGo API in November 2026?",
            "expected_behavior": "State that no authoritative measured failure rate was retrieved; do not present an estimate as fact.",
        },
        {
            "question": "How many private companies use the exact benchmark_v3 dataset internally?",
            "expected_behavior": "State that private usage is unobservable from public evidence; do not invent a count.",
        },
        {
            "question": "What is the total energy consumption of all Ollama installations worldwide?",
            "expected_behavior": "State that global installation-level energy data is unavailable; explain the measurement gap.",
        },
        {
            "question": "What is the average salary of a prompt engineer in every country in 2026?",
            "expected_behavior": "State that complete cross-country salary data was not retrieved; avoid presenting partial data as global fact.",
        },
        {
            "question": "How many prompt injection attacks targeted this specific repository last month?",
            "expected_behavior": "State that private incident data is not observable; do not fabricate an incident count.",
        },
        {
            "question": "What is the exact market share of Streamlit among all Python UI frameworks in 2026?",
            "expected_behavior": "State that no authoritative census was retrieved and distinguish downloads from market share.",
        },
        {
            "question": "How many Qwen3:4b tokens were generated globally yesterday?",
            "expected_behavior": "State that global token generation is not publicly measured; do not invent a count.",
        },
    ],
    "conflicting_sources": [
        {
            "question": "Do passkeys reduce account takeover risk compared with TOTP? Sources disagree on the size of the effect.",
            "expected_behavior": "Surface the disagreement, attribute each position, and avoid declaring a single consensus value.",
        },
        {
            "question": "Is Rust or Go faster for high-throughput network services? Published benchmarks conflict.",
            "expected_behavior": "Present both benchmark positions with their conditions; avoid a context-free winner claim.",
        },
        {
            "question": "Does remote work increase or decrease productivity? Studies report conflicting results.",
            "expected_behavior": "Report the conflicting findings and their populations/methods; do not average them into a false consensus.",
        },
        {
            "question": "Are solid-state batteries safer than lithium-ion cells? Sources disagree on failure modes.",
            "expected_behavior": "Separate chemistry, test conditions, and failure modes; surface the conflict.",
        },
        {
            "question": "Does retrieval augmented generation eliminate hallucinations? Reports conflict on residual error rates.",
            "expected_behavior": "State that RAG reduces some errors but does not eliminate them; surface conflicting measurements.",
        },
        {
            "question": "Is Kubernetes costlier than serverless at low scale? Sources disagree.",
            "expected_behavior": "Present both cost models and their assumptions; avoid a universal answer.",
        },
        {
            "question": "Do open-weight models match proprietary models? Benchmarks conflict.",
            "expected_behavior": "Name the benchmark and conditions; surface disagreement rather than a single ranking.",
        },
        {
            "question": "Is nuclear fusion commercially viable before 2040? Sources disagree.",
            "expected_behavior": "Distinguish scientific breakeven, engineering feasibility, and commercial deployment; surface uncertainty.",
        },
        {
            "question": "Does page fetching improve research quality? Sources conflict.",
            "expected_behavior": "Present both positions and measurement conditions; avoid an unsupported universal claim.",
        },
        {
            "question": "Are vector databases necessary for small RAG systems? Sources disagree.",
            "expected_behavior": "Present the scale-dependent arguments and surface the disagreement.",
        },
    ],
    "misleading_assumption": [
        {
            "question": "Why is DuckDuckGo's search index larger than Google's?",
            "expected_behavior": "Reject the false premise: do not accept that DuckDuckGo's index is larger without evidence, and correct it if evidence exists.",
        },
        {
            "question": "Explain why RAG always eliminates hallucinations.",
            "expected_behavior": "Reject the absolute premise and explain that RAG reduces but does not eliminate hallucination.",
        },
        {
            "question": "Why does LoRA require more trainable parameters than full fine-tuning?",
            "expected_behavior": "Correct the premise: LoRA injects low-rank matrices and trains far fewer parameters than full fine-tuning.",
        },
        {
            "question": "Explain why PostgreSQL cannot store JSON.",
            "expected_behavior": "Correct the premise: PostgreSQL supports JSON and JSONB types.",
        },
        {
            "question": "Why do passkeys require a password as a fallback?",
            "expected_behavior": "Reject the assumption; passkeys can be used without a password and fallback behaviour is implementation-specific.",
        },
        {
            "question": "Explain why Kubernetes is a database.",
            "expected_behavior": "Correct the category error: Kubernetes is a container orchestrator, not a database.",
        },
        {
            "question": "Why does Qwen3:4b have more parameters than Qwen3:8b?",
            "expected_behavior": "Correct the premise using the model naming convention; do not rationalize a false comparison.",
        },
        {
            "question": "Explain why HTTP/3 is slower than HTTP/2 in all cases.",
            "expected_behavior": "Reject the universal claim; performance depends on network conditions and implementation.",
        },
        {
            "question": "Why is zero trust architecture the same as a firewall?",
            "expected_behavior": "Correct the premise: zero trust is an architecture and policy model, not a single perimeter device.",
        },
        {
            "question": "Explain why vector search guarantees factual correctness.",
            "expected_behavior": "Reject the guarantee; similarity retrieval does not establish factual truth.",
        },
    ],
}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _domain(url: str) -> str:
    try:
        host = urlparse(url).hostname or ""
    except ValueError:
        return ""
    return host.lower().lstrip("www.")


def _source_map(dataset: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {
        str(source.get("source_id")): source
        for source in dataset.get("sources", [])
        if isinstance(source, dict) and source.get("source_id")
    }


def _verified_claims(question: Dict[str, Any]) -> List[Dict[str, Any]]:
    ground_truth = question.get("ground_truth") or {}
    claims = ground_truth.get("claims") or []
    return [
        claim
        for claim in claims
        if isinstance(claim, dict) and claim.get("status") == "verified"
    ]


def _reference_evidence(
    claims: Sequence[Dict[str, Any]], sources: Dict[str, Dict[str, Any]]
) -> List[Dict[str, Any]]:
    seen: set[str] = set()
    records: List[Dict[str, Any]] = []
    for claim in claims:
        for source_id in claim.get("source_ids") or []:
            source_id = str(source_id)
            if source_id in seen:
                continue
            source = sources.get(source_id)
            if not source:
                continue
            seen.add(source_id)
            records.append(
                {
                    "source_id": source_id,
                    "url": source.get("url", ""),
                    "title": source.get("title", ""),
                    "publisher": source.get("publisher", ""),
                    "publication_date": source.get("publication_date", ""),
                    "source_type": source.get("source_type", ""),
                    "verification_status": source.get(
                        "verification_status", "unknown"
                    ),
                    "inspection_notes": source.get("inspection_notes", ""),
                }
            )
    return records


def _expected_findings(
    claims: Sequence[Dict[str, Any]], sources: Dict[str, Dict[str, Any]]
) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    for claim in claims:
        source_ids = [str(sid) for sid in claim.get("source_ids") or []]
        urls = [
            sources[sid].get("url", "")
            for sid in source_ids
            if sid in sources and sources[sid].get("url")
        ]
        findings.append(
            {
                "claim_id": str(claim.get("claim_id") or ""),
                "text": claim.get("effective_claim_text")
                or claim.get("corrected_claim_text")
                or claim.get("claim_text")
                or "",
                "verification_status": "verified",
                "source_ids": source_ids,
                "source_urls": urls,
            }
        )
    return findings


def _retrieval_labels(
    evidence: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    urls = list(dict.fromkeys(
        record.get("url", "") for record in evidence if record.get("url")
    ))
    if not urls:
        return {
            "status": "unavailable",
            "relevant_urls": [],
            "label_quality": "none",
            "reason": "no_verified_reference_urls",
        }
    statuses = {
        record.get("verification_status", "unknown") for record in evidence
    }
    return {
        "status": "available",
        "relevant_urls": urls,
        "label_quality": (
            "repository_verified"
            if statuses == {"verified_in_repository_record"}
            else "mixed"
        ),
        "reason": "",
    }


def _engine_question_text(category: str, topic: str) -> str:
    template = _QUESTION_TEMPLATES[category]
    return template.format(topic=topic)


def _verified_case(
    case_id: str,
    source_question: Dict[str, Any],
    category: str,
    sources: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    claims = _verified_claims(source_question)
    evidence = _reference_evidence(claims, sources)
    findings = _expected_findings(claims, sources)
    metadata = source_question.get("metadata") or {}
    topic = str(source_question.get("topic") or "")
    expected_domains = [
        domain
        for domain in metadata.get("expected_source_domains", [])
        if isinstance(domain, str) and domain and "://" not in domain
    ]
    return {
        "id": case_id,
        "question": _engine_question_text(category, topic),
        "category": category,
        "difficulty": source_question.get("difficulty", "Intermediate"),
        "provenance": {
            "type": "verified_reference",
            "synthetic": False,
            "source_dataset": DEFAULT_SOURCE.name,
            "source_dataset_version": "3.0.0",
            "source_question_id": source_question.get("id"),
            "source_claim_ids": [claim.get("claim_id") for claim in claims],
            "notes": (
                "Question text is a benchmark variant; expected findings and "
                "citations are copied from the verified v3 claims and sources."
            ),
        },
        "expected_key_findings": findings,
        "reference_evidence": evidence,
        "expected_source_domains": expected_domains,
        "retrieval_ground_truth": _retrieval_labels(evidence),
        "evaluation_rubric": {
            "structural_requirements": list(STRUCTURAL_REQUIREMENTS),
            "minimum_word_count": (source_question.get("evaluation_rubric") or {}).get(
                "minimum_word_count", 250
            ),
            "scoring_criteria": (source_question.get("evaluation_rubric") or {}).get(
                "scoring_criteria", {}
            ),
        },
        "expected_behavior": (
            "Answer using the verified findings only, cite retrieved sources, "
            "and do not add uncited facts."
        ),
        "metadata": {
            "domain": source_question.get("domain"),
            "source_task_type": source_question.get("task_type"),
            "target_aspects": metadata.get("target_aspects", []),
        },
    }


def _synthetic_case(
    case_id: str, category: str, index: int, item: Dict[str, str]
) -> Dict[str, Any]:
    difficulty = "Intermediate" if index % 2 else "Easy"
    return {
        "id": case_id,
        "question": item["question"],
        "category": category,
        "difficulty": difficulty,
        "provenance": {
            "type": "synthetic",
            "synthetic": True,
            "source_dataset": None,
            "source_dataset_version": None,
            "source_question_id": None,
            "source_claim_ids": [],
            "notes": (
                "Manually constructed behavioural case. It has no verified "
                "reference answer, citation, or retrieval label."
            ),
        },
        "expected_key_findings": [],
        "reference_evidence": [],
        "expected_source_domains": [],
        "retrieval_ground_truth": {
            "status": "unavailable",
            "relevant_urls": [],
            "label_quality": "none",
            "reason": "synthetic_case_without_verified_reference",
        },
        "evaluation_rubric": {
            "structural_requirements": list(STRUCTURAL_REQUIREMENTS),
            "minimum_word_count": 180,
            "scoring_criteria": {
                "behaviour": item["expected_behavior"],
            },
        },
        "expected_behavior": item["expected_behavior"],
        "metadata": {
            "domain": "behavioural",
            "source_task_type": None,
            "target_aspects": [],
            "synthetic": True,
        },
    }


def _category_pair(task_type: Optional[str]) -> List[str]:
    if task_type == "Explanation":
        return ["factual_research", "technical_research"]
    if task_type == "Comparison":
        return ["comparison", "multi_source_research"]
    if task_type == "Multi-Source":
        return ["multi_source_research", "factual_research"]
    if task_type == "SOTA":
        return ["technical_research", "comparison"]
    return ["factual_research", "technical_research"]


def build_cases(source_dataset: Dict[str, Any]) -> List[Dict[str, Any]]:
    sources = _source_map(source_dataset)
    questions = source_dataset.get("benchmark_questions") or []
    cases: List[Dict[str, Any]] = []
    counter = 0

    for question in questions:
        if not isinstance(question, dict) or not _verified_claims(question):
            continue
        for category in _category_pair(question.get("task_type")):
            counter += 1
            cases.append(
                _verified_case(f"G{counter:03d}", question, category, sources)
            )

    for category in (
        "recent_information",
        "insufficient_evidence",
        "conflicting_sources",
        "misleading_assumption",
    ):
        for index, item in enumerate(_SYNTHETIC_CASES[category]):
            counter += 1
            cases.append(_synthetic_case(f"G{counter:03d}", category, index, item))

    return cases


def build_dataset(
    source_path: Path = DEFAULT_SOURCE,
    output_path: Path = DEFAULT_OUTPUT,
    manifest_path: Path = DEFAULT_MANIFEST,
) -> Dict[str, Any]:
    with source_path.open("r", encoding="utf-8") as handle:
        source_dataset = json.load(handle)
    cases = build_cases(source_dataset)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="\n") as handle:
        for case in cases:
            handle.write(
                json.dumps(case, ensure_ascii=False, sort_keys=True) + "\n"
            )

    category_counts = Counter(case["category"] for case in cases)
    difficulty_counts = Counter(case["difficulty"] for case in cases)
    provenance_counts = Counter(case["provenance"]["type"] for case in cases)
    label_counts = Counter(
        case["retrieval_ground_truth"]["status"] for case in cases
    )
    manifest = {
        "dataset": output_path.name,
        "version": DATASET_VERSION,
        "created_at": CREATED_AT,
        "case_count": len(cases),
        "category_counts": dict(sorted(category_counts.items())),
        "difficulty_counts": dict(sorted(difficulty_counts.items())),
        "provenance_counts": dict(sorted(provenance_counts.items())),
        "retrieval_label_counts": dict(sorted(label_counts.items())),
        "sha256": _sha256_file(output_path),
        "source_dataset": {
            "filename": source_path.name,
            "version": source_dataset.get("version"),
            "sha256": _sha256_file(source_path),
        },
        "generator": "benchmarks/build_golden_dataset.py",
        "methodology": [
            "Verified cases reuse only claims whose v3 status is 'verified'.",
            "Reference URLs are copied from the v3 source registry; none are invented.",
            "Synthetic behavioural cases have no reference answer or retrieval label.",
            "Missing retrieval labels are reported as unavailable, never scored as zero.",
        ],
    }
    with manifest_path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(manifest, handle, indent=2, ensure_ascii=False, sort_keys=True)
        handle.write("\n")
    return manifest


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=str(DEFAULT_SOURCE))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    args = parser.parse_args(argv)

    manifest = build_dataset(
        source_path=Path(args.source),
        output_path=Path(args.output),
        manifest_path=Path(args.manifest),
    )
    print(json.dumps(manifest, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
