"""
PALVERON SDK for Python — Official AI Governance Client
=====================================================

Usage::

    from palveron import Palveron

    client = Palveron(api_key="pv_live_xxx")
    result = client.verify("Check this prompt for safety")

    if result.decision == "BLOCKED":
        raise RuntimeError(f"Blocked: {result.reason}")

Async usage::

    from palveron import AsyncPalveron

    client = AsyncPalveron(api_key="pv_live_xxx")
    result = await client.verify("Check this prompt")
"""

from __future__ import annotations

import base64
import hashlib
import mimetypes
import os
import time
import random
import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional, Sequence

import httpx

__version__ = "1.1.0"
__all__ = [
    "Palveron",
    "AsyncPalveron",
    "VerifyRequest",
    "VerifyResponse",
    "Attachment",
    "RequestContext",
    "Finding",
    "Decision",
    "PalveronError",
    "PalveronAuthenticationError",
    "PalveronRateLimitError",
    "PalveronValidationError",
    "PalveronTimeoutError",
    "PalveronCircuitOpenError",
]

logger = logging.getLogger("palveron")

# ── Enums ────────────────────────────────────────────────────


class Decision(str, Enum):
    """
    Governance decision returned by ``/api/v1/verify``.

    The gateway emits ``PASSED`` (Sprint 73+); ``ALLOWED`` is preserved
    as an alias for older deployments. ``RATE_LIMITED`` is synthesised
    client-side when the gateway returns 429, so callers can branch on
    ``decision`` uniformly instead of catching an exception just for
    rate-limit hits.

    HTTP status code mapping (Sprint 87):

    * ``PASSED`` / ``ALLOWED`` / ``MODIFIED`` / ``FLAGGED`` /
      ``POLICY_CHANGE`` → 200 OK
    * ``PENDING_APPROVAL`` → 202 Accepted
    * ``BLOCKED`` → 403 Forbidden
    * ``RATE_LIMITED`` → 429 Too Many Requests
    * ``ERROR`` → transport/internal failure
    """

    PASSED = "PASSED"
    ALLOWED = "ALLOWED"
    BLOCKED = "BLOCKED"
    MODIFIED = "MODIFIED"
    FLAGGED = "FLAGGED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    POLICY_CHANGE = "POLICY_CHANGE"
    RATE_LIMITED = "RATE_LIMITED"
    ERROR = "ERROR"

    @classmethod
    def _missing_(cls, value: Any) -> "Decision":
        # Forward-compat: unknown decisions from a newer gateway should
        # not raise — surface as ERROR so the caller can decide.
        logger.warning("Unknown Decision value from gateway: %r — falling back to ERROR", value)
        return cls.ERROR


class RiskLevel(str, Enum):
    MINIMAL = "minimal"
    LIMITED = "limited"
    HIGH = "high"
    UNACCEPTABLE = "unacceptable"


# ── Data Classes ─────────────────────────────────────────────


@dataclass(frozen=True)
class Attachment:
    """Multi-modal attachment (image, audio, document, code)."""

    content_type: str
    data: str  # Base64-encoded
    filename: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None

    @classmethod
    def from_file(cls, path: str | Path) -> Attachment:
        """Create an attachment from a local file."""
        p = Path(path)
        content = p.read_bytes()
        mime, _ = mimetypes.guess_type(str(p))
        return cls(
            content_type=mime or "application/octet-stream",
            data=base64.b64encode(content).decode("ascii"),
            filename=p.name,
            metadata={"size_bytes": len(content)},
        )

    @classmethod
    def from_bytes(cls, data: bytes, content_type: str, filename: Optional[str] = None) -> Attachment:
        """Create an attachment from raw bytes."""
        return cls(
            content_type=content_type,
            data=base64.b64encode(data).decode("ascii"),
            filename=filename,
            metadata={"size_bytes": len(data)},
        )


@dataclass(frozen=True)
class RequestContext:
    """Agentic context for MCP, tool chains, and source systems."""

    mcp_server: Optional[str] = None
    tool_name: Optional[str] = None
    chain_depth: Optional[int] = None
    source_system: Optional[str] = None
    session_id: Optional[str] = None


