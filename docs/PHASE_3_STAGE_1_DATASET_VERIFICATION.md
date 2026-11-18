# Phase 3 Stage 1 - Dataset Verification Report

**Dataset under verification:** `benchmarks/datasets/benchmark_v2.json`  
**Frozen source dataset:** `benchmarks/datasets/benchmark_v1.json`  
**Verification date:** 2026-11-12  
**Verifier:** OpenCode verification agent (DeepSeek V4.1 Flash Free)  
**Method:** direct inspection of primary sources; one evidence record per claim  
**Final status:** **NEEDS ATTENTION**

---

## 1. Purpose and scope

This report records the factual verification of all 120 provisional reference claims attached to the 40 benchmark questions in `benchmark_v1.json`. The result is a new, evidence-backed dataset, `benchmark_v2.json`, that:

- preserves all 40 question IDs, taxonomy values, rubrics, and the exact original wording of every claim;
- attaches an auditable verification record to every claim;
- maps every claim to one or more directly inspected primary sources;
- records claim status, evidence, contradictions/gaps, suggested corrections, verifier, and verification date;
- derives the question-level verification flag from the claim outcomes;
- leaves every unresolved claim unverified and preserves its original wording.

This stage is **verification only**. It does not execute the research agent, run the benchmark, score answers, connect to PostgreSQL or Ollama, or change application logic.

---

## 2. Verification method

### 2.1 Evidence standard

A claim was not treated as verified because a URL existed. Each claim was compared with the exact meaning of the inspected source. Sources were preferred in this order:

1. peer-reviewed papers and preprints;
2. standards and specifications (NIST, IETF, W3C, OpenSSF, FIDO, SLSA);
3. official project documentation;
4. government publications;
5. vendor technical documentation.

General blog posts, search-result snippets, and secondary summaries were not used as the sole basis for a `verified` status. Where only a vendor claim was inspected, the limitation is recorded in the evidence record.

### 2.2 Claim status vocabulary

| Status | Meaning |
|---|---|
| `verified` | The claim's exact meaning is supported by the cited source(s). |
| `partially_supported` | The core of the claim is supported, but a qualifier, number, or generalisation is not. |
| `contradicted` | At least one material part of the claim is contradicted by the cited source(s). |
| `needs_more_evidence` | No inspected primary source establishes the claim; further evidence is required. |
| `time_sensitive` | Accurate for a stated date/version but may change. |

### 2.3 Question aggregation rule

A question is `verified` only when **all** of its claims are `verified`. Otherwise:

1. any `contradicted` claim → question `contradicted`;
2. else any `needs_more_evidence` claim → question `needs_more_evidence`;
3. else any `time_sensitive` claim → question `time_sensitive`;
4. else → question `partially_supported`.

`is_verified` is `true` only for questions with status `verified`. Unresolved questions have `verified_by = null` and no `verified_at`.

### 2.4 Source registry

`benchmark_v2.json` records **86 sources** (`S001`-`S086`). Every source record contains:

- `source_id`
- `url`
- `title`
- `publisher`
- `publication_date` (or version/living-document marker)
- `accessed_date` (`2026-11-12`)
- `source_type`
- `inspection_notes`

Source types include preprints, standards, government standards, official/project/vendor documentation, specifications, research project pages, and industry frameworks.

---

## 3. Summary results

### 3.1 Claim outcomes

| Status | Claims |
|---|---:|
| `verified` | 97 |
| `partially_supported` | 19 |
| `contradicted` | 3 |
| `needs_more_evidence` | 1 |
| `time_sensitive` | 0 |
| **Total** | **120** |

### 3.2 Question outcomes

| Status | Questions |
|---|---:|
| `verified` | 23 |
| `partially_supported` | 13 |
| `contradicted` | 3 |
| `needs_more_evidence` | 1 |
| `time_sensitive` | 0 |
| **Total** | **40** |

