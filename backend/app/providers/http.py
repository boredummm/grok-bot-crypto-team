"""Shared HTTP client: timeouts, backoff, Retry-After, and a circuit breaker."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import httpx

log = logging.getLogger(__name__)


class ProviderError(Exception):
    def __init__(self, provider: str, message: str) -> None:
        super().__init__(message)
        self.provider = provider
        self.message = message


@dataclass
class ProviderSnapshot:
    provider: str
    status: str
    configured: bool
    latency_ms: int | None
    circuit: str
    consecutive_failures: int
    detail: str | None
    last_error: str | None


class RateLimiter:
    def __init__(self, per_minute: int, sleep: Callable[[float], Awaitable[None]] | None = None) -> None:
        self.interval = 60.0 / max(1, per_minute)
        self._next = 0.0
        self._lock = asyncio.Lock()
        self._sleep = sleep or asyncio.sleep

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait = self._next - now
            if wait > 0:
                await self._sleep(wait)
                now = time.monotonic()
            self._next = max(self._next, now) + self.interval


class Circuit:
    def __init__(self, threshold: int = 3, open_seconds: float = 30.0) -> None:
        self.threshold = threshold
        self.open_seconds = open_seconds
        self.failures = 0
        self.open_until = 0.0
        self.state = "closed"

    def allow(self) -> bool:
        now = time.monotonic()
        if self.state == "open" and now >= self.open_until:
            self.state = "half"
            return True
        return self.state != "open"

    def success(self) -> None:
        self.failures = 0
        self.state = "closed"

    def failure(self) -> None:
        self.failures += 1
        if self.failures >= self.threshold:
            self.state = "open"
            self.open_until = time.monotonic() + self.open_seconds


class ProviderClient:
    def __init__(
        self,
        name: str,
        *,
        per_minute: int,
        timeout: float = 8.0,
        client: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self.name = name
        self.timeout = timeout
        self.limiter = RateLimiter(per_minute, sleep=sleep)
        self.circuit = Circuit()
        self._client = client
        self._owns_client = client is None
        self._sleep = sleep or asyncio.sleep
        self.last_error: str | None = None
        self.last_latency_ms: int | None = None
        self.configured = True

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    def snapshot(self) -> ProviderSnapshot:
        if not self.configured:
            status = "unconfigured"
        elif self.circuit.state == "open":
            status = "open_circuit"
        elif self.last_error:
            status = "degraded"
        else:
            status = "ok"
        return ProviderSnapshot(
            provider=self.name,
            status=status,
            configured=self.configured,
            latency_ms=self.last_latency_ms,
            circuit=self.circuit.state,
            consecutive_failures=self.circuit.failures,
            detail=None,
            last_error=self.last_error,
        )

    async def get_json(
        self,
        url: str,
        *,
        params: dict | None = None,
        headers: dict | None = None,
    ) -> Any:
        if not self.circuit.allow():
            raise ProviderError(self.name, "circuit open")
        await self.limiter.acquire()
        last_exc: Exception | None = None
        for attempt in range(3):
            started = time.perf_counter()
            try:
                response = await self._http().get(url, params=params, headers=headers)
                self.last_latency_ms = int((time.perf_counter() - started) * 1000)
                if response.status_code == 429:
                    retry = response.headers.get("retry-after")
                    try:
                        delay = float(retry) if retry is not None else 0.25 * (2**attempt)
                    except ValueError:
                        delay = 0.25 * (2**attempt)
                    await self._sleep(min(delay, 2.0))
                    last_exc = ProviderError(self.name, "rate limited")
                    continue
                if response.status_code >= 500:
                    last_exc = ProviderError(self.name, f"status {response.status_code}")
                    self.circuit.failure()
                    if not self.circuit.allow():
                        break
                    await self._sleep(0.05 * (2**attempt))
                    continue
                response.raise_for_status()
                self.circuit.success()
                self.last_error = None
                return response.json()
            except ProviderError:
                raise
            except Exception as exc:
                last_exc = exc
                self.last_latency_ms = int((time.perf_counter() - started) * 1000)
                await self._sleep(0.05 * (2**attempt))
        self.circuit.failure()
        message = str(last_exc) if last_exc else "request failed"
        self.last_error = message[:300]
        log.warning("provider request failed", extra={"provider": self.name})
        raise ProviderError(self.name, message)
