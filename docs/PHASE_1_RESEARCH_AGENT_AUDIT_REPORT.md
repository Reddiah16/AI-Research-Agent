# Phase 1: AI Research Agent — Project Audit & Evaluation Design Report

**Auditor:** Senior AI Engineer & LLM Evaluation Architect  
**Audit Date:** November 2026  
**Project:** AI Research Agent (LangGraph + Ollama Qwen3 + DuckDuckGo + PostgreSQL + Streamlit)  
**Audit Status:** **NEEDS ATTENTION**  

---

## Executive Summary

A comprehensive architectural and empirical audit was conducted on the **AI Research Agent** codebase. The system implements an autonomous research pipeline using LangGraph, local LLM generation via Ollama (`qwen3:4b`), DuckDuckGo web search via `ddgs`, PostgreSQL persistence with SQLAlchemy ORM, and a Streamlit UI with an integrated performance analytics dashboard.

### Baseline Status Verification
The existing baseline records in PostgreSQL were audited and verified against the live database:
- **Total Executions Recorded:** 18 runs (18 `SUCCESS`, 0 `FAILED`, 100% execution success rate).
- **Timed Executions:** 13 runs (5 legacy executions pre-date timing instrumentation and are preserved with `NULL` elapsed times).
- **Execution Latency:**
  - **Mean Latency:** 127.85 seconds across the 13 timed executions.
  - **Min Latency:** 97.53 seconds.
  - **Max Latency:** 222.65 seconds.
  - **P95 Latency (empirical nearest-rank):** 221.72 seconds.
- **Unique Sources Discovered:** 159 distinct URLs across all 18 sessions (mean: 8.83 unique sources/session).
- **Reports Generated & Persisted:** 18 structured reports.

### Primary Audit Findings & Needs Attention Triggers
1. **Research Agent Summarization Bypass:** In `agents/research_agent.py` (lines 71–85), per-query LLM summarization of web snippets was disabled. The agent constructs research summaries solely by concatenating search result titles: `summary = "\n".join([f"- {r['title']}" for r in search_results[:3]])`. Web page contents are never fetched, and snippet bodies are ignored. The editor agent synthesizes reports almost entirely from search result titles and LLM parametric memory.
2. **Editor JSON Token Starvation:** In `agents/editor_agent.py` (lines 211–217), `max_tokens=600` is enforced for JSON generation. For detailed reports, this causes JSON truncation (`Unterminated string at char 2741`), as confirmed in `benchmarks/results/benchmark_run.log`. While a markdown fallback path exists, it increases latency and triggers reasoning-leak filters.
3. **Missing Evaluation & Quality Instrumentation:** While runtime telemetry (status, overall elapsed time, unique source counts) is fully implemented in the database, **zero semantic evaluation instrumentation** exists for relevance, factual correctness, source authority, or report completeness.

---

## 1. Existing System Architecture & Codebase Inspection

### 1.1 Architectural Topology

```text
                                  User Query / Topic
                                          │
                                          ▼
                             ┌────────────────────────┐
                             │    app.py (Streamlit)  │
                             │  or run_benchmark.py   │
                             └───────────┬────────────┘
                                         │
                             ┌───────────▼────────────┐
                             │  LangGraph StateGraph  │
                             │   (GraphState TypedDict)│
                             └───────────┬────────────┘
                                         │
                 ┌───────────────────────┼───────────────────────┐
                 │                       │                       │
                 ▼                       ▼                       ▼
        ┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
        │  Planner Node   │     │  Research Node  │     │   Editor Node   │
        │  (TriageAgent)  │────▶│ (ResearchAgent) ├───▶ │  (EditorAgent)  │
        └────────┬────────┘     └────────┬────────┘     └────────┬────────┘
                 │                       │                       │
                 ▼                       ▼                       ▼
          Ollama qwen3:4b         DuckDuckGo Web          Ollama qwen3:4b
          (Search Queries,        Search (DDGS)          (Structured JSON
            Focus Areas)          (Titles only)             or Markdown)
                                                                 │
                                                                 ▼
                                                        ┌─────────────────┐
                                                        │  Final Report   │
                                                        │ (ResearchReport)│
                                                        └────────┬────────┘
                                                                 │
                                                                 ▼
                                                        ┌─────────────────┐
                                                        │   PostgreSQL    │
                                                        │ (SQLAlchemy ORM)│
                                                        └─────────────────┘
```

