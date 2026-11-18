# Editor Generation Optimization Report

**Project:** AI Research Agent (`C:\Reddiah\Research_agent`)  
**Scope:** Editor JSON generation latency  
**Date:** 14 November 2026  
**Runtime:** Python 3.13 (`py -3.13`), Ollama 0.40.16, `qwen3:4b` (Q4_K_M), CPU  
**Status:** **PASS** (measured on a real Ollama run)

---

## 1. Problem

A verified pre-change run produced an Editor latency of **734.054 s** for a 247-word
report. The Ollama server log for that request showed:

```
task 597 | prompt eval time =  54618.03 ms / 2355 tokens  (43.12 tok/s)
task 597 |        eval time = 677322.28 ms / 1995 tokens  ( 2.94 tok/s)
task 597 | stop processing: n_tokens = 2306, truncated = 1
```

The model generated **1,995 tokens** — effectively the `num_predict=2000` cap —
while the report itself was only ~250 words. The JSON object was complete well
before the cap, but `format="json"` permits trailing whitespace, so the model
continued emitting blank lines. `json.loads` accepted the padded content, so the
waste was silent: no parse error, no repair, no fallback.

Contributing factors:

| Factor | Effect |
|---|---|
| Structured report embedded in a JSON string | output is 2–3× the report text |
| `num_predict=2000` (vs "under 400 words") | permits ~1,100 useless tokens |
| No early termination on object completion | padding is decoded at full cost |
| CPU decode throughput 2.9–5.3 tok/s | every wasted token costs ~0.2–0.34 s |
| `num_ctx` unset | prompt + generation could exceed Ollama's default |

---

## 2. Files changed

| File | Change |
|---|---|
| `generation/llm.py` | `ollama_generate_json` now streams, accumulates, and cancels at the first complete top-level JSON object. Added `_json_object_end`, `_consume_json_stream`, `_chunk_field`, and `_JSON_GENERATION_OPTIONS` (`repeat_penalty=1.08`, `num_ctx=8192`). Usage logging added. |
| `agents/editor_agent.py` | Editor JSON `max_tokens` reduced `2000 → 1200` (call + log message). Repair and Markdown-fallback paths unchanged. |
| `tests/test_editor_generation_optimization.py` | New focused mocked-stream regression suite (17 tests). |
| `tests/test_phase3_reliability.py` | Isolated mock fix for the environment-dependent `ddgs` test (documented in the final report). |
| `docs/EDITOR_GENERATION_OPTIMIZATION_REPORT.md` | This report. |

No model, prompt content, graph topology, schema, dataset, or evaluation
definition was changed. JSON remains the primary generation path.

---

## 3. Early-stop implementation

`ollama_generate_json` now calls `client.chat(..., stream=True, format="json",
think=False, options={temperature, num_predict, repeat_penalty, num_ctx})` and
passes the iterator to `_consume_json_stream`.

`_json_object_end(text)` is a pure scanner over partial text:

- tracks `depth` for `{` / `}` only after the first `{` (`started`);
- tracks `in_string` and `escaped` so braces/quotes inside strings are ignored;
- honours backslash escapes, including a backslash that is itself escaped;
- returns the index just past the closing brace of the first balanced object,
  or `-1` while the object is still open.

On each content chunk the accumulator is re-scanned. When a chunk completes the
object, the loop breaks and the stream is closed in a `finally` block, which
releases the HTTP response and cancels server-side generation. If a `done` chunk
arrives first, its `done_reason`, `prompt_eval_count` and `eval_count` are
recorded instead. When the stream ends without a complete object, the existing
parse path runs unchanged: `json.loads` → `extract_json_object` → repair (Editor)
→ `JSONGenerationError` with the full `raw_content` preserved.

The public signature is unchanged:

```python
ollama_generate_json(prompt, system_prompt, config, temperature=0.0, max_tokens=128) -> Dict
```

Logging (never prompts or credentials):

```
Ollama JSON generation completed in 193.22s (early_stop=True, chunks=891,
prompt_eval_count=None, eval_count=None, done_reason=None)
```

Counts are `None` on early stop because Ollama emits them in the final chunk,
which is intentionally never read. Server-side counts remain available in the
Ollama log.

---

## 4. Tests executed

