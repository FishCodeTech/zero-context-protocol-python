#!/usr/bin/env python3
"""Native ZCP weather server example."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from zcp import FastZCP, PromptArgument, streamable_http_client, streamable_http_server

app = FastZCP("Weather ZCP Server")


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
    output_mode="scalar",
    inline_ok=True,
)
def get_weather(city: str, unit: str = "celsius") -> dict[str, object]:
    mock = {
        "hangzhou": {"temperature": 24, "condition": "Cloudy", "humidity": 67},
        "beijing": {"temperature": 18, "condition": "Sunny", "humidity": 35},
    }
    return {
        "city": city,
        "unit": unit,
        **mock.get(city.strip().lower(), {"temperature": 20, "condition": "Unknown", "humidity": 50}),
    }


@app.resource("weather://cities", name="Supported Cities", mime_type="application/json")
def list_cities() -> list[str]:
    return ["Hangzhou", "Beijing"]


@app.prompt(
    name="weather.summary",
    description="Prompt template for summarizing weather results.",
    arguments=[PromptArgument(name="city", required=True), PromptArgument(name="temperature")],
)
def weather_summary(city: str, temperature: str | None = None) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": "You summarize weather briefly in Chinese.",
        },
        {
            "role": "user",
            "content": f"请总结 {city} 的天气，温度 {temperature or '未知'}。",
        },
    ]


@app.completion("city")
def complete_city(request) -> list[str]:
    candidates = ["Hangzhou", "Beijing", "Shanghai", "Shenzhen"]
    prefix = request.value.lower()
    return [item for item in candidates if item.lower().startswith(prefix)]


async def main() -> None:
    server = streamable_http_server(app, endpoint="http://127.0.0.1:8000/zcp")
    client = streamable_http_client(server, roots_provider=lambda: [{"uri": "file:///workspace", "name": "workspace"}])
    print(json.dumps(await client.initialize(), ensure_ascii=False, indent=2))
    print(json.dumps(await client.list_tools(), ensure_ascii=False, indent=2))
    print(json.dumps(await client.call_tool("weather.get_current", {"city": "Hangzhou"}), ensure_ascii=False, indent=2))
    print(json.dumps(await client.list_resources(), ensure_ascii=False, indent=2))
    print(json.dumps(await client.get_prompt("weather.summary", {"city": "Hangzhou", "temperature": "24"}) , ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