### 1.2 Module Directory Structure

```text
RESEARCH_AGENT/
├── agents/
│   ├── editor_agent.py          # Report synthesizer (JSON primary, Markdown fallback)
│   ├── research_agent.py        # Web search orchestrator (DuckDuckGo integration)
│   └── triage_agent.py          # Query generator & research planner
├── benchmarks/
│   ├── results/
│   │   ├── benchmark_20261104_161005.csv  # Baseline benchmark execution record
│   │   └── benchmark_run.log               # Raw execution logs & error traces
│   └── run_benchmark.py         # 20-topic CLI benchmark runner with DB persistence
├── config/
│   └── config.py                # ResearchConfig dataclass (env-driven DATABASE_URL)
├── database/
│   └── database.py              # PostgreSQL/SQLite ORM models, migrations, metrics APIs
├── generation/
│   └── llm.py                   # Ollama API client (text, streaming, JSON mode)
├── graph/
│   └── workflow.py              # LangGraph StateGraph pipeline definition
├── logs/                        # Application run log directory
├── schemas/
│   ├── models.py                # Pydantic models (ResearchPlan, ResearchReport)
│   └── state.py                 # ResearchState TypedDict definition
├── tests/
│   ├── test_analytics_dashboard.py      # Streamlit analytics & chart test suite
│   ├── test_metrics_persistence.py      # DB schema, migration & idempotency tests
│   ├── test_performance_tracking.py     # Runtime timing & session tests
│   └── test_web_search.py               # DuckDuckGo search connectivity tests
├── tools/
│   └── web_search.py            # DuckDuckGo search wrappers (ddgs text/news)
├── ui/
│   ├── analytics.py             # Analytics dashboard (KPIs, Altair charts, history)
│   └── components.py            # Streamlit UI layout, forms, markdown renders
├── .env.example                 # Tracked connection string template
├── app.py                       # Main Streamlit application entry point
└── requirements.txt             # Pinned package dependencies
```

### 1.3 Detailed Component Responsibilities & Implementation Details

| Component | File | Responsibilities | Key Implementation Details |
|---|---|---|---|
| **Entry Point & UI Shell** | `app.py` | UI layout, state management, execution timing, error handling, session save | Calls `time.perf_counter()`, generates `uuid4()` idempotency keys (`run_id`), catches exceptions, invokes `save_session` |
| **Pipeline Workflow** | `graph/workflow.py` | Orchestrates LangGraph linear execution | Compiles `StateGraph(GraphState)` with nodes `planner` → `researcher` → `editor` |
| **Planner Agent** | `agents/triage_agent.py` | Deconstructs topic into 3 search queries & 3 focus areas | Uses `ollama_generate_json` with `temperature=0.0`. Implements hardcoded fallback if JSON parsing fails |
| **Research Agent** | `agents/research_agent.py` | Executes DuckDuckGo web search per query | Calls `web_search()`. **Critical limitation:** concatenates only top-3 result titles; LLM snippet summarization is bypassed |
| **Editor Agent** | `agents/editor_agent.py` | Synthesizes findings into structured research report | Attempts `ollama_generate_json` (`max_tokens=600`). On failure, falls back to plain markdown generation with regex reasoning-strip |
| **Search Engine Tool** | `tools/web_search.py` | Queries DuckDuckGo without API keys | Uses `ddgs.DDGS().text(query, max_results=3)`. Returns title, url, snippet |
| **LLM Generation** | `generation/llm.py` | Ollama client interface | Wraps `ollama.Client`. Implements `check_ollama_connection`, cached clients, text/stream/JSON generation |
| **Database & Analytics** | `database/database.py` | ORM models, migrations, metrics aggregations | Maps `ResearchSession`, `ResearchReportRow`, `ResearchSource`. Idempotent migration via `_migrate_add_columns` |

---

## 2. Evaluation Readiness Assessment