**23 of 40 questions are fully verified.** The remaining 17 questions contain at least one unresolved claim. No claim was marked verified on the basis of partial or contradictory evidence.

### 3.3 Questions requiring action

| Category | Questions |
|---|---|
| Contradicted | Q12, Q15, Q34 |
| Needs more evidence | Q17 |
| Partially supported | Q07, Q09, Q20, Q22, Q24, Q25, Q27, Q30, Q31, Q32, Q33, Q38, Q40 |

---

## 4. Per-question coverage

| Q | Topic | Claims | Question status | Unresolved claims |
|---|---|---|---|---|
| Q01 | Retrieval Augmented Generation architecture and core components | 3 | verified | None |
| Q02 | Parameter Efficient Fine Tuning methods: LoRA and QLoRA | 3 | verified | None |
| Q03 | Speculative Decoding vs Medusa for LLM inference acceleration | 3 | verified | None |
| Q04 | vLLM PagedAttention vs Hugging Face TGI memory architectures | 3 | verified | None |
| Q05 | Agentic workflows using LangGraph vs AutoGen vs CrewAI | 3 | verified | None |
| Q06 | Mixture of Depths and conditional computation in transformer architectures | 3 | verified | None |
| Q07 | Direct Preference Optimization (DPO) vs RLHF with PPO | 3 | partially_supported | Q07-C3 |
| Q08 | Serverless computing advantages and cold start latency mitigations | 3 | verified | None |
| Q09 | Kubernetes vs HashiCorp Nomad for edge computing orchestration | 3 | partially_supported | Q09-C3 |
| Q10 | FinOps best practices for multi-cloud Kubernetes clusters | 3 | verified | None |
| Q11 | eBPF vs sidecar proxies for service mesh observability and networking | 3 | verified | None |
| Q12 | Confidential computing hardware progress in public clouds: AMD SEV-SNP vs Intel TDX | 3 | contradicted | Q12-C1 |
| Q13 | Zero Trust Architecture core principles and NIST SP 800-207 | 3 | verified | None |
| Q14 | Passkeys and WebAuthn vs Traditional Multi-Factor Authentication | 3 | verified | None |
| Q15 | Software Supply Chain Security: SBOMs, Sigstore, and SLSA framework | 3 | contradicted | Q15-C3 |
| Q16 | Post-quantum cryptography standards: ML-KEM vs Dilithium vs Falcon | 3 | verified | None |
| Q17 | AI-assisted vulnerability discovery and automated patch generation | 3 | needs_more_evidence | Q17-C1, Q17-C2, Q17-C3 |
| Q18 | Apache Iceberg table format architecture and metadata layers | 3 | verified | None |
| Q19 | Delta Lake vs Apache Iceberg vs Apache Hudi feature comparison | 3 | verified | None |
| Q20 | ClickHouse vs DuckDB for analytical query processing | 3 | partially_supported | Q20-C3 |
| Q21 | Real-time streaming lakehouses using Apache Flink and Iceberg | 3 | verified | None |
| Q22 | Vector database indexing algorithms: HNSW vs DiskANN vs ScaNN | 3 | partially_supported | Q22-C2 |
| Q23 | Solid-state battery technology advantages over lithium-ion cells | 3 | verified | None |
| Q24 | Superconducting qubits vs Neutral atom quantum computing | 3 | partially_supported | Q24-C3 |
| Q25 | Commercial fusion energy approaches: Tokamak vs Stellarator vs Magnetized Target | 3 | partially_supported | Q25-C3 |
| Q26 | Neuromorphic computing hardware architectures: Intel Loihi 2 vs SpiNNaker 2 | 3 | verified | None |
| Q27 | Silicon photonics for inter-chip interconnects in AI compute clusters | 3 | partially_supported | Q27-C1, Q27-C3 |
| Q28 | Event-Driven Architecture: Event sourcing vs CQRS patterns | 3 | verified | None |
| Q29 | gRPC vs REST vs GraphQL for high-throughput internal microservices | 3 | verified | None |
| Q30 | Database branching and ephemeral environments for developer workflows | 3 | partially_supported | Q30-C2, Q30-C3 |
| Q31 | Rust vs Go for building high-performance distributed networking engines | 3 | partially_supported | Q31-C1, Q31-C3 |
| Q32 | WebAssembly (Wasm) in serverless and edge compute runtimes: Wasmtime vs WasmEdge | 3 | partially_supported | Q32-C1 |
| Q33 | Function calling and tool use mechanisms in modern LLMs | 3 | partially_supported | Q33-C2 |
| Q34 | Open-weights reasoning models: DeepSeek-R1 vs QwQ vs OpenAI o1 architecture | 3 | contradicted | Q34-C2 |
| Q35 | Multi-tenant isolation strategies in containerized environments | 3 | verified | None |
| Q36 | OAuth 2.0 Authorization Code flow with PKCE | 3 | verified | None |
| Q37 | Graph RAG architectures combining knowledge graphs with vector databases | 3 | verified | None |
| Q38 | High-NA EUV lithography deployment and semiconductor packaging | 3 | partially_supported | Q38-C2, Q38-C3 |
| Q39 | Distributed tracing with OpenTelemetry: traces, spans, and context propagation | 3 | verified | None |
| Q40 | Platform Engineering: Backstage vs Port for developer self-service portals | 3 | partially_supported | Q40-C2 |

