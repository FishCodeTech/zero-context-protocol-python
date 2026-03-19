from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class ExcelBenchmarkCase:
    tier: str
    case_id: str
    prompt_factory: Callable[[Path], str]
    required_tool_calls: dict[str, int]
    evaluator: Callable[[dict[str, Any] | None, Path], tuple[bool, bool, str]]
    autonomous: bool = False
    native_zcp_required_tool_calls: dict[str, int] | None = None
