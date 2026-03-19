#!/usr/bin/env python3
"""Minimal prompt-driven ZCP client for the native weather server example."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from examples.weather_zcp_server import app
from zcp import streamable_http_client, streamable_http_server

try:
    from openai import OpenAI
except ModuleNotFoundError as exc:  # pragma: no cover
    raise RuntimeError("Install the optional OpenAI dependency first: pip install 'zero-context-protocol-sdk[openai]'") from exc


def _model_client() -> OpenAI:
    api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("Set OPENAI_API_KEY or DEEPSEEK_API_KEY before running this example.")
    return OpenAI(
        api_key=api_key,
        base_url=os.environ.get("OPENAI_BASE_URL", "https://api.deepseek.com"),
    )


def _tool_spec(tool: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool.get("description") or "",
            "parameters": tool["inputSchema"],
            "strict": True,
        },
    }


async def run(query: str) -> dict[str, object]:
    client = streamable_http_client(streamable_http_server(app, endpoint="http://127.0.0.1:8000/zcp"))
    init = await client.initialize()
    await client.initialized()
    tools = await client.list_tools()
    llm = _model_client()
    model = os.environ.get("OPENAI_MODEL", "deepseek-chat")
    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": "You are a helpful weather assistant. Use tools when the user asks about weather. Keep the final answer concise.",
        },
        {
            "role": "user",
            "content": query,
        },
    ]
    tool_specs = [_tool_spec(tool) for tool in tools["tools"]]
    tool_log: list[dict[str, Any]] = []

    for _ in range(4):
        response = llm.chat.completions.create(
            model=model,
            messages=messages,
            tools=tool_specs,
            tool_choice="auto",
        )
        message = response.choices[0].message
        assistant_payload = message.model_dump(exclude_none=True)
        messages.append(assistant_payload)
        tool_calls = message.tool_calls or []
        if not tool_calls:
            return {
                "initialize": init,
                "tool_names": [tool["name"] for tool in tools["tools"]],
                "tool_log": tool_log,
                "answer": message.content,
            }
        for tool_call in tool_calls:
            arguments = json.loads(tool_call.function.arguments or "{}")
            result = await client.call_tool(tool_call.function.name, arguments)
            payload = result.get("structuredContent")
            if payload is None:
                payload = {"content": result.get("content", [])}
            tool_log.append({"name": tool_call.function.name, "arguments": arguments, "result": payload})
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": json.dumps(payload, ensure_ascii=False),
                }
            )
    return {
        "initialize": init,
        "tool_names": [tool["name"] for tool in tools["tools"]],
        "tool_log": tool_log,
        "answer": "Model did not finish within the maximum number of tool rounds.",
    }


def main() -> None:
    query = " ".join(sys.argv[1:]) or "请查询 Hangzhou 当前天气，并用一句话总结。"
    print(json.dumps(asyncio.run(run(query)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