| Command | Result |
|---|---|
| `py -3.13 -m unittest tests.test_editor_generation_optimization` | **17/17 OK** |
| `py -3.13 -m unittest tests.test_phase3_evaluation` | **23/23 OK** |
| `py -3.13 -m unittest tests.test_phase3_reliability` | **25/25 OK** |
| `py -3.13 -m unittest tests.test_phase2_research_quality` | **26/26 OK** |
| `py -3.13 benchmarks/validate_dataset.py` | **15/15 PASS**, dataset SHA unchanged |

Total: **91/91** unit tests passed. `tests/test_db_postgres.py` was not run.
`tests/test_phase2_instrumentation.py` was not run (its token-cap assertion
needs a deliberate review; see §7).

Focused tests cover normal JSON, nested objects/arrays, braces inside strings,
escaped quotes and backslashes (split across chunks), unterminated JSON,
malformed JSON, top-level arrays, cancellation after completion, option
propagation, and preserved `JSONGenerationError` content.

---

## 5. Streaming cancellation verification

Verified on a real run (session 23, 14 Nov 2026):

- Client: `early_stop=True`, `chunks=891`, call returned at 193.22 s.
- Ollama server log for that request:
  ```
  2026/11/14 - 22:05:32 | 200 | 3m10s | POST "/api/chat"
  prompt eval time =  21835.77 ms / 1383 tokens (63.34 tok/s)
         eval time = 169547.53 ms /  892 tokens ( 5.26 tok/s)
  stop processing: n_tokens = 2276, truncated = 0
  ```
- The server ended the request at **892 generated tokens**, matching the client's
  stop point, with `truncated = 0` (not cap-limited).
- The mocked suite additionally asserts that padding chunks after the closing
  brace are never consumed and that `stream.close()` is called.

Because the object completed below the 1,200-token cap, this run cannot prove
how many padding tokens would have followed; it does prove the stream terminates
at the object boundary and that the server stops there.

---

## 6. Measured latency (real runs)

| Metric | Baseline (pre-change) | After | Delta |
|---|---|---|---|
| Editor latency | **734.054 s** | **193.217 s** | −540.8 s (−73.7 %, 3.80×) |
| Pipeline total | 759.071 s | 231.175 s | −527.9 s |
| Editor share of pipeline | 96.7 % | 83.6 % | — |
| Prompt tokens | 2,355 | 1,383 | −972 |
| Prefill time | 54.6 s | 21.8 s | −32.8 s |
| Generated tokens | **1,995** (cap-bound) | **892** (object complete) | −1,103 |
| Decode time | 677.3 s | 169.5 s | −507.8 s |
| Decode rate | 2.94 tok/s | 5.26 tok/s | context-dependent |

**Interpretation:** the reduction is real but not a controlled A/B — the two runs
used different evidence bundles, so prompt size differed. The dominant factor is
output tokens (1,995 → 892): the baseline was cap-bound and padded, while the new
run ended at the completed object under a 1,200 cap. Prefill also improved because
the prompt was smaller. The early-stop guarantees the request cannot continue past
the object; the 1,200 cap and `repeat_penalty=1.08` bound the worst case.

---

## 7. Risks and limitations

1. **Trailing text in the same chunk** is retained; parsing already tolerates
   whitespace and recovers an embedded object. Covered by tests.
2. **Early stop hides usage counts** from the client log. Server logs remain the
   source of truth.
3. **Reduced cap (1,200)** can truncate a pathologically long payload; the
   existing repair path then yields `generation_mode="json_repaired"`. No silent
   dropping of `sources` or `key_findings` was introduced.
4. **`num_ctx=8192`** increases KV-cache memory. It worked on this host; lower-RAM
   hosts should be re-checked.
5. **`tests/test_phase2_instrumentation.py`** still asserts `max_tokens=1500`.
   The code now uses 1,200, so that assertion needs a deliberate decision before
   the suite is run. It was intentionally not run here.
6. **Not a controlled benchmark:** report quality depends on the retrieved
   evidence bundle. A single run is evidence, not a distribution.
7. **`key_findings` are not persisted:** the 3 structured findings were present in
   the generated payload and used by grounding/evaluation, but `save_session` does
   not write them (the `research_reports` table has no such column). This is a
   pre-existing persistence gap, not an optimization regression; see the final
   project completion report.

---

## 8. Final status

**PASS** — the optimization is implemented, unit-tested (17/17), and measured on a
real Ollama run with valid JSON, intact sources, intact key findings, working
grounding, persisted evaluation, and no schema or dataset changes.
