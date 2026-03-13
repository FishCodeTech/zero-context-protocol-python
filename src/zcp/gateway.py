from __future__ import annotations

import json
from typing import Any

from .protocol import request
from .server import ZCPServerSession
from .session import ZCPClientSession


class MCPGatewayServer:
    def __init__(self, server_session: ZCPServerSession) -> None:
        self.server_session = server_session

    async def handle_message(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        params = message.get("params", {})
        if method == "notifications/initialized":
            return await self.server_session.handle_message({"method": "initialized", "params": params})
        if method == "tools/call":
            zcp_params = {"name": params["name"], "arguments": params.get("arguments", {})}
            response = await self.server_session.handle_message(request(message["id"], "tools/call", zcp_params))
            return _zcp_response_to_mcp_tool_call(response)
        passthrough = {
            "initialize": "initialize",
            "initialized": "initialized",
            "ping": "ping",
            "tools/list": "tools/list",
            "resources/list": "resources/list",
            "resources/read": "resources/read",
            "prompts/list": "prompts/list",
            "prompts/get": "prompts/get",
            "completions/complete": "completions/complete",
            "roots/list": "roots/list",
            "sampling/createMessage": "sampling/createMessage",
            "elicitation/request": "elicitation/request",
            "tasks/create": "tasks/create",
            "tasks/list": "tasks/list",
            "tasks/get": "tasks/get",
            "tasks/cancel": "tasks/cancel",
        }
        if method not in passthrough:
            return {"jsonrpc": "2.0", "id": message.get("id"), "error": {"code": -32601, "message": f"unsupported:{method}"}}
        response = await self.server_session.handle_message(request(message.get("id"), passthrough[method], params))
        if response is None:
            return None
        return _zcp_response_to_mcp(response, method)


class MCPGatewayClient:
    def __init__(self, session: ZCPClientSession) -> None:
        self.session = session

    async def list_tools(self) -> dict[str, Any]:
        result = await self.session.list_tools()
        return {
            "tools": [
                {
                    "name": tool["name"],
                    "title": tool["name"],
                    "description": tool["description"],
                    "inputSchema": tool["inputSchema"],
                }
                for tool in result["tools"]
            ]
        }

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = await self.session.call_tool(name, arguments)
        if result["isError"]:
            return {"content": [{"type": "text", "text": result["error"]}], "isError": True}
        content = result.get("content")
        if content is None and "handle" in result:
            content = result["handle"]
        return {
            "content": [{"type": "text", "text": json.dumps(content, ensure_ascii=True)}],
            "structuredContent": content,
            "isError": False,
        }


def _zcp_response_to_mcp(response: dict[str, Any], method: str) -> dict[str, Any]:
    if "error" in response:
        return response
    result = response["result"]
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": response["id"],
            "result": {
                "protocolVersion": result["protocol_version"],
                "serverInfo": result["server_info"],
                "capabilities": result["capabilities"],
            },
        }
    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": response["id"],
            "result": {
                "tools": [
                    {
                        "name": tool["name"],
                        "title": tool["name"],
                        "description": tool["description"],
                        "inputSchema": tool["inputSchema"],
                    }
                    for tool in result["tools"]
                ]
            },
        }
    if method == "resources/read":
        contents = result["contents"]
        if isinstance(contents, (dict, list)):
            text = json.dumps(contents, ensure_ascii=True)
        else:
            text = str(contents)
        return {
            "jsonrpc": "2.0",
            "id": response["id"],
            "result": {
                "contents": [
                    {
                        "uri": result["uri"],
                        "mimeType": result["mimeType"],
                        "text": text,
                    }
                ]
            },
        }
    if method == "prompts/get":
        return {
            "jsonrpc": "2.0",
            "id": response["id"],
            "result": {
                "name": result["name"],
                "description": result.get("description"),
                "messages": [
                    {
                        "role": message["role"],
                        "content": {
                            "type": "text",
                            "text": message["content"],
                        },
                    }
                    for message in result["messages"]
                ],
            },
        }
    if method == "completions/complete":
        return {"jsonrpc": "2.0", "id": response["id"], "result": result}
    return {"jsonrpc": "2.0", "id": response["id"], "result": result}


def _zcp_response_to_mcp_tool_call(response: dict[str, Any]) -> dict[str, Any]:
    if "error" in response:
        return response
    result = response["result"]
    if result["isError"]:
        return {
            "jsonrpc": "2.0",
            "id": response["id"],
            "result": {
                "content": [{"type": "text", "text": result["error"]}],
                "isError": True,
            },
        }
    content = result.get("content")
    if content is None and "handle" in result:
        content = result["handle"]
    return {
        "jsonrpc": "2.0",
        "id": response["id"],
        "result": {
            "content": [{"type": "text", "text": json.dumps(content, ensure_ascii=True)}],
            "structuredContent": content,
            "isError": False,
        },
    }
