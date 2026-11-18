"""Ollama local LLM generation for Research Agent.

Reuses patterns from Hybrid Search RAG project — Ollama client with streaming,
adapted for multi-agent research workflows (planning, researching, editing).
"""

import json
import logging
import time
from functools import lru_cache
from typing import Dict, Generator, List, Optional, Tuple

from ollama import Client

from config.config import ResearchConfig
from generation.usage import record_usage
from tools.report_parsing import extract_json_object

logger = logging.getLogger(__name__)


class JSONGenerationError(ValueError):
    """Raised when the model returns content that is not a JSON object.

    ``raw_content`` is preserved so the caller can attempt a conservative
    repair before falling back to markdown.
    """

    def __init__(self, message: str, raw_content: str) -> None:
        super().__init__(message)
        self.raw_content = raw_content


@lru_cache
def get_client_cached(host: str) -> Client:
    return Client(host=host)


def get_client(config: ResearchConfig) -> Client:
    """Create an Ollama client from config."""
    return get_client_cached(config.ollama_host)


def check_ollama_connection(config: ResearchConfig) -> bool:
    """Verify Ollama server is reachable and the model is available.

    Returns:
        True if connection and model are available, False otherwise.
    """
    try:
        client = get_client(config)
        models = client.list()
        available = [m.model for m in models.models]
        if config.llm_model not in available:
            # Try partial match (e.g. "qwen3:8b" matches "qwen3:8b-...")
            base_name = config.llm_model.split(":")[0]
            partial_matches = [m for m in available if base_name in m]
            if not partial_matches:
                logger.warning(
                    f"Model '{config.llm_model}' not found. "
                    f"Available: {available}. Attempting to pull..."
                )
                client.pull(config.llm_model)
        return True
    except Exception as e:
        logger.error(f"Ollama connection error: {e}")
        return False


def ollama_generate(
    prompt: str,
    system_prompt: str,
    config: ResearchConfig,
    temperature: float = 0.7,
    max_tokens: int = 2048,
) -> str:
    """Generate a complete response using Ollama (non-streaming).

    Args:
        prompt: The user prompt / task.
        system_prompt: System instructions for the agent role.
        config: Research configuration.
        temperature: Sampling temperature.
        max_tokens: Maximum tokens to generate.

    Returns:
        The generated text response.
    """
    client = get_client(config)

    start = time.time()
    response = client.chat(
        model=config.llm_model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        think=False,
        options={
            "temperature": temperature,
            "num_predict": max_tokens,
        },
    )
    elapsed = time.time() - start
    record_usage(response, model=config.llm_model)
    logger.info(f"Ollama generation completed in {elapsed:.2f}s")

    return response["message"]["content"]


def ollama_generate_stream(
    prompt: str,
    system_prompt: str,
    config: ResearchConfig,
    chat_history: Optional[List[Tuple[str, str]]] = None,
    temperature: float = 0.7,
    max_tokens: int = 2048,
) -> Generator[str, None, None]:
    """Generate a streaming response using Ollama.

    Reuses the streaming pattern from the Hybrid Search RAG project.

    Args:
        prompt: The user prompt / task.
        system_prompt: System instructions for the agent role.
        config: Research configuration.
        chat_history: Optional list of (user_msg, assistant_msg) tuples.
        temperature: Sampling temperature.
        max_tokens: Maximum tokens to generate.

    Yields:
        Text chunks from the streaming response.
    """
    client = get_client(config)

    messages = [{"role": "system", "content": system_prompt}]

    # Append chat history if provided
    if chat_history:
        for user_msg, assistant_msg in chat_history:
            messages.append({"role": "user", "content": user_msg})
            messages.append({"role": "assistant", "content": assistant_msg})

    messages.append({"role": "user", "content": prompt})

    start = time.time()
    response = client.chat(
        model=config.llm_model,
        messages=messages,
        stream=True,
        think=False,
        options={
            "temperature": temperature,
            "num_predict": max_tokens,
        },
    )
    logger.info(f"Time until generation starts: {time.time() - start:.2f}s")

    for chunk in response:
        if "message" in chunk and "content" in chunk["message"]:
            yield chunk["message"]["content"]


# Applied to every JSON-mode request. ``repeat_penalty`` suppresses the
# degenerate whitespace tails qwen3 can emit after closing the object, and an
# explicit ``num_ctx`` stops prompt + generation from exceeding Ollama's
# implicit default context.
_JSON_GENERATION_OPTIONS = {
    "repeat_penalty": 1.08,
    "num_ctx": 8192,
}


