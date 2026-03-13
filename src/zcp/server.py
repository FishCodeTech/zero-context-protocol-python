from __future__ import annotations

import asyncio
import inspect
import json
from dataclasses import dataclass, field
from typing import Any, Callable

from .capabilities import AuthContext, AuthProfile, Capabilities, InitializeResult, PROTOCOL_VERSION, default_capabilities
from .protocol import failure, notification, success
from .runtime import (
    CallRequest,
    CanonicalValidator,
    CompletionRegistry,
    CompletionRequest,
    HandleStore,
    PromptArgument,
    PromptDescriptor,
    PromptRegistry,
    ResourceDescriptor,
    ResourceRegistry,
    ResourceTemplate,
    RuntimeExecutor,
    SamplingRequest,
    SamplingResult,
    SessionState,
    TaskManager,
    ToolDefinition,
    ToolRegistry,
    ValidationFailure,
    build_tool_from_callable,
    invoke_handler,
)


LOG_LEVELS = ["debug", "info", "warning", "error"]


@dataclass
class FastZCP:
    name: str
    version: str = "0.1.0"
    instructions: str | None = None
    auth_profile: AuthProfile | None = None
    capabilities: Capabilities = field(default_factory=default_capabilities)

    def __post_init__(self) -> None:
        self.tool_registry = ToolRegistry()
        self.resource_registry = ResourceRegistry()
        self.prompt_registry = PromptRegistry()
        self.completion_registry = CompletionRegistry()
        self.task_manager = TaskManager()
        self._tool_counter = 0
        self._task_handlers: dict[str, Callable[[dict[str, Any]], Any]] = {}

    def tool(
        self,
        *,
        name: str | None = None,
        description: str = "",
        input_schema: dict[str, Any],
        output_mode: str = "handle",
        defaults: dict[str, Any] | None = None,
        handle_kind: str = "generic",
        flags: frozenset[str] | None = None,
        inline_ok: bool = False,
        required_scopes: tuple[str, ...] = (),
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            self._tool_counter += 1
            alias = name or func.__name__
            tool = build_tool_from_callable(
                tool_id=str(self._tool_counter),
                alias=alias,
                description_short=description or (inspect.getdoc(func) or ""),
                input_schema=input_schema,
                handler=func,
                output_mode=output_mode,
                defaults=defaults,
                handle_kind=handle_kind,
                flags=flags,
                inline_ok=inline_ok,
            )
            tool.required_scopes = required_scopes
            self.tool_registry.register(tool)
            return func

        return decorator

    def resource(
        self,
        uri: str,
        *,
        name: str | None = None,
        description: str = "",
        mime_type: str = "text/plain",
        subscribe: bool = False,
        annotations: dict[str, Any] | None = None,
        required_scopes: tuple[str, ...] = (),
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            descriptor = ResourceDescriptor(
                uri=uri,
                name=name or func.__name__,
                description=description or (inspect.getdoc(func) or ""),
                mime_type=mime_type,
                subscribe=subscribe,
                handler=func,
                annotations=annotations or {},
                required_scopes=required_scopes,
            )
            self.resource_registry.register(descriptor)
            return func

        return decorator

    def resource_template(
        self,
        uri_template: str,
        *,
        name: str | None = None,
        description: str = "",
        mime_type: str = "text/plain",
        required_scopes: tuple[str, ...] = (),
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            descriptor = ResourceTemplate(
                uri_template=uri_template,
                name=name or func.__name__,
                description=description or (inspect.getdoc(func) or ""),
                mime_type=mime_type,
                handler=func,
                required_scopes=required_scopes,
            )
            self.resource_registry.register_template(descriptor)
            return func

        return decorator

    def prompt(
        self,
        *,
        name: str | None = None,
        description: str = "",
        arguments: list[PromptArgument] | None = None,
        required_scopes: tuple[str, ...] = (),
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            descriptor = PromptDescriptor(
                name=name or func.__name__,
                description=description or (inspect.getdoc(func) or ""),
                arguments=arguments or [],
                handler=func,
                required_scopes=required_scopes,
            )
            self.prompt_registry.register(descriptor)
            return func

        return decorator

    def completion(self, ref: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            self.completion_registry.register(ref, func)
            return func

        return decorator

    def task(self, kind: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            self._task_handlers[kind] = func
            return func

        return decorator

    def create_server_session(
        self,
        *,
        session_id: str,
        auth_context: AuthContext | None = None,
    ) -> "ZCPServerSession":
        return ZCPServerSession(self, session_id=session_id, auth_context=auth_context)


class ZCPServerSession:
    def __init__(self, app: FastZCP, *, session_id: str, auth_context: AuthContext | None = None) -> None:
        self.app = app
        self.state = SessionState(session_id=session_id)
        self.auth_context = auth_context or AuthContext(session_id=session_id)
        self.validator = CanonicalValidator()
        self.handle_store = HandleStore(self.state)
        self.executor = RuntimeExecutor(app.tool_registry, self.validator, self.handle_store)
        self.log_level = "info"
        self.subscriptions: set[str] = set()
        self._notifications: list[dict[str, Any]] = []
        self._client_roots_provider: Callable[[], list[dict[str, Any]]] | None = None
        self._sampling_handler: Callable[[SamplingRequest], Any] | None = None
        self._elicitation_handler: Callable[[Any], Any] | None = None
        self._log_handler: Callable[[dict[str, Any]], None] | None = None

    def attach_client(
        self,
        *,
        roots_provider: Callable[[], list[dict[str, Any]]] | None = None,
        sampling_handler: Callable[[SamplingRequest], Any] | None = None,
        elicitation_handler: Callable[[Any], Any] | None = None,
        log_handler: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self._client_roots_provider = roots_provider
        self._sampling_handler = sampling_handler
        self._elicitation_handler = elicitation_handler
        self._log_handler = log_handler

    def capabilities_dict(self) -> dict[str, Any]:
        capabilities = self.app.capabilities.to_dict()
        if not self.app.tool_registry.subset().tools:
            capabilities.pop("tools", None)
        if not self.app.resource_registry.list() and not self.app.resource_registry.list_templates():
            capabilities.pop("resources", None)
        if not self.app.prompt_registry.list():
            capabilities.pop("prompts", None)
        return capabilities

    def _check_scopes(self, required_scopes: tuple[str, ...]) -> None:
        if not required_scopes:
            return
        granted = set(self.auth_context.scopes)
        missing = [scope for scope in required_scopes if scope not in granted]
        if missing:
            raise ValidationFailure(f"forbidden:missing_scopes:{','.join(missing)}")

    async def handle_message(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        if method is None:
            return None
        try:
            result = await self._dispatch(method, message.get("params") or {})
        except ValidationFailure as exc:
            return failure(message.get("id"), -32602, exc.code)
        except KeyError as exc:
            return failure(message.get("id"), -32004, f"not_found:{exc.args[0]}")
        except NotImplementedError as exc:
            return failure(message.get("id"), -32601, str(exc))
        except Exception as exc:
            return failure(message.get("id"), -32000, str(exc))
        if "id" not in message:
            return None
        return success(message.get("id"), result)

    def drain_notifications(self) -> list[dict[str, Any]]:
        pending = list(self._notifications)
        self._notifications.clear()
        return pending

    def notify(self, method: str, params: dict[str, Any]) -> None:
        payload = notification(method, params)
        self._notifications.append(payload)
        if method == "notifications/logging/message" and self._log_handler is not None:
            self._log_handler(params)

    def emit_log(self, level: str, message: str, data: dict[str, Any] | None = None, logger: str = "zcp") -> None:
        if LOG_LEVELS.index(level) < LOG_LEVELS.index(self.log_level):
            return
        self.notify("notifications/logging/message", {"level": level, "logger": logger, "data": {"message": message, **(data or {})}})

    def emit_resource_updated(self, uri: str) -> None:
        if uri in self.subscriptions:
            self.notify("notifications/resources/updated", {"uri": uri})

    def emit_roots_changed(self) -> None:
        self.notify("notifications/roots/list_changed", {})

    async def _dispatch(self, method: str, params: dict[str, Any]) -> Any:
        if method == "initialize":
            result = InitializeResult(
                protocol_version=PROTOCOL_VERSION,
                server_info={"name": self.app.name, "version": self.app.version},
                capabilities=self.capabilities_dict(),
                auth=self.app.auth_profile.__dict__ if self.app.auth_profile else None,
            )
            return result.to_dict()
        if method == "initialized":
            return {"ok": True}
        if method == "ping":
            return {"ok": True, "sessionId": self.state.session_id}
        if method == "tools/list":
            return {
                "tools": [
                    {
                        "id": tool.tool_id,
                        "name": tool.alias,
                        "description": tool.description_short,
                        "inputSchema": tool.input_schema,
                        "outputMode": tool.output_mode,
                        "flags": sorted(tool.flags),
                        "requiredScopes": list(tool.required_scopes),
                    }
                    for tool in self.app.tool_registry.subset().tools
                ]
            }
        if method == "tools/call":
            tool = self.app.tool_registry.get_by_alias(params["name"])
            self._check_scopes(tool.required_scopes)
            request = CallRequest(
                cid=self.state.next_cid(),
                tool_id=tool.tool_id,
                alias=tool.alias,
                arguments=params.get("arguments", {}),
            )
            result = await self.executor.execute_call(request)
            return _call_result_to_dict(result)
        if method == "resources/list":
            return {
                "resources": [
                    {
                        "uri": item.uri,
                        "name": item.name,
                        "description": item.description,
                        "mimeType": item.mime_type,
                        "subscribe": item.subscribe,
                        "annotations": item.annotations,
                        "requiredScopes": list(item.required_scopes),
                    }
                    for item in self.app.resource_registry.list()
                ],
                "resourceTemplates": [
                    {
                        "uriTemplate": item.uri_template,
                        "name": item.name,
                        "description": item.description,
                        "mimeType": item.mime_type,
                        "requiredScopes": list(item.required_scopes),
                    }
                    for item in self.app.resource_registry.list_templates()
                ],
            }
        if method == "resources/read":
            uri = params["uri"]
            for item in self.app.resource_registry.list():
                if item.uri == uri:
                    self._check_scopes(item.required_scopes)
            for item in self.app.resource_registry.list_templates():
                if uri.startswith(item.uri_template.split("{", 1)[0]):
                    self._check_scopes(item.required_scopes)
            return await self.app.resource_registry.read(uri, params.get("arguments"))
        if method == "resources/subscribe":
            self.subscriptions.add(params["uri"])
            return {"ok": True, "uri": params["uri"]}
        if method == "resources/unsubscribe":
            self.subscriptions.discard(params["uri"])
            return {"ok": True, "uri": params["uri"]}
        if method == "prompts/list":
            return {
                "prompts": [
                    {
                        "name": item.name,
                        "description": item.description,
                        "arguments": [arg.__dict__ for arg in item.arguments],
                        "requiredScopes": list(item.required_scopes),
                    }
                    for item in self.app.prompt_registry.list()
                ]
            }
        if method == "prompts/get":
            self._check_scopes(self.app.prompt_registry._prompts[params["name"]].required_scopes)
            return await self.app.prompt_registry.get(params["name"], params.get("arguments"))
        if method == "completions/complete":
            request = CompletionRequest(
                ref=params["ref"],
                argument=params.get("argument", ""),
                value=params.get("value", ""),
                context=params.get("context", {}),
            )
            result = await self.app.completion_registry.complete(request)
            return {"completion": {"values": result.values, "hasMore": result.has_more}}
        if method == "logging/setLevel":
            self.log_level = params.get("level", "info")
            return {"level": self.log_level}
        if method == "roots/list":
            roots = self._client_roots_provider() if self._client_roots_provider else []
            return {"roots": roots}
        if method == "sampling/createMessage":
            if self._sampling_handler is None:
                raise NotImplementedError("sampling unavailable")
            request = SamplingRequest(
                messages=params.get("messages", []),
                system_prompt=params.get("systemPrompt"),
                model_preferences=params.get("modelPreferences", {}),
                tools=params.get("tools", []),
            )
            result = await invoke_handler(self._sampling_handler, request)
            if isinstance(result, SamplingResult):
                return {
                    "message": result.message,
                    "model": result.model,
                    "stopReason": result.stop_reason,
                }
            return result
        if method == "elicitation/request":
            if self._elicitation_handler is None:
                raise NotImplementedError("elicitation unavailable")
            result = await invoke_handler(self._elicitation_handler, params)
            return result if isinstance(result, dict) else result.__dict__
        if method == "tasks/create":
            kind = params["kind"]
            handler = self.app._task_handlers.get(kind)
            task = await self.app.task_manager.create(kind, params.get("input", {}), handler=handler)
            self.notify("notifications/tasks/updated", _task_to_dict(task))
            return {"task": _task_to_dict(task)}
        if method == "tasks/list":
            return {"tasks": [_task_to_dict(task) for task in self.app.task_manager.list()]}
        if method == "tasks/get":
            return {"task": _task_to_dict(self.app.task_manager.get(params["taskId"]))}
        if method == "tasks/cancel":
            task = self.app.task_manager.cancel(params["taskId"])
            self.notify("notifications/tasks/updated", _task_to_dict(task))
            return {"task": _task_to_dict(task)}
        if method == "cancel":
            return {"cancelled": params.get("requestId")}
        raise NotImplementedError(f"unsupported method {method}")


def _call_result_to_dict(result: Any) -> dict[str, Any]:
    if result.status == "error":
        return {
            "isError": True,
            "error": result.error.code if result.error else "exec:error",
            "hint": result.error.hint if result.error else None,
        }
    payload: dict[str, Any] = {
        "isError": False,
        "summary": result.summary,
    }
    if result.handle is not None:
        payload["handle"] = {
            "id": result.handle.id,
            "kind": result.handle.kind,
            "summary": result.handle.summary,
            "ttl": result.handle.ttl,
        }
    else:
        payload["content"] = result.scalar
    if result.meta:
        payload["meta"] = result.meta
    return payload


def _task_to_dict(task: Any) -> dict[str, Any]:
    return {
        "taskId": task.task_id,
        "kind": task.kind,
        "status": task.status,
        "input": task.input,
        "result": task.result,
        "error": task.error,
        "metadata": task.metadata,
    }
