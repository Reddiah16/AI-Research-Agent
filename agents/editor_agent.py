"""Editor Agent — Writes a comprehensive research report from gathered summaries.

Simple function, no LangGraph. Takes research summaries, returns a ResearchReport.
"""

import logging
import re
from typing import Dict, List, Union

from config.config import ResearchConfig
from generation.llm import ollama_generate, ollama_generate_json
from schemas.models import ResearchPlan, ResearchReport

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

# Primary path: structured JSON output — most reliable way to avoid Qwen3
# exposing its chain-of-thought, since format="json" forces structured output.
EDITOR_JSON_SYSTEM_PROMPT = """You are a senior researcher.

Return ONLY a valid JSON object — no other text, no reasoning, no commentary.

The JSON must have exactly these fields:
{
  "title": "A descriptive title for the report (string)",
  "outline": ["Introduction", "Key Points", "Applications", "Challenges", "Conclusion", "References"],
  "report": "The full report in markdown. Follow this EXACT structure:\n\n# Title\n\n## Introduction\nA 2-3 sentence overview paragraph.\n\n## Key Points\n- bullet point\n- bullet point\n- bullet point\n\n## Applications\n- bullet point\n- bullet point\n- bullet point\n\n## Challenges\n- bullet point\n- bullet point\n\n## Conclusion\nA 1-2 sentence closing paragraph.\n\n## References\n1. Source Name – https://url\n2. Source Name – https://url\n\nUnder 400 words total.",
  "sources": ["list of source URLs used"],
  "word_count": 0
}

Rules:
- Key Points, Applications, and Challenges MUST use markdown bullet points (- item).
- References MUST be a numbered list with source name and URL (e.g. '1. IBM – https://ibm.com/...').
- Do NOT write 'We are generating...', 'Steps:', 'Let's draft', 'However, note', 'Based on the findings'.
- Do NOT include any preamble or explanation outside the JSON object.
- The report field must begin with '# ' (the title as a markdown heading).
- Return ONLY the JSON object."""

# Fallback path: plain markdown — used only if JSON parsing fails.
EDITOR_MARKDOWN_SYSTEM_PROMPT = """You are a senior researcher.

Generate ONLY the final markdown report. Never explain your thinking.
Never describe your steps. Never say:
- We are generating...
- Steps:
- Let's draft
- However, note
- Based on the findings
- We can infer
- Here is the report

The VERY FIRST line of your output MUST be:
# <Title of the report>

Then write these sections in EXACTLY this format:

## Introduction
A 2-3 sentence overview paragraph.

## Key Points
- bullet point
- bullet point
- bullet point

## Applications
- bullet point
- bullet point
- bullet point

## Challenges
- bullet point
- bullet point

## Conclusion
A 1-2 sentence closing paragraph.

## References
1. Source Name – https://url
2. Source Name – https://url

Key Points, Applications, and Challenges MUST use bullet points (- item).
References MUST be a numbered list with name and URL.
Keep the report under 400 words. Output ONLY markdown. Nothing else."""

# ---------------------------------------------------------------------------
# Reasoning-leak patterns to strip from markdown fallback output
# ---------------------------------------------------------------------------
_REASONING_PATTERNS = re.compile(
    r"^(we are |steps:|let'?s |however,? note|based on (the )?findings?|"
    r"here is the report|we can infer|i will |i'll |now,? |okay,? )",
    re.IGNORECASE,
)


