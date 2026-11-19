# Retrieval Diagnostic Report

**Project:** AI Research Agent (repository root)
**Git checkpoint:** `024ec3824f8cce84b52d6b5e2269d6425dabb9be`
**Diagnostic date:** 2026-11-19
**Scope:** G001, G005, G014 (the three cases in `benchmarks/results/golden_eval_live_20261118_091654.json`)
**Mode:** Live pipeline instrumentation only. No mocked results are used in this report.

> **Important scope limit:** three completed cases are not a representative sample. The findings below explain these three cases and identify mechanisms that can affect other cases. They do not establish full-dataset retrieval quality.

---

## 1. Executive summary

The dominant retrieval failure is **not page fetching**. In the controlled rerun, 15 of 18 page fetches succeeded, and every failed fetch fell back to a search snippet without dropping the evidence item. The dominant failure is **what gets searched for, how deep the results are kept, and which sources are preferred**.

Three mechanisms explain almost all of the observed loss:

1. **The planner writes generic topic paraphrases, not retrieval-oriented queries.** It asks for exactly three queries with no paper-title, `site:`, source-authority, or citation intent. For G001 the queries contained benchmark meta-language ("verified facts", "verification"), which surfaced Wikipedia, IBM, and SEO/evaluation blogs instead of the canonical RAG and HNSW papers.
2. **The effective retrieval depth is three results per query, not ten.** `max_results=3`, `max_evidence_items_per_query=3`, and `max_pages_per_query=2` compound. Even when the diagnostic search pool was widened to 10, the one G001 label that appeared was at **rank 9**, so it would still have been discarded.
3. **There is no authority or relevance re-ranking.** Evidence keeps DuckDuckGo rank order, and the registry keeps first-seen query order. The computed lexical `relevance` value is recorded but explicitly does not reorder results (`evaluation/retrieval.py::documents_from_state`). Commercial blogs, LinkedIn posts, and even a Bing ad-redirect URL can occupy the retained slots.

A fourth mechanism is **search-result instability**. Repeating the *same* query within the same diagnostic session produced materially different top-3 lists. G014's labelled DPO paper appeared at **rank 2**, then **rank 7**, then not at all in three consecutive executions. G005's Medusa paper appeared at **rank 1**, then not at all in the next two executions of the same query. A single search call per query is therefore an unreliable retrieval strategy.

G005 succeeded because its question contains two distinctive named systems ("Speculative Decoding", "Medusa"), and the planner produced a query containing those exact entity tokens. In the committed live run, DuckDuckGo returned `https://arxiv.org/abs/2401.10774` at rank 2. That success is real but fragile: only one of the two G005 labels was found, the second was never recovered even by a title query, and the rerun's researcher call did not see the label at all.

### Per-case outcome

| Case | Labels | Best label rank observed | Retained label matches | Why |
|---|---:|---|---:|---|
| G001 | 4 | 9 (deep pool, query 3) | 0 | Generic queries; canonical papers outranked or absent from top 10; depth cap discards rank 9 |
| G005 | 2 | 1 (diagnostic), 2 (live artifact) | 1 | Entity-specific query matched the Medusa paper; second paper never appeared |
| G014 | 1 | 2 (diagnostic), 7 (deep pool) | 0 | Blog-heavy results; provider variance; rank-7 result discarded; mirror page not URL-equal to label |

### Live metrics being diagnosed

From `benchmarks/results/golden_eval_live_20261118_091654.json`:

| Metric | Value |
|---|---:|
| Recall@5 | 0.1667 |
| Precision@5 | 0.0667 |
| NDCG@5 | 0.1290 |
| MRR | 0.1667 |
| Cases completed | 3 (0 errors) |
| Mean pipeline latency | 465.05 s |

Per case: G001 Recall@5 `0.0`; G005 Recall@5 `0.5`, MRR `0.5`; G014 Recall@5 `0.0`. The aggregate is the mean of those three values, which is why one match at rank 2 produces `0.1667`.

---

## 2. Existing retrieval architecture

### 2.1 Execution path

```
run_research_pipeline(topic, config)                       graph/workflow.py
  -> build_research_graph()                                graph/workflow.py
  -> planner_node(state)                                   graph/workflow.py
       -> run_triage_agent(topic, config)                  agents/triage_agent.py
            -> ollama_generate_json(TRIAGE_SYSTEM_PROMPT)  generation/llm.py
  -> research_node(state)                                  graph/workflow.py
       -> run_research_agent(plan, config)                 agents/research_agent.py
            -> web_search(query, max_results)              tools/web_search.py
                 -> ddgs.text(query, max_results=...)      ddgs (external)
            -> normalize_results(...)                      tools/research_evidence.py
            -> build_query_evidence(...)                   tools/research_evidence.py
                 -> fetch_page(url) / FetchCache           tools/page_fetcher.py
                 -> html_to_text(...)                      tools/page_fetcher.py
            -> assemble_summary(...)                       tools/research_evidence.py
       -> build_source_registry(summaries)                 tools/research_evidence.py
  -> editor_node(state)                                    graph/workflow.py
       -> run_editor_agent(plan, facts, config)            agents/editor_agent.py
            -> format_evidence_context(...)                tools/research_evidence.py
            -> ollama_generate_json(...)                   generation/llm.py
            -> normalize_report_payload(...)               tools/report_parsing.py
  -> END
```

### 2.2 Stage responsibilities and limits

