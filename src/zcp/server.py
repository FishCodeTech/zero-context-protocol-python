from __future__ import annotations

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
    ElicitationField,
    ElicitationRequest,
    ElicitationResult,
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
    TaskExecutionContext,
    ToolRegistry,
    ValidationFailure,
    build_tool_from_callable,
    invoke_handler,
)


LOG_LEVELS = ["debug", "info", "notice", "warning", "error", "critical", "alert", "emergency"]
DEFAULT_PAGE_SIZE = 50


@dataclass
class RequestContext:
    session: "ZCPServerSession"
    request_id: str | None
    method: str
    progress_token: str | int | None = None
    task: TaskExecutionContext | None = None

    async def report_progress(self, progress: float, total: float | None = None, message: str | None = None) -> None:
        if self.progress_token is None:
            return
        self.session.emit_progress(
            progress_token=self.progress_token,
            progress=progress,
            total=total,
            message=message,
            related_request_id=self.request_id,
        )

    async def log(
        self,
        level: str,
        message: str,
        *,
        logger_name: str = "zcp",
        extra: dict[str, Any] | None = None,
    ) -> None:
        self.session.emit_log(level, message, data=extra, logger=logger_name)


@dataclass
class FastZCP:
    name: str
    version: str = "0.1.0"
    instructions: str | None = None
    title: str | None = None
    description: str | None = None
    website_url: str | None = None
    icons: list[dict[str, Any]] | None = None
    auth_profile: AuthProfile | None = None
    default_tool_profile: str | None = None
    default_tool_profiles: dict[str, str] = field(default_factory=dict)
    allow_client_tool_filters: bool = True
    semantic_workflow_profile: str = "semantic-workflow"
    semantic_group: str = "workflow"
    enforce_tool_visibility_on_call: bool = True
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
        title: str | None = None,
        description: str = "",
        input_schema: dict[str, Any],
        output_schema: dict[str, Any] | None = None,
        output_mode: str = "handle",
        defaults: dict[str, Any] | None = None,
        handle_kind: str = "generic",
        flags: frozenset[str] | None = None,
        inline_ok: bool = False,
        required_scopes: tuple[str, ...] = (),
        icons: list[dict[str, Any]] | None = None,
        annotations: dict[str, Any] | None = None,
        execution: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            self._tool_counter += 1
            alias = name or func.__name__
            tool = build_tool_from_callable(
                tool_id=str(self._tool_counter),
                alias=alias,
                title=title,
                description_short=description or (inspect.getdoc(func) or ""),
                input_schema=input_schema,
                output_schema=output_schema,
                handler=func,
                output_mode=output_mode,
                defaults=defaults,
                handle_kind=handle_kind,
                flags=flags,
                inline_ok=inline_ok,
                icons=icons,
                annotations=annotations,
                execution=execution,
                metadata=metadata,
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
        title: str | None = None,
        description: str = "",
        mime_type: str = "text/plain",
        subscribe: bool = False,
        annotations: dict[str, Any] | None = None,
        size: int | None = None,
        icons: list[dict[str, Any]] | None = None,
        meta: dict[str, Any] | None = None,
        required_scopes: tuple[str, ...] = (),
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            descriptor = ResourceDescriptor(
                uri=uri,
                name=name or func.__name__,
                title=title,
                description=description or (inspect.getdoc(func) or ""),
                mime_type=mime_type,
                subscribe=subscribe,
                handler=func,
                annotations=annotations or {},
                size=size,
                icons=icons,
                meta=meta,
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
        title: str | None = None,
        description: str = "",
        mime_type: str = "text/plain",
        annotations: dict[str, Any] | None = None,
        icons: list[dict[str, Any]] | None = None,
        meta: dict[str, Any] | None = None,
        required_scopes: tuple[str, ...] = (),
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            descriptor = ResourceTemplate(
                uri_template=uri_template,
                name=name or func.__name__,
                title=title,
                description=description or (inspect.getdoc(func) or ""),
                mime_type=mime_type,
                handler=func,
                annotations=annotations or {},
                icons=icons,
                meta=meta,
                required_scopes=required_scopes,
            )
            self.resource_registry.register_template(descriptor)
            return func

        return decorator

    def prompt(
        self,
        *,
        name: str | None = None,
        title: str | None = None,
        description: str = "",
        arguments: list[PromptArgument] | None = None,
        icons: list[dict[str, Any]] | None = None,
        meta: dict[str, Any] | None = None,
        required_scopes: tuple[str, ...] = (),
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            descriptor = PromptDescriptor(
                name=name or func.__name__,
                title=title,
                description=description or (inspect.getdoc(func) or ""),
                arguments=arguments or [],
                handler=func,
                icons=icons,
                meta=meta,
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
        def decorator(func: Callable[[dict[str, Any]], Any]) -> Callable[[dict[str, Any]], Any]:
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
        if not self.app.completion_registry._handlers:
            capabilities.pop("completions", None)
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
        params = message.get("params") or {}
        request_context = RequestContext(
            session=self,
            request_id=None if "id" not in message else str(message.get("id")),
            method=method,
            progress_token=_extract_progress_token(params),
        )
        try:
            result = await self._dispatch(method, params, request_context)
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

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        payload = notification(method, params or {})
        self._notifications.append(payload)
        if method in {"notifications/message", "notifications/logging/message"} and self._log_handler is not None:
            self._log_handler(params or {})

    def emit_log(self, level: str, message: str, data: dict[str, Any] | None = None, logger: str = "zcp") -> None:
        normalized = level if level in LOG_LEVELS else "info"
        if LOG_LEVELS.index(normalized) < LOG_LEVELS.index(self.log_level):
            return
        payload: Any = message if not data else {"message": message, **data}
        self.notify("notifications/message", {"level": normalized, "logger": logger, "data": payload})

    def emit_progress(
        self,
        *,
        progress_token: str | int,
        progress: float,
        total: float | None = None,
        message: str | None = None,
        related_request_id: str | None = None,
    ) -> None:
        params: dict[str, Any] = {"progressToken": progress_token, "progress": float(progress)}
        if total is not None:
            params["total"] = float(total)
        if message is not None:
            params["message"] = message
        if related_request_id is not None:
            params["relatedRequestId"] = related_request_id
        self.notify("notifications/progress", params)

    def emit_resource_updated(self, uri: str) -> None:
        if uri in self.subscriptions:
            self.notify("notifications/resources/updated", {"uri": uri})

    def emit_resources_changed(self) -> None:
        self.notify("notifications/resources/list_changed", {})

    def emit_tools_changed(self) -> None:
        self.notify("notifications/tools/list_changed", {})

    def emit_prompts_changed(self) -> None:
        self.notify("notifications/prompts/list_changed", {})

    def emit_roots_changed(self) -> None:
        self.notify("notifications/roots/list_changed", {})

    async def _dispatch(self, method: str, params: dict[str, Any], request_context: RequestContext) -> Any:
        if method == "initialize":
            result = InitializeResult(
                protocol_version=PROTOCOL_VERSION,
                server_info=_server_info(self.app),
                capabilities=self.capabilities_dict(),
                auth=self.app.auth_profile.__dict__ if self.app.auth_profile else None,
            )
            return result.to_dict()

        if method in {"initialized", "notifications/initialized"}:
            return {"ok": True}

        if method == "ping":
            return {"ok": True, "sessionId": self.state.session_id}

        if method == "tools/list":
            tool_dicts = [_tool_to_dict(tool) for tool in _select_tools(self.app, params)]
            tools, next_cursor = _paginate(tool_dicts, params.get("cursor"))
            result = {"tools": tools, "nextCursor": next_cursor}
            profile = _effective_tool_profile(self.app, params)
            if profile:
                result["profile"] = profile
            return result

        if method == "tools/call":
            name = str(params.get("name", ""))
            if self.app.enforce_tool_visibility_on_call and not _tool_is_exposed(self.app, params, name):
                raise KeyError(name)
            tool = self.app.tool_registry.get_by_alias(params["name"])
            self._check_scopes(tool.required_scopes)
            task_meta = params.get("task")
            task_mode = _task_support_mode(tool)
            if task_meta is not None:
                if task_mode == "forbidden":
                    raise NotImplementedError("tool does not support task-augmented invocation")
                task = await self.app.task_manager.create(
                    f"tool:{tool.alias}",
                    params.get("arguments", {}),
                    handler=self._build_tool_task_handler(tool, request_context),
                    ttl_ms=(task_meta or {}).get("ttl"),
                    poll_interval_ms=(task_meta or {}).get("pollInterval"),
                    metadata={"toolName": tool.alias},
                    on_update=self._notify_task_status,
                    context_factory=lambda task_id: TaskExecutionContext(self.app.task_manager, task_id, self),
                )
                return {"task": _task_to_dict(task)}
            if task_mode == "required":
                raise NotImplementedError("tool requires task-augmented invocation")
            request = CallRequest(
                cid=self.state.next_cid(),
                tool_id=tool.tool_id,
                alias=tool.alias,
                arguments=params.get("arguments", {}),
                raw_call_id=str(params.get("callId")) if params.get("callId") is not None else None,
                context=request_context,
            )
            result = await self.executor.execute_call(request)
            return _call_result_to_dict(result, tool)

        if method == "resources/list":
            resources, next_cursor = _paginate(
                [_resource_to_dict(item) for item in self.app.resource_registry.list()],
                params.get("cursor"),
            )
            return {
                "resources": resources,
                "resourceTemplates": [_resource_template_to_dict(item) for item in self.app.resource_registry.list_templates()],
                "nextCursor": next_cursor,
            }

        if method == "resources/templates/list":
            templates, next_cursor = _paginate(
                [_resource_template_to_dict(item) for item in self.app.resource_registry.list_templates()],
                params.get("cursor"),
            )
            return {"resourceTemplates": templates, "nextCursor": next_cursor}

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
            prompts, next_cursor = _paginate(
                [_prompt_to_dict(item) for item in self.app.prompt_registry.list()],
                params.get("cursor"),
            )
            return {"prompts": prompts, "nextCursor": next_cursor}

        if method == "prompts/get":
            descriptor = self.app.prompt_registry._prompts[params["name"]]
            self._check_scopes(descriptor.required_scopes)
            return await self.app.prompt_registry.get(params["name"], params.get("arguments"))

        if method in {"completion/complete", "completions/complete"}:
            ref = params.get("ref")
            argument = params.get("argument", "")
            value = params.get("value", "")
            if isinstance(argument, dict):
                value = argument.get("value", value)
            context = params.get("context") or {}
            request = CompletionRequest(ref=ref, argument=argument, value=value, context=context)
            result = await self.app.completion_registry.complete(request)
            return {
                "completion": {
                    "values": result.values,
                    "total": result.total,
                    "hasMore": result.has_more,
                }
            }

        if method == "logging/setLevel":
            requested = params.get("level", "info")
            self.log_level = requested if requested in LOG_LEVELS else "info"
            return {"level": self.log_level}

        if method == "roots/list":
            roots = self._client_roots_provider() if self._client_roots_provider else []
            return {"roots": roots}

        if method == "sampling/createMessage":
            if self._sampling_handler is None:
                raise NotImplementedError("sampling unavailable")
            request = _sampling_request_from_params(params)
            result = await invoke_handler(self._sampling_handler, request)
            return _sampling_result_to_dict(result)

        if method in {"elicitation/create", "elicitation/request"}:
            if self._elicitation_handler is None:
                raise NotImplementedError("elicitation unavailable")
            request = _elicitation_request_from_params(params)
            result = await invoke_handler(self._elicitation_handler, _elicitation_request_payload(request))
            return _elicitation_result_to_dict(result)

        if method == "tasks/create":
            kind = params["kind"]
            handler = self.app._task_handlers.get(kind)
            task_meta = params.get("task") or {}
            task = await self.app.task_manager.create(
                kind,
                params.get("input", {}),
                handler=handler,
                ttl_ms=task_meta.get("ttl"),
                poll_interval_ms=task_meta.get("pollInterval"),
                metadata={"source": "tasks/create"},
                on_update=self._notify_task_status,
                context_factory=lambda task_id: TaskExecutionContext(self.app.task_manager, task_id, self),
            )
            return {"task": _task_to_dict(task)}

        if method == "tasks/list":
            tasks, next_cursor = _paginate(
                [_task_metadata_to_dict(task) for task in self.app.task_manager.list()],
                params.get("cursor"),
            )
            return {"tasks": tasks, "nextCursor": next_cursor}

        if method == "tasks/get":
            return {"task": _task_metadata_to_dict(self.app.task_manager.get(_task_id_from_params(params)))}

        if method == "tasks/result":
            task = self.app.task_manager.get(_task_id_from_params(params))
            return _task_payload_result(task)

        if method == "tasks/cancel":
            task = await self.app.task_manager.cancel(_task_id_from_params(params), on_update=self._notify_task_status)
            return {"task": _task_metadata_to_dict(task)}

        if method in {"cancel", "notifications/cancelled"}:
            return {"cancelled": params.get("requestId") or params.get("request_id")}

        raise NotImplementedError(f"unsupported method {method}")

    def _build_tool_task_handler(self, tool: Any, parent_context: RequestContext) -> Callable[[dict[str, Any]], Any]:
        async def run(payload: dict[str, Any]) -> dict[str, Any]:
            task_context = payload.get("task") if isinstance(payload.get("task"), TaskExecutionContext) else None
            tool_context = RequestContext(
                session=self,
                request_id=parent_context.request_id,
                method=parent_context.method,
                progress_token=parent_context.progress_token,
                task=task_context,
            )
            request = CallRequest(
                cid=self.state.next_cid(),
                tool_id=tool.tool_id,
                alias=tool.alias,
                arguments={key: value for key, value in payload.items() if key not in {"task", "ctx"}},
                raw_call_id=None,
                context=tool_context,
            )
            result = await self.executor.execute_call(request)
            return _call_result_to_dict(result, tool)

        return run

    async def _notify_task_status(self, task: Any) -> None:
        self.notify("notifications/tasks/status", _task_metadata_to_dict(task))


def _server_info(app: FastZCP) -> dict[str, Any]:
    payload = {
        "name": app.name,
        "version": app.version,
        "title": app.title,
        "description": app.description or app.instructions,
        "websiteUrl": app.website_url,
        "icons": app.icons,
    }
    return {key: value for key, value in payload.items() if value is not None}


def _paginate(items: list[dict[str, Any]], cursor: str | None, page_size: int = DEFAULT_PAGE_SIZE) -> tuple[list[dict[str, Any]], str | None]:
    start = 0
    if cursor:
        try:
            start = max(0, int(cursor))
        except ValueError:
            start = 0
    end = start + page_size
    next_cursor = str(end) if end < len(items) else None
    return items[start:end], next_cursor


def _select_tools(app: FastZCP, params: dict[str, Any]) -> list[Any]:
    tools = app.tool_registry.subset().tools
    profile = _effective_tool_profile(app, params)
    include_groups = _normalize_filter_values(params.get("groups") or params.get("includeGroups")) if app.allow_client_tool_filters else set()
    exclude_groups = _normalize_filter_values(params.get("excludeGroups")) if app.allow_client_tool_filters else set()
    stages = _normalize_filter_values(params.get("stages")) if app.allow_client_tool_filters else set()

    if profile == app.semantic_workflow_profile:
        workflow_tools = [tool for tool in tools if app.semantic_group in _tool_groups(tool)]
        if workflow_tools:
            tools = workflow_tools

    if include_groups:
        tools = [tool for tool in tools if _tool_groups(tool) & include_groups]
    if exclude_groups:
        tools = [tool for tool in tools if not (_tool_groups(tool) & exclude_groups)]
    if stages:
        tools = [tool for tool in tools if _tool_stages(tool) & stages]
    return tools


def _effective_tool_profile(app: FastZCP, params: dict[str, Any]) -> str | None:
    if app.allow_client_tool_filters:
        requested = str(params.get("profile") or "").strip()
        if requested:
            return requested
    surface = _tool_surface(params)
    if surface in app.default_tool_profiles:
        value = str(app.default_tool_profiles[surface]).strip()
        return value or None
    fallback = str(app.default_tool_profile or "").strip()
    return fallback or None


def _tool_surface(params: dict[str, Any]) -> str:
    meta = params.get("_meta") or params.get("meta") or {}
    if not isinstance(meta, dict):
        meta = {}
    raw = params.get("surface") or meta.get("protocolSurface") or meta.get("surface") or "native"
    value = str(raw).strip().lower()
    return value or "native"


def _tool_is_exposed(app: FastZCP, params: dict[str, Any], name: str) -> bool:
    if not name:
        return False
    return any(tool.alias == name for tool in _select_tools(app, params))


def _normalize_filter_values(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        return {item.strip() for item in value.split(",") if item.strip()}
    if isinstance(value, list):
        return {str(item).strip() for item in value if str(item).strip()}
    return set()


def _tool_groups(tool: Any) -> set[str]:
    metadata = getattr(tool, "metadata", {}) or {}
    groups = metadata.get("groups") or []
    return {str(item) for item in groups if str(item)}


def _tool_stages(tool: Any) -> set[str]:
    metadata = getattr(tool, "metadata", {}) or {}
    stages = metadata.get("stages") or []
    return {str(item) for item in stages if str(item)}


def _extract_progress_token(params: dict[str, Any]) -> str | int | None:
    meta = params.get("_meta") or params.get("meta") or {}
    return meta.get("progressToken") or meta.get("progress_token") or params.get("progressToken")


def _task_support_mode(tool: Any) -> str:
    execution = tool.execution or {}
    mode = execution.get("taskSupport") or execution.get("task_support") or "forbidden"
    if mode not in {"forbidden", "optional", "required"}:
        return "forbidden"
    return mode


def _sampling_request_from_params(params: dict[str, Any]) -> SamplingRequest:
    messages = params.get("messages", [])
    if not isinstance(messages, list):
        raise ValidationFailure("invalid:messages")
    normalized_messages: list[dict[str, Any]] = []
    for item in messages:
        if not isinstance(item, dict) or "role" not in item:
            raise ValidationFailure("invalid:messages")
        normalized_messages.append(
            {
                "role": item["role"],
                "content": item.get("content"),
                **({"name": item["name"]} if item.get("name") is not None else {}),
            }
        )
    return SamplingRequest(
        messages=normalized_messages,
        system_prompt=params.get("systemPrompt") or params.get("system_prompt"),
        model_preferences=params.get("modelPreferences") or params.get("model_preferences") or {},
        include_context=params.get("includeContext") or params.get("include_context"),
        temperature=params.get("temperature"),
        max_tokens=params.get("maxTokens") or params.get("max_tokens"),
        stop_sequences=params.get("stopSequences") or params.get("stop_sequences"),
        metadata=params.get("metadata"),
        tools=params.get("tools", []),
        tool_choice=params.get("toolChoice") or params.get("tool_choice"),
    )


def _sampling_result_to_dict(result: Any) -> dict[str, Any]:
    if isinstance(result, SamplingResult):
        return {
            "message": result.message,
            "role": result.message.get("role"),
            "content": result.message.get("content"),
            "model": result.model,
            "stopReason": result.stop_reason,
        }
    if isinstance(result, dict):
        if "message" not in result and "content" in result:
            return {"message": {"role": result.get("role", "assistant"), "content": result["content"]}, **result}
        return result
    raise ValidationFailure("invalid:sampling_result")


def _elicitation_request_from_params(params: dict[str, Any]) -> ElicitationRequest:
    raw_fields = params.get("fields") or params.get("requestedSchema", {}).get("properties", {})
    fields: list[ElicitationField] = []
    if isinstance(raw_fields, list):
        for item in raw_fields:
            if not isinstance(item, dict) or "name" not in item or "type" not in item:
                raise ValidationFailure("invalid:elicitation_fields")
            fields.append(
                ElicitationField(
                    name=item["name"],
                    type=item["type"],
                    label=item.get("label", item["name"]),
                    required=bool(item.get("required", False)),
                    options=list(item.get("options", [])),
                )
            )
    request_kind = params.get("kind") or ("url" if params.get("url") else "form")
    prompt = params.get("prompt") or params.get("message")
    if not prompt:
        raise ValidationFailure("missing:prompt")
    return ElicitationRequest(
        kind=request_kind,
        prompt=prompt,
        fields=fields,
        url=params.get("url"),
        metadata=params.get("metadata") or {},
    )


def _elicitation_result_to_dict(result: Any) -> dict[str, Any]:
    if isinstance(result, ElicitationResult):
        status = result.status
        return {
            "action": {
                "submitted": "accept",
                "accepted": "accept",
                "declined": "decline",
                "cancelled": "cancel",
            }.get(status, status),
            "content": result.data,
        }
    if isinstance(result, dict):
        if "action" in result:
            return result
        if "status" in result:
            return {
                "action": {
                    "submitted": "accept",
                    "accepted": "accept",
                    "declined": "decline",
                    "cancelled": "cancel",
                }.get(result["status"], result["status"]),
                "content": result.get("data") or result.get("content"),
            }
        return {"action": "accept", "content": result}
    raise ValidationFailure("invalid:elicitation_result")


def _elicitation_request_payload(request: ElicitationRequest) -> dict[str, Any]:
    return {
        "kind": request.kind,
        "prompt": request.prompt,
        "fields": [field.__dict__ for field in request.fields],
        "url": request.url,
        "metadata": request.metadata,
    }


def _tool_to_dict(tool: Any) -> dict[str, Any]:
    payload = {
        "id": tool.tool_id,
        "name": tool.alias,
        "title": tool.title,
        "description": tool.description_short,
        "inputSchema": tool.input_schema,
        "outputSchema": tool.output_schema,
        "outputMode": tool.output_mode,
        "flags": sorted(tool.flags),
        "requiredScopes": list(tool.required_scopes),
        "icons": tool.icons,
        "annotations": tool.annotations,
        "execution": tool.execution,
        "_meta": tool.metadata or None,
    }
    return {key: value for key, value in payload.items() if value is not None}


def _resource_to_dict(item: Any) -> dict[str, Any]:
    payload = {
        "uri": item.uri,
        "name": item.name,
        "title": item.title,
        "description": item.description,
        "mimeType": item.mime_type,
        "size": item.size,
        "subscribe": item.subscribe,
        "annotations": item.annotations,
        "icons": item.icons,
        "_meta": item.meta,
        "requiredScopes": list(item.required_scopes),
    }
    return {key: value for key, value in payload.items() if value is not None}


def _resource_template_to_dict(item: Any) -> dict[str, Any]:
    payload = {
        "uriTemplate": item.uri_template,
        "name": item.name,
        "title": item.title,
        "description": item.description,
        "mimeType": item.mime_type,
        "annotations": item.annotations,
        "icons": item.icons,
        "_meta": item.meta,
        "requiredScopes": list(item.required_scopes),
    }
    return {key: value for key, value in payload.items() if value is not None}


def _prompt_to_dict(item: Any) -> dict[str, Any]:
    payload = {
        "name": item.name,
        "title": item.title,
        "description": item.description,
        "arguments": [arg.__dict__ for arg in item.arguments],
        "icons": item.icons,
        "_meta": item.meta,
        "requiredScopes": list(item.required_scopes),
    }
    return {key: value for key, value in payload.items() if value is not None}


def _call_result_to_dict(result: Any, tool: Any) -> dict[str, Any]:
    if result.status == "error":
        payload: dict[str, Any] = {
            "isError": True,
            "error": result.error.code if result.error else "exec:error",
            "hint": result.error.hint if result.error else None,
        }
        if result.error and result.error.detail:
            payload["content"] = [{"type": "text", "text": result.error.detail}]
        return payload

    payload: dict[str, Any] = {"isError": False, "summary": result.summary}
    if result.handle is not None:
        payload["handle"] = {
            "id": result.handle.id,
            "kind": result.handle.kind,
            "summary": result.handle.summary,
            "ttl": result.handle.ttl,
        }
        payload["content"] = [{"type": "text", "text": result.handle.summary}]
    else:
        payload["content"] = result.scalar
        if isinstance(result.scalar, list) and all(isinstance(item, dict) and "type" in item for item in result.scalar):
            pass
        else:
            payload["structuredContent"] = result.scalar
            if tool.output_schema is not None or not isinstance(result.scalar, list):
                payload["content"] = [{"type": "text", "text": json.dumps(result.scalar, ensure_ascii=False, default=str)}]
    if result.meta:
        payload["meta"] = result.meta
        payload["_meta"] = result.meta
    return payload


def _task_id_from_params(params: dict[str, Any]) -> str:
    return params.get("taskId") or params["task_id"]


def _task_metadata_to_dict(task: Any) -> dict[str, Any]:
    payload = {
        "taskId": task.task_id,
        "status": task.status,
        "statusMessage": task.status_message,
        "createdAt": task.created_at.isoformat(),
        "lastUpdatedAt": task.last_updated_at.isoformat(),
        "ttl": task.ttl_ms,
        "pollInterval": task.poll_interval_ms,
    }
    return {key: value for key, value in payload.items() if value is not None}


def _task_to_dict(task: Any) -> dict[str, Any]:
    payload = {
        **_task_metadata_to_dict(task),
        "kind": task.kind,
        "input": task.input,
        "result": task.result,
        "error": task.error,
        "metadata": task.metadata,
    }
    return {key: value for key, value in payload.items() if value is not None}


def _task_payload_result(task: Any) -> dict[str, Any]:
    if isinstance(task.result, dict):
        return task.result
    return {"result": task.result}
