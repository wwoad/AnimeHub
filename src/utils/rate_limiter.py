"""异步令牌桶限速器

控制 API 请求频率, 避免触发目标平台的风控策略。
支持配置请求速率(每秒令牌数)和突发容量。
"""

from __future__ import annotations

import asyncio
import time


class RateLimiter:
    """基于令牌桶算法的异步限速器

    Args:
        rate: 每秒允许的请求数(如 2.5 表示每秒2.5个请求, 即间隔0.4秒)
        burst: 突发容量, 允许短时间内连续发送的最大请求数
    """

    def __init__(self, rate: float = 2.5, burst: int = 3) -> None:
        if rate <= 0:
            raise ValueError(f"rate 必须大于0, 当前值: {rate}")
        if burst < 1:
            raise ValueError(f"burst 必须大于等于1, 当前值: {burst}")

        self._rate = rate
        self._burst = float(burst)
        self._tokens = self._burst
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """获取一个令牌, 若无可用令牌则等待"""
        async with self._lock:
            self._refill()

            if self._tokens < 1.0:
                wait_time = (1.0 - self._tokens) / self._rate
                await asyncio.sleep(wait_time)
                self._tokens = 0.0
                self._last_refill = time.monotonic()
            else:
                self._tokens -= 1.0

    def _refill(self) -> None:
        """根据经过时间补充令牌"""
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self._burst, self._tokens + elapsed * self._rate)
        self._last_refill = now
