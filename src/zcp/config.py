from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .auth import OAuthProvider


@dataclass
class BearerAuthConfig:
    token: str
    header_name: str = "authorization"
    scheme: str = "Bearer"


@dataclass
class RateLimitConfig:
    window_seconds: int = 60
    max_requests: int = 120


@dataclass
class SSEConfig:
    enabled: bool = True
    path: str = "/sse"
    keepalive_seconds: int = 15


@dataclass
class StreamableHTTPConfig:
    enabled: bool = True
    replay_buffer_size: int = 256
    retry_interval_ms: int = 1000
    session_ttl_seconds: int = 1800


@dataclass
class WebSocketConfig:
    enabled: bool = True
    path: str = "/ws"


@dataclass
class OAuthConfig:
    enabled: bool = False
    issuer: str = "http://127.0.0.1:8000"
    authorization_path: str = "/authorize"
    token_path: str = "/token"
    registration_path: str = "/register"
    revocation_path: str = "/revoke"
    metadata_path: str = "/.well-known/oauth-authorization-server"
    require_pkce: bool = True
    access_token_ttl_seconds: int = 3600
    allow_dynamic_client_registration: bool = True
    allow_token_revocation: bool = True
    resource_metadata_enabled: bool = True
    default_client_id: str = "zcp-local-client"


@dataclass
class HTTPConfig:
    index_path: str = "/"
    docs_path: str = "/docs"
    rpc_path: str = "/zcp"
    mcp_path: str = "/mcp"
    health_path: str = "/healthz"
    ready_path: str = "/readyz"
    metadata_path: str = "/metadata"
    cors_allow_origin: str = "*"
    public_paths: tuple[str, ...] = ("/", "/docs", "/healthz", "/readyz", "/metadata")


@dataclass
class ToolExposureConfig:
    default_profile: str | None = None
    native_default_profile: str | None = None
    mcp_default_profile: str | None = None
    allow_client_filters: bool = True
    semantic_workflow_profile: str = "semantic-workflow"
    semantic_group: str = "workflow"
    enforce_call_visibility: bool = True


@dataclass
class ZCPServerConfig:
    service_name: str = "zcp-service"
    environment: str = "production"
    http: HTTPConfig = field(default_factory=HTTPConfig)
    sse: SSEConfig = field(default_factory=SSEConfig)
    streamable_http: StreamableHTTPConfig = field(default_factory=StreamableHTTPConfig)
    websocket: WebSocketConfig = field(default_factory=WebSocketConfig)
    tool_exposure: ToolExposureConfig = field(default_factory=ToolExposureConfig)
    oauth: OAuthConfig = field(default_factory=OAuthConfig)
    oauth_provider: "OAuthProvider | None" = None
    auth: BearerAuthConfig | None = None
    rate_limit: RateLimitConfig = field(default_factory=RateLimitConfig)
    expose_metadata: bool = True
    session_header: str = "x-zcp-session"
    docs_dir: str = str(Path("docs/site"))
    serve_docs: bool = False
