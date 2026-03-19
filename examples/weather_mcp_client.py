#!/usr/bin/env python3
"""Minimal prompt-driven official MCP client for the weather server example."""

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

SERVER_SCRIPT = ROOT / "examples" / "weather_mcp_server.py"

try:
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
except ModuleNotFoundError as exc:  # pragma: no cover
    raise RuntimeError("Install the optional MCP dependency first: pip install 'zero-context-protocol-sdk[mcp]'") from exc

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


def _tool_spec(tool: Any) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description or "",
            "parameters": tool.inputSchema,
            "strict": True,
        },
    }


async def run(query: str) -> dict[str, object]:
    async with stdio_client(
        StdioServerParameters(
            command=sys.executable,
            args=[str(SERVER_SCRIPT)],
            cwd=str(ROOT),
        )
    ) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tools = await session.list_tools()
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
            tool_specs = [_tool_spec(tool) for tool in tools.tools]
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
                        "tool_names": [tool.name for tool in tools.tools],
                        "tool_log": tool_log,
                        "answer": message.content,
                    }
                for tool_call in tool_calls:
                    arguments = json.loads(tool_call.function.arguments or "{}")
                    result = await session.call_tool(tool_call.function.name, arguments)
                    payload: Any = result.structured_content
                    if payload is None and result.content:
                        payload = {"content": [block.text for block in result.content]}
                    tool_log.append({"name": tool_call.function.name, "arguments": arguments, "result": payload})
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": json.dumps(payload, ensure_ascii=False),
                        }
                    )
            return {
                "tool_names": [tool.name for tool in tools.tools],
                "tool_log": tool_log,
                "answer": "Model did not finish within the maximum number of tool rounds.",
            }


def main() -> None:
    query = " ".join(sys.argv[1:]) or "请查询 Hangzhou 当前天气，并用一句话总结。"
    print(json.dumps(asyncio.run(run(query)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