@dataclass(frozen=True)
class Finding:
    """Security finding from content analysis."""

    risk: str  # LOW, MEDIUM, HIGH, CRITICAL
    category: str
    description: str
    confidence: float


@dataclass(frozen=True)
class VerifyRequest:
    """Governance verification request."""

    prompt: str
    extracted_text: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None
    attachments: Optional[Sequence[Attachment]] = None
    context: Optional[RequestContext] = None


@dataclass(frozen=True)
class VerifyResponse:
    """Governance verification response."""

    decision: Decision
    output: str
    reason: str
    trace_id: str
    integrity_hash: str
    should_anchor: bool
    flare_status: str
    flare_tx_hash: Optional[str]
    content_type: str
    findings: list[Finding]
    latency_ms: float
    #: Retry hint when ``decision == RATE_LIMITED`` — derived from the
    #: gateway's ``Retry-After`` header (in milliseconds). Honour it
    #: before issuing the next request.
    retry_after_ms: Optional[int] = None
    #: HTTP status code that produced this response (200, 202, 403, 429).
    http_status: Optional[int] = None

    @property
    def is_allowed(self) -> bool:
        return self.decision in (Decision.ALLOWED, Decision.PASSED)

    @property
    def is_blocked(self) -> bool:
        return self.decision == Decision.BLOCKED

    @property
    def is_pending_approval(self) -> bool:
        return self.decision == Decision.PENDING_APPROVAL

    @property
    def is_rate_limited(self) -> bool:
        return self.decision == Decision.RATE_LIMITED

    @property
    def has_findings(self) -> bool:
        return len(self.findings) > 0


@dataclass(frozen=True)
class HealthResponse:
    status: str
    version: str
    uptime: float


# ── Errors ───────────────────────────────────────────────────


