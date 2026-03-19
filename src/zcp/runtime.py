from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from .canonical_protocol import CallRequest, CallResult, SessionState, ToolDefinition, utc_now
from .canonical_runtime import CanonicalValidator, HandleStore, RuntimeExecutor, ToolRegistry, ValidationFailure


ResourceHandler = Callable[..., Any]
PromptHandler = Callable[..., Any]
CompletionHandler = Callable[..., Any]


@dataclass
class ResourceDescriptor:
    uri: str
    name: str
    title: str | None = None
    description: str = ""
    mime_type: str = "text/plain"
    subscribe: bool = False
    handler: ResourceHandler | None = None
    annotations: dict[str, Any] = field(default_factory=dict)
    size: int | None = None
    icons: list[dict[str, Any]] | None = None
    meta: dict[str, Any] | None = None
    required_scopes: tuple[str, ...] = ()


@dataclass
class ResourceTemplate:
    uri_template: str
    name: str
    title: str | None = None
    description: str = ""
    mime_type: str = "text/plain"
    handler: ResourceHandler | None = None
    annotations: dict[str, Any] = field(default_factory=dict)
    icons: list[dict[str, Any]] | None = None
    meta: dict[str, Any] | None = None
    required_scopes: tuple[str, ...] = ()


@dataclass
class PromptArgument:
    name: str
    description: str = ""
    required: bool = False


@dataclass
class PromptDescriptor:
    name: str
    title: str | None = None
    description: str = ""
    arguments: list[PromptArgument] = field(default_factory=list)
    handler: PromptHandler | None = None
    icons: list[dict[str, Any]] | None = None
    meta: dict[str, Any] | None = None
    required_scopes: tuple[str, ...] = ()


@dataclass
class CompletionRequest:
    ref: Any
    argument: str | dict[str, Any]
    value: str
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class CompletionResult:
    values: list[str]
    total: int | None = None
    has_more: bool = False


@dataclass
class SamplingRequest:
    messages: list[dict[str, Any]]
    system_prompt: str | None = None
    model_preferences: dict[str, Any] = field(default_factory=dict)
    include_context: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    stop_sequences: list[str] | None = None
    metadata: dict[str, Any] | None = None
    tools: list[dict[str, Any]] = field(default_factory=list)
    tool_choice: dict[str, Any] | None = None


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
    status: str = "queued"
    status_message: str | None = None
    result: Any = None
    error: str | None = None
    created_at: Any = field(default_factory=utc_now)
    last_updated_at: Any = field(default_factory=utc_now)
    ttl_ms: int | None = None
    poll_interval_ms: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class TaskExecutionContext:
    def __init__(self, manager: "TaskManager", task_id: str, session: Any = None) -> None:
        self._manager = manager
        self._task_id = task_id
        self._session = session

    @property
    def task_id(self) -> str:
        return self._task_id

    @property
    def task(self) -> TaskDescriptor:
        return self._manager.get(self._task_id)

    @property
    def is_cancelled(self) -> bool:
        return self.task.status == "cancelled"

    async def update_status(self, message: str, *, status: str = "working") -> TaskDescriptor:
        return await self._manager.update(self._task_id, status=status, status_message=message)

    async def input_required(self, message: str) -> TaskDescriptor:
        return await self.update_status(message, status="input_required")

    async def complete(self, result: Any) -> TaskDescriptor:
        return await self._manager.complete(self._task_id, result)

    async def fail(self, error: str) -> TaskDescriptor:
        return await self._manager.fail(self._task_id, error)

    async def create_message(self, request: SamplingRequest | dict[str, Any]) -> Any:
        if self._session is None or getattr(self._session, "_sampling_handler", None) is None:
            raise NotImplementedError("sampling unavailable")
        payload = request
        if isinstance(payload, dict):
            payload = SamplingRequest(**payload)
        return await invoke_handler(self._session._sampling_handler, payload)

    async def elicit(self, request: ElicitationRequest | dict[str, Any]) -> Any:
        if self._session is None or getattr(self._session, "_elicitation_handler", None) is None:
            raise NotImplementedError("elicitation unavailable")
        payload = request
        if isinstance(payload, dict):
            payload = ElicitationRequest(**payload)
        await self.input_required(payload.prompt)
        try:
            return await invoke_handler(self._session._elicitation_handler, _elicitation_request_to_dict(payload))
        finally:
            if self.task.status == "input_required":
                await self.update_status("Working", status="working")