The project currently has operational telemetry but lacks semantic and qualitative evaluation frameworks. The table below assesses the readiness of the seven target metrics:

| Metric | Status | Measurement Capability | Instrumentation Gap |
|---|---|---|---|
| **1. Execution Success Rate** | **Ready** | Fully measurable via PostgreSQL status tracking | Validates structural completion only; does not evaluate report quality |
| **2. Research Relevance** | **Uninstrumented** | Requires semantic comparison of topic to queries, sources, and report | No LLM-as-a-judge or semantic embedding similarity integration |
| **3. Source Quality** | **Partially Ready** | Unique URL count and domain extraction are possible | No automated URL validation, domain authority scoring, or citation grounding |
| **4. Answer Correctness** | **Uninstrumented** | Requires comparison against ground truth claims | No ground truth dataset, claim extraction, or factual verification pipeline |
| **5. Report Completeness** | **Partially Ready** | Outline presence and word count are captured | No semantic rubric to evaluate whether planned focus areas were addressed |
| **6. Average Latency** | **Ready** | Fully captured end-to-end via `execution_time_seconds` | Node-level latency breakdown (planner vs search vs editor) is not persisted |
| **7. P95 Latency** | **Ready (Post-hoc)** | Can be computed from `execution_time_seconds` | No direct SQL helper or dashboard KPI for percentiles (P50, P90, P95) |

### Detailed Metric Definitions & Methodologies

#### Metric 1: Execution Success Rate
- **Definition:** The proportion of executed research tasks that successfully terminate with a fully validated `ResearchReport` object without unhandled pipeline errors.
- **Formula:** $\text{Success Rate} = \frac{N_{\text{SUCCESS}}}{N_{\text{SUCCESS}} + N_{\text{FAILED}}} \times 100\%$
- **Measurement Method:** SQL query `database.get_status_distribution()`, reading `research_sessions.status` normalized via `SUCCESS_STATUSES` (`SUCCESS`, `complete`).
- **Required Data:** `research_sessions.status`, `research_sessions.run_id`.
- **Limitations:** A "SUCCESS" status indicates code completion and JSON/markdown parsing success, but guarantees neither factual accuracy nor prompt fidelity.

#### Metric 2: Research Relevance
- **Definition:** The semantic alignment and topical relevance of generated search queries, retrieved web sources, and final synthesized text relative to the user's research topic.
- **Measurement Method:** Two-tier evaluation:
  1. *Lexical & Embedding Similarity:* Cosine similarity between topic embedding and query/report embeddings.
  2. *LLM-as-a-Judge (Rubric 1–5):* Evaluates topic fidelity, search query precision, and avoidance of off-topic drift.
- **Required Data:** `topic`, `plan.search_queries`, `plan.focus_areas`, `report.title`, `report.report`.
- **Limitations:** Susceptible to LLM judge bias (verbosity bias, model family bias). Local `qwen3:4b` is too constrained to act as its own judge reliably; requires a larger independent evaluator or reference embeddings.

#### Metric 3: Source Quality
- **Definition:** The authority, diversity, reachability, and utilization of sources gathered during research.
- **Measurement Method:**
  1. *Domain Authority Tiering:* Automated classification of URLs (Tier 1: official documentation, academic repositories, reputable technical press; Tier 2: corporate blogs, industry portals; Tier 3: aggregators, content farms).
  2. *Reachability Rate:* HTTP status check (HEAD/GET requests to verify 200 OK vs 404/403).
  3. *Citation Grounding Ratio:* Number of cited URLs in the final report that genuinely originated from the search step versus hallucinated links.
- **Required Data:** `research_sources.url`, `research_sources.title`, `research_reports.sources`, `research_reports.report_content`.
- **Limitations:** DuckDuckGo free tier does not allow filtering by domain tier, and web results change dynamically over time.

#### Metric 4: Answer Correctness (Factual Accuracy)
- **Definition:** The factual accuracy and truthfulness of claims asserted in the generated report compared against verified reference facts.
- **Measurement Method:** Claim-Level Verification:
  1. Deconstruct report into atomic factual claims.
  2. Cross-reference claims against a curated benchmark golden reference dataset using Natural Language Inference (NLI) scoring (`Entailment`, `Neutral`, `Contradiction`).