class PalveronError(Exception):
    """Base error for all PALVERON SDK errors."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "UNKNOWN",
        status_code: int = 0,
        request_id: Optional[str] = None,
        retryable: bool = False,
    ):
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.request_id = request_id
        self.retryable = retryable


class PalveronAuthenticationError(PalveronError):
    def __init__(self, message: str = "Invalid API key", request_id: Optional[str] = None):
        super().__init__(message, code="AUTHENTICATION_FAILED", status_code=401, request_id=request_id)


class PalveronRateLimitError(PalveronError):
    def __init__(self, message: str, retry_after_ms: int, request_id: Optional[str] = None):
        super().__init__(message, code="RATE_LIMITED", status_code=429, request_id=request_id, retryable=True)
        self.retry_after_ms = retry_after_ms


class PalveronValidationError(PalveronError):
    def __init__(self, message: str, field: Optional[str] = None, request_id: Optional[str] = None):
        super().__init__(message, code="VALIDATION_ERROR", status_code=400, request_id=request_id)
        self.field = field


class PalveronTimeoutError(PalveronError):
    def __init__(self, timeout_ms: float, request_id: Optional[str] = None):
        super().__init__(f"Request timed out after {timeout_ms:.0f}ms", code="TIMEOUT", status_code=408, request_id=request_id, retryable=True)


class PalveronCircuitOpenError(PalveronError):
    def __init__(self) -> None:
        super().__init__("Circuit breaker open — too many consecutive failures", code="CIRCUIT_OPEN", status_code=503)


# ── Circuit Breaker ──────────────────────────────────────────


class _CircuitBreaker:
    def __init__(self, threshold: int = 5, cooldown_s: float = 30.0):
        self._threshold = threshold
        self._cooldown_s = cooldown_s
        self._failures = 0
        self._last_failure = 0.0
        self._state = "closed"

    @property
    def state(self) -> str:
        return self._state

    def can_request(self) -> bool:
        if self._state == "closed":
            return True
        if self._state == "open" and time.monotonic() - self._last_failure >= self._cooldown_s:
            self._state = "half-open"
            return True
        return self._state == "half-open"

    def on_success(self) -> None:
        self._failures = 0
        self._state = "closed"

    def on_failure(self) -> None:
        self._failures += 1
        self._last_failure = time.monotonic()
        if self._failures >= self._threshold:
            self._state = "open"


# ── Response Parser ──────────────────────────────────────────


def _parse_verify_response(
    data: dict[str, Any],
    latency_ms: float,
    *,
    http_status: Optional[int] = None,
    retry_after_ms: Optional[int] = None,
) -> VerifyResponse:
    findings = [
        Finding(
            risk=f.get("risk", "LOW"),
            category=f.get("category", ""),
            description=f.get("description", ""),
            confidence=float(f.get("confidence", 0)),
        )
        for f in (data.get("findings") or [])
    ]
    # Decision precedence: body field > synthesised from HTTP status > ERROR.
    raw_decision = data.get("decision")
    if raw_decision:
        decision = Decision(raw_decision)
    elif http_status is not None:
        decision = _decision_from_status(http_status)
    else:
        decision = Decision.ERROR

    # Fall back to body.error when the body has no `reason` field
    # (notably 429 rate-limit responses use a different body shape).
    reason = data.get("reason") or data.get("error") or ""

    return VerifyResponse(
        decision=decision,
        output=data.get("output", ""),
        reason=reason,
        trace_id=data.get("trace_id", ""),
        integrity_hash=data.get("integrity_hash", ""),
        should_anchor=data.get("should_anchor", False),
        flare_status=data.get("flare_status", ""),
        flare_tx_hash=data.get("flare_tx_hash"),
        content_type=data.get("content_type", "text"),
        findings=findings,
        latency_ms=latency_ms,
        retry_after_ms=retry_after_ms,
        http_status=http_status,
    )


def _decision_from_status(status: int) -> Decision:
    """Synthesise a Decision from an HTTP status code when the response
    body has no `decision` field (notably 429 rate-limit responses)."""
    if status == 429:
        return Decision.RATE_LIMITED
    if status == 403:
        return Decision.BLOCKED
    if status == 202:
        return Decision.PENDING_APPROVAL
    if 200 <= status < 300:
        return Decision.PASSED
    return Decision.ERROR


def _parse_retry_after(value: Optional[str]) -> Optional[int]:
    """Parse an HTTP ``Retry-After`` header into milliseconds.

    Per RFC 7231 the value can be either delta-seconds (an integer) or
    an HTTP-date. We support both. Returns ``None`` when the header is
    missing or unparseable so the caller can apply its own default.
    """
    if not value:
        return None
    trimmed = value.strip()
    if not trimmed:
        return None
    # Try delta-seconds first
    try:
        seconds = float(trimmed)
        if seconds >= 0:
            return int(seconds * 1000)
    except ValueError:
        pass
    # Try HTTP-date
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(trimmed)
        delta_ms = int((dt.timestamp() - time.time()) * 1000)
        return max(delta_ms, 0)
    except (TypeError, ValueError):
        return None


def _build_verify_body(request: VerifyRequest) -> dict[str, Any]:
    body: dict[str, Any] = {"prompt": request.prompt}
    if request.extracted_text:
        body["extracted_text"] = request.extracted_text
    if request.metadata:
        body["metadata"] = request.metadata
    if request.attachments:
        body["attachments"] = [
            {
                "content_type": a.content_type,
                "data": a.data,
                "filename": a.filename,
                "metadata": a.metadata,
            }
            for a in request.attachments
        ]
    if request.context:
        ctx = request.context
        body["context"] = {
            k: v
            for k, v in {
                "mcp_server": ctx.mcp_server,
                "tool_name": ctx.tool_name,
                "chain_depth": ctx.chain_depth,
                "source_system": ctx.source_system,
                "session_id": ctx.session_id,
            }.items()
            if v is not None
        }
    return body


def _make_request_id() -> str:
    ts = hex(int(time.time() * 1000))[2:]
    rand = hashlib.md5(os.urandom(8)).hexdigest()[:6]
    return f"pv_{ts}_{rand}"


def _backoff_delay(attempt: int, base: float = 0.5) -> float:
    delay = base * (2 ** (attempt - 1))
    jitter = delay * 0.2 * random.random()
    return min(delay + jitter, 30.0)


# ── Sync Client ──────────────────────────────────────────────


class Palveron:
    """
    Synchronous PALVERON client.

    Args:
        api_key: Your project API key (starts with ``pv_live_``).
        base_url: Gateway URL. Defaults to ``https://gateway.palveron.com``.
        timeout: Request timeout in seconds. Defaults to ``30.0``.
        max_retries: Maximum retry attempts on transient failures. Defaults to ``3``.
        headers: Extra headers added to every request.

    Example::

        from palveron import Palveron

        client = Palveron(api_key="pv_live_abc123")

        # Simple check
        result = client.check("Is this prompt safe?")

        # With file attachment
        result = client.verify_file("Analyze this", "/path/to/doc.pdf")

        # Full control
        from palveron import VerifyRequest, Attachment
        result = client.verify(VerifyRequest(
            prompt="Check this image",
            attachments=[Attachment.from_file("photo.jpg")],
        ))
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://gateway.palveron.com",
        timeout: float = 30.0,
        max_retries: int = 3,
        headers: Optional[dict[str, str]] = None,
        circuit_threshold: int = 5,
        circuit_cooldown: float = 30.0,
    ):
        if not api_key:
            raise PalveronValidationError("api_key is required")

        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._max_retries = max_retries
        self._circuit = _CircuitBreaker(circuit_threshold, circuit_cooldown)

        self._client = httpx.Client(
            base_url=self._base_url,
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": f"palveron-sdk-python/{__version__}",
                **(headers or {}),
            },
        )

    def __enter__(self) -> Palveron:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    # ── Public API ───────────────────────────────────────

    def verify(self, request: str | VerifyRequest) -> VerifyResponse:
        """Send a governance verification request.

        Sprint 87 — the gateway maps the ``decision`` field onto HTTP
        status codes (200 PASSED / 202 PENDING_APPROVAL / 403 BLOCKED /
        429 RATE_LIMITED). This method treats all four as legitimate
        governance outcomes and returns a ``VerifyResponse`` for each;
        it does **not** raise on 403 / 429. Only transport, auth,
        validation, and 5xx failures raise.
        """
        if isinstance(request, str):
            request = VerifyRequest(prompt=request)
        body = _build_verify_body(request)
        start = time.monotonic()
        result = self._request(
            "POST", "/api/v1/verify",
            json=body,
            expect_governance_decision=True,
        )
        latency = (time.monotonic() - start) * 1000
        return _parse_verify_response(
            result["body"],
            latency,
            http_status=result["status"],
            retry_after_ms=result.get("retry_after_ms"),
        )

    def check(self, prompt: str) -> VerifyResponse:
        """Quick text-only verification."""
        return self.verify(prompt)

    def verify_file(self, prompt: str, path: str | Path) -> VerifyResponse:
        """Verify prompt with a file attachment."""
        attachment = Attachment.from_file(path)
        return self.verify(VerifyRequest(prompt=prompt, attachments=[attachment]))

    def health(self) -> HealthResponse:
        """Check gateway health."""
        data = self._request("GET", "/health")["body"]
        return HealthResponse(status=data.get("status", "unknown"), version=data.get("version", ""), uptime=data.get("uptime", 0))

    @property
    def diagnostics(self) -> dict[str, Any]:
        return {"sdk_version": __version__, "base_url": self._base_url, "timeout": self._timeout, "circuit_state": self._circuit.state}

    # ── Internal ─────────────────────────────────────────

    def _request(
        self,
        method: str,
        path: str,
        *,
        expect_governance_decision: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Issue an HTTP request with retry + circuit-breaker + timeout.

        Returns a dict with keys ``body`` (the parsed JSON), ``status``
        (the HTTP status code) and optionally ``retry_after_ms`` for
        rate-limited responses.

        When ``expect_governance_decision`` is True, the verify-path
        status codes (202 / 403 / 429) are returned as governance
        results instead of being raised as exceptions. Other endpoints
        keep the strict throw-on-non-2xx behaviour so 429 on idempotent
        reads (e.g. listPolicies) still retries with backoff.
        """
        if not self._circuit.can_request():
            raise PalveronCircuitOpenError()

        last_error: Optional[Exception] = None
        for attempt in range(self._max_retries + 1):
            if attempt > 0:
                delay = _backoff_delay(attempt)
                logger.debug(f"Retry {attempt}/{self._max_retries} after {delay:.1f}s")
                time.sleep(delay)

            request_id = _make_request_id()
            try:
                resp = self._client.request(method, path, headers={"X-Request-ID": request_id}, **kwargs)
                rid = resp.headers.get("x-request-id", request_id)

                if resp.is_success:
                    self._circuit.on_success()
                    return {"body": resp.json(), "status": resp.status_code}

                # ── Sprint 87 governance status codes ──
                if expect_governance_decision and resp.status_code in (202, 403, 429):
                    self._circuit.on_success()  # not a transport failure
                    body = self._safe_json(resp)
                    retry_after = (
                        _parse_retry_after(resp.headers.get("retry-after"))
                        if resp.status_code == 429
                        else None
                    )
                    return {
                        "body": body,
                        "status": resp.status_code,
                        "retry_after_ms": retry_after,
                    }

                self._handle_error(resp, rid, attempt)

            except (httpx.TimeoutException,):
                self._circuit.on_failure()
                last_error = PalveronTimeoutError(self._timeout * 1000, request_id)
            except (httpx.ConnectError, httpx.NetworkError):
                self._circuit.on_failure()
                last_error = PalveronError("Network error", code="NETWORK_ERROR", status_code=0, retryable=True)
            except PalveronError as e:
                if not e.retryable:
                    raise
                last_error = e

        raise last_error or PalveronError("Max retries exceeded", code="MAX_RETRIES")

    @staticmethod
    def _safe_json(resp: httpx.Response) -> dict[str, Any]:
        try:
            return resp.json()
        except Exception:  # noqa: BLE001
            return {}

    def _handle_error(self, resp: httpx.Response, request_id: str, attempt: int) -> None:
        if resp.status_code == 401:
            raise PalveronAuthenticationError(request_id=request_id)
        if resp.status_code == 429:
            retry_after = _parse_retry_after(resp.headers.get("retry-after")) or 5000
            raise PalveronRateLimitError("Rate limit exceeded", retry_after, request_id)
        if resp.status_code == 400:
            body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            raise PalveronValidationError(body.get("error", "Bad request"), body.get("field"), request_id)
        if resp.status_code >= 500:
            self._circuit.on_failure()
            raise PalveronError(f"Server error {resp.status_code}", code="SERVER_ERROR", status_code=resp.status_code, request_id=request_id, retryable=True)
        raise PalveronError(f"HTTP {resp.status_code}", code="CLIENT_ERROR", status_code=resp.status_code, request_id=request_id)


# ── Async Client ─────────────────────────────────────────────


class AsyncPalveron:
    """
    Async PALVERON client for asyncio applications.

    Example::

        import asyncio
        from palveron import AsyncPalveron

        async def main():
            async with AsyncPalveron(api_key="pv_live_abc") as client:
                result = await client.verify("Check this")
                print(result.decision)

        asyncio.run(main())
    """

    def __init__(self, api_key: str, *, base_url: str = "https://gateway.palveron.com", timeout: float = 30.0, max_retries: int = 3, headers: Optional[dict[str, str]] = None, circuit_threshold: int = 5, circuit_cooldown: float = 30.0):
        if not api_key:
            raise PalveronValidationError("api_key is required")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._max_retries = max_retries
        self._circuit = _CircuitBreaker(circuit_threshold, circuit_cooldown)
        self._client = httpx.AsyncClient(base_url=self._base_url, timeout=timeout, headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "Accept": "application/json", "User-Agent": f"palveron-sdk-python/{__version__}", **(headers or {})})

    async def __aenter__(self) -> AsyncPalveron:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    async def verify(self, request: str | VerifyRequest) -> VerifyResponse:
        """Send a governance verification request (async).

        See :meth:`Palveron.verify` for the Sprint 87 HTTP-status
        contract — 202 / 403 / 429 surface as governance outcomes,
        not exceptions.
        """
        if isinstance(request, str):
            request = VerifyRequest(prompt=request)
        body = _build_verify_body(request)
        start = time.monotonic()
        result = await self._request(
            "POST", "/api/v1/verify",
            json=body,
            expect_governance_decision=True,
        )
        latency = (time.monotonic() - start) * 1000
        return _parse_verify_response(
            result["body"],
            latency,
            http_status=result["status"],
            retry_after_ms=result.get("retry_after_ms"),
        )

    async def check(self, prompt: str) -> VerifyResponse:
        return await self.verify(prompt)

    async def verify_file(self, prompt: str, path: str | Path) -> VerifyResponse:
        attachment = Attachment.from_file(path)
        return await self.verify(VerifyRequest(prompt=prompt, attachments=[attachment]))

    async def health(self) -> HealthResponse:
        data = (await self._request("GET", "/health"))["body"]
        return HealthResponse(status=data.get("status", "unknown"), version=data.get("version", ""), uptime=data.get("uptime", 0))

    async def _request(
        self,
        method: str,
        path: str,
        *,
        expect_governance_decision: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        if not self._circuit.can_request():
            raise PalveronCircuitOpenError()
        last_error: Optional[Exception] = None
        for attempt in range(self._max_retries + 1):
            if attempt > 0:
                import asyncio
                await asyncio.sleep(_backoff_delay(attempt))
            request_id = _make_request_id()
            try:
                resp = await self._client.request(method, path, headers={"X-Request-ID": request_id}, **kwargs)
                if resp.is_success:
                    self._circuit.on_success()
                    return {"body": resp.json(), "status": resp.status_code}
                rid = resp.headers.get("x-request-id", request_id)

                # ── Sprint 87 governance status codes ──
                if expect_governance_decision and resp.status_code in (202, 403, 429):
                    self._circuit.on_success()  # not a transport failure
                    body = self._safe_json(resp)
                    retry_after = (
                        _parse_retry_after(resp.headers.get("retry-after"))
                        if resp.status_code == 429
                        else None
                    )
                    return {
                        "body": body,
                        "status": resp.status_code,
                        "retry_after_ms": retry_after,
                    }

                if resp.status_code == 401:
                    raise PalveronAuthenticationError(request_id=rid)
                if resp.status_code == 429:
                    retry_after = _parse_retry_after(resp.headers.get("retry-after")) or 5000
                    raise PalveronRateLimitError("Rate limit exceeded", retry_after, rid)
                if resp.status_code == 400:
                    body = resp.json() if "json" in resp.headers.get("content-type", "") else {}
                    raise PalveronValidationError(body.get("error", "Bad request"), body.get("field"), rid)
                if resp.status_code >= 500:
                    self._circuit.on_failure()
                    last_error = PalveronError(f"Server error {resp.status_code}", code="SERVER_ERROR", status_code=resp.status_code, request_id=rid, retryable=True)
                    continue
                raise PalveronError(f"HTTP {resp.status_code}", code="CLIENT_ERROR", status_code=resp.status_code, request_id=rid)
            except (httpx.TimeoutException,):
                self._circuit.on_failure()
                last_error = PalveronTimeoutError(self._timeout * 1000, request_id)
            except (httpx.ConnectError, httpx.NetworkError):
                self._circuit.on_failure()
                last_error = PalveronError("Network error", code="NETWORK_ERROR", retryable=True)
            except PalveronError as e:
                if not e.retryable:
                    raise
                last_error = e
        raise last_error or PalveronError("Max retries exceeded", code="MAX_RETRIES")

    @staticmethod
    def _safe_json(resp: httpx.Response) -> dict[str, Any]:
        try:
            return resp.json()
        except Exception:  # noqa: BLE001
            return {}
