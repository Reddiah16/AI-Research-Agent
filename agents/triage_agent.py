"""Triage Agent — builds a retrieval-oriented research plan.

The planner no longer paraphrases the question three times. It asks the model
for a structured, six-query plan that covers primary academic sources, official
documentation, entity/acronym expansion, comparisons, implementation
resources, and surveys. The model sees **only the user's research question**;
it never receives benchmark labels, expected URLs, or evaluation metadata.

Every model result is validated and normalised. Invalid, truncated, or
low-diversity output falls back to a deterministic, diverse plan so a single
bad generation cannot collapse retrieval depth.
"""

import logging
import re
from typing import Any, Dict, List

from config.config import ResearchConfig
from generation.llm import ollama_generate_json
from schemas.models import ResearchPlan

logger = logging.getLogger(__name__)

TRIAGE_SYSTEM_PROMPT = """
You are a retrieval planner for a technical research agent.

Return ONE JSON object:

{
  "topic": "the research question",
  "search_queries": ["query 1", "query 2", "query 3", "query 4", "query 5", "query 6"],
  "focus_areas": ["focus area 1", "focus area 2", "focus area 3"]
}

QUERY RULES
- Generate six SHORT, SPECIFIC, NON-REDUNDANT search queries.
- Use only the research question supplied by the user. Never invent benchmark
  answers, expected URLs, source labels, or hidden evaluation data.
- Cover six different retrieval intents:
  1. a direct topic query with no filler words;
  2. a primary academic query (papers, preprints, or a known paper title);
  3. a constrained academic query such as "site:arxiv.org <topic>";
  4. an official documentation or specification query;
  5. a comparison/trade-off query when the question compares things, otherwise
     an implementation or repository query;
  6. a survey or review query.
- Expand acronyms and include both the acronym and the expanded form when the
  expansion is unambiguous.
- Include a paper title or author only when it can be reasonably inferred from
  the question itself.
- Do NOT use phrases such as "verified facts", "key facts", "facts about",
  "fact check", or "verification".
- Do NOT repeat the same query with minor wording changes.
- Do NOT include URLs.

FOCUS AREAS
- Return three to five concrete subtopics the final report must cover.

Return JSON only. Do not add commentary.
"""

# Benchmark/answer-style meta language that historically attracted evaluation
# blogs and content farms instead of primary sources.
_META_PHRASES = (
    "verified facts",
    "verifiable facts",
    "key facts",
    "important facts",
    "facts about",
    "fact check",
    "fact-check",
    "verification of",
    "verified information",
)

_QUESTION_PREFIXES = (
    "what are the",
    "what is the",
    "what are",
    "what is",
    "how do",
    "how does",
    "how to",
    "why do",
    "why does",
    "explain the",
    "explain",
    "describe the",
    "describe",
    "compare the",
    "compare",
    "discuss the",
    "discuss",
    "analyze the",
    "analyze",
    "analyse the",
    "analyse",
    "about the",
    "about",
    "regarding",
    "the key",
    "the main",
    "key",
    "main",
)

# Small, generic acronym map used only by the deterministic fallback. It is
# independent of the benchmark dataset and contains no expected answers.
_ACRONYM_EXPANSIONS = {
    "rag": "retrieval augmented generation",
    "dpo": "direct preference optimization",
    "rlhf": "reinforcement learning from human feedback",
    "ppo": "proximal policy optimization",
    "llm": "large language model",
    "llms": "large language models",
    "hnsw": "hierarchical navigable small world",
    "ann": "approximate nearest neighbor",
    "faiss": "facebook ai similarity search",
    "ragas": "retrieval augmented generation assessment",
    "bm25": "best matching 25",
    "lora": "low rank adaptation",
    "qlora": "quantized low rank adaptation",
}

_COMPARISON_PATTERN = re.compile(
    r"\b(?:vs\.?|versus|compare|comparison|differences?|trade-?offs?)\b", re.IGNORECASE
)
_URL_PATTERN = re.compile(r"https?://", re.IGNORECASE)
_ARXIV_URL_PATTERN = re.compile(r"arxiv\.org/(?:abs|pdf|html)/", re.IGNORECASE)
_ARXIV_ID_PATTERN = re.compile(r"\b\d{4}\.\d{4,5}(?:v\d+)?\b")


def _clean_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value).strip()


