from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


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
class ZCPServerConfig:
    service_name: str = "zcp-service"
    environment: str = "production"
    http: HTTPConfig = field(default_factory=HTTPConfig)
    sse: SSEConfig = field(default_factory=SSEConfig)
    auth: BearerAuthConfig | None = None
    rate_limit: RateLimitConfig = field(default_factory=RateLimitConfig)
    expose_metadata: bool = True
    session_header: str = "x-zcp-session"
    docs_dir: str = str(Path("docs/site"))
    serve_docs: bool = False
