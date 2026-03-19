from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ZCPError:
    code: int
    message: str
    data: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {"code": self.code, "message": self.message}
        if self.data is not None:
            payload["data"] = self.data
        return payload


@dataclass
class ZCPEnvelope:
    id: str | int | None
    verb: str
    namespace: str = "core"
    method: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    result: Any = None
    error: ZCPError | None = None

    def to_jsonrpc(self) -> dict[str, Any]:
        if self.verb == "request":
            return {
                "jsonrpc": "2.0",
                "id": self.id,
                "method": self.method,
                "params": self.params,
            }
        if self.verb == "notification":
            return {
                "jsonrpc": "2.0",
                "method": self.method,
                "params": self.params,
            }
        payload = {
            "jsonrpc": "2.0",
            "id": self.id,
        }
        if self.error is not None:
            payload["error"] = self.error.to_dict()
        else:
            payload["result"] = self.result
        return payload


def request(message_id: str | int, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    return ZCPEnvelope(id=message_id, verb="request", method=method, params=params or {}).to_jsonrpc()


def notification(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    return ZCPEnvelope(id=None, verb="notification", method=method, params=params or {}).to_jsonrpc()


def success(message_id: str | int | None, result: Any) -> dict[str, Any]:
    return ZCPEnvelope(id=message_id, verb="response", result=result).to_jsonrpc()


def failure(message_id: str | int | None, code: int, message: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    return ZCPEnvelope(id=message_id, verb="response", error=ZCPError(code=code, message=message, data=data)).to_jsonrpc()
