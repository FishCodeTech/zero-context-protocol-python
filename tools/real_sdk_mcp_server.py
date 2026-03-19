#!/usr/bin/env python3
from __future__ import annotations

from typing import Any

from mcp.server import MCPServer

server = MCPServer("benchmark-mcp", log_level="ERROR")

WEATHER_DATA: dict[str, dict[str, Any]] = {
    "beijing": {"temperature_c": 18.0, "condition": "Sunny", "humidity": 35},
    "shanghai": {"temperature_c": 22.0, "condition": "Rain", "humidity": 81},
    "hangzhou": {"temperature_c": 24.0, "condition": "Cloudy", "humidity": 67},
    "shenzhen": {"temperature_c": 27.0, "condition": "Thunderstorms", "humidity": 84},
}


def _canonical_city(value: str) -> str:
    return value.strip().lower()


def _title_city(value: str) -> str:
    return value.strip().title()


@server.tool(name="get_weather", description="Get the current weather for one city.", structured_output=True)
def get_weather(city: str, unit: str = "celsius") -> dict[str, Any]:
    data = WEATHER_DATA[_canonical_city(city)]
    temperature_c = float(data["temperature_c"])
    temperature = temperature_c if unit == "celsius" else round((temperature_c * 9 / 5) + 32, 1)
    return {
        "city": _title_city(city),
        "unit": unit,
        "temperature": temperature,
        "condition": data["condition"],
        "humidity": int(data["humidity"]),
    }


@server.tool(name="subtract_numbers", description="Subtract b from a.", structured_output=True)
def subtract_numbers(a: float, b: float) -> dict[str, float]:
    return {"result": round(float(a) - float(b), 1)}


@server.tool(name="average_numbers", description="Average a list of numbers.", structured_output=True)
def average_numbers(values: list[float]) -> dict[str, float]:
    parsed = [float(value) for value in values]
    return {"result": round(sum(parsed) / len(parsed), 1)}


@server.tool(name="convert_celsius_to_fahrenheit", description="Convert Celsius to Fahrenheit.", structured_output=True)
def convert_celsius_to_fahrenheit(celsius: float) -> dict[str, float]:
    return {"result": round((float(celsius) * 9 / 5) + 32, 1)}


if __name__ == "__main__":
    server.run(transport="stdio")