def _topic_core(topic: str) -> str:
    """Strip question scaffolding and answer-style filler while preserving meaning."""
    text = _clean_text(topic).rstrip("?.!")
    for phrase in _META_PHRASES:
        text = re.sub(re.escape(phrase), " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip(" .,;:-")
    changed = True
    while changed:
        changed = False
        lowered = text.lower()
        for prefix in _QUESTION_PREFIXES:
            if lowered.startswith(prefix + " "):
                text = text[len(prefix):].strip(" ,:")
                changed = True
                break
    return text or _clean_text(topic)


def _expand_acronyms(text: str) -> str:
    expanded = text
    for acronym, expansion in _ACRONYM_EXPANSIONS.items():
        if re.search(rf"\b{re.escape(acronym)}\b", expanded, re.IGNORECASE):
            expanded = re.sub(
                rf"\b{re.escape(acronym)}\b",
                expansion,
                expanded,
                count=1,
                flags=re.IGNORECASE,
            )
    return expanded


def _clean_query(value: Any) -> str:
    """Normalise one generated query and reject unsafe/answer-like output."""
    if not isinstance(value, str):
        return ""
    text = value.strip().strip("\"'`")
    text = re.sub(r"^\s*(?:query\s*\d*\s*[:\-])\s*", "", text, flags=re.IGNORECASE)
    for phrase in _META_PHRASES:
        text = re.sub(re.escape(phrase), " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip(" .,;:-")
    if len(text) < 4 or len(text) > 240:
        return ""
    # Never emit URLs, arXiv links, or arXiv identifiers. This is a hard guard
    # against accidental benchmark-label leakage.
    if _URL_PATTERN.search(text):
        return ""
    if _ARXIV_URL_PATTERN.search(text):
        return ""
    if _ARXIV_ID_PATTERN.search(text):
        return ""
    return text


def _dedupe(values: List[str]) -> List[str]:
    seen: set[str] = set()
    result: List[str] = []
    for value in values:
        key = value.lower()
        if value and key not in seen:
            seen.add(key)
            result.append(value)
    return result


def fallback_queries(topic: str, count: int = 6) -> List[str]:
    """Deterministic diverse plan used when model output is invalid.

    Covers the same six intents as the prompt without consulting any dataset.
    """
    core = _topic_core(topic)
    expanded = _expand_acronyms(core)
    is_comparison = bool(_COMPARISON_PATTERN.search(topic or ""))
    candidates = [
        f"site:arxiv.org {core}",
        f"{core} research paper",
        f"{expanded} official documentation" if expanded != core else f"{core} official documentation",
        f"{expanded} survey review",
        (
            f"{core} trade-offs comparison"
            if is_comparison
            else f"site:github.com {core}"
        ),
        f"{expanded} technical architecture",
        f"{core} benchmark evaluation",
    ]
    queries = _dedupe([query for query in (_clean_query(item) for item in candidates) if query])
    return queries[: max(1, int(count or 6))]


def fallback_focus_areas(topic: str, count: int = 4) -> List[str]:
    core = _topic_core(topic)
    areas = [
        f"Core concepts and definitions of {core}",
        f"Primary research and authoritative sources for {core}",
        f"Trade-offs, limitations, and open problems in {core}",
        f"Implementation and practical use of {core}",
    ]
    return _dedupe(areas)[: max(1, int(count or 4))]


def _validated_queries(raw_queries: Any, topic: str, target: int) -> List[str]:
    queries: List[str] = []
    for raw in raw_queries if isinstance(raw_queries, (list, tuple)) else []:
        query = _clean_query(raw)
        if query:
            queries.append(query)
    queries = _dedupe(queries)

    # If the model produced fewer than three usable queries, treat the plan as
    # invalid and prefer the deterministic plan. Otherwise keep the model's
    # valid queries and top up with fallback intents to reach the target.
    fallback = fallback_queries(topic, target)
    if len(queries) < 3:
        logger.warning(
            "Planner produced %d usable queries; using deterministic fallback.",
            len(queries),
        )
        queries = _dedupe(fallback + queries)
    else:
        queries = _dedupe(queries + fallback)
    return queries[:target]


def _validated_focus_areas(raw_focus: Any, topic: str) -> List[str]:
    focus: List[str] = []
    for raw in raw_focus if isinstance(raw_focus, (list, tuple)) else []:
        area = _clean_query(raw)
        if area:
            focus.append(area)
    focus = _dedupe(focus)
    if len(focus) < 2:
        focus = _dedupe(focus + fallback_focus_areas(topic))
    return focus[:6]


def _build_plan(plan_data: Any, topic: str, config: ResearchConfig) -> ResearchPlan:
    target = max(1, int(getattr(config, "planner_num_queries", 6) or 6))
    payload: Dict[str, Any] = plan_data if isinstance(plan_data, dict) else {}
    queries = _validated_queries(payload.get("search_queries"), topic, target)
    focus = _validated_focus_areas(payload.get("focus_areas"), topic)
    return ResearchPlan(topic=topic, search_queries=queries, focus_areas=focus)


def run_triage_agent(topic: str, config: ResearchConfig) -> ResearchPlan:
    """Create a validated, retrieval-oriented research plan.

    Args:
        topic: The user's research question (the only input used).
        config: Application configuration.

    Returns:
        A validated ResearchPlan with six diverse queries and focus areas.
    """
    max_tokens = int(getattr(config, "planner_max_tokens", 512) or 512)
    try:
        plan_data = ollama_generate_json(
            prompt=f"Research question: {topic}",
            system_prompt=TRIAGE_SYSTEM_PROMPT,
            config=config,
            temperature=0.0,
            max_tokens=max_tokens,
        )
        plan = _build_plan(plan_data, topic, config)
        logger.info(
            "Triage plan created: %d queries, %d focus areas",
            len(plan.search_queries),
            len(plan.focus_areas),
        )
        return plan
    except Exception as exc:
        logger.warning(
            "Structured plan generation failed (%s); using deterministic fallback.",
            exc,
        )
        return _build_plan({}, topic, config)