- **Required Data:** Benchmark ground truth facts/claims, extracted report sentences.
- **Limitations:** Emerging or fast-changing topics (e.g. "Tech in 2026") cannot be verified against static reference datasets without live verification.

#### Metric 5: Report Completeness
- **Definition:** The degree to which the report fulfills structural format requirements and comprehensively covers all planned research focus areas.
- **Measurement Method:**
  1. *Structural Completeness:* Deterministic check for the 6 mandatory sections (`Introduction`, `Key Points`, `Applications`, `Challenges`, `Conclusion`, `References`), word count $\ge 250$, and $\ge 3$ bullet points per section.
  2. *Focus Area Coverage:* Semantic matching determining whether each of the 3 `focus_areas` from the research plan was addressed in the report body.
- **Required Data:** `report.outline`, `report.report`, `report.word_count`, `plan.focus_areas`.
- **Limitations:** Structural checks measure syntactic presence rather than analytical depth.

#### Metric 6: Average Latency
- **Definition:** The arithmetic mean duration in seconds required for the entire research workflow to execute end-to-end.
- **Measurement Method:** Database aggregation: `SELECT AVG(execution_time_seconds) FROM research_sessions WHERE execution_time_seconds IS NOT NULL`.
- **Required Data:** `research_sessions.execution_time_seconds`.
- **Limitations:** Only tracks total elapsed pipeline wall-clock time. Does not differentiate between network latency (DuckDuckGo), local inference latency (Ollama), and DB persistence overhead.

#### Metric 7: P95 Latency
- **Definition:** The 95th percentile of end-to-end execution latency, indicating the latency threshold below which 95% of executions complete.
- **Measurement Method:** PostgreSQL window/aggregate function:
  `SELECT percentile_cont(0.95) WITHIN GROUP (ORDER BY execution_time_seconds) FROM research_sessions WHERE execution_time_seconds IS NOT NULL;`
- **Required Data:** Distribution of `execution_time_seconds`.
- **Limitations:** On small sample sizes (such as the current 13 timed runs), single outlier runs significantly skew the metric.

---

## 3. Benchmark Dataset Design (40 Questions)

To rigorously evaluate the system, a 40-question benchmark dataset is proposed across 6 distinct technical domains, 3 difficulty tiers, and 4 functional task archetypes.

### 3.1 Taxonomy Matrix

- **Domains:**
  1. Artificial Intelligence & LLMs (AI)
  2. Cloud Computing & Distributed Systems (CC)
  3. Cybersecurity & Privacy (CS)
  4. Data Engineering & Databases (DE)
  5. Emerging & Quantum Technologies (ET)
  6. Software Architecture & DevOps (SE)
- **Difficulty Tiers:**
  - **Easy:** Definitional, well-established factual questions; straightforward single-query search.
  - **Intermediate:** Architectural comparisons, implementation trade-offs; requires cross-source synthesis.
  - **Difficult:** Bleeding-edge developments, subtle trade-offs, multi-perspective controversies; requires resolving conflicting data.
- **Task Types:**
  - `Explanation`: Deep-dive technical explanation.
  - `Comparison`: Multi-variable comparison between competing frameworks/technologies.
  - `State of the Art (SOTA)`: Current technological frontier and industry trends.
  - `Multi-Source Synthesis`: Requires corroborating data across diverse ecosystem documentation.

### 3.2 Proposed Benchmark Questions Catalog

