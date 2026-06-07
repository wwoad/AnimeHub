"""异步令牌桶限速器

控制 API 请求频率, 避免触发目标平台的风控策略。
支持配置请求速率(每秒令牌数)和突发容量。

AdaptiveRateLimiter 在此基础上增加自适应调速:
- 连续成功 → 逐步提速
- 收到 429/412/超时 → 立即降速
- 自动找到当前 IP+Cookie 组合下的最快安全速率
"""

from __future__ import annotations

import asyncio
import logging
import time

logger = logging.getLogger(__name__)

_ADJUST_COOLDOWN = 5.0  # 两次调速之间最小间隔(秒)
_SUCCESS_STREAK_NEEDED = 100  # 连续成功次数达到此值才提速
_INCREASE_STEP = 3.0  # 每次提速步长(req/s)
_DECREASE_FACTOR_MAJOR = 0.5  # 重大错误(429/412)降速因子
_DECREASE_FACTOR_MINOR = 0.8  # 一般错误降速因子
_ERROR_WINDOW = 30.0  # 错误计数窗口(秒)
_MAJOR_ERROR_THRESHOLD = 1  # 窗口内重大错误数触发降速
_MINOR_ERROR_THRESHOLD = 3  # 窗口内一般错误数触发降速


class RateLimiter:
    """基于令牌桶算法的异步限速器

    Args:
        rate: 每秒允许的请求数
        burst: 突发容量
    """

    def __init__(self, rate: float = 15.0, burst: int | None = None) -> None:
        if rate <= 0:
            raise ValueError(f"rate 必须大于0, 当前值: {rate}")
        self._rate = rate
        self._burst = float(burst) if burst else rate * 1.5
        if self._burst < 1:
            self._burst = 1.0
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

    @property
    def current_rate(self) -> float:
        return self._rate

    @property
    def current_burst(self) -> int:
        return int(self._burst)


class AdaptiveRateLimiter:
    """自适应限速器, 根据服务器反馈自动调整速率

    用法:
        limiter = AdaptiveRateLimiter(min_rate=5, max_rate=50, initial_rate=15)
        await limiter.acquire()
        limiter.report_success()   # 请求成功时调用
        limiter.report_error()     # 请求失败/429/412时调用
        limiter.report_major_error()  # 429/412时调用(更激进降速)
    """

    def __init__(
        self,
        min_rate: float = 5.0,
        max_rate: float = 50.0,
        initial_rate: float = 15.0,
    ) -> None:
        self._min_rate = min_rate
        self._max_rate = max_rate
        self._inner = RateLimiter(rate=initial_rate)
        self._success_streak = 0
        self._error_times: list[float] = []
        self._major_error_times: list[float] = []
        self._last_adjust = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        await self._inner.acquire()

    def report_success(self) -> None:
        self._success_streak += 1
        if self._success_streak >= _SUCCESS_STREAK_NEEDED:
            self._try_increase()

    def report_error(self) -> None:
        now = time.monotonic()
        self._error_times.append(now)
        self._success_streak = max(0, self._success_streak - 10)
        self._try_decrease_minor()

    def report_major_error(self) -> None:
        now = time.monotonic()
        self._major_error_times.append(now)
        self._success_streak = max(0, self._success_streak - 30)
        self._try_decrease_major()

    @property
    def current_rate(self) -> float:
        return self._inner.current_rate

    def _try_increase(self) -> None:
        now = time.monotonic()
        if now - self._last_adjust < _ADJUST_COOLDOWN:
            return
        self._cleanup_errors(now)
        if self._error_times or self._major_error_times:
            self._success_streak = 0
            return

        new_rate = min(self._max_rate, self._inner.current_rate + _INCREASE_STEP)
        if new_rate > self._inner.current_rate:
            self._inner = RateLimiter(rate=new_rate)
            self._last_adjust = now
            self._success_streak = 0
            logger.info("自适应速率 ↑ %.0f req/s (连续成功)", new_rate)

    def _try_decrease_minor(self) -> None:
        now = time.monotonic()
        self._cleanup_errors(now)
        minor_count = len(self._error_times)
        major_count = len(self._major_error_times)
        if major_count >= _MAJOR_ERROR_THRESHOLD:
            return
        if minor_count < _MINOR_ERROR_THRESHOLD:
            return

        new_rate = max(self._min_rate, self._inner.current_rate * _DECREASE_FACTOR_MINOR)
        if new_rate < self._inner.current_rate:
            self._inner = RateLimiter(rate=new_rate)
            self._last_adjust = now
            self._error_times.clear()
            self._success_streak = 0
            logger.info("自适应速率 ↓ %.0f req/s (错误数=%d)", new_rate, minor_count)

    def _try_decrease_major(self) -> None:
        now = time.monotonic()
        self._cleanup_errors(now)
        major_count = len(self._major_error_times)
        if major_count < _MAJOR_ERROR_THRESHOLD:
            return

        new_rate = max(self._min_rate, self._inner.current_rate * _DECREASE_FACTOR_MAJOR)
        if new_rate < self._inner.current_rate:
            self._inner = RateLimiter(rate=new_rate)
            self._last_adjust = now
            self._major_error_times.clear()
            self._error_times.clear()
            self._success_streak = 0
            logger.info("自适应速率 ↓ %.0f req/s (429/412数=%d)", new_rate, major_count)

    def _cleanup_errors(self, now: float) -> None:
        cutoff = now - _ERROR_WINDOW
        self._error_times = [t for t in self._error_times if t > cutoff]
        self._major_error_times = [t for t in self._major_error_times if t > cutoff]
