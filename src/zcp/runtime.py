from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from .canonical_protocol import CallRequest, CallResult, SessionState, ToolDefinition
from .canonical_runtime import CanonicalValidator, HandleStore, RuntimeExecutor, ToolRegistry, ValidationFailure


ResourceHandler = Callable[..., Any]
PromptHandler = Callable[..., Any]
CompletionHandler = Callable[..., Any]


@dataclass
class ResourceDescriptor:
    uri: str
    name: str
    description: str = ""
    mime_type: str = "text/plain"
    subscribe: bool = False
    handler: ResourceHandler | None = None
    annotations: dict[str, Any] = field(default_factory=dict)
    required_scopes: tuple[str, ...] = ()


@dataclass
class ResourceTemplate:
    uri_template: str
    name: str
    description: str = ""
    mime_type: str = "text/plain"
    handler: ResourceHandler | None = None
    required_scopes: tuple[str, ...] = ()


@dataclass
class PromptArgument:
    name: str
    description: str = ""
    required: bool = False


@dataclass
class PromptDescriptor:
    name: str
    description: str = ""
    arguments: list[PromptArgument] = field(default_factory=list)
    handler: PromptHandler | None = None
    required_scopes: tuple[str, ...] = ()


@dataclass
class CompletionRequest:
    ref: str
    argument: str
    value: str
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class CompletionResult:
    values: list[str]
    has_more: bool = False


@dataclass
class SamplingRequest:
    messages: list[dict[str, Any]]
    system_prompt: str | None = None
    model_preferences: dict[str, Any] = field(default_factory=dict)
    tools: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class SamplingResult:
    message: dict[str, Any]
    model: str | None = None
    stop_reason: str | None = None


@dataclass
class ElicitationField:
    name: str
    type: str
    label: str
    required: bool = False
    options: list[str] = field(default_factory=list)


@dataclass
class ElicitationRequest:
    kind: str
    prompt: str
    fields: list[ElicitationField] = field(default_factory=list)
    url: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ElicitationResult:
    status: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class TaskDescriptor:
    task_id: str
    kind: str
    input: dict[str, Any]
    status: str = "pending"
    result: Any = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class TaskManager:
    def __init__(self) -> None:
        self._tasks: dict[str, TaskDescriptor] = {}
        self._counter = 0

    async def create(self, kind: str, payload: dict[str, Any], handler: Callable[[dict[str, Any]], Awaitable[Any] | Any] | None = None) -> TaskDescriptor:
        self._counter += 1
        task_id = f"task-{self._counter}"
        task = TaskDescriptor(task_id=task_id, kind=kind, input=payload, status="running")
        self._tasks[task_id] = task
        if handler is None:
            task.status = "completed"
            task.result = payload
            return task
        try:
            result = handler(payload)
            if asyncio.iscoroutine(result):
                result = await result
            task.result = result
            task.status = "completed"
        except Exception as exc:
            task.status = "failed"
            task.error = str(exc)
        return task

    def list(self) -> list[TaskDescriptor]:
        return list(self._tasks.values())

    def get(self, task_id: str) -> TaskDescriptor:
        return self._tasks[task_id]

    def cancel(self, task_id: str) -> TaskDescriptor:
        task = self._tasks[task_id]
        if task.status in {"completed", "failed", "cancelled"}:
            return task
        task.status = "cancelled"
        return task