| Stage | File / function | Actual behaviour |
|---|---|---|
| Planning | `agents/triage_agent.py::run_triage_agent` | Prompt requests exactly `search_queries` of 3 strings, plus `focus_areas`. Temperature `0.0`. Falls back to `"What is …"`, `"Applications of …"`, `"Future of …"` on any parse failure. |
| Query generation | `TRIAGE_SYSTEM_PROMPT` | No paper-title, `site:`, domain, recency, or source-authority guidance. `focus_areas` are generated but never used by the researcher. |
| Search | `tools/web_search.py::web_search` | `ddgs.text(query, max_results=max_results)`. Retries only on raised exceptions (`search_max_retries=2` → up to 3 attempts). Empty successful responses are not retried. `region` and `time_range` are accepted but never forwarded to `ddgs`. |
| Search result coercion | `tools/web_search.py::_coerce_results` | Accepts any non-empty URL string. No ad/redirect/domain filtering. |
| Normalization | `tools/research_evidence.py::normalize_results` | Validates HTTP(S), cleans title/snippet, deduplicates by `canonical_url` **within one query only**. |
| URL canonicalization | `tools/research_evidence.py::canonical_url` | Lowercases host, forces `https`, strips fragments, default ports, repeated slashes, trailing slash, and tracking params. Does **not** collapse arXiv `abs`/`html`/`pdf`/`vN` variants or DOI/mirror duplicates. |
| Page fetching | `tools/page_fetcher.py::fetch_page` | Standard-library HTTP, 6 s timeout, 500 kB cap, robots.txt enabled, DNS safety enabled. Rejects non-text content types. No JavaScript execution. |
| Content extraction | `tools/page_fetcher.py::html_to_text` / `_TextExtractor` | Strips `script`/`style`/`noscript`/`template`; collects visible text. Full extracted text is returned. `page_fetch_max_chars` exists in config but is never passed to the fetcher or used. |
| Filtering / evidence | `tools/research_evidence.py::build_query_evidence` | Fetches only the first `fetch_limit` normalized results (default 2). Retains at most `max_items` (default 3). Drops items with empty excerpts or lexical relevance `< 0.05`. |
| Snippet selection | `tools/research_evidence.py::extract_relevant_excerpt` | Caps every excerpt at `evidence_excerpt_chars` (default 900). Selects up to 4 query-overlapping sentences for long pages. |
| Cross-query dedup | `tools/research_evidence.py::build_source_registry` | Deduplicates by `canonical_url`, assigns `SRC-###`, preserves first-seen order. |
| Ranking | `evaluation/retrieval.py::documents_from_state` | Uses registry order. The docstring states that `relevance` "does not reorder the list." |
| Retrieval metrics | `evaluation/retrieval.py::evaluate_retrieval_case` | Exact canonical-URL match against labels, over the registry only. |
| Editor context | `tools/research_evidence.py::format_evidence_context` | Includes every retained item (already capped at 3 per query), with source ID, URL, retrieval method, fetch status, and excerpt. |
| Citation sanitization | `tools/report_parsing.py::normalize_report_payload` | Keeps only model-emitted URLs that appear in the retrieved set. |

### 2.3 Effective retrieval funnel

The configured limits create a much shallower funnel than the name "web research" suggests:

| Stage | Configured limit | Maximum for a 3-query plan |
|---|---|---:|
| Queries per run | 3 (prompt-forced) | 3 |
| Search results per query | `max_results=3` | 9 raw results |
| Fetched pages per query | `max_pages_per_query=2` | 6 fetches |
| Retained evidence per query | `max_evidence_items_per_query=3` | 9 items |
| Total fetches per run | `max_total_pages_per_run=12` | not binding here |
| Sources seen by metrics | registry only | ≤ 9 |

Because `max_results` and `max_evidence_items` are both 3, the retention cap never binds beyond the search cap. **Raising `max_results` alone would not increase effective depth**; `max_evidence_items_per_query` must rise at the same time. Likewise, `fetch_limit=2` guarantees that the third retained item per query is always snippet-only.

---

## 3. G001 failure analysis

### 3.1 Question and labels

**Question:** "What are the key verified facts about Retrieval Augmented Generation architecture and core components?"

**Ground-truth URLs:**

1. `https://arxiv.org/abs/2005.11401` — RAG (Lewis et al.)
2. `https://docs.langchain.com/oss/python/langchain/rag` — LangChain RAG documentation
3. `https://github.com/facebookresearch/faiss/wiki` — FAISS wiki
4. `https://arxiv.org/abs/1603.09320` — HNSW paper

### 3.2 Planner output (diagnostic run)

```json
[
  "Retrieval Augmented Generation (RAG) architecture key facts",
  "Core components of RAG system verification",
  "Verified facts on RAG retrieval and generation mechanisms"
]
```

The plan parsed successfully (not the fallback). It contains three paraphrases of the question and two instances of benchmark meta-language ("verification", "verified facts"). It contains no paper title, author, arXiv identifier, `site:arxiv.org`, or documentation-domain intent.

### 3.3 Raw search results (application depth = 3)

| Query | Rank 1 | Rank 2 | Rank 3 | Label hits |
|---|---|---|---|---|
| RAG architecture key facts | Wikipedia | IBM | `arxiv.org/pdf/2507.18910` | 0 |
| Core components of RAG system verification | Pinecone | Analytics Vidhya | DeepLearning.AI | 0 |
| Verified facts on RAG retrieval and generation mechanisms | Wikipedia | `arxiv.org/html/2507.18910v1` | `arxiv.org/html/2506.00054v1` | 0 |

Widening the same queries to 10 results produced a label only once:

