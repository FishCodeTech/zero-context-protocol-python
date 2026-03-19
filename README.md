# Zero Context Protocol Python SDK

`zero-context-protocol-python` is the reference Python SDK and runtime for Zero
Context Protocol (ZCP).

It serves two goals at the same time:

- provide a native ZCP runtime that can optimize tool exposure, routing, and
  token usage for agent workflows
- remain compatible with MCP-facing integrations through stdio, streamable
  HTTP, and WebSocket surfaces

The public Python package remains:

- package name: `zcp`
- import path: `import zcp`

The companion protocol and documentation repository lives in
[`zero-context-protocol`](https://github.com/jiayuqi7813/zero-context-protocol).

## What This Repository Owns

- [`src/zcp`](src/zcp): official SDK, runtime, transports, gateway, auth, and profiles
- [`examples`](examples): public examples, migration paths, and benchmark entrypoints
- [`tests`](tests): SDK, MCP compatibility, transport, and benchmark regression coverage
- [`tools`](tools): local benchmark harnesses and benchmark suites
- [`benchmark_reports`](benchmark_reports): published benchmark artifacts

## Why ZCP Instead Of Plain MCP

ZCP keeps the MCP compatibility surface, but adds native runtime affordances
for model-facing efficiency:

- handle-first results and compact tool output shaping
- semantic workflow profiles for native tool discovery
- staged tool exposure for complex workflows
- task-aware runtime behavior
- benchmark-backed token reductions in real LLM scenarios

The latest published Excel benchmark lives in
[`benchmark_reports/full_semantic_compare_v5`](benchmark_reports/full_semantic_compare_v5/semantic_benchmark_summary.md).
Current headline result:

- overall native ZCP vs MCP surface: `8027.9` vs `30723.7` total tokens
- overall advantage: `3.83x`

## Install

```bash
pip install -e ".[dev,openai,mcp]"
```

Python `3.10+` is required.

## 3-Minute Quickstart

Run a minimal MCP-compatible stdio server:

```bash
python3 examples/run_zcp_mcp_stdio_server.py
```

Run an ASGI service exposing native and MCP-compatible surfaces:

```bash
python3 examples/run_zcp_api_server.py
```

Run the smallest native ZCP example:

```bash
python3 examples/zcp_weather_server.py
```

List native semantic workflow tools from a client:

```python
from zcp import SemanticWorkflowProfile

profile = SemanticWorkflowProfile()
tools = await client.list_tools(**profile.as_list_tools_params())
```

## Stable, Beta, Experimental

### Stable

- tools
- resources and resource templates
- prompts
- `completion/complete`
- MCP-compatible stdio
- MCP-compatible HTTP at `/mcp`
- native ZCP tool transport helpers
- bearer auth metadata and server wiring
- tool profiles and semantic workflow discovery

### Beta

- streamable HTTP resume/replay behavior
- WebSocket transport
- OAuth provider integration
- task-oriented tool execution

### Experimental

- advanced sampling / elicitation orchestration
- benchmark-specific semantic workflow adapters outside the public examples

## Repository Layout

- SDK/runtime: [`src/zcp`](src/zcp)
- examples: [`examples/README.md`](examples/README.md)
- tests: [`tests`](tests)
- benchmark reproduction: [`benchmark_reports/README.md`](benchmark_reports/README.md)

## Validation

Fast local validation:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q
```

The current repo subset used for release-focused validation includes transport,
SDK, gateway, and benchmark regression coverage.

## Benchmarks

Primary public benchmark entrypoints:

- compact tool-call benchmark:
  [`examples/compare_zcp_mcp_tool_call_benchmark.py`](examples/compare_zcp_mcp_tool_call_benchmark.py)
- Excel LLM token benchmark:
  [`examples/compare_excel_client_protocol_benchmark.py`](examples/compare_excel_client_protocol_benchmark.py)

Public benchmark guidance and official artifact selection:

- [`benchmark_reports/README.md`](benchmark_reports/README.md)

## Security Note

Benchmark and provider-backed examples require environment variables. Do not
commit API keys. Use [`.env.example`](.env.example) as the reference shape.

## License

Apache-2.0. See [`LICENSE`](LICENSE).
