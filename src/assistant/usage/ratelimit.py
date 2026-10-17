"""Small in-memory rate limiter (sliding window). Resets when the bot restarts."""
import time
from collections import deque


class RateLimiter:
    def __init__(self, max_calls: int, window_seconds: float, clock=time.monotonic):
        self._max = max_calls
        self._window = window_seconds
        self._clock = clock
        self._hits: dict[object, deque] = {}

    def check(self, key) -> float:
        """Returns 0 and records the call if allowed, otherwise the seconds to wait."""
        now = self._clock()
        hits = self._hits.setdefault(key, deque())
        while hits and now - hits[0] >= self._window:
            hits.popleft()
        if len(hits) >= self._max:
            return max(self._window - (now - hits[0]), 1.0)
        hits.append(now)
        return 0.0
