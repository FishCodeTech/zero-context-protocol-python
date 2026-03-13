#!/usr/bin/env python3
"""Reference example for ZCP's OpenAI-compatible adapter against DeepSeek.

This example uses the ZCP runtime and the OpenAI-compatible adapter in auto mode:
- If the base_url supports `/responses`, it uses that.
- If the base_url does not support `/responses`, it falls back to `/chat/completions`.

DeepSeek currently documents tool calling on the chat completions endpoint, so this
script is directly useful with `https://api.deepseek.com`.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from openai import OpenAI

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from zcp import AgentLoop, CanonicalValidator, HandleStore, OpenAIAdapter, RuntimeExecutor, SessionState, ToolDefinition, ToolRegistry


def get_weather(arguments: dict[str, Any]) -> dict[str, Any]:
    city = arguments["city"].strip().lower()
    unit = arguments.get("unit", "celsius")
    mock_weather = {
        "beijing": {"temperature": 18, "condition": "Sunny", "humidity": 35},
        "shanghai": {"temperature": 22, "condition": "Rain", "humidity": 81},
        "hangzhou": {"temperature": 24, "condition": "Cloudy", "humidity": 67},
        "shenzhen": {"temperature": 27, "condition": "Thunderstorms", "humidity": 84},
    }
    weather = mock_weather.get(city, {"temperature": 20, "condition": "Unknown", "humidity": 50})
    return {
        "city": arguments["city"],
        "unit": unit,
        **weather,
    }


def build_runtime() -> tuple[AgentLoop, SessionState]:
    session = SessionState(session_id="deepseek-demo")
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            tool_id="17",
            alias="weather.get_current",
            description_short="Get the current weather for a city.",
            input_schema={
                "type": "object",
                "properties": {
                    "city": {"type": "string"},
                    "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]},
                },
                "required": ["city"],
                "additionalProperties": False,
            },
            defaults={"unit": "celsius"},
            handler=get_weather,
            output_mode="scalar",
            inline_ok=True,
            handle_kind="weather",
        )
    )
    executor = RuntimeExecutor(registry, CanonicalValidator(), HandleStore(session))
    adapter = OpenAIAdapter(registry, executor, api_style="auto")
    return AgentLoop(adapter), session


def safe_dump(value: Any) -> str:
    if value is None:
        return "null"
    if hasattr(value, "model_dump"):
        value = value.model_dump(exclude_none=True)
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def extract_visible_reasoning(raw_response: Any) -> list[Any]:
    items: list[Any] = []

    output = getattr(raw_response, "output", None)
    if output:
        for item in output:
            item_type = getattr(item, "type", None) if not isinstance(item, dict) else item.get("type")
            if item_type == "reasoning":
                items.append(item.model_dump(exclude_none=True) if hasattr(item, "model_dump") else item)

    choices = getattr(raw_response, "choices", None)
    if choices:
        for choice in choices:
            message = getattr(choice, "message", None) or (choice.get("message") if isinstance(choice, dict) else None)
            if message is None:
                continue
            for attr in ("reasoning", "reasoning_content", "reasoning_text"):
                value = getattr(message, attr, None) if not isinstance(message, dict) else message.get(attr)
                if value:
                    items.append({attr: value})
    return items


async def run_with_trace(loop: AgentLoop, client: OpenAI, session: SessionState) -> None:
    current_input: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": "You are a concise assistant. Use the weather tool for weather questions.",
        },
        {
            "role": "user",
            "content": "请帮我查询杭州现在的华氏度，并用中文简短回答。",
        },
    ]
    previous_response_id: str | None = None

    print("=== TRACE NOTICE ===")
    print("无法输出模型隐藏思维链；下面打印的是 provider 显式返回的 reasoning 字段、assistant 输出、工具选择和工具执行结果。")
    print()

    for round_index in range(1, loop.max_tool_rounds + 1):
        turn = await loop.adapter.run_turn(
            client,
            model="deepseek-chat",
            input_items=current_input,
            session=session,
            previous_response_id=previous_response_id,
        )

        print(f"=== ROUND {round_index} ===")
        print("endpoint:")
        print(turn.endpoint_used)

        print("raw_response:")
        print(safe_dump(turn.raw_response))

        print("visible_reasoning:")
        print(safe_dump(extract_visible_reasoning(turn.raw_response)))

        print("assistant_message:")
        print(safe_dump(turn.assistant_message))

        print("call_requests:")
        print(
            safe_dump(
                [
                    {
                        "cid": item.cid,
                        "tool_id": item.tool_id,
                        "alias": item.alias,
                        "arguments": item.arguments,
                        "raw_call_id": item.raw_call_id,
                    }
                    for item in turn.call_requests
                ]
            )
        )

        print("call_results:")
        print(
            safe_dump(
                [
                    {
                        "cid": item.cid,
                        "status": item.status,
                        "summary": item.summary,
                        "scalar": item.scalar,
                        "handle": item.handle.id if item.handle else None,
                        "error": item.error.code if item.error else None,
                        "raw_call_id": item.raw_call_id,
                    }
                    for item in turn.call_results
                ]
            )
        )

        print("submitted_outputs:")
        print(safe_dump(turn.submitted_outputs))

        if not turn.has_function_calls:
            print("final_output_text:")
            print(turn.final_output_text)
            return

        if turn.endpoint_used == "chat_completions":
            current_input = [
                *current_input,
                *([turn.assistant_message] if turn.assistant_message else []),
                *turn.submitted_outputs,
            ]
        else:
            current_input = turn.submitted_outputs
            previous_response_id = turn.response_id

    raise RuntimeError("max_tool_rounds exceeded")


async def main() -> None:
    client = OpenAI(
        api_key="sk-bdf3780f33b140c5aab7a66fe8459ca4",
        base_url="https://api.deepseek.com",
    )
    loop, session = build_runtime()
    await run_with_trace(loop, client, session)


if __name__ == "__main__":
    asyncio.run(main())
