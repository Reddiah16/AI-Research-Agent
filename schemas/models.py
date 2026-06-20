from pydantic import BaseModel


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