| ID | Domain | Difficulty | Task Type | Research Topic / Question | Expected Findings / Core Criteria | Expected Sources |
|---|---|---|---|---|---|---|
| Q01 | AI | Easy | Explanation | Retrieval Augmented Generation architecture and core components | Chunking, vector indexing, dense retrieval, prompt augmentation | LangChain, LlamaIndex, arXiv |
| Q02 | AI | Easy | Explanation | Parameter Efficient Fine Tuning methods: LoRA and QLoRA | Low-rank adaptation matrices, quantization, memory reduction | Hugging Face, Tim Dettmers blog |
| Q03 | AI | Intermediate | Comparison | Speculative Decoding vs Medusa for LLM inference acceleration | Draft models, multiple decoding heads, latency vs VRAM trade-off | Together AI, vLLM docs |
| Q04 | AI | Intermediate | Comparison | vLLM PagedAttention vs Hugging Face TGI memory architectures | KV cache fragmentation, virtual memory paging, continuous batching | vLLM paper, Red Hat TGI docs |
| Q05 | AI | Difficult | Multi-Source | Agentic workflows using LangGraph vs AutoGen vs CrewAI | Cyclic state graphs, actor models, multi-agent communication protocols | Official project docs, GitHub |
| Q06 | AI | Difficult | SOTA | Mixture of Depths and conditional computation in transformer architectures | Dynamic compute allocation, token routing, inference throughput | DeepMind papers, arXiv |
| Q07 | AI | Intermediate | Explanation | Direct Preference Optimization (DPO) vs RLHF with PPO | Reward modeling elimination, closed-form loss, training stability | Stanford paper, Hugging Face |
| Q08 | CC | Easy | Explanation | Serverless computing advantages and cold start latency mitigations | Provisioned concurrency, lightweight microVMs, container snapshotting | AWS Docs, Google Cloud Architecture |
| Q09 | CC | Intermediate | Comparison | Kubernetes vs HashiCorp Nomad for edge computing orchestration | Operational overhead, footprint, declarative scheduling, networking | CNCF reports, HashiCorp docs |
| Q10 | CC | Intermediate | Multi-Source | FinOps best practices for multi-cloud Kubernetes clusters | Spot instances, cluster autoscaling, Karpenter, Kubecost allocation | FinOps Foundation, AWS, CNCF |
| Q11 | CC | Difficult | Comparison | eBPF vs sidecar proxies for service mesh observability and networking | Cilium, Istio ambient mesh, kernel bypass, security isolation | Cilium docs, Isovalent, CNCF |
| Q12 | CC | Difficult | SOTA | Confidential computing hardware progress in public clouds: AMD SEV-SNP vs Intel TDX | Hardware-encrypted enclaves, remote attestation, performance penalties | Azure Security, GCP Security, AMD/Intel |
| Q13 | CS | Easy | Explanation | Zero Trust Architecture core principles and NIST SP 800-207 | Continuous verification, least privilege, assumed breach, microsegmentation | NIST, CISA publications |
| Q14 | CS | Intermediate | Comparison | Passkeys and WebAuthn vs Traditional Multi-Factor Authentication | FIDO2, public-key cryptography, phishing resistance, credential sync | FIDO Alliance, Yubico |
| Q15 | CS | Intermediate | Multi-Source | Software Supply Chain Security: SBOMs, Sigstore, and SLSA framework | Provenance attestation, cosign, in-toto, dependency vulnerability scanning | OpenSSF, SLSA dev |
| Q16 | CS | Difficult | Comparison | Post-quantum cryptography standards: ML-KEM vs Dilithium vs Falcon | Lattice-based cryptography, NIST standardization, key size trade-offs | NIST PQC project, Cloudflare Research |
| Q17 | CS | Difficult | SOTA | AI-assisted vulnerability discovery and automated patch generation | LLM-based fuzzing, symbolic execution, DARPA AI Cyber Challenge | DARPA, Google Project Zero |
| Q18 | DE | Easy | Explanation | Apache Iceberg table format architecture and metadata layers | Metadata files, manifest lists, manifest files, snapshot isolation | Apache Iceberg docs, Tabular |
| Q19 | DE | Intermediate | Comparison | Delta Lake vs Apache Iceberg vs Apache Hudi feature comparison | ACID transactions, time travel, schema evolution, catalog interoperability | Databricks, OneTable/Apache XTable |
| Q20 | DE | Intermediate | Comparison | ClickHouse vs DuckDB for analytical query processing | Distributed column-store vs in-process OLAP, vectorization, memory limits | ClickHouse docs, DuckDB Foundation |
| Q21 | DE | Difficult | Multi-Source | Real-time streaming lakehouses using Apache Flink and Iceberg | Streaming ingest, upsert semantics, compaction strategies, watermark handling | Flink docs, Apache Iceberg |
| Q22 | DE | Difficult | SOTA | Vector database indexing algorithms: HNSW vs DiskANN vs ScaNN | Graph-based search, product quantization, SSD-optimized indexing | Microsoft Research, Pinecone, Qdrant |
| Q23 | ET | Easy | Explanation | Solid-state battery technology advantages over lithium-ion cells | Solid electrolyte, energy density, dendrite prevention, thermal runaway | Dept of Energy, Nature Materials |
| Q24 | ET | Intermediate | Comparison | Superconducting qubits vs Neutral atom quantum computing | Qubit coherence times, gate fidelity, scalability, laser trapping | IBM Quantum, QuEra Computing |
| Q25 | ET | Intermediate | SOTA | Commercial fusion energy approaches: Tokamak vs Stellarator vs Magnetized Target | Magnetic confinement, plasma stability, net energy gain, high-temperature superconductors | ITER, Commonwealth Fusion, Helion |
| Q26 | ET | Difficult | Multi-Source | Neuromorphic computing hardware architectures: Intel Loihi 2 vs SpiNNaker 2 | Spiking neural networks, event-driven compute, ultra-low power consumption | Intel Labs, Nature Electronics |
| Q27 | ET | Difficult | SOTA | Silicon photonics for inter-chip interconnects in AI compute clusters | Optical transceivers, co-packaged optics, bandwidth density, power reduction | TSMC, Ayar Labs, IEEE Spectrum |
| Q28 | SE | Easy | Explanation | Event-Driven Architecture: Event sourcing vs CQRS patterns | Append-only event store, read/write model separation, eventual consistency | Martin Fowler, Microsoft Architecture |
| Q29 | SE | Intermediate | Comparison | gRPC vs REST vs GraphQL for high-throughput internal microservices | Protocol Buffers, multiplexing, schema-first design, payload overhead | Cloudflare, Uber Engineering blog |
| Q30 | SE | Intermediate | Multi-Source | Database branching and ephemeral environments for developer workflows | Copy-on-write storage, Neon, PlanetScale, schema migration automation | Neon docs, PlanetScale docs |
| Q31 | SE | Difficult | Comparison | Rust vs Go for building high-performance distributed networking engines | Memory safety without GC vs lightweight goroutines, binary size, concurrency | Cloudflare, Discord Engineering |
| Q32 | SE | Difficult | SOTA | WebAssembly (Wasm) in serverless and edge compute runtimes: Wasmtime vs WasmEdge | Component Model, sandbox isolation, sub-millisecond startup, WASI | Bytecode Alliance, CNCF |
| Q33 | AI | Easy | Explanation | Function calling and tool use mechanisms in modern LLMs | JSON schema validation, grammar-constrained decoding, tool dispatch | Ollama docs, OpenAI docs |
| Q34 | AI | Intermediate | SOTA | Open-weights reasoning models: DeepSeek-R1 vs QwQ vs OpenAI o1 architecture | Reinforcement learning with verifiable rewards, chain-of-thought scaling | DeepSeek, Qwen team blogs |
| Q35 | CC | Intermediate | Multi-Source | Multi-tenant isolation strategies in containerized environments | gVisor, Kata Containers, Linux namespaces, seccomp profiles | Google Open Source, OpenStack |
| Q36 | CS | Easy | Explanation | OAuth 2.0 Authorization Code flow with PKCE | Proof Key for Code Exchange, public clients, auth grant interception prevention | IETF RFC 7636, Auth0 |
| Q37 | DE | Intermediate | SOTA | Graph RAG architectures combining knowledge graphs with vector databases | Entity extraction, graph traversal, multi-hop reasoning, community detection | Microsoft Research GraphRAG |
| Q38 | ET | Difficult | SOTA | High-NA EUV lithography deployment and semiconductor packaging | Numerical aperture 0.55, anamorphic lenses, chiplet 3D stacking | ASML, TSMC, AnandTech |
| Q39 | SE | Easy | Explanation | Distributed tracing with OpenTelemetry: traces, spans, and context propagation | W3C TraceContext, collector architecture, sampling strategies | OpenTelemetry docs, CNCF |
| Q40 | SE | Intermediate | Comparison | Platform Engineering: Backstage vs Port for developer self-service portals | Software catalog, plugin ecosystem, workflow automation, RBAC | CNCF, Spotify Backstage |

