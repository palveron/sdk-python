"""
VEXIS SDK for Python — Official AI Governance Client
=====================================================

Usage::

    from vexis import Vexis

    client = Vexis(api_key="gp_live_xxx")
    result = client.verify("Check this prompt for safety")

    if result.decision == "BLOCKED":
        raise RuntimeError(f"Blocked: {result.reason}")

Async usage::

    from vexis import AsyncVexis

    client = AsyncVexis(api_key="gp_live_xxx")
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

__version__ = "0.4.0"
__all__ = [
    "Vexis",
    "AsyncVexis",
    "VerifyRequest",
    "VerifyResponse",
    "Attachment",
    "RequestContext",
    "Finding",
    "Decision",
    "VexisError",
    "VexisAuthenticationError",
    "VexisRateLimitError",
    "VexisValidationError",
    "VexisTimeoutError",
    "VexisCircuitOpenError",
]

logger = logging.getLogger("vexis")

# ── Enums ────────────────────────────────────────────────────


class Decision(str, Enum):
    ALLOWED = "ALLOWED"
    BLOCKED = "BLOCKED"
    MODIFIED = "MODIFIED"
    ERROR = "ERROR"


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

    @property
    def is_allowed(self) -> bool:
        return self.decision == Decision.ALLOWED

    @property
    def is_blocked(self) -> bool:
        return self.decision == Decision.BLOCKED

    @property
    def has_findings(self) -> bool:
        return len(self.findings) > 0


@dataclass(frozen=True)
class HealthResponse:
    status: str
    version: str
    uptime: float


# ── Errors ───────────────────────────────────────────────────


class VexisError(Exception):
    """Base error for all VEXIS SDK errors."""

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


class VexisAuthenticationError(VexisError):
    def __init__(self, message: str = "Invalid API key", request_id: Optional[str] = None):
        super().__init__(message, code="AUTHENTICATION_FAILED", status_code=401, request_id=request_id)


class VexisRateLimitError(VexisError):
    def __init__(self, message: str, retry_after_ms: int, request_id: Optional[str] = None):
        super().__init__(message, code="RATE_LIMITED", status_code=429, request_id=request_id, retryable=True)
        self.retry_after_ms = retry_after_ms


class VexisValidationError(VexisError):
    def __init__(self, message: str, field: Optional[str] = None, request_id: Optional[str] = None):
        super().__init__(message, code="VALIDATION_ERROR", status_code=400, request_id=request_id)
        self.field = field


class VexisTimeoutError(VexisError):
    def __init__(self, timeout_ms: float, request_id: Optional[str] = None):
        super().__init__(f"Request timed out after {timeout_ms:.0f}ms", code="TIMEOUT", status_code=408, request_id=request_id, retryable=True)


class VexisCircuitOpenError(VexisError):
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


def _parse_verify_response(data: dict[str, Any], latency_ms: float) -> VerifyResponse:
    findings = [
        Finding(
            risk=f.get("risk", "LOW"),
            category=f.get("category", ""),
            description=f.get("description", ""),
            confidence=float(f.get("confidence", 0)),
        )
        for f in (data.get("findings") or [])
    ]
    return VerifyResponse(
        decision=Decision(data.get("decision", "ERROR")),
        output=data.get("output", ""),
        reason=data.get("reason", ""),
        trace_id=data.get("trace_id", ""),
        integrity_hash=data.get("integrity_hash", ""),
        should_anchor=data.get("should_anchor", False),
        flare_status=data.get("flare_status", ""),
        flare_tx_hash=data.get("flare_tx_hash"),
        content_type=data.get("content_type", "text"),
        findings=findings,
        latency_ms=latency_ms,
    )


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
    return f"vx_{ts}_{rand}"


def _backoff_delay(attempt: int, base: float = 0.5) -> float:
    delay = base * (2 ** (attempt - 1))
    jitter = delay * 0.2 * random.random()
    return min(delay + jitter, 30.0)


# ── Sync Client ──────────────────────────────────────────────


class Vexis:
    """
    Synchronous VEXIS client.

    Args:
        api_key: Your project API key (starts with ``gp_live_`` or ``gp_test_``).
        base_url: Gateway URL. Defaults to ``https://gateway.vexis.io``.
        timeout: Request timeout in seconds. Defaults to ``30.0``.
        max_retries: Maximum retry attempts on transient failures. Defaults to ``3``.
        headers: Extra headers added to every request.

    Example::

        from vexis import Vexis

        client = Vexis(api_key="gp_live_abc123")

        # Simple check
        result = client.check("Is this prompt safe?")

        # With file attachment
        result = client.verify_file("Analyze this", "/path/to/doc.pdf")

        # Full control
        from vexis import VerifyRequest, Attachment
        result = client.verify(VerifyRequest(
            prompt="Check this image",
            attachments=[Attachment.from_file("photo.jpg")],
        ))
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://gateway.vexis.io",
        timeout: float = 30.0,
        max_retries: int = 3,
        headers: Optional[dict[str, str]] = None,
        circuit_threshold: int = 5,
        circuit_cooldown: float = 30.0,
    ):
        if not api_key:
            raise VexisValidationError("api_key is required")

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
                "User-Agent": f"vexis-sdk-python/{__version__}",
                **(headers or {}),
            },
        )

    def __enter__(self) -> Vexis:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    # ── Public API ───────────────────────────────────────

    def verify(self, request: str | VerifyRequest) -> VerifyResponse:
        """Send a governance verification request."""
        if isinstance(request, str):
            request = VerifyRequest(prompt=request)
        body = _build_verify_body(request)
        start = time.monotonic()
        raw = self._request("POST", "/api/v1/verify", json=body)
        latency = (time.monotonic() - start) * 1000
        return _parse_verify_response(raw, latency)

    def check(self, prompt: str) -> VerifyResponse:
        """Quick text-only verification."""
        return self.verify(prompt)

    def verify_file(self, prompt: str, path: str | Path) -> VerifyResponse:
        """Verify prompt with a file attachment."""
        attachment = Attachment.from_file(path)
        return self.verify(VerifyRequest(prompt=prompt, attachments=[attachment]))

    def health(self) -> HealthResponse:
        """Check gateway health."""
        data = self._request("GET", "/health")
        return HealthResponse(status=data.get("status", "unknown"), version=data.get("version", ""), uptime=data.get("uptime", 0))

    @property
    def diagnostics(self) -> dict[str, Any]:
        return {"sdk_version": __version__, "base_url": self._base_url, "timeout": self._timeout, "circuit_state": self._circuit.state}

    # ── Internal ─────────────────────────────────────────

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        if not self._circuit.can_request():
            raise VexisCircuitOpenError()

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
                    return resp.json()

                self._handle_error(resp, rid, attempt)

            except (httpx.TimeoutException,):
                self._circuit.on_failure()
                last_error = VexisTimeoutError(self._timeout * 1000, request_id)
            except (httpx.ConnectError, httpx.NetworkError):
                self._circuit.on_failure()
                last_error = VexisError("Network error", code="NETWORK_ERROR", status_code=0, retryable=True)
            except VexisError as e:
                if not e.retryable:
                    raise
                last_error = e

        raise last_error or VexisError("Max retries exceeded", code="MAX_RETRIES")

    def _handle_error(self, resp: httpx.Response, request_id: str, attempt: int) -> None:
        if resp.status_code == 401:
            raise VexisAuthenticationError(request_id=request_id)
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("retry-after", "5")) * 1000
            raise VexisRateLimitError("Rate limit exceeded", retry_after, request_id)
        if resp.status_code == 400:
            body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            raise VexisValidationError(body.get("error", "Bad request"), body.get("field"), request_id)
        if resp.status_code >= 500:
            self._circuit.on_failure()
            raise VexisError(f"Server error {resp.status_code}", code="SERVER_ERROR", status_code=resp.status_code, request_id=request_id, retryable=True)
        raise VexisError(f"HTTP {resp.status_code}", code="CLIENT_ERROR", status_code=resp.status_code, request_id=request_id)


