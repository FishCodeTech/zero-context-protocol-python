from __future__ import annotations

import base64
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
        if method in {"notifications/initialized", "initialized"}:
            return await self.server_session.handle_message({"method": "initialized", "params": params})
        if method == "notifications/cancelled":
            await self.server_session.handle_message({"method": "notifications/cancelled", "params": params})
            return None
        if method == "tools/call":
            tool_meta = _merge_surface_meta(params, surface="mcp")
            zcp_params = {
                "name": params["name"],
                "arguments": params.get("arguments", {}),
                "meta": tool_meta,
            }
            if "profile" in params:
                zcp_params["profile"] = params["profile"]
            response = await self.server_session.handle_message(request(message["id"], "tools/call", zcp_params))
            return _zcp_response_to_mcp_tool_call(response)

        passthrough = {
            "initialize": "initialize",
            "initialized": "initialized",
            "ping": "ping",
            "tools/list": "tools/list",
            "resources/list": "resources/list",
            "resources/templates/list": "resources/templates/list",
            "resources/read": "resources/read",
            "resources/subscribe": "resources/subscribe",
            "resources/unsubscribe": "resources/unsubscribe",
            "prompts/list": "prompts/list",
            "prompts/get": "prompts/get",
            "completion/complete": "completion/complete",
            "completions/complete": "completions/complete",
            "logging/setLevel": "logging/setLevel",
            "roots/list": "roots/list",
            "sampling/createMessage": "sampling/createMessage",
            "elicitation/create": "elicitation/create",
            "tasks/create": "tasks/create",
            "tasks/list": "tasks/list",
            "tasks/get": "tasks/get",
            "tasks/result": "tasks/result",
            "tasks/cancel": "tasks/cancel",
        }
        if method not in passthrough:
            return {"jsonrpc": "2.0", "id": message.get("id"), "error": {"code": -32601, "message": f"unsupported:{method}"}}
        forwarded_params = params
        if method == "tools/list":
            forwarded_params = {**params, "_meta": _merge_surface_meta(params, surface="mcp")}
        response = await self.server_session.handle_message(request(message.get("id"), passthrough[method], forwarded_params))
        if response is None:
            return None
        return _zcp_response_to_mcp(response, method)


class MCPGatewayClient:
    def __init__(self, session: ZCPClientSession) -> None:
        self.session = session

    async def list_tools(self) -> dict[str, Any]:
        result = await self.session.list_tools()
        return {"tools": [_mcp_tool(tool) for tool in result["tools"]], "nextCursor": result.get("nextCursor")}

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = await self.session.call_tool(name, arguments)
        if result["isError"]:
            return {
                "content": result.get("content") or [{"type": "text", "text": result["error"]}],
                "isError": True,
            }
        content = _ensure_tool_content(result)
        payload = {
            "content": content,
            "structuredContent": result.get("structuredContent"),
            "isError": False,
        }
        if "_meta" in result:
            payload["_meta"] = result["_meta"]
        return payload


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
                "tools": [_mcp_tool(tool) for tool in result["tools"]],
                **({"nextCursor": result["nextCursor"]} if result.get("nextCursor") is not None else {}),
            },
        }
    if method == "resources/list":
        return {
            "jsonrpc": "2.0",
            "id": response["id"],
            "result": {
                "resources": [_mcp_resource(item) for item in result["resources"]],
                **({"nextCursor": result["nextCursor"]} if result.get("nextCursor") is not None else {}),
            },
        }
    if method == "resources/templates/list":
        return {
            "jsonrpc": "2.0",
            "id": response["id"],
            "result": {
                "resourceTemplates": [_mcp_resource_template(item) for item in result["resourceTemplates"]],
                **({"nextCursor": result["nextCursor"]} if result.get("nextCursor") is not None else {}),
            },
        }
    if method == "resources/read":
        return {
            "jsonrpc": "2.0",
            "id": response["id"],
            "result": {
                "contents": _resource_contents(result["uri"], result.get("mimeType"), result.get("contents")),
            },
        }
    if method == "prompts/list":
        return {
            "jsonrpc": "2.0",
            "id": response["id"],
            "result": {
                "prompts": [_mcp_prompt(prompt) for prompt in result["prompts"]],
                **({"nextCursor": result["nextCursor"]} if result.get("nextCursor") is not None else {}),
            },
        }
    if method == "prompts/get":
        return {
            "jsonrpc": "2.0",
            "id": response["id"],
            "result": {
                "name": result["name"],
                "description": result.get("description"),
                "messages": [_mcp_prompt_message(message) for message in result["messages"]],
            },
        }
    if method in {"completion/complete", "completions/complete"}:
        return {"jsonrpc": "2.0", "id": response["id"], "result": result}
    if method in {"tasks/get", "tasks/cancel", "tasks/create"}:
        return {"jsonrpc": "2.0", "id": response["id"], "result": {"task": result["task"]}}
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
                "content": result.get("content") or [{"type": "text", "text": result["error"]}],
                "isError": True,
            },
        }
    payload = {
        "content": _ensure_tool_content(result),
        "isError": False,
    }
    if isinstance(result.get("structuredContent"), dict):
        payload["structuredContent"] = result["structuredContent"]
    if result.get("_meta") is not None:
        payload["_meta"] = result["_meta"]
    return {"jsonrpc": "2.0", "id": response["id"], "result": payload}


