from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .capabilities import InitializeResult, PROTOCOL_VERSION
from .config import ZCPServerConfig
from .gateway import MCPGatewayServer
from .observability import MetricsRegistry, StructuredLogger
from .server import FastZCP, ZCPServerSession


@dataclass
class SessionRuntime:
    session: ZCPServerSession
    queue: asyncio.Queue[dict[str, Any]]


class ZCPASGIApp:
    def __init__(
        self,
        app: FastZCP,
        *,
        config: ZCPServerConfig | None = None,
        logger: StructuredLogger | None = None,
        metrics: MetricsRegistry | None = None,
    ) -> None:
        self.app = app
        self.config = config or ZCPServerConfig(service_name=app.name)
        self.logger = logger or StructuredLogger()
        self.metrics = metrics or MetricsRegistry()
        self._sessions: dict[str, SessionRuntime] = {}
        self._rate_buckets: dict[str, deque[float]] = defaultdict(deque)

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self._json(send, 500, {"error": "unsupported_scope"})
            return
        start = time.perf_counter()
        path = scope["path"]
        method = scope["method"]
        client_ip = (scope.get("client") or ("unknown", 0))[0]
        try:
            if not self._is_allowed(client_ip):
                self.metrics.increment("http.rate_limited")
                await self._json(send, 429, {"error": "rate_limited"})
                return
            if method == "GET" and path == self.config.http.health_path:
                await self._json(send, 200, {"status": "ok"})
                return
            if method == "GET" and path == self.config.http.ready_path:
                await self._json(send, 200, {"status": "ready"})
                return
            if self.config.serve_docs and method == "GET" and path in {self.config.http.index_path, self.config.http.docs_path}:
                await self._serve_docs("index.html", send)
                return
            if self.config.serve_docs and method == "GET" and path.startswith(f"{self.config.http.docs_path}/"):
                await self._serve_docs(path[len(self.config.http.docs_path) + 1 :], send)
                return
            if method == "GET" and path == self.config.http.metadata_path and self.config.expose_metadata:
                await self._json(send, 200, self._metadata())
                return
            if not self._authorized(scope):
                self.metrics.increment("http.unauthorized")
                await self._json(send, 401, {"error": "unauthorized"})
                return
            if method == "GET" and path == self.config.sse.path and self.config.sse.enabled:
                await self._handle_sse(scope, receive, send)
                return
            if method == "POST" and path == self.config.http.rpc_path:
                await self._handle_rpc(scope, receive, send)
                return
            if method == "POST" and path == self.config.http.mcp_path:
                await self._handle_mcp_rpc(scope, receive, send)
                return
            await self._json(send, 404, {"error": "not_found"})
        finally:
            self.metrics.observe_ms("http.request_ms", (time.perf_counter() - start) * 1000)

    async def _handle_rpc(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        runtime = self._get_session(scope)
        body = await _read_body(receive)
        payload = json.loads(body.decode("utf-8") or "{}")
        batch = payload if isinstance(payload, list) else [payload]
        results: list[dict[str, Any]] = []
        for item in batch:
            self.metrics.increment("rpc.requests")
            response = await runtime.session.handle_message(item)
            notifications = runtime.session.drain_notifications()
            for note in notifications:
                await runtime.queue.put(note)
            if response is not None:
                results.append(response)
        final_payload: Any = results if isinstance(payload, list) else (results[0] if results else {})
        await self._json(
            send,
            200,
            final_payload,
            headers=[(self.config.session_header.encode("latin1"), runtime.session.state.session_id.encode("latin1"))],
        )

    async def _handle_sse(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        del receive
        runtime = self._get_session(scope)
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", b"text/event-stream"),
                    (b"cache-control", b"no-cache"),
                    (b"connection", b"keep-alive"),
                    (self.config.session_header.encode("latin1"), runtime.session.state.session_id.encode("latin1")),
                ],
            }
        )
        deadline = time.time() + self.config.sse.keepalive_seconds
        while time.time() < deadline:
            try:
                item = await asyncio.wait_for(runtime.queue.get(), timeout=0.25)
                body = f"event: notification\ndata: {json.dumps(item, ensure_ascii=False)}\n\n".encode("utf-8")
                await send({"type": "http.response.body", "body": body, "more_body": True})
            except asyncio.TimeoutError:
                continue
        await send({"type": "http.response.body", "body": b": keepalive\n\n", "more_body": False})

    async def _handle_mcp_rpc(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        runtime = self._get_session(scope)
        gateway = MCPGatewayServer(runtime.session)
        body = await _read_body(receive)
        payload = json.loads(body.decode("utf-8") or "{}")
        batch = payload if isinstance(payload, list) else [payload]
        results: list[dict[str, Any]] = []
        for item in batch:
            self.metrics.increment("mcp.rpc.requests")
            response = await gateway.handle_message(item)
            if response is not None:
                results.append(response)
        final_payload: Any = results if isinstance(payload, list) else (results[0] if results else {})
        await self._json(
            send,
            200,
            final_payload,
            headers=[(self.config.session_header.encode("latin1"), runtime.session.state.session_id.encode("latin1"))],
        )

    def _get_session(self, scope: dict[str, Any]) -> SessionRuntime:
        session_id = _header(scope, self.config.session_header) or str(uuid.uuid4())
        if session_id not in self._sessions:
            self._sessions[session_id] = SessionRuntime(
                session=self.app.create_server_session(session_id=session_id),
                queue=asyncio.Queue(),
            )
        return self._sessions[session_id]

    def _metadata(self) -> dict[str, Any]:
        result = InitializeResult(
            protocol_version=PROTOCOL_VERSION,
            server_info={"name": self.app.name, "version": self.app.version},
            capabilities=self.app.capabilities.to_dict(),
            auth=self.app.auth_profile.__dict__ if self.app.auth_profile else None,
        )
        return {
            "service": self.config.service_name,
            "environment": self.config.environment,
            **result.to_dict(),
            "http": {
                "rpcPath": self.config.http.rpc_path,
                "mcpPath": self.config.http.mcp_path,
                "indexPath": self.config.http.index_path,
                "docsPath": self.config.http.docs_path,
                "healthPath": self.config.http.health_path,
                "readyPath": self.config.http.ready_path,
                "metadataPath": self.config.http.metadata_path,
                "ssePath": self.config.sse.path if self.config.sse.enabled else None,
            },
            "metrics": self.metrics.snapshot(),
        }

    def _authorized(self, scope: dict[str, Any]) -> bool:
        path = scope["path"]
        if path in self.config.http.public_paths:
            return True
        if self.config.serve_docs and path.startswith(f"{self.config.http.docs_path}/"):
            return True
        if self.config.auth is None:
            return True
        value = _header(scope, self.config.auth.header_name)
        prefix = f"{self.config.auth.scheme} "
        return bool(value and value.startswith(prefix) and value[len(prefix) :] == self.config.auth.token)

    def _is_allowed(self, client_ip: str) -> bool:
        bucket = self._rate_buckets[client_ip]
        now = time.time()
        while bucket and now - bucket[0] > self.config.rate_limit.window_seconds:
            bucket.popleft()
        if len(bucket) >= self.config.rate_limit.max_requests:
            return False
        bucket.append(now)
        return True

    async def _json(self, send: Any, status: int, payload: Any, headers: list[tuple[bytes, bytes]] | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        all_headers = [
            (b"content-type", b"application/json; charset=utf-8"),
            (b"access-control-allow-origin", self.config.http.cors_allow_origin.encode("latin1")),
        ]
        if headers:
            all_headers.extend(headers)
        await send({"type": "http.response.start", "status": status, "headers": all_headers})
        await send({"type": "http.response.body", "body": body, "more_body": False})

    async def _serve_docs(self, relative_path: str, send: Any) -> None:
        docs_root = Path(self.config.docs_dir).resolve()
        requested = (docs_root / relative_path).resolve()
        if docs_root not in requested.parents and requested != docs_root / "index.html":
            await self._json(send, 403, {"error": "forbidden"})
            return
        if not requested.exists() or not requested.is_file():
            await self._json(send, 404, {"error": "not_found"})
            return
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", _content_type(requested.suffix).encode("latin1")),
                    (b"cache-control", b"public, max-age=60"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": requested.read_bytes(), "more_body": False})


def create_asgi_app(
    app: FastZCP,
    *,
    config: ZCPServerConfig | None = None,
    logger: StructuredLogger | None = None,
    metrics: MetricsRegistry | None = None,
) -> ZCPASGIApp:
    return ZCPASGIApp(app, config=config, logger=logger, metrics=metrics)


async def _read_body(receive: Any) -> bytes:
    chunks = bytearray()
    more = True
    while more:
        message = await receive()
        if message["type"] != "http.request":
            break
        chunks.extend(message.get("body", b""))
        more = message.get("more_body", False)
    return bytes(chunks)


def _header(scope: dict[str, Any], name: str) -> str | None:
    needle = name.lower().encode("latin1")
    for key, value in scope.get("headers", []):
        if key.lower() == needle:
            return value.decode("latin1")
    return None


def _content_type(suffix: str) -> str:
    return {
        ".html": "text/html; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".js": "application/javascript; charset=utf-8",
        ".json": "application/json; charset=utf-8",
    }.get(suffix, "application/octet-stream")
