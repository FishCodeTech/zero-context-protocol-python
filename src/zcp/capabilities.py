from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any

PROTOCOL_VERSION = "2025-11-25"


def dataclass_to_dict(value: Any) -> Any:
    if is_dataclass(value):
        return {key: dataclass_to_dict(item) for key, item in asdict(value).items() if item is not None}
    if isinstance(value, dict):
        return {key: dataclass_to_dict(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [dataclass_to_dict(item) for item in value]
    if isinstance(value, tuple):
        return [dataclass_to_dict(item) for item in value]
    return value


@dataclass
class AuthProfile:
    issuer: str
    authorization_url: str | None = None
    token_url: str | None = None
    scopes: list[str] = field(default_factory=list)
    pkce_required: bool = True
    resource_indicators: bool = True
    step_up_scopes: bool = True


@dataclass
class AuthContext:
    subject: str | None = None
    scopes: list[str] = field(default_factory=list)
    session_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Capabilities:
    tools: dict[str, Any] | None = None
    resources: dict[str, Any] | None = None
    prompts: dict[str, Any] | None = None
    completions: dict[str, Any] | None = None
    logging: dict[str, Any] | None = None
    roots: dict[str, Any] | None = None
    sampling: dict[str, Any] | None = None
    elicitation: dict[str, Any] | None = None
    tasks: dict[str, Any] | None = None
    auth: dict[str, Any] | None = None
    experimental: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return dataclass_to_dict(self)


@dataclass
class InitializeParams:
    protocol_version: str = PROTOCOL_VERSION
    client_info: dict[str, Any] = field(default_factory=dict)
    capabilities: dict[str, Any] = field(default_factory=dict)


@dataclass
class InitializeResult:
    protocol_version: str
    server_info: dict[str, Any]
    capabilities: dict[str, Any]
    auth: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return dataclass_to_dict(self)


@dataclass
class ProgressToken:
    token: str
    total: float | None = None


def default_capabilities() -> Capabilities:
    return Capabilities(
        tools={"listChanged": True},
        resources={"listChanged": True, "subscribe": True},
        prompts={"listChanged": True},
        completions={"argumentCompletion": True},
        logging={"structured": True},
        roots={"listChanged": True},
        sampling={"createMessage": True, "toolsInSampling": True},
        elicitation={"forms": True, "url": True, "basic": True},
        tasks={"experimental": True},
        auth={"oauth2_1": True, "pkce": True, "resourceIndicators": True, "stepUpScopes": True},
    )