| Query | Pool size | Best label | Rank |
|---|---:|---|---:|
| RAG architecture key facts | 10 | — | — |
| Core components of RAG system verification | 10 | — | — |
| Verified facts on RAG retrieval and generation mechanisms | 10 | `arxiv.org/abs/2005.11401` | **9** |

That rank-9 result is outside both `max_results=3` and `max_evidence_items_per_query=3`. Even if search depth alone were raised to 10, the retention cap would still discard it.

### 3.4 Where each label was lost

| Label | Appears in 3-result calls? | Appears in 10-result calls? | Appears in researcher's call? | Diagnostic recovery |
|---|---|---|---|---|
| `arxiv.org/abs/2005.11401` | No | Yes, rank 9 (query 3) | No | Paper-title query → rank 1; `site:arxiv.org` query → rank 1 |
| `docs.langchain.com/oss/python/langchain/rag` | No | No | No | "LangChain RAG documentation" returned `docs.langchain.com/oss/python/deepagents/rag` and the docs home page, not the labelled URL |
| `github.com/facebookresearch/faiss/wiki` | No | No | No | Not separately re-queried; absent from every generated-query pool |
| `arxiv.org/abs/1603.09320` | No | No | No | Exact HNSW title + `site:arxiv.org` → rank 1 |

Two of the four labels were recovered at rank 1 by exact-title or `site:arxiv.org` recovery queries. The FAISS wiki was not separately probed (it was absent from every generated-query pool), and the LangChain documentation URL was not recovered even with a documentation-oriented query.

### 3.5 Fetching and evidence construction

- 3 queries × 2 fetches = 6 fetch attempts, **0 failures**.
- 9 evidence items retained (3 per query), all with non-empty excerpts.
- 6 items used fetched page text; 3 used search snippets because `fetch_limit=2`.
- Retained domains in the rerun: Wikipedia, IBM, `arxiv.org/pdf/2507.18910`, SuperAnnotate, GetMaxim, Algorithmic, KDnuggets, EmergentMind, ReadMedium.
- The committed live artifact (a different execution) retained Wikipedia, IBM, DeepLearning.AI, Kalvad, Lucent Innovation, CustomGPT, and LinkedIn instead. The change in domains across executions is itself an example of search-result variance.
- Relevance scores in the rerun: `0.3829`–`0.7857` on the first query, `0.52`–`0.72` on the second, `0.44`–`0.5933` on the third.

Fetching, extraction, and relevance filtering were **not** responsible for the G001 loss: all six fetches succeeded and every retained item passed the relevance floor. No JavaScript-rendering failure was observed in this case. The content was fetched successfully, but it was the wrong content.

### 3.6 Root cause of G001

**Primary:** query formulation. The planner searched for the *topic* ("RAG architecture", "core components", "verified facts") rather than for the *sources* that answer it. DuckDuckGo's ranking for those phrases is dominated by Wikipedia, vendor explainers, course pages, and SEO listicles.

**Secondary:** the effective depth cap of 3. The only appearance of the canonical RAG paper was at rank 9 in a 10-result diagnostic pool.

**Tertiary:** no source-authority preference. Even with the retrieved set, there was no mechanism to prefer `arxiv.org`, official framework docs, or the FAISS repository over vendor blogs.

**Not causal in this case:** page-fetch failure, PDF handling, robots.txt, context-window limits, and lexical relevance filtering. No JavaScript-rendering loss was observed, though the extractor cannot render JavaScript in general.

---

## 4. G005 success analysis

### 4.1 Question and labels

**Question:** "Compare the main approaches or options in Speculative Decoding vs Medusa for LLM inference acceleration. What are the key differences and trade-offs?"

**Ground-truth URLs:**

1. `https://arxiv.org/abs/2211.17192` — speculative decoding
2. `https://arxiv.org/abs/2401.10774` — Medusa

### 4.2 Planner output (diagnostic run)

```json
[
  "speculative decoding vs medusa llm inference acceleration",
  "key differences speculative decoding medusa",
  "trade-offs between speculative decoding and medusa"
]
```

Unlike G001, these queries contain the distinctive entity token **"medusa"** and the technical phrase **"speculative decoding"**. They are close to the paper's own title vocabulary.

### 4.3 Why it succeeded

- In the committed live artifact, `https://arxiv.org/abs/2401.10774` was retrieved at **rank 2** and retained, producing Recall@5 `0.5`, Precision@5 `0.2`, NDCG@5 `0.3869`, MRR `0.5` for this case.
- In the diagnostic session, the same first query returned `https://arxiv.org/abs/2401.10774` at **rank 1** in one call.
- Exact-title, `site:arxiv.org`, and arXiv-ID recovery queries all returned the Medusa paper at **rank 1**, confirming that the source is highly retrievable when the query names the work.

### 4.4 Why the success is fragile

1. **Provider variance.** The researcher's own execution of `"speculative decoding vs medusa llm inference acceleration"` returned Together AI, AussieAI, and the GitHub repository — not the arXiv paper. The next two identical-query executions (deep pool and researcher) also did not contain the label. The rank-1 observation and the researcher's missing label occurred within the same diagnostic session.
2. **Only one of two labels was found.** `https://arxiv.org/abs/2211.17192` never appeared in any 3-result pool or 10-result pool. Even the exact-title recovery query `"Fast Inference from Transformers via Speculative Decoding arxiv"` returned no matching URL.
3. **PDF variant was not usable.** The researcher's second query retrieved `https://arxiv.org/pdf/2401.10774` at rank 1. The fetcher rejected it with `unsupported_content_type:application/pdf`, so only a 242-character snippet was used. The canonical form `pdf/2401.10774` does not equal the label `abs/2401.10774`, so it would not count as a match even if retained.
4. **Variant duplication.** The live artifact retained `abs`, `html`, and `pdf` forms of the same Medusa paper as three separate sources. This consumes three of nine registry slots and dilutes precision.

