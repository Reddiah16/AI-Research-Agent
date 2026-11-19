"""Phase 3 tests for citation-to-source integrity.

Covers stable source IDs, invalid-citation detection, evidence-text support,
evidence-based citation binding, URL-variant deduplication, displayed/cited
source-count consistency, snippet-only vs fetched shares, and the deterministic
limitations note.

Run:
    python tests/test_phase3_citation_integrity.py
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.citation_integrity import (
    CITATION_INTEGRITY_VERSION,
    add_evidence_limitations,
    bind_and_verify_findings,
    build_report_sources,
    evaluate_citation_integrity,
    verify_findings,
)
from tools.research_evidence import canonical_url


def record(
    source_id,
    url,
    title,
    excerpt,
    *,
    method="fetched_page",
    status="ok",
    source_type="academic",
    rank_score=0.8,
    query="q1",
):
    return {
        "source_id": source_id,
        "url": url,
        "canonical_url": canonical_url(url),
        "original_url": url,
        "title": title,
        "excerpt": excerpt,
        "retrieval_method": method,
        "fetch_status": status,
        "source_type": source_type,
        "rank_score": rank_score,
        "queries": [query],
    }


REGISTRY = [
    record(
        "SRC-001",
        "https://example.com/rag",
        "Retrieval Augmented Generation",
        "Retrieval augmented generation combines information retrieval with text generation to improve factual accuracy.",
    ),
    record(
        "SRC-002",
        "https://example.com/medusa",
        "Medusa speculative decoding",
        "Medusa adds parallel decoding heads that predict several subsequent tokens in a tree structured pass.",
    ),
    record(
        "SRC-003",
        "https://example.com/snippet",
        "Snippet only source",
        "A short snippet about language model inference acceleration.",
        method="search_snippet",
        status="not_attempted",
        source_type="independent_blog",
        rank_score=0.4,
    ),
]


class FindingVerificationTests(unittest.TestCase):
    def test_supported_claim_is_classified_as_supported(self):
        findings = [
            {
                "claim": "Retrieval augmented generation combines information retrieval with text generation",
                "source_ids": ["SRC-001"],
            }
        ]
        annotated, summary = verify_findings(findings, REGISTRY)
        self.assertEqual(annotated[0]["support_status"], "supported")
        self.assertTrue(annotated[0]["content_supported"])
        self.assertEqual(summary["claims_supported"], 1)
        self.assertEqual(summary["evidence_support_rate"], 1.0)
        self.assertEqual(summary["version"], CITATION_INTEGRITY_VERSION)

    def test_invalid_source_id_is_removed_and_counted(self):
        findings = [
            {"claim": "A claim with a fake citation", "source_ids": ["SRC-999"]}
        ]
        annotated, summary = verify_findings(findings, REGISTRY)
        self.assertEqual(annotated[0]["support_status"], "invalid_citation")
        self.assertEqual(annotated[0]["source_ids"], [])
        self.assertEqual(annotated[0]["invalid_source_ids"], ["SRC-999"])
        self.assertEqual(summary["invalid_references"], 1)
        self.assertEqual(summary["citation_precision"], 0.0)

    def test_missing_citation_is_not_forced(self):
        findings = [{"claim": "An uncited statement", "source_ids": []}]
        annotated, summary = verify_findings(findings, REGISTRY, bind=True)
        self.assertEqual(annotated[0]["support_status"], "no_citation")
        self.assertEqual(annotated[0]["source_ids"], [])
        self.assertEqual(summary["bindings_added"], 0)

    def test_unsupported_cited_claim_is_not_promoted(self):
        findings = [
            {
                "claim": "A completely unrelated statement about medieval poetry",
                "source_ids": ["SRC-001"],
            }
        ]
        annotated, summary = verify_findings(findings, REGISTRY, bind=False)
        self.assertIn(
            annotated[0]["support_status"],
            {"insufficient_evidence", "partially_supported"},
        )
        self.assertFalse(annotated[0]["content_supported"])
        self.assertIn("interpretation", annotated[0]["support_note"])
        self.assertEqual(summary["claims_supported"], 0)

    def test_binding_adds_only_an_existing_supporting_source(self):
        findings = [
            {
                "claim": "Medusa adds parallel decoding heads that predict several subsequent tokens",
                "source_ids": ["SRC-001"],
            }
        ]
        annotated, summary = verify_findings(
            findings, REGISTRY, threshold=0.55, bind=True
        )
        self.assertEqual(annotated[0]["support_status"], "supported")
        self.assertIn("SRC-002", annotated[0]["source_ids"])
        self.assertEqual(annotated[0]["bound_source_ids"], ["SRC-002"])
        self.assertEqual(summary["bindings_added"], 1)
        self.assertTrue(summary["binding_enabled"])

    def test_binding_never_invents_a_source_when_no_evidence_matches(self):
        findings = [
            {
                "claim": "Quantum entanglement proves medieval poetry is obsolete",
                "source_ids": ["SRC-001"],
            }
        ]
        annotated, summary = verify_findings(
            findings, REGISTRY, threshold=0.55, bind=True
        )
        self.assertEqual(annotated[0]["bound_source_ids"], [])
        self.assertEqual(summary["bindings_added"], 0)
        self.assertFalse(annotated[0]["content_supported"])

    def test_original_source_ids_are_preserved_for_audit(self):
        findings = [
            {"claim": "Some claim", "source_ids": ["SRC-999", "SRC-001"]}
        ]
        annotated, _ = verify_findings(findings, REGISTRY)
        self.assertEqual(annotated[0]["original_source_ids"], ["SRC-999", "SRC-001"])
        self.assertEqual(annotated[0]["invalid_source_ids"], ["SRC-999"])
        self.assertEqual(annotated[0]["source_ids"], ["SRC-001"])


class SourceListTests(unittest.TestCase):
    def test_url_variants_collapse_and_cited_sources_are_displayed(self):
        model_sources = [
            "https://example.com/rag/",
            "https://www.example.com/rag",
            "https://not-retrieved.example/fake",
        ]
        findings = [{"claim": "X", "source_ids": ["SRC-001", "SRC-002"]}]
        displayed, audit = build_report_sources(model_sources, findings, REGISTRY)
        self.assertEqual(
            displayed,
            ["https://example.com/rag", "https://example.com/medusa"],
        )
        self.assertEqual(audit["duplicates_collapsed"], 1)
        self.assertEqual(
            audit["unknown_model_sources"], ["https://not-retrieved.example/fake"]
        )
        self.assertEqual(audit["added_from_findings"], 1)

    def test_cited_sources_are_never_hidden_from_display(self):
        findings = [{"claim": "X", "source_ids": ["SRC-002"]}]
        displayed, _ = build_report_sources([], findings, REGISTRY)
        self.assertEqual(len(displayed), 1)
        self.assertIn("https://example.com/medusa", displayed)

    def test_no_sources_is_empty_not_fabricated(self):
        displayed, audit = build_report_sources([], [], REGISTRY)
        self.assertEqual(displayed, [])
        self.assertEqual(audit["displayed_source_count"], 0)


class ReportIntegrityTests(unittest.TestCase):
    def test_report_metrics_expose_support_and_shares(self):
        report = {
            "report": "Body",
            "sources": ["https://example.com/rag", "https://example.com/medusa"],
            "key_findings": [
                {
                    "claim": "Retrieval augmented generation combines information retrieval with text generation",
                    "source_ids": ["SRC-001"],
                },
                {
                    "claim": "Medusa adds parallel decoding heads that predict several subsequent tokens",
                    "source_ids": ["SRC-002"],
                },
                {
                    "claim": "A snippet claim about inference acceleration",
                    "source_ids": ["SRC-003"],
                },
            ],
        }
        integrity = evaluate_citation_integrity(report, REGISTRY, threshold=0.55)
        self.assertEqual(integrity["version"], CITATION_INTEGRITY_VERSION)
        self.assertEqual(integrity["claims_total"], 3)
        self.assertEqual(integrity["claims_cited"], 3)
        self.assertEqual(integrity["cited_source_count"], 3)
        self.assertEqual(integrity["displayed_source_count"], 2)
        # SRC-003 is cited but not displayed, which must be flagged.
        self.assertFalse(integrity["source_count_consistent"])
        self.assertEqual(len(integrity["missing_cited_from_display"]), 1)
        self.assertEqual(integrity["fetched_cited_sources"], 2)
        self.assertEqual(integrity["snippet_only_cited_sources"], 1)
        self.assertEqual(integrity["fetched_source_share"], round(2 / 3, 4))
        self.assertEqual(integrity["snippet_only_share"], round(1 / 3, 4))
        self.assertEqual(integrity["source_type_diversity"], 2)

    def test_consistent_source_count_when_all_cited_sources_displayed(self):
        integrity = evaluate_citation_integrity(
            {
                "report": "Body",
                "sources": ["https://example.com/rag"],
                "key_findings": [
                    {"claim": "X", "source_ids": ["SRC-001"]}
                ],
            },
            REGISTRY,
        )
        self.assertTrue(integrity["source_count_consistent"])
        self.assertEqual(integrity["missing_cited_from_display"], [])

    def test_missing_measurements_are_none_not_zero(self):
        integrity = evaluate_citation_integrity(
            {"report": "Body", "sources": [], "key_findings": []}, REGISTRY
        )
        self.assertIsNone(integrity["citation_precision"])
        self.assertIsNone(integrity["citation_coverage"])
        self.assertIsNone(integrity["evidence_support_rate"])
        self.assertIsNone(integrity["unsupported_claim_rate"])


class LimitationsNoteTests(unittest.TestCase):
    def test_note_is_added_before_references_and_is_truthful(self):
        integrity = {
            "claims_total": 3,
            "claims_insufficient_evidence": 1,
            "claims_invalid_citation": 1,
            "invalid_references": 1,
            "snippet_only_share": 0.6,
        }
        report = "# T\n\n## Introduction\nText.\n\n## References\n1. Source – https://x"
        updated, added = add_evidence_limitations(report, integrity)
        self.assertTrue(added)
        self.assertIn("## Limitations", updated)
        self.assertIn("1 of 3 key findings", updated)
        self.assertIn("60% of cited sources", updated)
        self.assertLess(updated.index("## Limitations"), updated.index("## References"))

    def test_existing_limitations_section_is_not_duplicated(self):
        integrity = {"claims_total": 1, "claims_insufficient_evidence": 1}
        report = "# T\n\n## Limitations\n- Already noted.\n"
        updated, added = add_evidence_limitations(report, integrity)
        self.assertFalse(added)
        self.assertEqual(updated.count("## Limitations"), 1)

    def test_no_note_when_evidence_is_complete(self):
        integrity = {
            "claims_total": 2,
            "claims_insufficient_evidence": 0,
            "claims_invalid_citation": 0,
            "invalid_references": 0,
            "snippet_only_share": 0.0,
        }
        report = "# T\n\n## References\n1. Source – https://x"
        updated, added = add_evidence_limitations(report, integrity)
        self.assertFalse(added)
        self.assertEqual(updated, report)

    def test_note_can_be_disabled(self):
        integrity = {"claims_total": 1, "claims_insufficient_evidence": 1}
        report = "# T"
        updated, added = add_evidence_limitations(report, integrity, enabled=False)
        self.assertFalse(added)
        self.assertEqual(updated, report)


if __name__ == "__main__":
    unittest.main(verbosity=2)
