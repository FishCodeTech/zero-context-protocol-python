# Examples

This directory intentionally keeps only the public entrypoints that are useful
to new users.

## Minimal

- `weather_zcp_server.py`: smallest native ZCP weather server (`/zcp`)
- `weather_zcp_client.py`: matching native ZCP client using a prompt-driven OpenAI-compatible tool loop
- `weather_mcp_server.py`: smallest MCP-compatible stdio weather server
- `weather_mcp_client.py`: matching official MCP client using a prompt-driven OpenAI-compatible tool loop

If you want to run the prompt-driven client examples, install the optional dependencies first:

```bash
pip install "zero-context-protocol-sdk[mcp,openai]"
```

The client examples expect one of `OPENAI_API_KEY` or `DEEPSEEK_API_KEY`, plus optional
`OPENAI_BASE_URL` and `OPENAI_MODEL`.

## Production-shaped

- `run_zcp_api_server.py`: ASGI host exposing `/zcp`, `/mcp`, and `/ws`
- `zcp_server_template.py`: reference service template for a real backend

## Benchmarks

- `compare_zcp_mcp_tool_call_benchmark.py`: official compact tool-call token benchmark
- `compare_excel_client_protocol_benchmark.py`: official Excel LLM token benchmark

Internal benchmark baselines and helpers now live under `tools/`.