### 4.5 Downstream citation observation

The live artifact's answer-quality block reports `report.source_count: 0` for G005 and `generation_mode: "json_repaired"`, despite nine retrieved sources and three structured findings. Retrieval succeeded, but the final report exposed no supported citation URLs. This is a **generation/citation** issue, not a retrieval-metric issue; it needs separate investigation.

### 4.6 Root cause of the G005 outcome

**Success cause:** entity-specific query vocabulary matched the Medusa paper, and DuckDuckGo surfaced the arXiv abstract in the live run.

**Fragility cause:** one search call per query, no result-set stabilization, no canonical handling of arXiv variants, and no PDF extraction. The case scored `0.5` recall because only one of the two labelled works was ever found.

---

## 5. G014 failure analysis

### 5.1 Question and label

**Question:** "Explain the technical mechanisms and implementation details behind Direct Preference Optimization (DPO) vs RLHF with PPO."

**Ground-truth URL:**

- `https://arxiv.org/abs/2305.18290` — DPO paper

### 5.2 Planner output (diagnostic run)

```json
[
  "Direct Preference Optimization DPO technical implementation",
  "RLHF with PPO implementation details",
  "Key differences between DPO and RLHF PPO"
]
```

These are reasonable topic queries, but they optimize for **implementation tutorials**, not for the primary paper. None contains `arxiv`, `paper`, a title phrase, or a `site:` constraint.

### 5.3 Search-result instability

The same first query produced three different outcomes in one session:

| Execution | Label rank |
|---|---:|
| Application-depth search (`max_results=3`) | **2** |
| Deep search (`max_results=10`) | **7** |
| Researcher's own search call | absent |

This is the clearest evidence that the pipeline's single search call is not a stable sample of the result space.

### 5.4 Where the label was lost

| Observation | Result |
|---|---|
| App-depth top 3 | `aisecurityandsafety.org`, `arxiv.org/abs/2305.18290` (rank 2), `together.ai` |
| Deep top 10 | label at rank 7, behind DeepWiki, Trentorch, HackerNoon, AIWiki, Opodab, PyImageSearch |
| Researcher's call | `hackernoon.com/.../direct-preference-optimization-your-language-model-is-secretly-a-reward-model`, DeepWiki, Trentorch |
| Recovery: exact paper title | rank 1 |
| Recovery: `site:arxiv.org` DPO/RLHF/PPO | rank 1 |
| Recovery: `arxiv 2305.18290` | rank 1 |

The researcher's first result is a **mirror/repost** whose URL slug contains the DPO paper title. The content may be the same work, but exact-URL evaluation cannot match it to `arxiv.org/abs/2305.18290`. That is a label-scope limitation, not a retrieval bug; it must not be "fixed" by editing labels.

### 5.5 Fetching and evidence

- 6 fetch attempts, **2 failures**: `robots_disallowed` (HackerNoon) and `http_error:429` (DeepWiki).
- Both failures fell back to snippets; no evidence item was dropped.
- 5 of 9 retained excerpts were snippet-only (3 due to `fetch_limit=2`, 2 due to fetch failures).
- Retained domains: HackerNoon, DeepWiki, Trentorch, Hugging Face, arXiv (`2403.17031`, a different paper), ICLR blog, Heinzer, AIAgentsKit, LLMModels.
- `arxiv.org/abs/2403.17031` is the RLHF/PPO paper, not the DPO label. It is relevant to the question but not to the label.

### 5.6 Root cause of G014

**Primary:** query intent. The planner searched for implementation tutorials, so DuckDuckGo returned blogs, framework docs, and mirrors rather than the primary DPO paper.

**Secondary:** search-result variance plus a depth cap of 3. In one call the paper was at rank 2 and would have been retained; in the researcher's call it was absent; in a deeper call it was at rank 7 and would have been discarded.

**Tertiary:** exact-URL label matching. A mirror carrying the paper's title was retrieved but cannot match the arXiv label. This depresses the measured score even when the underlying content is relevant.

**Not causal:** page-fetch failure did not remove the label, because the label was not in the researcher's result set at all. Fetch failures only reduced excerpt depth for other sources.

---

## 6. Identified bottlenecks

### 6.0 Issue summary

Classification: **C** = confirmed by code and/or execution evidence; **S** = suspected (supported but not proven for these cases); **T** = requires additional testing (Section 6.3).

