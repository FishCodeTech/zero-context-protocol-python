#!/usr/bin/env python3
"""Server template for a user-implemented ZCP backend.

Run with:
    uvicorn examples.zcp_server_template:application --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from zcp import (
    AuthProfile,
    BearerAuthConfig,
    FastZCP,
    PromptArgument,
    RateLimitConfig,
    ZCPServerConfig,
    create_asgi_app,
)

app = FastZCP(
    "Weather Backend Template",
    version="1.0.0",
    instructions="Example user-owned ZCP backend with tools, resources, prompts, completions, tasks, and auth metadata.",
    auth_profile=AuthProfile(
        issuer=os.environ.get("ZCP_AUTH_ISSUER", "https://auth.example.com"),
        authorization_url=os.environ.get("ZCP_AUTHORIZATION_URL", "https://auth.example.com/oauth/authorize"),
        token_url=os.environ.get("ZCP_TOKEN_URL", "https://auth.example.com/oauth/token"),
        scopes=["weather.read", "weather.admin"],
    ),
)


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
    required_scopes=("weather.read",),
)
def get_weather(city: str, unit: str = "celsius", ctx=None):
    return {"city": city, "unit": unit, "temperature": 24, "condition": "Cloudy", "humidity": 67}


@app.resource(
    "weather://cities",
    name="Supported Cities",
    mime_type="application/json",
    required_scopes=("weather.read",),
)
def supported_cities():
    return ["Hangzhou", "Beijing", "Shanghai", "Shenzhen"]


@app.prompt(
    name="weather.summary",
    description="Build a user-facing weather summary prompt.",
    arguments=[PromptArgument(name="city", required=True), PromptArgument(name="temperature")],
    required_scopes=("weather.read",),
)
def weather_prompt(city: str, temperature: str | None = None):
    return [
        {"role": "system", "content": "You summarize weather clearly and briefly."},
        {"role": "user", "content": f"Summarize the weather for {city}. Temperature: {temperature or 'unknown'}."},
    ]


@app.completion("city")
def complete_city(request):
    names = ["Hangzhou", "Beijing", "Shanghai", "Shenzhen"]
    return [item for item in names if item.lower().startswith(request.value.lower())]


@app.task("weather.refresh")
def refresh_weather(payload):
    return {"status": "refreshed", "city": payload["city"]}


application = create_asgi_app(
    app,
    config=ZCPServerConfig(
        service_name="zcp-weather",
        environment="production",
        auth=BearerAuthConfig(token=os.environ.get("ZCP_BEARER_TOKEN", "demo-token")),
        rate_limit=RateLimitConfig(window_seconds=60, max_requests=240),
    ),
)
