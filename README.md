# vexis-sdk

Official Python SDK for the **VEXIS AI Governance Platform** — guardrails, audit trails, and blockchain attestation for every AI interaction.

[![PyPI version](https://img.shields.io/pypi/v/vexis-sdk.svg?style=flat-square)](https://pypi.org/project/vexis-sdk/)
[![Python](https://img.shields.io/pypi/pyversions/vexis-sdk.svg?style=flat-square)](https://pypi.org/project/vexis-sdk/)
[![CI](https://img.shields.io/github/actions/workflow/status/disruptivetrends/vexis-sdk-python/ci.yml?style=flat-square)](https://github.com/disruptivetrends/vexis-sdk-python/actions)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg?style=flat-square)](https://opensource.org/licenses/Apache-2.0)
[![Docs](https://img.shields.io/badge/docs-vexis.io-5A67D8?style=flat-square)](https://docs.vexis.io/sdk/python)

---

- **Sync + Async** — `Vexis` for synchronous code, `AsyncVexis` for asyncio
- **Fully typed** — `py.typed`, strict mypy, dataclass responses
- **Multi-modal** — text, images, audio, documents, code
- **Enterprise-grade** — retry with backoff, circuit breaker, typed errors
- **On-prem ready** — point to any VEXIS Gateway endpoint

## Installation

```bash
pip install vexis-sdk
```

For HTTP/2 support (optional):

```bash
pip install "vexis-sdk[async]"
```

## Quick Start

```python
from vexis import Vexis

client = Vexis(api_key="gp_live_xxx")

# Verify any prompt before sending it to an LLM
result = client.verify("Transfer $50,000 to account DE89370400440532013000")

if result.is_blocked:
    raise RuntimeError(f"Blocked: {result.reason}")

# result.decision is Decision.ALLOWED or Decision.MODIFIED
# result.output contains the (possibly sanitized) text
# result.trace_id links to the immutable audit trail
```

### Async Usage

```python
import asyncio
from vexis import AsyncVexis

async def main():
    async with AsyncVexis(api_key="gp_live_xxx") as client:
        result = await client.verify("Check this prompt")
        print(result.decision)

asyncio.run(main())
```

## API Reference

### Constructor

```python
client = Vexis(
    api_key="gp_live_xxx",                     # Required — project or agent API key
    base_url="https://gateway.vexis.io",       # Custom endpoint for on-prem
    timeout=30.0,                               # Request timeout in seconds
    max_retries=3,                              # Retry attempts on transient failures
    headers={"X-Tenant": "acme"},              # Custom headers on every request
    circuit_threshold=5,                        # Failures before circuit opens
    circuit_cooldown=30.0,                      # Cooldown before half-open retry
)
```

### `verify(request)` — Core governance check

```python
from vexis import VerifyRequest, Attachment, RequestContext

result = client.verify(VerifyRequest(
    prompt="Transfer funds to external account",
    metadata={"user_id": "u_123", "department": "finance"},
    attachments=[
        Attachment.from_file("./contract.pdf"),
        Attachment.from_bytes(image_bytes, "image/png", "screenshot.png"),
    ],
    context=RequestContext(
        mcp_server="https://mcp.internal.corp",
        tool_name="bank_transfer",
        chain_depth=2,
        source_system="crewai",
        session_id="sess_abc",
    ),
))
```

**Returns `VerifyResponse`:**

| Field | Type | Description |
|-------|------|-------------|
| `decision` | `Decision` | `ALLOWED`, `BLOCKED`, `MODIFIED`, or `ERROR` |
| `output` | `str` | Sanitized output (PII redacted if MODIFIED) |
| `reason` | `str` | Human-readable explanation |
| `trace_id` | `str` | Unique audit trail ID |
| `integrity_hash` | `str` | SHA-256 hash for tamper detection |
| `should_anchor` | `bool` | Whether trace will be anchored to Flare blockchain |
| `flare_status` | `str` | `LOCAL_ONLY`, `PENDING`, `ANCHORED`, `SKIPPED`, `FAILED` |
| `flare_tx_hash` | `str \| None` | Blockchain transaction hash (after anchoring) |
| `content_type` | `str` | Detected content type |
| `findings` | `list[Finding]` | Security findings (PII, secrets, policy violations) |
| `latency_ms` | `float` | Round-trip latency in milliseconds |

**Convenience properties:**

```python
result.is_allowed    # True if decision == ALLOWED
result.is_blocked    # True if decision == BLOCKED
result.has_findings  # True if findings list is non-empty
```

### `check(prompt)` — Quick text-only verification

```python
result = client.check("Is this prompt safe?")
```

### `verify_file(prompt, path)` — File attachment

```python
result = client.verify_file("Analyze this document", "/path/to/report.pdf")
```

### `health()` — Gateway health check

```python
health = client.health()
print(health.status)  # 'healthy'
```

### `diagnostics` — SDK diagnostics (property)

```python
diag = client.diagnostics
# {'sdk_version': '0.5.0', 'base_url': '...', 'timeout': 30.0, 'circuit_state': 'closed'}
```

## Error Handling

All errors inherit from `VexisError` with structured metadata:

```python
from vexis import VexisError, VexisRateLimitError

try:
    result = client.verify("User input")
except VexisRateLimitError as e:
    # e.retry_after_ms — wait this long before retrying
    time.sleep(e.retry_after_ms / 1000)
    result = client.verify("User input")  # retry
except VexisError as e:
    print(e.code, e.status_code, e.request_id)
```

| Error Class | Code | Retryable | When |
|-------------|------|:---------:|------|
| `VexisAuthenticationError` | `AUTHENTICATION_FAILED` | No | Invalid or expired API key |
| `VexisRateLimitError` | `RATE_LIMITED` | Yes | Quota exceeded (includes `retry_after_ms`) |
| `VexisValidationError` | `VALIDATION_ERROR` | No | Malformed request (includes `field`) |
| `VexisTimeoutError` | `TIMEOUT` | Yes | Gateway didn't respond in time |
| `VexisCircuitOpenError` | `CIRCUIT_OPEN` | No | Too many consecutive failures |

## Context Manager

Both clients support context managers for clean resource management:

```python
# Sync
with Vexis(api_key="gp_live_xxx") as client:
    result = client.verify("Check this")

# Async
async with AsyncVexis(api_key="gp_live_xxx") as client:
    result = await client.verify("Check this")
```

## Framework Integration

VEXIS works with any LLM framework. Dedicated adapters with deeper integration:

| Framework | Package | Integration |
|-----------|---------|-------------|
| LangChain | `vexis-langchain` | `VexisCallbackHandler` — automatic governance on every LLM call |
| CrewAI | `vexis-crewai` | `VexisGovernance` plugin — task-level governance per crew |
| OpenAI Agents SDK | `vexis-openai-agents` | Middleware hook for the official OpenAI framework |

## On-Premise / Self-Hosted

```python
client = Vexis(
    api_key="gp_live_xxx",
    base_url="https://gateway.internal.acme.corp:8080",
    timeout=10.0,
    max_retries=5,
)
```

## Requirements

- Python 3.9+
- `httpx` (automatically installed)

## Links

- [Documentation](https://docs.vexis.io/sdk/python)
- [API Reference](https://docs.vexis.io/api)
- [Dashboard](https://app.vexis.io)
- [GitHub](https://github.com/disruptivetrends/vexis-sdk-python)
- [Changelog](https://github.com/disruptivetrends/vexis-sdk-python/blob/main/CHANGELOG.md)

## License

[Apache 2.0](./LICENSE)