from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .server import FastZCP
from .session import ZCPClientSession


@dataclass
class TransportConfig:
    kind: str
    endpoint: str | None = None
    metadata: dict[str, Any] | None = None


def stdio_server(app: FastZCP, *, session_id: str = "stdio-server"):
    return app.create_server_session(session_id=session_id)


def stdio_client(server_session, **kwargs: Any) -> ZCPClientSession:
    return ZCPClientSession(server_session, transport="stdio", **kwargs)


def sse_server(app: FastZCP, *, endpoint: str = "http://127.0.0.1:8000/sse", session_id: str = "sse-server"):
    session = app.create_server_session(session_id=session_id)
    session.transport_config = TransportConfig(kind="sse", endpoint=endpoint)
    return session


def sse_client(server_session, **kwargs: Any) -> ZCPClientSession:
    return ZCPClientSession(server_session, transport="sse", **kwargs)


def streamable_http_server(
    app: FastZCP,
    *,
    endpoint: str = "http://127.0.0.1:8000/zcp",
    session_id: str = "http-server",
):
    session = app.create_server_session(session_id=session_id)
    session.transport_config = TransportConfig(kind="streamable_http", endpoint=endpoint)
    return session


def streamable_http_client(server_session, **kwargs: Any) -> ZCPClientSession:
    return ZCPClientSession(server_session, transport="streamable_http", **kwargs)