| Issue | Evidence | Root cause | Impact | Priority |
|---|---|---|---|---|
| C1 — Planner emits three generic topic paraphrases | G001 queries contained "verified facts"/"verification"; no query contained `arxiv`, `site:`, or a paper title | `TRIAGE_SYSTEM_PROMPT` requests three free-form queries with no retrieval or source intent | High | P0 |
| C2 — Effective depth is 3 per query | `max_results=3`, `max_pages_per_query=2`, `max_evidence_items_per_query=3`; G001 label only at rank 9 of 10 | Compounded caps; depth and retention must change together | High | P0 |
| C3 — No authority or relevance re-ranking | Evidence kept in DuckDuckGo order; registry in first-seen query order; `relevance` does not reorder | `build_query_evidence` appends; `documents_from_state` preserves order | High | P0 |
| C4 — Search results are unstable and include ads/mirrors | G014 label at ranks 2, 7, absent; G005 label rank 1 then absent; `bing.com/aclick` in live G005 rank 1 | Provider-side ranking variance; no result-set union or stabilization; no redirect filtering | High | P1 |
| C5 — arXiv variants are distinct URLs | `abs`, `html`, `pdf` of 2401.10774 retained as three sources | `canonical_url` does not parse arXiv identifiers | Medium | P1 |
| C6 — Metrics only see retained evidence | `documents_from_state` reads `source_registry`; raw search results are not scored | Evaluation is coupled to evidence retention | Medium | P1 |
| C7 — PDF fetch is rejected | `failed:unsupported_content_type:application/pdf`; 242-char snippet used | `fetch_page` only accepts text content types | Medium | P2 |
| C8 — No date filtering; `time_range` ignored | `_call()` passes only `query` and `max_results` to `ddgs.text` | Parameter never wired; no date metadata consumed | Low for these cases | P2 |
| C9 — Snippet-only evidence | G001 3/9, G005 4/9, G014 5/9 snippet-only; 155–899 chars | `fetch_limit=2` plus fetch failures | Medium | P1 |
| C10 — `focus_areas` unused | Researcher iterates only `plan.search_queries` | No query expansion or diversity path | Medium | P2 |
| S1 — JavaScript pages cannot be read | Standard-library HTML parser only; no JS execution | No rendering layer | Unknown here | P3 |
| S2 — Empty search results are not retried | Retry loop catches exceptions only | Empty list treated as valid success | Medium | P2 |
| S3 — Mirror pages do not match arXiv labels | G014 HackerNoon mirror carries the DPO title but a different URL | Exact-URL labels; canonicalization scope is undefined | Medium (measurement) | P2 |
| S4 — Planner token ceiling may truncate plans | `ollama_generate_json` default `max_tokens=128`; not overridden by triage | Small default ceiling; fallback hides truncation | Low/Medium | P2 |

### 6.1 Confirmed issues

**C1 — Query generation is topic paraphrase, not retrieval strategy.**
The planner asks for three queries and supplies no source, title, domain, or citation intent. G001's second query used the word "verification" and returned RAG-evaluation pages rather than architecture sources. None of the nine generated queries across the three cases included `arxiv`, `site:`, or a paper title.
*Evidence:* `agents/triage_agent.py::TRIAGE_SYSTEM_PROMPT`; diagnostic planner outputs in Sections 3.2, 4.2, 5.2.

**C2 — Effective retrieval depth is three per query.**
`max_results=3` and `max_evidence_items_per_query=3` are redundant caps; `max_pages_per_query=2` allows at most two fetched items per query, so the third retained item is always snippet-only. G001's only label appearance was rank 9.
*Evidence:* `config/config.py`; `agents/research_agent.py::run_research_agent`; `tools/research_evidence.py::build_query_evidence`.

**C3 — No authority or relevance re-ranking.**
Retained evidence is in DuckDuckGo rank order; the registry is in first-seen query order. The lexical `relevance` score is computed but never used to reorder.
*Evidence:* `tools/research_evidence.py::build_query_evidence` appends in iteration order; `evaluation/retrieval.py::documents_from_state` explicitly does not reorder.

**C4 — Search-result instability and low-quality result types.**
Identical queries returned different top-3 sets within minutes (G014: ranks 2, 7, absent; G005: rank 1, absent, absent). Results also included a Bing ad-redirect URL (`bing.com/aclick?...`) and mirror/repost pages.
*Evidence:* diagnostic runs; the live G005 artifact's rank 1 is a `bing.com/aclick` redirect URL; deep G001 query 1 rank 2 is another ad redirect.

**C5 — arXiv variants are not canonicalized.**
`canonical_url("https://arxiv.org/abs/2401.10774")`, `.../html/2401.10774`, and `.../pdf/2401.10774` return three distinct keys. G005 retained all three as separate sources.
*Evidence:* direct execution of `canonical_url`; G005 artifact `retrieved_urls` entries 2–4.

**C6 — Retrieval metrics only see retained evidence.**
`documents_from_state` reads `source_registry`, not raw search results. A label at rank 4–10 in a search pool is normally invisible to Recall/Precision/NDCG/MRR even if it was returned by DuckDuckGo, unless earlier items were filtered and it still fit inside the retention cap.
*Evidence:* `evaluation/retrieval.py::documents_from_state`; `benchmarks/run_golden_evaluation.py` builds `retrieved_urls` from `documents_from_state(state)`.

**C7 — PDF results are fetched and rejected.**
`fetch_page` rejects `application/pdf`; the researcher keeps the snippet. This reduced G005's Medusa PDF to a 242-character snippet.
*Evidence:* diagnostic `fetch_errors=['failed:unsupported_content_type:application/pdf']`; `tools/page_fetcher.py` content-type check.

**C8 — No publication-date filtering; `time_range` is a dead parameter.**
`web_search(..., time_range=...)` accepts the argument but `_call()` invokes `ddgs.text(query, max_results=max_results)` without it. `region` is likewise unused. No date metadata is read or used.
*Evidence:* `tools/web_search.py::web_search._call`; no `time_range` reference in `agents/research_agent.py`.

**C9 — Evidence is snippet-only for at least one-third of retained slots, and more when fetches fail.**
G001: 3/9 snippet-only. G005: 4/9. G014: 5/9. Excerpt lengths ranged from 155 to 899 characters.
*Evidence:* diagnostic `evidence_items` records (`retrieval_method`, `content_chars`, `fetch_status`).