def _strip_leaked_reasoning(text: str) -> str:
    """Remove lines that look like internal model reasoning/preamble.

    Scans from the top of the text and drops any line that matches known
    Qwen3 chain-of-thought patterns, stopping as soon as a '# ' heading
    is found (which is the intended start of the report).
    """
    lines = text.splitlines()
    clean: List[str] = []
    found_title = False
    for line in lines:
        stripped = line.strip()
        if not found_title:
            if stripped.startswith("# "):
                found_title = True
                clean.append(line)
            elif _REASONING_PATTERNS.match(stripped) or stripped in ("", "---"):
                # skip preamble / reasoning lines before the title
                continue
            else:
                # Non-reasoning, non-title line before title — skip it too
                continue
        else:
            clean.append(line)
    return "\n".join(clean) if clean else text


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_markdown_to_report(markdown_content: str, all_sources: List[str]) -> ResearchReport:
    """Parse raw markdown report into a structured ResearchReport object."""
    title = "Research Report"
    for line in markdown_content.split("\n"):
        cleaned = line.strip()
        if cleaned.startswith("# "):
            title = cleaned[2:].strip()
            title = title.replace("**", "").replace("__", "").replace("*", "").replace("_", "")
            break

    outline = []
    for line in markdown_content.split("\n"):
        cleaned = line.strip()
        if cleaned.startswith("## ") or cleaned.startswith("### "):
            heading = cleaned.lstrip("#").strip()
            heading = heading.replace("**", "").replace("__", "").replace("*", "").replace("_", "")
            if heading and heading not in outline:
                outline.append(heading)

    word_count = len(markdown_content.split())

    return ResearchReport(
        title=title,
        outline=outline,
        report=markdown_content,
        sources=all_sources,
        word_count=word_count,
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_editor_agent(
    plan: ResearchPlan,
    research_summaries: List[Dict],
    config: ResearchConfig,
    on_chunk_callback=None,
) -> Union[ResearchReport, str]:
    """Generate a comprehensive research report from gathered summaries.

    Strategy (in order of preference):
      1. ollama_generate_json  → structured ResearchReport (most reliable)
      2. ollama_generate       → markdown → parse_markdown_to_report
      3. Return raw markdown string as last resort

    Args:
        plan: The original research plan.
        research_summaries: List of dicts with "query" and "summary" keys.
        config: Application configuration.
        on_chunk_callback: Optional callback invoked with the final report text
                           (kept for API compatibility; streaming is not used).

    Returns:
        A ResearchReport object on success, or a raw markdown string as fallback.
    """
    # Build context from research summaries
    research_context = "\n\n---\n\n".join([
        f"**Query**: {s['query']}\n**Findings**:\n{s['summary']}"
        for s in research_summaries
    ])

    # Collect all source URLs
    all_sources = []
    for s in research_summaries:
        for url in s.get("source_urls", []):
            if url not in all_sources:
                all_sources.append(url)

    prompt = (
        f"Topic: {plan.topic}\n\n"
        f"Research findings:\n{research_context}\n\n"
        f"Write the report."
    )

    # ------------------------------------------------------------------
    # Path 1: JSON structured output (primary — avoids Qwen3 reasoning leak)
    # ------------------------------------------------------------------
    try:
        logger.info("Generating report via JSON path...")
        report_data = ollama_generate_json(
            prompt=prompt,
            system_prompt=EDITOR_JSON_SYSTEM_PROMPT,
            config=config,
            temperature=0.2,
            max_tokens=600,
        )

        # Merge collected source URLs into the report sources
        report_sources = report_data.get("sources", [])
        for url in all_sources:
            if url not in report_sources:
                report_sources.append(url)
        report_data["sources"] = report_sources

        # Ensure word_count is populated
        if not report_data.get("word_count"):
            report_data["word_count"] = len(report_data.get("report", "").split())

        report = ResearchReport(**report_data)

        if on_chunk_callback:
            on_chunk_callback(report.report)

        logger.info(f"Report generated (JSON path): '{report.title}' ({report.word_count} words)")
        return report

    except Exception as e_json:
        logger.warning(f"JSON report generation failed: {e_json} — falling back to markdown")

    # ------------------------------------------------------------------
    # Path 2: Plain markdown output (fallback)
    # ------------------------------------------------------------------
    try:
        logger.info("Generating report via markdown fallback...")
        raw_text = ollama_generate(
            prompt=prompt,
            system_prompt=EDITOR_MARKDOWN_SYSTEM_PROMPT,
            config=config,
            temperature=0.2,
            max_tokens=500,
        )

        # Strip any leaked reasoning preamble
        clean_text = _strip_leaked_reasoning(raw_text)

        if not clean_text.strip():
            raise ValueError("Markdown generation returned empty content after stripping")

        if on_chunk_callback:
            on_chunk_callback(clean_text)

        report = parse_markdown_to_report(clean_text, all_sources)
        logger.info(f"Report generated (markdown path): '{report.title}' ({report.word_count} words)")
        return report

    except Exception as e_md:
        logger.error(f"Markdown report generation also failed: {e_md}")

    # ------------------------------------------------------------------
    # Path 3: Last-resort raw string
    # ------------------------------------------------------------------
    logger.error("All report generation paths failed — returning raw fallback string")
    return f"# {plan.topic}\n\nReport generation failed. Please try again."
