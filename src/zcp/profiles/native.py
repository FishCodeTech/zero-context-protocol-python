from __future__ import annotations

import json
from typing import Any

from zcp.canonical_protocol import CallResult, ToolDefinition


def format_registry(tools: list[ToolDefinition]) -> str:
    entries = []
    for tool in tools:
        params = ",".join(f"{name}:{_compact_type(schema)}" for name, schema in tool.input_schema.get("properties", {}).items())
        entries.append(f"TOOL @{tool.tool_id} {tool.alias}({params}) -> {tool.output_mode}")
    return "\n".join(entries)


def format_call(tool: ToolDefinition, arguments: dict[str, Any]) -> str:
    params = " ".join(f'{key}={json.dumps(value, ensure_ascii=True)}' for key, value in arguments.items())
    return f'CALL @{tool.tool_id} {tool.alias} {params}'.strip()


def format_result(result: CallResult) -> str:
    if result.status == "error":
        return f"ERR {result.cid} {result.error.code if result.error else 'exec:error'}"
    if result.handle is not None:
        return f"RET {result.cid} {result.handle.id} summary={json.dumps(result.summary, ensure_ascii=True)}"
    return f"RET {result.cid} ok {json.dumps(result.scalar, ensure_ascii=True)}"


def _compact_type(schema: dict[str, Any]) -> str:
    mapping = {
        "string": "str",
        "integer": "int",
        "number": "float",
        "boolean": "bool",
        "object": "json",
        "array": "json",
    }
    return mapping.get(schema.get("type"), "json")