**C10 — `focus_areas` are generated but never used.**
The planner produces focus areas; the researcher iterates only `plan.search_queries`. No query expansion or diversity mechanism exists.
*Evidence:* `agents/triage_agent.py`; `agents/research_agent.py::run_research_agent`.

### 6.2 Suspected issues

**S1 — JavaScript-rendered pages cannot be read.**
The extractor is standard-library HTML only and never executes JavaScript. No fetch failure in these three cases was attributed to missing JavaScript, so its impact here is unproven; it is a structural limitation for JS-heavy documentation sites.

**S2 — Empty search responses are treated as success.**
`_search_with_retries` retries only raised exceptions. A transient empty list is accepted and never retried, which can silently produce zero evidence for a query.
*Evidence:* `tools/web_search.py::_search_with_retries` docstring and loop.

**S3 — Mirror/repost pages are not matched to canonical labels.**
G014 retrieved a HackerNoon page whose slug is the DPO paper title, but the label is the arXiv URL. This is a measurement-scope issue, not a retrieval defect. Any future canonicalization policy must be explicit and must not rewrite labels retroactively.

**S4 — Planner token ceiling may truncate longer plans.**
`ollama_generate_json` defaults to `max_tokens=128`, and `run_triage_agent` does not override it. All three diagnostic plans parsed successfully, so truncation was not observed here; longer or multi-part topics could hit the ceiling and fall back to generic queries.

### 6.3 Issues requiring additional testing

**T1 — Full-dataset prevalence.** The three cases cannot establish how often each mechanism dominates. A full labelled run is required.
**T2 — Whether recency filtering helps or hurts.** Foundational labels (RAG 2020, HNSW 2016) could be removed by aggressive date filters; this needs per-category testing.
**T3 — Whether authority-weighted ranking improves precision without suppressing legitimate blogs.** Needs an offline replay over stored candidate pools.
**T4 — PDF extraction benefit.** Recommended as a fix, but its effect on evidence quality is untested.
**T5 — Alternative search backends/regions.** Provider variance may be reduced by a different backend or by querying multiple backends; untested.

---

## 7. Evidence supporting each finding

### 7.1 Pipeline evidence

- `agents/research_agent.py::run_research_agent` reads `max_results` (3), `max_evidence_items_per_query` (3), `max_pages_per_query` (2), and `max_total_pages_per_run` (12).
- `tools/research_evidence.py::build_query_evidence` contains `if len(items) >= max_items: break` and decrements `fetch_budget` only for the first two normalized results.
- `tools/research_evidence.py::build_query_evidence` computes `relevance_score(...)` and appends the item; it never sorts by that score.
- `tools/research_evidence.py::build_source_registry` assigns `SRC-###` in first-seen order and deduplicates by canonical URL.
- `evaluation/retrieval.py::documents_from_state` documents that registry order "is the retrieval ranking used here" and that `relevance` "does not reorder the list."
- `tools/web_search.py::web_search._call` is:

```python
def _call():
    from ddgs import DDGS
    with DDGS() as ddgs:
        return list(ddgs.text(query, max_results=max_results))
```

`time_range` and `region` are not referenced inside `_call`.

- `tools/page_fetcher.py::fetch_page` rejects any content type outside its text set, which includes HTML but not `application/pdf`.
- `tools/page_fetcher.py::html_to_text` uses `html.parser.HTMLParser`; there is no script execution or headless browser.

### 7.2 Controlled diagnostic evidence

A temporary instrumented script (outside the repository) called the unchanged application functions:

```text
run_triage_agent(question, config)
web_search(query, max_results=3)      # application depth
web_search(query, max_results=10)     # deeper diagnostic pool
run_research_agent(plan, config)      # unchanged application path
build_source_registry(summaries)
evaluate_retrieval_case(case_id, documents_from_state(state), labels)
```

Configuration during the diagnostic run:

```json
{
  "max_results": 3,
  "enable_page_fetch": true,
  "max_pages_per_query": 2,
  "max_evidence_items_per_query": 3,
  "max_total_pages_per_run": 12,
  "evidence_excerpt_chars": 900,
  "min_evidence_relevance": 0.05,
  "page_fetch_timeout": 6.0,
  "respect_robots_txt": true,
  "enforce_dns_safety": true,
  "search_max_retries": 2,
  "llm_model": "qwen3:4b"
}
```

Diagnostic artifacts were written to a temporary directory outside the repository and are not part of it.

### 7.3 Search instability evidence

| Case | Query | Execution 1 | Execution 2 | Execution 3 |
|---|---|---|---|---|
| G014 | `Direct Preference Optimization DPO technical implementation` | label rank 2 | label rank 7 | absent from researcher call |
| G005 | `speculative decoding vs medusa llm inference acceleration` | label rank 1 | absent from deep pool | absent from researcher call |

Both sequences used the same query string, same config, same machine, and same session. This is provider-side variation, not a pipeline bug, but the pipeline has no mitigation (no multi-call union, no caching, no cross-query expansion).

### 7.4 Fetch evidence

| Case | Fetch attempts | Failures | Failure reasons | Snippet-only retained items |
|---|---:|---:|---|---:|
| G001 | 6 | 0 | — | 3 / 9 |
| G005 | 6 | 1 | `unsupported_content_type:application/pdf` | 4 / 9 |
| G014 | 6 | 2 | `robots_disallowed`, `http_error:429` | 5 / 9 |

No fetch failure caused an evidence item to be dropped: `build_query_evidence` falls back to the snippet when a fetch fails. This rules out fetch failure as the primary cause of the three missing labels.

