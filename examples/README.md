# Examples

The public examples are grouped by intent so new users can find the right entry
point quickly.

## Minimal

- `zcp_weather_server.py`: smallest native ZCP server
- `run_zcp_mcp_stdio_server.py`: smallest MCP-compatible stdio server

## Production-shaped

- `run_zcp_api_server.py`: ASGI host exposing `/zcp`, `/mcp`, and `/ws`
- `zcp_server_template.py`: reference service template for a real backend
- `zcp_mcp_gateway_demo.py`: gateway demo for MCP compatibility layering

## Migration / Comparison

- `mcp_weather_server.py`: official MCP-style baseline
- `real_sdk_mcp_server.py`: MCP baseline server used in benchmark harnesses
- `compare_mcp_zcp_profiles.py`: static payload/profile comparison

## Benchmarks

- `compare_zcp_mcp_tool_call_benchmark.py`: official compact tool-call token benchmark
- `compare_excel_client_protocol_benchmark.py`: official Excel LLM token benchmark
- `run_mcp_tool_call_benchmark.py`: MCP-only benchmark entrypoint
- `run_zcp_tool_call_benchmark.py`: ZCP-only benchmark entrypoint

## Provider-specific diagnostics

- `deepseek_chat_tool_call_example.py`: DeepSeek/OpenAI-compatible adapter trace example
