"""Optional LLM-as-a-judge evaluator, kept separate from deterministic metrics.

The judge is disabled by default. It is never presented as ground truth, and it
records its model, prompt version, and availability status. If Ollama or the
configured model is unavailable, it returns ``status="not_available"`` instead
of inventing a score.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

__all__ = [
    "JUDGE_PROMPT_VERSION",
    "JudgeUnavailable",
    "evaluate_with_judge",
]

JUDGE_PROMPT_VERSION = "judge-1.0"

JUDGE_SYSTEM_PROMPT = """You are an evaluation judge, not the report author.
Score the supplied report against the question and evidence.
Return ONLY a JSON object with these numeric fields between 0 and 1:
{"faithfulness": 0.0, "answer_relevance": 0.0, "contextual_precision": 0.0, "contextual_recall": 0.0}
Rules:
- Use only the supplied question, report, and evidence.
- Do not reward confident wording without evidence.
- Do not infer facts that are not present.
- If the report does not address the question, relevance is 0.
"""


class JudgeUnavailable(RuntimeError):
    """Raised when the optional judge cannot be executed."""


def _clamp(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return round(max(0.0, min(1.0, float(value))), 4)


def evaluate_with_judge(
    *,
    question: str,
    report_text: str,
    evidence_text: str,
    config: Any,
    generate_json: Optional[Callable[..., Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Score a report with an optional LLM judge.

    Args:
        generate_json: Injectable callable for testing. When omitted, the
            project's Ollama JSON helper is used lazily.
    """
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    base = {
        "evaluator": "llm_judge",
        "prompt_version": JUDGE_PROMPT_VERSION,
        "model": getattr(config, "llm_model", None),
        "evaluated_at": timestamp,
        "is_ground_truth": False,
    }

    prompt = (
        f"QUESTION:\n{question}\n\n"
        f"REPORT:\n{report_text[:6000]}\n\n"
        f"EVIDENCE:\n{evidence_text[:6000]}\n\n"
        "Return the JSON scores only."
    )

    try:
        if generate_json is None:
            from generation.llm import ollama_generate_json

            generate_json = ollama_generate_json
        payload = generate_json(
            prompt=prompt,
            system_prompt=JUDGE_SYSTEM_PROMPT,
            config=config,
            temperature=0.0,
            max_tokens=200,
        )
    except Exception as exc:
        return {
            **base,
            "status": "not_available",
            "reason": f"{type(exc).__name__}: {exc}",
            "scores": None,
        }

    if not isinstance(payload, dict):
        return {
            **base,
            "status": "invalid_response",
            "reason": "judge response was not a JSON object",
            "scores": None,
        }

    scores = {
        name: _clamp(payload.get(name))
        for name in (
            "faithfulness",
            "answer_relevance",
            "contextual_precision",
            "contextual_recall",
        )
    }
    if all(value is None for value in scores.values()):
        return {
            **base,
            "status": "invalid_response",
            "reason": "judge response contained no usable numeric scores",
            "raw": payload,
            "scores": scores,
        }

    return {
        **base,
        "status": "measured",
        "scores": scores,
        "methodology_note": (
            "LLM-judge scores are model opinions, not ground truth. They are "
            "reported separately from deterministic metrics."
        ),
    }
