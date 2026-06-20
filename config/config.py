from dataclasses import dataclass


@dataclass
class ResearchConfig:
    ollama_host: str = "http://localhost:11434"
    llm_model: str = "qwen3:4b"

    search_provider: str = "duckduckgo"
    max_results: int = 3

    jls_extract_var = "postgresql://postgres:postgresql@localhost:5432/ai_research_agent"
    database_url: str = (
        jls_extract_var
    )

    temperature: float = 0.3
