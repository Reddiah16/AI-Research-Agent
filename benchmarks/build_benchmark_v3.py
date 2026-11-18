#!/usr/bin/env python3
"""Build ``benchmarks/datasets/benchmark_v3.json`` from immutable v1 and v2 datasets.

Version 3 is a remediation release. It:

  * verifies the SHA-256 of ``benchmark_v1.json`` and ``benchmark_v2.json``
    before reading either file, and re-verifies them after writing;
  * preserves every question, question ID, taxonomy value, rubric, source
    record, claim ID and original claim wording from v1/v2;
  * records the original and corrected wording separately for claims whose
    v2 wording exceeded the inspected evidence;
  * keeps the original v2 status in ``original_status`` and applies the new
    ``status`` to ``effective_claim_text``;
  * records a review history and an explicit reason for every status change;
  * separates source verification from claim verification;
  * reconciles each question's frozen Phase 1 ``expected_source_domains``
    with the domains actually cited by its claims, without broadening the
    expected list;
  * emits byte-identical output on repeated runs.

Run:
    python benchmarks/build_benchmark_v3.py

No network access, database connection, Ollama call, research-pipeline run,
benchmark execution or dependency installation is performed.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parent.parent
V1_PATH = PROJECT_ROOT / "benchmarks" / "datasets" / "benchmark_v1.json"
V2_PATH = PROJECT_ROOT / "benchmarks" / "datasets" / "benchmark_v2.json"
V3_PATH = PROJECT_ROOT / "benchmarks" / "datasets" / "benchmark_v3.json"

V1_SHA256 = "4f1cb7f5ca19e1c58e10de714844fd042ffdd70ac4f8321e80046a6cd52573ec"
V2_SHA256 = "5f859b81be45b787b5a6108378a3b9abe0584e4697ad3d8def0f65a49a5b779f"

VERIFICATION_DATE = "2026-11-12"
V2_VERIFIER = "OpenCode verification agent (DeepSeek V4.1 Flash Free)"
V3_VERIFIER = "OpenCode remediation agent (DeepSeek V4.1 Flash Free)"
REVIEW_REFERENCE = "docs/PHASE_3_STAGE_1_FINAL_QUALITY_REVIEW.md"

VALID_STATUSES = {
    "verified",
    "partially_supported",
    "contradicted",
    "needs_more_evidence",
    "time_sensitive",
}

VALID_RESOLUTIONS = {
    "confirmed_verified",
    "retained_partially_supported",
    "retained_needs_more_evidence",
    "corrected_and_verified",
    "downgraded_needs_more_evidence",
}


# ---------------------------------------------------------------------------
# Claim remediation map.
#
# Each entry documents one finding from the final quality review, the wording
# correction (when the inspected record supports one), the resulting status,
# and the exact reason. A correction is only recorded when the cited
# inspection notes support the corrected wording. No new source was fetched
# for this release.
# ---------------------------------------------------------------------------

REMEDIATIONS: dict[str, dict[str, Any]] = {}


def _remediate(
    claim_id: str,
    finding: str,
    status: str,
    resolution: str,
    reason: str,
    corrected: str | None = None,
    correction_evidence: list[str] | None = None,
    evidence_summary: str | None = None,
    support_explanation: str | None = None,
    notes: str | None = None,
    source_verification_note: str | None = None,
) -> None:
    assert status in VALID_STATUSES, status
    assert resolution in VALID_RESOLUTIONS, resolution
    REMEDIATIONS[claim_id] = {
        "finding": finding,
        "corrected": corrected,
        "status": status,
        "resolution": resolution,
        "reason": reason,
        "correction_evidence": correction_evidence,
        "evidence_summary": evidence_summary,
        "support_explanation": support_explanation,
        "notes": notes,
        "source_verification_note": source_verification_note,
    }


# --- Tier A: verified claims whose exact wording was not supported -----------

_remediate(
    "Q01-C2",
    "Tier A exact-wording overstatement",
    "verified",
    "corrected_and_verified",
    "Removed 'semantic' because the inspected LangChain RAG record documents "
    "chunking strategies but does not establish semantic chunking as a core "
    "pipeline phase.",
    corrected="Core pipeline phases include document loading, chunking, vector "
    "embedding, similarity retrieval, and generation prompt synthesis.",
    correction_evidence=["S002"],
    evidence_summary="LangChain RAG documentation describes loading, "
    "splitting/chunking, embedding, storing, retrieving and prompt synthesis.",
    support_explanation="The documented pipeline covers the named phases; "
    "chunking is documented as a general split strategy.",
    notes="v3 correction: 'semantic' removed because S002 records chunking "
    "strategies, not semantic chunking as a pipeline phase.",
)

_remediate(
    "Q08-C3",
    "Tier A exact-wording overstatement",
    "verified",
    "corrected_and_verified",
    "Narrowed the mitigation list to the two controls documented in the "
    "inspected AWS sources; lightweight runtimes and keep-alive pingers were "
    "not established by those records.",
    corrected="Documented mitigation strategies include provisioned concurrency "
    "and container snapshotting (e.g. AWS Lambda SnapStart).",
    correction_evidence=["S015", "S016"],
    evidence_summary="AWS Lambda documentation describes provisioned concurrency "
    "and SnapStart snapshot restore as cold-start mitigations.",
    support_explanation="The Lambda Developer Guide documents provisioned "
    "concurrency, and the SnapStart page documents snapshot-based restore.",
    notes="v3 correction: the two undocumented mitigations were removed rather "
    "than retained as verified.",
)

_remediate(
    "Q11-C3",
    "Tier A exact-wording overstatement",
    "verified",
    "corrected_and_verified",
    "Corrected the proxy example: the inspected records state that ztunnel "
    "handles L4 while Envoy/waypoint proxies handle L7, so ztunnel was an "
    "invalid L7 example.",
    corrected="eBPF excels at L3/L4 networking and transparent telemetry, while "
    "L7 parsing (HTTP headers, mTLS termination) requires specialized proxy "
    "logic such as Envoy sidecars or waypoint proxies; ztunnel handles L4 at "
    "the node level.",
    correction_evidence=["S023", "S024"],
    evidence_summary="eBPF is strongest at L3/L4; Cilium uses Envoy for L7 "
    "policy, and Istio ambient mode uses ztunnel for L4 plus waypoint proxies "
    "for L7.",
    support_explanation="Cilium documentation assigns L7 policy to Envoy; Istio "
    "documentation assigns L4 to ztunnel and L7 to waypoint proxies.",
    notes="v3 correction: the claim's own support text contradicted the ztunnel "
    "L7 example; the example now matches the inspected architecture.",
)

_remediate(
    "Q14-C2",
    "Tier A exact-wording overstatement",
    "verified",
    "corrected_and_verified",
    "Replaced the absolute 'immune' with 'resistant', matching the inspected "
    "FIDO and WebAuthn records, which document phishing resistance.",
    corrected="Passkeys provide cryptographic origin binding, making them "
    "resistant to credential phishing and adversary-in-the-middle attacks.",
    correction_evidence=["S028", "S029"],
    evidence_summary="Passkeys are origin/RP-bound, which makes them resistant "
    "to credential phishing and adversary-in-the-middle attacks.",
    support_explanation="WebAuthn binds assertions to the origin/RP ID, and "
    "FIDO describes phishing resistance rather than immunity.",
    notes="v3 correction: absolute 'immune' downgraded to the documented "
    "resistance property.",
)

_remediate(
    "Q15-C2",
    "Tier A exact-wording overstatement",
    "verified",
    "corrected_and_verified",
    "Fulcio is a certificate authority, not a transparency log; the corrected "
    "wording reflects the inspected Sigstore record.",
    corrected="Sigstore provides keyless cryptographic signing, a certificate "
    "authority (Fulcio), and a public transparency log (Rekor) for container "
    "images and binaries.",
    correction_evidence=["S030"],
    evidence_summary="Sigstore provides keyless signing with Fulcio "
    "certificates and public transparency logging with Rekor for container "
    "images and binaries.",
    support_explanation="Sigstore documentation describes keyless signing, "
    "Fulcio certificates and Rekor transparency logs.",
    notes="v3 correction: Fulcio and Rekor now carry their documented roles.",
)

_remediate(
    "Q16-C3",
    "Tier A exact-wording overstatement",
    "verified",
    "corrected_and_verified",
    "Removed the unsupported comparison with Dilithium; the inspected Falcon "
    "record documents compact NTRU signatures and constant-time floating-point "
    "implementation, not a head-to-head signature-size result.",
    corrected="Falcon provides compact NTRU-lattice signatures and requires "
    "careful constant-time floating-point arithmetic.",
    correction_evidence=["S035"],
    evidence_summary="Falcon uses NTRU lattices, produces compact signatures, "
    "and requires careful floating-point/constant-time implementation.",
    support_explanation="The Falcon specification describes NTRU-based compact "
    "signatures and FFT/floating-point implementation with constant-time "
    "concerns; it does not record a Dilithium comparison.",
    notes="v3 correction: the unsupported Dilithium comparison was removed.",
)

_remediate(
    "Q25-C1",
    "Tier A exact-wording overstatement",
    "verified",
    "corrected_and_verified",
    "Removed the Commonwealth Fusion Systems example; only the ITER source was "
    "inspected for this claim and CFS is absent from its record.",
    corrected="Tokamaks (e.g. ITER) confine plasma in a toroidal chamber using "
    "external magnets and an induced plasma current.",
    correction_evidence=["S054"],
    evidence_summary="Tokamaks such as ITER confine plasma in a toroidal "
    "chamber with external magnets and an induced plasma current.",
    support_explanation="ITER's fusion explainer describes the tokamak "
    "configuration.",
    notes="v3 correction: the unsupported CFS example was removed.",
)

_remediate(
    "Q27-C2",
    "Tier A exact-wording overstatement",
    "verified",
    "corrected_and_verified",
    "Corrected 'compute silicon' to the networking ASIC documented by NVIDIA "
    "and removed the unsupported optical I/O chiplet disjunct.",
    corrected="Silicon photonics can integrate optical transceivers with a "
    "networking ASIC on the same package using Co-Packaged Optics (CPO), "
    "replacing pluggable transceivers.",
    correction_evidence=["S086"],
    evidence_summary="NVIDIA describes co-packaged optics that place silicon "
    "photonics on the same package as a networking ASIC, replacing pluggable "
    "transceivers.",
    support_explanation="The inspected NVIDIA page documents CPO with the ASIC "
    "on the same package; it does not describe integration with compute "
    "silicon or optical I/O chiplets.",
    notes="v3 correction: 'compute silicon' and the optical I/O chiplet "
    "alternative were not supported by S086.",
)

_remediate(
    "Q29-C2",
    "Tier A exact-wording overstatement",
    "needs_more_evidence",
    "downgraded_needs_more_evidence",
    "Neither inspected source (the gRPC introduction and the GraphQL learn "
    "page) documents REST verbs, payload formats, HTTP caching or payload "
    "overhead. No REST primary source was inspected, so the claim is retained "
    "unresolved rather than rewritten.",
    evidence_summary="The inspected gRPC and GraphQL sources do not document "
    "REST verbs, payload formats, HTTP caching or payload overhead.",
    support_explanation="Neither inspected source establishes the REST "
    "mechanics asserted in the claim; the comparison is therefore unsupported.",
    notes="v3 downgrade: promoted to needs_more_evidence because no REST "
    "primary source is in the record.",
)

_remediate(
    "Q33-C1",
    "Tier A exact-wording overstatement",
    "verified",
    "corrected_and_verified",
    "Replaced the unsupported injection mechanism ('system prompts or special "
    "tool-call token sequences') with the JSON-schema tool declaration and "
    "tool-call flow documented by OpenAI.",
    corrected="Function calling declares available tools as JSON schemas; the "
    "model then returns structured tool calls, and the application executes "
    "them.",
    correction_evidence=["S066"],
    evidence_summary="OpenAI's function-calling guide documents JSON-schema "
    "tool definitions, model-returned tool calls and application execution of "
    "those calls.",
    support_explanation="The guide describes tools declared as JSON schemas and "
    "structured tool-call outputs; it does not describe injection into system "
    "prompts or special token sequences.",
    notes="v3 correction: the unsupported injection mechanism was removed.",
)

_remediate(
    "Q35-C2",
    "Tier A exact-wording overstatement",
    "verified",
    "corrected_and_verified",
    "Removed the absolute 'preventing direct kernel exploitation'; the "
    "inspected record documents syscall interception and file-access "
    "mediation, not a guarantee against exploitation.",
    corrected="gVisor intercepts application syscalls in userspace through the "
    "Sentry and mediates file access through the Gofer.",
    correction_evidence=["S070"],
    evidence_summary="gVisor intercepts application syscalls in userspace "
    "through the Sentry and mediates file access through the Gofer.",
    support_explanation="The gVisor architecture documentation names Sentry and "
    "Gofer and describes syscall interception; it does not claim exploitation "
    "is prevented.",
    notes="v3 correction: the unqualified prevention guarantee was removed.",
)

_remediate(
    "Q38-C1",
    "Tier A exact-wording overstatement",
    "verified",
    "corrected_and_verified",
    "Replaced 'sub-2nm' with the '2 nm Logic node' wording recorded by ASML; "
    "the evidence summary and source note otherwise support the claim.",
    corrected="High-NA EUV lithography increases numerical aperture from 0.33 "
    "to 0.55, enabling printing of 8 nm critical features for the 2 nm Logic "
    "node.",
    correction_evidence=["S075", "S076"],
    evidence_summary="High-NA EUV raises NA from 0.33 to 0.55 and provides 8 nm "
    "resolution for the 2 nm Logic node.",
    support_explanation="ASML documents 0.55 NA, 8 nm resolution and support "
    "for the 2 nm Logic node.",
    notes="v3 correction: the evidence summary's 'sub-2 nm' wording was "
    "inconsistent with the source note's '2 nm Logic node'.",
)

_remediate(
    "Q40-C3",
    "Tier A exact-wording overstatement",
    "verified",
    "corrected_and_verified",
    "Removed the attribution of scorecards to both platforms; the inspected "
    "records document catalogs for both and scorecards/self-service actions "
    "for Port. The unsupported cognitive-load outcome was also removed.",
    corrected="Both platforms centralize a software catalog; Port additionally "
    "provides scorecards and self-service actions.",
    correction_evidence=["S082", "S083"],
    evidence_summary="Backstage documents a software catalog, templates and "
    "TechDocs; Port documents a catalog, scorecards and self-service actions.",
    support_explanation="Both records document software catalogs; only Port's "
    "record documents scorecards and self-service actions.",
    notes="v3 correction: scorecards and self-service actions are now "
    "attributed to Port only.",
)

# --- Tier B: verified claims with unsupported examples, numbers or rankings --

_remediate(
    "Q09-C1",
    "Tier B unsupported qualifier",
    "verified",
    "corrected_and_verified",
    "Removed the unsupported edge-node suitability; the inspected record "
    "supports the single-binary architecture, low resource footprint and task "
    "drivers.",
    corrected="Nomad provides a single-binary orchestrator with a lightweight "
    "resource footprint and multiple task drivers.",
    correction_evidence=["S017"],
    evidence_summary="Nomad is a single-binary scheduler with a low resource "
    "footprint and multiple task drivers.",
    support_explanation="The Nomad documentation describes the single-binary "
    "architecture, low operational footprint and container/raw-exec/Java/QEMU "
    "task drivers.",
    notes="v3 correction: 'well-suited for constrained edge nodes' was not in "
    "the inspected record.",
)

_remediate(
    "Q10-C3",
    "Tier B unsupported ranking",
    "verified",
    "corrected_and_verified",
    "Removed the unsupported 'foundational' ranking and aligned the second "
    "lever with the FinOps record's spot/commitment optimisation.",
    corrected="Workload rightsizing using the Vertical Pod Autoscaler (VPA) "
    "and spot-instance or commitment optimisation are cost-optimisation levers "
    "in the FinOps framework.",
    correction_evidence=["S020", "S022"],
    evidence_summary="The FinOps Framework documents rightsizing and "
    "spot/commitment optimisation; the VPA project documents automated "
    "CPU/memory rightsizing.",
    support_explanation="FinOps covers rightsizing and spot/commitment "
    "strategies, and the VPA project documents automated request/limit "
    "rightsizing.",
    notes="v3 correction: 'foundational' removed because the record does not "
    "rank the levers.",
)

_remediate(
    "Q12-C3",
    "Tier B imprecise terminology",
    "verified",
    "corrected_and_verified",
    "Replaced 'CPU-attested' with Intel's precise term 'CPU-measured', as "
    "recorded in the inspected TDX source.",
    corrected="Intel TDX isolates virtual machines into Trust Domains (TDs) "
    "managed by a CPU-measured Intel TDX Module.",
    correction_evidence=["S025", "S026"],
    evidence_summary="Intel TDX creates hardware-isolated Trust Domains (TDs) "
    "managed by a CPU-measured TDX module running in SEAM.",
    support_explanation="Intel's page states that a CPU-measured TDX module "
    "enables TDX and manages TD entry/exit.",
    notes="v3 correction: Intel's term is CPU-measured; the v2 note already "
    "admitted the imprecision.",
)

_remediate(
    "Q14-C3",
    "Tier B unsupported example",
    "verified",
    "corrected_and_verified",
    "Removed the unsupported Evilginx example; the inspected FIDO record "
    "documents the MFA weaknesses without naming that tool.",
    corrected="Traditional MFA (SMS, OTP, push notifications) is vulnerable to "
    "SIM swapping, prompt fatigue, and reverse-proxy phishing.",
    correction_evidence=["S028"],
    evidence_summary="FIDO's passkey material documents SIM swapping, "
    "prompt-fatigue and reverse-proxy phishing risks for SMS, OTP and push "
    "MFA.",
    support_explanation="The FIDO record discusses these legacy-MFA weaknesses "
    "without naming Evilginx.",
    notes="v3 correction: the unsupported Evilginx example was removed.",
)

_remediate(
    "Q20-C1",
    "Tier B unsupported quantifier",
    "verified",
    "corrected_and_verified",
    "Removed the unsupported 'petabyte-scale' quantifier; the inspected "
    "ClickHouse introduction supports the columnar OLAP and distributed/cloud "
    "design without a specific dataset-size claim.",
    corrected="ClickHouse is a distributed, client-server columnar database "
    "engineered for multi-node analytical processing.",
    correction_evidence=["S042"],
    evidence_summary="ClickHouse is a column-oriented SQL DBMS for OLAP, "
    "available as open source and as a cloud service, with vectorised "
    "execution.",
    support_explanation="The ClickHouse introduction documents the "
    "column-oriented OLAP design and distributed/cloud offerings without a "
    "specific dataset-size claim.",
    notes="v3 correction: 'petabyte-scale' was not in the inspected record.",
)

_remediate(
    "Q21-C2",
    "Tier B unsupported number",
    "verified",
    "corrected_and_verified",
    "Replaced the unsupported 'thousands of' count with 'many'; the inspected "
    "records document small-file maintenance without a specific count.",
    corrected="Streaming ingestion risks creating many small metadata and data "
    "files, requiring asynchronous table maintenance (compaction, snapshot "
    "expiration).",
    correction_evidence=["S041", "S038"],
    evidence_summary="Streaming ingestion can create many small data and "
    "metadata files, requiring compaction and snapshot-expiration maintenance.",
    support_explanation="The Flink connector and Iceberg maintenance "
    "documentation describe small-file compaction and snapshot expiry without "
    "a specific file count.",
    notes="v3 correction: the specific 'thousands' count was removed.",
)

_remediate(
    "Q22-C1",
    "Tier B unsupported scale",
    "verified",
    "corrected_and_verified",
    "Removed the unsupported 'billion-scale' quantifier; DiskANN's inspected "
    "motivation supports high DRAM cost for in-memory indexes without a "
    "specific scale.",
    corrected="HNSW constructs hierarchical proximity graphs in RAM for fast "
    "search, but suffers from high memory consumption at scale.",
    correction_evidence=["S044", "S045"],
    evidence_summary="HNSW builds a hierarchical proximity graph in memory; "
    "large in-memory deployments face high memory pressure.",
    support_explanation="The HNSW paper describes the in-memory hierarchical "
    "graph, and DiskANN's motivation is the DRAM cost of such in-memory "
    "indexes.",
    notes="v3 correction: the specific 'billion-scale' quantifier was removed.",
)

_remediate(
    "Q23-C2",
    "Tier B unsupported intensifier",
    "verified",
    "corrected_and_verified",
    "Removed 'significantly'; the DOE safety record frames thermal runaway as "
    "a risk to be reduced, not a quantified reduction.",
    corrected="Eliminating flammable liquid electrolytes reduces thermal "
    "runaway risk and fire hazards.",
    correction_evidence=["S047", "S048"],
    evidence_summary="Removing the flammable liquid electrolyte reduces "
    "thermal-runaway and fire risk, though it does not eliminate all risk.",
    support_explanation="CAS describes non-flammable, leak-free solid "
    "electrolytes; DOE's safety plan treats thermal runaway as a risk to be "
    "reduced.",
    notes="v3 correction: 'significantly' was not quantified by the inspected "
    "sources.",
)

_remediate(
    "Q25-C2",
    "Tier B imprecise terminology",
    "verified",
    "corrected_and_verified",
    "Replaced 'internal current' with the source's precise 'net plasma "
    "current' wording.",
    corrected="Stellarators (e.g. Wendelstein 7-X) use complex twisted 3D "
    "modular coils to stabilize plasma continuously without a net plasma "
    "current.",
    correction_evidence=["S055"],
    evidence_summary="Stellarators such as Wendelstein 7-X use complex twisted "
    "3D modular coils to confine plasma continuously without a net plasma "
    "current.",
    support_explanation="The IPP Wendelstein 7-X material describes the coil "
    "geometry and current-free operation.",
    notes="v3 correction: 'internal current' replaced with 'net plasma "
    "current'.",
)

_remediate(
    "Q31-C2",
    "Tier B unsupported attributes",
    "verified",
    "corrected_and_verified",
    "Removed 'deterministic memory layout and zero-cost abstractions', which "
    "the inspected ownership chapter does not establish; the corrected wording "
    "matches the recorded memory-safety and no-GC statements.",
    corrected="Rust enforces memory safety at compile-time via ownership and "
    "borrow-checker rules and does not require a garbage collector.",
    correction_evidence=["S063"],
    evidence_summary="Rust enforces memory safety at compile time through "
    "ownership and borrow checking, without a garbage collector.",
    support_explanation="The Rust Book ownership chapter describes compile-time "
    "ownership/borrowing and no garbage collector.",
    notes="v3 correction: the two unsupported attributes were removed.",
)

_remediate(
    "Q32-C2",
    "Tier B unsupported ranking",
    "verified",
    "corrected_and_verified",
    "Removed the unsupported 'strict standards compliance' ranking; the "
    "inspected Wasmtime introduction documents Cranelift, WASI and the "
    "component model.",
    corrected="Wasmtime (Bytecode Alliance) provides Cranelift JIT compilation, "
    "WASI and the Wasm Component Model.",
    correction_evidence=["S064"],
    evidence_summary="Wasmtime provides Cranelift JIT compilation, WASI and the "
    "WebAssembly Component Model.",
    support_explanation="The Wasmtime introduction documents Cranelift, WASI "
    "and the component model.",
    notes="v3 correction: 'strict standards compliance' was removed.",
)

_remediate(
    "Q32-C3",
    "Tier B incorrect product name",
    "verified",
    "corrected_and_verified",
    "Replaced TensorFlow with TensorFlow-Lite, the runtime named in the "
    "inspected WasmEdge plugin record.",
    corrected="WasmEdge (CNCF) is optimized for edge AI and serverless "
    "microservices with extensions for TensorFlow-Lite, PyTorch, and LLM "
    "acceleration.",
    correction_evidence=["S065"],
    evidence_summary="WasmEdge provides official WASI-NN plugins for "
    "TensorFlow-Lite, PyTorch, OpenVINO and GGML/LLM backends.",
    support_explanation="The official plugin list includes TensorFlow-Lite, "
    "PyTorch, OpenVINO and GGML/LLM; the record does not list full TensorFlow.",
    notes="v3 correction: TensorFlow changed to TensorFlow-Lite.",
)

_remediate(
    "Q34-C3",
    "Tier B unsupported qualifier",
    "verified",
    "corrected_and_verified",
    "Removed the unsupported 'commodity hardware' qualifier; the inspected "
    "DeepSeek-R1 record documents distillation into smaller dense models for "
    "cheaper inference.",
    corrected="Reasoning patterns can be distilled into smaller dense "
    "architectures to reduce inference cost.",
    correction_evidence=["S067", "S068"],
    evidence_summary="Reasoning capabilities can be distilled into smaller "
    "dense models for cheaper inference.",
    support_explanation="DeepSeek-R1 documents distillation into smaller dense "
    "models; QwQ-32B demonstrates strong reasoning in a 32B model.",
    notes="v3 correction: 'commodity hardware' was removed.",
)

# --- Q27-C3 evidence-record inconsistency -----------------------------------

_remediate(
    "Q27-C3",
    "Q27-C3 evidence-to-source inconsistency",
    "verified",
    "corrected_and_verified",
    "Replaced the unsupported order-of-magnitude bandwidth-density, latency "
    "and energy-per-bit wording with NVIDIA's recorded 5x power-efficiency and "
    "network-resiliency claims. The v2 evidence summary's 'lower latency' was "
    "not present in the source inspection note and was removed.",
    corrected="Co-packaged optics deliver 5x better power efficiency and higher "
    "network resiliency than pluggable transceivers.",
    correction_evidence=["S086"],
    evidence_summary="NVIDIA reports 5x better power efficiency and higher "
    "network resiliency for co-packaged optics versus pluggable transceivers.",
    support_explanation="The inspected NVIDIA page reports 5x better power "
    "efficiency and higher network resiliency; it does not report an "
    "order-of-magnitude bandwidth-density gain, lower latency or energy per "
    "bit.",
    notes="v3 correction: the v2 evidence summary claimed 'lower latency', but "
    "S086's inspection note records power efficiency, network resiliency and "
    "sustained runtime instead.",
)

# --- Contradicted v1/v2 claims, corrected with inspected evidence -----------

_remediate(
    "Q12-C1",
    "Contradicted claim",
    "verified",
    "corrected_and_verified",
    "Replaced 'encrypting CPU memory registers' with the supported "
    "distinction: guest memory is encrypted, while CPU state is "
    "protected/isolated.",
    corrected="Confidential computing protects data in use by encrypting guest "
    "memory and protecting CPU state, with hardware-backed isolation.",
    correction_evidence=["S025", "S026"],
    evidence_summary="Confidential VMs use hardware-based memory encryption and "
    "isolate/protect CPU state; guest memory is the encrypted surface, not CPU "
    "registers.",
    support_explanation="Google Cloud describes hardware memory encryption, and "
    "Intel describes encrypting memory and protecting TD CPU state; neither "
    "says CPU registers are encrypted.",
    notes="v3 correction: the original contradicted wording is preserved in "
    "claim_text and original_contradiction_or_gap.",
)

_remediate(
    "Q15-C3",
    "Contradicted claim",
    "verified",
    "corrected_and_verified",
    "Replaced the superseded 'SLSA 1-4' numbering and the absolute "
    "'tamper-proof' guarantee with the SLSA v1.0 L0-L3 track and the source's "
    "explicit statement that L1 provenance is not tamper-proof.",
    corrected="SLSA v1.0 defines Build levels L0-L3 (the older specification "
    "used SLSA 1-4), and L1 provenance is not tamper-proof.",
    correction_evidence=["S031"],
    evidence_summary="SLSA v1.0 uses a Build track with levels L0-L3; the older "
    "single track used SLSA 1-4. Build L1 provenance is trivial to forge.",
    support_explanation="The SLSA v1.0 security-levels page states both the "
    "current L0-L3 build track and that the previous version used SLSA 1-4; it "
    "also states that L1 provenance is trivial to bypass or forge.",
    notes="v3 correction: the original contradicted wording is preserved in "
    "claim_text and original_contradiction_or_gap.",
)

_remediate(
    "Q34-C2",
    "Contradicted claim",
    "verified",
    "corrected_and_verified",
    "Replaced 'without supervised fine-tuning' with the recorded training "
    "description: the final R1 used cold-start SFT and multi-stage training, "
    "while R1-Zero was the pure-RL variant.",
    corrected="DeepSeek-R1 combined large-scale RL and rule-based verification "
    "with cold-start SFT data and multi-stage training; R1-Zero was the "
    "pure-RL variant.",
    correction_evidence=["S067", "S068"],
    evidence_summary="DeepSeek-R1 used large-scale RL with rule-based rewards, "
    "but the final R1 model also used cold-start supervised data and "
    "multi-stage training; only R1-Zero was pure RL without SFT.",
    support_explanation="The DeepSeek-R1 paper and Qwen's QwQ-32B post both "
    "describe cold-start data and multi-stage training for R1.",
    notes="v3 correction: the original contradicted wording is preserved in "
    "claim_text and original_contradiction_or_gap.",
)

# --- S053 dependency: narrow Q24-C2 so it no longer rests on the weak source -

_remediate(
    "Q24-C2",
    "S053 bibliographic deficiency",
    "verified",
    "corrected_and_verified",
    "Removed the Rubidium example and the 'in vacuum' qualifier that depended "
    "on the insufficiently verified S053 record; the corrected wording is "
    "supported by QuEra's inspected Aquila page alone.",
    corrected="Neutral atom processors trap neutral atoms using optical laser "
    "tweezers and provide Rydberg interactions and all-to-all connectivity.",
    correction_evidence=["S052"],
    evidence_summary="QuEra's Aquila page describes a reconfigurable "
    "neutral-atom array using optical tweezers, with Rydberg interactions and "
    "all-to-all connectivity.",
    support_explanation="S052 supports the effective wording. S053 is "
    "corroborating context only and is marked insufficiently verified.",
    notes="v3 correction: the claim no longer depends on S053 for any element "
    "of its effective wording.",
    source_verification_note="S052 (QuEra, official product documentation) "
    "supports the effective wording. S053 is retained as corroborating context "
    "but is marked insufficiently_verified (no authors, year-only date), so it "
    "is not load-bearing.",
)


# ---------------------------------------------------------------------------
# Per-question domain-substitution rationales. The expected Phase 1 domain
# list is never widened; each observed outside domain is classified and
# justified against the evidence actually cited.
# ---------------------------------------------------------------------------

DOMAIN_SUBSTITUTIONS: dict[str, dict[str, tuple[str, str]]] = {
    "Q01": {
        "github.com": (
            "official_project_repository",
            "The FAISS wiki is hosted in Meta AI's official facebookresearch/faiss "
            "GitHub repository, so github.com is the canonical host for the cited "
            "project documentation.",
        )
    },
    "Q04": {
        "arxiv.org": (
            "primary_technical_source",
            "The PagedAttention paper is the primary technical source for "
            "PagedAttention; arXiv is the paper's canonical host rather than one "
            "of the Phase 1 search-plan domains.",
        )
    },
    "Q05": {
        "docs.langchain.com": (
            "canonical_alias",
            "docs.langchain.com is LangChain's current official documentation "
            "domain, replacing the legacy langchain-ai.github.io host named in the "
            "Phase 1 search plan.",
        )
    },
    "Q09": {
        "docs.k3s.io": (
            "official_project_domain",
            "K3s is a CNCF/SUSE Kubernetes distribution; docs.k3s.io is its "
            "official documentation domain and is consistent with the expected "
            "kubernetes.io/CNCF ecosystem.",
        )
    },
    "Q10": {
        "github.com": (
            "official_project_repository",
            "The Vertical Pod Autoscaler is a Kubernetes project; its official "
            "documentation lives in the kubernetes/autoscaler GitHub repository.",
        )
    },
    "Q15": {
        "www.cisa.gov": (
            "government_authority_substitution",
            "CISA is the U.S. government authority for SBOM guidance; this is an "
            "authority substitution for the Phase 1 supply-chain domains.",
        )
    },
    "Q16": {
        "falcon-sign.info": (
            "official_specification_site",
            "falcon-sign.info is the official Falcon specification site maintained "
            "by the Falcon team; it is the primary authority for Falcon and "
            "complements the expected NIST domains.",
        )
    },
    "Q23": {
        "www.cas.org": (
            "scientific_authority_substitution",
            "CAS is a scientific information authority; its solid-state battery "
            "insight is used alongside the expected DOE energy.gov source.",
        )
    },
    "Q24": {
        "arxiv.org": (
            "primary_technical_source",
            "The superconducting-qubit guide and the neutral-atom review are arXiv "
            "technical/scientific sources; arXiv is their canonical host.",
        )
    },
    "Q25": {
        "www.ipp.mpg.de": (
            "official_operator_domain",
            "Wendelstein 7-X is operated by the Max Planck Institute for Plasma "
            "Physics; ipp.mpg.de is the official operator domain.",
        )
    },
    "Q26": {
        "apt.cs.manchester.ac.uk": (
            "official_project_domain",
            "SpiNNaker is developed at the University of Manchester; "
            "apt.cs.manchester.ac.uk is the official project domain.",
        ),
        "open-neuromorphic.org": (
            "specialist_corroborating_source",
            "Open Neuromorphic is an independent technical community documenting "
            "Loihi 2; Intel's official page is also cited, so this is corroborating "
            "specialist coverage.",
        ),
    },
    "Q27": {
        "www.nvidia.com": (
            "vendor_primary_authority",
            "NVIDIA is the vendor and primary authority for its co-packaged "
            "silicon-photonics networking product; this replaces the Phase 1 "
            "IEEE/TSMC/Ayar Labs search expectations with the documented product "
            "source.",
        )
    },
    "Q28": {
        "microservices.io": (
            "canonical_pattern_catalogue",
            "microservices.io (Chris Richardson) is the canonical pattern "
            "catalogue for Event Sourcing and CQRS named in the claim; it "
            "substitutes for the Phase 1 general architecture domains.",
        )
    },
    "Q30": {
        "neon.com": (
            "canonical_alias",
            "neon.com is the current canonical domain for Neon; the Phase 1 plan "
            "recorded the legacy neon.tech alias.",
        )
    },
    "Q32": {
        "docs.wasmtime.dev": (
            "official_project_documentation",
            "docs.wasmtime.dev is the official Wasmtime documentation site "
            "maintained by the Bytecode Alliance project.",
        )
    },
    "Q34": {
        "arxiv.org": (
            "primary_technical_source",
            "The DeepSeek-R1 paper is the primary technical source and is hosted on "
            "arXiv; deepseek.com is the vendor domain named in the search plan but "
            "was not the inspected source.",
        )
    },
    "Q35": {
        "raw.githubusercontent.com": (
            "official_project_repository",
            "The Kata Containers README and hypervisor documentation are the "
            "project's canonical files served from its official GitHub repository.",
        )
    },
    "Q36": {
        "www.rfc-editor.org": (
            "canonical_alias",
            "RFC 7636 is published by the IETF and canonically hosted at "
            "rfc-editor.org; this is an alias relationship with the expected "
            "ietf.org.",
        )
    },
    "Q38": {
        "www.intel.com": (
            "vendor_primary_authority",
            "Intel Foundry's official packaging documentation is the primary source "
            "for Intel Foveros and chiplet interconnect claims; this substitutes "
            "for the Phase 1 ASML/TSMC/AnandTech expectations.",
        )
    },
    "Q40": {
        "docs.port.io": (
            "canonical_alias",
            "docs.port.io is Port's official documentation domain; getport.io is "
            "the marketing domain named in the Phase 1 plan.",
        )
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _domain_matches(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def _claim_source_domains(
    question: dict[str, Any], sources: dict[str, dict[str, Any]]
) -> tuple[list[str], dict[str, dict[str, list[str]]]]:
    actual: set[str] = set()
    per_domain: dict[str, dict[str, set[str]]] = {}
    for claim in question["ground_truth"]["claims"]:
        for source_id in claim["source_ids"]:
            source = sources[source_id]
            host = urlparse(source["url"]).netloc.lower()
            if not host:
                raise SystemExit(f"source {source_id} has no host: {source['url']}")
            actual.add(host)
            bucket = per_domain.setdefault(host, {"source_ids": set(), "claim_ids": set()})
            bucket["source_ids"].add(source_id)
            bucket["claim_ids"].add(claim["claim_id"])
    return sorted(actual), {
        host: {
            "source_ids": sorted(values["source_ids"]),
            "claim_ids": sorted(values["claim_ids"]),
        }
        for host, values in per_domain.items()
    }


def aggregate_status(statuses: list[str]) -> str:
    if all(status == "verified" for status in statuses):
        return "verified"
    if "contradicted" in statuses:
        return "contradicted"
    if "needs_more_evidence" in statuses:
        return "needs_more_evidence"
    if "time_sensitive" in statuses:
        return "time_sensitive"
    return "partially_supported"


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


def build() -> dict[str, Any]:
    actual_v1_hash = sha256(V1_PATH)
    if actual_v1_hash != V1_SHA256:
        raise SystemExit(
            f"benchmark_v1.json hash mismatch: {actual_v1_hash} != {V1_SHA256}"
        )
    actual_v2_hash = sha256(V2_PATH)
    if actual_v2_hash != V2_SHA256:
        raise SystemExit(
            f"benchmark_v2.json hash mismatch: {actual_v2_hash} != {V2_SHA256}"
        )

    with V1_PATH.open("r", encoding="utf-8") as handle:
        v1 = json.load(handle)
    with V2_PATH.open("r", encoding="utf-8") as handle:
        v2 = json.load(handle)

    if v2.get("verification_metadata", {}).get("v1_sha256") != V1_SHA256:
        raise SystemExit("benchmark_v2.json does not record the expected v1 SHA-256")

    v1_questions = v1["benchmark_questions"]
    v2_questions = v2["benchmark_questions"]
    if len(v1_questions) != 40 or len(v2_questions) != 40:
        raise SystemExit(
            f"expected 40 questions, found v1={len(v1_questions)}, v2={len(v2_questions)}"
        )

    v2_sources: dict[str, dict[str, Any]] = {
        source["source_id"]: source for source in v2["sources"]
    }
    if len(v2_sources) != len(v2["sources"]):
        raise SystemExit("duplicate source_id in benchmark_v2.json")

    v1_claim_keys = {
        f"{question['id']}-C{index}"
        for question in v1_questions
        for index in range(
            1, len(question["ground_truth"]["provisional_reference_claims"]) + 1
        )
    }
    v2_claim_keys = {
        claim["claim_id"]
        for question in v2_questions
        for claim in question["ground_truth"]["claims"]
    }
    if v1_claim_keys != v2_claim_keys:
        raise SystemExit(
            "v1/v2 claim-key mismatch: "
            f"missing={sorted(v1_claim_keys - v2_claim_keys)}, "
            f"extra={sorted(v2_claim_keys - v1_claim_keys)}"
        )
    unknown_remediations = set(REMEDIATIONS) - v2_claim_keys
    if unknown_remediations:
        raise SystemExit(f"remediation map has unknown claim IDs: {sorted(unknown_remediations)}")

    # Verify v1 wording is still identical to v2 before changing anything.
    v1_by_id = {question["id"]: question for question in v1_questions}
    for question in v2_questions:
        original = v1_by_id[question["id"]]
        if (
            question["ground_truth"]["provisional_reference_claims"]
            != original["ground_truth"]["provisional_reference_claims"]
        ):
            raise SystemExit(f"{question['id']}: v2 changed the original claim wording")

    v3_questions: list[dict[str, Any]] = []
    claim_status_counts: Counter[str] = Counter()
    resolution_counts: Counter[str] = Counter()
    question_status_counts: Counter[str] = Counter()
    questions_verified = 0
    claims_corrected = 0
    claims_downgraded = 0
    claims_unchanged = 0
    exact_wording_met = 0
    domain_mismatch_questions = 0
    domain_mismatch_claims: set[str] = set()
    outside_domains: set[str] = set()

    for question in v2_questions:
        question_copy = copy.deepcopy(question)
        ground_truth = question_copy["ground_truth"]
        original_claims = ground_truth["provisional_reference_claims"]
        original_question_status = ground_truth.get("verification_status")
        claim_records: list[dict[str, Any]] = []
        corrected_in_question = 0
        downgraded_in_question = 0

        for index, original_text in enumerate(original_claims, start=1):
            claim_id = f"{question['id']}-C{index}"
            v2_claim = ground_truth["claims"][index - 1]
            if v2_claim["claim_id"] != claim_id:
                raise SystemExit(
                    f"{claim_id}: v2 claim_id mismatch {v2_claim['claim_id']!r}"
                )
            if v2_claim["claim_text"] != original_text:
                raise SystemExit(f"{claim_id}: v2 claim_text is not verbatim v1 wording")

            original_status = v2_claim["status"]
            original_gap = v2_claim.get("contradiction_or_gap")
            original_correction = v2_claim.get("suggested_correction")
            remediation = REMEDIATIONS.get(claim_id)

            if remediation is None:
                corrected_text = None
                status = original_status
                resolution = (
                    "confirmed_verified"
                    if original_status == "verified"
                    else (
                        "retained_partially_supported"
                        if original_status == "partially_supported"
                        else "retained_needs_more_evidence"
                    )
                )
                if resolution not in VALID_RESOLUTIONS:
                    raise SystemExit(
                        f"{claim_id}: unsupported unchanged status {original_status!r}"
                    )
                reason = {
                    "confirmed_verified": (
                        "No change: the recorded evidence was re-reviewed and "
                        "supports the original wording exactly."
                    ),
                    "retained_partially_supported": (
                        "No change: the recorded evidence supports the claim's core "
                        "but not its stated qualifier or generalisation."
                    ),
                    "retained_needs_more_evidence": (
                        "No change: no inspected primary source establishes the "
                        "claim, so it remains unresolved."
                    ),
                }[resolution]
                finding = None
                correction_evidence = None
                evidence_summary = v2_claim["evidence_summary"]
                support_explanation = v2_claim["support_explanation"]
                gap = original_gap
                correction = original_correction
                notes = v2_claim.get("notes", "")
                source_verification_note = None
                claims_unchanged += 1
            else:
                finding = remediation["finding"]
                corrected_text = remediation["corrected"]
                status = remediation["status"]
                resolution = remediation["resolution"]
                reason = remediation["reason"]
                correction_evidence = remediation["correction_evidence"]
                evidence_summary = (
                    remediation["evidence_summary"] or v2_claim["evidence_summary"]
                )
                support_explanation = (
                    remediation["support_explanation"]
                    or v2_claim["support_explanation"]
                )
                notes = remediation["notes"] if remediation["notes"] is not None else v2_claim.get("notes", "")
                source_verification_note = remediation["source_verification_note"]

                if resolution == "corrected_and_verified":
                    if corrected_text is None or not correction_evidence:
                        raise SystemExit(
                            f"{claim_id}: corrected_and_verified requires corrected "
                            "wording and correction evidence"
                        )
                    gap = None
                    correction = None
                    claims_corrected += 1
                    corrected_in_question += 1
                elif resolution == "downgraded_needs_more_evidence":
                    if corrected_text is not None:
                        raise SystemExit(
                            f"{claim_id}: downgraded claim must not carry corrected wording"
                        )
                    gap = (
                        "No inspected primary source documents the REST mechanics "
                        "asserted in the claim."
                    )
                    correction = (
                        "Add an authoritative REST primary source (for example an "
                        "HTTP/REST standard or official REST API guide) or reword the "
                        "claim around gRPC/GraphQL comparisons only."
                    )
                    claims_downgraded += 1
                    downgraded_in_question += 1
                else:
                    raise SystemExit(
                        f"{claim_id}: unsupported remediation resolution {resolution!r}"
                    )

            effective_text = corrected_text if corrected_text is not None else original_text
            exact = status == "verified"
            if exact:
                exact_wording_met += 1
            elif status == "contradicted":
                raise SystemExit(f"{claim_id}: v3 must not leave a contradicted claim")

            if status == "verified":
                if gap is not None or correction is not None:
                    raise SystemExit(
                        f"{claim_id}: verified claim must not carry a gap or correction"
                    )
            else:
                if not gap or not correction:
                    raise SystemExit(
                        f"{claim_id}: unresolved claim must carry a gap and correction"
                    )

            review_history = [
                {
                    "date": VERIFICATION_DATE,
                    "reviewer": V2_VERIFIER,
                    "action": "initial_verification",
                    "status_before": None,
                    "status_after": original_status,
                    "reason": "Recorded in benchmark_v2.json.",
                    "evidence_refs": list(v2_claim["source_ids"]),
                },
                {
                    "date": VERIFICATION_DATE,
                    "reviewer": V3_VERIFIER,
                    "action": resolution,
                    "status_before": original_status,
                    "status_after": status,
                    "reason": reason,
                    "evidence_refs": (
                        list(correction_evidence)
                        if correction_evidence is not None
                        else list(v2_claim["source_ids"])
                    ),
                },
            ]

            claim_records.append(
                {
                    "claim_id": claim_id,
                    "claim_text": original_text,
                    "corrected_claim_text": corrected_text,
                    "effective_claim_text": effective_text,
                    "status": status,
                    "original_status": original_status,
                    "resolution": resolution,
                    "status_change_reason": reason,
                    "correction_evidence": (
                        list(correction_evidence)
                        if correction_evidence is not None
                        else None
                    ),
                    "exact_wording_met": exact,
                    "exact_wording_assessment": (
                        "The effective wording is supported by the cited inspected "
                        "sources."
                        if exact
                        else "The recorded gap prevents exact-wording verification."
                    ),
                    "review_reference": REVIEW_REFERENCE if finding else None,
                    "finding": finding,
                    "source_ids": list(v2_claim["source_ids"]),
                    "source_verification_note": source_verification_note,
                    "evidence_summary": evidence_summary,
                    "support_explanation": support_explanation,
                    "contradiction_or_gap": gap,
                    "suggested_correction": correction,
                    "original_contradiction_or_gap": original_gap,
                    "original_suggested_correction": original_correction,
                    "review_history": review_history,
                    "verifier": V3_VERIFIER,
                    "verified_at": VERIFICATION_DATE,
                    "notes": notes,
                }
            )
            claim_status_counts[status] += 1
            resolution_counts[resolution] += 1

        statuses = [record["status"] for record in claim_records]
        aggregate = aggregate_status(statuses)
        is_verified = aggregate == "verified"
        if is_verified:
            questions_verified += 1
        question_status_counts[aggregate] += 1

        ground_truth["original_verification_status"] = original_question_status
        ground_truth["verification_status"] = aggregate
        ground_truth["is_verified"] = is_verified
        ground_truth["verified_by"] = V3_VERIFIER if is_verified else None
        ground_truth["reviewed_by"] = V3_VERIFIER
        ground_truth["reviewed_at"] = VERIFICATION_DATE
        if is_verified:
            ground_truth["verified_at"] = "2026-11-12T00:00:00Z"
        else:
            ground_truth.pop("verified_at", None)

        verified_count = sum(status == "verified" for status in statuses)
        ground_truth["verification_notes"] = (
            f"v3 remediation completed {VERIFICATION_DATE}. "
            f"{verified_count} of {len(statuses)} claims verified; "
            f"{corrected_in_question} wording correction(s), "
            f"{downgraded_in_question} downgrade(s); aggregate status: {aggregate}. "
            "See ground_truth.claims for original wording, effective wording, "
            "evidence, corrections and review history."
        )
        ground_truth["claims"] = claim_records

        # Reconcile the frozen Phase 1 expected domains with actual citations.
        expected_domains = list(question_copy["metadata"]["expected_source_domains"])
        actual_domains, per_domain = _claim_source_domains(question_copy, v2_sources)
        outside = [
            host
            for host in actual_domains
            if not any(_domain_matches(host, domain) for domain in expected_domains)
        ]
        substitutions: list[dict[str, Any]] = []
        if outside:
            domain_mismatch_questions += 1
            outside_domains.update(outside)
            rationales = DOMAIN_SUBSTITUTIONS.get(question["id"], {})
            missing = [host for host in outside if host not in rationales]
            if missing:
                raise SystemExit(
                    f"{question['id']}: missing domain-substitution rationale for {missing}"
                )
            for host in outside:
                classification, rationale = rationales[host]
                if not rationale.strip():
                    raise SystemExit(
                        f"{question['id']}: empty rationale for outside domain {host}"
                    )
                entries = per_domain[host]
                domain_mismatch_claims.update(entries["claim_ids"])
                substitutions.append(
                    {
                        "observed_domain": host,
                        "classification": classification,
                        "rationale": rationale,
                        "source_ids": entries["source_ids"],
                        "claim_ids": entries["claim_ids"],
                    }
                )

        question_copy["metadata"]["source_domain_audit"] = {
            "expected_source_domains": expected_domains,
            "actual_source_domains": actual_domains,
            "outside_expected_source_domains": outside,
            "mismatch_claim_ids": sorted(
                {
                    claim_id
                    for host in outside
                    for claim_id in per_domain[host]["claim_ids"]
                }
            ),
            "mismatch_claim_count": len(
                {
                    claim_id
                    for host in outside
                    for claim_id in per_domain[host]["claim_ids"]
                }
            ),
            "status": (
                "reconciled_with_documented_substitutions"
                if outside
                else "within_expected_domains"
            ),
            "substitutions": substitutions,
        }

        v3_questions.append(question_copy)

    # --- Source verification is recorded separately from claim verification.
    v3_sources: list[dict[str, Any]] = []
    sources_insufficient: list[str] = []
    for source in v2["sources"]:
        source_copy = copy.deepcopy(source)
        if source_copy["source_id"] == "S053":
            source_copy["verification_status"] = "insufficiently_verified"
            source_copy["bibliographic_gaps"] = [
                "authors missing",
                "publication_date is year-only ('2026')",
                "version/DOI not recorded",
            ]
            source_copy["v3_review_note"] = (
                "Cannot be independently confirmed from the repository: no authors, "
                "no full publication date and no version/DOI. Retained as "
                "corroborating context only; it is not load-bearing for any verified "
                "claim."
            )
            sources_insufficient.append(source_copy["source_id"])
        else:
            source_copy["verification_status"] = "verified_in_repository_record"
            source_copy["v3_review_note"] = (
                "Bibliographic metadata and inspection note are internally "
                "consistent; content was not re-fetched during v3 remediation "
                "(repository-only review)."
            )
        v3_sources.append(source_copy)

    if sources_insufficient != ["S053"]:
        raise SystemExit(f"unexpected insufficient sources: {sources_insufficient}")

    # Every verified claim that cites an insufficient source needs an explicit
    # dependency note and at least one other source.
    for question in v3_questions:
        for claim in question["ground_truth"]["claims"]:
            if claim["status"] != "verified":
                continue
            if "S053" in claim["source_ids"]:
                if not claim["source_verification_note"]:
                    raise SystemExit(
                        f"{claim['claim_id']}: verified claim cites S053 without a "
                        "source-verification dependency note"
                    )
                if len(claim["source_ids"]) < 2:
                    raise SystemExit(
                        f"{claim['claim_id']}: verified claim depends solely on S053"
                    )

    domain_mismatch_claim_count = len(domain_mismatch_claims)
    if domain_mismatch_claim_count != 39:
        raise SystemExit(
            f"expected 39 claims with domain mismatches, found {domain_mismatch_claim_count}"
        )
    if domain_mismatch_questions != 20:
        raise SystemExit(
            f"expected 20 questions with domain mismatches, found {domain_mismatch_questions}"
        )

    claims_total = sum(claim_status_counts.values())
    if claims_total != 120:
        raise SystemExit(f"expected 120 claims, found {claims_total}")
    if claims_corrected != 30:
        raise SystemExit(f"expected 30 corrected claims, found {claims_corrected}")
    if claims_downgraded != 1:
        raise SystemExit(f"expected 1 downgraded claim, found {claims_downgraded}")
    if claims_unchanged != 89:
        raise SystemExit(f"expected 89 unchanged claims, found {claims_unchanged}")
    if exact_wording_met != claim_status_counts["verified"]:
        raise SystemExit("exact-wording count does not match verified claim count")
    if claim_status_counts.get("contradicted", 0) != 0:
        raise SystemExit("v3 must resolve all contradicted claims")

    metadata = {
        "verification_date": VERIFICATION_DATE,
        "verifier": V3_VERIFIER,
        "previous_version": "benchmark_v2.json",
        "v1_sha256": V1_SHA256,
        "v2_sha256": V2_SHA256,
        "methodology": (
            "Version 3 re-reviewed every v2 claim against the recorded source "
            "inspection notes. Original wording is preserved in claim_text. Where "
            "the original wording exceeded the inspected evidence, corrected "
            "wording is recorded in corrected_claim_text and status applies to "
            "effective_claim_text. No claim was promoted by rewriting alone: a "
            "corrected claim is verified only when the cited inspection notes "
            "support the corrected wording exactly. Unsupported claims were "
            "downgraded and retained as unresolved."
        ),
        "source_verification_methodology": (
            "Source verification is recorded independently from claim "
            "verification. Each v2 source record was checked for internal "
            "bibliographic consistency (title, publisher, URL, date, inspection "
            "note). Content was not re-fetched during v3; this is a repository-only "
            "review. S053 is marked insufficiently_verified because its record has "
            "no authors, only a year-level date and no version/DOI."
        ),
        "claim_verification_methodology": (
            "A claim is verified only when the exact meaning of its effective "
            "wording is supported by the cited inspected sources. A qualifier, "
            "number, example, ranking or mechanism that the record does not "
            "establish prevents verification. Contradicted and unsupported claims "
            "remain unresolved unless the record supports a narrower correction."
        ),
        "source_inspection_policy": (
            "Prefer peer-reviewed papers, standards, official project "
            "documentation, government publications and vendor technical "
            "documentation. Record URL, title, publisher, publication/version "
            "date, access date and inspection notes for every source."
        ),
        "status_definitions": {
            "verified": "The exact meaning of the effective_claim_text is supported by the cited source(s).",
            "partially_supported": "The core of the claim is supported but a qualifier, number or generalisation is not.",
            "contradicted": "At least one material part of the claim is contradicted by the cited source(s).",
            "needs_more_evidence": "No inspected primary source establishes the claim; further evidence is required.",
            "time_sensitive": "The claim was accurate for a stated date/version but may change.",
        },
        "v3_status_scope": (
            "status applies to effective_claim_text. The status of the original "
            "claim_text is preserved in original_status and in review_history."
        ),
        "aggregation_rule": (
            "A question is verified only when every claim is verified. Otherwise "
            "the question status is contradicted if any claim is contradicted, "
            "else needs_more_evidence if any claim needs more evidence, else "
            "time_sensitive if any claim is time-sensitive, else "
            "partially_supported."
        ),
        "claims_total": claims_total,
        "claims_by_status": dict(sorted(claim_status_counts.items())),
        "claims_corrected": claims_corrected,
        "claims_downgraded": claims_downgraded,
        "claims_unchanged": claims_unchanged,
        "claims_exact_wording_met": exact_wording_met,
        "claims_exact_wording_not_met": claims_total - exact_wording_met,
        "claims_by_resolution": dict(sorted(resolution_counts.items())),
        "questions_total": len(v3_questions),
        "questions_verified": questions_verified,
        "questions_by_status": dict(sorted(question_status_counts.items())),
        "sources_total": len(v3_sources),
        "sources_verified_in_repository_record": len(v3_sources) - len(sources_insufficient),
        "sources_insufficiently_verified": len(sources_insufficient),
        "insufficiently_verified_source_ids": sources_insufficient,
        "source_domain_mismatch_questions": domain_mismatch_questions,
        "source_domain_mismatch_claims": domain_mismatch_claim_count,
        "source_domain_distinct_outside": len(outside_domains),
    }

    v3 = {
        "version": "3.0.0",
        "created_at": "2026-11-12T00:00:00Z",
        "description": (
            "Remediated Phase 3 benchmark dataset. Version 3 preserves all 40 "
            "questions, 120 claims, question IDs, taxonomy values, rubrics and "
            "original claim wording from versions 1 and 2. It adds original and "
            "corrected wording, effective-wording verification status, explicit "
            "resolution reasons, separated source and claim verification, review "
            "history, and per-question source-domain reconciliation."
        ),
        "methodology_disclaimer": (
            "Structural and factual verification only. Claim statuses describe the "
            "degree to which the effective claim wording is supported by the cited "
            "sources as recorded on 2026-11-12. They are not benchmark execution "
            "results and do not assert that an AI agent will answer the question "
            "correctly. Content was not re-fetched during this remediation; S053 is "
            "insufficiently verified and is not load-bearing for any verified claim."
        ),
        "supersedes": "benchmark_v2.json",
        "verification_metadata": metadata,
        "sources": v3_sources,
        "benchmark_questions": v3_questions,
    }
    return v3


def main() -> int:
    v1_before = sha256(V1_PATH)
    v2_before = sha256(V2_PATH)
    v3 = build()
    v1_after = sha256(V1_PATH)
    v2_after = sha256(V2_PATH)
    if v1_before != v1_after or v2_before != v2_after:
        raise SystemExit("v1 or v2 changed during the v3 build")

    V3_PATH.parent.mkdir(parents=True, exist_ok=True)
    with V3_PATH.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(v3, handle, indent=2, ensure_ascii=False)
        handle.write("\n")

    counts = Counter(
        claim["status"]
        for question in v3["benchmark_questions"]
        for claim in question["ground_truth"]["claims"]
    )
    print(f"Wrote {V3_PATH}")
    print(f"Questions: {len(v3['benchmark_questions'])}")
    print(f"Claims: {sum(counts.values())}")
    print(f"Claim statuses: {dict(sorted(counts.items()))}")
    print(f"Corrected claims: {v3['verification_metadata']['claims_corrected']}")
    print(f"Downgraded claims: {v3['verification_metadata']['claims_downgraded']}")
    print(f"Verified questions: {v3['verification_metadata']['questions_verified']}")
    print(
        "Source-domain mismatches: "
        f"{v3['verification_metadata']['source_domain_mismatch_claims']} claims / "
        f"{v3['verification_metadata']['source_domain_mismatch_questions']} questions"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
