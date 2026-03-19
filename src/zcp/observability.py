from __future__ import annotations

import json
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class MetricsRegistry:
    counters: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    timings_ms: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))

    def increment(self, name: str, value: int = 1) -> None:
        self.counters[name] += value

    def observe_ms(self, name: str, value: float) -> None:
        self.timings_ms[name].append(value)

    def snapshot(self) -> dict[str, Any]:
        return {
            "counters": dict(self.counters),
            "timings_ms": {key: values[:] for key, values in self.timings_ms.items()},
        }


@dataclass
class StructuredLogger:
    sink: Callable[[str], None] = print

    def emit(self, level: str, event: str, **fields: Any) -> None:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "level": level,
            "event": event,
            **fields,
        }
        self.sink(json.dumps(payload, ensure_ascii=False, sort_keys=True))