### 7.5 Metric evidence

For the committed live run:

- G001: 7 retained sources, 0 labels → all retrieval metrics `0.0`.
- G005: 9 retained sources, 1 label at rank 2 → Recall@5 `0.5`, Precision@5 `0.2`, NDCG@5 `0.3869`, MRR `0.5`.
- G014: 9 retained sources, 0 labels → all retrieval metrics `0.0`.
- Aggregate Recall@5 `0.1667` = `(0.0 + 0.5 + 0.0) / 3`.

Because metrics are computed over the registry, the denominator is the retained set, not the search candidate set. Precision is therefore sensitive to duplicate arXiv variants and ad/redirect URLs.

---

## 8. Recommended improvements ranked by impact

### 1. Replace the three generic queries with a retrieval-oriented query plan (highest impact)

Generate 6–8 queries per question with explicit intents:

- **Primary-source intent:** `"<paper title> arxiv"` or `"<technique> arxiv paper"`.
- **Constrained intent:** `site:arxiv.org <technique>`; `site:docs.<framework>.com <topic>` for documentation labels.
- **Entity intent:** expand acronyms and full names (`RAG` ↔ `Retrieval-Augmented Generation`; `DPO` ↔ `Direct Preference Optimization`).
- **Comparison intent:** `"<A> vs <B> paper"` rather than `"key differences"`.
- **Implementation intent:** one tutorial/docs query, not three.
- **Survey intent:** `"<topic> survey arxiv"`.

Remove benchmark meta-language such as "verified facts", "verification", and "key facts" from queries. Do **not** generate queries from expected URLs or labels; that would leak the benchmark.

**Expected effect:** the recovery tests show 2 of 4 G001 labels at rank 1 (the LangChain documentation label was not recovered, and the FAISS wiki was not separately probed), 1 of 2 G005 labels at rank 1, and the G014 label at rank 1 using title/`site:`/ID queries. Query intent is the largest controllable lever.

### 2. Deepen the candidate pool and re-rank before retention (high impact)

- Raise `max_results` to 8–10.
- Raise `max_evidence_items_per_query` to 5–6.
- Fetch the top 4 results per query, not 2.
- Score candidates on authority, lexical relevance, and source type; sort before building the registry.
- Keep a separate **raw candidate pool** for diagnostics, even if only the top N are retained.

**Expected effect:** depth alone does not fix the G001 loss (rank 9 sits outside any reasonable cap); the authority-aware re-rank is what promotes a primary source like the rank-9 RAG paper into the retained set. The re-rank also addresses the G014 rank-7 result. Together with a higher fetch limit, it reduces the one-third snippet-only evidence rate. Keeping the raw candidate pool makes the metric reflect what search actually returned, not only what survived the cap.

### 3. Canonicalize scholarly URLs and reject redirect/ad URLs (medium-high impact)

- Normalize `arxiv.org/{abs,html,pdf}/<id>[vN]` to a single work key (`arxiv:<id>`).
- Prefer the `abs` URL as the displayed citation.
- Deduplicate DOI, OpenReview, and publisher variants where the work identity is unambiguous.
- Filter known ad/redirect hosts (`bing.com/aclick`, tracking redirectors) before evidence assembly.

**Expected effect:** G005's three Medusa URLs collapse to one source; precision denominator shrinks; PDF-only results can still match an `abs` label. This is also necessary for stable evaluation.

### 4. Add source-authority and source-type ranking (medium-high impact)

Assign explicit weights to:

- peer-reviewed/preprint repositories (`arxiv.org`, `openreview.net`, `aclanthology.org`, `neurips.cc`, `acm.org`, `springer.com`);
- official framework documentation and repositories (`docs.langchain.com`, `github.com/<org>/<repo>`);
- vendor engineering blogs;
- general blogs, Medium, LinkedIn, content farms, and aggregators.

Downrank listicles and SEO pages. Keep the ranking explainable and log the signals so evaluation can attribute score changes.

**Expected effect:** prevents vendor explainers and LinkedIn posts from displacing primary sources when both are present.

### 5. Harden fetching and content yield (medium impact)

- Extract text from PDFs (for example with `pypdf`) or prefer the arXiv `abs`/`html` URL when available.
- Retry `429` responses with bounded backoff and a longer timeout.
- Treat `robots_disallowed` as expected and keep the snippet path.
- Plumb `time_range` into `ddgs` only where recency is genuinely required; do not apply it globally to foundational topics.
- Use `page_fetch_max_chars` or remove it; it is currently dead configuration.

**Expected effect:** raises excerpt length and quality for PDFs and rate-limited pages, but does not by itself recover missing labels.

### Additional (lower priority)

- Union results from two search calls per query to reduce provider variance.
- Use `focus_areas` for query diversity or remove the field.
- Add a second fetch pass for retained items that were not in the first two results.
- Investigate the Editor's `json_repaired` / `source_count: 0` behaviour separately from retrieval.

---

## 9. Risks and trade-offs

| Change | Risk | Mitigation |
|---|---|---|
| More queries per run | Higher latency and more DuckDuckGo/robots load; current mean run is already 465 s | Cap at 6–8 queries; parallelize search; measure latency separately from quality |
| Deeper result pools and more fetches | More 429/robots failures; higher memory and prompt size | Keep a hard fetch budget; respect robots; fetch only top-ranked candidates |
| Authority weighting | May suppress legitimate independent blogs or regional docs | Use additive boosts, not hard allow-lists; log the ranking rationale |
| arXiv canonicalization | Could merge genuinely different versions or papers if done by string pattern alone | Parse the arXiv identifier and version explicitly; never merge different IDs |
| Date filtering | Removes seminal older papers (RAG 2020, HNSW 2016) | Apply only to explicitly time-sensitive categories; test per category |
| PDF extraction | Adds a dependency and parsing failures | Keep snippet fallback; prefer HTML/abs where available |
| Query rewriting | Risk of benchmark leakage if labels influence queries | Use only the user question and generic source intent; never read expected URLs at runtime |
| Metric changes | Could make historical results incomparable | Version the metric, keep the old registry-based metric, and report both |

