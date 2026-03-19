#!/usr/bin/env python3
"""Minimal native ZCP weather server."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from zcp import FastZCP, create_asgi_app


app = FastZCP("Weather ZCP Server", version="0.1.0")


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
    metadata={"groups": ["workflow", "weather"], "stages": ["operate"]},
)
def get_current_weather(city: str, unit: str = "celsius") -> dict[str, object]:
    base = {
        "hangzhou": {"temperature": 24, "condition": "Cloudy"},
        "beijing": {"temperature": 18, "condition": "Sunny"},
        "shanghai": {"temperature": 22, "condition": "Rainy"},
    }
    payload = base.get(city.strip().lower(), {"temperature": 20, "condition": "Unknown"})
    return {"city": city, "unit": unit, **payload}


application = create_asgi_app(app)


def main() -> None:
    try:
        import uvicorn
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise RuntimeError("uvicorn is required to run the native ZCP weather server") from exc

    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(application, host=host, port=port, log_level="warning", ws="auto", lifespan="off")


if __name__ == "__main__":
    main()
