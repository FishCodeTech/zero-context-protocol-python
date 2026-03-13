# Zero Context Protocol Python SDK

This repository is the official Python SDK for Zero Context Protocol.

The public Python surface stays:

- package name: `zcp`
- import path: `import zcp`

The companion documentation and protocol site lives separately as
`zero-context-protocol`.

## What This Repository Owns

- `src/zcp`: the official Python SDK and runtime
- `examples`: official and reference Python examples
- `tests`: SDK, MCP compatibility, and runtime regression tests
- `tools`: repository-local tooling such as the real SDK benchmark harness
- `benchmark_reports`: generated benchmark artifacts that the docs site can consume

## Install

```bash
pip install -e ".[dev,openai,mcp]"
```

Python `3.10+` is required. The MCP extra uses the official MCP Python SDK.

## Official Paths

### MCP-compatible stdio server

```bash
python3 examples/run_zcp_mcp_stdio_server.py
```

### ASGI server with `/zcp` and `/mcp`

```bash
python3 examples/run_zcp_api_server.py
```

### Native ZCP example

```bash
python3 examples/zcp_weather_server.py
```

### Real SDK benchmark

```bash
python3 examples/compare_zcp_mcp_tool_call_benchmark.py --repeats 2
```

Generated reports land in `benchmark_reports/`.

## Validation

```bash
python3 -m pytest -q
```

## Alpha Boundary

Repo-level alpha guarantees focus on:

- `initialize`, `initialized`, `ping`
- tools
- resources
- prompts
- MCP-compatible stdio
- MCP-compatible HTTP at `/mcp`
- native ZCP transport helpers
- bearer auth on the ASGI surface

These remain partial or experimental unless separately contract-tested:

- logging parity details
- sampling
- elicitation
- tasks
- OAuth flows beyond exposed metadata

## Benchmarks

Use only the real SDK benchmark as the primary evidence source:

- script: `examples/compare_zcp_mcp_tool_call_benchmark.py`
- harness: `tools/benchmarking.py`
- reports: `benchmark_reports/zcp_mcp_tool_call_benchmark.json` and `.md`

The static payload comparison in `examples/compare_mcp_zcp_profiles.py` is only
reference material and is not the primary benchmark source.
