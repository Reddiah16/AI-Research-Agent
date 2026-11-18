from typing import Optional

from pydantic import BaseModel, Field


class ResearchPlan(BaseModel):
    topic: str
    search_queries: list[str]
    focus_areas: list[str]


class ResearchReport(BaseModel):
    title: str
    outline: list[str]
    report: str
    sources: list[str]
    word_count: int
    generation_mode: Optional[str] = "json"
    # Structured, source-linked findings (Phase 2 research quality).
    key_findings: list[dict] = Field(default_factory=list)
    # Citation URLs emitted by the model but never retrieved during research.
    unsupported_citations: list[str] = Field(default_factory=list)
    # Optional provenance records for the sources supplied to the Editor.
    source_details: list[dict] = Field(default_factory=list)