---

## 5. Contradicted claims

These are material factual defects in the original v1 wording. They must be corrected before the dataset is treated as a stable reference answer key.

### Q12-C1

- **Original claim:** "Confidential computing protects data in-use by encrypting CPU memory registers and enforcing hardware-backed hypervisor isolation."
- **Status:** `contradicted`
- **Sources:** S025 Google Cloud Confidential VM overview; S026 Intel TDX.
- **Finding:** Confidential VMs encrypt guest **memory**; CPU state/registers are isolated and protected, not encrypted. The original conflates memory encryption with register protection.
- **Suggested correction:** "Confidential computing protects data in use by encrypting guest memory and isolating/protecting CPU register state, with hardware-backed hypervisor isolation."

### Q15-C3

- **Original claim:** "The SLSA framework establishes incremental security tracks (SLSA 1-4) guaranteeing build integrity and tamper-proof provenance."
- **Status:** `contradicted`
- **Source:** S031 SLSA v1.0 Security Levels (OpenSSF).
- **Finding:** SLSA v1.0 uses a Build track with levels **L0-L3**. The older specification used SLSA 1-4. The v1.0 page also states that Build L1 provenance is trivial to bypass or forge, so "tamper-proof provenance" is false at that level.
- **Suggested correction:** "SLSA v1.0 defines Build levels L0-L3 (the older specification used SLSA 1-4); higher levels provide increasing, not absolute, tamper resistance."

### Q34-C2

- **Original claim:** "DeepSeek-R1 demonstrated that large-scale reinforcement learning (via GRPO) with rule-based verification induces self-reflection and reasoning without supervised fine-tuning."
- **Status:** `contradicted`
- **Sources:** S067 DeepSeek-R1 paper; S068 Qwen QwQ-32B announcement.
- **Finding:** The final DeepSeek-R1 model used cold-start supervised data and multi-stage training. Only **R1-Zero** was the pure-RL variant without SFT. The original sentence is false for "DeepSeek-R1" as a whole.
- **Suggested correction:** "DeepSeek-R1 combined large-scale RL and rule-based verification with cold-start SFT data and multi-stage training; R1-Zero was the pure-RL variant."

---

## 6. Claims needing more evidence

### Q17-C2