def _mcp_tool(tool: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "name": tool["name"],
        "title": tool.get("title") or (tool.get("annotations") or {}).get("title"),
        "description": tool.get("description"),
        "inputSchema": tool["inputSchema"],
        "outputSchema": tool.get("outputSchema"),
        "icons": tool.get("icons"),
        "annotations": tool.get("annotations"),
        "_meta": tool.get("_meta"),
        "execution": tool.get("execution"),
    }
    return {key: value for key, value in payload.items() if value is not None}


def _mcp_resource(item: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "uri": item["uri"],
        "name": item["name"],
        "title": item.get("title"),
        "description": item.get("description"),
        "mimeType": item.get("mimeType"),
        "size": item.get("size"),
        "icons": item.get("icons"),
        "annotations": item.get("annotations"),
        "_meta": item.get("_meta"),
    }
    return {key: value for key, value in payload.items() if value is not None}


def _mcp_resource_template(item: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "uriTemplate": item["uriTemplate"],
        "name": item["name"],
        "title": item.get("title"),
        "description": item.get("description"),
        "mimeType": item.get("mimeType"),
        "icons": item.get("icons"),
        "annotations": item.get("annotations"),
        "_meta": item.get("_meta"),
    }
    return {key: value for key, value in payload.items() if value is not None}


def _mcp_prompt(prompt: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "name": prompt["name"],
        "title": prompt.get("title"),
        "description": prompt.get("description"),
        "arguments": prompt.get("arguments"),
        "icons": prompt.get("icons"),
        "_meta": prompt.get("_meta"),
    }
    return {key: value for key, value in payload.items() if value is not None}


def _mcp_prompt_message(message: dict[str, Any]) -> dict[str, Any]:
    content = message.get("content")
    if isinstance(content, dict) and "type" in content:
        block = content
    elif isinstance(content, list):
        block = content
    else:
        block = {"type": "text", "text": str(content)}
    return {"role": message["role"], "content": block}


def _ensure_tool_content(result: dict[str, Any]) -> list[dict[str, Any]]:
    content = result.get("content")
    if isinstance(content, list) and all(isinstance(item, dict) and "type" in item for item in content):
        return content
    if content is None and result.get("handle") is not None:
        content = result["handle"]
    if content is None and result.get("structuredContent") is not None:
        content = result["structuredContent"]
    text = json.dumps(content, ensure_ascii=False, default=str)
    return [{"type": "text", "text": text}]


def _resource_contents(uri: str, mime_type: str | None, contents: Any) -> list[dict[str, Any]]:
    if isinstance(contents, list) and all(isinstance(item, dict) and "uri" in item for item in contents):
        return contents
    if isinstance(contents, (bytes, bytearray)):
        return [
            {
                "uri": uri,
                "mimeType": mime_type or "application/octet-stream",
                "blob": base64.b64encode(bytes(contents)).decode("ascii"),
            }
        ]
    if isinstance(contents, dict) and "blob" in contents:
        return [{"uri": uri, "mimeType": mime_type or contents.get("mimeType"), "blob": contents["blob"]}]
    if isinstance(contents, dict) and "text" in contents:
        return [{"uri": uri, "mimeType": mime_type or contents.get("mimeType"), "text": contents["text"]}]
    if isinstance(contents, (dict, list)):
        text = json.dumps(contents, ensure_ascii=False, default=str)
    else:
        text = str(contents)
    return [{"uri": uri, "mimeType": mime_type or "text/plain", "text": text}]


def _merge_surface_meta(params: dict[str, Any], *, surface: str) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for key in ("_meta", "meta"):
        raw = params.get(key)
        if isinstance(raw, dict):
            merged.update(raw)
    merged.setdefault("protocolSurface", surface)
    return merged
