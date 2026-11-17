"""In-process pub/sub used to push fetch progress to SSE clients."""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from typing import Any


class EventBus:
    def __init__(self, max_queue: int = 500):
        self._subscribers: set[asyncio.Queue[tuple[str, str]]] = set()
        self._max_queue = max_queue

    def publish(self, event: str, data: dict[str, Any]) -> None:
        payload = json.dumps(data, default=str)
        for queue in list(self._subscribers):
            # Slow client: drop events rather than block fetching.
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait((event, payload))

    async def subscribe(self) -> AsyncIterator[tuple[str, str]]:
        queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue(self._max_queue)
        self._subscribers.add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._subscribers.discard(queue)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)
