from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Literal, Mapping


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


ToolHandler = Callable[[dict[str, Any]], Any]


@dataclass
class ToolDefinition:
    tool_id: str
    alias: str
    description_short: str
    input_schema: dict[str, Any]
    output_mode: Literal["handle", "scalar"] = "handle"
    flags: frozenset[str] = field(default_factory=frozenset)
    defaults: dict[str, Any] = field(default_factory=dict)
    approval_policy: str | None = None
    handler: ToolHandler | None = None
    handle_kind: str = "generic"
    strict: bool = True
    inline_ok: bool = False
    inline_max_bytes: int = 512
    summarize: Callable[[Any], str] | None = None
    meta: Callable[[Any], dict[str, Any]] | None = None
    required_scopes: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RegistryView:
    version: int
    hash: str
    tools: list[ToolDefinition]


@dataclass
class HandleRef:
    id: str
    kind: str
    summary: str
    created_at: datetime
    expires_at: datetime | None = None
    hash: str | None = None
    size: int | None = None
    data: Any = None
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def ttl(self) -> str | None:
        if self.expires_at is None:
            return None
        remaining = self.expires_at - utc_now()
        if remaining.total_seconds() <= 0:
            return "0s"
        return format_timedelta(remaining)

    def is_expired(self) -> bool:
        return self.expires_at is not None and utc_now() >= self.expires_at


@dataclass
class CallRequest:
    cid: str
    tool_id: str
    alias: str
    arguments: dict[str, Any]
    raw_call_id: str | None = None


@dataclass
class CallError:
    code: str
    hint: str | None = None
    detail: str | None = None


@dataclass
class CallResult:
    cid: str
    status: Literal["ok", "error"]
    scalar: Any = None
    handle: HandleRef | None = None
    summary: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    error: CallError | None = None
    raw_call_id: str | None = None


@dataclass
class CTPEvent:
    kind: str
    cid: str | None = None
    tool_id: str | None = None
    alias: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class SessionState:
    session_id: str
    registry_hash: str = ""
    tool_subset: tuple[str, ...] = ()
    openai_response_id: str | None = None
    handles: dict[str, HandleRef] = field(default_factory=dict)
    seq: int = 0

    def next_cid(self) -> str:
        self.seq += 1
        return f"c{self.seq}"

    def register_handle(self, handle: HandleRef) -> None:
        self.handles[handle.id] = handle


def format_timedelta(delta: timedelta) -> str:
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m" if seconds == 0 else f"{minutes}m{seconds}s"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h" if minutes == 0 else f"{hours}h{minutes}m"
    days, hours = divmod(hours, 24)
    return f"{days}d" if hours == 0 else f"{days}d{hours}h"


def is_scalar_value(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def merge_defaults(arguments: Mapping[str, Any], defaults: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(defaults)
    merged.update(arguments)
    return merged
