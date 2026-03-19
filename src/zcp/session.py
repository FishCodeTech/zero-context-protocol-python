from __future__ import annotations

import itertools
from typing import Any, Callable

from .capabilities import InitializeParams
from .protocol import request
from .server import ZCPServerSession


class ZCPClientSession:
    def __init__(
        self,
        server_session: ZCPServerSession,
        *,
        transport: str = "local",
        client_info: dict[str, Any] | None = None,
        roots_provider: Callable[[], list[dict[str, Any]]] | None = None,
        sampling_handler: Callable[[Any], Any] | None = None,
        elicitation_handler: Callable[[Any], Any] | None = None,
        log_handler: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.server_session = server_session
        self.transport = transport
        self.client_info = client_info or {"name": "zcp-client", "version": "0.1.0"}
        self.notifications: list[dict[str, Any]] = []
        self._ids = itertools.count(1)
        self.server_session.attach_client(
            roots_provider=roots_provider,
            sampling_handler=sampling_handler,
            elicitation_handler=elicitation_handler,
            log_handler=log_handler or self.notifications.append,
        )

    async def initialize(self, capabilities: dict[str, Any] | None = None) -> dict[str, Any]:
        params = InitializeParams(client_info=self.client_info, capabilities=capabilities or {}).__dict__
        return await self._request("initialize", params)

    async def initialized(self) -> dict[str, Any]:
        return await self._request("initialized", {})

    async def ping(self) -> dict[str, Any]:
        return await self._request("ping", {})

    async def list_tools(
        self,
        *,
        cursor: str | None = None,
        profile: str | None = None,
        groups: list[str] | None = None,
        exclude_groups: list[str] | None = None,
        stages: list[str] | None = None,
    ) -> dict[str, Any]:
        params = _cursor_params(cursor)
        if profile is not None:
            params["profile"] = profile
        if groups:
            params["groups"] = groups
        if exclude_groups:
            params["excludeGroups"] = exclude_groups
        if stages:
            params["stages"] = stages
        return await self._request("tools/list", params)

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        profile: str | None = None,
        meta: dict[str, Any] | None = None,
        task: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"name": name, "arguments": arguments}
        if profile is not None:
            payload["profile"] = profile
        if meta is not None:
            payload["meta"] = meta
        if task is not None:
            payload["task"] = task
        return await self._request("tools/call", payload)

    async def call_tool_as_task(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        ttl: int = 60000,
        poll_interval: int | None = None,
        profile: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        task: dict[str, Any] = {"ttl": ttl}
        if poll_interval is not None:
            task["pollInterval"] = poll_interval
        return await self.call_tool(name, arguments, profile=profile, meta=meta, task=task)

    async def list_resources(self, *, cursor: str | None = None) -> dict[str, Any]:
        return await self._request("resources/list", _cursor_params(cursor))

    async def list_resource_templates(self, *, cursor: str | None = None) -> dict[str, Any]:
        return await self._request("resources/templates/list", _cursor_params(cursor))

    async def read_resource(self, uri: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self._request("resources/read", {"uri": uri, "arguments": arguments or {}})

    async def subscribe_resource(self, uri: str) -> dict[str, Any]:
        return await self._request("resources/subscribe", {"uri": uri})

    async def unsubscribe_resource(self, uri: str) -> dict[str, Any]:
        return await self._request("resources/unsubscribe", {"uri": uri})

    async def list_prompts(self, *, cursor: str | None = None) -> dict[str, Any]:
        return await self._request("prompts/list", _cursor_params(cursor))

    async def get_prompt(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self._request("prompts/get", {"name": name, "arguments": arguments or {}})

    async def complete(
        self,
        ref: str | dict[str, Any],
        argument: str | dict[str, Any],
        value: str | None = None,
        *,
        context: dict[str, Any] | None = None,
        context_arguments: dict[str, str] | None = None,
        method: str = "completion/complete",
    ) -> dict[str, Any]:
        if isinstance(ref, str) and not isinstance(argument, dict):
            payload = {
                "ref": ref,
                "argument": argument,
                "value": value or "",
                "context": context or {},
            }
            return await self._request("completions/complete", payload)

        payload = {
            "ref": ref,
            "argument": argument if isinstance(argument, dict) else {"name": argument, "value": value or ""},
        }
        if context is not None:
            payload["context"] = context
        elif context_arguments is not None:
            payload["context"] = {"arguments": context_arguments}
        return await self._request(method, payload)

    async def set_logging_level(self, level: str) -> dict[str, Any]:
        return await self._request("logging/setLevel", {"level": level})

    async def list_roots(self) -> dict[str, Any]:
        return await self._request("roots/list", {})

    async def create_message(self, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        return await self._request("sampling/createMessage", {"messages": messages, **kwargs})

    async def elicit(self, kind: str, prompt: str, **kwargs: Any) -> dict[str, Any]:
        return await self._request("elicitation/request", {"kind": kind, "prompt": prompt, **kwargs})

    async def create_task(self, kind: str, input: dict[str, Any], *, task: dict[str, Any] | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"kind": kind, "input": input}
        if task is not None:
            payload["task"] = task
        return await self._request("tasks/create", payload)

    async def list_tasks(self, *, cursor: str | None = None) -> dict[str, Any]:
        return await self._request("tasks/list", _cursor_params(cursor))

    async def get_task(self, task_id: str) -> dict[str, Any]:
        return await self._request("tasks/get", {"taskId": task_id})

    async def get_task_result(self, task_id: str) -> dict[str, Any]:
        return await self._request("tasks/result", {"taskId": task_id})

    async def cancel_task(self, task_id: str) -> dict[str, Any]:
        return await self._request("tasks/cancel", {"taskId": task_id})

    async def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        payload = request(next(self._ids), method, params)
        response = await self.server_session.handle_message(payload)
        self.notifications.extend(self.server_session.drain_notifications())
        if response is None:
            return {}
        if "error" in response:
            raise RuntimeError(response["error"]["message"])
        return response["result"]


class ZCPSessionGroup:
    def __init__(self, sessions: list[ZCPClientSession]) -> None:
        self.sessions = sessions

    async def list_tools(self, **kwargs: Any) -> dict[str, Any]:
        tools: list[dict[str, Any]] = []
        for session in self.sessions:
            result = await session.list_tools(**kwargs)
            for tool in result["tools"]:
                tools.append({**tool, "_transport": session.transport})
        return {"tools": tools}

    async def list_resources(self) -> dict[str, Any]:
        resources: list[dict[str, Any]] = []
        templates: list[dict[str, Any]] = []
        for session in self.sessions:
            result = await session.list_resources()
            resources.extend([{**item, "_transport": session.transport} for item in result.get("resources", [])])
            templates.extend([{**item, "_transport": session.transport} for item in result.get("resourceTemplates", [])])
        return {"resources": resources, "resourceTemplates": templates}

    async def list_prompts(self) -> dict[str, Any]:
        prompts: list[dict[str, Any]] = []
        for session in self.sessions:
            result = await session.list_prompts()
            prompts.extend([{**item, "_transport": session.transport} for item in result["prompts"]])
        return {"prompts": prompts}


def _cursor_params(cursor: str | None) -> dict[str, Any]:
    return {} if cursor is None else {"cursor": cursor}
