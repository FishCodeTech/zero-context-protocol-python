#!/usr/bin/env python3
"""Official minimal MCP-compatible stdio server for ZCP."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from zcp import FastZCP, PromptArgument
from zcp.mcp_stdio import run_mcp_stdio_server_sync

app = FastZCP("ZCP MCP Compatibility Server")


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
            "humidity": {"type": "integer"},
        },
        "required": ["city", "unit", "temperature", "condition", "humidity"],
    },
    output_mode="scalar",
    inline_ok=True,
)
def get_weather(city: str, unit: str = "celsius", ctx=None) -> dict[str, object]:
    return {"city": city, "unit": unit, "temperature": 24, "condition": "Cloudy", "humidity": 67}


@app.resource("weather://cities", name="Cities", mime_type="application/json")
def cities():
    return ["Hangzhou", "Beijing", "Shanghai"]


@app.resource_template("weather://city/{name}", name="City Weather", mime_type="application/json")
def city_weather(uri: str):
    return {"uri": uri, "temperature": 24}


@app.prompt(
    name="weather.summary",
    description="Weather summary prompt.",
    arguments=[PromptArgument(name="city", required=True)],
)
def weather_prompt(city: str):
    return [{"role": "user", "content": f"Summarize weather for {city}"}]


@app.completion("weather.summary")
def complete_city(request):
    cities = ["Hangzhou", "Beijing", "Shanghai"]
    return [item for item in cities if item.lower().startswith(request.value.lower())]


if __name__ == "__main__":
    run_mcp_stdio_server_sync(app)
