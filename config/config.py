import os
from dataclasses import dataclass, field


@dataclass
class ResearchConfig:
    ollama_host: str = "http://localhost:11434"
    llm_model: str = "qwen3:4b"

    search_provider: str = "duckduckgo"
    max_results: int = 3

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
    max_pages_per_query: int = 2
    min_evidence_relevance: float = 0.05
    max_evidence_items_per_query: int = 3

    # --- Phase 3 reliability / fetch safety ---
    # Hard cap on page fetches for the whole run (across all queries).
    max_total_pages_per_run: int = field(
        default_factory=lambda: int(os.environ.get("MAX_TOTAL_PAGES_PER_RUN", "12"))
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

    temperature: float = 0.3
