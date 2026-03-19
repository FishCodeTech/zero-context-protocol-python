from __future__ import annotations

import json
from typing import Any

from .canonical_protocol import CallResult


def encode_tool_output(result: CallResult) -> str:
    if result.status == "error":
        payload: dict[str, Any] = {
            "ok": False,
            "error": result.error.code if result.error else "exec:error",
        }
        if result.error and result.error.hint:
            payload["hint"] = result.error.hint
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=True)

    if result.handle is not None:
        payload = {
            "ok": True,
            "handle": result.handle.id,
            "summary": result.summary or result.handle.summary,
            "meta": {
                "kind": result.handle.kind,
                **result.meta,
            },
        }
        ttl = result.handle.ttl
        if ttl is not None:
            payload["meta"]["ttl"] = ttl
        if result.handle.size is not None:
            payload["meta"]["size"] = result.handle.size
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=True)

    payload = {"ok": True, "value": result.scalar}
    if result.meta:
        payload["meta"] = result.meta
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=True)


def decode_tool_output(payload: str) -> dict[str, Any]:
    return json.loads(payload)
