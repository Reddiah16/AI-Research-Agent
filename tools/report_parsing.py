"""Pure report-parsing and citation-sanitising helpers.

These functions are standard-library only so the Editor's failure handling can
be tested without Ollama or Pydantic. They do not decide factual truth; they
decide whether a generated payload is well-formed and whether its citations
point at sources that were actually retrieved.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

from tools.research_evidence import canonical_url

_FENCE_PATTERN = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.DOTALL)
_WORD_TOKEN_RE = re.compile(r"\S+")


def count_words(text: Any) -> int:
    """Count words in final report prose.

    A word is a whitespace-delimited token containing at least one
    alphanumeric character. This deliberately ignores markdown/JSON structural
    tokens such as ``#``, ``---``, ``|`` and ``*`` while counting punctuation
    attached to a word (``world!``) as one word. Empty, whitespace-only, and
    non-string input counts as zero.

    This is a whitespace token count, not a linguistic word segmentation: it
    does not split hyphenated compounds and is not language-aware.
    """
    if not isinstance(text, str) or not text.strip():
        return 0
    return sum(
        1 for token in _WORD_TOKEN_RE.findall(text)
        if any(char.isalnum() for char in token)
    )


def strip_code_fences(text: str) -> str:
    """Remove markdown code fences around JSON, if present."""
    if not isinstance(text, str):
        return ""
    match = _FENCE_PATTERN.search(text)
    if match:
        return match.group(1).strip()
    return text.strip()


def extract_json_object(text: str) -> str:
    """Return the first balanced JSON object found in the text."""
    if not isinstance(text, str):
        return ""
    source = strip_code_fences(text)
    start = source.find("{")
    if start == -1:
        return ""
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(source)):
        char = source[index]
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
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    return source[start:]


def repair_truncated_json(text: str) -> str:
    """Best-effort closure of a truncated JSON object.

    The repair only closes an open string and unclosed containers. It never
    invents fields. Callers must validate the parsed object before use.
    """
    source = extract_json_object(text)
    if not source:
        return ""

    stack: List[str] = []
    in_string = False
    escaped = False
    for char in source:
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
        elif char in "{[":
            stack.append(char)
        elif char in "}]" and stack:
            stack.pop()

    repaired = source.rstrip()
    if repaired.endswith(",") or repaired.endswith(":"):
        repaired = repaired[:-1].rstrip()
    if in_string:
        repaired += '"'
    closers = {"{": "}", "[": "]"}
    repaired += "".join(closers[open_char] for open_char in reversed(stack))
    return repaired


def load_json_payload(raw_text: str) -> Tuple[Dict[str, Any], bool]:
    """Parse a JSON object, retrying with a conservative repair.

    Returns:
        ``(payload, repaired)``. Raises ``ValueError`` when no object can be
        parsed.
    """
    if not isinstance(raw_text, str) or not raw_text.strip():
        raise ValueError("empty_json_response")

    candidates = [raw_text.strip(), extract_json_object(raw_text)]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, dict):
            return parsed, False

    repaired = repair_truncated_json(raw_text)
    if repaired:
        try:
            parsed = json.loads(repaired)
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError(f"invalid_json_after_repair:{exc}") from exc
        if isinstance(parsed, dict):
            return parsed, True

    raise ValueError("no_json_object_found")


def _as_string_list(value: Any) -> List[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    result: List[str] = []
    for item in value:
        if isinstance(item, str):
            cleaned = item.strip()
            if cleaned and cleaned not in result:
                result.append(cleaned)
    return result


def split_citations(
    cited_urls: Sequence[Any], retrieved_urls: Sequence[Any]
) -> Tuple[List[str], List[str]]:
    """Split cited URLs into retrieved (supported) and unretrieved (phantom).

    URL presence is a citation-alignment signal only; it is not evidence that
    the cited page supports a statement.
    """
    retrieved_map: Dict[str, str] = {}
    for url in retrieved_urls or []:
        if not isinstance(url, str):
            continue
        canonical = canonical_url(url)
        if canonical and canonical not in retrieved_map:
            retrieved_map[canonical] = url.strip()

    supported: List[str] = []
    unsupported: List[str] = []
    for url in cited_urls or []:
        if not isinstance(url, str) or not url.strip():
            continue
        canonical = canonical_url(url)
        if canonical and canonical in retrieved_map:
            actual = retrieved_map[canonical]
            if actual not in supported:
                supported.append(actual)
        else:
            cleaned = url.strip()
            if cleaned not in unsupported:
                unsupported.append(cleaned)
    return supported, unsupported


def sanitize_key_findings(
    findings: Any, valid_source_ids: Optional[Sequence[str]] = None
) -> List[Dict[str, Any]]:
    """Normalize finding records and flag unsupported citation references."""
    if not isinstance(findings, list):
        return []
    valid_ids = set(valid_source_ids or [])
    cleaned: List[Dict[str, Any]] = []
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        claim = finding.get("claim") or finding.get("text") or finding.get("finding")
        if not isinstance(claim, str) or not claim.strip():
            continue
        source_ids = _as_string_list(finding.get("source_ids", []))
        if valid_ids:
            supported_ids = [sid for sid in source_ids if sid in valid_ids]
        else:
            supported_ids = source_ids
        cleaned.append(
            {
                "claim": claim.strip(),
                "source_ids": supported_ids,
                "support": finding.get("support", "") if isinstance(
                    finding.get("support", ""), str
                ) else "",
                "citation_supported": bool(supported_ids),
                "unsupported_source_ids": [
                    sid for sid in source_ids if sid not in supported_ids
                ],
            }
        )
    return cleaned


def normalize_report_payload(
    payload: Dict[str, Any],
    retrieved_urls: Sequence[Any],
    valid_source_ids: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Validate and sanitize a generated report payload.

    Required fields: non-empty ``title`` and ``report``. ``outline``,
    ``sources``, ``word_count`` and ``key_findings`` are normalized.

    Raises:
        ValueError: if required content is missing.
    """
    if not isinstance(payload, dict):
        raise ValueError("report_payload_is_not_an_object")

    title = payload.get("title")
    report = payload.get("report")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("missing_report_title")
    if not isinstance(report, str) or not report.strip():
        raise ValueError("missing_report_body")

    outline = _as_string_list(payload.get("outline", []))
    cited = _as_string_list(payload.get("sources", []))
    supported, unsupported = split_citations(cited, retrieved_urls)

    # The model's own ``word_count`` is ignored: a plausible-looking positive
    # integer was stored verbatim (398 vs 251 actual words). The final body is
    # the only source of truth.
    word_count = count_words(report)

    findings = sanitize_key_findings(payload.get("key_findings", []), valid_source_ids)

    return {
        "title": title.strip(),
        "outline": outline,
        "report": report.strip(),
        "sources": supported,
        "word_count": word_count,
        "key_findings": findings,
        "unsupported_citations": unsupported,
    }