### 3.3 Preparation Methodology for Reference Data & Evaluation Rubrics

To execute this benchmark objectively without human evaluator fatigue, each question must be paired with:
1. **Ground Truth Fact Sheet:** 3–5 atomic verifiable propositions that any accurate report must contain.
2. **Domain Whitelist / Reference Sources:** Curated lists of credible primary domains (e.g. `github.com`, `arxiv.org`, `docs.kernel.org`, `cncf.io`).
3. **Structured Evaluation Rubric:** A 1-to-5 scoring rubric with explicit anchoring definitions:
   - **Score 5:** Comprehensive, zero factual errors, cites authoritative sources, perfectly covers all focus areas.
   - **Score 3:** Technically accurate overview, minor omissions in challenges or applications, relies on secondary sources.
   - **Score 1:** Factually inaccurate, major hallucinations, off-topic, or missing required sections.

---

## 4. Risks, Systemic Gaps & Mitigation Strategies

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                          Identified Architectural Gaps                       │
├─────────────────────────────────────────────────────────────────────────────┤
│ 1. Snippet Summarization Disabled                                            │
│    └─ Research agent passes only search result titles to editor.             │
│ 2. Editor Max Token Starvation                                              │
│    └─ 600 max_tokens causes JSON truncation in complex topics.               │
│ 3. Missing Node-Level Latency Instrumentation                               │
│    └─ Cannot identify whether planner, search, or editor causes latency.    │
│ 4. Single-Model Self-Evaluation Bias                                         │
│    └─ qwen3:4b cannot reliably evaluate its own factual accuracy.            │
│ 5. DuckDuckGo Rate Limiting Risk                                             │
│    └─ Rapid consecutive benchmark execution triggers empty DDG returns.      │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 4.1 Detailed Gap Analysis

