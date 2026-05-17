# Changelog

All notable changes to `palveron-sdk` will be documented in this file.

This project follows [Semantic Versioning](https://semver.org/).

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