# ── Async Client ─────────────────────────────────────────────


class AsyncVexis:
    """
    Async VEXIS client for asyncio applications.

    Example::

        import asyncio
        from vexis import AsyncVexis

        async def main():
            async with AsyncVexis(api_key="gp_live_abc") as client:
                result = await client.verify("Check this")
                print(result.decision)

        asyncio.run(main())
    """

    def __init__(self, api_key: str, *, base_url: str = "https://gateway.vexis.io", timeout: float = 30.0, max_retries: int = 3, headers: Optional[dict[str, str]] = None, circuit_threshold: int = 5, circuit_cooldown: float = 30.0):
        if not api_key:
            raise VexisValidationError("api_key is required")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._max_retries = max_retries
        self._circuit = _CircuitBreaker(circuit_threshold, circuit_cooldown)
        self._client = httpx.AsyncClient(base_url=self._base_url, timeout=timeout, headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "Accept": "application/json", "User-Agent": f"vexis-sdk-python/{__version__}", **(headers or {})})

    async def __aenter__(self) -> AsyncVexis:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    async def verify(self, request: str | VerifyRequest) -> VerifyResponse:
        if isinstance(request, str):
            request = VerifyRequest(prompt=request)
        body = _build_verify_body(request)
        start = time.monotonic()
        raw = await self._request("POST", "/api/v1/verify", json=body)
        latency = (time.monotonic() - start) * 1000
        return _parse_verify_response(raw, latency)

    async def check(self, prompt: str) -> VerifyResponse:
        return await self.verify(prompt)

    async def verify_file(self, prompt: str, path: str | Path) -> VerifyResponse:
        attachment = Attachment.from_file(path)
        return await self.verify(VerifyRequest(prompt=prompt, attachments=[attachment]))

    async def health(self) -> HealthResponse:
        data = await self._request("GET", "/health")
        return HealthResponse(status=data.get("status", "unknown"), version=data.get("version", ""), uptime=data.get("uptime", 0))

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        if not self._circuit.can_request():
            raise VexisCircuitOpenError()
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
                    return resp.json()
                rid = resp.headers.get("x-request-id", request_id)
                if resp.status_code == 401:
                    raise VexisAuthenticationError(request_id=rid)
                if resp.status_code == 429:
                    raise VexisRateLimitError("Rate limit exceeded", int(resp.headers.get("retry-after", "5")) * 1000, rid)
                if resp.status_code == 400:
                    body = resp.json() if "json" in resp.headers.get("content-type", "") else {}
                    raise VexisValidationError(body.get("error", "Bad request"), body.get("field"), rid)
                if resp.status_code >= 500:
                    self._circuit.on_failure()
                    last_error = VexisError(f"Server error {resp.status_code}", code="SERVER_ERROR", status_code=resp.status_code, request_id=rid, retryable=True)
                    continue
                raise VexisError(f"HTTP {resp.status_code}", code="CLIENT_ERROR", status_code=resp.status_code, request_id=rid)
            except (httpx.TimeoutException,):
                self._circuit.on_failure()
                last_error = VexisTimeoutError(self._timeout * 1000, request_id)
            except (httpx.ConnectError, httpx.NetworkError):
                self._circuit.on_failure()
                last_error = VexisError("Network error", code="NETWORK_ERROR", retryable=True)
            except VexisError as e:
                if not e.retryable:
                    raise
                last_error = e
        raise last_error or VexisError("Max retries exceeded", code="MAX_RETRIES")