1. **Title-Only Research Processing (Architectural Blind Spot):**
   - *Detail:* In `agents/research_agent.py`, the research summary consists solely of `"- " + r['title']`. The snippet text returned by DuckDuckGo and the actual webpage contents are discarded.
   - *Impact on Quality:* The Editor Agent is forced to write detailed reports based almost exclusively on its internal parametric knowledge, using the search titles merely as thematic prompts.
   - *Mitigation:* Re-enable snippet inclusion in the context provided to the Editor Agent, or extract key sentences from the top snippets.

2. **JSON Output Token Truncation:**
   - *Detail:* `agents/editor_agent.py` sets `max_tokens=600` for JSON generation. A 300-word structured markdown report wrapped in a JSON envelope with title, outline, and source arrays routinely exceeds 600 tokens.
   - *Impact on Latency:* Truncated JSON triggers a JSONDecodeError, forcing the pipeline into the secondary markdown generation path. This doubles local inference time from ~100s to >200s, explaining the outlier latencies observed in the baseline (220.45s and 222.65s).
   - *Mitigation:* Increase `max_tokens` to 1500–2048 for the Editor Agent JSON generation path.

3. **Absence of Intermediate Node Latency:**
   - *Detail:* `app.py` and `run_benchmark.py` only measure total elapsed wall-clock time (`pipeline_start = time.perf_counter()`).
   - *Impact:* Profiling cannot isolate bottlenecks between the Planner (LLM), Researcher (Network I/O), and Editor (LLM).
   - *Mitigation:* Record timestamps in `GraphState` at each node transition (`planner_time`, `research_time`, `editor_time`).

4. **Self-Judge Evaluation Bias:**
   - *Detail:* Attempting to use the local `qwen3:4b` model to grade its own generated outputs creates severe self-preference and blind-spot biases.
   - *Mitigation:* Employ deterministic evaluation (rubric checks, regex, domain matching, NLI) combined with an independent external evaluator model or human ground-truth validation for quality metrics.

