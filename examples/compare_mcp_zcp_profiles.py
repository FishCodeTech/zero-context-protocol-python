#!/usr/bin/env python3
"""Reference-only static payload comparison between MCP-style and ZCP profiles.

This script is a shape-level payload comparison only. For real SDK benchmarking,
use `examples/compare_zcp_mcp_tool_call_benchmark.py`.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from zcp import CallResult, SessionState, ToolDefinition, ToolRegistry, compile_openai_tools, submit_tool_results


def count_metrics(payload: str) -> dict[str, int]:
    byte_len = len(payload.encode("utf-8"))
    char_len = len(payload)
    approx_tokens = math.ceil(byte_len / 4)
    return {
        "bytes": byte_len,
        "chars": char_len,
        "approx_tokens": approx_tokens,
    }


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def build_weather_tool() -> ToolDefinition:
    return ToolDefinition(
        tool_id="17",
        alias="weather.get_current",
        description_short="Get the current weather for a city.",
        input_schema={
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "City name such as Hangzhou or Beijing.",
                },
                "unit": {
                    "type": "string",
                    "description": "Temperature unit.",
                    "enum": ["celsius", "fahrenheit"],
                },
            },
            "required": ["city"],
            "additionalProperties": False,
        },
        defaults={"unit": "celsius"},
        output_mode="scalar",
        inline_ok=True,
        handle_kind="weather",
    )


def build_registry() -> tuple[ToolRegistry, Any]:
    registry = ToolRegistry()
    registry.register(build_weather_tool())
    return registry, registry.subset(["weather.get_current"])


def traditional_mcp_payloads() -> dict[str, str]:
    tools_list_result = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "tools": [
                {
                    "name": "get_weather",
                    "title": "Current Weather",
                    "description": "Get the current weather for a city.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "city": {
                                "type": "string",
                                "description": "City name such as Hangzhou or Beijing.",
                            },
                            "unit": {
                                "type": "string",
                                "description": "Temperature unit.",
                                "enum": ["celsius", "fahrenheit"],
                            },
                        },
                        "required": ["city"],
                        "additionalProperties": False,
                    },
                    "outputSchema": {
                        "type": "object",
                        "properties": {
                            "city": {"type": "string"},
                            "unit": {"type": "string"},
                            "temperature": {"type": "number"},
                            "condition": {"type": "string"},
                            "humidity": {"type": "number"},
                        },
                        "required": ["city", "unit", "temperature", "condition", "humidity"],
                    },
                }
            ]
        },
    }
    tools_call_request = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {
            "name": "get_weather",
            "arguments": {
                "city": "Hangzhou",
                "unit": "celsius",
            },
        },
    }
    tools_call_result = {
        "jsonrpc": "2.0",
        "id": 2,
        "result": {
            "content": [
                {
                    "type": "text",
                    "text": "{\"city\":\"Hangzhou\",\"unit\":\"celsius\",\"temperature\":24,\"condition\":\"Cloudy\",\"humidity\":67}",
                }
            ],
            "structuredContent": {
                "city": "Hangzhou",
                "unit": "celsius",
                "temperature": 24,
                "condition": "Cloudy",
                "humidity": 67,
            },
            "isError": False,
        },
    }
    return {
        "registry": stable_json(tools_list_result),
        "call": stable_json(tools_call_request),
        "result": stable_json(tools_call_result),
    }


def zcp_native_payloads() -> dict[str, str]:
    registry = (
        "HELLO sid=s1 reg=v1 hash=wx17 caps=parallel,stream,handles\n"
        "TOOL @17 weather.get_current(city:str, unit?:str) -> json !readonly"
    )
    call = 'CALL c1 @17 city="Hangzhou" unit="celsius"'
    result = (
        "RET c1 ok "
        '{"city":"Hangzhou","unit":"celsius","temperature":24,"condition":"Cloudy","humidity":67}'
    )
    return {
        "registry": registry,
        "call": call,
        "result": result,
    }


def zcp_oai_payloads() -> dict[str, str]:
    registry, registry_view = build_registry()
    tools = compile_openai_tools(registry_view, endpoint="chat_completions")
    tool_schema = stable_json(tools)
    tool_call = stable_json(
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "weather_get_current",
                        "arguments": "{\"city\":\"Hangzhou\",\"unit\":\"celsius\"}",
                    },
                }
            ],
        }
    )
    session = SessionState(session_id="s1")
    tool_result = CallResult(
        cid="c1",
        status="ok",
        scalar={
            "city": "Hangzhou",
            "unit": "celsius",
            "temperature": 24,
            "condition": "Cloudy",
            "humidity": 67,
        },
        raw_call_id="call_1",
    )
    returned = submit_tool_results(session, [tool_result], endpoint="chat_completions")
    return {
        "registry": tool_schema,
        "call": tool_call,
        "result": stable_json(returned),
    }


def zcp_gateway_payloads() -> dict[str, str]:
    registry = stable_json(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "tools": [
                    {
                        "name": "weather.get_current",
                        "title": "weather.get_current",
                        "description": "Get the current weather for a city.",
                        "inputSchema": build_weather_tool().input_schema,
                    }
                ]
            },
        }
    )
    call = stable_json(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "weather.get_current", "arguments": {"city": "Hangzhou", "unit": "celsius"}},
        }
    )
    result = stable_json(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "result": {
                "content": [
                    {
                        "type": "text",
                        "text": "{\"city\":\"Hangzhou\",\"unit\":\"celsius\",\"temperature\":24,\"condition\":\"Cloudy\",\"humidity\":67}",
                    }
                ],
                "structuredContent": {
                    "city": "Hangzhou",
                    "unit": "celsius",
                    "temperature": 24,
                    "condition": "Cloudy",
                    "humidity": 67,
                },
                "isError": False,
            },
        }
    )
    return {"registry": registry, "call": call, "result": result}


def print_table(title: str, payloads: dict[str, str]) -> None:
    print(f"=== {title} ===")
    total_bytes = 0
    total_tokens = 0
    for name in ("registry", "call", "result"):
        metrics = count_metrics(payloads[name])
        total_bytes += metrics["bytes"]
        total_tokens += metrics["approx_tokens"]
        print(
            f"{name:>8}: bytes={metrics['bytes']:>4} "
            f"chars={metrics['chars']:>4} approx_tokens={metrics['approx_tokens']:>4}"
        )
    print(f"{'total':>8}: bytes={total_bytes:>4} approx_tokens={total_tokens:>4}")
    print()


def print_multi_turn_comparison() -> None:
    mcp = traditional_mcp_payloads()
    zcp_native = zcp_native_payloads()
    zcp_oai = zcp_oai_payloads()
    zcp_gateway = zcp_gateway_payloads()

    five_turn_mcp = 5 * count_metrics(mcp["registry"])["approx_tokens"] + 5 * count_metrics(mcp["call"])["approx_tokens"] + 5 * count_metrics(mcp["result"])["approx_tokens"]
    five_turn_zcp_native = count_metrics(zcp_native["registry"])["approx_tokens"] + 5 * count_metrics(zcp_native["call"])["approx_tokens"] + 5 * count_metrics(zcp_native["result"])["approx_tokens"]
    five_turn_zcp_oai = 5 * count_metrics(zcp_oai["registry"])["approx_tokens"] + 5 * count_metrics(zcp_oai["call"])["approx_tokens"] + 5 * count_metrics(zcp_oai["result"])["approx_tokens"]
    five_turn_zcp_gateway = 5 * count_metrics(zcp_gateway["registry"])["approx_tokens"] + 5 * count_metrics(zcp_gateway["call"])["approx_tokens"] + 5 * count_metrics(zcp_gateway["result"])["approx_tokens"]

    twenty_turn_mcp = 20 * count_metrics(mcp["registry"])["approx_tokens"] + 20 * count_metrics(mcp["call"])["approx_tokens"] + 20 * count_metrics(mcp["result"])["approx_tokens"]
    twenty_turn_zcp_native = count_metrics(zcp_native["registry"])["approx_tokens"] + 20 * count_metrics(zcp_native["call"])["approx_tokens"] + 20 * count_metrics(zcp_native["result"])["approx_tokens"]
    twenty_turn_zcp_oai = 20 * count_metrics(zcp_oai["registry"])["approx_tokens"] + 20 * count_metrics(zcp_oai["call"])["approx_tokens"] + 20 * count_metrics(zcp_oai["result"])["approx_tokens"]
    twenty_turn_zcp_gateway = 20 * count_metrics(zcp_gateway["registry"])["approx_tokens"] + 20 * count_metrics(zcp_gateway["call"])["approx_tokens"] + 20 * count_metrics(zcp_gateway["result"])["approx_tokens"]

    print("=== 5-turn Approx Token Cost ===")
    print(f"traditional_mcp : {five_turn_mcp}")
    print(f"zcp_native      : {five_turn_zcp_native}")
    print(f"zcp_gateway     : {five_turn_zcp_gateway}")
    print(f"zcp_oai_compat  : {five_turn_zcp_oai}")
    print()
    print("=== 20-turn Approx Token Cost ===")
    print(f"traditional_mcp : {twenty_turn_mcp}")
    print(f"zcp_native      : {twenty_turn_zcp_native}")
    print(f"zcp_gateway     : {twenty_turn_zcp_gateway}")
    print(f"zcp_oai_compat  : {twenty_turn_zcp_oai}")
    print()
    print("Notes:")
    print("- traditional_mcp assumes the full schema/result shape stays visible to the model every turn.")
    print("- zcp_native sends the compact registry once, then reuses stable tool ids.")
    print("- zcp_gateway preserves MCP wire semantics, so its cost is close to traditional MCP on the model-visible side.")
    print("- zcp_oai_compat still pays provider tool-schema cost because OpenAI-compatible endpoints need full JSON schema.")
    print("- For large tool outputs, ZCP-native usually widens the gap further because it can return handles instead of full payload reinjection.")


def main() -> None:
    print_table("Official MCP-style Payloads", traditional_mcp_payloads())
    print_table("ZCP Native", zcp_native_payloads())
    print_table("ZCP via MCP Gateway", zcp_gateway_payloads())
    print_table("ZCP OAI Compat", zcp_oai_payloads())
    print_multi_turn_comparison()


if __name__ == "__main__":
    main()