**Evaluation-integrity risk:** tuning against these three cases can overfit. The recovery queries in this report are diagnostic probes, not proposed runtime queries for the benchmark; adopting them verbatim would effectively leak the labels.

---

## 10. Proposed implementation plan

### Phase 0 — Freeze the baseline (no code changes)

- Keep `golden_eval_live_20261118_091654.json` unchanged.
- Preserve the diagnostic script and outputs outside the repository.
- Record the three-case baseline and the full-dataset baseline separately.
- Gate: no source, label, result, schema, or database change.

### Phase 1 — Measurement integrity

- Add arXiv-aware canonicalization and redirect/ad filtering.
- Record the raw search candidate pool (top 10) alongside the retained registry in evaluation artifacts.
- Add a diagnostic-only `recall@k` over the candidate pool so search recall and retention loss are separable.
- Gate: existing tests pass; historical metrics remain reproducible under the old metric.

### Phase 2 — Query planner

- Replace free-form three-query generation with a structured plan: primary-source, constrained-site, entity-expansion, comparison, implementation, and survey intents.
- Remove benchmark meta-language from query templates.
- Raise the planner token ceiling above 128 and validate the JSON schema.
- Add a deterministic fallback that still produces diverse queries if the model fails.
- Gate: G001/G005/G014 recovery-style queries are produced from the question alone, without label access; manual review confirms no leakage.

### Phase 3 — Depth and ranking

- Raise search depth and retention caps together.
- Fetch the top 4 per query.
- Add authority/source-type scoring and a deterministic sort before registry construction.
- Deduplicate across queries before spending evidence slots and fetch budget.
- Gate: candidate-pool recall and retained recall both improve; duplicate-source count falls; fetch-failure rate does not increase materially.

### Phase 4 — Fetch and content hardening

- Add PDF text extraction or arXiv `abs`/`html` preference.
- Add bounded `429` retry/backoff.
- Wire or remove `time_range` and `page_fetch_max_chars`.
- Optionally add a headless-rendering path only if evidence shows JS-heavy labels are being lost.
- Gate: snippet-only evidence share falls; no new robots violations.

### Phase 5 — Evaluation and reporting

- Re-run the same three cases, then the full labelled dataset.
- Report old and new metrics side by side; do not rewrite labels or historical artifacts.
- Classify residual misses as search-recall, retention, canonicalization, fetch, or generation.
- Gate: improvements hold beyond the three diagnostic cases before any claim of general improvement.

---

## Appendix A — Diagnostic query inventory

### G001

Generated:

1. `Retrieval Augmented Generation (RAG) architecture key facts`
2. `Core components of RAG system verification`
3. `Verified facts on RAG retrieval and generation mechanisms`

Recovery probes:

1. `Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks arxiv` → label rank 1
2. `site:arxiv.org Retrieval Augmented Generation knowledge intensive NLP` → label rank 1
3. `arxiv 2005.11401` → label rank 7
4. `site:arxiv.org efficient and robust approximate nearest neighbor search hierarchical navigable small world graphs` → label rank 1
5. `LangChain RAG documentation` → no exact label

### G005

Generated:

1. `speculative decoding vs medusa llm inference acceleration`
2. `key differences speculative decoding medusa`
3. `trade-offs between speculative decoding and medusa`

Recovery probes:

1. `Fast Inference from Transformers via Speculative Decoding arxiv` → no label
2. `Medusa Simple LLM Inference Acceleration Framework with Multiple Decoding Heads arxiv` → label rank 1
3. `site:arxiv.org speculative decoding Medusa multiple decoding heads` → label rank 1
4. `arxiv 2401.10774` → label rank 1

### G014

Generated:

1. `Direct Preference Optimization DPO technical implementation`
2. `RLHF with PPO implementation details`
3. `Key differences between DPO and RLHF PPO`

Recovery probes:

1. `Direct Preference Optimization Your Language Model is Secretly a Reward Model arxiv` → label rank 1
2. `site:arxiv.org direct preference optimization DPO RLHF PPO` → label rank 1
3. `arxiv 2305.18290` → label rank 1

---

## Appendix B — What this report does not claim

- It does not claim that G001/G005/G014 represent the full golden dataset.
- It does not claim that any single fix will raise Recall@5 to a specific value.
- It does not claim that JavaScript rendering, date filtering, or context limits caused these three misses; those are structural limitations or untested hypotheses.
- It does not propose changing labels, expected URLs, benchmark artifacts, database schemas, or records.
- It does not treat mocked benchmark results as evidence about live retrieval.

---

## Appendix C — Reproduction notes

- Live diagnostic outputs: `retrieval_diag/diag_retrieval_results.json` (temporary, not committed)
- Console log: `retrieval_diag/diag_run.log` (temporary, not committed)
- Script: `retrieval_diag/diag_retrieval.py` (temporary, not committed)
- These paths are outside the repository and were not committed.
- Search results are live and vary between executions; exact ranks in this report are observations from the stated run, not deterministic constants.
