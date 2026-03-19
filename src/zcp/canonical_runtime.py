from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from .canonical_protocol import (
    CallError,
    CallRequest,
    CallResult,
    HandleRef,
    RegistryView,
    SessionState,
    ToolDefinition,
    is_scalar_value,
    merge_defaults,
    utc_now,
)
from .canonical_schema import registry_hash


class ValidationFailure(ValueError):
    def __init__(self, code: str, hint: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.hint = hint


class ToolRegistry:
    def __init__(self, version: int = 1) -> None:
        self.version = version
        self._tools_by_id: dict[str, ToolDefinition] = {}
        self._tools_by_alias: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> None:
        self._tools_by_id[tool.tool_id] = tool
        self._tools_by_alias[tool.alias] = tool

    def get_by_id(self, tool_id: str) -> ToolDefinition:
        return self._tools_by_id[tool_id]

    def get_by_alias(self, alias: str) -> ToolDefinition:
        return self._tools_by_alias[alias]

    def subset(self, aliases: list[str] | tuple[str, ...] | None = None, limit: int = 256) -> RegistryView:
        if aliases is None:
            tools = list(self._tools_by_id.values())
        else:
            tools = [self._tools_by_alias[alias] for alias in aliases]
        if len(tools) > limit:
            raise ValueError(f"tool subset exceeds limit {limit}")
        return RegistryView(version=self.version, hash=registry_hash(tools), tools=tools)


class CanonicalValidator:
    def validate(self, schema: dict[str, Any], value: Any) -> Any:
        return self._validate_node(schema, value, path="")

    def _validate_node(self, schema: dict[str, Any], value: Any, path: str) -> Any:
        if "anyOf" in schema:
            return self._validate_composite(schema["anyOf"], value, path)
        if "oneOf" in schema:
            return self._validate_composite(schema["oneOf"], value, path)

        expected_type = schema.get("type")
        if isinstance(expected_type, list):
            for item_type in expected_type:
                try:
                    return self._validate_node({**schema, "type": item_type}, value, path)
                except ValidationFailure:
                    continue
            raise ValidationFailure(self._invalid_code(path))

        if expected_type == "object":
            if not isinstance(value, dict):
                raise ValidationFailure(self._invalid_code(path))
            properties = schema.get("properties", {})
            required = schema.get("required", [])
            validated: dict[str, Any] = {}
            for key in required:
                if key not in value:
                    raise ValidationFailure(f"missing:{self._join_path(path, key)}")
            if schema.get("additionalProperties") is False:
                extra = set(value) - set(properties)
                if extra:
                    name = sorted(extra)[0]
                    raise ValidationFailure(f"invalid:{self._join_path(path, name)}")
            for key, item in value.items():
                child_schema = properties.get(key)
                if child_schema is None:
                    continue
                validated[key] = self._validate_node(child_schema, item, self._join_path(path, key))
            return validated

        if expected_type == "array":
            if not isinstance(value, list):
                raise ValidationFailure(self._invalid_code(path))
            item_schema = schema.get("items", {})
            return [self._validate_node(item_schema, item, f"{path}[{idx}]") for idx, item in enumerate(value)]

        if expected_type == "string":
            if value is None:
                raise ValidationFailure(self._invalid_code(path))
            if not isinstance(value, str):
                value = str(value)
            enum = schema.get("enum")
            if enum and value not in enum:
                raise ValidationFailure(self._invalid_code(path))
            return value

        if expected_type == "integer":
            if value is None:
                raise ValidationFailure(self._invalid_code(path))
            coerced = self._coerce_int(value)
            if coerced is None:
                raise ValidationFailure(self._invalid_code(path))
            return coerced

        if expected_type == "number":
            if value is None:
                raise ValidationFailure(self._invalid_code(path))
            coerced = self._coerce_float(value)
            if coerced is None:
                raise ValidationFailure(self._invalid_code(path))
            return coerced

        if expected_type == "boolean":
            if isinstance(value, bool):
                return value
            if isinstance(value, str) and value.lower() in {"true", "false"}:
                return value.lower() == "true"
            raise ValidationFailure(self._invalid_code(path))

        if expected_type == "null":
            if value is not None:
                raise ValidationFailure(self._invalid_code(path))
            return None

        raise ValidationFailure(self._invalid_code(path))

    def _validate_composite(self, options: list[dict[str, Any]], value: Any, path: str) -> Any:
        last_error: ValidationFailure | None = None
        for option in options:
            try:
                return self._validate_node(option, value, path)
            except ValidationFailure as exc:
                last_error = exc
        raise last_error or ValidationFailure(self._invalid_code(path))

    def _coerce_int(self, value: Any) -> int | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            try:
                return int(value)
            except ValueError:
                return None
        return None

    def _coerce_float(self, value: Any) -> float | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                return None
        return None

    def _join_path(self, path: str, part: str) -> str:
        return f"{path}.{part}" if path else part

    def _invalid_code(self, path: str) -> str:
        return f"invalid:{path or '$'}"


class HandleStore:
    def __init__(self, session: SessionState, default_ttl: timedelta = timedelta(minutes=10)) -> None:
        self.session = session
        self.default_ttl = default_ttl
        self._counter = 0

    def create(
        self,
        *,
        kind: str,
        data: Any,
        summary: str,
        ttl: timedelta | None = None,
        meta: dict[str, Any] | None = None,
    ) -> HandleRef:
        self._counter += 1
        handle_id = f"#{kind[:1].upper()}{self._counter}"
        now = utc_now()
        expires_at = now + (ttl or self.default_ttl) if ttl is not None or self.default_ttl else None
        payload = json.dumps(data, sort_keys=True, default=str, ensure_ascii=True).encode("utf-8")
        handle = HandleRef(
            id=handle_id,
            kind=kind,
            summary=summary,
            created_at=now,
            expires_at=expires_at,
            hash=hashlib.sha256(payload).hexdigest()[:12],
            size=len(payload),
            data=data,
            meta=meta or {},
        )
        self.session.register_handle(handle)
        return handle

    def get(self, handle_id: str) -> HandleRef:
        handle = self.session.handles[handle_id]
        if handle.is_expired():
            raise ValidationFailure(f"expired:{handle_id}")
        return handle

    def summarize(self, handle_id: str) -> str:
        return self.get(handle_id).summary

    def count(self, handle_id: str) -> int:
        data = self.get(handle_id).data
        if isinstance(data, (list, tuple, dict, str)):
            return len(data)
        raise ValidationFailure(f"invalid:{handle_id}")

    def read(self, handle_id: str, *, item: int | None = None, fields: list[str] | None = None) -> Any:
        data = self.get(handle_id).data
        if item is not None:
            if not isinstance(data, list):
                raise ValidationFailure(f"invalid:{handle_id}")
            data = data[item - 1]
        if fields:
            if not isinstance(data, dict):
                raise ValidationFailure(f"invalid:{handle_id}")
            return {field: data.get(field) for field in fields}
        return data

    def view(self, handle_id: str, *, cols: list[str], limit: int = 10) -> list[dict[str, Any]]:
        data = self.get(handle_id).data
        if not isinstance(data, list):
            raise ValidationFailure(f"invalid:{handle_id}")
        rows: list[dict[str, Any]] = []
        for row in data[:limit]:
            if not isinstance(row, dict):
                raise ValidationFailure(f"invalid:{handle_id}")
            rows.append({col: row.get(col) for col in cols})
        return rows


@dataclass
class ExecutionPayload:
    value: Any
    summary: str | None = None
    meta: dict[str, Any] | None = None
    handle_kind: str | None = None
    ttl: timedelta | None = None


class RuntimeExecutor:
    def __init__(
        self,
        registry: ToolRegistry,
        validator: CanonicalValidator,
        handle_store: HandleStore,
    ) -> None:
        self.registry = registry
        self.validator = validator
        self.handle_store = handle_store

    async def execute_call(self, request: CallRequest) -> CallResult:
        try:
            tool = self.registry.get_by_id(request.tool_id)
        except KeyError:
            return CallResult(
                cid=request.cid,
                status="error",
                error=CallError(code="unknown"),
                raw_call_id=request.raw_call_id,
            )

        if "approval" in tool.flags or tool.approval_policy:
            return CallResult(
                cid=request.cid,
                status="error",
                error=CallError(code="approval_required"),
                raw_call_id=request.raw_call_id,
            )

        arguments = merge_defaults(request.arguments, tool.defaults)
        try:
            validated = self.validator.validate(tool.input_schema, arguments)
        except ValidationFailure as exc:
            return CallResult(
                cid=request.cid,
                status="error",
                error=CallError(code=exc.code, hint=exc.hint),
                raw_call_id=request.raw_call_id,
            )

        try:
            payload = await self._invoke(tool, validated, request.context)
        except ValidationFailure as exc:
            return CallResult(
                cid=request.cid,
                status="error",
                error=CallError(code=exc.code, hint=exc.hint),
                raw_call_id=request.raw_call_id,
            )
        except Exception:
            return CallResult(
                cid=request.cid,
                status="error",
                error=CallError(code="exec:error"),
                raw_call_id=request.raw_call_id,
            )

        return self._build_result(tool, request, payload)

    async def execute_many(self, requests: list[CallRequest]) -> list[CallResult]:
        return list(await asyncio.gather(*(self.execute_call(request) for request in requests)))

    async def _invoke(self, tool: ToolDefinition, arguments: dict[str, Any], context: Any = None) -> Any:
        if tool.handler is None:
            raise RuntimeError(f"tool {tool.alias} has no handler")
        parameters = list(inspect.signature(tool.handler).parameters.values())
        if context is not None and len(parameters) >= 2:
            result = tool.handler(arguments, context)
        else:
            result = tool.handler(arguments)
        if asyncio.iscoroutine(result):
            return await result
        return result

    def _build_result(self, tool: ToolDefinition, request: CallRequest, payload: Any) -> CallResult:
        if isinstance(payload, ExecutionPayload):
            value = payload.value
            summary = payload.summary
            meta = payload.meta or {}
            handle_kind = payload.handle_kind or tool.handle_kind
            ttl = payload.ttl
        else:
            value = payload
            summary = tool.summarize(value) if tool.summarize else default_summary(value, tool.alias)
            meta = tool.meta(value) if tool.meta else {}
            handle_kind = tool.handle_kind
            ttl = None

        if tool.output_mode == "scalar" and (tool.inline_ok or is_scalar_value(value)):
            if isinstance(value, str) and len(value.encode("utf-8")) > tool.inline_max_bytes and not tool.inline_ok:
                pass
            else:
                return CallResult(
                    cid=request.cid,
                    status="ok",
                    scalar=value,
                    summary=summary,
                    meta=meta,
                    raw_call_id=request.raw_call_id,
                )

        handle = self.handle_store.create(
            kind=handle_kind,
            data=value,
            summary=summary or default_summary(value, tool.alias),
            ttl=ttl,
            meta=meta,
        )
        return CallResult(
            cid=request.cid,
            status="ok",
            handle=handle,
            summary=handle.summary,
            meta=meta,
            raw_call_id=request.raw_call_id,
        )


def default_summary(value: Any, alias: str) -> str:
    if isinstance(value, list):
        return f"{len(value)} items returned by {alias}"
    if isinstance(value, dict):
        return f"{len(value)} fields returned by {alias}"
    if isinstance(value, str):
        preview = value[:60]
        return f"{alias}: {preview}"
    return f"result from {alias}"


def expand_handle(session: SessionState, handle: str, op: str, **kwargs: Any) -> Any:
    store = HandleStore(session)
    if op == "sum":
        return store.summarize(handle)
    if op == "count":
        return store.count(handle)
    if op == "read":
        return store.read(handle, item=kwargs.get("item"), fields=kwargs.get("fields"))
    if op == "view":
        return store.view(handle, cols=kwargs["cols"], limit=kwargs.get("limit", 10))
    raise ValueError(f"unsupported handle op {op!r}")
