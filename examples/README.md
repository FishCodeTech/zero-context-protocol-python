# Examples

Examples are grouped by support level.

## Official

- `run_zcp_mcp_stdio_server.py`: minimal MCP-compatible stdio server
- `run_zcp_api_server.py`: ASGI host exposing `/zcp` and `/mcp`
- `zcp_server_template.py`: official backend template
- `zcp_weather_server.py`: native ZCP example
- `compare_zcp_mcp_tool_call_benchmark.py`: real SDK benchmark

## Reference / Experimental

- `mcp_weather_server.py`: official MCP SDK baseline for comparison
- `real_sdk_mcp_server.py`: MCP baseline server used by the benchmark harness
- `zcp_mcp_gateway_demo.py`: gateway shape comparison demo
- `compare_mcp_zcp_profiles.py`: static payload comparison only
- `deepseek_chat_tool_call_example.py`: model/provider-specific adapter trace
- `run_mcp_tool_call_benchmark.py`: MCP-only benchmark entrypoint
- `run_zcp_tool_call_benchmark.py`: ZCP-only benchmark entrypoint
