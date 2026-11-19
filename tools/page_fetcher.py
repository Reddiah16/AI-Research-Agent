"""Bounded, best-effort webpage fetching and text extraction.

This module is deliberately dependency-free (standard library only) so it can
be unit-tested in environments where the full application dependencies are not
installed. It never executes JavaScript and never writes to disk.

Safety properties:
- only ``http`` and ``https`` URLs are accepted;
- loopback / private-literal hosts are rejected;
- optional DNS resolution rejects hostnames that resolve to private addresses
  (best-effort defence against DNS rebinding);
- redirects are re-validated against the same host and DNS policy;
- optional ``robots.txt`` enforcement (fail-open on retrieval errors);
- responses are capped by timeout and byte limit;
- only text-ish content types are decoded;
- script/style/noscript/template content is discarded by the HTML parser;
- a per-run ``FetchCache`` deduplicates URLs and enforces a total budget.

Residual risk: DNS is checked before the connection is opened. A hostile DNS
server could change the answer between the check and the connection (TOCTOU),
and full IP pinning is not implemented because it complicates TLS SNI. This is
a mitigation, not a sandbox.
"""

from __future__ import annotations

import gzip
import ipaddress
import logging
import re
import socket
import ssl
import time
import zlib
from html.parser import HTMLParser
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener
from urllib.robotparser import RobotFileParser

from tools.research_evidence import canonical_url

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = (
    "AIResearchAgent/1.0 (+local evaluation; contact: project maintainer)"
)

Resolver = Callable[[str, Any], Iterable[Any]]

_TEXT_CONTENT_TYPES = {
    "text/html",
    "application/xhtml+xml",
    "text/plain",
    "text/markdown",
    "application/json",
}

_SKIP_TAGS: Set[str] = {
    "script",
    "style",
    "noscript",
    "template",
    "svg",
    "canvas",
    "iframe",
    "form",
    "nav",
    "footer",
}