def parse_markdown_report(
    markdown_content: str, retrieved_urls: Sequence[Any]
) -> Dict[str, Any]:
    """Parse a markdown fallback into report fields and sanitize citations."""
    if not isinstance(markdown_content, str) or not markdown_content.strip():
        raise ValueError("empty_markdown_report")

    title = "Research Report"
    for line in markdown_content.splitlines():
        cleaned = line.strip()
        if cleaned.startswith("# "):
            title = cleaned[2:].strip().replace("**", "").replace("__", "")
            title = title.replace("*", "").replace("_", "")
            break

    outline: List[str] = []
    for line in markdown_content.splitlines():
        cleaned = line.strip()
        if cleaned.startswith("## ") or cleaned.startswith("### "):
            heading = cleaned.lstrip("#").strip()
            heading = heading.replace("**", "").replace("__", "")
            heading = heading.replace("*", "").replace("_", "")
            if heading and heading not in outline:
                outline.append(heading)

    cited = re.findall(r"https?://[^\s)\]\"'>]+", markdown_content)
    cited = [url.rstrip(".,;:!?)") for url in cited]
    supported, unsupported = split_citations(cited, retrieved_urls)

    return {
        "title": title or "Research Report",
        "outline": outline,
        "report": markdown_content.strip(),
        "sources": supported,
        "word_count": count_words(markdown_content),
        "key_findings": [],
        "unsupported_citations": unsupported,
    }


def is_safe_citation_url(url: str) -> bool:
    """Reject non-HTTP(S) citations such as ``javascript:`` or ``file:``."""
    if not isinstance(url, str):
        return False
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return False
    return parsed.scheme.lower() in {"http", "https"} and bool(parsed.hostname)
