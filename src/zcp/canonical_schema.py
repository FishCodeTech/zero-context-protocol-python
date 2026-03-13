from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from .canonical_protocol import RegistryView, ToolDefinition


class SchemaCompileError(ValueError):
    """Raised when a canonical schema cannot be compiled for OpenAI strict mode."""


class OpenAIStrictSchemaCompiler:
    """Compile canonical schemas into OpenAI strict-compatible function tools."""

    def compile_tool(self, tool: ToolDefinition) -> dict[str, Any]:
        return {
            "type": "function",
            "name": normalize_tool_name(tool.alias),
            "description": tool.description_short.strip(),
            "strict": tool.strict,
            "parameters": self.compile_schema(tool.input_schema),
        }

    def compile_registry(self, registry: RegistryView) -> list[dict[str, Any]]:
        return [self.compile_tool(tool) for tool in registry.tools]

    def compile_schema(self, schema: dict[str, Any]) -> dict[str, Any]:
        compiled = self._compile_node(copy.deepcopy(schema), path="$")
        node_type = compiled.get("type")
        if node_type != "object":
            raise SchemaCompileError("top-level schema must compile to an object")
        return compiled

    def _compile_node(self, schema: dict[str, Any], path: str) -> dict[str, Any]:
        if "anyOf" in schema:
            return {"anyOf": [self._compile_node(item, f"{path}.anyOf[{idx}]") for idx, item in enumerate(schema["anyOf"])]}
        if "oneOf" in schema:
            return {"oneOf": [self._compile_node(item, f"{path}.oneOf[{idx}]") for idx, item in enumerate(schema["oneOf"])]}

        node_type = schema.get("type")
        if isinstance(node_type, list):
            compiled_types: list[str] = []
            for item in node_type:
                if item == "null":
                    compiled_types.append(item)
                    continue
                if item not in {"string", "integer", "number", "boolean", "object", "array"}:
                    raise SchemaCompileError(f"unsupported type {item!r} at {path}")
                compiled_types.append(item)
            schema["type"] = compiled_types
            if "properties" in schema:
                schema["properties"] = {
                    key: self._compile_node(value, f"{path}.{key}")
                    for key, value in schema["properties"].items()
                }
                schema["required"] = list(schema["properties"].keys())
                schema["additionalProperties"] = False
            if "items" in schema and isinstance(schema["items"], dict):
                schema["items"] = self._compile_node(schema["items"], f"{path}[]")
            return schema

        if node_type == "object":
            properties = schema.get("properties", {})
            if not isinstance(properties, dict):
                raise SchemaCompileError(f"object properties must be a dict at {path}")
            compiled_props: dict[str, Any] = {}
            original_required = set(schema.get("required", []))
            for key, value in properties.items():
                compiled = self._compile_node(value, f"{path}.{key}")
                if key not in original_required:
                    compiled = self._nullable(compiled)
                compiled_props[key] = compiled
            schema["properties"] = compiled_props
            schema["required"] = list(compiled_props.keys())
            schema["additionalProperties"] = False
            return schema

        if node_type == "array":
            items = schema.get("items")
            if not isinstance(items, dict):
                raise SchemaCompileError(f"array items must be a schema object at {path}")
            schema["items"] = self._compile_node(items, f"{path}[]")
            return schema

        if node_type in {"string", "integer", "number", "boolean", "null"}:
            return schema

        raise SchemaCompileError(f"unsupported or missing type at {path}: {node_type!r}")

    def _nullable(self, schema: dict[str, Any]) -> dict[str, Any]:
        node_type = schema.get("type")
        if isinstance(node_type, list):
            if "null" not in node_type:
                schema["type"] = [*node_type, "null"]
            return schema
        if node_type is None and ("oneOf" in schema or "anyOf" in schema):
            key = "oneOf" if "oneOf" in schema else "anyOf"
            choices = list(schema[key])
            if not any(choice.get("type") == "null" for choice in choices):
                choices.append({"type": "null"})
            schema[key] = choices
            return schema
        schema["type"] = [node_type, "null"]
        return schema


def normalize_tool_name(alias: str) -> str:
    return alias.replace(".", "_").replace("-", "_")


def registry_hash(tools: list[ToolDefinition]) -> str:
    payload = [
        {
            "tool_id": tool.tool_id,
            "alias": tool.alias,
            "description_short": tool.description_short,
            "input_schema": tool.input_schema,
            "output_mode": tool.output_mode,
            "flags": sorted(tool.flags),
            "handle_kind": tool.handle_kind,
            "strict": tool.strict,
        }
        for tool in tools
    ]
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:12]
