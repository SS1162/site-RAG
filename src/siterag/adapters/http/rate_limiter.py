"""Politeness delay between requests: base + jitter, robots Crawl-delay, adaptive slow-down."""

from __future__ import annotations

import logging
import random
import time
from collections import deque
from collections.abc import Callable

log = logging.getLogger(__name__)


class RateLimiter:
    """Spaces requests out; one request at a time.

    The delay before a request is ``(base + uniform(0, jitter)) * factor``, counted from
    the end of the previous request. ``base`` is the larger of the profile delay and the
    robots.txt ``Crawl-delay``. ``factor`` is 1 normally and ``adaptive_factor`` while the
    server is slow: it switches on when the moving average response time reaches
    ``slow_ratio`` times the average of the run's first ``baseline_size`` responses, and
    switches off only when the ratio falls below ``recover_ratio`` (hysteresis).
    """

    def __init__(
        self,
        base_delay_s: float,
        jitter_s: float,
        adaptive_factor: float = 2.0,
        *,
        baseline_size: int = 20,
        window_size: int = 20,
        slow_ratio: float = 2.0,
        recover_ratio: float = 1.5,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        rand: Callable[[], float] = random.random,
    ) -> None:
        self._profile_delay = base_delay_s
        self._crawl_delay: float | None = None
        self._jitter = jitter_s
        self._factor = adaptive_factor
        self._baseline_size = baseline_size
        self._slow_ratio = slow_ratio
        self._recover_ratio = recover_ratio
        self._clock = clock
        self._sleep = sleep
        self._rand = rand
        self._baseline: list[float] = []
        self._window: deque[float] = deque(maxlen=window_size)
        self._last_done: float | None = None
        self.slowed = False

    @property
    def base_delay(self) -> float:
        """Delay before jitter and adaptation."""
        return max(self._profile_delay, self._crawl_delay or 0.0)

    def set_crawl_delay(self, seconds: float | None) -> None:
        """Apply robots.txt ``Crawl-delay``."""
        self._crawl_delay = seconds

    def next_delay(self) -> float:
        """Draw the delay to wait before the next request."""
        delay = self.base_delay + self._rand() * self._jitter
        return delay * self._factor if self.slowed else delay

    def wait(self) -> None:
        """Sleep until the next request may start (no wait before the first one)."""
        if self._last_done is None:
            return
        remaining = self._last_done + self.next_delay() - self._clock()
        if remaining > 0:
            self._sleep(remaining)

    def record(self, elapsed_s: float) -> None:
        """Record a finished request and its response time."""
        self._last_done = self._clock()
        if len(self._baseline) < self._baseline_size:
            self._baseline.append(elapsed_s)
        self._window.append(elapsed_s)
        self._adapt()

    def _adapt(self) -> None:
        if len(self._baseline) < self._baseline_size or not self._window:
            return
        baseline = sum(self._baseline) / len(self._baseline)
        if baseline <= 0:
            return
        ratio = (sum(self._window) / len(self._window)) / baseline
        if not self.slowed and ratio >= self._slow_ratio:
            self.slowed = True
            log.warning("Server is slow (x%.1f response time): delay multiplied", ratio)
        elif self.slowed and ratio < self._recover_ratio:
            self.slowed = False
            log.info("Server response time recovered (x%.1f): normal delay", ratio)
