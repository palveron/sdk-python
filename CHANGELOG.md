# Changelog

All notable changes to `palveron-sdk` will be documented in this file.

This project follows [Semantic Versioning](https://semver.org/).

## [1.1.0] — 2026-05-19

### Changed
- `verify()` (both `Palveron` and `AsyncPalveron`) now treats the gateway's
  Sprint-87 HTTP semantics as governance decisions rather than errors:
  - `200 OK` → `Decision.PASSED` / `MODIFIED` / `FLAGGED` / `POLICY_CHANGE`
  - `202 Accepted` → `Decision.PENDING_APPROVAL`
  - `403 Forbidden` → `Decision.BLOCKED`
  - `429 Too Many Requests` → `Decision.RATE_LIMITED` (synthesised) with
    `VerifyResponse.retry_after_ms` parsed from `Retry-After`
- Previous behaviour: 403/429 raised `PalveronError` / `PalveronRateLimitError`.
  New behaviour: only transport / auth / 400 / 5xx / network / timeout
  raise. Every governance outcome flows through `VerifyResponse.decision`.
- Non-verify endpoints (`health`, future read APIs) keep the strict
  error-on-non-2xx behaviour so 429 on idempotent reads still retries.
- Fixed `__version__` lagging behind `pyproject.toml` (was `"0.5.0"` while
  the package was at `1.0.0`); both now resolve to `1.1.0`.

### Added
- `Decision` enum extended to cover every gateway decision:
  `PASSED`, `ALLOWED`, `BLOCKED`, `MODIFIED`, `FLAGGED`, `PENDING_APPROVAL`,
  `POLICY_CHANGE`, `RATE_LIMITED`, `ERROR`.
- Forward-compat: unknown decisions from a newer gateway fall back to
  `Decision.ERROR` (with a warning log) instead of raising `ValueError`.
- `VerifyResponse.retry_after_ms` — populated when
  `decision == RATE_LIMITED`.
- `VerifyResponse.http_status` — the HTTP status that produced the
  response, useful for observability.
- `VerifyResponse.is_pending_approval` and `is_rate_limited` convenience
  properties (alongside the existing `is_allowed` / `is_blocked`).
- `is_allowed` now matches both `Decision.ALLOWED` and `Decision.PASSED`.
- RFC-7231-compliant `Retry-After` parsing (handles both delta-seconds
  and HTTP-date formats).

### Migration
- Replace `except PalveronError` blocks that handled blocks with a
  `if result.decision == Decision.BLOCKED:` branch.
- Replace `except PalveronRateLimitError` with
  `if result.is_rate_limited:` plus `time.sleep(result.retry_after_ms / 1000)`.

## [1.0.0] — 2026-05-17

### Added
- Initial public release of `palveron-sdk` on PyPI
- `Palveron` synchronous client with full API coverage
- `AsyncPalveron` async client with full API parity
- `verify()` core method with multi-modal attachments (image, audio,
  video, document, code) via `Attachment.from_file()` /
  `Attachment.from_bytes()`
- `check()` convenience method for text-only verification
- `verify_file()` convenience method with automatic MIME detection
- `list_policies()` and `health()` endpoints
- `RequestContext` for MCP / agentic context
  (`mcp_server`, `tool_name`, `chain_depth`, `source_system`, `session_id`)
- Typed error hierarchy: `PalveronError`, `PalveronAuthenticationError`,
  `PalveronRateLimitError`, `PalveronValidationError`,
  `PalveronTimeoutError`, `PalveronCircuitOpenError`
- `retry_after_ms` on rate-limit errors
- Convenience properties: `is_allowed`, `is_blocked`, `has_findings`
- Retry with exponential backoff + jitter
- Circuit breaker with configurable threshold and cooldown
- Custom headers support for proxy / auth scenarios
- Custom `base_url` for on-premise / self-hosted gateways
- `diagnostics` property for runtime introspection
- Full type hints (PEP 561 compliant via `py.typed` marker)
- Single dependency: `httpx`
