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

    temperature: float = 0.3
