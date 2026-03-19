from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from .server import ZCPServerSession


@dataclass
class SessionEvent:
    event_id: str
    payload: dict[str, Any]
    created_at: float


@dataclass
class SessionRuntime:
    session: "ZCPServerSession"
    replay_buffer_size: int = 256
    listeners: list[asyncio.Queue[SessionEvent]] = field(default_factory=list)
    events: deque[SessionEvent] = field(default_factory=deque)
    next_event_id: int = 0
    last_seen_at: float = field(default_factory=time.time)

    def touch(self) -> None:
        self.last_seen_at = time.time()

    def add_listener(self) -> asyncio.Queue[SessionEvent]:
        queue: asyncio.Queue[SessionEvent] = asyncio.Queue()
        self.listeners.append(queue)
        self.touch()
        return queue

    def remove_listener(self, queue: asyncio.Queue[SessionEvent]) -> None:
        self.listeners = [item for item in self.listeners if item is not queue]

    def publish(self, payload: dict[str, Any]) -> SessionEvent:
        self.touch()
        self.next_event_id += 1
        event = SessionEvent(
            event_id=str(self.next_event_id),
            payload=payload,
            created_at=time.time(),
        )
        self.events.append(event)
        while len(self.events) > self.replay_buffer_size:
            self.events.popleft()
        for listener in list(self.listeners):
            listener.put_nowait(event)
        return event

    def replay_after(self, event_id: str | None) -> tuple[list[SessionEvent], bool]:
        if event_id is None:
            return list(self.events), True
        if event_id == "0":
            return list(self.events), True
        matched = False
        replay: list[SessionEvent] = []
        for event in self.events:
            if matched:
                replay.append(event)
            elif event.event_id == event_id:
                matched = True
        return replay, matched