class _TextExtractor(HTMLParser):
    """Collect visible text and the document title from HTML."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._in_title = False
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        tag = tag.lower()
        if tag == "title":
            self._in_title = True
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        if tag in {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self.text_parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "title":
            self._in_title = False
        if tag in _SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        if tag in {"p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self.text_parts.append(" ")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)
        elif not self._skip_depth:
            self.text_parts.append(data)


def _clean_text(value: str) -> str:
    value = value.replace("\xa0", " ")
    value = re.sub(r"[ \t\f\v]+", " ", value)
    value = re.sub(r"\n\s*\n+", "\n", value)
    return value.strip()


def html_to_text(html: str) -> Dict[str, str]:
    """Extract title and visible text from an HTML document.

    Malformed HTML is tolerated: the standard-library parser recovers and the
    extractor keeps whatever text it could read.
    """
    if not isinstance(html, str):
        return {"title": "", "text": ""}
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception as exc:  # pragma: no cover - HTMLParser is very tolerant
        logger.debug(f"HTML parsing recovered from error: {exc}")
    return {
        "title": _clean_text(" ".join(parser.title_parts)),
        "text": _clean_text("\n".join(parser.text_parts)),
    }


def _is_public_ip(address: str) -> bool:
    try:
        parsed = ipaddress.ip_address(str(address).strip("[]"))
    except ValueError:
        return False
    return not (
        parsed.is_private
        or parsed.is_loopback
        or parsed.is_link_local
        or parsed.is_multicast
        or parsed.is_reserved
        or parsed.is_unspecified
    )


def _is_public_host(host: str) -> bool:
    """Reject localhost and private/loopback literal addresses."""
    if not host:
        return False
    lowered = host.lower().strip("[]")
    if lowered in {"localhost", "localhost.localdomain"} or lowered.endswith(".local"):
        return False
    try:
        ipaddress.ip_address(lowered)
    except ValueError:
        # Hostname: literal checks pass; optional DNS resolution is applied by
        # ``is_safe_fetch_url(..., resolve_dns=True)``.
        return True
    return _is_public_ip(lowered)


def resolve_host_addresses(host: str, resolver: Optional[Resolver] = None) -> List[str]:
    """Resolve a hostname to its IP address strings.

    ``resolver`` is injectable so tests never perform DNS lookups. The default
    is ``socket.getaddrinfo``.
    """
    resolver = resolver or socket.getaddrinfo
    addresses: List[str] = []
    for info in resolver(host, None):
        # getaddrinfo tuples are (family, type, proto, canonname, sockaddr)
        if isinstance(info, (tuple, list)) and len(info) >= 5:
            sockaddr = info[4]
            if isinstance(sockaddr, (tuple, list)) and sockaddr:
                addresses.append(str(sockaddr[0]))
    return list(dict.fromkeys(addresses))


def is_safe_fetch_url(
    url: str,
    *,
    resolve_dns: bool = False,
    resolver: Optional[Resolver] = None,
) -> bool:
    """Return True when the URL is an allowed, non-local HTTP(S) URL.

    Args:
        resolve_dns: When True, resolve the hostname and reject it if *any*
            resolved address is private, loopback, link-local, multicast,
            reserved, or unspecified. This mitigates DNS rebinding at check
            time; it is not a full IP-pinning sandbox.
        resolver: Optional injectable DNS resolver for tests.
    """
    if not isinstance(url, str) or not url.strip():
        return False
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return False
    if parsed.scheme.lower() not in {"http", "https"}:
        return False
    host = parsed.hostname
    if not host or not _is_public_host(host):
        return False
    if resolve_dns and not _is_literal_ip(host):
        try:
            addresses = resolve_host_addresses(host, resolver)
        except (OSError, socket.gaierror, UnicodeError) as exc:
            logger.info(f"DNS safety check failed for {host}: {exc}")
            return False
        if not addresses or any(not _is_public_ip(address) for address in addresses):
            return False
    return True


def _is_literal_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        return False


class _SafeRedirectHandler(HTTPRedirectHandler):
    def __init__(
        self,
        resolve_dns: bool = False,
        resolver: Optional[Resolver] = None,
    ) -> None:
        super().__init__()
        self.resolve_dns = resolve_dns
        self.resolver = resolver

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        if not is_safe_fetch_url(
            newurl, resolve_dns=self.resolve_dns, resolver=self.resolver
        ):
            raise HTTPError(newurl, code, "Blocked unsafe redirect", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class RobotsPolicy:
    """Minimal ``robots.txt`` policy with per-origin caching.

    Retrieval failures are **fail-open** (the page is allowed) because a
    missing or temporarily unreachable robots file must not break research.
    The policy is opt-in: ``fetch_page`` only applies it when asked.
    """

    def __init__(
        self,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout: float = 3.0,
        fetch_text: Optional[Callable[[str], str]] = None,
    ) -> None:
        self.user_agent = user_agent
        self.timeout = timeout
        self._fetch_text = fetch_text or self._default_fetch_text
        self._cache: Dict[str, Optional[RobotFileParser]] = {}
        self.fetch_failures = 0

    @staticmethod
    def _origin(url: str) -> str:
        parsed = urlparse(url)
        return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"

    def _default_fetch_text(self, url: str) -> str:
        request = Request(
            url,
            headers={"User-Agent": self.user_agent, "Accept": "text/plain,*/*;q=0.1"},
        )
        opener = build_opener(_SafeRedirectHandler())
        with opener.open(request, timeout=self.timeout) as response:
            return response.read(100_000).decode("utf-8", "replace")

    def _load(self, origin: str, *, respect_robots: bool) -> Optional[RobotFileParser]:
        if not respect_robots:
            return None
        try:
            text = self._fetch_text(f"{origin}/robots.txt")
        except Exception as exc:
            self.fetch_failures += 1
            logger.info(f"robots.txt unavailable for {origin} (fail-open): {exc}")
            return None
        if not text or not str(text).strip():
            return None
        parser = RobotFileParser()
        parser.parse(str(text).splitlines())
        return parser

    def is_allowed(self, url: str, *, respect_robots: bool = True) -> bool:
        """Return whether fetching ``url`` is permitted by robots.txt."""
        origin = self._origin(url)
        if not origin:
            return False
        if origin not in self._cache:
            self._cache[origin] = self._load(origin, respect_robots=respect_robots)
        parser = self._cache[origin]
        if parser is None:
            return True
        try:
            return bool(parser.can_fetch(self.user_agent, url))
        except Exception:  # pragma: no cover - robotparser is very tolerant
            return True


class FetchCache:
    """Per-run URL fetch deduplication with a hard total budget.

    Counters are exposed for telemetry:
        attempts, hits, budget_exhausted, failures
    """

    def __init__(self, max_fetches: int = 12) -> None:
        self.max_fetches = max(0, int(max_fetches))
        self.attempts = 0
        self.hits = 0
        self.budget_exhausted = 0
        self.failures = 0
        self._cache: Dict[str, Dict[str, Any]] = {}

    def get(self, url: str, fetcher: Callable[[str], Dict[str, Any]]) -> Dict[str, Any]:
        key = canonical_url(url) or (url or "").strip()
        if not key:
            self.failures += 1
            return {"ok": False, "url": url, "text": "", "error": "invalid_url"}
        if key in self._cache:
            self.hits += 1
            cached = dict(self._cache[key])
            cached["from_cache"] = True
            return cached
        if self.attempts >= self.max_fetches:
            self.budget_exhausted += 1
            return {
                "ok": False,
                "url": url,
                "text": "",
                "error": "fetch_budget_exhausted",
            }
        self.attempts += 1
        try:
            result = fetcher(url)
        except Exception as exc:  # fetchers must never break research
            result = {
                "ok": False,
                "url": url,
                "text": "",
                "error": f"fetcher_exception:{type(exc).__name__}",
            }
        if not isinstance(result, dict):
            result = {
                "ok": False,
                "url": url,
                "text": "",
                "error": "invalid_fetch_result",
            }
        if not result.get("ok"):
            self.failures += 1
        self._cache[key] = result
        return result

    @property
    def cached_urls(self) -> int:
        return len(self._cache)


def _decode_body(raw: bytes, content_type: str) -> str:
    charset_match = re.search(r"charset=([\w\-]+)", content_type or "", re.IGNORECASE)
    encoding = charset_match.group(1) if charset_match else "utf-8"
    try:
        return raw.decode(encoding, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def _decompress(raw: bytes, encoding: str) -> bytes:
    encoding = (encoding or "").lower()
    try:
        if "gzip" in encoding:
            return gzip.decompress(raw)
        if "deflate" in encoding:
            return zlib.decompress(raw)
    except (OSError, zlib.error):
        logger.debug("Response decompression failed; using raw bytes")
    return raw


def _classify_fetch_error(exc: Exception) -> str:
    """Map a fetch exception to a stable, non-secret error code."""
    if isinstance(exc, ssl.SSLCertVerificationError):
        return "tls_certificate_error"
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return "timeout"
    if isinstance(exc, URLError):
        reason = getattr(exc, "reason", None)
        if isinstance(reason, ssl.SSLCertVerificationError):
            return "tls_certificate_error"
        if isinstance(reason, (socket.timeout, TimeoutError)):
            return "timeout"
        return f"network_error:{type(reason).__name__}" if reason is not None else "network_error"
    return f"network_error:{type(exc).__name__}"


def _retry_after_seconds(headers: Any) -> Optional[float]:
    """Read a bounded ``Retry-After`` header value when present."""
    try:
        value = headers.get("Retry-After") if headers is not None else None
    except Exception:
        return None
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return None


def fetch_page(
    url: str,
    timeout: float = 6.0,
    max_bytes: int = 500_000,
    user_agent: str = DEFAULT_USER_AGENT,
    *,
    respect_robots: bool = False,
    robots_policy: Optional[RobotsPolicy] = None,
    resolve_dns: bool = False,
    resolver: Optional[Resolver] = None,
    retry_statuses: Sequence[int] = (429,),
    max_retries: int = 0,
    retry_backoff: float = 1.0,
) -> Dict[str, Any]:
    """Fetch a page and return extracted text with an explicit status record.

    Args:
        url: HTTP(S) URL to fetch.
        timeout: Socket timeout in seconds.
        max_bytes: Hard response-size cap.
        user_agent: User-Agent and robots.txt agent identity.
        respect_robots: Apply the robots.txt policy (opt-in for backward
            compatibility; the Researcher enables it from configuration).
        robots_policy: Optional shared policy/cache (recommended for runs).
        resolve_dns: Resolve hostnames and reject private addresses.
        resolver: Injectable DNS resolver for tests.
        retry_statuses: HTTP status codes eligible for bounded retry
            (default: 429 Too Many Requests).
        max_retries: Additional attempts after the first (0 = one attempt).
        retry_backoff: Base exponential-backoff seconds between retries.

    Returns:
        Dict with ``ok``, ``url``, ``status``, ``content_type``, ``title``,
        ``text``, ``error``, ``fetched_chars``, ``truncated``,
        ``robots_status``, ``dns_checked`` and ``attempts``. Failures never
        raise; callers fall back to the search snippet.
    """
    result: Dict[str, Any] = {
        "ok": False,
        "url": url,
        "status": None,
        "content_type": "",
        "title": "",
        "text": "",
        "error": None,
        "fetched_chars": 0,
        "truncated": False,
        "robots_status": "not_checked",
        "dns_checked": bool(resolve_dns),
        "attempts": 0,
    }
    if not is_safe_fetch_url(url, resolve_dns=resolve_dns, resolver=resolver):
        result["error"] = "blocked_or_invalid_url"
        return result

    if respect_robots:
        policy = robots_policy or RobotsPolicy(user_agent=user_agent, timeout=min(3.0, timeout))
        if not policy.is_allowed(url, respect_robots=True):
            result["robots_status"] = "disallowed"
            result["error"] = "robots_disallowed"
            return result
        result["robots_status"] = "allowed"

    attempts = max(1, int(max_retries) + 1)
    retry_codes = {int(code) for code in (retry_statuses or ()) if isinstance(code, int)}

    for attempt in range(1, attempts + 1):
        result["attempts"] = attempt
        request = Request(
            url,
            headers={
                "User-Agent": user_agent,
                "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.5",
                "Accept-Encoding": "gzip, deflate",
            },
        )
        try:
            opener = build_opener(_SafeRedirectHandler(resolve_dns, resolver))
            with opener.open(request, timeout=timeout) as response:
                result["status"] = getattr(response, "status", None)
                content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                result["content_type"] = content_type
                if content_type and content_type not in _TEXT_CONTENT_TYPES:
                    result["error"] = f"unsupported_content_type:{content_type}"
                    return result

                raw = response.read(max_bytes + 1)
                if len(raw) > max_bytes:
                    raw = raw[:max_bytes]
                    result["truncated"] = True
                raw = _decompress(raw, response.headers.get("Content-Encoding") or "")
                body = _decode_body(raw, content_type)

            if content_type in {"text/html", "application/xhtml+xml"}:
                extracted = html_to_text(body)
            else:
                extracted = {"title": "", "text": _clean_text(body)}

            result["title"] = extracted["title"]
            result["text"] = extracted["text"]
            result["fetched_chars"] = len(extracted["text"])
            result["ok"] = bool(extracted["text"])
            if not result["ok"]:
                result["error"] = "empty_extracted_text"
            return result
        except HTTPError as exc:
            code = getattr(exc, "code", None)
            result["status"] = code
            if isinstance(code, int) and code in retry_codes and attempt < attempts:
                retry_after = _retry_after_seconds(getattr(exc, "headers", None))
                delay = retry_after if retry_after is not None else float(retry_backoff) * attempt
                delay = min(max(0.0, delay), 10.0)
                result["error"] = f"http_error:{code}:retrying"
                logger.info(
                    f"Fetch attempt {attempt}/{attempts} for {url} returned {code}; "
                    f"retrying in {delay:.2f}s"
                )
                if delay:
                    time.sleep(delay)
                continue
            result["error"] = f"http_error:{code}"
            return result
        except (URLError, socket.timeout, TimeoutError, ssl.SSLError, OSError) as exc:
            result["error"] = _classify_fetch_error(exc)
            return result
        except Exception as exc:  # pragma: no cover - defensive boundary
            logger.debug(f"Unexpected page-fetch failure for {url}: {exc}")
            result["error"] = f"unexpected_error:{type(exc).__name__}"
            return result

    return result
