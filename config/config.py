import os
from dataclasses import dataclass, field


@dataclass
class ResearchConfig:
    ollama_host: str = "http://localhost:11434"
    llm_model: str = "qwen3:4b"

    search_provider: str = "duckduckgo"
    # Search depth per query. The retrieval-optimisation work raised this from
    # 3 to 10 so labelled sources at ranks 4-10 are no longer discarded before
    # ranking. Override with SEARCH_MAX_RESULTS when a cheaper run is needed.
    max_results: int = field(
        default_factory=lambda: int(os.environ.get("SEARCH_MAX_RESULTS", "10"))
    )

    # --- Retrieval-oriented planner ---
    # Target number of diverse queries generated per research question.
    planner_num_queries: int = field(
        default_factory=lambda: int(os.environ.get("PLANNER_NUM_QUERIES", "6"))
    )
    # The triage prompt emits a six-query JSON plan. The previous default of
    # 128 tokens could truncate it before the closing brace.
    planner_max_tokens: int = field(
        default_factory=lambda: int(os.environ.get("PLANNER_MAX_TOKENS", "512"))
    )

    # Database connection string, resolved at instantiation time (after
    # app.py calls load_dotenv()) so it is supplied through the DATABASE_URL
    # environment variable in a git-ignored .env file.
    # No credentials are hardcoded here — see .env.example for the format.
    # An unconfigured value stays empty; database.get_engine() raises an
    # actionable error before any connection is attempted.
    database_url: str = field(
        default_factory=lambda: os.environ.get("DATABASE_URL", "")
    )

    # --- Research quality / evidence processing ---
    # Page fetching is best-effort and bounded. Disable with ENABLE_PAGE_FETCH=false.
    enable_page_fetch: bool = field(
        default_factory=lambda: os.environ.get("ENABLE_PAGE_FETCH", "true").lower()
        not in {"0", "false", "no", "off"}
    )
    page_fetch_timeout: float = 6.0
    page_fetch_max_bytes: int = 500_000
    page_fetch_max_chars: int = 4_000
    evidence_excerpt_chars: int = 900
    # Retrieval depth (evidence retention and fetch budget per query). These
    # are raised together: raising the search cap alone would not increase
    # effective depth because retention is capped separately.
    max_pages_per_query: int = field(
        default_factory=lambda: int(os.environ.get("MAX_PAGES_PER_QUERY", "4"))
    )
    min_evidence_relevance: float = 0.05
    max_evidence_items_per_query: int = field(
        default_factory=lambda: int(
            os.environ.get("MAX_EVIDENCE_ITEMS_PER_QUERY", "5")
        )
    )

    # --- Source ranking ---
    # Deterministic additive ranking of search candidates before evidence
    # retention. Disable to reproduce the pre-optimisation DuckDuckGo order.
    enable_source_ranking: bool = field(
        default_factory=lambda: os.environ.get("ENABLE_SOURCE_RANKING", "true").lower()
        not in {"0", "false", "no", "off"}
    )

    # --- Fetch hardening ---
    # Bounded retry/backoff for HTTP 429 (and optionally other transient codes).
    fetch_429_max_retries: int = field(
        default_factory=lambda: int(os.environ.get("FETCH_429_MAX_RETRIES", "2"))
    )
    fetch_429_backoff: float = field(
        default_factory=lambda: float(os.environ.get("FETCH_429_BACKOFF", "1.0"))
    )

    # --- Phase 3 reliability / fetch safety ---
    # Hard cap on page fetches for the whole run (across all queries).
    # Raised from 12 to 24 so the deeper 6-query x 4-fetch plan can actually
    # spend its per-query budget. It remains a hard safety ceiling and can be
    # lowered with MAX_TOTAL_PAGES_PER_RUN=12 for cheap runs.
    max_total_pages_per_run: int = field(
        default_factory=lambda: int(os.environ.get("MAX_TOTAL_PAGES_PER_RUN", "24"))
    )
    # Honoured only when page fetching is enabled.
    respect_robots_txt: bool = field(
        default_factory=lambda: os.environ.get("RESPECT_ROBOTS_TXT", "true").lower()
        not in {"0", "false", "no", "off"}
    )
    robots_timeout: float = 3.0
    # Resolve hostnames and reject private/loopback answers (DNS rebinding mitigation).
    enforce_dns_safety: bool = field(
        default_factory=lambda: os.environ.get("ENFORCE_DNS_SAFETY", "true").lower()
        not in {"0", "false", "no", "off"}
    )
    # Bounded DuckDuckGo retries (0 = single attempt).
    search_max_retries: int = field(
        default_factory=lambda: int(os.environ.get("SEARCH_MAX_RETRIES", "2"))
    )
    search_retry_backoff: float = 1.0

    # --- Phase 3 evaluation ---
    evaluation_support_threshold: float = field(
        default_factory=lambda: float(os.environ.get("EVALUATION_SUPPORT_THRESHOLD", "0.55"))
    )
    evaluation_record_path: str = field(
        default_factory=lambda: os.environ.get(
            "EVALUATION_RECORD_PATH", "benchmarks/results/evaluation_records.jsonl"
        )
    )

    # --- Phase 3 Editor evidence budget ---
    # The retrieval phase can retain 27-30 sources, but a 4B local model does
    # not benefit from an unbounded evidence block: prompt-evaluation time grows
    # and citations become less precise. These limits are applied by a
    # deterministic selector before generation. They are safe defaults, not
    # claims about the ideal values for every model.
    editor_max_sources: int = field(
        default_factory=lambda: int(os.environ.get("EDITOR_MAX_SOURCES", "12"))
    )
    editor_min_sources: int = field(
        default_factory=lambda: int(os.environ.get("EDITOR_MIN_SOURCES", "3"))
    )
    editor_max_context_chars: int = field(
        default_factory=lambda: int(
            os.environ.get("EDITOR_MAX_CONTEXT_CHARS", "14000")
        )
    )
    editor_excerpt_chars: int = field(
        default_factory=lambda: int(os.environ.get("EDITOR_EXCERPT_CHARS", "700"))
    )
    editor_near_duplicate_threshold: float = field(
        default_factory=lambda: float(
            os.environ.get("EDITOR_NEAR_DUPLICATE_THRESHOLD", "0.85")
        )
    )
    # Output ceilings are safety limits, not word-count targets. The report
    # rubric itself remains "under 400 words".
    editor_max_json_tokens: int = field(
        default_factory=lambda: int(os.environ.get("EDITOR_MAX_JSON_TOKENS", "1000"))
    )
    editor_max_markdown_tokens: int = field(
        default_factory=lambda: int(
            os.environ.get("EDITOR_MAX_MARKDOWN_TOKENS", "700")
        )
    )
    # Evidence-based citation binding: when a model cites a source that does
    # not cover its claim, add another already-retrieved source whose excerpt
    # does. Never invent a source.
    editor_bind_citations: bool = field(
        default_factory=lambda: os.environ.get("EDITOR_BIND_CITATIONS", "true").lower()
        not in {"0", "false", "no", "off"}
    )
    editor_append_limitations: bool = field(
        default_factory=lambda: os.environ.get("EDITOR_APPEND_LIMITATIONS", "true").lower()
        not in {"0", "false", "no", "off"}
    )

    # Request-level safety ceiling for a single Ollama call. The measured
    # Editor stage can exceed 20 minutes on CPU-only hardware, so this is a
    # runaway guard rather than an aggressive timeout.
    llm_request_timeout_seconds: float = field(
        default_factory=lambda: float(
            os.environ.get("LLM_REQUEST_TIMEOUT_SECONDS", "1800")
        )
    )

    temperature: float = 0.3
