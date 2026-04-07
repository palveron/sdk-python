# Changelog

All notable changes to `vexis-sdk` will be documented in this file.

This project follows [Semantic Versioning](https://semver.org/).

## [0.5.0] — 2026-04-07

### Added
- Framework integration hints in README (LangChain, CrewAI, OpenAI Agents SDK)
- Publish-ready README with badges for pypi.org
- Extended keyword list for discoverability

### Changed
- Version bump from 0.4.0 to 0.5.0 (SDK Publishing milestone — Sprint 21)

## [0.4.0] — 2026-03-15

### Added
- Multi-modal attachments (`Attachment.from_file()`, `Attachment.from_bytes()`)
- `verify_file()` convenience method with automatic MIME detection
- `RequestContext` for MCP/agentic context
- Circuit breaker with configurable threshold and cooldown
- `VexisCircuitOpenError` for circuit breaker state
- `diagnostics` property for runtime introspection
- Custom headers support

### Changed
- Retry logic now uses exponential backoff with jitter

## [0.3.0] — 2026-02-01

### Added
- `check()` convenience method for text-only verification
- `AsyncVexis` async client with full API parity
- `health()` endpoint
- Typed error hierarchy with structured metadata
- `retry_after_ms` on rate limit errors
- Convenience properties: `is_allowed`, `is_blocked`, `has_findings`

## [0.2.0] — 2026-01-15

### Added
- Retry with configurable max attempts
- Custom `base_url` for on-premise deployments
- `metadata` field on verify requests
- Request ID tracking

## [0.1.0] — 2025-12-01

### Added
- Initial release
- `Vexis` synchronous client
- `verify()` method with `VerifyRequest` / `VerifyResponse`
- Bearer token authentication