class ResourceRegistry:
    def __init__(self) -> None:
        self._resources: dict[str, ResourceDescriptor] = {}
        self._templates: dict[str, ResourceTemplate] = {}

    def register(self, descriptor: ResourceDescriptor) -> None:
        self._resources[descriptor.uri] = descriptor

    def register_template(self, descriptor: ResourceTemplate) -> None:
        self._templates[descriptor.uri_template] = descriptor

    def list(self) -> list[ResourceDescriptor]:
        return list(self._resources.values())

    def list_templates(self) -> list[ResourceTemplate]:
        return list(self._templates.values())

    async def read(self, uri: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        if uri in self._resources:
            descriptor = self._resources[uri]
            data = await invoke_handler(descriptor.handler, {"uri": uri, **(arguments or {})})
            return {"uri": descriptor.uri, "mimeType": descriptor.mime_type, "contents": data}
        for template in self._templates.values():
            if _matches_template(template.uri_template, uri):
                data = await invoke_handler(template.handler, {"uri": uri, **(arguments or {})})
                return {"uri": uri, "mimeType": template.mime_type, "contents": data}
        raise KeyError(uri)


class PromptRegistry:
    def __init__(self) -> None:
        self._prompts: dict[str, PromptDescriptor] = {}

    def register(self, descriptor: PromptDescriptor) -> None:
        self._prompts[descriptor.name] = descriptor

    def list(self) -> list[PromptDescriptor]:
        return list(self._prompts.values())

    async def get(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        descriptor = self._prompts[name]
        result = await invoke_handler(descriptor.handler, arguments or {})
        return {
            "name": descriptor.name,
            "description": descriptor.description,
            "messages": result if isinstance(result, list) else [{"role": "user", "content": str(result)}],
        }


class CompletionRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, CompletionHandler] = {}

    def register(self, ref: str, handler: CompletionHandler) -> None:
        self._handlers[ref] = handler

    async def complete(self, request: CompletionRequest) -> CompletionResult:
        handler = self._handlers[request.ref]
        result = await invoke_handler(handler, request)
        if isinstance(result, CompletionResult):
            return result
        return CompletionResult(values=list(result))


async def invoke_handler(handler: Callable[..., Any] | None, payload: Any) -> Any:
    if handler is None:
        return payload
    signature = inspect.signature(handler)
    parameters = list(signature.parameters.values())
    if isinstance(payload, dict):
        if not parameters:
            result = handler()
        else:
            named_parameters = {
                parameter.name
                for parameter in parameters
                if parameter.kind in {inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY}
            }
            accepts_var_kwargs = any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters)
            if accepts_var_kwargs:
                result = handler(**payload)
            elif named_parameters and named_parameters.issubset(payload.keys()):
                result = handler(**{name: payload[name] for name in named_parameters})
            elif len(parameters) == 1:
                result = handler(payload)
            else:
                result = handler(**payload)
    else:
        if not parameters:
            result = handler()
        else:
            result = handler(payload)
    if asyncio.iscoroutine(result):
        return await result
    return result


def build_tool_from_callable(
    *,
    tool_id: str,
    alias: str,
    description_short: str,
    input_schema: dict[str, Any],
    handler: Callable[..., Any],
    output_mode: str = "handle",
    defaults: dict[str, Any] | None = None,
    handle_kind: str = "generic",
    flags: frozenset[str] | None = None,
    inline_ok: bool = False,
) -> ToolDefinition:
    return ToolDefinition(
        tool_id=tool_id,
        alias=alias,
        description_short=description_short,
        input_schema=input_schema,
        output_mode=output_mode,  # type: ignore[arg-type]
        defaults=defaults or {},
        handler=lambda arguments: invoke_handler(handler, arguments),
        handle_kind=handle_kind,
        flags=flags or frozenset(),
        inline_ok=inline_ok,
    )


def _matches_template(template: str, uri: str) -> bool:
    prefix = template.split("{", 1)[0]
    return uri.startswith(prefix)


__all__ = [
    "CanonicalValidator",
    "CallRequest",
    "CallResult",
    "CompletionRegistry",
    "CompletionRequest",
    "CompletionResult",
    "ElicitationField",
    "ElicitationRequest",
    "ElicitationResult",
    "HandleStore",
    "PromptArgument",
    "PromptDescriptor",
    "PromptRegistry",
    "ResourceDescriptor",
    "ResourceRegistry",
    "ResourceTemplate",
    "RuntimeExecutor",
    "SamplingRequest",
    "SamplingResult",
    "SessionState",
    "TaskDescriptor",
    "TaskManager",
    "ToolDefinition",
    "ToolRegistry",
    "ValidationFailure",
    "build_tool_from_callable",
]
