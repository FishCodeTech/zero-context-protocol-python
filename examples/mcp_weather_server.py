#!/usr/bin/env python3
"""Reference MCP baseline server using the official MCP Python SDK.

Reference:
- MCP tools spec: https://modelcontextprotocol.io/specification/2025-06-18/server/tools
- MCP Python SDK quick example: https://py.sdk.modelcontextprotocol.io/

Run:
    uv run --with mcp examples/mcp_weather_server.py

Then connect with an MCP stdio-capable client or inspector.
"""

from __future__ import annotations

from typing import Any

from mcp.server import MCPServer

mcp = MCPServer("Weather MCP Server", log_level="ERROR")


def get_weather_data(city: str, unit: str = "celsius") -> dict[str, Any]:
    mock_weather = {
        "beijing": {"temperature": 18, "condition": "Sunny", "humidity": 35},
        "shanghai": {"temperature": 22, "condition": "Rain", "humidity": 81},
        "hangzhou": {"temperature": 24, "condition": "Cloudy", "humidity": 67},
        "shenzhen": {"temperature": 27, "condition": "Thunderstorms", "humidity": 84},
    }
    weather = mock_weather.get(city.strip().lower(), {"temperature": 20, "condition": "Unknown", "humidity": 50})
    return {
        "city": city,
        "unit": unit,
        **weather,
    }


@mcp.tool(
    title="Current Weather",
    structured_output=True,
)
def get_weather(city: str, unit: str = "celsius") -> dict[str, Any]:
    """Get the current weather for a city."""
    return get_weather_data(city=city, unit=unit)


if __name__ == "__main__":
    mcp.run(transport="stdio")
