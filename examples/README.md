# Examples

This directory intentionally keeps only the public entrypoints that are useful
to new users.

## Minimal

- `zcp_weather_server.py`: smallest native ZCP server
- `run_zcp_mcp_stdio_server.py`: smallest MCP-compatible stdio server

## Production-shaped

- `run_zcp_api_server.py`: ASGI host exposing `/zcp`, `/mcp`, and `/ws`
- `zcp_server_template.py`: reference service template for a real backend

## Benchmarks

- `compare_zcp_mcp_tool_call_benchmark.py`: official compact tool-call token benchmark
- `compare_excel_client_protocol_benchmark.py`: official Excel LLM token benchmark

Internal benchmark baselines and helpers now live under `tools/`.
