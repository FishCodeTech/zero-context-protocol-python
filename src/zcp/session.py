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

    async def list_tools(self) -> dict[str, Any]:
        return await self._request("tools/list", {})

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return await self._request("tools/call", {"name": name, "arguments": arguments})

    async def list_resources(self) -> dict[str, Any]:
        return await self._request("resources/list", {})

    async def read_resource(self, uri: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self._request("resources/read", {"uri": uri, "arguments": arguments or {}})

    async def subscribe_resource(self, uri: str) -> dict[str, Any]:
        return await self._request("resources/subscribe", {"uri": uri})

    async def unsubscribe_resource(self, uri: str) -> dict[str, Any]:
        return await self._request("resources/unsubscribe", {"uri": uri})

    async def list_prompts(self) -> dict[str, Any]:
        return await self._request("prompts/list", {})

    async def get_prompt(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self._request("prompts/get", {"name": name, "arguments": arguments or {}})

    async def complete(self, ref: str, argument: str, value: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self._request(
            "completions/complete",
            {"ref": ref, "argument": argument, "value": value, "context": context or {}},
        )

    async def set_logging_level(self, level: str) -> dict[str, Any]:
        return await self._request("logging/setLevel", {"level": level})

    async def list_roots(self) -> dict[str, Any]:
        return await self._request("roots/list", {})

    async def create_message(self, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        return await self._request("sampling/createMessage", {"messages": messages, **kwargs})

    async def elicit(self, kind: str, prompt: str, **kwargs: Any) -> dict[str, Any]:
        return await self._request("elicitation/request", {"kind": kind, "prompt": prompt, **kwargs})

    async def create_task(self, kind: str, input: dict[str, Any]) -> dict[str, Any]:
        return await self._request("tasks/create", {"kind": kind, "input": input})

    async def list_tasks(self) -> dict[str, Any]:
        return await self._request("tasks/list", {})

    async def get_task(self, task_id: str) -> dict[str, Any]:
        return await self._request("tasks/get", {"taskId": task_id})

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

    async def list_tools(self) -> dict[str, Any]:
        tools: list[dict[str, Any]] = []
        for session in self.sessions:
            result = await session.list_tools()
            for tool in result["tools"]:
                tools.append({**tool, "_transport": session.transport})
        return {"tools": tools}

    async def list_resources(self) -> dict[str, Any]:
        resources: list[dict[str, Any]] = []
        templates: list[dict[str, Any]] = []
        for session in self.sessions:
            result = await session.list_resources()
            resources.extend([{**item, "_transport": session.transport} for item in result["resources"]])
            templates.extend([{**item, "_transport": session.transport} for item in result["resourceTemplates"]])
        return {"resources": resources, "resourceTemplates": templates}

    async def list_prompts(self) -> dict[str, Any]:
        prompts: list[dict[str, Any]] = []
        for session in self.sessions:
            result = await session.list_prompts()
            prompts.extend([{**item, "_transport": session.transport} for item in result["prompts"]])
        return {"prompts": prompts}
