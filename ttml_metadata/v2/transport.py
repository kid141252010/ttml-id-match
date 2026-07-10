from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import httpx

from ttml_metadata.network import NetworkPolicy


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    headers: Mapping[str, str]
    content: bytes

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", "ignore")

    def json(self) -> Any:
        return json.loads(self.content.decode("utf-8"))


@runtime_checkable
class HttpTransport(Protocol):
    def request(
        self,
        source: str,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        content: bytes | str | None = None,
    ) -> HttpResponse: ...


class HttpxTransport:
    """Shared synchronous HTTP transport with proxy-aware connection pools."""

    def __init__(
        self,
        *,
        policy: NetworkPolicy | None = None,
        timeout_seconds: float = 20.0,
        attempts: int = 3,
        retry_delay_seconds: float = 0.2,
        max_connections: int = 20,
        max_keepalive_connections: int = 10,
        client_factory: Callable[[str | None], httpx.Client] | None = None,
        sleep_func: Callable[[float], None] = time.sleep,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        if attempts < 1:
            raise ValueError("attempts must be at least 1")
        self._policy = policy or NetworkPolicy.from_env()
        self._timeout = timeout_seconds
        self._attempts = attempts
        self._retry_delay = max(0.0, retry_delay_seconds)
        self._sleep = sleep_func
        self._clients: dict[str | None, httpx.Client] = {}
        self._lock = threading.RLock()
        self._client_factory = client_factory or (
            lambda proxy: httpx.Client(
                proxy=proxy,
                timeout=httpx.Timeout(timeout_seconds),
                limits=httpx.Limits(
                    max_connections=max_connections,
                    max_keepalive_connections=max_keepalive_connections,
                ),
                follow_redirects=True,
            )
        )

    def request(
        self,
        source: str,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        content: bytes | str | None = None,
    ) -> HttpResponse:
        client = self._client_for(self._policy.proxy_for(source))
        for attempt in range(self._attempts):
            try:
                response = client.request(
                    method,
                    url,
                    headers=dict(headers or {}),
                    content=content,
                    timeout=self._timeout,
                )
            except httpx.TransportError:
                if attempt + 1 >= self._attempts:
                    raise
                self._wait_before_retry(attempt, None)
                continue

            if _retryable_status(response.status_code) and attempt + 1 < self._attempts:
                retry_after = response.headers.get("Retry-After")
                response.close()
                self._wait_before_retry(attempt, retry_after)
                continue

            response.raise_for_status()
            payload = response.content
            result = HttpResponse(
                status_code=response.status_code,
                headers=dict(response.headers),
                content=payload,
            )
            response.close()
            return result
        raise RuntimeError("unreachable HTTP retry state")

    def close(self) -> None:
        with self._lock:
            clients = tuple(self._clients.values())
            self._clients.clear()
        for client in clients:
            client.close()

    def __enter__(self) -> HttpxTransport:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _client_for(self, proxy: str | None) -> httpx.Client:
        with self._lock:
            client = self._clients.get(proxy)
            if client is None:
                client = self._client_factory(proxy)
                self._clients[proxy] = client
            return client

    def _wait_before_retry(self, attempt: int, retry_after: str | None) -> None:
        delay = _retry_after_seconds(retry_after)
        if delay is None:
            delay = self._retry_delay * (2**attempt)
        if delay > 0:
            self._sleep(delay)


def _retryable_status(status_code: int) -> bool:
    return status_code in {408, 429} or 500 <= status_code < 600


def _retry_after_seconds(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return max(0.0, float(value.strip()))
    except ValueError:
        return None
