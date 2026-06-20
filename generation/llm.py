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

logger = logging.getLogger(__name__)


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


def ollama_generate_json(
    prompt: str,
    system_prompt: str,
    config: ResearchConfig,
    temperature: float = 0.0,
    max_tokens: int = 128,
) -> Dict:
    """Generate a JSON response using Ollama with format enforcement.

    Used for structured outputs like ResearchPlan and ResearchReport.

    Args:
        prompt: The user prompt / task.
        system_prompt: System instructions (should ask for JSON output).
        config: Research configuration.
        temperature: Lower temperature for structured output.
        max_tokens: Maximum tokens to generate.

    Returns:
        Parsed JSON dict from the response.
    """
    client = get_client(config)

    start = time.time()
    response = client.chat(
        model=config.llm_model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        format="json",
        think=False,
        options={
            "temperature": temperature,
            "num_predict": max_tokens,
        },
    )
    elapsed = time.time() - start
    logger.info(f"Ollama JSON generation completed in {elapsed:.2f}s")

    content = response["message"]["content"]

    try:
        return json.loads(content)
    except json.JSONDecodeError:
        logger.error(f"Failed to parse JSON from Ollama response: {content[:200]}")
        # Attempt to extract JSON from markdown code block
        if "```json" in content:
            json_str = content.split("```json")[1].split("```")[0].strip()
            return json.loads(json_str)
        elif "```" in content:
            json_str = content.split("```")[1].split("```")[0].strip()
            return json.loads(json_str)
        raise