- **Original claim:** "Automated Program Repair (APR) leverages code models to synthesize candidate patches for memory safety and logic vulnerabilities."
- **Status:** `needs_more_evidence`
- **Source inspected:** S036 Google Project Zero, "From Naptime to Big Sleep".
- **Finding:** The source documents vulnerability discovery and testcase generation for a real SQLite memory-safety bug. It does not demonstrate automated patch synthesis or validated patching for memory-safety or logic vulnerabilities.
- **Suggested correction:** Cite a specific APR system/paper, or soften the claim to "APR is an active research direction".

Q17-C1 and Q17-C3 are `partially_supported` for related reasons: the source supports LLM-assisted discovery and general caution, but not grammar-aware fuzz-seed generation or each named APR risk individually.

---

## 7. Partially supported claims

Each entry below preserves the original wording, records the inspected source(s), states the gap, and gives a suggested correction. These are **not** marked verified.

### Q07 - Direct Preference Optimization (DPO) vs RLHF with PPO

Question status: `partially_supported`

- **Q07-C3** - `partially_supported`
  - Original claim: "DPO offers greater training stability and lower compute overhead, though RLHF remains relevant for complex iterative exploration."
  - Sources: S014 (arXiv, Rafailov et al., https://arxiv.org/abs/2305.18290).
  - Evidence: DPO removes the reward-model stage and is reported as stable and performant, but the paper does not establish a universal stability/compute advantage over RLHF.
  - Gap: The comparative and forward-looking parts of the claim exceed the inspected evidence.
  - Suggested correction: "DPO avoids a separate reward model and is simpler to train; relative stability/compute and the residual role of RLHF are context-dependent."

### Q09 - Kubernetes vs HashiCorp Nomad for edge computing orchestration

Question status: `partially_supported`

- **Q09-C3** - `partially_supported`
  - Original claim: "Kubernetes provides a much broader ecosystem and cloud-native integration (CRDs, CSI, CNI), but with higher operational overhead (K3s/KubeEdge mitigate this)."
  - Sources: S018 (CNCF, https://kubernetes.io/docs/concepts/overview/); S019 (SUSE/Rancher, https://docs.k3s.io/).
  - Evidence: Kubernetes has a broader ecosystem and CRD/CSI/CNI extension points; K3s provides a lightweight distribution for constrained environments.
  - Gap: KubeEdge was not directly inspected, and operational overhead is deployment-specific.
  - Suggested correction: Keep the ecosystem/extension-point comparison; qualify the overhead and KubeEdge claims.

### Q20 - ClickHouse vs DuckDB for analytical query processing

Question status: `partially_supported`

- **Q20-C3** - `partially_supported`
  - Original claim: "Both engines feature vectorized execution, but ClickHouse targets enterprise clusters while DuckDB excels in single-node data science and local pipelines."
  - Sources: S042 (ClickHouse, https://clickhouse.com/docs/en/intro); S043 (DuckDB Foundation, https://duckdb.org/why_duckdb).
  - Evidence: Both engines use vectorised execution, but the "ClickHouse = enterprise clusters, DuckDB = single-node" split is a simplification.
  - Gap: The comparative framing is directionally useful but not a strict product boundary.
  - Suggested correction: "ClickHouse is optimised for large distributed OLAP deployments, while DuckDB is optimised for embedded/local analytics; both support vectorised execution."

### Q22 - Vector database indexing algorithms: HNSW vs DiskANN vs ScaNN

Question status: `partially_supported`

- **Q22-C2** - `partially_supported`
  - Original claim: "DiskANN compresses vectors in RAM while storing a single-layer Vamana graph on SSDs, drastically lowering memory requirements."
  - Source: S045 (Microsoft Research, https://www.microsoft.com/en-us/research/project/project-akupara-approximate-nearest-neighbor-search/).
  - Evidence: DiskANN uses auxiliary SSD storage and low DRAM to index far more points per machine.
  - Gap: The "single-layer Vamana graph" detail requires the DiskANN paper or source code, which were not inspected for this record.
  - Suggested correction: Cite the DiskANN paper for the Vamana detail, or remove "single-layer Vamana".

### Q24 - Superconducting qubits vs Neutral atom quantum computing

Question status: `partially_supported`

- **Q24-C3** - `partially_supported`
  - Original claim: "Superconducting systems feature rapid gate speeds but limited physical connectivity; neutral atoms offer all-to-all reconfigurable qubit arrays and longer coherence times."
  - Sources: S052 (QuEra, https://www.quera.com/aquila); S053 (arXiv, https://arxiv.org/abs/2608.05010).
  - Evidence: Neutral atoms offer reconfigurable all-to-all connectivity and long coherence; the superconducting comparison is qualitative.
  - Gap: The comparative half of the claim is not directly evidenced by a single inspected primary source.
  - Suggested correction: Keep the all-to-all/reconfigurability and coherence statements; qualify the superconducting comparison as typical rather than universal.

### Q25 - Commercial fusion energy approaches

Question status: `partially_supported`

- **Q25-C3** - `partially_supported`
  - Original claim: "Private fusion companies utilize high-temperature superconducting (HTS) magnets (REBCO) to achieve higher magnetic field strength in smaller reactor volumes."
  - Source: S056 (Commonwealth Fusion Systems, https://cfs.energy/technology).
  - Evidence: CFS uses HTS REBCO magnets to achieve high field in a compact tokamak.
  - Gap: Only one private company was directly inspected.
  - Suggested correction: "Some private fusion companies, notably CFS, use HTS magnets..." or add sources for the other companies.

### Q27 - Silicon photonics for inter-chip interconnects in AI compute clusters

Question status: `partially_supported`

- **Q27-C1** - `partially_supported`
  - Original claim: "Electrical interconnects (copper traces) encounter severe physical limits in attenuation, signal integrity, and power dissipation at high frequencies (>100 Gbps)."
  - Source: S086 (NVIDIA, https://www.nvidia.com/en-us/networking/products/silicon-photonics/).
  - Evidence: NVIDIA states that co-packaged optics overcome the limits of electrical signalling in large-scale AI factories.
  - Gap: The specific numerical threshold is not directly evidenced.
  - Suggested correction: "Electrical interconnects degrade in reach, signal integrity and power at high bandwidth; CPO addresses these limits."

- **Q27-C3** - `partially_supported`
  - Original claim: "Optical interconnects increase bandwidth density by an order of magnitude while cutting latency and energy consumption per bit."
  - Source: S086 (NVIDIA, https://www.nvidia.com/en-us/networking/products/silicon-photonics/).
  - Evidence: NVIDIA reports 5x better power efficiency and higher network resiliency for CPO versus pluggable transceivers.
  - Gap: The "order of magnitude" and "energy per bit" wording exceeds the inspected evidence.
  - Suggested correction: Use the vendor-reported 5x power-efficiency figure and qualify bandwidth-density claims.

### Q30 - Database branching and ephemeral environments

Question status: `partially_supported`

- **Q30-C2** - `partially_supported`
  - Original claim: "Ephemeral preview environments spin up isolated database branches per pull request, enabling safe integration testing without staging contamination."
  - Source: S061 (Neon, https://neon.com/docs/introduction/branching).
  - Evidence: Neon supports isolated branches for development, testing and parallel CI/CD, with integrations that can automate per-PR environments.
  - Gap: The claim states a general database-branching guarantee that is actually integration-specific.
  - Suggested correction: "Database branching enables isolated per-PR preview environments when wired into CI/CD; Neon documents the branch primitive and integrations."

- **Q30-C3** - `partially_supported`
  - Original claim: "Schema changes can be validated and merged non-destructively through automated migration deployment pipelines."
  - Source: S061 (Neon, https://neon.com/docs/introduction/branching).
  - Evidence: Schema changes can be tested on an isolated branch and merged; automated migration deployment is a pipeline concern.
  - Gap: The migration-automation half is not directly supported by the branching documentation.
  - Suggested correction: Separate the branch-validation capability from the migration pipeline claim.

### Q31 - Rust vs Go for distributed networking engines

Question status: `partially_supported`

- **Q31-C1** - `partially_supported`
  - Original claim: "Go provides rapid compilation, developer productivity, and lightweight concurrency via goroutines and channels, but relies on a garbage collector that can introduce p99 tail latency spikes."
  - Source: S062 (Go Project, https://go.dev/doc/faq).
  - Evidence: Go provides fast compilation, goroutines/channels and garbage collection; p99 tail-latency spikes are workload-dependent.
  - Gap: The tail-latency characterisation is an operational observation, not a documented language guarantee.
  - Suggested correction: "GC can contribute to tail latency in some workloads; measure it rather than assuming spikes."

- **Q31-C3** - `partially_supported`
  - Original claim: "Rust delivers superior raw throughput and predictable tail latency for core proxies, whereas Go accelerates distributed systems tooling where development velocity is prioritized."
  - Sources: S062 (Go Project); S063 (Rust Project, https://doc.rust-lang.org/book/ch04-00-understanding-ownership.html).
  - Evidence: Rust's design favours predictable latency and zero-cost abstractions, while Go favours development velocity; raw throughput is benchmark-dependent.
  - Gap: The comparative performance claim is too broad to verify from language documentation alone.
  - Suggested correction: Reword as a design-trade-off statement and cite benchmarks for specific proxies.

### Q32 - WebAssembly runtimes: Wasmtime vs WasmEdge

Question status: `partially_supported`

- **Q32-C1** - `partially_supported`
  - Original claim: "WebAssembly runtimes offer memory-safe, capability-based sandboxing with near-instant (<1ms) cold starts, bypassing Linux container startup overhead."
  - Sources: S064 (Bytecode Alliance, https://docs.wasmtime.dev/); S065 (WasmEdge/CNCF, https://wasmedge.org/docs/start/wasmedge/extensions/plugins/).
  - Evidence: WebAssembly runtimes provide memory-safe, capability-based sandboxing and very fast startup compared with Linux containers.
  - Gap: The specific "<1ms" threshold is not directly evidenced.
  - Suggested correction: Use "sub-millisecond to low-millisecond startup in many deployments", or cite a benchmark.

### Q33 - Function calling and tool use in modern LLMs

Question status: `partially_supported`

- **Q33-C2** - `partially_supported`
  - Original claim: "Modern runtimes employ constrained decoding (context-free grammars or JSON schema masks) to guarantee syntactic validity of generated tool arguments."
  - Source: S066 (OpenAI, https://platform.openai.com/docs/guides/function-calling).
  - Evidence: Some runtimes use constrained decoding or schema masks, but it is not universal; OpenAI documents structured outputs/JSON schema rather than a single decoding strategy.
  - Gap: The universal quantifier is too strong.
  - Suggested correction: "Many runtimes offer schema-constrained or guided decoding; implementation varies."

### Q38 - High-NA EUV lithography and semiconductor packaging

Question status: `partially_supported`

- **Q38-C2** - `partially_supported`
  - Original claim: "High-NA uses anamorphic optics with different magnifications in X and Y axes (4x and 8x), halving the reticle field size."
  - Source: S075 (ASML, https://www.asml.com/en/products/euv-lithography-systems/twinscan-exe-5000).
  - Evidence: High-NA uses anamorphic optics and a half-size exposure field.
  - Gap: The 4x/8x numbers need a separate ASML/Zeiss technical source.
  - Suggested correction: Keep "anamorphic optics and half-size field"; cite 4x/8x separately or remove the numbers.

- **Q38-C3** - `partially_supported`
  - Original claim: "Advanced 3D packaging technologies (e.g. TSMC CoWoS, Intel Foveros) complement lithography scaling by interconnecting heterogeneous chiplets at sub-micron pitch."
  - Sources: S077 (TSMC, https://3dfabric.tsmc.com/english/dedicatedFoundry/technology/3DFabric.htm); S078 (Intel Foundry, https://www.intel.com/content/www/us/en/foundry/packaging.html).
  - Evidence: CoWoS and Foveros-class packaging integrate heterogeneous chiplets, but the inspected pages do not support a sub-micron interconnect pitch.
  - Gap: "Sub-micron pitch" is unsupported and is inconsistent with current hybrid-bonding pitches of several microns.
  - Suggested correction: Remove "sub-micron pitch" or replace it with the documented pitch for the specific process.

### Q40 - Platform Engineering: Backstage vs Port

Question status: `partially_supported`

- **Q40-C2** - `partially_supported`
  - Original claim: "Port is a SaaS-native, configuration-driven internal developer portal offering flexible data models, webhooks, and pre-built integrations without coding custom plugins."
  - Source: S083 (Port, https://docs.port.io/).
  - Evidence: Port is a SaaS platform with configuration-driven blueprints, integrations, scorecards and self-service actions.
  - Gap: The absolute "without coding custom plugins" is not supported, because Port supports custom plugins.
  - Suggested correction: "Port provides configuration-driven blueprints and many pre-built integrations, reducing the need for custom plugin code."

### Q17 - AI-assisted vulnerability discovery and automated patch generation

Question status: `needs_more_evidence`

- **Q17-C1** - `partially_supported`
  - Original claim: "LLM-guided fuzzing generates grammatically valid seed inputs and edge-case test harnesses targeting hard-to-reach code paths."
  - Source: S036 (Google Project Zero, https://googleprojectzero.blogspot.com/2024/10/from-naptime-to-big-sleep.html).
  - Evidence: Big Sleep demonstrated an LLM agent generating hypotheses and crashing testcases for a real SQLite memory-safety bug.
  - Gap: "LLM-guided fuzzing" is not the same as agentic variant analysis; grammar-aware seed generation was not directly evidenced.
  - Suggested correction: "LLM agents can assist vulnerability discovery by generating hypotheses and targeted testcases; evidence for grammar-aware fuzz-seed generation is separate."

- **Q17-C2** - `needs_more_evidence` (see Section 6)

- **Q17-C3** - `partially_supported`
  - Original claim: "Key challenges include regression avoidance, hallucinated fixes that introduce secondary security flaws, and semantic validation."
  - Source: S036 (Google Project Zero).
  - Evidence: The Big Sleep post describes experimental status and the difficulty of generalising variant analysis, but does not individually evidence regression avoidance or hallucinated fixes.
  - Gap: The claim is plausible but only partly evidenced.
  - Suggested correction: Keep the challenges as a discussion point; attach a specific APR/LLM-security evaluation for each named risk.

---

## 8. Questions fully verified

The following 23 questions have three `verified` claims each:

Q01, Q02, Q03, Q04, Q05, Q06, Q08, Q10, Q11, Q13, Q14, Q16, Q18, Q19, Q21, Q23, Q26, Q28, Q29, Q35, Q36, Q37, Q39.

Their evidence records are in `benchmark_v2.json` under `ground_truth.claims`. Representative examples:

- **Q01** - RAG paper, LangChain RAG docs, FAISS wiki, HNSW paper.
- **Q02** - LoRA and QLoRA papers.
- **Q13** - NIST SP 800-207.
- **Q16** - FIPS 203, FIPS 204, Falcon specification.
- **Q23** - CAS solid-state battery overview and DOE energy-storage safety plan.
- **Q35** - gVisor documentation and Kata Containers README/hypervisor docs.
- **Q36** - RFC 7636 (PKCE).
- **Q37** - Microsoft GraphRAG research post.

---

## 9. Time sensitivity and living documents

No claim was assigned `time_sensitive`, because each claim was judged against the source version inspected on 2026-11-12. Several sources are living documents or fast-moving vendor pages, including:

- LangChain, LangGraph, AutoGen, CrewAI documentation;
- AWS, HashiCorp, Kubernetes, K3s, Cilium, Istio documentation;
- Iceberg, Delta Lake, Hudi, ClickHouse, DuckDB documentation;
- Neon branching documentation;
- ASML, TSMC, Intel Foundry, NVIDIA, IBM and QuEra product pages;
- SLSA v1.0 (now superseded by later SLSA versions).

A future re-verification should re-inspect these pages and update publication/version notes. Claims about specific vendor figures (for example NVIDIA's 5x CPO power-efficiency statement) remain vendor-reported and should not be generalised.

---

## 10. Reproducibility

| Artefact | Role |
|---|---|
| `benchmarks/datasets/benchmark_v1.json` | Immutable source dataset. SHA-256 `4f1cb7f5ca19e1c58e10de714844fd042ffdd70ac4f8321e80046a6cd52573ec`. |
| `benchmarks/build_benchmark_v2.py` | Reproducible builder: source registry, evidence records, claim-status aggregation, v1 hash assertion, exact claim-wording check. |
| `benchmarks/datasets/benchmark_v2.json` | Generated evidence-backed dataset (40 questions, 120 claims, 86 sources). |
| `benchmarks/validate_dataset.py` | Read-only v1/v2 validator (15 checks for v1, 20 for v2). |
| `tests/test_phase3_dataset_verification.py` | 25-test suite covering v1, v2, evidence requirements, and negative fixtures. |
| `benchmarks/datasets/DATASET_VERIFICATION_GUIDE.md` | Schema, status rules, and reviewer workflow. |

Rebuild command:

```powershell
python benchmarks\build_benchmark_v2.py
```

Validation commands:

```powershell
python benchmarks\validate_dataset.py
python benchmarks\validate_dataset.py --dataset benchmarks\datasets\benchmark_v2.json
```

Test command:

```powershell
python tests\test_phase3_dataset_verification.py
```

All three commands currently exit `0`; the validator reports 15/15 checks for v1 and 20/20 checks for v2; the test suite reports 25/25 passing.

---

## 11. Limitations

1. **No benchmark execution.** This report does not show that an AI agent will answer the questions correctly. It only verifies the reference claims.
2. **Source coverage is finite.** Some vendor-specific figures are supported only by vendor documentation; where a numerical detail was absent, the claim was downgraded rather than inferred.
3. **Some sources are living documents.** Exact wording can change after 2026-11-12.
4. **One source per claim is often insufficient for comparative claims.** Multi-source claims (for example Kubernetes vs Nomad) remain partially supported where only part of the comparison was inspected.
5. **The Phase 2 regression suite could not run in this environment** because `sqlalchemy` is not installed. No dependency was installed, per the task constraints.
6. **The three contradicted claims require a decision.** They were not silently rewritten in v2; their original wording is preserved and a correction is proposed.

---

## 12. Final status

### NEEDS ATTENTION

**Reason:** All 40 questions and 120 claims were verified and recorded, and the dataset/validator/tests are structurally sound. However, only 23 of 40 questions are fully verified:

- 3 claims are **contradicted** (Q12-C1, Q15-C3, Q34-C2);
- 1 claim **needs more evidence** (Q17-C2);
- 19 claims are **partially supported** across 13 further questions.

Those 23 unresolved claims remain unverified. The dataset is auditable and honest about its gaps, but it is not yet a fully verified reference answer key. A future stage should either add evidence or adopt the suggested corrections in a v3 dataset.

---

**Verification date:** 2026-11-12  
**Verifier:** OpenCode verification agent (DeepSeek V4.1 Flash Free)  
**Status:** NEEDS ATTENTION