5. **DuckDuckGo Rate Limiting:**
   - *Detail:* `ddgs` interacts with DuckDuckGo's public HTML interface. Fast iterative execution of 40 benchmark questions (3 queries each = 120 searches) risks IP throttling or empty responses.
   - *Mitigation:* Maintain exponential backoff and jitter between queries (`INTER_RUN_DELAY_SECONDS >= 2.0`), with automated retry logic in `tools/web_search.py`.

---

## 5. Prioritized Implementation Plan (Phases 2–5)

```text
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│     Phase 2     │────▶│     Phase 3     │────▶│     Phase 4     │────▶│     Phase 5     │
│ Instrumentation │     │ Benchmark Suite │     │ Automated Eval  │     │ Optimization &  │
│  & Telemetry    │     │  & Ground Truth │     │    Pipeline     │     │   Regressions   │
└─────────────────┘     └─────────────────┘     └─────────────────┘     └─────────────────┘
```

### Phase 2: Telemetry & Intermediate Instrumentation (Priority: HIGH)
- **Objective:** Enable fine-grained latency and quality tracking without altering core agent logic.
- **Key Tasks:**
  1. Add intermediate node timestamps to `GraphState` (`planner_duration`, `research_duration`, `editor_duration`, `search_network_duration`).
  2. Add nullable columns to `research_sessions` table (`planner_latency`, `research_latency`, `editor_latency`, `p95_latency_metric`) via idempotent schema migration.
  3. Increase `max_tokens` on Editor Agent JSON generation to prevent fallback latency doubling.
  4. Instrument source grounding checks (tracking which retrieved URLs actually appear in the final report).

### Phase 3: Benchmark Suite & Golden Dataset Assembly (Priority: HIGH)
- **Objective:** Finalize test harness and ground truth reference data.
- **Key Tasks:**
  1. Persist the 40 benchmark questions with ground truth claims into a structured dataset (`benchmarks/datasets/benchmark_v1.json`).
  2. Implement an automated benchmark runner that executes with backoff, tracks node-level timing, and writes comprehensive JSON/CSV run artifacts.
  3. Ensure complete isolation of the original 18-session baseline database records.

### Phase 4: Automated Multi-Metric Evaluation Engine (Priority: MEDIUM)
- **Objective:** Implement scoring pipelines for Relevance, Quality, Correctness, and Completeness.
- **Key Tasks:**
  1. Build deterministic evaluators for Report Completeness (structure, section headings, word counts) and Source Quality (domain classification, URL reachability).
  2. Build claim extraction and NLI verification against the benchmark ground truth for Answer Correctness.
  3. Build an evaluation runner that calculates aggregate KPI summaries and percentile distributions (P50, P90, P95).

### Phase 5: UI Integration & Optimization Feedback Loop (Priority: MEDIUM)
- **Objective:** Expose quality metrics in the Streamlit Analytics dashboard and benchmark comparative runs.
- **Key Tasks:**
  1. Add an Evaluation View tab in Streamlit displaying radar charts of the 7 quality metrics across benchmark iterations.
  2. Add percentile latency indicators (P50, P95) alongside existing average duration KPIs.
  3. Re-enable snippet summarization in `research_agent.py` and benchmark quality/latency trade-offs against the Phase 1 baseline.

---

## 6. Audit Assessment & Phase 2 Recommendations

### Overall Assessment: **NEEDS ATTENTION**

**Justification:**  
The application demonstrates strong architectural discipline, pristine local persistence, clean ORM abstraction, and 100% operational success across baseline runs. However, it **NEEDS ATTENTION** before systematic evaluation because:
1. The research agent currently ignores search snippets and feeds only result titles to the synthesizer.
2. Token truncation in the editor JSON generation forces frequent fallback to markdown, doubling latency for complex topics.
3. Node-level telemetry and semantic evaluation metrics are currently absent.

### Immediate Action Items Required for Phase 2 Approval
1. **Approve Schema & Telemetry Extensions:** Authorize adding node-level duration tracking to `GraphState` and `research_sessions`.
2. **Approve Editor Token Adjustments:** Authorize adjusting `max_tokens` from 600 to 1500 to resolve JSON truncation fallbacks.
3. **Approve Benchmark Dataset Adoption:** Ratify the 40-question taxonomy and ground truth catalog defined in Section 3.

