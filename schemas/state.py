from typing_extensions import TypedDict


class ResearchState(TypedDict):
    topic: str
    plan: list[str]
    facts: list[str]
    sources: list[str]
    report: str
    status: str
