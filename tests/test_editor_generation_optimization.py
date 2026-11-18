"""Focused regression tests for streamed JSON generation early-stop.

Covers the Editor generation optimisation in ``generation/llm.py``:

- early stop at the first complete top-level JSON object;
- nested objects and arrays;
- braces and brackets inside strings;
- escaped quotes and backslashes (including split across chunks);
- unterminated and malformed payloads still raise ``JSONGenerationError``;
- streaming cancellation (later chunks are never consumed, stream is closed);
- request options keep temperature / JSON format / ``think=False`` and add
  ``repeat_penalty`` and ``num_ctx``.

Standard library only. No network, Ollama, database or pipeline is used.

Run:
    python tests/test_editor_generation_optimization.py
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.config import ResearchConfig
from generation.llm import (
    JSONGenerationError,
    _json_object_end,
    ollama_generate_json,
)


class FakeStream:
    """Iterator that records how many chunks were consumed and closed."""

    def __init__(self, chunks):
        self._chunks = list(chunks)
        self.consumed = 0
        self.closed = False

    def __iter__(self):
        for chunk in self._chunks:
            self.consumed += 1
            yield chunk

    def close(self):
        self.closed = True


class FakeClient:
    """Minimal stand-in for ``ollama.Client`` returning a scripted stream."""

    def __init__(self, chunks):
        self.stream = FakeStream(chunks)
        self.kwargs = None

    def chat(self, **kwargs):
        self.kwargs = kwargs
        return self.stream


def chunk(text: str = "", done: bool = False, **extra) -> dict:
    payload = {
        "message": {"role": "assistant", "content": text},
        "done": done,
    }
    payload.update(extra)
    return payload


def padded_chunks(payload: dict, padding: str = "\n" * 40):
    """Encode a payload, then append a sentinel padding chunk and a done chunk."""
    return [
        chunk(json.dumps(payload)),
        chunk(padding),
        chunk("padding-after-object", done=True, eval_count=999),
    ]


class EarlyStopTests(unittest.TestCase):
    def _run(self, chunks, **kwargs):
        client = FakeClient(chunks)
        options = dict(prompt="topic", system_prompt="system", config=ResearchConfig())
        options.update(kwargs)
        with patch("generation.llm.get_client", return_value=client):
            result = ollama_generate_json(**options)
        return result, client

    def test_normal_object_stops_before_padding(self):
        payload = {"title": "Report", "sources": []}
        result, client = self._run(padded_chunks(payload))
        self.assertEqual(result, payload)
        self.assertEqual(client.stream.consumed, 1)
        self.assertTrue(client.stream.closed)

    def test_nested_objects_and_arrays(self):
        payload = {
            "a": {"b": [1, 2, {"c": "d"}]},
            "e": {"f": []},
            "g": [[{"h": 1}]],
        }
        encoded = json.dumps(payload)
        chunks = [chunk(encoded[i:i + 7]) for i in range(0, len(encoded), 7)]
        chunks.append(chunk("padding", done=True))
        result, client = self._run(chunks)
        self.assertEqual(result, payload)
        self.assertEqual(client.stream.consumed, len(chunks) - 1)
        self.assertTrue(client.stream.closed)

    def test_braces_inside_strings(self):
        payload = {"text": "a { b } c", "nested": "}{", "block": "{not json}"}
        encoded = json.dumps(payload)
        chunks = [chunk(encoded[i:i + 4]) for i in range(0, len(encoded), 4)]
        chunks.append(chunk("padding", done=True))
        result, client = self._run(chunks)
        self.assertEqual(result, payload)
        self.assertEqual(client.stream.consumed, len(chunks) - 1)

    def test_escaped_quotes_and_backslashes(self):
        payload = {"text": 'he said "hi" \\ then left', "path": "C:\\temp\\x"}
        encoded = json.dumps(payload)
        # 1-character chunks guarantee split escape sequences across chunks.
        chunks = [chunk(char) for char in encoded]
        chunks.append(chunk("padding", done=True))
        result, client = self._run(chunks)
        self.assertEqual(result, payload)
        self.assertEqual(client.stream.consumed, len(encoded))

    def test_unterminated_json_raises(self):
        chunks = [chunk('{"a": 1, "b": "unterminated')]
        with self.assertRaises(JSONGenerationError) as ctx:
            self._run(chunks)
        self.assertEqual(ctx.exception.raw_content, '{"a": 1, "b": "unterminated')

    def test_malformed_json_raises(self):
        chunks = [chunk('{"a": }'), chunk("padding", done=True)]
        with self.assertRaises(JSONGenerationError) as ctx:
            self._run(chunks)
        self.assertIn('{"a": }', ctx.exception.raw_content)

    def test_top_level_array_is_not_a_dict(self):
        chunks = [chunk("[1, 2, 3]", done=True)]
        with self.assertRaises(JSONGenerationError) as ctx:
            self._run(chunks)
        self.assertEqual(ctx.exception.raw_content, "[1, 2, 3]")

    def test_done_chunk_records_usage_and_stops(self):
        chunks = [
            chunk('{"a": 1'),
            chunk("}", done=True, done_reason="stop", eval_count=12,
                  prompt_eval_count=34),
        ]
        with patch("generation.llm.logger") as mock_logger:
            result, client = self._run(chunks)
        self.assertEqual(result, {"a": 1})
        self.assertEqual(client.stream.consumed, 2)
        message = mock_logger.info.call_args[0][0]
        rendered = message % mock_logger.info.call_args[0][1:]
        self.assertIn("eval_count=12", rendered)
        self.assertIn("prompt_eval_count=34", rendered)
        self.assertIn("done_reason=stop", rendered)

    def test_request_options_preserved_and_extended(self):
        _, client = self._run(padded_chunks({"a": 1}), temperature=0.2,
                              max_tokens=1200)
        kwargs = client.kwargs
        self.assertEqual(kwargs["format"], "json")
        self.assertIs(kwargs["think"], False)
        self.assertIs(kwargs["stream"], True)
        self.assertEqual(kwargs["model"], ResearchConfig().llm_model)
        self.assertEqual(kwargs["messages"][0]["role"], "system")
        self.assertEqual(kwargs["messages"][1]["role"], "user")
        self.assertEqual(kwargs["options"]["temperature"], 0.2)
        self.assertEqual(kwargs["options"]["num_predict"], 1200)
        self.assertEqual(kwargs["options"]["repeat_penalty"], 1.08)
        self.assertEqual(kwargs["options"]["num_ctx"], 8192)

    def test_default_max_tokens_preserved(self):
        _, client = self._run(padded_chunks({"a": 1}))
        self.assertEqual(client.kwargs["options"]["num_predict"], 128)


class ScannerTests(unittest.TestCase):
    def test_simple_object(self):
        self.assertEqual(_json_object_end('{"a": 1}'), 8)

    def test_trailing_text_after_object(self):
        text = '{"a": 1}\n\n\n{"b": 2}'
        self.assertEqual(_json_object_end(text), 8)

    def test_nested_object(self):
        text = '{"a": {"b": "}"}}'
        self.assertEqual(_json_object_end(text), len(text))

    def test_object_with_escaped_backslash_before_quote(self):
        payload = {"text": "ends with a backslash \\"}
        text = json.dumps(payload)
        self.assertEqual(_json_object_end(text), len(text))

    def test_open_object_returns_minus_one(self):
        self.assertEqual(_json_object_end('{"a": {"b": 1}'), -1)

    def test_array_only_returns_minus_one(self):
        self.assertEqual(_json_object_end("[1, 2, 3]"), -1)

    def test_stray_closing_brace_is_ignored(self):
        self.assertEqual(_json_object_end(' } {"a": 1}'), 11)


if __name__ == "__main__":
    unittest.main(verbosity=2)
