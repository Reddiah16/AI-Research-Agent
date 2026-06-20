"""Triage Agent — Plans the research by generating search queries and focus areas.

Simple function, no LangGraph. Takes a topic, returns a ResearchPlan.
"""

import logging
from typing import Dict

from config.config import ResearchConfig
from generation.llm import ollama_generate_json
from schemas.models import ResearchPlan

logger = logging.getLogger(__name__)

TRIAGE_SYSTEM_PROMPT = """
Generate JSON with:

{
 "topic": "topic",
 "search_queries": ["query 1", "query 2", "query 3"],
 "focus_areas": ["focus area 1", "focus area 2", "focus area 3"]
}

Return JSON only.
"""


def run_triage_agent(topic: str, config: ResearchConfig) -> ResearchPlan:
    """Create a research plan for the given topic.

    Args:
        topic: The user's research topic.
        config: Application configuration.

    Returns:
        A validated ResearchPlan with topic, search_queries, and focus_areas.
    """
    try:
        plan_data = ollama_generate_json(
            prompt=f"Topic: {topic}",
            system_prompt=TRIAGE_SYSTEM_PROMPT,
            config=config,
            temperature=0.0,
        )

        plan = ResearchPlan(**plan_data)
        logger.info(
            f"Triage plan created: {len(plan.search_queries)} queries, "
            f"{len(plan.focus_areas)} focus areas"
        )
        return plan

    except Exception as e:
        logger.warning(f"Structured plan parsing failed ({e}), using fallback plan.")
        return ResearchPlan(
            topic=topic,
            search_queries=[
                f"What is {topic}",
                f"Applications of {topic}",
                f"Future of {topic}",
            ],
            focus_areas=[
                f"Introduction to {topic}",
                f"Applications of {topic}",
                f"Future of {topic}",
            ],
        )
