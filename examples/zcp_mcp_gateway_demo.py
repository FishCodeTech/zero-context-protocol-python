#!/usr/bin/env python3
"""Reference demo comparing native ZCP and MCP gateway calls against the same server."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from zcp import FastZCP, MCPGatewayClient, MCPGatewayServer, stdio_client, stdio_server

app = FastZCP("Gateway Demo")


@app.tool(
    name="weather.get_current",
    description="Get current weather for a city.",
    input_schema={
        "type": "object",
        "properties": {
            "city": {"type": "string"},
        },
        "required": ["city"],
        "additionalProperties": False,
    },
    output_mode="scalar",
    inline_ok=True,
)
def get_weather(city: str) -> dict[str, object]:
    return {"city": city, "temperature": 24, "condition": "Cloudy"}


async def main() -> None:
    server = stdio_server(app)
    client = stdio_client(server)
    gateway_server = MCPGatewayServer(server)
    gateway_client = MCPGatewayClient(client)

    print("native zcp:")
    print(json.dumps(await client.list_tools(), ensure_ascii=False, indent=2))
    print(json.dumps(await client.call_tool("weather.get_current", {"city": "Hangzhou"}), ensure_ascii=False, indent=2))

    print("mcp gateway client:")
    print(json.dumps(await gateway_client.list_tools(), ensure_ascii=False, indent=2))
    print(json.dumps(await gateway_client.call_tool("weather.get_current", {"city": "Hangzhou"}), ensure_ascii=False, indent=2))

    print("mcp gateway server:")
    print(
        json.dumps(
            await gateway_server.handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "weather.get_current", "arguments": {"city": "Hangzhou"}},
                }
            ),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
