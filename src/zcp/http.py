from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from .auth import (
    AccessToken,
    AuthorizationCode,
    InMemoryOAuthProvider,
    OAuthClient,
    RefreshToken,
    generate_code,
    pkce_s256_challenge,
)
from .capabilities import AuthContext, InitializeResult, PROTOCOL_VERSION
from .config import ZCPServerConfig
from .gateway import MCPGatewayServer
from .observability import MetricsRegistry, StructuredLogger
from .server import FastZCP
from .transport_runtime import SessionEvent, SessionRuntime


@dataclass
class SSEStreamState:
    queue: asyncio.Queue[SessionEvent]
    last_keepalive_at: float


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
        default_clients = (
            OAuthClient(
                client_id=self.config.oauth.default_client_id,
                redirect_uris=("http://127.0.0.1/callback", "http://localhost/callback"),
                name="ZCP default local client",
            ),
        )
        self.oauth_provider = self.config.oauth_provider or InMemoryOAuthProvider(default_clients=default_clients)

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        start = time.perf_counter()
        scope_type = scope["type"]
        client_ip = (scope.get("client") or ("unknown", 0))[0]
        try:
            if scope_type == "http":
                await self._handle_http(scope, receive, send, client_ip)
                return
            if scope_type == "websocket":
                await self._handle_websocket(scope, receive, send, client_ip)
                return
            await self._json(send, 500, {"error": "unsupported_scope"})
        finally:
            self.metrics.observe_ms("http.request_ms", (time.perf_counter() - start) * 1000)

    async def _handle_http(self, scope: dict[str, Any], receive: Any, send: Any, client_ip: str) -> None:
        method = scope["method"]
        path = scope["path"]
        self._purge_expired_state()
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

        if self.config.oauth.enabled and await self._handle_oauth_route(scope, receive, send):
            return

        if not self._authorized(scope):
            self.metrics.increment("http.unauthorized")
            await self._json(send, 401, {"error": "unauthorized"})
            return

        if method == "GET" and path in {self.config.sse.path, self.config.http.mcp_path} and self.config.sse.enabled:
            await self._handle_event_stream(scope, receive, send)
            return
        if method == "DELETE" and path == self.config.http.mcp_path:
            await self._handle_mcp_delete(scope, send)
            return
        if method == "POST" and path == self.config.http.rpc_path:
            await self._handle_rpc(scope, receive, send)
            return
        if method == "POST" and path == self.config.http.mcp_path:
            await self._handle_mcp_rpc(scope, receive, send)
            return
        await self._json(send, 404, {"error": "not_found"})

    async def _handle_websocket(self, scope: dict[str, Any], receive: Any, send: Any, client_ip: str) -> None:
        self._purge_expired_state()
        if not self.config.websocket.enabled or scope["path"] != self.config.websocket.path:
            await send({"type": "websocket.close", "code": 4404})
            return
        if not self._is_allowed(client_ip):
            await send({"type": "websocket.close", "code": 4429})
            return
        if not self._authorized(scope):
            await send({"type": "websocket.close", "code": 4401})
            return

        runtime = self._get_session(scope)
        listener = runtime.add_listener()
        await send({"type": "websocket.accept", "subprotocol": "mcp"})

        async def writer() -> None:
            while True:
                event = await listener.get()
                await send({"type": "websocket.send", "text": json.dumps(event.payload, ensure_ascii=False)})

        writer_task = asyncio.create_task(writer())
        gateway = MCPGatewayServer(runtime.session)
        try:
            while True:
                message = await receive()
                if message["type"] == "websocket.disconnect":
                    return
                if message["type"] != "websocket.receive":
                    continue
                raw = message.get("text")
                if not raw:
                    continue
                payload = json.loads(raw)
                batch = payload if isinstance(payload, list) else [payload]
                responses: list[dict[str, Any]] = []
                for item in batch:
                    response = await gateway.handle_message(item)
                    self._publish_notifications(runtime)
                    if response is not None:
                        responses.append(response)
                final_payload: Any = responses if isinstance(payload, list) else (responses[0] if responses else None)
                if final_payload is not None:
                    await send({"type": "websocket.send", "text": json.dumps(final_payload, ensure_ascii=False)})
        finally:
            writer_task.cancel()
            runtime.remove_listener(listener)

    async def _handle_rpc(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        runtime = self._get_session(scope)
        body = await _read_body(receive)
        payload = json.loads(body.decode("utf-8") or "{}")
        batch = payload if isinstance(payload, list) else [payload]
        results: list[dict[str, Any]] = []
        for item in batch:
            self.metrics.increment("rpc.requests")
            response = await runtime.session.handle_message(item)
            self._publish_notifications(runtime)
            if response is not None:
                results.append(response)
        final_payload: Any = results if isinstance(payload, list) else (results[0] if results else {})
        await self._json(
            send,
            200,
            final_payload,
            headers=self._session_headers(runtime),
        )

    async def _handle_mcp_rpc(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        runtime = self._get_session(scope)
        gateway = MCPGatewayServer(runtime.session)
        body = await _read_body(receive)
        payload = json.loads(body.decode("utf-8") or "{}")
        batch = payload if isinstance(payload, list) else [payload]
        results: list[dict[str, Any]] = []
        notifications_only = True
        wants_sse = _prefers_sse(scope)
        is_initialize = any(item.get("method") == "initialize" for item in batch if isinstance(item, dict))
        start_event_id = str(runtime.next_event_id) if wants_sse and not is_initialize else None

        for item in batch:
            self.metrics.increment("mcp.rpc.requests")
            response = await gateway.handle_message(item)
            self._publish_notifications(runtime)
            if item.get("id") is not None:
                notifications_only = False
            if response is not None:
                results.append(response)

        final_payload: Any = results if isinstance(payload, list) else (results[0] if results else None)
        if notifications_only:
            await self._empty(send, 202, headers=self._session_headers(runtime))
            return

        if wants_sse and not is_initialize and final_payload is not None:
            payloads = final_payload if isinstance(final_payload, list) else [final_payload]
            for item in payloads:
                runtime.publish(item)
            events, _matched = runtime.replay_after(start_event_id)
            await self._open_sse(send, runtime, initial_events=events, close_after_response=True)
            return

        await self._json(
            send,
            200,
            final_payload or {},
            headers=self._session_headers(runtime),
        )

    async def _handle_event_stream(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        del receive
        runtime = self._get_session(scope)
        last_event_id = _header(scope, "last-event-id")
        replay_events, matched = runtime.replay_after(last_event_id)
        if last_event_id is not None and runtime.events and not matched:
            await self._json(send, 409, {"error": "invalid_last_event_id"}, headers=self._session_headers(runtime))
            return
        await self._open_sse(send, runtime, initial_events=replay_events, close_after_response=False)

    async def _handle_mcp_delete(self, scope: dict[str, Any], send: Any) -> None:
        session_id = _header(scope, "mcp-session-id") or _header(scope, self.config.session_header)
        if session_id is None or session_id not in self._sessions:
            await self._empty(send, 404, headers=[(b"mcp-protocol-version", PROTOCOL_VERSION.encode("latin1"))])
            return
        runtime = self._sessions.pop(session_id)
        for listener in list(runtime.listeners):
            runtime.remove_listener(listener)
        await self._empty(send, 204, headers=self._session_headers(runtime))

    async def _open_sse(
        self,
        send: Any,
        runtime: SessionRuntime,
        *,
        initial_events: list[SessionEvent],
        close_after_response: bool,
    ) -> None:
        listener = runtime.add_listener()
        state = SSEStreamState(queue=listener, last_keepalive_at=time.time())
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", b"text/event-stream"),
                    (b"cache-control", b"no-cache"),
                    (b"connection", b"keep-alive"),
                    (b"mcp-protocol-version", PROTOCOL_VERSION.encode("latin1")),
                    *self._session_headers(runtime),
                ],
            }
        )
        try:
            if self.config.streamable_http.enabled:
                await self._send_sse_event(
                    send,
                    event_id=str(runtime.next_event_id or 0),
                    payload=None,
                    retry=self.config.streamable_http.retry_interval_ms,
                )
            for event in initial_events:
                await self._send_sse_event(send, event.event_id, event.payload)
            if close_after_response:
                await send({"type": "http.response.body", "body": b"", "more_body": False})
                return

            deadline = time.time() + self.config.sse.keepalive_seconds
            while time.time() < deadline:
                try:
                    event = await asyncio.wait_for(state.queue.get(), timeout=0.25)
                    await self._send_sse_event(send, event.event_id, event.payload)
                    deadline = time.time() + self.config.sse.keepalive_seconds
                except asyncio.TimeoutError:
                    now = time.time()
                    if now - state.last_keepalive_at >= 1.0:
                        await send({"type": "http.response.body", "body": b": keepalive\n\n", "more_body": True})
                        state.last_keepalive_at = now
            await send({"type": "http.response.body", "body": b"", "more_body": False})
        finally:
            runtime.remove_listener(listener)

    async def _send_sse_event(
        self,
        send: Any,
        event_id: str,
        payload: dict[str, Any] | None,
        *,
        retry: int | None = None,
    ) -> None:
        chunks: list[str] = []
        if event_id:
            chunks.append(f"id: {event_id}")
        if retry is not None:
            chunks.append(f"retry: {retry}")
        chunks.append("event: message")
        chunks.append(f"data: {json.dumps(payload, ensure_ascii=False)}" if payload is not None else "data: ")
        body = ("\n".join(chunks) + "\n\n").encode("utf-8")
        await send({"type": "http.response.body", "body": body, "more_body": True})

    def _get_session(self, scope: dict[str, Any]) -> SessionRuntime:
        session_id = _header(scope, "mcp-session-id") or _header(scope, self.config.session_header) or str(uuid.uuid4())
        auth_context = self._auth_context_for_scope(scope, session_id)
        if session_id not in self._sessions:
            self._sessions[session_id] = SessionRuntime(
                session=self.app.create_server_session(session_id=session_id, auth_context=auth_context),
                replay_buffer_size=self.config.streamable_http.replay_buffer_size,
            )
        runtime = self._sessions[session_id]
        runtime.touch()
        runtime.session.auth_context = auth_context
        return runtime

    def _publish_notifications(self, runtime: SessionRuntime) -> None:
        for note in runtime.session.drain_notifications():
            runtime.publish(note)

    def _session_headers(self, runtime: SessionRuntime) -> list[tuple[bytes, bytes]]:
        session_id = runtime.session.state.session_id.encode("latin1")
        return [
            (self.config.session_header.encode("latin1"), session_id),
            (b"mcp-session-id", session_id),
        ]

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
                "websocketPath": self.config.websocket.path if self.config.websocket.enabled else None,
            },
            "toolExposure": {
                "defaultProfile": self.app.default_tool_profile or self.config.tool_exposure.default_profile,
                "nativeDefaultProfile": self.app.default_tool_profiles.get("native") or self.config.tool_exposure.native_default_profile,
                "mcpDefaultProfile": self.app.default_tool_profiles.get("mcp") or self.config.tool_exposure.mcp_default_profile,
                "semanticWorkflowProfile": self.config.tool_exposure.semantic_workflow_profile,
                "semanticGroup": self.config.tool_exposure.semantic_group,
                "allowClientFilters": self.app.allow_client_tool_filters,
                "enforceCallVisibility": self.app.enforce_tool_visibility_on_call,
            },
            "metrics": self.metrics.snapshot(),
        }

    async def _handle_oauth_route(self, scope: dict[str, Any], receive: Any, send: Any) -> bool:
        path = scope["path"]
        method = scope["method"]
        if method == "GET" and path == self.config.oauth.metadata_path:
            await self._json(send, 200, self._oauth_metadata())
            return True
        if (
            method == "GET"
            and self.config.oauth.resource_metadata_enabled
            and path == self._protected_resource_metadata_path(self.config.http.mcp_path)
        ):
            await self._json(send, 200, self._protected_resource_metadata())
            return True
        if path == self.config.oauth.authorization_path and method in {"GET", "POST"}:
            await self._handle_authorize(scope, receive, send)
            return True
        if path == self.config.oauth.token_path and method == "POST":
            await self._handle_token(scope, receive, send)
            return True
        if path == self.config.oauth.registration_path and method == "POST" and self.config.oauth.allow_dynamic_client_registration:
            await self._handle_register(receive, send)
            return True
        if path == self.config.oauth.revocation_path and method == "POST" and self.config.oauth.allow_token_revocation:
            await self._handle_revoke(receive, send)
            return True
        return False

    async def _handle_authorize(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        params = await _request_params(scope, receive)
        client_id = params.get("client_id")
        redirect_uri = params.get("redirect_uri")
        response_type = params.get("response_type")
        code_challenge = params.get("code_challenge")
        if response_type != "code" or not client_id or not redirect_uri:
            await self._json(send, 400, {"error": "invalid_request"})
            return
        if self.config.oauth.require_pkce and not code_challenge:
            await self._json(send, 400, {"error": "invalid_request", "error_description": "missing code_challenge"})
            return
        client = self.oauth_provider.get_client(client_id)
        if client is None:
            client = OAuthClient(client_id=client_id, redirect_uris=(redirect_uri,))
            self.oauth_provider.save_client(client)
        elif redirect_uri not in client.redirect_uris:
            await self._json(send, 400, {"error": "invalid_redirect_uri"})
            return
        code = generate_code("zcp_code")
        scopes = tuple((params.get("scope") or "").split()) if params.get("scope") else tuple(self._auth_scopes())
        self.oauth_provider.save_authorization_code(
            AuthorizationCode(
                code=code,
                client_id=client_id,
                redirect_uri=redirect_uri,
                scopes=scopes,
                state=params.get("state"),
                code_challenge=code_challenge,
                code_challenge_method=params.get("code_challenge_method") or "S256",
            )
        )
        location = _merge_query_params(redirect_uri, {"code": code, "state": params.get("state")})
        await send({"type": "http.response.start", "status": 302, "headers": [(b"location", location.encode("latin1"))]})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    async def _handle_token(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        params = await _form_body(receive)
        grant_type = params.get("grant_type")
        client_id, client_secret = _oauth_client_credentials(scope, params)
        if not grant_type or not client_id:
            await self._json(send, 400, {"error": "invalid_request"})
            return
        client = self.oauth_provider.get_client(client_id)
        if client is None:
            await self._json(send, 401, {"error": "invalid_client"})
            return
        if client.client_secret and client.client_secret != client_secret:
            await self._json(send, 401, {"error": "invalid_client"})
            return
        if grant_type == "authorization_code":
            await self._exchange_authorization_code(client_id, params, send)
            return
        if grant_type == "refresh_token":
            await self._exchange_refresh_token(client_id, params, send)
            return
        await self._json(send, 400, {"error": "unsupported_grant_type"})

    async def _exchange_authorization_code(self, client_id: str, params: dict[str, str], send: Any) -> None:
        code = params.get("code")
        redirect_uri = params.get("redirect_uri")
        code_verifier = params.get("code_verifier")
        if not code or not redirect_uri:
            await self._json(send, 400, {"error": "invalid_request"})
            return
        record = self.oauth_provider.pop_authorization_code(code)
        if record is None or record.expired or record.redirect_uri != redirect_uri or client_id != record.client_id:
            await self._json(send, 400, {"error": "invalid_grant"})
            return
        if self.config.oauth.require_pkce:
            if not code_verifier or pkce_s256_challenge(code_verifier) != record.code_challenge:
                await self._json(send, 400, {"error": "invalid_grant", "error_description": "pkce_verification_failed"})
                return
        response = self._issue_tokens(client_id, record.scopes)
        await self._json(send, 200, response)

    async def _exchange_refresh_token(self, client_id: str, params: dict[str, str], send: Any) -> None:
        refresh_token = params.get("refresh_token")
        if not refresh_token:
            await self._json(send, 400, {"error": "invalid_request"})
            return
        record = self.oauth_provider.get_refresh_token(refresh_token)
        if record is None or record.expired or record.client_id != client_id:
            await self._json(send, 400, {"error": "invalid_grant"})
            return
        response = self._issue_tokens(client_id, record.scopes, include_refresh_token=False)
        await self._json(send, 200, response)

    def _issue_tokens(
        self,
        client_id: str,
        scopes: tuple[str, ...],
        *,
        include_refresh_token: bool = True,
    ) -> dict[str, Any]:
        token_value = generate_code("zcp_at")
        self.oauth_provider.save_access_token(
            AccessToken(
                token=token_value,
                client_id=client_id,
                scopes=scopes,
                expires_at=time.time() + self.config.oauth.access_token_ttl_seconds,
            )
        )
        payload = {
            "access_token": token_value,
            "token_type": "Bearer",
            "expires_in": self.config.oauth.access_token_ttl_seconds,
            "scope": " ".join(scopes),
        }
        if include_refresh_token:
            refresh_token = generate_code("zcp_rt")
            self.oauth_provider.save_refresh_token(
                RefreshToken(
                    token=refresh_token,
                    client_id=client_id,
                    scopes=scopes,
                    expires_at=time.time() + (self.config.oauth.access_token_ttl_seconds * 24),
                )
            )
            payload["refresh_token"] = refresh_token
        return payload

    async def _handle_register(self, receive: Any, send: Any) -> None:
        payload = json.loads((await _read_body(receive)).decode("utf-8") or "{}")
        redirect_uris = tuple(payload.get("redirect_uris", []))
        if not redirect_uris:
            await self._json(send, 400, {"error": "invalid_redirect_uris"})
            return
        client_id = generate_code("zcp_client")
        client_secret = generate_code("zcp_secret")
        self.oauth_provider.save_client(
            OAuthClient(
                client_id=client_id,
                client_secret=client_secret,
                redirect_uris=redirect_uris,
                name=payload.get("client_name"),
            )
        )
        await self._json(
            send,
            201,
            {
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uris": list(redirect_uris),
                "token_endpoint_auth_method": "client_secret_post",
            },
        )

    async def _handle_revoke(self, receive: Any, send: Any) -> None:
        params = await _form_body(receive)
        token = params.get("token")
        if token:
            self.oauth_provider.revoke_token(token)
        await self._empty(send, 200)

    def _oauth_metadata(self) -> dict[str, Any]:
        issuer = self.config.oauth.issuer.rstrip("/")
        return {
            "issuer": issuer,
            "authorization_endpoint": f"{issuer}{self.config.oauth.authorization_path}",
            "token_endpoint": f"{issuer}{self.config.oauth.token_path}",
            "registration_endpoint": f"{issuer}{self.config.oauth.registration_path}",
            "revocation_endpoint": f"{issuer}{self.config.oauth.revocation_path}",
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "response_types_supported": ["code"],
            "code_challenge_methods_supported": ["S256"],
            "scopes_supported": self._auth_scopes(),
            "token_endpoint_auth_methods_supported": ["client_secret_post", "client_secret_basic"],
        }

    def _protected_resource_metadata(self) -> dict[str, Any]:
        issuer = self.config.oauth.issuer.rstrip("/")
        resource = f"{issuer}{self.config.http.mcp_path}"
        return {
            "resource": resource,
            "authorization_servers": [issuer],
            "scopes_supported": self._auth_scopes(),
            "bearer_methods_supported": ["header"],
            "resource_name": self.app.name,
        }

    def _auth_scopes(self) -> list[str]:
        if self.app.auth_profile and self.app.auth_profile.scopes:
            return list(self.app.auth_profile.scopes)
        return []

    def _auth_context_for_scope(self, scope: dict[str, Any], session_id: str) -> AuthContext:
        token = _bearer_token(scope)
        scopes: list[str] = []
        subject = None
        record = None
        if token and self.config.auth is not None and token == self.config.auth.token:
            scopes = self._auth_scopes()
            subject = "static-bearer"
        else:
            record = self.oauth_provider.get_access_token(token) if token else None
        if record is not None:
            scopes = list(record.scopes)
            subject = record.client_id
        return AuthContext(subject=subject, scopes=scopes, session_id=session_id)

    def _authorized(self, scope: dict[str, Any]) -> bool:
        path = scope["path"]
        if path in self.config.http.public_paths:
            return True
        if self.config.serve_docs and path.startswith(f"{self.config.http.docs_path}/"):
            return True
        if self.config.oauth.enabled and path in {
            self.config.oauth.metadata_path,
            self.config.oauth.authorization_path,
            self.config.oauth.token_path,
            self.config.oauth.registration_path,
            self.config.oauth.revocation_path,
            self._protected_resource_metadata_path(self.config.http.mcp_path),
        }:
            return True
        token = _bearer_token(scope)
        if token is None:
            return self.config.auth is None and not self.config.oauth.enabled
        if self.config.auth is not None and token == self.config.auth.token:
            return True
        record = self.oauth_provider.get_access_token(token)
        return bool(record and not record.expired)

    def _is_allowed(self, client_ip: str) -> bool:
        bucket = self._rate_buckets[client_ip]
        now = time.time()
        while bucket and now - bucket[0] > self.config.rate_limit.window_seconds:
            bucket.popleft()
        if len(bucket) >= self.config.rate_limit.max_requests:
            return False
        bucket.append(now)
        return True

    def _purge_expired_state(self) -> None:
        ttl = self.config.streamable_http.session_ttl_seconds
        now = time.time()
        for session_id in [key for key, value in self._sessions.items() if now - value.last_seen_at >= ttl]:
            self._sessions.pop(session_id, None)
        self.oauth_provider.purge_expired()

    def _protected_resource_metadata_path(self, resource_path: str) -> str:
        return f"/.well-known/oauth-protected-resource{resource_path}"

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

    async def _empty(self, send: Any, status: int, headers: list[tuple[bytes, bytes]] | None = None) -> None:
        all_headers = [(b"access-control-allow-origin", self.config.http.cors_allow_origin.encode("latin1"))]
        if headers:
            all_headers.extend(headers)
        await send({"type": "http.response.start", "status": status, "headers": all_headers})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

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
    if config is not None:
        exposure = config.tool_exposure
        if exposure.default_profile and app.default_tool_profile is None and "native" not in app.default_tool_profiles:
            app.default_tool_profiles["native"] = exposure.default_profile
        if exposure.native_default_profile and "native" not in app.default_tool_profiles:
            app.default_tool_profiles["native"] = exposure.native_default_profile
        if exposure.mcp_default_profile and "mcp" not in app.default_tool_profiles:
            app.default_tool_profiles["mcp"] = exposure.mcp_default_profile
        app.allow_client_tool_filters = exposure.allow_client_filters
        app.semantic_workflow_profile = exposure.semantic_workflow_profile
        app.semantic_group = exposure.semantic_group
        app.enforce_tool_visibility_on_call = exposure.enforce_call_visibility
    return ZCPASGIApp(app, config=config, logger=logger, metrics=metrics)


async def _read_body(receive: Any) -> bytes:
    chunks = bytearray()
    more = True
    while more:
        message = await receive()
        if message["type"] not in {"http.request", "websocket.receive"}:
            break
        chunks.extend(message.get("body", b""))
        more = message.get("more_body", False)
    return bytes(chunks)


async def _form_body(receive: Any) -> dict[str, str]:
    raw = (await _read_body(receive)).decode("utf-8")
    return {key: values[-1] for key, values in parse_qs(raw, keep_blank_values=True).items()}


async def _request_params(scope: dict[str, Any], receive: Any) -> dict[str, str]:
    params = {key: values[-1] for key, values in parse_qs(scope.get("query_string", b"").decode("utf-8")).items()}
    if scope["method"] == "POST":
        params.update(await _form_body(receive))
    return params


def _merge_query_params(url: str, params: dict[str, str | None]) -> str:
    parts = list(urlsplit(url))
    query = parse_qs(parts[3], keep_blank_values=True)
    for key, value in params.items():
        if value is not None:
            query[key] = [value]
    parts[3] = urlencode(query, doseq=True)
    return urlunsplit(parts)


def _header(scope: dict[str, Any], name: str) -> str | None:
    needle = name.lower().encode("latin1")
    for key, value in scope.get("headers", []):
        if key.lower() == needle:
            return value.decode("latin1")
    return None


def _prefers_sse(scope: dict[str, Any]) -> bool:
    accept = (_header(scope, "accept") or "").lower()
    return "text/event-stream" in accept


def _bearer_token(scope: dict[str, Any]) -> str | None:
    auth_header = _header(scope, "authorization") or ""
    if auth_header.startswith("Bearer "):
        return auth_header.split(" ", 1)[1]
    query = parse_qs(scope.get("query_string", b"").decode("utf-8"))
    for key in ("access_token", "token"):
        values = query.get(key)
        if values:
            return values[-1]
    return None


def _oauth_client_credentials(scope: dict[str, Any], params: dict[str, str]) -> tuple[str | None, str | None]:
    auth_header = _header(scope, "authorization") or ""
    if auth_header.startswith("Basic "):
        import base64

        raw = auth_header.split(" ", 1)[1]
        decoded = base64.b64decode(raw).decode("utf-8")
        client_id, _, client_secret = decoded.partition(":")
        return client_id or None, client_secret or None
    return params.get("client_id"), params.get("client_secret")


def _content_type(suffix: str) -> str:
    return {
        ".html": "text/html; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".js": "application/javascript; charset=utf-8",
        ".json": "application/json; charset=utf-8",
    }.get(suffix, "application/octet-stream")