def _chunk_field(chunk, name: str, default=None):
    """Read a field from a streamed chunk that may be a dict or a model object."""
    try:
        value = chunk[name]
    except (KeyError, TypeError, AttributeError, IndexError):
        value = getattr(chunk, name, default)
    return default if value is None else value


def _json_object_end(text: str) -> int:
    """Return the index just past the first complete top-level JSON object.

    Operates on partial (streamed) text: the scan is independent of
    ``json.loads`` so it can run before the payload is complete. Braces and
    brackets inside strings are ignored, and backslash escapes are honoured so
    a quoted ``\\"`` does not terminate the string.

    Returns:
        The exclusive end index of the first complete object, or ``-1`` while
        the object is still open (or no object has started).
    """
    depth = 0
    in_string = False
    escaped = False
    started = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
            started = True
        elif char == "}":
            if not started:
                continue
            depth -= 1
            if depth == 0:
                return index + 1
    return -1


def _consume_json_stream(stream) -> Tuple[str, Dict]:
    """Consume streamed chunks until the first complete JSON object closes.

    Stops reading as soon as a balanced top-level object is available, which
    cancels the remainder of the HTTP stream (and server-side generation)
    instead of waiting for the model to reach ``num_predict`` or EOS.

    Returns:
        ``(content, stats)`` where content is the accumulated text and stats
        carries chunk/early-stop/usage information for logging.
    """
    parts: List[str] = []
    stats: Dict = {"chunks": 0, "early_stop": False}

    try:
        for chunk in stream:
            stats["chunks"] += 1
            message = _chunk_field(chunk, "message", {})
            piece = _chunk_field(message, "content", "")
            if piece:
                parts.append(piece)

            if _chunk_field(chunk, "done", False):
                stats["done_reason"] = _chunk_field(chunk, "done_reason")
                stats["eval_count"] = _chunk_field(chunk, "eval_count")
                stats["prompt_eval_count"] = _chunk_field(chunk, "prompt_eval_count")
                break

            if piece and _json_object_end("".join(parts)) != -1:
                stats["early_stop"] = True
                break
    finally:
        # Closing the iterator releases the HTTP response; otherwise the
        # server keeps decoding into a socket nobody reads.
        close = getattr(stream, "close", None)
        if callable(close):
            close()

    return "".join(parts), stats


def ollama_generate_json(
    prompt: str,
    system_prompt: str,
    config: ResearchConfig,
    temperature: float = 0.0,
    max_tokens: int = 128,
) -> Dict:
    """Generate a JSON response using Ollama with format enforcement.

    Used for structured outputs like ResearchPlan and ResearchReport.

    Generation is streamed and aborted as soon as the first complete top-level
    JSON object closes. This preserves the returned payload while avoiding the
    padded/whitespace tails that would otherwise accumulate until
    ``num_predict``, which dominated Editor latency.

    Args:
        prompt: The user prompt / task.
        system_prompt: System instructions (should ask for JSON output).
        config: Research configuration.
        temperature: Lower temperature for structured output.
        max_tokens: Maximum tokens to generate (safety ceiling, not a target).

    Returns:
        Parsed JSON dict from the response.
    """
    client = get_client(config)

    start = time.time()
    stream = client.chat(
        model=config.llm_model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        format="json",
        think=False,
        stream=True,
        options={
            "temperature": temperature,
            "num_predict": max_tokens,
            **_JSON_GENERATION_OPTIONS,
        },
    )
    content, stats = _consume_json_stream(stream)
    elapsed = time.time() - start
    record_usage(
        prompt_tokens=stats.get("prompt_eval_count"),
        completion_tokens=stats.get("eval_count"),
        model=config.llm_model,
    )
    logger.info(
        "Ollama JSON generation completed in %.2fs "
        "(early_stop=%s, chunks=%s, prompt_eval_count=%s, eval_count=%s, done_reason=%s)",
        elapsed,
        stats.get("early_stop"),
        stats.get("chunks"),
        stats.get("prompt_eval_count"),
        stats.get("eval_count"),
        stats.get("done_reason"),
    )

    try:
        parsed = json.loads(content)
        if isinstance(parsed, dict):
            return parsed
        raise ValueError("JSON response is not an object")
    except (json.JSONDecodeError, ValueError):
        logger.error(f"Failed to parse JSON from Ollama response: {content[:200]}")
        # Accept a fenced/embedded object, but leave truncated-JSON repair to
        # the Editor so it can record the "repaired" generation mode.
        embedded = extract_json_object(content)
        if embedded:
            try:
                parsed = json.loads(embedded)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass
        raise JSONGenerationError("invalid_json_response", content) from None
