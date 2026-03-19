#!/usr/bin/env python3
"""Minimal MCP-compatible weather server backed by ZCP."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from zcp import FastZCP
from zcp.mcp_stdio import run_mcp_stdio_server_sync


app = FastZCP("Weather MCP Compatibility Server", version="0.1.0")


@app.tool(
    name="weather.get_current",
    description="Get the current weather for a city.",
    input_schema={
        "type": "object",
        "properties": {
            "city": {"type": "string"},
            "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]},
        },
        "required": ["city"],
        "additionalProperties": False,
    },
    output_schema={
        "type": "object",
        "properties": {
            "city": {"type": "string"},
            "unit": {"type": "string"},
            "temperature": {"type": "integer"},
            "condition": {"type": "string"},
        },
        "required": ["city", "unit", "temperature", "condition"],
        "additionalProperties": False,
    },
    output_mode="scalar",
    inline_ok=True,
)
def get_current_weather(city: str, unit: str = "celsius") -> dict[str, object]:
    base = {
        "hangzhou": {"temperature": 24, "condition": "Cloudy"},
        "beijing": {"temperature": 18, "condition": "Sunny"},
        "shanghai": {"temperature": 22, "condition": "Rainy"},
    }
    payload = base.get(city.strip().lower(), {"temperature": 20, "condition": "Unknown"})
    return {"city": city, "unit": unit, **payload}


def main() -> None:
    run_mcp_stdio_server_sync(app)


if __name__ == "__main__":
    main()
