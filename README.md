# palveron-sdk

Official Python SDK for the **Palveron AI Governance Gateway** — policy enforcement, trace verification, and blockchain-anchored audit trails for every AI interaction.

[![PyPI version](https://img.shields.io/pypi/v/palveron-sdk.svg?style=flat-square&color=cb3837)](https://pypi.org/project/palveron-sdk/)
[![Python versions](https://img.shields.io/pypi/pyversions/palveron-sdk.svg?style=flat-square)](https://pypi.org/project/palveron-sdk/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg?style=flat-square)](https://opensource.org/licenses/MIT)
[![Documentation](https://img.shields.io/badge/docs-palveron.com-5A67D8?style=flat-square)](https://docs.palveron.com/sdks)

---

Every AI interaction your application makes — governed, audited, and optionally anchored to the blockchain. In one import.

- **One dependency** (`httpx`) — clean install, no surprises
- **Sync + Async** — `Palveron` and `AsyncPalveron` clients with the same surface
- **Multi-modal** — text, images, audio, documents, code
- **Enterprise-grade** — retry with exponential backoff, circuit breaker, typed errors
- **On-prem ready** — point to any Palveron Gateway endpoint
- **Typed** — full type hints, ships with `py.typed`

## Installation

```bash
pip install palveron-sdk
```

## Quick Start

```python
from palveron import Palveron

client = Palveron(api_key="pv_live_xxx")

result = client.verify("Transfer $50,000 to account DE89370400440532013000")

if result.is_blocked:
    raise RuntimeError(f"Blocked by policy: {result.reason}")

# result.output is the (possibly sanitized) prompt — always use it
# instead of the raw input so downstream LLMs never see PII / secrets.
print(result.output, result.trace_id)
```

## Async Support

```python
import asyncio
from palveron import AsyncPalveron

async def main():
    async with AsyncPalveron(api_key="pv_live_xxx") as client:
        result = await client.verify("Is this safe?")
        print(result.decision)

asyncio.run(main())
```

## Features

- **Policy Enforcement** — every prompt routed through your active guardrails before it reaches an LLM
- **Trace Verification** — every decision logged with an integrity hash for tamper detection
- **Multi-modal attachments** — `Attachment.from_file()` and `Attachment.from_bytes()` with auto MIME detection
- **Agentic / MCP context** — pass `RequestContext` so the audit trail records the tool chain, not just the prompt
- **Blockchain Attestation** — high-severity traces anchored to Flare for cryptographic audit trails
- **EU AI Act / DORA / GDPR / NIST AI RMF** — compliance-ready audit fields out of the box

## Configuration

```python
client = Palveron(
    api_key="pv_live_xxx",                   # Required — project or agent API key
    base_url="https://gateway.palveron.com", # Custom endpoint for on-prem
    timeout=30.0,                            # Request timeout in seconds
    max_retries=3,                           # Retry attempts on transient failures
    headers={"X-Tenant": "acme"},            # Custom headers on every request
    circuit_threshold=5,                     # Failures before circuit opens
    circuit_cooldown=30.0,                   # Cooldown before half-open retry (seconds)
)
```

## API Reference

Full reference at **[docs.palveron.com/sdks](https://docs.palveron.com/sdks)**. Quick summary:

| Method | Description |
|--------|-------------|
| `verify(prompt, *, attachments=None, context=None, metadata=None)` | Core governance check. Returns `VerifyResponse`. |
| `check(prompt)` | Quick text-only verification — convenience wrapper around `verify`. |
| `verify_file(prompt, path)` | Read a local file, base64-encode it, send it as an attachment. |
| `list_policies(env="prod")` | List all active policies for the project. |
| `health()` | Gateway health-check endpoint. |
| `diagnostics` | Property — returns SDK version, base URL, timeout, retry config, circuit state. |

## Examples

### LLM gateway with governance (OpenAI)

```python
from palveron import Palveron
from openai import OpenAI

palveron = Palveron(api_key=os.environ["PALVERON_API_KEY"])
openai = OpenAI()

def ask_with_governance(user_prompt: str) -> str:
    gate = palveron.verify(user_prompt)
    if gate.is_blocked:
        raise RuntimeError(f"Blocked by policy: {gate.reason}")
    # Always use gate.output instead of the raw input so the LLM never
    # sees PII / secrets the gateway redacted.
    resp = openai.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": gate.output}],
    )
    return resp.choices[0].message.content
```

### Agentic / MCP audit context

```python
from palveron import Palveron, RequestContext

client = Palveron(api_key=os.environ["PALVERON_API_KEY"])

result = client.verify(
    "Execute bank transfer",
    context=RequestContext(
        mcp_server="https://banking-mcp.corp.internal",
        tool_name="transfer_funds",
        chain_depth=3,
        source_system="crewai",
        session_id="agent_session_42",
    ),
)
```

### On-premise / self-hosted gateway

```python
client = Palveron(
    api_key=os.environ["PALVERON_API_KEY"],
    base_url="https://gateway.internal.acme.corp:8080",
    timeout=10.0,
    max_retries=5,
)
```

## Error Handling

All errors extend `PalveronError` with structured metadata:

```python
from palveron import Palveron, PalveronError, PalveronRateLimitError
import time

client = Palveron(api_key=os.environ["PALVERON_API_KEY"])

try:
    client.verify(user_input)
except PalveronRateLimitError as err:
    # err.retry_after_ms — wait this long before retrying
    time.sleep(err.retry_after_ms / 1000)
except PalveronError as err:
    print(err.code, err.status_code, err.request_id)
```

| Error Class | Code | Retryable | When |
|-------------|------|:---------:|------|
| `PalveronAuthenticationError` | `AUTHENTICATION_FAILED` | No | Invalid or expired API key |
| `PalveronRateLimitError` | `RATE_LIMITED` | Yes | Quota exceeded (includes `retry_after_ms`) |
| `PalveronValidationError` | `VALIDATION_ERROR` | No | Malformed request (includes `field`) |
| `PalveronTimeoutError` | `TIMEOUT` | Yes | Gateway didn't respond in time |
| `PalveronCircuitOpenError` | `CIRCUIT_OPEN` | No | Too many consecutive failures |

## Requirements

- **Python 3.8 or newer**
- Single runtime dependency: `httpx`

## Links

- **Documentation** — [docs.palveron.com](https://docs.palveron.com)
- **SDK reference** — [docs.palveron.com/sdks](https://docs.palveron.com/sdks)
- **Dashboard** — [palveron.com](https://palveron.com)
- **Support** — [hello@palveron.com](mailto:hello@palveron.com)
- **GitHub** — [palveron/sdk-python](https://github.com/palveron/sdk-python)
- **Changelog** — [CHANGELOG.md](https://github.com/palveron/sdk-python/blob/main/CHANGELOG.md)

## License

[MIT](./LICENSE) — Copyright © 2026 Palveron.