class TaskManager:
    def __init__(self) -> None:
        self._tasks: dict[str, TaskDescriptor] = {}
        self._workers: dict[str, asyncio.Task[Any]] = {}
        self._counter = 0

    async def create(
        self,
        kind: str,
        payload: dict[str, Any],
        handler: Callable[[dict[str, Any]], Awaitable[Any] | Any] | None = None,
        *,
        ttl_ms: int | None = None,
        poll_interval_ms: int | None = None,
        metadata: dict[str, Any] | None = None,
        on_update: Callable[[TaskDescriptor], Awaitable[None] | None] | None = None,
        context_factory: Callable[[str], TaskExecutionContext | None] | None = None,
    ) -> TaskDescriptor:
        self.purge_expired()
        self._counter += 1
        task_id = f"task-{self._counter}"
        task = TaskDescriptor(
            task_id=task_id,
            kind=kind,
            input=payload,
            status="queued",
            status_message="Queued",
            ttl_ms=ttl_ms,
            poll_interval_ms=poll_interval_ms,
            metadata=metadata or {},
        )
        self._tasks[task_id] = task
        await self._emit(task, on_update)
        if handler is None:
            await self.complete(task_id, payload, on_update=on_update)
            return task
        worker = asyncio.create_task(self._run_task(task_id, handler, on_update=on_update, context_factory=context_factory))
        self._workers[task_id] = worker
        return task

    def list(self) -> list[TaskDescriptor]:
        self.purge_expired()
        return list(self._tasks.values())

    def get(self, task_id: str) -> TaskDescriptor:
        self.purge_expired()
        return self._tasks[task_id]

    async def cancel(
        self,
        task_id: str,
        *,
        on_update: Callable[[TaskDescriptor], Awaitable[None] | None] | None = None,
    ) -> TaskDescriptor:
        task = self._tasks[task_id]
        if task.status in {"completed", "failed", "cancelled"}:
            return task
        task.status = "cancelled"
        task.status_message = "Cancelled"
        task.last_updated_at = utc_now()
        worker = self._workers.get(task_id)
        if worker is not None:
            worker.cancel()
        await self._emit(task, on_update)
        return task

    async def update(
        self,
        task_id: str,
        *,
        status: str | None = None,
        status_message: str | None = None,
        result: Any = None,
        error: str | None = None,
        metadata: dict[str, Any] | None = None,
        on_update: Callable[[TaskDescriptor], Awaitable[None] | None] | None = None,
    ) -> TaskDescriptor:
        task = self._tasks[task_id]
        if status is not None:
            task.status = status
        if status_message is not None:
            task.status_message = status_message
        if result is not None:
            task.result = result
        if error is not None:
            task.error = error
        if metadata:
            task.metadata.update(metadata)
        task.last_updated_at = utc_now()
        await self._emit(task, on_update)
        return task

    async def complete(
        self,
        task_id: str,
        result: Any,
        *,
        on_update: Callable[[TaskDescriptor], Awaitable[None] | None] | None = None,
    ) -> TaskDescriptor:
        return await self.update(
            task_id,
            status="completed",
            status_message="Completed",
            result=result,
            on_update=on_update,
        )

    async def fail(
        self,
        task_id: str,
        error: str,
        *,
        on_update: Callable[[TaskDescriptor], Awaitable[None] | None] | None = None,
    ) -> TaskDescriptor:
        return await self.update(
            task_id,
            status="failed",
            status_message=error,
            error=error,
            on_update=on_update,
        )

    def purge_expired(self) -> None:
        expired = [task_id for task_id, task in self._tasks.items() if _task_is_expired(task)]
        for task_id in expired:
            self._tasks.pop(task_id, None)
            worker = self._workers.pop(task_id, None)
            if worker is not None and not worker.done():
                worker.cancel()

    async def _run_task(
        self,
        task_id: str,
        handler: Callable[[dict[str, Any]], Awaitable[Any] | Any],
        *,
        on_update: Callable[[TaskDescriptor], Awaitable[None] | None] | None = None,
        context_factory: Callable[[str], TaskExecutionContext | None] | None = None,
    ) -> None:
        task = self._tasks[task_id]
        try:
            await self.update(task_id, status="working", status_message="Working", on_update=on_update)
            execution_context = context_factory(task_id) if context_factory is not None else None
            payload = dict(task.input)
            if execution_context is not None:
                payload.setdefault("task", execution_context)
                payload.setdefault("ctx", execution_context)
            result = handler(payload)
            if asyncio.iscoroutine(result):
                result = await result
            current = self._tasks.get(task_id)
            if current is None or current.status in {"completed", "failed", "cancelled"}:
                return
            await self.complete(task_id, result, on_update=on_update)
        except asyncio.CancelledError:
            current = self._tasks.get(task_id)
            if current is not None and current.status != "cancelled":
                await self.update(task_id, status="cancelled", status_message="Cancelled", on_update=on_update)
            raise
        except Exception as exc:
            await self.fail(task_id, str(exc), on_update=on_update)
        finally:
            self._workers.pop(task_id, None)

    async def _emit(
        self,
        task: TaskDescriptor,
        callback: Callable[[TaskDescriptor], Awaitable[None] | None] | None,
    ) -> None:
        if callback is None:
            return
        result = callback(task)
        if asyncio.iscoroutine(result):
            await result


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
        key = request.ref
        if isinstance(key, dict):
            key = key.get("name") or key.get("uri")
        handler = self._handlers[key]
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
            required_parameters = {
                parameter.name
                for parameter in parameters
                if parameter.kind in {inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY}
                and parameter.default is inspect._empty
            }
            accepts_var_kwargs = any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters)
            if accepts_var_kwargs:
                result = handler(**payload)
            elif named_parameters:
                matching = {name: payload[name] for name in named_parameters if name in payload}
                if required_parameters.issubset(matching.keys()):
                    result = handler(**matching)
                elif len(parameters) == 1:
                    result = handler(payload)
                else:
                    result = handler(**payload)
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
    title: str | None = None,
    output_schema: dict[str, Any] | None = None,
    icons: list[dict[str, Any]] | None = None,
    annotations: dict[str, Any] | None = None,
    execution: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> ToolDefinition:
    return ToolDefinition(
        tool_id=tool_id,
        alias=alias,
        title=title,
        description_short=description_short,
        input_schema=input_schema,
        output_schema=output_schema,
        output_mode=output_mode,  # type: ignore[arg-type]
        defaults=defaults or {},
        handler=lambda arguments, context=None: invoke_handler(handler, {**arguments, "ctx": context}),
        handle_kind=handle_kind,
        flags=flags or frozenset(),
        inline_ok=inline_ok,
        icons=icons,
        annotations=annotations,
        execution=execution,
        metadata=metadata or {},
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
    "TaskExecutionContext",
    "TaskManager",
    "ToolDefinition",
    "ToolRegistry",
    "ValidationFailure",
    "build_tool_from_callable",
]


def _task_is_expired(task: TaskDescriptor) -> bool:
    if task.ttl_ms is None:
        return False
    age_ms = (utc_now() - task.created_at).total_seconds() * 1000
    return age_ms >= task.ttl_ms


def _elicitation_request_to_dict(request: ElicitationRequest) -> dict[str, Any]:
    return {
        "kind": request.kind,
        "prompt": request.prompt,
        "fields": [field.__dict__ for field in request.fields],
        "url": request.url,
        "metadata": request.metadata,
    }
